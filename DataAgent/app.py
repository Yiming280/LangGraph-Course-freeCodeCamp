"""Data Agent Web 服务 —— FastAPI + SSE 流式。

启动：
    cd DataAgent
    python app.py            # 或 uvicorn app:app --host 0.0.0.0 --port 8000
    # 浏览器打开 http://localhost:8000

memory：用 AsyncSqliteSaver 持久化到 agent_memory.sqlite，重启不丢。
线程隔离：thread_id = 前端传来的 session_id。
"""
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

from agent import SYSTEM_PROMPT, build_agent

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MEMORY_DB = BASE_DIR / "agent_memory.sqlite"


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


@app.post("/chat")
async def chat(request: Request):
    """
    接收用户消息，通过 SSE 流式返回 Data Agent 的输出。

    请求体: {"message": "...", "session_id": "..."}
    响应: text/event-stream，事件类型：
      {"type": "thinking",   "content": "<token>"}   思考过程（逐 token）
      {"type": "content",    "content": "<token>"}   正式回答（逐 token）
      {"type": "tool_status","content": "..."}        工具调用进度
      {"type": "chart",      "content": "<url>"}      生成的图表图片 URL
      {"type": "done"}                                流结束
      {"type": "error",      "content": "..."}        错误
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip() or "default"

    if not user_message:
        return StreamingResponse(
            _empty_stream("请输入内容"),
            media_type="text/event-stream",
        )

    agent = app.state.agent

    # checkpointer 按 thread_id 记忆；把前端 session_id 作为 thread_id
    config: RunnableConfig = {"configurable": {"thread_id": session_id}}
    inputs: dict = {
        "messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_message)],
    }

    async def event_stream():
        try:
            # stream_mode="custom"：节点 writer 推送的逐 token 事件
            # stream_mode="updates"：每个节点运行完后的增量状态
            async for mode, chunk in agent.astream(
                cast(dict, inputs),
                config=config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    # chunk 是节点 writer 推的 dict（thinking/content/tool_status/chart）
                    yield _sse_event(chunk)  # type: ignore[arg-type]
                # updates 无需转发
        except Exception as e:
            yield _sse_event({"type": "error", "content": str(e)})
        finally:
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
