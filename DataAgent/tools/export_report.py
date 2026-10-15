"""报告导出工具：把对话总结 + 图表 + 分析结论导出为 Markdown / PDF。

设计：
  - 总结由模型完成（它本来就负责写最终结论），本工具只做「渲染 + 落盘 + 返回下载链接」。
  - Markdown：直接写 .md 文件，图表用 ![](url) 引用。
  - PDF：无第三方依赖，用 matplotlib 的 PdfPages 排版 —— 标题/标题层级/列表/代码块/
    段落 + 内嵌图表 PNG（自包含，可离线查看）。中文字体复用 charts.py 已配置的 CJK 字体。
  - 产物保存到 static/reports/，返回 /static/reports/xxx 下载 URL 给前端/模型引用。
"""
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无界面后端，服务器安全

import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from langchain_core.tools import tool

from .charts import COLOR_BLUE, GRIDLINE, INK_MUTED, INK_PRIMARY, INK_SECONDARY, SURFACE

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
REPORTS_DIR = STATIC_DIR / "reports"
CHARTS_DIR = STATIC_DIR / "charts"

# 内容上限：防模型把超大文本塞进来拖垮 PDF 排版
MAX_CONTENT = 20000

# A4 页面（英寸）与边距
PAGE_W = 8.27
PAGE_H = 11.69
MARGIN_X = 0.7
MARGIN_TOP = 0.7
MARGIN_BOTTOM = 0.6


def _ensure_dir() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Markdown 解析（轻量自实现，避免引入 markdown 依赖）
# ============================================================
def _strip_inline(text: str) -> str:
    """去掉行内格式标记，只留纯文本（供 PDF 排版）。"""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # 行内图片交给 images 参数处理
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) → label
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    return text


# CJK 字体（Noto Sans CJK）不含 emoji / 装饰符号，PDF 排版时先去掉，避免渲染成豆腐块。
# 覆盖：补充平面 emoji、BMP 杂项符号/dingbat、变体选择符、零宽连接符。
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F‍]"
)


def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text)


def _parse_blocks(md: str) -> list[tuple[str, Any]]:
    """把 Markdown 切成块：("h1"|"h2"|"h3"|"h4", text) / ("para", text) /
    ("list", [(kind, text), ...]) / ("code", text) / ("hr", "")。"""
    blocks: list[tuple[str, Any]] = []
    lines = md.split("\n")
    n = len(lines)
    i = 0
    in_code = False
    code_buf: list[str] = []
    para_buf: list[str] = []
    list_buf: list[tuple[str, str]] = []

    def flush_para() -> None:
        nonlocal para_buf
        if para_buf:
            blocks.append(("para", " ".join(para_buf)))
            para_buf = []

    def flush_list() -> None:
        nonlocal list_buf
        if list_buf:
            blocks.append(("list", list_buf))
            list_buf = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                blocks.append(("code", "\n".join(code_buf)))
                code_buf = []
                in_code = False
            else:
                flush_para()
                flush_list()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        if not stripped:
            flush_para()
            flush_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para()
            flush_list()
            blocks.append((f"h{len(m.group(1))}", m.group(2)))
            i += 1
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            flush_para()
            flush_list()
            blocks.append(("hr", ""))
            i += 1
            continue

        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
        if m:
            flush_para()
            kind = "ol" if m.group(1)[0].isdigit() else "ul"
            list_buf.append((kind, m.group(2)))
            i += 1
            continue

        flush_list()
        para_buf.append(stripped)
        i += 1

    flush_para()
    flush_list()
    if in_code:
        blocks.append(("code", "\n".join(code_buf)))
    return blocks


# ============================================================
# PDF 排版（matplotlib PdfPages）
# ============================================================
def _char_width(ch: str) -> float:
    """估算字符宽度：CJK/全角算 1.0，ASCII 算 0.5。"""
    return 1.0 if ord(ch) >= 0x2E80 else 0.5


def _wrap_line(text: str, max_units: float) -> list[str]:
    """按估算宽度折行，优先在空格处断行。"""
    lines: list[str] = []
    cur = ""
    cur_w = 0.0
    last_space = -1
    for ch in text:
        if ch == "\n":
            lines.append(cur.rstrip())
            cur, cur_w, last_space = "", 0.0, -1
            continue
        w = _char_width(ch)
        if cur_w + w > max_units and cur:
            if last_space >= 0:
                lines.append(cur[:last_space].rstrip())
                cur = cur[last_space + 1:]
                cur_w = sum(_char_width(c) for c in cur)
                last_space = -1
            else:
                lines.append(cur)
                cur, cur_w = "", 0.0
        cur += ch
        cur_w += w
        if ch == " ":
            last_space = len(cur) - 1
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


class _Pdf:
    """极简 PDF 排版器：块状文本流 + 内嵌图片，自动分页。"""

    def __init__(self, path: Path):
        self.pdf = PdfPages(str(path))
        self.fig = None
        self.ax = None
        self.y = 0.0

    def _new_page(self) -> None:
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
        self.fig = plt.figure(figsize=(PAGE_W, PAGE_H))
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.axis("off")
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.y = 1.0 - MARGIN_TOP / PAGE_H

    def _ensure(self) -> None:
        if self.ax is None:
            self._new_page()

    def _line_h(self, fontsize: float, spacing: float) -> float:
        return (fontsize * spacing) / (PAGE_H * 72)

    def text(
        self,
        s: str,
        fontsize: float,
        weight: str = "normal",
        color: str = INK_PRIMARY,
        indent: float = 0.0,
        spacing: float = 1.5,
        pre: bool = False,
    ) -> None:
        s = _strip_emoji(s)
        if not s:
            return
        self._ensure()
        line_h = self._line_h(fontsize, spacing)
        usable_pt = (PAGE_W - 2 * MARGIN_X - indent) * 72
        max_units = max(1.0, usable_pt / fontsize)
        lines = s.split("\n") if pre else _wrap_line(s, max_units)
        x = (MARGIN_X + indent) / PAGE_W
        for ln in lines:
            if self.y - line_h < MARGIN_BOTTOM / PAGE_H:
                self._new_page()
            self.y -= line_h
            self.ax.text(x, self.y, ln, fontsize=fontsize, fontweight=weight,
                         color=color, va="top")

    def hr(self) -> None:
        self._ensure()
        self.y -= 0.012
        if self.y < MARGIN_BOTTOM / PAGE_H:
            self._new_page()
        y = self.y + 0.006
        self.ax.plot([MARGIN_X / PAGE_W, (PAGE_W - MARGIN_X) / PAGE_W], [y, y],
                     color=GRIDLINE, linewidth=1.0)
        self.y -= 0.012

    def image(self, png_path: Path, max_w: float = 6.0, max_h: float = 4.5) -> None:
        self._ensure()
        try:
            img = mpimg.imread(str(png_path))
        except Exception:
            return
        ih, iw = img.shape[:2]
        aspect = iw / ih
        disp_w = min(max_w, max_h * aspect)
        disp_h = disp_w / aspect
        fw = disp_w / PAGE_W
        fh = disp_h / PAGE_H
        if self.y - fh < MARGIN_BOTTOM / PAGE_H:
            self._new_page()
        self.y -= fh
        x0 = MARGIN_X / PAGE_W
        self.ax.imshow(img, extent=[x0, x0 + fw, self.y, self.y + fh],
                       aspect="auto", interpolation="bilinear")

    def close(self) -> None:
        if self.fig is not None:
            self.pdf.savefig(self.fig)
            plt.close(self.fig)
        self.pdf.close()


def _render_pdf(path: Path, title: str, blocks: list[tuple[str, Any]], image_paths: list[Path]) -> None:
    """把解析好的块 + 图片渲染成多页 PDF。"""
    doc = _Pdf(path)
    doc._new_page()

    # 标题 + 生成时间 + 分隔线
    doc.text(title, 18, weight="bold", spacing=1.4)
    doc.text(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", 9.5, color=INK_MUTED)
    doc.hr()

    ol_counter = 0
    for kind, payload in blocks:
        if kind in ("h1", "h2", "h3", "h4"):
            ol_counter = 0
            size = {"h1": 16, "h2": 14, "h3": 12.5, "h4": 11.5}[kind]
            doc.text(_strip_inline(payload), size, weight="bold", color=COLOR_BLUE)
        elif kind == "para":
            ol_counter = 0
            doc.text(_strip_inline(payload), 11, spacing=1.6)
        elif kind == "list":
            for k, item in payload:
                if k == "ol":
                    ol_counter += 1
                    marker = f"{ol_counter}. "
                else:
                    ol_counter = 0
                    marker = "• "
                doc.text(marker + _strip_inline(item), 11, indent=0.25)
        elif kind == "code":
            ol_counter = 0
            doc.text(payload, 9.5, color=INK_MUTED, indent=0.2, spacing=1.35, pre=True)
        elif kind == "hr":
            ol_counter = 0
            doc.hr()

    # 图表附录（图片自包含在 PDF 里）
    if image_paths:
        doc.text("图表附录", 13, weight="bold", color=INK_SECONDARY)
        for p in image_paths:
            doc.image(p)

    doc.close()


# ============================================================
# 工具
# ============================================================
# 匹配会话消息里出现的图表 URL（/static/charts/xxx.png）
_CHART_URL_RE = re.compile(r"/static/charts/[A-Za-z0-9_.\-]+\.(?:png|jpe?g|svg|webp)")


def _resolve_images(images: Any) -> list[Path]:
    """把 images 参数解析成本地图表文件路径列表（跳过不存在的）。"""
    if not images:
        return []
    try:
        raw = json.loads(images) if isinstance(images, str) else images
    except json.JSONDecodeError as e:
        raise ValueError(f"images 不是合法的 JSON 数组字符串: {e}")
    if not isinstance(raw, list):
        raise ValueError("images 必须是 JSON 数组字符串，例如 '[\"/static/charts/a.png\"]'")

    out: list[Path] = []
    seen: set[str] = set()
    for u in raw:
        s = str(u).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if s.startswith("/static/"):
            p = STATIC_DIR / s[len("/static/"):]
        elif s.startswith("static/"):
            p = STATIC_DIR / s[len("static/"):]
        else:
            p = CHARTS_DIR / Path(s).name
        if p.is_file():
            out.append(p)
    return out


def _build_markdown(title: str, content: str, image_urls: list[str]) -> str:
    """拼出最终 Markdown 文本。"""
    parts = [f"# {title}\n", f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n", content.rstrip(), ""]
    if image_urls:
        parts.append("\n## 图表\n")
        for u in image_urls:
            parts.append(f"![chart]({u})\n")
    return "\n".join(parts)


def _write_report(title: str, content: str, image_paths: list[Path], fmt: str) -> dict[str, Any]:
    """落盘 .md（+ 可选 .pdf）并返回结果 dict。两个入口（模型工具 / 按钮）共用。"""
    image_urls = [f"/static/charts/{p.name}" for p in image_paths]

    _ensure_dir()
    name = f"report_{uuid.uuid4().hex[:12]}"
    md_path = REPORTS_DIR / f"{name}.md"
    md_path.write_text(_build_markdown(title, content, image_urls), encoding="utf-8")

    url = f"/reports/{name}.md"
    if fmt == "pdf":
        pdf_path = REPORTS_DIR / f"{name}.pdf"
        _render_pdf(pdf_path, title, _parse_blocks(content), image_paths)
        url = f"/reports/{name}.pdf"

    return {
        "url": url,
        "summary": f"报告已导出：{title}（{'PDF' if fmt == 'pdf' else 'Markdown'}，含 {len(image_paths)} 张图表）",
        "format": fmt,
        "count": len(image_paths),
    }


def compose_conversation_markdown(messages: list[dict]) -> tuple[str, list[str]]:
    """把会话消息列表整理成报告正文 + 图表 URL 列表（去重、保持出现顺序）。

    messages 元素形如 {"role": "human"|"ai"|"tool", "content": "...", ...}。
    只保留「问/答」正文，tool 结果不进正文；图表 URL 从所有消息里提取。
    """
    body: list[str] = []
    charts: list[str] = []
    seen: set[str] = set()

    for m in messages:
        content = (m.get("content") or "").strip()
        for u in _CHART_URL_RE.findall(content):
            if u not in seen:
                seen.add(u)
                charts.append(u)

        role = m.get("role")
        if role == "human" and content:
            body.append(f"**问**：{content}")
        elif role == "ai" and content:
            body.append(f"**答**：\n\n{content}")

    return "\n\n".join(body), charts


def _default_title(messages: list[dict]) -> str:
    """用第一条用户提问截断作默认标题。"""
    for m in messages:
        if m.get("role") == "human" and (m.get("content") or "").strip():
            text = _strip_inline(m["content"].split("\n")[0])
            return text[:30] + ("…" if len(text) > 30 else "")
    return "数据分析报告"


def export_conversation_report(messages: list[dict], title: str = "", format: str = "pdf") -> dict[str, Any]:
    """由后端「生成报告」按钮调用：直接把当前会话（问/答 + 图表）导出为文档。

    不经过 LLM，点击即生成，确定性、秒回。
    """
    fmt = (format or "pdf").strip().lower()
    if fmt not in ("md", "pdf"):
        fmt = "pdf"

    body, chart_urls = compose_conversation_markdown(messages)
    title = (title or "").strip() or _default_title(messages)
    content = (body or "（本会话暂无内容）")[:MAX_CONTENT]
    image_paths = _resolve_images(chart_urls)
    return _write_report(title, content, image_paths, fmt)


@tool
def export_report(
    title: str,
    content: str,
    images: str = "",
    format: str = "md",
) -> dict[str, Any]:
    """把当前分析的总结导出成 Markdown / PDF 文档，返回下载链接。

    你（模型）负责总结：把本轮对话里用户的问题、你做的分析、关键数据/结论
    整理成 Markdown 写进 content；把要附上的图表 URL（之前图表工具返回的
    /static/charts/xxx.png）放进 images。工具负责渲染并生成可下载的文件。

    Args:
        title: 报告标题（中文）。
        content: 报告正文的 Markdown 字符串，含对话摘要、分析过程与结论。
        images: 要嵌入的图表 URL 数组的 JSON 字符串，如 '["/static/charts/a.png"]'，可空。
        format: 导出格式，"md"（Markdown，默认）或 "pdf"（PDF，图表自包含可离线查看）。
    """
    if not title.strip():
        raise ValueError("title 不能为空")
    content = content[:MAX_CONTENT]

    fmt = (format or "md").strip().lower()
    if fmt not in ("md", "pdf"):
        raise ValueError("format 只支持 'md' 或 'pdf'")

    image_paths = _resolve_images(images)
    return _write_report(title, content, image_paths, fmt)
