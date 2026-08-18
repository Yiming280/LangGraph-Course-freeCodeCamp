"""Data Agent —— LangGraph 图定义。

所有工具都来自 tools/ 包（自动发现注册），本文件只声明、不定义工具：
    from tools import ALL_TOOLS, TOOL_MAP

用途：
    - import get_agent() / build_agent(checkpointer) 供 app.py 使用
    - python agent.py 直接跑一个简单 CLI 流式入口（调试用）

memory：checkpointer 由外部传入。web 场景用 AsyncSqliteSaver（lifespan 里
async with 保证生命周期）；CLI 调试用内存 MemorySaver。
"""
import json
import uuid
from typing import Annotated, Sequence, TypedDict

import ollama
from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from tools import ALL_TOOLS, TOOL_MAP

load_dotenv()

# ============================================================
# 配置
# ============================================================
OLLAMA_HOST = "http://10.8.20.83:11435"
MODEL = "qwen3.6:27b"
TEMPERATURE = 0.2

# 原生异步客户端：qwen3.6 的思考在 thinking 字段、正式回答在 content 字段、
# 工具调用在 tool_calls 字段，三者都能在流式下逐 token 拿到。
# （langchain 的 ChatOllama 会丢弃 thinking 字段，所以用原生客户端）
client = ollama.AsyncClient(host=OLLAMA_HOST)

# 工具转换（langchain @tool → Ollama 能识别的 OpenAI 函数格式），模块级只算一次
OLLAMA_TOOLS = [convert_to_openai_tool(t) for t in ALL_TOOLS]

SYSTEM_PROMPT = """你是 Data Agent，一个能提取数据库数据、做分析和可视化的智能助手。

你有以下工具：
0. get_current_date() —— 获取今天日期和各表最新数据日期。
   **当用户没有指定分析时间段时，先调用它**，拿到 today 后默认分析【当日】数据；
   同时能看到各表数据最新到哪天，避免分析空数据。
1. get_production_dictionary(section, keyword) —— 【生产分析必读】查询业务数据字典：
   三张表的字段定义、枚举含义（normal_end / metrics_name / metrics_type）、瓶颈分析口径。
   **任何涉及生产数据的分析，先调用它理解业务语义，再写 SQL。**
   可指定章节：reports（任务报表+normal_end）、metrics（设备状态）、agg（聚合表）、
   bottleneck（分析口径）、enums（枚举汇总）；或用 keyword 精确检索某字段。
2. query_database(sql) —— 对 PostgreSQL（agile_dispatch 库）执行**只读** SELECT 查询。
   - 探索 schema 的常用查询：
     SELECT table_name FROM information_schema.tables WHERE table_schema='public';
     SELECT column_name, data_type FROM information_schema.columns WHERE table_name='表名';
   - 提取数据时，用 GROUP BY / ORDER BY ... LIMIT 控制行数，不要一次取全表。
3. create_histogram / create_bar_chart / create_pie_chart —— 生成统计图（PNG）。
   把查到的数据整理成 JSON 数组字符串传进去，工具会自动出图并返回图片地址。
   - bar / pie：适合各分类的数量、求和、占比，SQL 里 ORDER BY ... LIMIT 20 取 top-N。
   - histogram：适合数值字段的分布（如时长、数量），传原始数值数组即可。
4. python_executor(code) —— 在沙箱子进程里执行 Python 代码，做复杂分析、计算、批处理。

你的工作流程：
1. 遇到数据问题，先 query_database 探索相关表的结构。
2. 用 SELECT 提取所需数据（注意只读，不要写库）。
3. 需要可视化时，把数据整理成 JSON 数组传给图表工具，让工具生成图片。
4. 复杂的计算/清洗/多步分析用 python_executor 完成。
5. 最后用中文给出清晰、结构化的结论，必要时引用图表里的数字。

【生产瓶颈分析专项指引】
当用户让你分析"生产瓶颈"时，按以下思路：
1. 先 get_production_dictionary(section="bottleneck") 拿到分析口径。
2. 从 t_prod_reports 看整体：按 normal_end 分组统计任务结果；
   按 abnormal_info 分组归因（注意"导航失败"高频出现）；拆 task_used 时间结构。
3. 用 t_device_statistical_metrics_agg 定位瓶颈设备与瓶颈时段（window_type='hour'）。
4. 可视化输出：bar（设备/异常/状态对比）、pie（异常构成/耗时结构）、
   histogram（耗时分布）。
5. 结论给出可执行的改进建议（如：某设备待料占比高 → 优化供料；导航失败多 → 排查路径）。

【时间范围规则】
- 用户**没有指定时间段**时：先 get_current_date() 拿到 today，默认分析**当日**数据。
- 用户**指定了时间段**（如"7月1日"、"最近7天"、"7月1日~7月5日"）：按用户指定分析，不用默认。
- SQL 里过滤当日用半开区间，避免漏掉当天最后一条：
  `WHERE task_start_time >= 'YYYY-MM-DD 00:00:00' AND task_start_time < 'YYYY-MM-DD+1 00:00:00'`
- 若 get_current_date() 显示**当日没有数据**（如任务表数据滞后），如实告诉用户
  "当日无数据，最新到 X"，并主动按最新可用日期继续分析或询问用户。

【产量分析专项指引】
用户问"产量""产出""产量波动""送料次数"时：
1. **产量没有直接计数表，用 AMR 送料次数近似**（见字典第7章，**人工换料也算产量**）。
   送料任务口径：`WHERE device_name LIKE 'AMR%' AND (action_type LIKE '%上下料%' OR action_type LIKE '%人工换料%')`
2. 先 get_production_dictionary(section="bottleneck", keyword="送料") 拿到完整归因框架。
3. 默认按天聚合送料次数（用户指定粒度则按用户），找异常高/低点。
4. 对送料次数**少**的天下钻归因（异常高发? 设备离线? 数据滞后? 待料?）——
   按 abnormal_info / normal_end / 参与设备数 / 时段 逐项排查。
5. 可视化：line/bar（送料趋势）、pie（异常构成）、bar（设备贡献）。
6. 结论量化归因，如"某日送料少是因为导航失败 X 次，占当日异常 Y%"。

注意：
- 若工具返回错误，仔细阅读并修正后重试。
- 数据量大时优先用 SQL 聚合（COUNT/GROUP BY/SUM/AVG），不要把几千行都拿回来。
- 涉及枚举含义（normal_end=0/1/2/3 等）时以数据字典为准，不要自行猜测。"""


# ============================================================
# Agent 状态
# ============================================================
class AgentState(TypedDict):
    # add_messages reducer：新消息自动追加到历史（memory 的核心机制）
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ============================================================
# 消息转换（LangChain 消息 ↔ Ollama 原生格式）
# ============================================================
def convert_to_ollama(messages: Sequence[BaseMessage]) -> list[dict]:
    """把 LangChain 消息转成 Ollama 需要的 dict 格式。"""
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
                        "function": {"name": tc["name"], "arguments": tc["args"]},
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


# ============================================================
# 节点
# ============================================================
async def model_call(state: AgentState, config) -> AgentState:
    """模型节点：流式调用 LLM，边生成边把 thinking / content 推给 SSE。

    - 用 get_stream_writer() 推送自定义事件（配合 stream_mode="custom"）
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

        writer({"type": "tool_status", "content": f"🔧 调用 {tc['name']}..."})
        try:
            result = await fn.ainvoke(tc["args"])
        except Exception as e:
            result = f"工具执行失败: {e}"
            new_messages.append(
                ToolMessage(content=result, tool_call_id=tc["id"], name=tc["name"])
            )
            continue

        # 图表工具返回 {"image": url, ...} → 额外推一个 chart 事件给前端渲染图片
        if isinstance(result, dict) and result.get("image"):
            writer({"type": "chart", "content": result["image"]})
            writer(
                {
                    "type": "tool_status",
                    "content": f"✅ {tc['name']} 已生成图表 → {result['image']}",
                }
            )
            result = json.dumps(result, ensure_ascii=False)
        else:
            writer({"type": "tool_status", "content": f"✅ {tc['name']} = {str(result)[:200]}"})

        new_messages.append(
            ToolMessage(content=str(result), tool_call_id=tc["id"], name=tc["name"])
        )

    return {"messages": new_messages}


# ============================================================
# 建图
# ============================================================
def build_agent(checkpointer) -> Runnable:
    """编译 Data Agent graph。

    Args:
        checkpointer: LangGraph 检查点（内存 / SqliteSaver / AsyncSqliteSaver 均可）
    """
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
    return graph.compile(checkpointer=checkpointer)


# 延迟加载的默认 agent（CLI 调试用内存版；web 版在 app.py 的 lifespan 里用 AsyncSqliteSaver 重建）
_agent: Runnable | None = None


def get_agent() -> Runnable:
    """获取默认 agent（内存 checkpointer）。"""
    global _agent
    if _agent is None:
        from langgraph.checkpoint.memory import MemorySaver

        _agent = build_agent(MemorySaver())
    return _agent


def _save_graph_image() -> None:
    try:
        png = get_agent().get_graph().draw_mermaid_png()
        with open("agent_graph.png", "wb") as f:
            f.write(png)
        print("Graph saved to agent_graph.png")
    except Exception as e:
        print(f"(graph image not saved: {e})")


# ============================================================
# CLI 调试入口（流式，逐 token 打印）
# ============================================================
if __name__ == "__main__":
    import asyncio

    from langchain_core.messages import HumanMessage

    print("===== DATA AGENT (CLI debug) =====")
    print("type 'exit' to quit\n")

    agent = get_agent()
    _save_graph_image()

    async def cli():
        thread_id = "cli-session"
        messages: list[BaseMessage] = []
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() == "exit":
                break
            messages.append(HumanMessage(content=user_input))

            config = {"configurable": {"thread_id": thread_id}}
            async for mode, chunk in agent.astream(
                {"messages": [SystemMessage(content=SYSTEM_PROMPT), *messages]},
                config=config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    t = chunk.get("type")
                    if t in ("thinking", "content"):
                        print(chunk["content"], end="", flush=True)
                    elif t == "tool_status":
                        print(f"\n\n⏳ {chunk['content']}\n")
                    elif t == "chart":
                        print(f"\n\n🖼️ 图表: {chunk['content']}\n")
                    elif t == "error":
                        print(f"\n\n❌ {chunk['content']}")
            print("\n")

    asyncio.run(cli())
