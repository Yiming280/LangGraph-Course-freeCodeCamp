"""Agent 错误分类法（Error Taxonomy）：结构化错误记录 + 聚合统计。

每条错误追加写一行 JSON（error_log.jsonl），字段见 record_error()。
分类分两级：
  error_stage  → 7 大错误类别（Tool Selection / Argument / Execution / ...）
  error_type   → 每个类别下的细分子类型（唯一归属一个大类）

统计时按 error_stage 聚合出 7 类占比，见 aggregate()。
本模块不依赖第三方库、用同步小写：错误路径低频，一次一行追加；且 should_continue
是同步函数也要能直接调用，故不引入 asyncio。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "error_log.jsonl"

# 版本号放在这里作为单一来源（agent.py 避免反向 import）。
# 改 SYSTEM_PROMPT 时 bump PROMPT_VERSION；改工具签名/描述时 bump TOOL_SCHEMA_VERSION。
PROMPT_VERSION = "2"
TOOL_SCHEMA_VERSION = "2"

# 7 大类别：error_stage → 展示标签
CATEGORIES: dict[str, str] = {
    "tool_selection": "Tool Selection Error",
    "argument_generation": "Argument Error",
    "tool_execution": "Execution Error",
    "result_parsing": "Result Parsing Error",
    "state_context": "State/Context Error",
    "planning": "Planning Error",
    "model_generation": "Model Capability Error",
}

# 子类型 → 大类：每个 error_type 唯一归属一个 error_stage
ERROR_TYPES: dict[str, str] = {
    # Tool Selection
    "unknown_tool": "tool_selection",
    "wrong_tool": "tool_selection",
    "redundant_call": "tool_selection",
    # Argument
    "missing_required_argument": "argument_generation",
    "empty_arguments": "argument_generation",
    "malformed_arguments": "argument_generation",
    "wrong_argument_type": "argument_generation",
    "invalid_enum_value": "argument_generation",
    # Execution
    "tool_exception": "tool_execution",
    "readonly_violation": "tool_execution",
    "timeout": "tool_execution",
    # Result parsing
    "malformed_result": "result_parsing",
    "unexpected_schema": "result_parsing",
    "empty_result": "result_parsing",
    # State / Context
    "context_truncation": "state_context",
    "orphan_tool_message": "state_context",
    # Planning
    "tool_loop": "planning",
    "max_rounds_exceeded": "planning",
    "premature_conclusion": "planning",
    # Model capability
    "empty_answer": "model_generation",
    "truncated_output": "model_generation",
    "llm_connection_error": "model_generation",
    "llm_timeout": "model_generation",
    "llm_status_error": "model_generation",
}


def record_error(
    *,
    task_id: str,
    error_type: str,
    tool: str | None = None,
    arguments: dict | None = None,
    message: str = "",
    retry_count: int = 0,
    model: str = "",
    provider: str = "",
) -> None:
    """追加一条结构化错误记录（JSONL 一行）。error_type 自动归到对应大类。

    记录失败（如目录不可写）静默忽略，绝不影响主流程。
    """
    stage = ERROR_TYPES.get(error_type, "unknown")
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": task_id or "",
        "tool": tool or None,
        "error_stage": stage,
        "error_type": error_type,
        "error_category": CATEGORIES.get(stage, stage),
        "arguments": arguments or {},
        "message": message or "",
        "tool_schema_version": TOOL_SCHEMA_VERSION,
        "model": model or "",
        "prompt_version": PROMPT_VERSION,
        "retry_count": retry_count,
        "provider": provider or "",
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def read_errors() -> list[dict]:
    """读取全部错误记录；文件不存在返回空，损坏行跳过。"""
    if not LOG_FILE.exists():
        return []
    out: list[dict] = []
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def aggregate(errors: list[dict] | None = None) -> dict:
    """聚合统计：7 大类计数 + 占比（按计数降序），以及每个 error_type 的计数。"""
    errors = errors if errors is not None else read_errors()

    cat_counts: dict[str, int] = {k: 0 for k in CATEGORIES}
    type_counts: dict[str, int] = {}
    for e in errors:
        stage = e.get("error_stage")
        if stage in cat_counts:
            cat_counts[stage] += 1
        t = e.get("error_type")
        if t:
            type_counts[t] = type_counts.get(t, 0) + 1

    total = len(errors)
    categories = [
        {
            "key": k,
            "label": label,
            "count": cat_counts[k],
            "percent": round(cat_counts[k] / total * 100, 1) if total else 0.0,
        }
        for k, label in CATEGORIES.items()
    ]
    categories.sort(key=lambda c: (-c["count"], c["key"]))

    return {
        "total": total,
        "categories": categories,
        "error_types": sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0])),
    }
