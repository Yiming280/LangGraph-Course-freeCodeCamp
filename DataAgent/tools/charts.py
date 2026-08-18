"""统计图工具：生成 histogram / bar / pie 图表 PNG。

设计遵循 dataviz 规范（已验证浅色 palette）：
  - bar 用 categorical slot1 蓝 #2a78d6
  - histogram 用同色系单色蓝（sequential）
  - pie 用固定顺序的 categorical 槽位，超过 8 类折叠为"其他"
  - 轴/标签用次级墨色，网格用发丝线

图表渲染为 PNG 保存到 static/charts/，返回图片 URL 给前端展示。
模型不需要搬运数据 —— 直接传 JSON 数组字符串即可，数据量过大工具内部会限长。
"""
import json
import uuid
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无界面后端，服务器安全

import matplotlib.pyplot as plt
from matplotlib import font_manager
from langchain_core.tools import tool

# ---- 中文字体：优先 Noto Sans CJK SC（系统已装），找不到则回退默认 ----
_CJK_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]
for _p in _CJK_CANDIDATES:
    if Path(_p).exists():
        font_manager.fontManager.addfont(_p)
        matplotlib.rcParams["font.family"] = font_manager.FontProperties(fname=_p).get_name()
        break

# ---- 已验证的浅色 palette（dataviz references/palette.md）----
COLOR_BLUE = "#2a78d6"        # categorical slot 1
CATEGORICAL = [
    "#2a78d6",  # blue
    "#eb6834",  # orange
    "#1baf7a",  # aqua
    "#eda100",  # yellow
    "#e87ba4",  # magenta
    "#008300",  # green
    "#4a3aa7",  # violet
    "#e34948",  # red
]
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
SURFACE = "#ffffff"

# 数据量上限（防模型传超大数组拖垮服务）
MAX_BAR_PIE = 200
MAX_HIST = 5000

CHARTS_DIR = Path(__file__).resolve().parents[1] / "static" / "charts"


def _ensure_charts_dir() -> None:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def _render(fig: matplotlib.figure.Figure, title: str) -> dict[str, Any]:
    """统一收尾：标题、保存 PNG、返回给 agent 的结构化结果。"""
    _ensure_charts_dir()
    if title:
        fig.suptitle(title, fontsize=14, color=INK_PRIMARY, y=0.99)
    fig.set_facecolor(SURFACE)

    name = f"{uuid.uuid4().hex[:16]}.png"
    path = CHARTS_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    return {
        "image": f"/static/charts/{name}",
        "summary": f"图表已生成（{title}）",
        "count": 0,
    }


def _tidy_hist(data: Any) -> list[float]:
    """把传入数据整理成合法的浮点数组，丢弃 None/NaN。"""
    try:
        raw = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError as e:
        raise ValueError(f"data 不是合法的 JSON 数组字符串: {e}")
    if not isinstance(raw, list):
        raise ValueError("data 必须是 JSON 数组字符串，例如 [1, 2, 3.5, ...]")

    out = []
    for x in raw:
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        out.append(f)
    return out


def _tidy_pairs(labels: Any, values: Any) -> list[tuple[str, float]]:
    """把 labels/values 整理成 (label, value) 对，丢弃无效项。"""
    try:
        lbls = json.loads(labels) if isinstance(labels, str) else labels
        vals = json.loads(values) if isinstance(values, str) else values
    except json.JSONDecodeError as e:
        raise ValueError(f"labels/values 不是合法的 JSON 数组字符串: {e}")
    if not isinstance(lbls, list) or not isinstance(vals, list):
        raise ValueError("labels 和 values 都必须是 JSON 数组字符串")
    if len(lbls) != len(vals):
        raise ValueError(f"labels 与 values 长度不一致: {len(lbls)} vs {len(vals)}")

    pairs = []
    for l, v in zip(lbls, vals):
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv != fv or fv == float("inf"):
            continue
        pairs.append((str(l), fv))
    return pairs


@tool
def create_bar_chart(labels: str, values: str, title: str = "", x_label: str = "", y_label: str = "") -> dict[str, Any]:
    """生成柱状图（bar chart）PNG。

    适合"各分类的数量/求和/占比"这类数据。数据较多时请先在 SQL 里
    GROUP BY + ORDER BY ... LIMIT 20 取 top-N，避免图太挤。

    Args:
        labels: 分类名数组的 JSON 字符串，如 '["A","B","C"]'。
        values: 对应数值数组的 JSON 字符串，如 '[10,20,15]'。
        title: 图表标题（中文）。
        x_label: x 轴标签，可选。
        y_label: y 轴标签，可选。
    """
    pairs = _tidy_pairs(labels, values)
    if not pairs:
        raise ValueError("没有有效的 (label, value) 数据")
    if len(pairs) > MAX_BAR_PIE:
        pairs = pairs[:MAX_BAR_PIE]

    fig, ax = plt.subplots(figsize=(max(6, min(14, 0.45 * len(pairs))), 5))
    cats = [p[0] for p in pairs]
    nums = [p[1] for p in pairs]

    ax.bar(cats, nums, color=COLOR_BLUE, width=0.72, edgecolor=SURFACE, linewidth=0.6)
    ax.set_xlabel(x_label, color=INK_SECONDARY) if x_label else None
    ax.set_ylabel(y_label, color=INK_SECONDARY) if y_label else None
    ax.tick_params(colors=INK_SECONDARY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(INK_MUTED)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)

    # 分类较少时在柱顶直接标数值（选择性直接标注，避免每根都标）
    if len(pairs) <= 20:
        for i, v in enumerate(nums):
            ax.text(i, v, f"{v:g}", ha="center", va="bottom",
                    fontsize=9, color=INK_PRIMARY)

    # 分类多时旋转标签防重叠
    if len(pairs) > 8:
        ax.tick_params(axis="x", rotation=45)

    result = _render(fig, title)
    result["count"] = len(pairs)
    return result


@tool
def create_pie_chart(labels: str, values: str, title: str = "") -> dict[str, Any]:
    """生成饼图（pie chart）PNG。

    适合"各分类占比"。超过 8 类时自动把最小的折叠为"其他"。
    优先在 SQL 里 GROUP BY + LIMIT 8 取主要分类。

    Args:
        labels: 分类名数组的 JSON 字符串，如 '["A","B"]'。
        values: 对应数值数组的 JSON 字符串，如 '[70,30]'。
        title: 图表标题（中文）。
    """
    pairs = _tidy_pairs(labels, values)
    if not pairs:
        raise ValueError("没有有效的 (label, value) 数据")

    # 固定顺序用 categorical 槽位；超过 8 类把最小的折叠进"其他"
    if len(pairs) > len(CATEGORICAL):
        pairs.sort(key=lambda p: p[1])
        other_sum = sum(v for _, v in pairs[len(CATEGORICAL) - 1:])
        pairs = pairs[:len(CATEGORICAL) - 1] + [("其他", other_sum)]
        pairs.sort(key=lambda p: p[1], reverse=True)

    cats = [p[0] for p in pairs]
    nums = [p[1] for p in pairs]
    colors = CATEGORICAL[:len(pairs)]

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    wedges, _texts, autotexts = ax.pie(
        nums,
        labels=cats,
        colors=colors,
        autopct="%.1f%%",
        startangle=90,
        counterclock=False,
        pctdistance=0.72,
        wedgeprops={"edgecolor": SURFACE, "linewidth": 1.2},
    )
    for t in _texts:
        t.set_color(INK_PRIMARY)
        t.set_fontsize(10)
    for at in autotexts:
        at.set_color(SURFACE)
        at.set_fontsize(9)

    result = _render(fig, title)
    result["count"] = len(pairs)
    return result


@tool
def create_histogram(data: str, bins: int = 20, title: str = "", x_label: str = "", y_label: str = "") -> dict[str, Any]:
    """生成直方图（histogram）PNG。

    适合"数值字段的分布"（如时长、数量、指标值的分布）。

    Args:
        data: 数值数组的 JSON 字符串，如 '[1.2, 2.5, 3.0, ...]'。最多 5000 个点。
        bins: 分箱数量，默认 20。
        title: 图表标题（中文）。
        x_label: x 轴标签，可选。
        y_label: y 轴标签，可选。
    """
    values = _tidy_hist(data)
    if len(values) < 2:
        raise ValueError("至少需要 2 个有效数值才能画直方图")
    values = values[:MAX_HIST]

    bins = int(bins)
    if bins < 2:
        bins = 20
    if bins > 100:
        bins = 100

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.hist(values, bins=bins, color=COLOR_BLUE, edgecolor=SURFACE, linewidth=0.6)
    ax.set_xlabel(x_label, color=INK_SECONDARY) if x_label else None
    ax.set_ylabel(y_label, color=INK_SECONDARY) if y_label else None
    ax.tick_params(colors=INK_SECONDARY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(INK_MUTED)
    ax.grid(axis="y", color=GRIDLINE, linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)

    result = _render(fig, title)
    result["count"] = len(values)
    return result
