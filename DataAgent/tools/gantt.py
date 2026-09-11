"""甘特图工具：生成时间区间甘特图（Gantt chart）PNG。

与 charts.py 同一套 dataviz 规范（复用其 palette / 收尾逻辑）：
  - 每个任务一根横条，长度表示 start→end 时间跨度
  - 可选 groups 分组着色（循环用 categorical 槽位）
  - 渲染为 PNG 保存到 static/charts/，返回图片 URL，模型无需搬运 base64

模型不用自己写 matplotlib 画甘特图再转 base64 —— 直接传任务名 / 起止时间
JSON 数组字符串即可。
"""
import json
from datetime import datetime
from typing import Any

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from langchain_core.tools import tool

from .charts import CATEGORICAL, GRIDLINE, INK_MUTED, INK_SECONDARY, SURFACE, _render

# 任务条数上限：太多会挤成一团
MAX_TASKS = 40

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


def _parse_dt(value: Any) -> datetime:
    """把字符串解析成 datetime，容忍常见日期格式。"""
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_array(x: Any):
    """把「JSON 数组字符串 / 列表」解析成列表；空串 / None 视为无。"""
    if x is None:
        return None
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        return json.loads(s)
    return x


def _tidy_tasks(tasks: Any, starts: Any, ends: Any, groups: Any) -> list[dict]:
    """把 tasks/starts/ends/(groups) 整理成合法任务列表，丢弃无效项。"""
    tasks = _load_array(tasks)
    starts = _load_array(starts)
    ends = _load_array(ends)
    groups = _load_array(groups)

    if not isinstance(tasks, list) or not isinstance(starts, list) or not isinstance(ends, list):
        raise ValueError("tasks/starts/ends 都必须是 JSON 数组字符串")
    if not (len(tasks) == len(starts) == len(ends)):
        raise ValueError(f"tasks/starts/ends 长度不一致: {len(tasks)}/{len(starts)}/{len(ends)}")
    if groups is not None and (not isinstance(groups, list) or len(groups) != len(tasks)):
        raise ValueError("groups 必须是与 tasks 等长的 JSON 数组字符串")

    out = []
    for i in range(len(tasks)):
        try:
            s = _parse_dt(starts[i])
            e = _parse_dt(ends[i])
        except (ValueError, TypeError):
            continue
        if e <= s:
            continue
        out.append(
            {
                "label": str(tasks[i]),
                "start": s,
                "end": e,
                "group": str(groups[i]) if groups else "",
            }
        )
    return out


@tool
def create_gantt_chart(
    tasks: str,
    starts: str,
    ends: str,
    groups: str = "",
    title: str = "",
) -> dict[str, Any]:
    """生成甘特图（Gantt chart）PNG，展示各任务/工序的时间区间。

    适合"哪个任务从几点到几点""设备/工位占用时间段""生产计划排程"这类
    有明确 start/end 时间的数据。用户要甘特图 / 时间轴 / 排程 / 进度条 / 占用
    时间段时用本工具，不要用 python_executor 画图再输出 base64。

    Args:
        tasks: 任务名的 JSON 数组字符串，如 '["A","B","C"]'。
        starts: 对应开始时间的 JSON 数组字符串，如 '["2026-07-01 08:00","2026-07-01 09:00"]'。
        ends: 对应结束时间的 JSON 数组字符串，格式同 starts。
        groups: 可选，分组/类别名的 JSON 数组字符串（与 tasks 等长），同组同色；缺省则全部同色。
        title: 图表标题（中文）。
    """
    items = _tidy_tasks(tasks, starts, ends, groups)
    if not items:
        raise ValueError("没有有效的 (task, start, end) 数据")
    if len(items) > MAX_TASKS:
        items = items[:MAX_TASKS]

    # 分组着色：按 group 首次出现顺序分配 categorical 槽位
    groups_order: list[str] = []
    for it in items:
        if it["group"] and it["group"] not in groups_order:
            groups_order.append(it["group"])

    n = len(items)
    fig, ax = plt.subplots(figsize=(max(8, min(16, 0.6 * n)), max(4, min(12, 0.35 * n))))

    # y 轴：第一条任务画在最上面（y 越大越靠上）
    for row, it in enumerate(items):
        y = n - 1 - row
        if it["group"]:
            color = CATEGORICAL[groups_order.index(it["group"]) % len(CATEGORICAL)]
        else:
            color = CATEGORICAL[0]
        x = mdates.date2num(it["start"])
        width = mdates.date2num(it["end"]) - x
        ax.barh(y, width, left=x, height=0.6, color=color, edgecolor=SURFACE, linewidth=0.6)

    ax.set_yticks(range(n))
    ax.set_yticklabels([it["label"] for it in reversed(items)])

    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()  # 旋转日期标签防重叠

    ax.tick_params(colors=INK_SECONDARY)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(INK_MUTED)
    ax.grid(axis="x", color=GRIDLINE, linewidth=0.8, alpha=0.8)
    ax.set_axisbelow(True)

    if groups_order:
        handles = [
            plt.Rectangle((0, 0), 1, 1, color=CATEGORICAL[i % len(CATEGORICAL)])
            for i in range(len(groups_order))
        ]
        ax.legend(handles, groups_order, loc="upper right", frameon=False, fontsize=9)

    result = _render(fig, title)
    result["count"] = n
    return result
