"""Data Agent Web 服务 —— FastAPI + SSE 流式。

启动：
    cd DataAgent
    python app.py            # 或 uvicorn app:app --host 0.0.0.0 --port 8000
    # 浏览器打开 http://localhost:8000

memory：用 AsyncSqliteSaver 持久化到 agent_memory.sqlite，重启不丢。
线程隔离：thread_id = 前端传来的 session_id。
"""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

import memory as mem
from agent import (
    DEFAULT_PROVIDER,
    available_models,
    build_agent,
    build_system_prompt,
    format_llm_error,
)
from tools._settings import (
    add_or_update_database,
    add_or_update_iotdb,
    add_or_update_log_source,
    delete_database,
    delete_iotdb,
    delete_log_source,
    get_database,
    get_databases,
    get_default_alias,
    get_iotdb,
    get_iotdbs,
    get_log_dirs,
    get_log_sources,
)
from tools.database import test_connection
from tools.iotdb import test_iotdb_connection

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MEMORY_DB = BASE_DIR / "agent_memory.sqlite"

# session_id -> asyncio.Event：前端点「停止生成」时置位，用于取消该会话的流
_stop_events: dict[str, asyncio.Event] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """AsyncSqliteSaver 需要在 async 上下文里创建/关闭，放在 lifespan 里最干净。

    graph 在 startup 时编译，shutdown 时释放 SQLite 连接。
    """
    async with AsyncSqliteSaver.from_conn_string(str(MEMORY_DB)) as checkpointer:
        app.state.agent = build_agent(checkpointer)
        app.state.checkpointer = checkpointer
        print(f"✅ Data Agent ready, memory → {MEMORY_DB}")
        yield


app = FastAPI(title="Data Agent", lifespan=lifespan)

# 静态文件：index.html 及图表 PNG
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    """聊天页面（禁止缓存，保证前端改动即时生效）"""
    return HTMLResponse(
        content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/models")
async def models():
    """返回可用 LLM 后端清单，供前端下拉框渲染。"""
    return {"default": DEFAULT_PROVIDER, "models": available_models()}


@app.get("/databases")
async def list_databases():
    """列出所有已配置数据库（env + 网页新增），密码不返回，只给 has_password 标记。"""
    default = get_default_alias()
    out = []
    for d in get_databases():
        out.append(
            {
                "alias": d["alias"],
                "description": d.get("description", ""),
                "host": d.get("host", ""),
                "port": d.get("port", "5432"),
                "dbname": d.get("dbname", ""),
                "user": d.get("user", ""),
                "has_password": bool(d.get("password")),
                "source": d.get("source", "env"),
                "is_default": d["alias"] == default,
            }
        )
    return {"databases": out, "default": default}


@app.post("/databases")
async def save_database(request: Request):
    """新增/更新一个网页端数据库。密码留空时保留原有密码。"""
    body = await request.json()
    alias = (body.get("alias") or "").strip()
    host = (body.get("host") or "").strip()
    if not alias or not host:
        return {"ok": False, "error": "别名和主机地址必填"}
    add_or_update_database(
        {
            "alias": alias,
            "original_alias": (body.get("original_alias") or "").strip(),
            "description": body.get("description", ""),
            "host": host,
            "port": body.get("port", "5432"),
            "dbname": body.get("dbname", ""),
            "user": body.get("user", ""),
            "password": body.get("password", ""),
        }
    )
    return {"ok": True}


@app.delete("/databases/{alias}")
async def remove_database(alias: str):
    """删除一个数据库（env 或网页配置均可删；env 库会记进 deleted 列表，跨重启持久）。"""
    ok = delete_database(alias)
    if not ok:
        return {"ok": False, "error": "该库不存在"}
    return {"ok": True}


@app.post("/databases/test")
async def test_database(request: Request):
    """测试数据库连接（用请求体里的连接参数，不落盘）。

    密码留空时，若 original_alias/alias 对应一个已配置库，则复用该库的密码
    （env 或已保存的），避免「编辑时不改密码 → 测试却因无密码失败」。
    """
    body = await request.json()
    host = (body.get("host") or "").strip()
    if not host:
        return {"ok": False, "error": "主机地址必填"}
    password = body.get("password", "")
    if not password:
        ref = (body.get("original_alias") or body.get("alias") or "").strip()
        if ref:
            existing = get_database(ref)
            if existing:
                password = existing.get("password", "")
    cfg = {
        "host": host,
        "port": str(body.get("port", "5432") or "5432").strip(),
        "dbname": (body.get("dbname") or "").strip(),
        "user": (body.get("user") or "").strip(),
        "password": password,
    }
    ok, msg = await asyncio.to_thread(test_connection, cfg)
    return {"ok": ok, "message": msg}


@app.get("/settings")
async def get_settings():
    """返回全部连接配置（数据库 + IoTDB + 日志来源）。密码只给 has_password 标记。"""
    default = get_default_alias()
    dbs = [
        {
            "alias": d["alias"],
            "description": d.get("description", ""),
            "host": d.get("host", ""),
            "port": d.get("port", "5432"),
            "dbname": d.get("dbname", ""),
            "user": d.get("user", ""),
            "has_password": bool(d.get("password")),
            "source": d.get("source", "env"),
            "is_default": d["alias"] == default,
        }
        for d in get_databases()
    ]
    iotdbs = [
        {
            "alias": d["alias"],
            "host": d["host"],
            "port": d["port"],
            "user": d["user"],
            "has_password": bool(d.get("password")),
        }
        for d in get_iotdbs()
    ]
    log_sources = [{"alias": s["alias"], "dirs": s["dirs"]} for s in get_log_sources()]
    return {
        "databases": dbs,
        "default": default,
        "iotdbs": iotdbs,
        "log_sources": log_sources,
    }


@app.post("/settings/iotdb")
async def save_iotdb_config(request: Request):
    """新增/更新一个 IoTDB 连接（按别名；密码留空保留原密码）。"""
    body = await request.json()
    host = (body.get("host") or "").strip()
    if not host:
        return {"ok": False, "error": "主机地址必填"}
    add_or_update_iotdb(
        {
            "alias": body.get("alias") or "",
            "original_alias": body.get("original_alias") or "",
            "host": host,
            "port": body.get("port") or 6667,
            "user": body.get("user") or "",
            "password": body.get("password") or "",
        }
    )
    return {"ok": True}


@app.post("/settings/iotdb/test")
async def test_iotdb_config(request: Request):
    """测试 IoTDB 连接（用请求体里的参数，不落盘）。密码留空时复用该别名的密码。"""
    body = await request.json()
    host = (body.get("host") or "").strip()
    if not host:
        return {"ok": False, "error": "主机地址必填"}
    password = body.get("password") or ""
    alias = (body.get("alias") or "").strip()
    if not password and alias:
        existing = get_iotdb(alias)
        if existing:
            password = existing["password"]
    cfg = {
        "host": host,
        "port": body.get("port") or 6667,
        "user": body.get("user") or "",
        "password": password,
    }
    ok, msg = await asyncio.to_thread(test_iotdb_connection, cfg)
    return {"ok": ok, "message": msg}


@app.delete("/settings/iotdb/{alias}")
async def remove_iotdb_config(alias: str):
    """删除一个 IoTDB 连接。"""
    ok = delete_iotdb(alias)
    if not ok:
        return {"ok": False, "error": "该连接不存在"}
    return {"ok": True}


@app.post("/settings/logs")
async def save_log_source_config(request: Request):
    """新增/更新一个日志来源（按别名 + 目录，逗号或换行分隔）。"""
    body = await request.json()
    add_or_update_log_source(
        {
            "alias": body.get("alias") or "",
            "original_alias": body.get("original_alias") or "",
            "dirs": body.get("dirs") or [],
        }
    )
    return {"ok": True}


@app.delete("/settings/logs/{alias}")
async def remove_log_source_config(alias: str):
    """删除一个日志来源。"""
    ok = delete_log_source(alias)
    if not ok:
        return {"ok": False, "error": "该来源不存在"}
    return {"ok": True}


@app.get("/memory/export")
async def export_memory_file():
    """把全部会话记忆导出成一个 JSON 知识库文件（下载）。"""
    data = await mem.export_memory(app.state.checkpointer, str(MEMORY_DB))
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="dataagent-memory.json"'},
    )


@app.post("/memory/import")
async def import_memory_file(request: Request):
    """接收 JSON 知识库（body 直接是导出的那份 JSON），写回 checkpointer。

    同名 thread_id 会被覆盖为新版本；新的 thread_id 追加为新会话。
    """
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": "不是合法的 JSON"}
    if not isinstance(data, dict) or data.get("format") != mem.FORMAT:
        return {"ok": False, "error": "文件格式不匹配（应为 dataagent-memory 导出文件）"}
    count = await mem.import_memory(app.state.checkpointer, data)
    return {"ok": True, "imported_sessions": count}


@app.post("/chat")
async def chat(request: Request):
    """
    接收用户消息，通过 SSE 流式返回 Data Agent 的输出。

    请求体: {"message": "...", "session_id": "...", "provider": "ollama|openai", "api_key": "..."}
      provider / api_key 可选：provider 选 LLM 后端，api_key 仅 openai 后端需要（留空用 .env 默认）
    响应: text/event-stream，事件类型：
      {"type": "thinking",    "content": "<token>"}  思考过程（逐 token）
      {"type": "content",     "content": "<token>"}  最终回答（逐 token，Markdown）
      {"type": "tool_call",   "name": "...", "args": {...}}  工具调用（含 SQL/code 入参）
      {"type": "tool_result", "name": "...", "content": "...", "error": bool}  执行结果
      {"type": "chart",       "content": "<url>"}    生成的图表图片 URL
      {"type": "done"}                               流结束
      {"type": "error",       "content": "..."}      错误
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip() or "default"
    # 可选：前端传来的后端选择 + 自定义 API Key（留空则回退到 .env 默认值）
    provider = body.get("provider")
    api_key = (body.get("api_key") or "").strip() or None

    if not user_message:
        return StreamingResponse(
            _empty_stream("请输入内容"),
            media_type="text/event-stream",
        )

    agent = app.state.agent

    # checkpointer 按 thread_id 记忆；把前端 session_id 作为 thread_id
    # provider / api_key 透传给 agent，按请求选择 LLM 后端
    config: RunnableConfig = {
        "configurable": {
            "thread_id": session_id,
            "provider": provider,
            "api_key": api_key,
        }
    }
    inputs: dict = {
        "messages": [SystemMessage(content=build_system_prompt()), HumanMessage(content=user_message)],
    }

    # 注册本会话的停止事件，供 /stop 端点置位
    stop_event = asyncio.Event()
    _stop_events[session_id] = stop_event

    async def event_stream():
        gen = agent.astream(
            cast(dict, inputs),
            config=config,
            stream_mode=["custom", "updates"],
        )
        try:
            # stream_mode="custom"：节点 writer 推送的逐 token 事件
            # stream_mode="updates"：每个节点运行完后的增量状态
            async for mode, chunk in gen:
                if stop_event.is_set():
                    break
                if mode == "custom":
                    # chunk 是节点 writer 推的 dict（thinking/content/tool_call/tool_result/chart）
                    yield _sse_event(chunk)  # type: ignore[arg-type]
                # updates 无需转发
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not stop_event.is_set():
                yield _sse_event({"type": "error", "content": format_llm_error(e)})
        finally:
            if stop_event.is_set():
                # 被「停止」时显式关闭流，让底层图任务取消（例如打断超长 base64 生成）
                try:
                    await gen.aclose()
                except Exception:
                    pass
            _stop_events.pop(session_id, None)
            yield _sse_event({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


@app.post("/stop")
async def stop(request: Request):
    """停止某个会话正在进行的生成（前端「停止」按钮调用）。"""
    body = await request.json()
    session_id = body.get("session_id", "").strip()
    ev = _stop_events.get(session_id)
    if ev:
        ev.set()
    return {"ok": True, "stopped": session_id}


# ============================================================
# 工具函数
# ============================================================
def _sse_event(data: dict) -> str:
    """将 dict 序列化为一条 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _empty_stream(msg: str):
    """快速返回一条错误消息后结束"""
    yield _sse_event({"type": "error", "content": msg})
    yield _sse_event({"type": "done"})


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    import os

    import uvicorn

    # 端口可配置：DATA_AGENT_PORT 环境变量优先，默认 8001
    # （仓库里其他 agent 的 server.py 占用了 8000，所以这里避开）
    port = int(os.getenv("DATA_AGENT_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
