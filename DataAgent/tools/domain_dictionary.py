"""生产业务数据字典工具：把领域知识（字段定义、枚举含义、分析口径）按需提供给模型。

数据字典文件：domain/production_dictionary.md（业务人员可直接编辑，无需改代码）。
模型在分析生产数据前应调用 get_production_dictionary 获取业务口径，
否则无法正确理解 normal_end、metrics_name 等枚举字段的语义。
"""
from pathlib import Path

from langchain_core.tools import tool

_DICT_FILE = Path(__file__).resolve().parents[1] / "domain" / "production_dictionary.md"

# 章节标题 → 是否属于该章（按 Markdown 标题匹配，按需裁剪正文）
_SECTIONS = {
    "overview": ("## 1. 业务概览", "## 2."),
    "metrics": ("## 2. t_device_statistical_metrics", "## 3."),
    "agg": ("## 3. t_device_statistical_metrics_agg", "## 4."),
    "reports": ("## 4. t_prod_reports", "## 5."),
    "bottleneck": ("## 5. 生产瓶颈分析口径", None),
    "enums": ("## 枚举", "## 5."),
    "all": (None, None),
}


def _load_dict() -> str:
    try:
        return _DICT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"(数据字典文件未找到: {_DICT_FILE})"


def _slice_section(text: str, start: str | None, end: str | None) -> str:
    """截取 [start, end) 两个标题之间的内容。"""
    start_idx = text.index(start) if start else 0
    end_idx = text.index(end, start_idx) if end else len(text)
    return text[start_idx:end_idx].rstrip()


@tool
def get_production_dictionary(section: str = "overview", keyword: str = "") -> str:
    """查询生产业务的【数据字典】——字段定义、枚举含义、瓶颈分析口径。

    **分析生产数据前务必先调用本工具**，否则无法正确理解
    normal_end、metrics_name、metrics_type 等字段的业务语义。

    Args:
        section: 要查询的章节，可选：
            - "overview"（默认）：业务概览 + 三张表定位
            - "metrics"：t_device_statistical_metrics 设备状态明细表定义
            - "agg"：t_device_statistical_metrics_agg 聚合表定义
            - "reports"：t_prod_reports 任务报表定义（含 normal_end 枚举）
            - "enums"：所有枚举值含义汇总
            - "bottleneck"：生产瓶颈分析口径与关键指标
            - "all"：全部内容
        keyword: 可选，进一步过滤——返回含该关键词的段落（如 "normal_end"、"导航失败"）。

    Returns:
        数据字典正文。若命中 keyword，只返回相关片段。
    """
    text = _load_dict()
    if section not in _SECTIONS:
        section = "overview"
    start, end = _SECTIONS[section]
    body = _slice_section(text, start, end)

    kw = keyword.strip()
    if kw:
        lines = [ln for ln in body.splitlines() if kw in ln]
        if lines:
            return "数据字典片段（含关键词 %r）:\n" % kw + "\n".join(lines)
        return f"（未在章节 {section} 中找到关键词 {kw}，返回整章）\n" + body
    return body
