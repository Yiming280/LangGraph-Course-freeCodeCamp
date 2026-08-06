import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_ollama import ChatOllama
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="LangGraph Chat Bot")

# ============================================================
# LLM 配置（沿用你现有的 Ollama 设置）
# ============================================================
llm = ChatOllama(
    base_url="http://10.8.20.83:11435",
    model="qwen3.6:27b",
    temperature=0.2,
)

system_prompt = SystemMessage(content=f"""
   你是一个具备深厚逻辑推理能力的大语言模型。
    无论用户提出什么问题，你都【必须】严格按照以下格式回答：

    <think>
    在这里写下你的详细思考过程、逻辑拆解、边界条件分析与方案对比。
    </think>

    在这里给出最终对用户展示的正式回答。""")


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
    响应: text/event-stream，每个 chunk 一个 SSE data 行
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    if not user_message:
        return StreamingResponse(
            _empty_stream("请输入内容"),
            media_type="text/event-stream",
        )

    messages = [system_prompt, HumanMessage(content=user_message)]

    async def event_stream():
        """
        SSE 事件流生成器。

        每条事件格式:
          data: {"content": "<token>", "done": false}
          data: {"content": "", "done": true}
        """
        try:
            async for chunk in llm.astream(messages):
                content = chunk.content
                if content:
                    yield _sse_event({"content": content, "done": False})

            # 流结束，发送完成信号
            yield _sse_event({"content": "", "done": True})

        except Exception as e:
            yield _sse_event({"content": f"\n[错误] {str(e)}", "done": True})

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
    yield _sse_event({"content": msg, "done": True})


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
