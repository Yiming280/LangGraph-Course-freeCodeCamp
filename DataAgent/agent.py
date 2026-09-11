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
import os
import re
import uuid
from typing import Annotated, Any, Sequence, TypedDict, cast

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
from tools._db_config import databases_summary

load_dotenv()

# ============================================================
# 配置
# ============================================================
# LLM 后端：ollama（qwen3.6:27b）| openai（OpenAI 兼容代理，qwen3.5:9b）
# 默认后端由 .env 的 LLM_PROVIDER 决定；网页端可在每次请求里用 provider/api_key 覆盖。
# 两个后端都用原生客户端：qwen 的思考在 thinking/reasoning_content 字段、回答在 content、
# 工具调用在 tool_calls，三者都能在流式下逐 token 拿到（langchain 的 ChatOllama 会丢思考字段）。
DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# ---- Ollama ----
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://10.8.20.94:11435")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.6:27b")

# ---- OpenAI 兼容代理（provider=openai 时使用）----
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://aiproxy.agile-robots.cn/v1")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "qwen3.5:9b")

TEMPERATURE = float(os.getenv("TEMPERATURE", "0.2"))


def available_models() -> list[dict]:
    """供前端渲染的可用后端清单（id 用于请求里的 provider 字段）。"""
    return [
        {"id": "ollama", "label": OLLAMA_MODEL, "hint": "Ollama 本地推理，无需 Key", "needs_key": False},
        {"id": "openai", "label": OPENAI_MODEL, "hint": "代理服务，需 API Key", "needs_key": True},
    ]


# 客户端按需懒加载。Ollama 无 key、全局共享一个；OpenAI 按 api_key 缓存（用户用自己的 key 时不串号）。
_ollama_client = None
_openai_clients: dict[str, Any] = {}


def _get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        import ollama

        _ollama_client = ollama.AsyncClient(host=OLLAMA_HOST)
    return _ollama_client


def _get_openai_client(api_key: str | None):
    key = (api_key or "").strip() or OPENAI_API_KEY
    if key not in _openai_clients:
        from openai import AsyncOpenAI

        _openai_clients[key] = AsyncOpenAI(base_url=OPENAI_BASE_URL, api_key=key)
    return _openai_clients[key]

# 工具转换（langchain @tool → OpenAI 函数格式，ollama/openai 通用），模块级只算一次
OPENAI_TOOLS = [convert_to_openai_tool(t) for t in ALL_TOOLS]

SYSTEM_PROMPT = """你是 Data Agent，一个能提取数据库数据、做分析和可视化的智能助手。

你有以下工具：
0. get_current_date(db="") —— 获取今天日期和各表最新数据日期。
   db 参数：数据库别名（见【可用数据库】清单）；用户提了库名/别名就传对应别名，没提则不传（默认库）。
   **当用户没有指定分析时间段时，先调用它**，拿到 today 后默认分析【当日】数据；
   同时能看到各表数据最新到哪天，避免分析空数据。
1. get_production_dictionary(section, keyword) —— 【生产分析必读】查询业务数据字典：
   三张表的字段定义、枚举含义（normal_end / metrics_name / metrics_type）、瓶颈分析口径。
   **任何涉及生产数据的分析，先调用它理解业务语义，再写 SQL。**
   可指定章节：reports（任务报表+normal_end）、metrics（设备状态）、agg（聚合表）、
   bottleneck（分析口径）、enums（枚举汇总）；或用 keyword 精确检索某字段。
2. query_database(sql, db="") —— 对 PostgreSQL 执行**只读** SELECT 查询。
   db 参数：数据库别名（见【可用数据库】清单）；用户提了库名/别名就传对应别名，没提则不传（默认库）。
   - 探索 schema 的常用查询：
     SELECT table_name FROM information_schema.tables WHERE table_schema='public';
     SELECT column_name, data_type FROM information_schema.columns WHERE table_name='表名';
   - 提取数据时，用 GROUP BY / ORDER BY ... LIMIT 控制行数，不要一次取全表。
3. create_histogram / create_bar_chart / create_pie_chart —— 生成统计图（PNG）。
   把查到的数据整理成 JSON 数组字符串传进去，工具会自动出图并返回图片地址。
   - bar / pie：适合各分类的数量、求和、占比，SQL 里 ORDER BY ... LIMIT 20 取 top-N。
   - histogram：适合数值字段的分布（如时长、数量），传原始数值数组即可。
4. python_executor(code) —— 在沙箱子进程里执行 Python 代码，做复杂分析、计算、批处理。
   没有专用图表工具时才用它画图：savefig 到 static/charts/<名称>.png 并 print 该路径，
   系统会自动在聊天里显示；禁止输出 base64。
5. search_stock_market(query) —— 【美股知识库检索】从《2024 美股市场表现》PDF 文档中
   检索相关内容。当用户询问 2024 年美股 / 股票市场表现（指数、板块、季度行情、涨跌幅等）
   时调用，用检索到的片段回答并引用来源。与生产数据库无关，只有明确问美股时再用。
6. get_supply_chain(node, direction) —— 【供料拓扑查询】查"谁给谁供料"的静态关系。
   分析某 CNC 待料/瓶颈前先调用它，找到该 CNC 的供料 AMR 及上游链（见下方专项指引）。
   拓扑只覆盖 HL/WL 车 + ZZT；库里另有 AMR-CS/AX/HP、AGV-CS 等车及 BI/BJ/BK/AQ 等工位不在拓扑内。
7. list_databases() —— 列出所有可用数据库的别名及用途。当不确定用户提到的库对应哪个别名时调用，
   再用返回的别名传给 query_database / get_current_date 的 db 参数。
7. create_gantt_chart(tasks, starts, ends, groups, title) —— 生成甘特图（时间区间/排程 PNG）。
   tasks / starts / ends / groups 都是 JSON 数组字符串（groups 可选）。用户要"甘特图/时间轴/
   排程/进度条/占用时间段"时用本工具，不要用 python_executor 画图再输出 base64。

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

【供料相关性归因专项指引】
分析某 CNC 待料/瓶颈时，按以下思路逐层追溯供料链：
1. get_supply_chain(node=该CNC, direction="feeder") 找到它的供料 AMR；
   再用 direction="path" 追到上游 AGV / 中转台 / 人工供料。
2. 供料任务关联：t_prod_reports WHERE device_name=该AMR AND target_device_name=该CNC
   （中转台 ZZT0X 用 target_device_name LIKE 'ZZT0X%'，因库里同时有 ZZT0X 与
   ZZT0X-PASSTHROUGH 两种写法，属同一物理中转台）。
   看 normal_end / task_used / abnormal_info 判断是否断供或送料异常。
3. 上游状态关联：t_device_statistical_metrics_agg WHERE device_name=上游车，
   看待机/离线/通讯中断时段是否与该 CNC 的待料时间重合。
4. 归因结论逐层量化：CNC 待料 ← 上游 AMR 断供 ← AGV 离线 / 中转台缺料 / 人工未供料，
   不要只看 CNC 单点数字。
5. 数据质量：get_supply_chain 返回里带 data_quality（已知数据库质量问题）。若发现某车
   喂了它 coverage 之外的工位，属数据库录入错误——指出该异常并建议核查数据库，
   不要自行改写/纠正数据。
注意：供料任务靠 device_name + target_device_name 关联，action_type 是泛化文案
（如【AMR】给【2-1夹区】【精雕】【上下料】）不含工位号，不能用来识别工位。

注意：
- 若工具返回错误，仔细阅读并修正后重试。
- 用户提问里提到某个库名/别名时，query_database / get_current_date 用 db 参数指定该别名；
  没提则不传 db（用默认库）。
- 数据量大时优先用 SQL 聚合（COUNT/GROUP BY/SUM/AVG），不要把几千行都拿回来。
- 涉及枚举含义（normal_end=0/1/2/3 等）时以数据字典为准，不要自行猜测。
- **禁止在回答或代码里输出 base64 / data:image 图片数据**：所有图表交给图表工具（返回
  图片 URL，前端会自动渲染）；python_executor 只做计算，不要用它画图、也不要 print 图片 base64。"""


def build_system_prompt() -> str:
    """SYSTEM_PROMPT + 运行时注入的可用数据库清单（来自 .env 配置）。"""
    return SYSTEM_PROMPT + databases_summary()


# ============================================================
# Agent 状态
# ============================================================
class AgentState(TypedDict):
    # add_messages reducer：新消息自动追加到历史（memory 的核心机制）
    messages: Annotated[Sequence[BaseMessage], add_messages]


# ============================================================
# 消息转换（LangChain 消息 ↔ OpenAI/Ollama 兼容格式）
# ============================================================
def convert_messages(messages: Sequence[BaseMessage], provider: str) -> list[dict]:
    """把 LangChain 消息转成 OpenAI/Ollama 兼容的 dict 格式。"""
    out: list[dict] = []
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
            # Ollama 需要把思考回填进 history；OpenAI 的 reasoning_content 是只读输出，不回传
            if provider == "ollama":
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
        out.append(msg)
    return out


# ============================================================
# 节点
# ============================================================
async def _stream_ollama(client, model, messages: list[dict], writer):
    """Ollama 后端：思考在 thinking 字段，tool_calls 一次性给出完整对象（无 id）。"""
    thinking = ""
    content = ""
    raw_tool_calls = None

    async for part in await client.chat(
        model=model,
        messages=messages,
        tools=OPENAI_TOOLS,
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
    return thinking, content, tool_calls


async def _stream_openai(client, model, messages: list[dict], writer):
    """OpenAI 兼容后端：思考在 reasoning_content 字段，tool_calls 按 index 流式累加。"""
    thinking = ""
    content = ""
    tool_calls_by_index: dict[int, dict] = {}

    async for chunk in await client.chat.completions.create(
        model=model,
        messages=cast(Any, messages),
        tools=cast(Any, OPENAI_TOOLS),
        stream=True,
        temperature=TEMPERATURE,
        # Qwen3 思考模式；若代理不接受该参数（报 400）则删掉这行，由服务端默认控制
        extra_body={"enable_thinking": True},
    ):
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        th = getattr(delta, "reasoning_content", "") or ""
        ct = delta.content or ""

        if th:
            thinking += th
            writer({"type": "thinking", "content": th})
        if ct:
            content += ct
            writer({"type": "content", "content": ct})
        if delta.tool_calls:
            for tc in delta.tool_calls:
                e = tool_calls_by_index.setdefault(
                    tc.index, {"id": "", "name": "", "args": ""}
                )
                if tc.id:
                    e["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        e["name"] += tc.function.name
                    if tc.function.arguments:
                        e["args"] += tc.function.arguments

    tool_calls = []
    for e in tool_calls_by_index.values():
        try:
            args = json.loads(e["args"])
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(
            {
                "id": e["id"] or f"call_{uuid.uuid4().hex[:12]}",
                "name": e["name"],
                "args": args,
                "type": "tool_call",
            }
        )
    return thinking, content, tool_calls


async def model_call(state: AgentState, config) -> AgentState:
    """模型节点：流式调用 LLM，边生成边把 thinking / content 推给 SSE。

    - 用 get_stream_writer() 推送自定义事件（配合 stream_mode="custom"）
    - 若模型要调工具，把 tool_calls 装进 AIMessage 返回
    - 后端由 config 里的 provider 决定（ollama / openai），缺省用 .env 的 LLM_PROVIDER
    """
    writer = get_stream_writer()

    # 每次请求读 provider/api_key（由 app.py 注入 config["configurable"]），缺省用 .env 默认值
    cfg = config.get("configurable", {}) or {}
    provider = (cfg.get("provider") or "").strip().lower() or DEFAULT_PROVIDER
    api_key = (cfg.get("api_key") or "").strip() or None

    messages = convert_messages(state["messages"], provider)

    try:
        if provider == "openai":
            client = _get_openai_client(api_key)
            thinking, content, tool_calls = await _stream_openai(client, OPENAI_MODEL, messages, writer)
        else:
            client = _get_ollama_client()
            thinking, content, tool_calls = await _stream_ollama(client, OLLAMA_MODEL, messages, writer)
    except Exception as e:
        writer({"type": "error", "content": f"模型调用失败: {e}"})
        raise

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


# 匹配工具结果里出现的图表图片路径（如 /static/charts/xxx.png）。
# 用于把「模型用 python_executor 等画图并保存到 static/charts」的结果也推给前端显示。
_CHART_PATH_RE = re.compile(r"/static/charts/[A-Za-z0-9_.\-]+\.(?:png|jpe?g|svg|webp)")


def _truncate_for_display(text: str, limit: int = 4000) -> str:
    """展示用结果截断（完整结果仍进 ToolMessage 供模型后续推理）。"""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…(结果过长，已截断)"


async def call_tools(state: AgentState) -> AgentState:
    """工具节点：执行模型请求的工具调用，把结果作为 ToolMessage 回填。

    推送两类结构化事件，供前端拆分「调用 / 代码 / 结果」：
      tool_call    → {"type","name","args"}       调用哪个工具 + 入参（含 SQL/code）
      tool_result  → {"type","name","content","error"}  执行结果
    图表工具额外推 chart 事件（图片 URL）。
    """
    writer = get_stream_writer()
    last = state["messages"][-1]

    new_messages = []
    for tc in getattr(last, "tool_calls", []):
        # 先推「调用 + 入参」，前端据此创建可折叠的工具块
        writer({"type": "tool_call", "name": tc["name"], "args": tc["args"]})

        fn = TOOL_MAP.get(tc["name"])
        if fn is None:
            new_messages.append(
                ToolMessage(
                    content=f"未知工具: {tc['name']}",
                    tool_call_id=tc["id"],
                    name=tc["name"],
                )
            )
            writer(
                {
                    "type": "tool_result",
                    "name": tc["name"],
                    "content": f"未知工具: {tc['name']}",
                    "error": True,
                }
            )
            continue

        try:
            result = await fn.ainvoke(tc["args"])
        except Exception as e:
            result = f"工具执行失败: {e}"
            new_messages.append(
                ToolMessage(content=result, tool_call_id=tc["id"], name=tc["name"])
            )
            writer(
                {
                    "type": "tool_result",
                    "name": tc["name"],
                    "content": result,
                    "error": True,
                }
            )
            continue

        # 图表工具返回 {"image": url, ...} → 额外推一个 chart 事件给前端渲染图片
        if isinstance(result, dict) and result.get("image"):
            writer({"type": "chart", "content": result["image"]})
            writer(
                {
                    "type": "tool_result",
                    "name": tc["name"],
                    "content": result.get("summary", "已生成图表"),
                }
            )
            result = json.dumps(result, ensure_ascii=False)
        else:
            text = str(result)
            # 结果里若引用了保存到 static/charts 的图片路径，也推 chart 事件让前端显示
            for url in dict.fromkeys(_CHART_PATH_RE.findall(text)):
                writer({"type": "chart", "content": url})
            writer(
                {
                    "type": "tool_result",
                    "name": tc["name"],
                    "content": _truncate_for_display(text),
                }
            )

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
                {"messages": [SystemMessage(content=build_system_prompt()), *messages]},
                config=config,
                stream_mode=["custom", "updates"],
            ):
                if mode == "custom":
                    t = chunk.get("type")
                    if t in ("thinking", "content"):
                        print(chunk["content"], end="", flush=True)
                    elif t == "tool_call":
                        args = chunk.get("args") or {}
                        code = args.get("sql") or args.get("code") or json.dumps(args, ensure_ascii=False)
                        print(f"\n\n🔧 调用 {chunk['name']}:\n{code}\n")
                    elif t == "tool_result":
                        flag = "❌" if chunk.get("error") else "✅"
                        print(f"\n{flag} {chunk['name']} = {chunk['content']}\n")
                    elif t == "chart":
                        print(f"\n\n🖼️ 图表: {chunk['content']}\n")
                    elif t == "error":
                        print(f"\n\n❌ {chunk['content']}")
            print("\n")

    asyncio.run(cli())
