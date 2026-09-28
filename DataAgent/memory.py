"""记忆导出/导入：把各会话的对话历史打包成可迁移的 JSON「知识库」。

导出：枚举 sqlite 里所有 thread_id，读取每个会话的最新 checkpoint，
      把 user / assistant / tool 消息序列化成 JSON（不含 system prompt 和思考过程）。
导入：解析 JSON，把每个会话的消息写回 checkpointer（同名 thread_id 会被覆盖为新版本，
      新 thread_id 追加为新会话）。

知识库文件格式（JSON）：
{
  "format": "dataagent-memory",
  "version": 1,
  "exported_at": "2026-09-28T12:00:00Z",
  "sessions": [
    {"thread_id": "s-xxx", "messages": [
      {"role": "human", "content": "..."},
      {"role": "ai", "content": "...", "tool_calls": [{"id","name","args"}]},
      {"role": "tool", "content": "...", "tool_call_id": "...", "name": "..."}
    ]}
  ]
}
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

FORMAT = "dataagent-memory"
VERSION = 1


def _content(m: BaseMessage) -> str:
    c = m.content
    return c if isinstance(c, str) else json.dumps(c, ensure_ascii=False, default=str)


def _msg_to_dict(m: BaseMessage) -> dict | None:
    """LangChain 消息 → 知识库 dict。system 消息/其它类型返回 None（不导出）。"""
    if isinstance(m, HumanMessage):
        return {"role": "human", "content": _content(m)}
    if isinstance(m, AIMessage):
        d = {"role": "ai", "content": _content(m)}
        if m.tool_calls:
            d["tool_calls"] = [
                {"id": tc.get("id", ""), "name": tc.get("name", ""), "args": tc.get("args", {})}
                for tc in m.tool_calls
            ]
        return d
    if isinstance(m, ToolMessage):
        return {
            "role": "tool",
            "content": _content(m),
            "tool_call_id": m.tool_call_id or "",
            "name": m.name or "",
        }
    return None


def _dict_to_msg(d: dict) -> BaseMessage | None:
    """知识库 dict → LangChain 消息。无法识别的 role 返回 None。"""
    role = d.get("role")
    content = d.get("content", "") or ""
    if role == "human":
        return HumanMessage(content=content)
    if role == "ai":
        tcs = [
            {
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": tc.get("name", ""),
                "args": tc.get("args", {}),
                "type": "tool_call",
            }
            for tc in (d.get("tool_calls") or [])
        ]
        return AIMessage(content=content, tool_calls=tcs)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=d.get("tool_call_id", ""),
            name=d.get("name") or None,
        )
    return None


def _list_thread_ids(db_path: str) -> list[str]:
    """枚举 sqlite 里所有 thread_id（只读连接，不干扰正在运行的服务）。"""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


async def export_memory(checkpointer, db_path: str) -> dict:
    """把所有会话的最新对话导出成知识库 dict。"""
    sessions = []
    for tid in _list_thread_ids(db_path):
        cp = await checkpointer.aget({"configurable": {"thread_id": tid}})
        if not cp:
            continue
        msgs = cp.get("channel_values", {}).get("messages", [])
        items = [d for d in (_msg_to_dict(m) for m in msgs) if d]
        if items:
            sessions.append({"thread_id": tid, "messages": items})
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sessions": sessions,
    }


async def import_memory(checkpointer, data: dict) -> int:
    """把知识库 dict 写回 checkpointer，返回导入的会话数。"""
    sessions = data.get("sessions", [])
    if not isinstance(sessions, list):
        return 0
    count = 0
    for s in sessions:
        tid = (s.get("thread_id") or "").strip()
        if not tid:
            continue
        msgs = [_dict_to_msg(d) for d in (s.get("messages") or [])]
        msgs = [m for m in msgs if m is not None]
        if not msgs:
            continue
        cid = str(uuid.uuid4())
        checkpoint = {
            "v": 1,
            "id": cid,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "channel_values": {"messages": msgs},
            "channel_versions": {"messages": str(uuid.uuid4())},
            "versions_seen": {},
        }
        await checkpointer.aput(
            {"configurable": {"thread_id": tid, "checkpoint_ns": "", "checkpoint_id": cid}},
            checkpoint,
            {},
            {},
        )
        count += 1
    return count
