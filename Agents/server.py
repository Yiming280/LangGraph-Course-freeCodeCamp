import json
from pathlib import Path

import ollama
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="LangGraph Chat Bot")

# ============================================================
# Ollama 配置（远程服务器）
# ============================================================
OLLAMA_HOST = "http://10.8.20.83:11435"
MODEL = "qwen3.6:27b"
TEMPERATURE = 0.2

# 原生异步客户端：qwen3.6 会把"思考过程"放在 thinking 字段、
# "正式回答"放在 content 字段，两个字段都通过流式逐 token 返回。
# （注意：langchain 的 ChatOllama 会丢弃 thinking 字段，所以这里用原生客户端）
client = ollama.AsyncClient(host=OLLAMA_HOST)

SYSTEM_PROMPT = """你是一个具备深厚逻辑推理能力的大语言模型。
无论用户提出什么问题，你都应该先深入思考再回答：

1. 在内部拆解问题：用户真正想要什么？
2. 分析边界条件：问题的前提、限制与可能的多义性。
3. 对比不同方案：列出可行思路，说明各自的优劣，再选出最佳方案。
4. 然后给出简洁、准确、结构清晰的最终回答。

思考过程由系统自动捕捉并展示，你不需要输出任何标签或标记。
直接给出最终回答即可。"""


# ============================================================
# 路由
# ============================================================

@app.get("/")
async def root():
    """返回聊天页面"""
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.post("/chat")
async def chat(request: Request):
    """
    接收用户消息，通过 SSE 流式返回 LLM 输出。

    前端 fetch 请求体: {"message": "你好"}
    响应: text/event-stream，包含两类事件：
      {"type": "thinking", "content": "<token>"}  思考过程（逐 token）
      {"type": "content",  "content": "<token>"}  正式回答（逐 token）
      {"type": "done"}                              流结束
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return StreamingResponse(
            _empty_stream("请输入内容"),
            media_type="text/event-stream",
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    async def event_stream():
        """
        SSE 事件流生成器：把模型流式输出拆成 thinking / content 两条通道。
        """
        try:
            async for part in await client.chat(
                model=MODEL,
                messages=messages,
                stream=True,
                options={"temperature": TEMPERATURE},
            ):
                thinking = getattr(part.message, "thinking", "") or ""
                content = part.message.content or ""

                if thinking:
                    yield _sse_event({"type": "thinking", "content": thinking})
                if content:
                    yield _sse_event({"type": "content", "content": content})

                if part.done:
                    break

            yield _sse_event({"type": "done"})

        except Exception as e:
            yield _sse_event({"type": "error", "content": str(e)})
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
