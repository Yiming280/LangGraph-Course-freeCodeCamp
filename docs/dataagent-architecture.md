# DataAgent 架构讲解

> 本文解释 `DataAgent/` 目录下每个文件的作用，以及**为什么这样写**——从 AI Agent 开发的角度。

---

## 目录

- [0. 一图看懂整体架构](#0-一图看懂整体架构)
- [1. 一条核心设计原则](#1-一条核心设计原则)
- [2. 文件逐层讲解](#2-文件逐层讲解)
- [3. 专题一：tools/__init__.py 的自动注册机制](#3-专题一tools__init__py-的自动注册机制)
- [4. 专题二：ReAct 循环的流式实现细节](#4-专题二react-循环的流式实现细节)
- [5. 专题三：知识外置 vs 写进 prompt，哪个好？](#5-专题三知识外置-vs-写进-prompt哪个好)
- [附录：关于「口径」这个词](#附录关于口径这个词)

---

## 0. 一图看懂整体架构

```
浏览器 ──SSE──► app.py（FastAPI 服务层）
                    │ build_agent(checkpointer)
                    ▼
              agent.py（LangGraph 智能体核心）
               ┌──────── ReAct 循环 ────────┐
               │  model 节点 ◄──► tools 节点 │
               └────────────────────────────┘
                    │ 调用                     ▲ 返回结果
                    ▼                          │
        tools/*.py（工具：查库 / 画图 / 查字典 / 查拓扑 / 跑代码）
                    │ 读取
                    ▼
        domain/*（领域知识：字典 markdown + 拓扑 json，可无代码改）
```

一句话：**agent.py 定义「怎么想」，tools/ 定义「能做什么」，domain/ 定义「知道什么」，app.py 定义「怎么对外服务」。**

---

## 1. 一条核心设计原则

整份代码几乎都围绕一条原则展开：

> **可变的知识当数据存，不变的逻辑当代码写。**

- agent 的「知识」（业务字段定义、供料拓扑）放在 `domain/` 下的文件里，业务人员能直接改，不用动代码。
- agent 的「行为」（怎么思考、调什么工具、分析流程）写在 `SYSTEM_PROMPT` 和工具逻辑里，是代码。
- 两者之间的接口，是**每个工具的 docstring（函数说明文字）**——模型读它就知道「什么时候该调这个工具、传什么参数」。

理解这条，下面每个文件「为什么这样写」就都通了。

---

## 2. 文件逐层讲解

### 2.1 服务层：`app.py` + `static/`

**[app.py](DataAgent/app.py)**：Web 入口，负责把 agent 的能力暴露成 HTTP + SSE（Server-Sent Events，服务器单向推送）。

为什么要单独拆一层，而不是把 FastAPI 写进 agent.py？

1. **关注点分离**：agent.py 只关心「模型怎么想、调什么工具」，完全不感知 HTTP。这样同一个 agent 既能被 Web 调用（`build_agent(checkpointer)`），也能被命令行直接调用（agent.py 末尾的调试入口），互不影响。
2. **SSE 流式**：`/chat` 用 `astream` + `stream_mode=["custom","updates"]` 逐 token 推给前端。事件类型（`thinking` / `content` / `tool_status` / `chart` / `done`）是**产品层约定**——把「模型在思考」和「模型在回答」分开推。这是 qwen3.6 这类会显式输出思考过程的模型才能做的体验优化。
3. **生命周期管理**：`lifespan` 里 `async with AsyncSqliteSaver` 保证记忆存储（checkpointer）在服务启动时创建、关闭时释放——这是异步资源的标准用法。

**[static/](DataAgent/static/)**：`index.html`（前端页面）+ `charts/`（运行时生成的图表 PNG，属于产物不是源码）。

---

### 2.2 智能体核心：`agent.py`

这是「大脑」，分四块讲。

#### ① StateGraph = ReAct 循环的骨架

```python
graph.add_node("model", model_call)      # 模型思考
graph.add_node("tools", call_tools)      # 执行工具
graph.add_conditional_edges("model", should_continue, {"tools": "tools", "end": END})
graph.add_edge("tools", "model")         # 工具结果喂回模型
```

这就是 LLM agent 最经典的 **ReAct（Reasoning + Acting）循环**：模型先思考 → 决定调工具 → 工具执行 → 结果喂回 → 再思考……直到不再调工具为止。

为什么用 LangGraph 而不是手写 `while` 循环？

- **状态管理**：`AgentState.messages` 用 `add_messages` 这个 reducer，新消息自动追加进历史——这就是「多轮记忆」的底层机制。
- **checkpointer**：把每一步状态持久化（`thread_id` = 前端 session_id），跨轮、跨重启都不丢。模型本身每次只看当前输入，是记不住上一轮的；正是靠 checkpointer 把历史塞回去，才能多轮对话。

#### ② `SYSTEM_PROMPT` = 模型的「人设 + 使用手册」

这是最关键的一段，做了三件事：

1. **列工具清单**（每个工具一行「什么时候用」）——告诉模型它有哪些能力。
2. **给工作流程**（先查字典 → 再查表 → 再画图）——教它按什么顺序思考。
3. **给业务规则**（瓶颈分析思路、产量=AMR 送料次数、供料拓扑归因……）——把领域专家的经验塞进去。

为什么这些放 prompt 而不是写死在代码里？因为 agent 的「智能」本质上就是**提示词 + 工具的组合**，模型的判断力来自这段文字。

#### ③ 用原生 `ollama.AsyncClient`，不用 LangChain 的 `ChatOllama`

注释里写了原因：`ChatOllama` 会丢弃 `thinking` 字段。qwen3.6 的思考在 `thinking`、回答在 `content`，两者都要在流式下逐 token 拿到，就必须用原生客户端。这是一个很实际的取舍——**框架好用，但框架抽象掉的东西（思考流）恰好是产品要的**。

#### ④ `convert_to_ollama` = 消息格式适配层

LangChain 的消息对象 ↔ Ollama 原生 dict 之间要互转，还顺手给工具调用补了 `id`（Ollama 的工具调用没有 id，而 LangChain 的 `ToolMessage` 需要）。这就是「胶水层」，连接两个不同的抽象体系。

---

### 2.3 工具层：`tools/`

这是「可扩展性」的关键，核心是 **[tools/__init__.py](DataAgent/tools/__init__.py) 的自动注册**（机制详见专题一）。

逐个工具看，理解「工具 = 模型的感知/执行器官」：

| 工具 | 角色 | 为什么这样写 |
|---|---|---|
| `database.py` | 模型的「眼睛」看数据库 | **只读护栏**：`startswith("SELECT")` 白名单，非 SELECT 直接拒绝——LLM 可能被诱导写出 `DROP`/`UPDATE`，这是安全边界。`RealDictCursor`（返回带列名）、`LIMIT 50`（控制上下文长度）、`connect_timeout=5`（数据库挂了快速失败）都是工程护栏 |
| `current_time.py` | 模型的「时钟」 | LLM 不知道「今天几号」，且三张表数据新鲜度不同。分析前先拿「今天 + 各表最新日期」，避免分析空数据 |
| `domain_dictionary.py` | 模型的「业务字典」 | **每次调用都读文件、不缓存**——这是「改字典不改代码」的机制根源。按章节裁剪 + 关键词过滤，本质是对一份精编 markdown 做的轻量检索 |
| `supply_chain.py` | 模型的「供料地图」 | 拓扑是**图结构**，所以做结构化查询（谁供料 / 谁被供 / 供料链）而不是把全文糊给模型。`db_matching` 教它怎么和数据库字段对上，`data_quality` 告诉它哪里有脏数据 |
| `rag.py` | 模型的「外部知识库」 | **懒加载 + 缓存**向量库：第一次调用才初始化，优先复用已持久化的 Chroma，没有才从 PDF 重建。和字典的区别：字典是**结构化业务定义**（用切片），RAG 是**非结构化文档**（用向量检索） |
| `charts.py` | 模型的「画图手」 | 模型只传 JSON 数组，工具负责渲染 PNG（`matplotlib.use("Agg")` 无界面后端）。好处：样式统一、模型不用写画图代码（不会跑偏）、图表事件直接流图片地址给前端。`MAX_BAR_PIE`/`MAX_HIST` 上限防模型传超大数组拖垮服务 |
| `code_runner.py` | 模型的「计算手」 | 通用 Python 兜底：SQL/图表覆盖不了的复杂计算。**子进程 + 临时文件 + 30 秒超时**是安全隔离——模型生成的代码不可信，必须在受控环境跑 |

**关键认知**：每个工具的 **docstring 就是给模型的「说明书」**。模型不是「看见」了函数实现，而是读 docstring 里的参数说明和「何时调用」来决定行为。所以写工具，**一半功夫在写 docstring**——它就是 prompt 的一部分。

---

### 2.4 领域知识层：`domain/`

**[domain/production_dictionary.md](DataAgent/domain/production_dictionary.md)**：业务数据字典。字段枚举（`normal_end=0/1/2/3` 各代表什么）、瓶颈分析规则、AMR 送料判定标准全在这。文件头写明「业务人员可随时修改，无需改代码」。

**[domain/supply_chain.json](DataAgent/domain/supply_chain.json)**：供料拓扑。和字典同一套路，但因为是**图结构**所以用 JSON 而不是 markdown。

为什么要把「知识」抽成数据文件，而不是写死在 prompt 里？三个理由：

1. **免代码维护**：业务规则会变（今天某台车覆盖 F01~F21，明天可能加一台）。业务人员改 markdown/json 比改 Python 安全得多。
2. **控制上下文**：字典很长，全塞进 prompt 每轮都发、又贵又挤。做成工具**按需取**，模型用到才查——这是「知识外置 + 工具检索」的思路。
3. **单一事实来源**：规则只在一处，agent 和人都看同一份。

---

### 2.5 配置与运行时

- **[requirements.txt](DataAgent/requirements.txt)**：依赖清单，声明式、可复现。
- **[.env.example](DataAgent/.env.example)**：连接配置模板（Ollama 地址、数据库凭据）。真实的 `.env` 在 gitignore 里不提交——密码不进版本库，这是安全惯例。
- **agent_memory.sqlite**：checkpointer 写的持久化记忆文件，**运行时产物**，不是源码（所以也在 gitignore）。

---

## 3. 专题一：tools/__init__.py 的自动注册机制

这是整个项目「加工具不改 agent」的支撑点，值得展开讲清楚。

### 3.1 `@tool` 装饰器做了什么

`tools/` 里每个工具函数头上都有一个 `@tool`（来自 `langchain_core.tools`）。这个装饰器把**一个普通 Python 函数**包装成一个 `BaseTool` 对象：

- **用函数的类型注解**（`sql: str`、`labels: str` …）自动生成参数 schema——模型调用时要按这个格式传参；
- **用函数的 docstring** 生成工具描述——模型靠它判断「什么时候该用、参数什么意思」。

所以「写一个工具」=「写一个带类型注解 + 详细 docstring 的普通函数」，装饰器负责把它变成模型能调用的东西。

### 3.2 `_discover_tools()` 的扫描流程

[tools/__init__.py](DataAgent/tools/__init__.py) 在**被 import 的那一刻**执行 `_discover_tools()`：

```python
pkg_path = Path(__file__).parent
for mod_info in pkgutil.iter_modules([str(pkg_path)]):   # 1. 列出 tools/ 下所有 .py 文件
    if mod_info.name.startswith("_"):
        continue                                          # 2. 跳过 _ 开头的辅助模块
    module = importlib.import_module(f"{_PACKAGE}.{mod_info.name}")  # 3. import 它
    for obj in vars(module).values():                     # 4. 遍历该模块顶层所有名字
        if isinstance(obj, BaseTool):                     # 5. 找出 @tool 装饰出来的工具对象
            TOOL_MAP[obj.name] = obj                      # 6. 按名字登记
            ALL_TOOLS.append(obj)                         # 7. 也放进列表
```

逐条解释：

1. **`pkgutil.iter_modules`**：列出包目录下所有模块文件（就是所有 `.py`）。这要求 `tools/` 是一个 Python 包（有 `__init__.py`）。
2. **跳过 `_` 前缀**：约定「下划线开头 = 辅助/私有模块，不当工具加载」。给写公共函数留的后门（比如 `_helpers.py` 放工具共用的工具函数）。
3. **`importlib.import_module`**：动态 import 这个模块。这一步会执行模块顶层代码——正是这一步让 `@tool` 装饰器运行、把函数变成工具对象。
4. **`vars(module).values()`**：拿到模块顶层所有变量（函数、类、常量…）。
5. **`isinstance(obj, BaseTool)`**：从一堆变量里筛出「被 `@tool` 装饰过的」那些。
6/7. **登记**：`TOOL_MAP`（名字 → 工具，供执行时按名查找）、`ALL_TOOLS`（列表，供转成模型可识别的格式）。

### 3.3 为什么 agent.py 完全不用改

agent.py 里只有两行和工具相关：

```python
from tools import ALL_TOOLS, TOOL_MAP
OLLAMA_TOOLS = [convert_to_openai_tool(t) for t in ALL_TOOLS]
```

它**消费** `ALL_TOOLS`/`TOOL_MAP`，但从不硬编码工具名单。所以你加一个工具 = 新建一个 `.py` 文件 → 被自动扫描 → 自动出现在 `ALL_TOOLS` 里。这正是**开放封闭原则**（对扩展开放、对修改封闭）：加能力不改已有代码。

### 3.4 两个韧性细节

- **`try/except` 跳过坏模块**：单个工具 import 失败只打警告、不影响其他工具。生产系统里这是必要的——你不会希望一个坏掉的图表库让整个 agent 挂掉。
- **模块加载失败只告警**：`logger.warning(...)` 后 `continue`，保证「一个坏了，其余照常」。

---

## 4. 专题二：ReAct 循环的流式实现细节

结合 [agent.py](DataAgent/agent.py) 真实代码，走一遍「一次用户提问」的完整数据流。

### 4.1 入口

```python
async for mode, chunk in agent.astream(
    {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_message)]},
    config=config,
    stream_mode=["custom", "updates"],
):
```

- `custom`：拿节点里 `writer()` 推送的自定义事件（思考/回答/工具状态/图表）。
- `updates`：每个节点跑完后的状态增量（这个项目里没转发给前端）。

### 4.2 `model_call` 节点：边生成边推流

```python
writer = get_stream_writer()                 # 拿到 custom 流的写入器
messages = convert_to_ollama(state["messages"])

async for part in await client.chat(model=MODEL, messages=messages,
                                    tools=OLLAMA_TOOLS, stream=True, ...):
    th = getattr(part.message, "thinking", "") or ""   # 思考 token
    ct = part.message.content or ""                    # 回答 token
    if th: writer({"type": "thinking", "content": th}) # 推「思考」
    if ct: writer({"type": "content",  "content": ct}) # 推「回答」
    if part.message.tool_calls: raw_tool_calls = part.message.tool_calls
```

关键点：

1. **`get_stream_writer()`** 是 LangGraph 的一个上下文变量，让节点能在图运行中途向外推消息（而不只是等节点结束）。
2. **`thinking` 和 `content` 分开推**：qwen3.6 是「推理型」模型，会先生成思考、再生成本体回答。产品上「思考」可以折叠成灰色、「回答」作为正文。
3. **工具调用也来自同一个流**：模型在思考/回答的过程中，可能决定要调工具，这时 `part.message.tool_calls` 会有值。

### 4.3 把工具调用装进 AIMessage

```python
for tc in raw_tool_calls:
    tool_calls.append({"id": f"call_{uuid.uuid4().hex[:12]}",
                       "name": tc.function.name,
                       "args": dict(tc.function.arguments), ...})
ai_msg = AIMessage(content=content, tool_calls=tool_calls, ...)
```

Ollama 的工具调用**没有 id**，而 LangChain 的 `ToolMessage` 需要 `tool_call_id` 来回填结果，所以这里用 `uuid` 现造一个。

### 4.4 `should_continue`：决定下一步

```python
last = state["messages"][-1]
if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
    return "tools"   # 模型要调工具 → 去执行
return "end"         # 模型不调工具了 → 结束
```

这就是 ReAct 循环的「终止条件」：**模型不再产生 tool_calls，就是它已经回答完了**。

### 4.5 `call_tools` 节点：执行并回填

```python
for tc in last.tool_calls:
    fn = TOOL_MAP.get(tc["name"])
    writer({"type": "tool_status", "content": f"🔧 调用 {tc['name']}..."})
    result = await fn.ainvoke(tc["args"])          # 真正执行工具
    if isinstance(result, dict) and result.get("image"):
        writer({"type": "chart", "content": result["image"]})  # 图表事件 → 前端内嵌图片
    new_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"], ...))
return {"messages": new_messages}
```

- 按 `tc["name"]` 去 `TOOL_MAP` 查对应函数，`ainvoke` 异步执行。
- 结果包成 `ToolMessage` 塞回消息历史，`tool_call_id` 对上号。
- 图表工具额外推一个 `chart` 事件，让前端把图片直接内嵌出来。

### 4.6 循环闭合

图里有一条边 `tools → model`。工具执行完，模型节点**再跑一次**，这次历史里多了 `ToolMessage`（工具结果），模型据此继续推理：可能再调下一个工具，也可能直接给出最终答案。如此往复，直到 `should_continue` 返回 `end`。

**为什么这样设计**：模型不是「一次想清楚」的，而是「边做边看」。给它工具结果当反馈，它才能修正、补充，最终收敛到完整答案。这正是 ReAct 的价值——**让模型通过与环境交互来逼近正确结果**。

---

## 5. 专题三：知识外置 vs 写进 prompt，哪个好？

给模型「知识」有两种方式，这是 LLM 应用里一个反复出现的决策。

### 5.1 两种做法

**A. 写进 prompt（内联知识）**：把知识直接写进 `SYSTEM_PROMPT` 里，模型每轮都带着它。

- 优点：模型一定看得到，没有额外工具调用，行为稳定可预期。
- 缺点：
  1. **上下文成本**：每轮都付全量知识的 token 费用，哪怕用不到的部分也在发。
  2. **过期难改**：知识变了要改代码 + 重新部署。
  3. **prompt 臃肿**：塞太多，模型注意力被稀释，反而更容易「不听指挥」（所谓「迷失在长上下文里」）。
  4. **非技术人员难维护**：改 prompt 得懂代码。

**B. 外置成工具/数据文件（检索式）**：知识放文件或数据库，做成工具按需检索。

- 优点：
  1. **按需付费**：只加载相关章节，上下文干净。
  2. **免代码修改**：业务人员改文件即可。
  3. **可无限扩展**：加知识不增大基础 prompt。
  4. **单一事实来源**。
- 缺点：
  1. **多一次工具调用**：有额外延迟。
  2. **依赖模型判断**：模型必须正确决定「现在该去查」——存在漏查风险（可用 prompt 引导缓解）。
  3. **结构更复杂**：多一层文件 + 工具。

### 5.2 本项目的选择：混合

```
SYSTEM_PROMPT（内联，小而稳定）
    ├─ 角色定义、工具清单、通用工作流
    └─ 高层次的业务规则（瓶颈分析思路、供料归因思路）

domain/*（外置，大而多变）
    ├─ production_dictionary.md：字段定义、枚举含义、统计规则
    └─ supply_chain.json：供料拓扑 + 数据库匹配规则

tools/rag.py（外置，非结构化）
    └─ 美股 PDF → 向量库按需检索
```

### 5.3 判断标准（一句话决策规则）

> **模型每轮都需要、或者用来定义「它是谁」的知识 → 写进 prompt；大、常变、或只是偶尔相关的知识 → 外置成工具。**

落到具体问题可以这么问自己：

- 这份知识**每次对话都用得上吗**？→ 是，放 prompt；否，外置。
- 它**多久变一次**？→ 常变，外置（改文件）；基本不变，可放 prompt。
- 它**多大**？→ 超过一屏，外置（不然 prompt 臃肿）。
- **谁维护**？→ 业务/非技术同学维护，外置；开发者维护，可放 prompt。

### 5.4 本质

这个取舍和 **RAG 的动机**是同一件事：当知识超出「能塞进上下文、且值得每轮都带」的量级时，就从「内联」切换到「按需检索」。它是一个光谱——**上下文工程（context engineering）**的核心就是在「内联」和「检索」之间找平衡：能塞的、常用的塞进去；塞不下的、不常用的，做成工具让模型自己取。

---

## 附录：关于「口径」这个词

「口径」在数据/业务语境里，指**大家事先约定好、统一的判定或统计标准**——让不同人、不同查询得到一致结果的那条规则。

几个例子（都来自本项目）：

- 「送料任务」的判定标准：什么算一次送料？这里约定为 `device_name LIKE 'AMR%'` 且 `action_type` 含「上下料」或「人工换料」。
- `normal_end=1` 的含义：约定「1 = 正常完成」，算成功率时只数 1，2/3 要单独看待。
- 「产量」的近似规则：没有直接的产量表，约定用「AMR 送料次数」近似产量。

英文里大致对应 **metric definition**、**data definition**、**counting rule**。本文里都换成了「统计规则 / 判定标准 / 字段定义 / 业务规则」这类更直白的说法。
