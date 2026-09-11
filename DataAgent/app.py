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
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent import build_agent, build_system_prompt, available_models, DEFAULT_PROVIDER
from tools._db_config import (
    add_or_update_user_database,
    delete_user_database,
    get_databases,
    get_default_alias,
)
from tools.database import test_connection

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
        print(f"✅ Data Agent ready, memory → {MEMORY_DB}")
        yield


app = FastAPI(title="Data Agent", lifespan=lifespan)

# 静态文件：index.html 及图表 PNG
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    """聊天页面"""
    return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))


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
    add_or_update_user_database(
        {
            "alias": alias,
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
    """删除一个网页端数据库（env 里的库不在其中，删不掉）。"""
    ok = delete_user_database(alias)
    if not ok:
        return {"ok": False, "error": "该库不存在或来自 env，不可删除"}
    return {"ok": True}


@app.post("/databases/test")
async def test_database(request: Request):
    """测试数据库连接（用请求体里的连接参数，不落盘）。"""
    body = await request.json()
    host = (body.get("host") or "").strip()
    if not host:
        return {"ok": False, "error": "主机地址必填"}
    cfg = {
        "host": host,
        "port": str(body.get("port", "5432") or "5432").strip(),
        "dbname": (body.get("dbname") or "").strip(),
        "user": (body.get("user") or "").strip(),
        "password": body.get("password", ""),
    }
    ok, msg = await asyncio.to_thread(test_connection, cfg)
    return {"ok": ok, "message": msg}


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
                yield _sse_event({"type": "error", "content": str(e)})
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
