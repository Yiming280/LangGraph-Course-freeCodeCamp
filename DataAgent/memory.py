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
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

FORMAT = "dataagent-memory"
VERSION = 1

# 读 checkpointer 落盘数据用的序列化器（与 AsyncSqliteSaver 默认的 JsonPlusSerializer 一致）
_serde = JsonPlusSerializer()


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


# ============================================================
# 会话列表 / 分页读取 / 删除（供前端「历史会话」侧栏使用）
# ============================================================
# 说明：checkpoint 里因 MAX_MESSAGES 只保留最近 30 条，完整历史在 writes 表里
# （每个 checkpoint 对 messages 通道只写一次增量）。这里按 checkpoint_id（UUIDv6，
# 时间有序，与 checkpointer.alist 的顺序一致）联读 writes，重建完整、有序的对话。


def _loads(type_: str, blob) -> Any:
    """反序列化 checkpointer 落盘的字节（异常时返回 None，避免单条脏数据拖垮列表）。"""
    try:
        return _serde.loads_typed((type_, blob))
    except Exception:
        return None


def _checkpoint_ts(checkpoint_blob) -> str:
    """从 checkpoint 的 msgpack 里取出 ts（时间戳字符串）。"""
    cp = _loads("msgpack", checkpoint_blob)
    return (cp or {}).get("ts") or ""


def _shorten(text: str, limit: int) -> str:
    """压成单行并截断，用于标题/预览。"""
    text = (text or "").replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _reconstruct_session(db_path: str, thread_id: str) -> tuple[list[dict], str]:
    """重建某会话的完整有序消息（去掉 system prompt），返回 (消息 dict 列表, 最新 ts)。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """SELECT type, value FROM writes
               WHERE thread_id = ? AND channel = 'messages'
               ORDER BY checkpoint_id, task_id, idx""",
            (thread_id,),
        ).fetchall()
        latest = conn.execute(
            """SELECT checkpoint FROM checkpoints
               WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1""",
            (thread_id,),
        ).fetchone()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for typ, value in rows:
        msgs = _loads(typ, value)
        if msgs is None:
            continue
        if not isinstance(msgs, list):
            msgs = [msgs]
        for m in msgs:
            d = _msg_to_dict(m)
            if d:
                items.append(d)
    latest_ts = _checkpoint_ts(latest[0]) if latest else ""
    return items, latest_ts


def list_sessions(db_path: str) -> list[dict]:
    """列出所有会话（id / 标题 / 预览 / 更新时间 / 消息数），按更新时间倒序。"""
    sessions = []
    for tid in _list_thread_ids(db_path):
        items, latest_ts = _reconstruct_session(db_path, tid)
        if not items:
            continue
        title = ""
        for d in items:
            if d.get("role") == "human":
                title = _shorten(d.get("content"), 40) or "（无文字）"
                break
        preview = ""
        for d in reversed(items):
            if d.get("role") in ("human", "ai") and (d.get("content") or "").strip():
                preview = _shorten(d.get("content"), 60)
                break
        sessions.append(
            {
                "id": tid,
                "title": title or "（无文字）",
                "preview": preview,
                "updated_at": latest_ts,
                "message_count": len(items),
            }
        )
    sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return sessions


def get_full_session(db_path: str, thread_id: str) -> list[dict] | None:
    """返回某会话完整有序消息（旧→新，去掉 system prompt）；无消息返回 None。"""
    items, _ = _reconstruct_session(db_path, thread_id)
    return items or None


def get_session_messages(
    db_path: str, thread_id: str, before: int | None = None, limit: int = 20
) -> dict | None:
    """分页读取某会话消息（由新到旧）。

    before：游标 = 当前已加载的最早消息索引；省略则取最新一段。
    返回 {messages, before, has_more, total}；会话不存在或无消息返回 None。
    """
    items, _ = _reconstruct_session(db_path, thread_id)
    if not items:
        return None
    total = len(items)
    if before is None:
        before = total
    else:
        before = max(0, min(before, total))
    start = max(0, before - limit)
    # 对齐到 turn 边界（human 开头），避免把一轮「提问→工具→回答」拆到两页
    while start > 0 and items[start].get("role") != "human":
        start -= 1
    return {
        "messages": items[start:before],
        "before": start,
        "has_more": start > 0,
        "total": total,
    }


async def delete_session(checkpointer, thread_id: str) -> bool:
    """删除一个会话（清空其全部 checkpoint + 写入）。不存在返回 False。"""
    cp = await checkpointer.aget({"configurable": {"thread_id": thread_id}})
    if not cp:
        return False
    await checkpointer.adelete_thread(thread_id)
    return True
