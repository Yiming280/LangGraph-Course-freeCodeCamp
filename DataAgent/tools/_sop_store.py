"""SOP 知识库存储：维修 SOP 条目，落盘 sop.json，可网页/工具增改删。

数据模型（灵活字段，兼容不同 SOP 文件的列）：
  - 每条 = { id, error_code(可空), title(必填，问题描述), cause_type, troubleshoot,
             solution, device, extra }。
  - 业务去重键 = error_code（非空时，大小写不敏感）否则 title（大小写不敏感）。
  - 导入/编辑时未识别的列进 extra（保留原字段，全文可搜）。

本模块以下划线开头，不会被 tools/__init__.py 当作工具扫描（同 _settings.py）。
"""
import csv
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any

_SOP_FILE = Path(__file__).resolve().parent.parent / "sop.json"

# 字段别名：Excel/CSV 列名 → canonical 字段（兼容不同文件的命名）
_ALIASES = {
    "error_code": ["错误码", "故障码", "错误代码", "故障代码", "errorcode", "errcode", "code", "编号"],
    "title": ["问题描述", "故障现象", "现象", "问题", "故障名称", "description", "symptom", "problem"],
    "cause_type": ["引起问题类型", "问题类型", "原因类型", "故障类型", "cause", "type", "类型"],
    "troubleshoot": ["排查方法", "排查步骤", "排查", "troubleshooting", "diagnosis"],
    "solution": ["解决方法", "处理方法", "处理步骤", "维修步骤", "解决", "solution", "措施"],
    "device": ["适用设备", "设备", "设备模块", "模块", "device", "machine"],
}
_CANONICAL = ["error_code", "cause_type", "troubleshoot", "solution", "device"]


def _norm(s: str) -> str:
    """列名归一化：去空白/下划线/中划线/斜杠，转小写。"""
    return re.sub(r"[\s_\-/]+", "", str(s)).lower()


# 预归一化别名集合：canonical 字段 -> 归一化后的别名集
_ALIAS_NORM = {f: {_norm(a) for a in aliases} for f, aliases in _ALIASES.items()}


def _read() -> list[dict]:
    if not _SOP_FILE.exists():
        return []
    try:
        data = json.loads(_SOP_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _write(entries: list[dict]) -> None:
    _SOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SOP_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _find(entries: list[dict], error_code: str, key: str) -> dict | None:
    """按去重键找已有条目：有 error_code 按码，无码按 title。"""
    kl = key.lower()
    for e in entries:
        ec = (e.get("error_code") or "").strip()
        if error_code:
            if ec.lower() == kl:
                return e
        else:
            if not ec and (e.get("title") or "").strip().lower() == kl:
                return e
    return None


def _full_text(e: dict) -> str:
    """拼一条记录的全部文本，供关键词检索。"""
    parts = [e.get("title", ""), e.get("error_code", ""), e.get("cause_type", ""),
             e.get("troubleshoot", ""), e.get("solution", ""), e.get("device", "")]
    for v in (e.get("extra") or {}).values():
        parts.append(str(v))
    return "\n".join(parts).lower()


# ------------------------------------------------------------
# 读
# ------------------------------------------------------------
def list_entries() -> list[dict]:
    """全部条目（含所有字段），按原始顺序。"""
    return _read()


def get_entry(error_code: str) -> dict | None:
    """按 error_code 精确匹配（大小写不敏感，仅对 error_code 非空条目）。"""
    key = (error_code or "").strip().lower()
    if not key:
        return None
    for e in _read():
        if (e.get("error_code") or "").strip().lower() == key:
            return e
    return None


def search_entries(keyword: str) -> list[dict]:
    """全字段子串匹配（覆盖无码条目）。"""
    kw = (keyword or "").strip().lower()
    if not kw:
        return []
    return [e for e in _read() if kw in _full_text(e)]


# ------------------------------------------------------------
# 写
# ------------------------------------------------------------
def upsert_entry(cfg: dict, fill_blank: bool = False) -> dict:
    """新增/更新一条 SOP。

    cfg: { id?(编辑用), error_code?, title(必填), cause_type?, troubleshoot?,
           solution?, device?, extra? }。
    fill_blank: True 时空的可选字段回退到已有值（模型部分编辑用，避免误清空）；
                False 时按传入值覆盖（网页/导入的整条编辑用）。
    """
    title = (cfg.get("title") or "").strip()
    if not title:
        raise ValueError("问题描述（title）必填")
    error_code = (cfg.get("error_code") or "").strip()

    entries = _read()
    existing = None
    eid = (cfg.get("id") or "").strip()
    if eid:
        existing = next((e for e in entries if e.get("id") == eid), None)
    if existing is None:
        key = error_code.lower() if error_code else title.lower()
        existing = _find(entries, error_code, key)

    def val(field: str) -> str:
        v = (cfg.get(field) or "").strip()
        if fill_blank and existing and not v:
            return (existing.get(field) or "").strip()
        return v

    extra = dict(existing.get("extra") or {}) if existing else {}
    extra.update(cfg.get("extra") or {})

    entry = {
        "id": existing["id"] if existing else uuid.uuid4().hex,
        "error_code": val("error_code"),
        "title": title,
        "cause_type": val("cause_type"),
        "troubleshoot": val("troubleshoot"),
        "solution": val("solution"),
        "device": val("device"),
        "extra": extra,
    }

    if existing:
        for i, e in enumerate(entries):
            if e.get("id") == existing["id"]:
                entries[i] = entry
                break
    else:
        entries.append(entry)
    _write(entries)
    return entry


def delete_entry(key: str) -> bool:
    """删除一条 SOP（key 可为 id / error_code / title，大小写不敏感）。返回是否真的删掉。"""
    kl = (key or "").strip().lower()
    if not kl:
        return False
    entries = _read()
    for i, e in enumerate(entries):
        if (
            e.get("id") == key
            or (e.get("error_code") or "").strip().lower() == kl
            or (e.get("title") or "").strip().lower() == kl
        ):
            entries.pop(i)
            _write(entries)
            return True
    return False


# ------------------------------------------------------------
# 导入（CSV）
# ------------------------------------------------------------
def _map_columns(fieldnames: list[str]) -> tuple[dict[str, str], list[str]]:
    """把 CSV 列名映射到 canonical 字段；未识别的列进 extra。返回 (field->列名, extra列)。"""
    mapping: dict[str, str] = {}
    for field in ("error_code", "title", "cause_type", "troubleshoot", "solution", "device"):
        for col in fieldnames:
            if _norm(col) in _ALIAS_NORM[field]:
                mapping[field] = col
                break
    used = set(mapping.values())
    extra = [c for c in fieldnames if c not in used]
    return mapping, extra


def import_csv(path: Any) -> tuple[int, int]:
    """导入一个 CSV 文件到 SOP 库。返回 (导入条数, 跳过条数)。

    编码按 utf-8-sig → gbk 回退（中文 Excel 另存 CSV 常为 GBK）。
    """
    path = Path(path)
    text = None
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if text is None:
        raise ValueError(f"无法读取 CSV（编码不支持）: {path}")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return 0, 0
    mapping, extra_cols = _map_columns(list(reader.fieldnames))

    imported = skipped = 0
    for row in reader:
        title_col = mapping.get("title")
        title = (row.get(title_col) or "").strip() if title_col else ""
        if not title:
            skipped += 1
            continue
        cfg: dict[str, Any] = {"title": title}
        for field in _CANONICAL:
            col = mapping.get(field)
            if col:
                cfg[field] = (row.get(col) or "").strip()
        extra = {c: (row.get(c) or "").strip() for c in extra_cols if (row.get(c) or "").strip()}
        if extra:
            cfg["extra"] = extra
        upsert_entry(cfg)
        imported += 1
    return imported, skipped


# ------------------------------------------------------------
# 导入（Excel .xlsx）
# ------------------------------------------------------------
def import_xlsx(path: Any) -> int:
    """从 Excel（.xlsx）导入维修 SOP，覆盖整个库（以 xlsx 为权威源）。

    xlsx 是合并单元格结构：表头含 问题描述/引起问题类型/排查方法/解决方法，
    同一个问题下面有多行、每行一种「引起问题类型」。
    导入时按「问题」聚合，多个类型及其排查/解决按段落拼进同一条目。返回导入条数。
    """
    path = Path(path)
    entries = _parse_sop_xlsx(path)
    _write(entries)
    return len(entries)


def _parse_sop_xlsx(path: Path) -> list[dict]:
    """解析 SOP xlsx（纯标准库）：共享字符串 + 合并单元格向下填充 + 按问题聚合。"""
    import zipfile
    import xml.etree.ElementTree as ET

    def local(tag: str) -> str:
        return tag.split("}")[-1]

    z = zipfile.ZipFile(str(path))

    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root:
            shared.append("".join(t.text or "" for t in si.iter() if local(t.tag) == "t"))

    sheet_files = sorted(
        n for n in z.namelist()
        if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
    )
    if not sheet_files:
        raise ValueError(f"xlsx 里没有工作表: {path}")

    def cell_value(c) -> str:
        t = c.get("t")
        val = None
        for ch in c:
            if local(ch.tag) == "v":
                val = ch.text
            elif local(ch.tag) == "is":
                val = "".join(x.text or "" for x in ch.iter() if local(x.tag) == "t")
        if val is None:
            return ""
        if t == "s":
            val = shared[int(val)]
        return (val or "").strip()

    root = ET.fromstring(z.read(sheet_files[0]))
    raw_rows: list[dict[str, str]] = []
    for row in root.iter():
        if local(row.tag) != "row":
            continue
        cells: dict[str, str] = {}
        for c in row:
            if local(c.tag) != "c":
                continue
            ref = c.get("r") or ""
            col = "".join(ch for ch in ref if ch.isalpha())
            v = cell_value(c)
            if v:
                cells[col] = v
        if cells:
            raw_rows.append(cells)

    # 表头：含「问题描述」的那一行
    header: dict[str, str] = {}
    header_idx = None
    for i, row in enumerate(raw_rows):
        if "问题描述" in row.values():
            header_idx = i
            for col, name in row.items():
                header[name] = col
            break
    if header_idx is None:
        raise ValueError(f"找不到表头（含「问题描述」的行）: {path}")

    def get(row: dict, field: str) -> str:
        col = header.get(field)
        return row.get(col, "") if col else ""

    # 逐行聚合：问题描述非空 = 新问题
    groups: list[dict] = []
    cur = None
    for row in raw_rows[header_idx + 1:]:
        desc = get(row, "问题描述")
        typ = get(row, "引起问题类型")
        ts = get(row, "排查方法")
        sol = get(row, "解决方法")
        if desc:
            if cur:
                groups.append(cur)
            cur = {"title": desc, "no": get(row, "序号"), "rows": []}
        if cur is None:
            continue
        if typ or ts or sol:
            cur["rows"].append((typ, ts, sol))
    if cur:
        groups.append(cur)

    entries: list[dict] = []
    for g in groups:
        rows = g["rows"]
        extra = {"序号": g["no"]} if g["no"] else {}
        entries.append(
            {
                "id": uuid.uuid4().hex,
                "error_code": "",
                "title": g["title"],
                "cause_type": " / ".join(t for t, _, _ in rows if t),
                "troubleshoot": "\n\n".join(f"【{t}】\n{s}" for t, s, _ in rows if s),
                "solution": "\n\n".join(f"【{t}】\n{s}" for t, _, s in rows if s),
                "device": "",
                "extra": extra,
            }
        )
    return entries
