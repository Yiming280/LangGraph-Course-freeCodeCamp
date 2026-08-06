import json
import uuid
from pathlib import Path
from typing import Annotated, Sequence, TypedDict, cast

from langchain_core.runnables import RunnableConfig

import ollama
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

load_dotenv()

app = FastAPI(title="LangGraph Chat Bot")

# ============================================================
# Ollama 配置
# ============================================================
OLLAMA_HOST = "http://10.8.20.83:11435"
MODEL = "qwen3.6:27b"
TEMPERATURE = 0.2

# 原生异步客户端：
#   - qwen3.6 的"思考过程"在 thinking 字段，正式回答在 content 字段
#   - 工具调用在 tool_calls 字段
#   - 三个字段都能在流式下逐 token 拿到
# （注意：langchain 的 ChatOllama 会丢弃 thinking 字段，所以这里用原生客户端）
client = ollama.AsyncClient(host=OLLAMA_HOST)

SYSTEM_PROMPT = """你是一个具备深厚逻辑推理能力的大语言模型。
无论用户提出什么问题，你都应该先深入思考再回答：

1. 在内部拆解问题：用户真正想要什么？
2. 分析边界条件：问题的前提、限制与可能的多义性。
3. 对比不同方案：列出可行思路，说明各自的优劣，再选出最佳方案。
4. 如果问题需要计算或查数据，调用提供的工具，不要自己心算。
5. 然后给出简洁、准确、结构清晰的最终回答。

你的思考过程由系统自动捕捉并展示给用户，你不需要输出任何标签或标记。
直接给出最终回答即可。"""


# ============================================================
# 工具定义（@tool，与项目其他 agent 风格一致）
# ============================================================

@tool
def add(a: int, b: int) -> int:
    """把两个整数相加。"""
    return a + b


@tool
def multiply(a: int, b: int) -> int:
    """把两个整数相乘。"""
    return a * b


TOOLS = [add, multiply]
TOOL_MAP = {t.name: t for t in TOOLS}
# 转成 Ollama 能识别的 OpenAI 函数格式
OLLAMA_TOOLS = [convert_to_openai_tool(t) for t in TOOLS]


# ============================================================
# LangGraph agent loop
# ============================================================

class AgentState(TypedDict):
    # add_messages reducer：新消息自动追加到历史（memory agent 核心）
    messages: Annotated[Sequence[BaseMessage], add_messages]


def convert_to_ollama(messages: Sequence[BaseMessage]) -> list[dict]:
    """把 LangChain 消息转成 Ollama 需要的 dict 格式。

    规则参考 langchain_ollama 源码：
      - HumanMessage / SystemMessage / ToolMessage → role 直接对应
      - AIMessage → role=assistant，保留 tool_calls 和 thinking（additional_kwargs）
      - ToolMessage → role=tool，带 tool_call_id
    """
    ollama_msgs: list[dict] = []
    for m in messages:
        extra: dict = {}
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
            if m.tool_calls:
                extra["tool_calls"] = [
                    {
                        "type": "function",
                        "id": tc["id"],
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["args"],
                        },
                    }
                    for tc in m.tool_calls
                ]
            thinking = m.additional_kwargs.get("reasoning_content")
            if thinking:
                extra["thinking"] = thinking
        elif isinstance(m, SystemMessage):
            role = "system"
        elif isinstance(m, ToolMessage):
            role = "tool"
            if m.tool_call_id:
                extra["tool_call_id"] = m.tool_call_id
        else:
            continue

        msg = {"role": role, "content": m.content}
        msg.update(extra)
        ollama_msgs.append(msg)
    return ollama_msgs


async def model_call(state: AgentState) -> AgentState:
    """模型节点：流式调用 LLM，边生成边把 thinking / content 推给 SSE。

    - 用 get_stream_writer() 推送自定义事件（配合外层 stream_mode="custom"）
    - 若模型要调工具，把 tool_calls 装进 AIMessage 返回
    """
    writer = get_stream_writer()
    messages = convert_to_ollama(state["messages"])

    thinking = ""
    content = ""
    raw_tool_calls = None

    try:
        async for part in await client.chat(
            model=MODEL,
            messages=messages,
            tools=OLLAMA_TOOLS,
            stream=True,
            options={"temperature": TEMPERATURE},
        ):
            m = part.message
            th = getattr(m, "thinking", "") or ""
            ct = m.content or ""

            if th:
                thinking += th
                writer({"type": "thinking", "content": th})
            if ct:
                content += ct
                writer({"type": "content", "content": ct})
            if m.tool_calls:
                raw_tool_calls = m.tool_calls
            if part.done:
                break
    except Exception as e:
        writer({"type": "error", "content": f"模型调用失败: {e}"})
        raise

    # Ollama 的 tool_call 没有 id，用 uuid 生成（langchain 的 ToolMessage 需要）
    tool_calls = []
    if raw_tool_calls:
        for tc in raw_tool_calls:
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "name": tc.function.name,
                    "args": dict(tc.function.arguments),
                    "type": "tool_call",
                }
            )

    ai_msg = AIMessage(
        content=content,
        additional_kwargs={"reasoning_content": thinking} if thinking else {},
        tool_calls=tool_calls,
    )
    return {"messages": [ai_msg]}


def should_continue(state: AgentState) -> str:
    """路由：模型要调工具则去 tools 节点，否则结束。"""
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "end"


async def call_tools(state: AgentState) -> AgentState:
    """工具节点：执行模型请求的工具调用，把结果作为 ToolMessage 回填。"""
    writer = get_stream_writer()
    last = state["messages"][-1]

    new_messages = []
    for tc in getattr(last, "tool_calls", []):
        fn = TOOL_MAP.get(tc["name"])
        if fn is None:
            new_messages.append(
                ToolMessage(
                    content=f"未知工具: {tc['name']}",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
            continue

        writer({"type": "tool_status", "content": f"🔧 调用 {tc['name']}{tc['args']}..."})
        try:
            result = await fn.ainvoke(tc["args"])
            writer({"type": "tool_status", "content": f"✅ {tc['name']} = {result}"})
        except Exception as e:
            result = f"工具执行失败: {e}"

        new_messages.append(
            ToolMessage(content=str(result), tool_call_id=tc["id"], name=tc["name"])
        )

    return {"messages": new_messages}


# 构建 graph：model → (should_continue) → tools → model → ... → END
# checkpointer（MemorySaver）：按 thread_id 保存每条消息，
# 天然实现跨轮记忆，会话隔离由 thread_id 承担
graph = StateGraph(AgentState)
graph.add_node("model", model_call)
graph.add_node("tools", call_tools)
graph.set_entry_point("model")
graph.add_conditional_edges(
    "model",
    should_continue,
    {"tools": "tools", "end": END},
)
graph.add_edge("tools", "model")
agent = graph.compile(checkpointer=MemorySaver())

# Save graph visualization to file
graph_png = agent.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as f:
    f.write(graph_png)
print("Graph saved to graph.png")

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
    接收用户消息，通过 SSE 流式返回 LangGraph agent 的输出。

    请求体: {"message": "...", "session_id": "..."}
    响应: text/event-stream，事件类型：
      {"type": "thinking",  "content": "<token>"}  思考过程（逐 token）
      {"type": "content",   "content": "<token>"}  正式回答（逐 token）
      {"type": "tool_status","content": "..."}       工具调用进度
      {"type": "done"}                               流结束
      {"type": "error",     "content": "..."}       错误
    """
    body = await request.json()
    user_message = body.get("message", "").strip()
    session_id = body.get("session_id", "").strip() or "default"

    if not user_message:
        return StreamingResponse(
            _empty_stream("请输入内容"),
            media_type="text/event-stream",
        )

    # checkpointer 按 thread_id 记忆；这里把前端 session_id 作为 thread_id
    config: RunnableConfig = {"configurable": {"thread_id": session_id}}
    inputs: AgentState = {
        "messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_message)],
    }

    async def event_stream():
        try:
            # stream_mode="custom"：模型节点 writer 推送的逐 token 事件
            # stream_mode="updates"：每个节点运行完后的增量状态
            async for mode, chunk in agent.astream(
                cast(AgentState, inputs),
                config=config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    # chunk 是模型节点 writer 推的 dict（thinking/content/tool_status）
                    yield _sse_event(chunk)  # type: ignore[arg-type]
                else:
                    # updates 是 {节点名: {字段: 新值}} 的增量，仅需转发
                    pass
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
