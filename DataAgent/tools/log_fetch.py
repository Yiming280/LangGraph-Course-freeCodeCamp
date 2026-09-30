"""日志工具：读取本机日志文件（tail / 关键词检索）。

配合 IoTDB 时序库使用：从时序库定位到「状态变化 / 工程师操作」的时间点后，
用本工具到本机日志里检索对应记录，还原问题现场、归纳工程师如何解决问题。

日志目录从 tools/_settings.py 读取（网页可编辑，settings.json 覆盖 env 种子）。
只允许读白名单目录内的文件，防 agent 乱读任意路径：
    LOG_DIR   日志根目录，默认 /var/log
    LOG_DIRS  逗号分隔的多个允许目录（优先于 LOG_DIR）
"""
import gzip
from collections import deque
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.tools import tool

from tools._settings import get_log_dirs, get_log_sources

load_dotenv()

_MAX_OUTPUT = 20000   # 单次返回字符上限
_MAX_MATCHES = 500    # search_logs 最多返回匹配条数
_MAX_TAIL = 5000      # read_log_file 最大 tail 行数


def _log_dirs(alias: str = "") -> list[Path]:
    """日志白名单目录列表（按来源别名；settings.json 覆盖 env，兜底 /var/log）。"""
    dirs = [Path(item).expanduser() for item in get_log_dirs(alias) if item.strip()]
    return dirs or [Path("/var/log")]


def _resolve(path: str, alias: str = "") -> Path | None:
    """把用户给的路径解析为绝对路径，并校验落在白名单目录内；越界返回 None。"""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = _log_dirs(alias)[0] / p
    p = p.resolve()
    for d in _log_dirs(alias):
        dr = d.resolve()
        try:
            p.relative_to(dr)
            return p
        except ValueError:
            continue
    return None


def _iter_lines(path: Path):
    """逐行读取（透明支持 .gz），UTF-8，非法字节用替换符。"""
    if path.name.endswith(".gz"):
        fh = gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        fh = open(path, "r", encoding="utf-8", errors="replace")
    with fh:
        yield from fh


def _tail_text(path: Path, n: int) -> list[str]:
    """高效读取文本文件末尾 n 行（反向读块，不整文件读入）。"""
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []
        block = 8192
        pos = size
        data = b""
        while pos > 0:
            read = min(block, pos)
            pos -= read
            f.seek(pos)
            data = f.read(read) + data
            if data.count(b"\n") > n:
                break
    return data.decode("utf-8", errors="replace").splitlines()[-n:]


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def _clamp(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n…(输出过长，已截断)"


@tool(parse_docstring=True)
def list_log_sources() -> str:
    """列出所有已配置的日志来源（别名 + 目录），用于选择检索哪套日志。

    不确定有哪些日志目录、或用户提到某套日志时，先调用本工具拿到别名，
    再把别名传给 list_log_files / read_log_file / search_logs 的 alias 参数。
    """
    sources = get_log_sources()
    if not sources:
        return "未配置任何日志来源。"
    lines = []
    for i, s in enumerate(sources):
        tag = "（默认）" if i == 0 else ""
        lines.append(f"- {s['alias']}{tag} —— {', '.join(s['dirs']) or '(无目录)'}")
    return "\n".join(lines)


@tool(parse_docstring=True)
def list_log_files(directory: str = "", alias: str = "") -> str:
    """列出日志目录下的文件（含大小和修改时间）。

    Args:
        directory: 可选，日志子目录（相对 LOG_DIR 或绝对路径）；不传则用默认日志根目录。
        alias: 可选，日志来源别名（见 list_log_sources）；不传用默认（第一个）。
    """
    base = _resolve(directory, alias) if directory else _log_dirs(alias)[0].resolve()
    if base is None:
        return f"路径 {directory!r} 不在允许的日志目录内。"
    if not base.exists() or not base.is_dir():
        return f"目录不存在：{base}"
    lines = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        try:
            st = p.stat()
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"- {p} ({_human_size(st.st_size)}, {mtime})")
        except OSError:
            continue
    if not lines:
        return f"目录 {base} 下无文件。"
    return _clamp("\n".join(lines))


@tool(parse_docstring=True)
def read_log_file(path: str = "", tail: int = 200, keyword: str = "", alias: str = "") -> str:
    """读取指定日志文件末尾若干行（可选按关键词过滤）。

    Args:
        path: 日志文件路径（相对 LOG_DIR 或绝对路径）。
        tail: 返回末尾多少行（默认 200，最大 5000）。
        keyword: 可选，只返回包含该关键词的行（大小写不敏感）；过滤在 tail 窗口内进行，
            若要在整个目录内检索请用 search_logs。
        alias: 可选，日志来源别名（见 list_log_sources）；不传用默认（第一个）。
    """
    if not (path or "").strip():
        return "缺少 path 参数：请提供要读取的日志文件路径（相对或绝对路径）。不确定有哪些文件时，先调用 list_log_files() 列出日志目录下的文件。"
    p = _resolve(path, alias)
    if p is None:
        return f"路径 {path!r} 不在允许的日志目录内。"
    if not p.exists() or not p.is_file():
        return f"文件不存在：{p}"
    tail = max(1, min(int(tail), _MAX_TAIL))
    kw = (keyword or "").strip().lower()
    try:
        if p.name.endswith(".gz"):
            keep = deque(maxlen=tail)
            for line in _iter_lines(p):
                keep.append(line.rstrip("\n"))
            lines = list(keep)
        else:
            lines = _tail_text(p, tail)
    except OSError as e:
        return f"读取失败：{e}"
    if kw:
        lines = [ln for ln in lines if kw in ln.lower()]
    if not lines:
        return "(没有匹配的行)"
    return _clamp("\n".join(lines))


@tool(parse_docstring=True)
def search_logs(keyword: str = "", directory: str = "", tail: int = 200, alias: str = "") -> str:
    """在日志目录内检索包含关键词的行（返回 文件名:行号: 内容）。

    配合 IoTDB 工具：先拿到状态变化的时间点/关键词，再到日志里定位对应记录。

    Args:
        keyword: 必填，检索关键词（大小写不敏感）。
        directory: 可选，限定搜索的日志子目录；不传则递归搜索默认日志根目录。
        tail: 每个文件最多返回多少条匹配（默认 200）。
        alias: 可选，日志来源别名（见 list_log_sources）；不传用默认（第一个）。
    """
    kw = (keyword or "").strip()
    if not kw:
        return "缺少 keyword 参数：请提供检索关键词。可先 list_log_files() 看有哪些文件、read_log_file() 看文件内容，再确定关键词。"
    kw = kw.lower()
    base = _resolve(directory, alias) if directory else _log_dirs(alias)[0].resolve()
    if base is None:
        return f"目录 {directory!r} 不在允许的日志目录内。"
    if not base.exists() or not base.is_dir():
        return f"目录不存在：{base}"
    tail = max(1, min(int(tail), 2000))
    results: list[str] = []
    truncated = False
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        try:
            for i, line in enumerate(_iter_lines(p), 1):
                if kw in line.lower():
                    results.append(f"{p}:{i}: {line.rstrip()}")
                    if len(results) >= _MAX_MATCHES:
                        truncated = True
                        break
        except OSError:
            continue
        if truncated:
            break
    if not results:
        return f"在 {base} 下未找到包含 {keyword!r} 的行。"
    out = "\n".join(results)
    if truncated:
        out += "\n…(匹配过多，已截断)"
    return _clamp(out)
