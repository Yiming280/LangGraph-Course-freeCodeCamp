"""维修 SOP 知识库工具：检索 + 增改删维修 SOP 条目。

SOP 存 sop.json（结构化 JSON，error_code 可空、title 即问题描述必填）。
模型在用户问维修/故障/某个 error code 时，先 search_sop 查知识库；
用户要求新增/修改/删除 SOP 时，用 save_sop / delete_sop。

工具会自动注册进 tools 包（见 tools/__init__.py 的 pkgutil 扫描），无需改 agent 脚本。
"""
import json
from typing import Any

from langchain_core.tools import tool

from ._sop_store import delete_entry, get_entry, list_entries, search_entries, upsert_entry

MAX_RESULTS = 5


def _format_entry(e: dict) -> str:
    """把一条 SOP 格式化成给模型看的文本。"""
    code = e.get("error_code") or "无码"
    lines = [f"【{code}】{e.get('title', '')}"]
    if e.get("cause_type"):
        lines.append(f"问题类型：{e['cause_type']}")
    if e.get("device"):
        lines.append(f"适用设备：{e['device']}")
    if e.get("troubleshoot"):
        lines.append(f"排查方法：\n{e['troubleshoot']}")
    if e.get("solution"):
        lines.append(f"解决方法：\n{e['solution']}")
    extra = e.get("extra") or {}
    if extra:
        lines.append("其他字段：" + json.dumps(extra, ensure_ascii=False))
    return "\n".join(lines)


def _format_results(entries: list[dict], total: int) -> str:
    if not entries:
        return "SOP 知识库中没有找到相关内容。"
    head = f"命中 {total} 条 SOP（只列最相关的 {len(entries)} 条）：" if total > len(entries) else f"命中 {total} 条 SOP："
    parts = [head]
    for i, e in enumerate(entries, 1):
        parts.append(f"--- {i} ---\n{_format_entry(e)}")
    return "\n\n".join(parts)


def _format_all_entries(entries: list[dict]) -> str:
    """把全部条目全文格式化，供模型按语义挑选（无码/自然语言描述兜底用）。"""
    return "\n\n".join(f"--- {i} ---\n{_format_entry(e)}" for i, e in enumerate(entries, 1))


@tool
def search_sop(error_code: str = "", keyword: str = "") -> str:
    """检索维修 SOP 知识库，返回维修/故障处理的条目内容。

    当用户问维修、设备故障、某个 error code 怎么处理时调用本工具：
    - 用户给了 error code：传 error_code 精确查；未命中时转关键词/全量兜底。
    - 用户只说现象/描述（很多条目没有 error code）：传 keyword 做关键词检索；
      关键词没精确命中时，返回全部条目全文，交给模型做语义匹配（条目少，成本可控）。
    - 都为空：返回全部条目索引（error code + 问题描述），供浏览选择。

    Args:
        error_code: 错误码/故障码，如 "E1001"。可选。
        keyword: 现象/描述关键词，如 "主轴不转"。可选。

    Returns:
        匹配的 SOP 条目文本；无结果或空库时返回说明。
    """
    ec = (error_code or "").strip()
    kw = (keyword or "").strip()

    if ec:
        entry = get_entry(ec)
        if entry:
            return _format_entry(entry)
        results = search_entries(ec)
        if results:
            return _format_results(results[:MAX_RESULTS], len(results))

    if kw:
        results = search_entries(kw)
        if results:
            return _format_results(results[:MAX_RESULTS], len(results))

    entries = list_entries()
    if not entries:
        return "SOP 知识库为空，暂无条目。"

    # 给了查询但没精确命中 → 返回全部全文，让模型按语义挑（无码/自然语言描述主要走这里）
    if ec or kw:
        return _format_all_entries(entries)

    lines = ["SOP 知识库现有条目（error code | 问题描述）："]
    for e in entries:
        code = e.get("error_code") or "（无码）"
        lines.append(f"- {code} | {e.get('title', '')}")
    return "\n".join(lines)


@tool
def save_sop(
    title: str,
    error_code: str = "",
    cause_type: str = "",
    troubleshoot: str = "",
    solution: str = "",
    device: str = "",
) -> str:
    """新增或修改一条维修 SOP（按 error_code 或 title 去重，存在则覆盖）。

    用户明确要求"新增/修改/写一条 SOP"时调用；不要凭空编造维修步骤，以用户或
    知识库现有内容为准。编辑已有条目时：有 error_code 就传 error_code，否则传
    原 title 定位；留空的可选字段会保留原值（不会误清空）。

    Args:
        title: 问题描述/故障现象（必填），如 "主轴不转"。
        error_code: 错误码/故障码（可选，很多条目无码）。
        cause_type: 引起问题类型，如 "机械 / 电气 / 导航"（可选）。
        troubleshoot: 排查方法/步骤（可选）。
        solution: 解决方法/维修步骤（可选）。
        device: 适用设备/模块（可选）。
    """
    entry = upsert_entry(
        {
            "title": title,
            "error_code": error_code,
            "cause_type": cause_type,
            "troubleshoot": troubleshoot,
            "solution": solution,
            "device": device,
        },
        fill_blank=True,
    )
    code = entry.get("error_code") or "无码"
    return f"已保存 SOP：【{code}】{entry.get('title', '')}"


@tool
def delete_sop(key: str) -> str:
    """删除一条维修 SOP。

    Args:
        key: 要删除的条目的 error_code、问题描述（title）或 id。

    Returns:
        删除成功/失败说明。
    """
    ok = delete_entry(key)
    if ok:
        return f"已删除 SOP：{key}"
    return f"未找到要删除的 SOP：{key}"
