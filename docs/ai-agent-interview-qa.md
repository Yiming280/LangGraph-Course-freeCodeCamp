# AI Agent 开发面试题库（按频率综合排序）

> 面向「AI Agent / LLM 应用 / LangGraph 开发」岗位的面试准备手册。
>
> **排序依据**：综合三项权重打分后由高到低排列——
> - **面试出现频率**（这个题会不会被问，权重 40%）
> - **实际开发遇到频率**（你上工时会不会真的用到，权重 35%）
> - **问题普遍性**（跨公司、跨框架是否通用，权重 25%）
>
> 每题给出：**考察点 → 参考答案 → 进阶追问 → 实战映射**（结合本仓库 `DataAgent/` 的真实实现，方便你「讲项目」时对上号）。

---

## 目录

- [Tier 1 · 必问必会（不答出来基本凉）](#tier-1--必问必会)
- [Tier 2 · 高频（决定你是否「做过项目」）](#tier-2--高频)
- [Tier 3 · 中频/进阶（拉开差距）](#tier-3--中频进阶)
- [一页纸速记 + 万能答题框架](#一页纸速记--万能答题框架)
- [面试前 30 分钟自测清单](#面试前-30-分钟自测清单)

---

# Tier 1 · 必问必会

> 这一层是「筛人线」：答不上来基本等于没有 Agent 实战经验。共 9 题，每题都要能脱口而出。

---

## 1. 什么是 AI Agent？它和一个普通的 LLM 调用有什么区别？

**考察点**：最基础的认知框架，判断你是否理解「Agent」这个被用滥的词到底指什么。

**参考答案**

- **LLM 调用**：`输入 → 一次推理 → 输出`，是「无状态的一次函数」。
- **Agent**：给 LLM 装上**感知-决策-行动**的闭环——它有一个**循环**（loop），能**调用工具**（tool）与环境交互，能根据工具返回的**观察（observation）**继续推理，直到任务完成。
- 一句话定义：**Agent = LLM（大脑）+ 工具（手脚）+ 记忆（上下文）+ 执行循环（控制流）**。
- 关键区别不在模型，而在**控制权**：普通调用是「人写死流程」，Agent 是「模型自己决定下一步做什么」。

**进阶追问**

- Agent 和 Workflow（工作流）的区别？→ Agent 的路径由模型在运行时动态决定（不确定、可分支、可循环）；Workflow 的路径是开发者预先写死的（确定、DAG）。
- 什么时候不该用 Agent？→ 流程固定、可控性强、成本敏感的场景，用 Workflow 更稳更便宜。

**实战映射**：`DataAgent/agent.py` 里 `model_call → should_continue → call_tools → model_call` 的循环，就是 Agent 的最小闭环——模型自己决定「要不要调工具、调哪个、什么时候停」。

---

## 2. 讲讲 ReAct 模式 / Agent 的执行循环是怎么跑起来的？

**考察点**：Agent 的核心机制，最常被要求「画出来」或「讲一遍」。

**参考答案**

ReAct = **Reasoning（推理）+ Acting（行动）**，循环三要素：

1. **Thought（思考）**：模型先推理「我现在该干嘛」。
2. **Action（行动）**：决定调用某个工具并生成参数。
3. **Observation（观察）**：工具返回结果，喂回模型，进入下一轮思考。

循环终止条件：模型不再产生工具调用，直接给出最终答案（或达到最大迭代次数强制结束）。

以「查数据库回答问题」为例：`用户问 → 模型想『要先看表结构』→ 调 query_database → 拿到 schema → 再想『写查询』→ 调 query_database → 拿到数据 → 生成结论`。

**进阶追问**

- 怎么防止死循环？→ 设 `max_iterations` 上限；模型连续重复调同一工具时提前打断；让模型在 prompt 里学会「工具结果已足够就停止」。
- ReAct 的缺点？→ token 消耗大（每轮都要重放全部历史）、慢、可能发散。

**实战映射**：本仓库 `ReAct.py` 是标准教学版（`model_call → should_continue → ToolNode`）；`DataAgent/agent.py` 是生产版——用 `should_continue` 判断最后一条 `AIMessage` 有没有 `tool_calls`，有则进 `tools` 节点，否则 `END`，`tools → model` 构成回环。

---

## 3. Function Calling / Tool Use 的原理是什么？工具是怎么被「喂」给模型的？

**考察点**：工具调用是 Agent 的命脉，几乎必问原理。

**参考答案**

- **核心**：把「函数」用 **JSON Schema** 描述出来（函数名、参数、参数类型、用途说明），随请求一起发给模型。模型不执行函数，只**输出「要调哪个函数 + 参数」的 JSON**。
- 流程：`开发者定义工具 + 描述 → 转成 schema 发给模型 → 模型返回 function call（name + arguments）→ 开发者解析并真正执行 → 把执行结果作为 tool message 回填 → 模型继续`。
- **关键认知**：模型「决定调用」和「真正执行」是**分离**的——执行在你的代码里，模型只是发出意图。

**进阶追问**

- 工具描述（docstring）为什么重要？→ 模型靠它判断「何时该用、参数怎么填」，描述越清晰准确率越高；这是「调出来的」不是「写出来的」。
- 并行工具调用？→ 一次返回多个 tool_calls，可并发执行再一起回填（本仓库 `call_tools` 里的 `for tc in tool_calls` 就是这个结构）。

**实战映射**：`DataAgent` 用 `@tool` + docstring 定义工具，`convert_to_openai_tool(t)` 统一转成 Ollama 能识别的 OpenAI 函数格式（`agent.py:49`）。注意 Ollama 的 tool_call 没有 `id`，代码里用 `uuid` 补上，因为 LangChain 的 `ToolMessage` 需要 `tool_call_id` 对齐——这是踩过的真实坑。

---

## 4. RAG 是什么？为什么 Agent 需要它？完整流程讲一遍？

**考察点**：RAG 是 LLM 应用最常见落地方向，和 Agent 强相关。

**参考答案**

- **RAG = Retrieval-Augmented Generation（检索增强生成）**：先从外部知识库检索相关内容，再把这些内容拼进 prompt，让模型基于检索结果回答。
- **为什么需要**：解决 LLM 三个固有缺陷——① 知识有截止日期（不知道新东西）；② 会「幻觉」（编造）；③ 不知道私有/内部知识。
- **完整流程（离线建库 + 在线查询两条线）**：
  - **离线**：`文档 → 清洗/切分（chunking）→ 向量化（embedding）→ 存入向量库（vector store）`。
  - **在线**：`用户问题 → 向量化 → 相似度检索 Top-K → 把片段拼进 prompt → LLM 生成带引用的答案`。

**进阶追问**

- RAG 和微调（fine-tune）选哪个？→ 需要「注入新知识/私有数据、可追溯来源」选 RAG；需要「改模型的风格/能力/格式」选微调；两者不冲突。
- 检索不准怎么调？→ 见 Tier 2 的「混合检索与重排」。

**实战映射**：`Agents/RAG_Agent.py` 是「PyPDFLoader → RecursiveCharacterTextSplitter → OllamaEmbeddings → Chroma → as_retriever(k=5) → retriever_tool」的完整 RAG 链路；`DataAgent` 的 SYSTEM_PROMPT 里把 `search_stock_market` 作为工具之一暴露给模型，实现「需要时检索、不需要时不用」。

---

## 5. Agent 的「记忆 / 多轮对话状态」是怎么管理的？短时和长时记忆的区别？

**考察点**：多轮对话是 Agent 必备能力，记忆设计是高频题。

**参考答案**

- **短期记忆（对话上下文）**：把当前会话的历史消息放进 prompt 一起发给模型。用**消息队列**维护，每次追加新消息。
- **长期记忆（跨会话持久化）**：把状态存到外部（数据库/向量库），下次会话能恢复。
- **实现层次**：
  1. 最朴素：把 `messages` 列表一路 append（本仓库 `Memory_Agent.py` 的 `conversation_history`）。
  2. 框架级：LangGraph 用 **checkpointer**（`MemorySaver` 内存 / `AsyncSqliteSaver` 落库），按 `thread_id` 隔离不同会话，自动保存/恢复整个图状态。
  3. 语义记忆：把历史对话向量化，检索式召回（和 RAG 共用一套向量库）。

**进阶追问**

- 上下文太长怎么办？→ 摘要（summary）、滑动窗口截断、淘汰旧消息（见第 6 题）。
- 多个用户怎么隔离？→ `thread_id` / `session_id` 做键，每个会话独立状态。

**实战映射**：`DataAgent` 用了两层——`AgentState.messages` 用 `add_messages` reducer（新消息自动追加，`agent.py:121`），web 场景用 `AsyncSqliteSaver`（`lifespan` 里 `async with` 保证生命周期），靠 `session_id` 隔离不同用户会话。这正是「Tool Calling 规范的 Memory 消息队列」——`convert_to_ollama` 把 `tool_calls` / `tool_call_id` / `thinking` 都原样保留，保证模型能正确关联「哪次工具调用返回了哪个结果」。

---

## 6. 上下文窗口 / Token 有限，怎么管理和优化？

**考察点**：工程落地绕不开的实际问题，考察你是否「真上过线」。

**参考答案**

- 问题本质：每轮都把全部历史重放，token 线性增长，会撞上限、变慢、变贵。
- 常用手段（按成本从低到高）：
  1. **滑动窗口**：只保留最近 N 条消息。
  2. **摘要压缩**：把老对话用 LLM 压成一段摘要，替代原始消息（LangGraph 的 `SummarizationNode` / `RemoveMessage`）。
  3. **消息裁剪**：丢弃最旧的、不重要的（`trim_messages`）。
  4. **工具结果截断**：大段工具返回只留关键部分（本仓库 `call_tools` 里 `str(result)[:200]` 只展示前 200 字给前端，实际回填可另行裁剪）。
  5. **Prompt caching**：把稳定的 system prompt + 工具定义放最前面，命中缓存省 token。

**进阶追问**

- 摘要丢了细节怎么办？→ 关键信息（结论、数字、已调用工具）不进摘要，单独保留。
- 多轮下来状态膨胀？→ 定期做「记忆整理」节点。

**实战映射**：`SYSTEM_PROMPT` 很长（含业务字典、专项指引），这是典型的「稳定前缀」，适合放缓存位；工具返回前做 `[:200]` 截断是轻量裁剪。可以进一步扩展摘要节点。

---

## 7. LangGraph 的 StateGraph 是怎么回事？状态（State）和 reducer 是什么？

**考察点**：既然你的技术栈是 LangGraph，框架核心概念必问。

**参考答案**

- **StateGraph**：把 Agent 建模成一张**有向图**——节点（node）= 处理逻辑，边（edge）= 控制流。用 `StateGraph(state)` 声明，`add_node` / `add_edge` / `add_conditional_edges` 搭结构，`compile()` 编译成可执行对象。
- **State（状态）**：在节点间流转的共享数据，用 `TypedDict` 定义字段。
- **reducer（归约器）**：定义「新值如何写入状态」。默认是**覆盖**；`messages` 字段用 `Annotated[Sequence[BaseMessage], add_messages]` 表示**追加**——`add_messages` 会把新消息 append 到历史，而不是覆盖。这就是记忆的核心机制。

**进阶追问**

- 为什么要用 `Annotated[..., add_messages]`？→ 因为多轮消息要累积，不能每轮覆盖；reducer 让「状态更新规则」和「状态结构」解耦。
- `conditional_edges` 干嘛的？→ 根据当前状态动态决定下一条边（如「有 tool_calls 去 tools，否则结束」）。

**实战映射**：`agent.py` 的 `AgentState` 用 `messages: Annotated[Sequence[BaseMessage], add_messages]`；`build_agent` 里 `set_entry_point("model")` + `add_conditional_edges("model", should_continue, {"tools":"tools","end":END})` + `add_edge("tools","model")` 就是完整的 ReAct 图。

---

## 8. 流式输出（Streaming）是怎么实现的？SSE 和 WebSocket 选哪个？

**考察点**：体验好坏的决定因素，前端交互岗位几乎必问。

**参考答案**

- **为什么**：LLM 生成慢（秒级），不流式用户会以为卡死；逐 token 推送能显著提升体感。
- **服务端**：模型接口开 `stream=True`，逐 chunk 拿 `delta`；LangGraph 用 `astream(stream_mode=["custom","updates"])`，在节点里用 `get_stream_writer()` 推自定义事件。
- **传输协议**：
  - **SSE（Server-Sent Events）**：单向、服务器→客户端，基于 HTTP，实现简单、天然断线重连，适合「回答逐字推送」。
  - **WebSocket**：双向、全双工，适合「需要客户端持续发消息/双向实时」。
  - 纯「AI 回答流式」用 SSE 足够且更简单；要做「多轮交互 + 主动推送」才上 WebSocket。

**进阶追问**

- 流式时怎么处理「思考过程」和「正式回答」？→ 某些模型（如 qwen3.6）把 thinking 和 content 分字段，需要分别流式转发（见实战映射）。
- 断线了怎么办？→ SSE 有 `retry`；记录已发送 token，重连续传。

**实战映射**：`DataAgent/app.py` 用 FastAPI 的 `text/event-stream` 做 SSE；`agent.py:model_call` 用 `get_stream_writer()` 把 `thinking` / `content` / `tool_status` / `chart` / `error` 分类推给前端。前端据此做了「折叠思考过程 + 逐字回答 + 图表/图片实时渲染」。

---

## 9. 怎么让模型输出「结构化数据」？输出解析怎么做？

**考察点**：要把 Agent 接进业务（存库、渲染大屏、喂给下游），结构化输出必问。

**参考答案**

- **手段**：
  1. **Prompt 约束**：在提示词里明确格式要求（如「只输出 JSON，字段为…」）。
  2. **JSON mode / 函数式结构化输出**：模型接口开启结构化输出，按给定 JSON Schema 生成（最可靠）。
  3. **工具调用当输出**：把「输出结构」定义成一个工具，让模型以「调用工具」的形式产出结构化结果。
  4. **解析层兜底**：`OutputParser` / `Pydantic` 校验 + 正则提取 + 失败重试。
- **关键认知**：模型输出天然不稳定，必须「约束 + 校验 + 重试」三件套，不能只靠 prompt。

**进阶追问**

- 模型输出非法 JSON 怎么办？→ 捕获解析异常，把错误信息回填让模型重试一次；再失败用正则兜底提取。
- 怎么输出符合前端大屏的格式？→ 先定好 schema，用结构化输出 + 校验。

**实战映射**：`DataAgent` 目前是「prompt 要求结构化中文结论」，属于第一层（prompt 约束）；task.md 里「输出符合前端大屏格式」标记为 🟡 部分完成——这正是可以在面试里讲的「我下一步会用 JSON Schema 结构化输出收紧这里」。

---

# Tier 2 · 高频

> 决定面试官是否认为你「真正做过完整项目」，而不仅仅是「调过 API」。共 8 题。

---

## 10. System Prompt / 提示词你是怎么设计和优化的？

**考察点**：Agent 的「指令层」，质量直接决定行为，也是最被低估的环节。

**参考答案**

设计一个好 System Prompt 的要素：

1. **角色设定**：一句话说明「你是谁、能干什么」。
2. **工具说明书**：每个工具叫什么、何时用、参数含义（模型靠它决策）。
3. **工作流程 SOP**：明确「先干嘛、再干嘛」的顺序（如「先查字典再写 SQL」）。
4. **边界与约束**：只读、不要全表扫描、枚举含义以字典为准、不要猜。
5. **专项指引**：针对高频场景给「怎么做」的详细思路（如瓶颈分析五步）。
6. **失败恢复规则**：「工具报错就阅读错误并重试」。

优化方法：**迭代**——跑真实用例 → 看哪里跑偏 → 补规则 → 回归。提示词是「调」出来的，不是一次写成的。

**进阶追问**

- prompt 里塞太多会不会有副作用？→ 会，太长稀释注意力、增加 token；要「结构化分节 + 只留高频规则」。
- 怎么防「提示词漂移」？→ 把关键规则做成测试用例，每次改 prompt 后回归。

**实战映射**：`DataAgent/agent.py` 的 `SYSTEM_PROMPT` 是教科书级示范——工具说明书（0~5 号工具逐一说明）、时间范围规则（含 SQL 半开区间写法）、瓶颈分析五步、产量分析六步、失败重试规则。面试时可以直接展示这一段，讲「我是怎么把业务口径固化进提示词的」。

---

## 11. 工具执行出错怎么办？Agent 怎么「自纠错」？

**考察点**：真实环境工具会挂（SQL 错、网络超时、返回空），考察鲁棒性设计。

**参考答案**

- **隐式纠错（推荐，最简单）**：工具执行失败时，把**错误信息**作为 `ToolMessage` 回填给模型，让模型读错误、自行修正重试（如「SQL 语法错误：column xxx not exist」→ 模型改 SQL 重查）。
- **显式纠错工具**：提供一个 `fix_xxx` / `validate_xxx` 工具让模型主动调用。
- **兜底**：重试 N 次仍失败 → 降级（返回部分结果/友好提示），并记日志。

**进阶追问**

- 怎么防止无限重试？→ 计数上限 + 相同调用去重检测。
- 错误信息要不要全量回传？→ 截断关键行即可，避免 token 爆炸。

**实战映射**：`agent.py:call_tools` 里 `except Exception as e: result = f"工具执行失败: {e}"`，把错误作为 `ToolMessage` 回填——这就是隐式纠错；`SYSTEM_PROMPT` 里也写了「若工具返回错误，仔细阅读并修正后重试」。

---

## 12. Embedding 和向量数据库是怎么工作的？相似度检索怎么选？

**考察点**：RAG 的技术底座，理解向量检索原理。

**参考答案**

- **Embedding**：把文本映射成高维稠密向量，语义相近的文本向量距离近。用 embedding 模型（如 OllamaEmbeddings / OpenAI text-embedding）生成。
- **向量库**：存储向量 + 原始文本，支持按相似度快速检索。常用：Chroma（轻量）、FAISS、Milvus、Pinecone、pgvector。
- **相似度度量**：余弦相似度（最常用，归一化后方向一致）、内积、欧氏距离。选哪个取决于 embedding 是否归一化。
- **Top-K 检索**：`as_retriever(search_kwargs={"k": 5})` 召回最相关的 5 个片段。

**进阶追问**

- 纯向量检索有什么问题？→ 对「精确关键词/编号」不敏感，需要混合检索（见下题）。
- 向量库怎么选？→ 小规模/本地用 Chroma/FAISS；生产级/分布式用 Milvus/pgvector。

**实战映射**：`RAG_Agent.py` 用 `OllamaEmbeddings + Chroma`（`chroma.sqlite3` 本地持久化），`as_retriever(k=5)`。这是轻量本地方案，适合单机。

---

## 13. RAG 召回不准怎么优化？混合检索和重排（rerank）是什么？

**考察点**：RAG 从「能跑」到「好用」的分水岭，进阶必问。

**参考答案**

问题根源：**纯向量检索**对语义近但精确词不同的情况强，但对「精确关键词、编号、专有名词」弱。

优化三板斧（从易到难）：

1. **调分块（chunking）**：调整 `chunk_size` / `chunk_overlap`；改按标题/语义切分（`MarkdownHeaderTextSplitter`），保证块语义完整。
2. **混合检索（Hybrid Search）**：`向量相似度 + BM25 关键词检索` 双路召回，再用 **RRF（倒数排名融合）** 合并两个排名——兼顾语义和精确词。
3. **重排（Rerank）**：先粗召回 20 条，再用 CrossEncoder 模型对「问题+片段」成对精打分，取前 5。

**评测闭环**：标注 QA 对，算 `recall@k / MRR / hit-rate`，每次调参看指标，不靠感觉。

**进阶追问**

- 怎么确定 chunk 大小？→ 看文档结构，语义块优先；常见 256~1000 token，重叠 10%~20%。
- RRF 原理一句话？→ 取每个榜单名次的倒数求和，避免不同榜单分数尺度不可比。

**实战映射**：`task.md` 第 18 项把混合检索/重排/语义切分列为待办，仓库现有只是纯 similarity 检索。面试时可以说「我调研了 BM25+RRF+CrossEncoder 的优化路径，准备落地」——展示你有完整的方法论，而不是只到「能跑」。

---

## 14. Multi-Agent 多智能体怎么设计？什么时候需要拆多个 Agent？

**考察点**：架构能力，判断你是否只会写单 Agent。

**参考答案**

- **什么时候拆**：任务天然分域（如「查数据库的分析 Agent」+「写代码的 Code Agent」+「检索文档的 RAG Agent」）；单 Agent 工具太多、prompt 太杂、上下文互相污染时。
- **组织模式**：
  1. **Supervisor（主管模式）**：一个主管 Agent 把任务分派给子 Agent，汇总结果。
  2. **Swarm / 手动流转**：开发者定义「谁交给谁」的规则。
  3. **Graph 编排**：用 LangGraph 把多个 Agent 作为节点，用条件边编排协作（最灵活）。
- **关键认知**：多 Agent ≠ 一定更好，会带来更高的 token 成本和更多的不确定性；**能用一个 Agent + 好工具解决的，别硬拆**。

**进阶映射**：本仓库 `Agents/` 下有 `Code_Agent.py`、`RAG_Agent.py`、`Memory_Agent.py`、`Drafter.py`，正是多 Agent 的雏形——各自独立，可被编排成协作图。

---

## 15. 你怎么评测（evaluate）一个 Agent？怎么知道它变好还是变坏？

**考察点**：工程成熟度，能区分「demo」和「产品」。

**参考答案**

- **为什么难**：Agent 输出是开放式文本，还带多步工具调用，没有单一「对错」。
- **分层评测**：
  1. **组件层**：工具单独测（SQL 查询对不对、RAG 召回 recall@k、图表能否生成）。
  2. **流程层**：Agent 是否调对了工具、调用顺序对不对（trajectory 评测）。
  3. **结果层**：最终答案对不对——人工标注集 + LLM-as-judge（用强模型打分）+ 关键指标（准确率、工具调用成功率）。
- **回归**：把真实/合成的用例集固化，每次改 prompt / 工具后跑一遍，看有没有退化。

**进阶追问**

- LLM-as-judge 靠谱吗？→ 需要设计好的评分 rubric + 抽样人工校准；不能全信。
- 轨迹评测怎么落地？→ 记录每次 run 的 tool_calls 序列，比对「黄金轨迹」。

**实战映射**：`task.md` 第 17 项「召回率调试」写的就是这个方法论（标注 20~50 条 QA、算 recall@k/MRR/hit-rate、迭代达标）。面试时可强调「我知道评测不能省，这是项目里最容易被人砍、但砍了上线没底气的一步」。

---

## 16. 怎么控制 Agent 的成本和延迟？

**考察点**：生产环境必须回答「跑一次多少钱、多慢」。

**参考答案**

成本三来源：token 用量、模型单价、调用次数。

1. **降 token**：上下文裁剪/摘要（第 6 题）、工具结果截断、精简 prompt。
2. **降单价**：分级模型路由——简单任务用小模型，难任务才上大模型；本地部署开源模型（如本项目 Ollama + qwen）。
3. **降次数**：减少无谓的工具往返；Prompt caching 命中稳定前缀。
4. **降延迟**：流式输出（第 8 题）让用户「感知快」；并行工具调用；预热模型连接。

**进阶追问**

- 模型路由怎么设计？→ 先小模型答，置信度低再升大模型（cascade）。
- caching 什么能命中？→ 稳定的 system prompt + 工具定义放最前面。

**实战映射**：本项目用本地 `Ollama` + `qwen3.6:27b`，本身就是「降单价」——自部署开源模型、无 API 按 token 计费；`TEMPERATURE=0.2` 也偏向稳定输出、减少重试成本。

---

## 17. Checkpointer（检查点）是干什么的？为什么要持久化？

**考察点**：LangGraph 特有的记忆/恢复机制，结合你项目问。

**参考答案**

- **Checkpointer**：把图的**每次状态更新**保存到存储（内存 / SQLite / Redis），让图能「暂停-恢复」。
- **用途**：
  1. **跨会话记忆**：按 `thread_id` 存每个会话的完整状态，下次接着聊。
  2. **时间旅行（time travel）**：回到历史某个检查点重放/分叉。
  3. **容错**：中途失败能从最近检查点恢复，不用从头跑。
- **类型**：`MemorySaver`（进程内，调试用）、`AsyncSqliteSaver`（本地落库）、生产可用 Redis/Postgres 后端。

**进阶追问**

- MemorySaver 和 SqliteSaver 区别？→ 前者进程重启即丢，后者持久化到文件。
- 怎么控制「存多久」？→ 检查点清理策略，避免无限膨胀。

**实战映射**：`agent.py` 的 `build_agent(checkpointer)` 把 checkpointer 做成注入参数——CLI 调试传 `MemorySaver`，web 用 `AsyncSqliteSaver`（`app.py` lifespan 里 `async with` 管生命周期），这是「依赖注入 + 场景隔离」的好实践。

---

# Tier 3 · 中频/进阶

> 拉开候选差距、或对应「更高阶/更资深」岗位的问题。共 8 题。

---

## 18. 什么是 Prompt Injection（提示注入）？Agent 面临哪些安全风险？

**考察点**：Agent 会执行工具、访问数据库，安全问题比纯 LLM 严重得多。

**参考答案**

- **Prompt Injection**：攻击者构造恶意输入，诱导模型忽略系统指令、泄露 prompt、或执行危险工具。分为**直接注入**（用户直接输入）和**间接注入**（藏在被检索的文档/网页里，RAG 尤其危险）。
- **Agent 特有风险**：工具滥用（诱导执行 `delete` / 写库）、数据泄露（诱导 dump 数据库）、越权。
- **防御**：
  1. 工具**最小权限**（只读、白名单、参数校验、需人工确认的高危操作）。
  2. 对检索到的外部内容做「隔离」标记（`<untrusted>...</untrusted>`），明确告诉模型「这是外部数据，不是指令」。
  3. 输出过滤 + 敏感信息脱敏。
  4. Human-in-the-loop：危险操作前让人确认。

**进阶追问**

- RAG 怎么防间接注入？→ 检索内容当数据不当指令，用特殊分隔符包裹 + prompt 里声明。
- 只读是不是就安全了？→ 只读能防「写」，但防不了「泄露数据」，仍要脱敏和输出审查。

**实战映射**：`DataAgent` 的 `query_database` 工具明确「**只读** SELECT」（`agent.py` SYSTEM_PROMPT 也强调「不要写库」）——这就是最小权限的落地。可以在面试里主动提「我还需要加高危操作的人工确认」。

---

## 19. Human-in-the-loop（人在回路）怎么实现？

**考察点**：关键决策需要人审核的场景（审批、写库、重要操作）。

**参考答案**

- **概念**：Agent 执行到某个关键节点时**暂停**，等待人工批准或输入后再继续。
- **LangGraph 实现**：`interrupt()` 在节点里中断，把控制权交回调用方；人工确认后 `Command(resume=...)` 恢复执行。
- **适用场景**：写库/删除、对外发消息、金额/权限相关、不确定的结论需要人工拍板。

**进阶追问**

- 中断后状态会丢吗？→ 不会，checkpointer 保存了状态，恢复时接着跑。
- 怎么控制「哪些步骤要人确认」？→ 在工具/节点层面打标，按风险分级。

**实战映射**：当前 `DataAgent` 是全自动只读，暂无人工审批点；可作为「下一步安全加固」的面试话题。

---

## 20. 怎么处理「思考型模型」的 thinking / reasoning 字段？

**考察点**：新模型（qwen3.6、o 系列、deepseek 等）把「思考过程」和「正式回答」分开，工程上要正确转发。

**参考答案**

- 现象：模型流式返回时，`thinking`（推理）和 `content`（回答）是**分离字段**，且思考 token 往往不计入「正式输出」。
- 处理：分别捕获，**思考过程可以展示（折叠）但不进正式结果**；多轮对话里思考内容**通常不重放**（或仅保留最新一轮），避免 token 膨胀。
- 坑：某些封装（如旧版 `ChatOllama`）会**丢弃 thinking 字段**，导致「推理过程丢失、模型行为异常」，需用原生客户端或最新版。

**进阶映射**：`DataAgent` 专门为此用了**原生 `ollama.AsyncClient`** 而非 langchain 的 `ChatOllama`——注释里写得很清楚：「langchain 的 ChatOllama 会丢弃 thinking 字段，所以用原生客户端」。`model_call` 里把 `thinking` 和 `content` 分别 `writer()` 推给前端，前端折叠展示思考过程。这是很能加分的实战细节。

---

## 21. 怎么把 Agent 部署上线？容器化 / 交付要注意什么？

**考察点**：从「能跑」到「能交付」的工程能力。

**参考答案**

- **交付物**：FastAPI/Flask 服务 + 前端 + 模型服务 + 数据库 + 向量库。
- **容器化**：Dockerfile 打包；注意**中文字体**（图表渲染中文会变方块，需装 Noto CJK）、依赖锁定、模型服务（Ollama）单独镜像或外置。
- **编排**：docker-compose 组织 `db / agent / web / ollama` 多服务。
- **环境可复现**：纯净环境一键部署脚本 + 验收测试（服务启动 / 接口 / SSE / 前端冒烟）。
- **数据迁移**：跨环境导入导出 + 字段映射 + 枚举清洗（task.md 第 37 项点出的最大雷）。

**进阶追问**

- 模型服务怎么部署？→ 本地 Ollama 独立服务、或云 API；注意 GPU 资源。
- 版本怎么管？→ 模型版本、prompt 版本、代码版本都要可回溯。

**实战映射**：`task.md` 第 36~40 项（Docker 交付、数据迁移、一键部署、验收）目前仓库未实现，但方案已在文档里写清（Dockerfile + Noto 中文字体 + docker-compose + deploy.sh）。面试时可讲「交付链路的方案我已设计，含中文字体、数据迁移清洗这些细节」。

---

## 22. Agent 和 Workflow（工作流）到底怎么选？各自适用场景？

**考察点**：架构判断力，避免「什么都上 Agent」的过度设计。

**参考答案**

| 维度 | Workflow | Agent |
|---|---|---|
| 控制流 | 开发者写死（确定 DAG） | 模型运行时决定（不确定、可循环） |
| 可预测性 | 高 | 低（可能跑偏/发散） |
| 成本 | 低 | 高（多轮 token） |
| 适用 | 流程固定、可控性要求高、批量处理 | 开放式任务、需动态决策、多工具协作 |

- **实践建议**：能用确定流程解决的用 Workflow；只有「路径不确定、需要模型自主判断」的部分才用 Agent。LangGraph 两者都支持——甚至可以用图把 Workflow 节点和 Agent 节点混合编排。

**进阶追问**

- 混合编排例子？→ 先 Workflow 节点做数据清洗（确定），再 Agent 节点做分析（不确定），再 Workflow 节点格式化输出。

---

## 23. 你踩过哪些 Agent 开发的坑？怎么解决的？

**考察点**：开放题，考察真实经验深度，答得好非常加分。

**参考答案（可挑 3~4 个结合自己项目讲）**

1. **模型不按预期调工具** → 工具 docstring 写不清 / prompt 没讲清「何时用」→ 补工具说明书 + 专项指引 + 用例回归。
2. **Ollama tool_call 没有 id** → LangChain `ToolMessage` 需要 `tool_call_id` 对齐 → 用 `uuid` 补 id（`agent.py:208`）。
3. **ChatOllama 丢 thinking 字段** → 换原生 `AsyncClient`（`agent.py:45`）。
4. **工具返回太大撑爆上下文** → 前端只显示截断，回填控制长度。
5. **「当日无数据」模型硬编结果** → prompt 里加「如实告知 + 用最新可用日期」规则。
6. **多用户会话串线** → `session_id` / `thread_id` 隔离 + checkpointer。
7. **模型陷入重试死循环** → 迭代上限 + 相同调用去重。

**讲法**：每个坑用「现象 → 根因 → 解法」三句话讲，比罗列更打动人。

**实战映射**：以上 1~6 基本都能在本仓库 `DataAgent` 代码里找到对应注释和实现，是现成的「项目故事」。

---

## 24. 如果让你从头设计一个「数据分析 Agent」，你的架构是怎样的？

**考察点**：综合设计题（可能是压轴），考察全链路能力。

**参考答案（给出一个可套用的框架）**

```
前端(聊天/大屏) 
   ↕ SSE 流式
FastAPI 接口层（POST /chat, session 管理）
   ↕
LangGraph Agent（ReAct 循环）
   ├─ System Prompt（角色 + 工具说明书 + 业务 SOP + 边界）
   ├─ 工具集：
   │    ├─ query_database（只读 SQL，带 schema 探索）
   │    ├─ 业务字典查询（枚举/口径）
   │    ├─ 可视化（图表生成）
   │    ├─ 代码执行（复杂分析，沙箱）
   │    ├─ RAG 检索（知识库）
   │    └─ 算法工具（瓶颈/根因/预测/偏移检测）
   ├─ 记忆：checkpointer（AsyncSqliteSaver + thread_id 隔离）
   └─ 容错：工具错误回填 + 隐式重试 + 迭代上限
   ↕
基础设施：PostgreSQL（数据）+ Chroma（向量）+ Ollama（模型）
   ↕
交付：Docker 容器化 + 一键部署 + 验收测试
```

**关键要讲出的设计取舍**：
- 为什么用 ReAct 而不是写死流程？→ 用户问题开放，需模型自主探索 schema、决定查询。
- 为什么工具要「只读」？→ 安全最小权限。
- 为什么记忆用 checkpointer？→ 多轮追问 + 会话隔离。
- 为什么模型选本地 Ollama？→ 降成本 + 数据不出内网。

**实战映射**：这整套就是 `DataAgent` 的架构，你已经在真实项目里实现了主线（数据提取 → 工具化 → ReAct → 流式前端 → 记忆）。直接画这张图讲，最有说服力。

---

## 25. LangGraph 现在有哪些新的 API / 玩法？StateGraph 和 Functional API 区别？

**考察点**：是否跟进框架演进（面试官可能用新 API 考你）。

**参考答案**

- **StateGraph（图式 API）**：显式声明节点/边，适合可视化、复杂控制流，是当前主流（你的项目用的这个）。
- **Functional API（函数式）**：用 `@entrypoint` / `@task` 装饰器把普通函数组合成图，写起来更像普通 Python，控制流靠函数调用表达，简洁但可视化弱一些。
- **预构建 Agent**：`create_react_agent` / `create_agent` 一行起一个带工具、记忆、循环的 Agent，适合快速原型。
- **中间件（middleware）**：拦截/改写模型输入输出，统一加日志、重试、脱敏。

**答题策略**：先讲清「我用 StateGraph 是因为要精细控制循环和流式自定义事件」，再表示「了解 Functional API 和 create_agent，原型阶段可以快速起步」——既体现深度又体现跟进。

---

# 一页纸速记 + 万能答题框架

## 万能答题框架（任何 Agent 题都能套）

1. **是什么**：一句话定义（Agent = LLM + 工具 + 记忆 + 循环）。
2. **为什么**：它解决什么问题（知识/幻觉/自动化/体验）。
3. **怎么做**：分层讲（模型层 → 编排层 → 工具层 → 记忆层 → 交付层）。
4. **踩过的坑 / 取舍**：讲 1 个真实 trade-off（这是加分项）。
5. **结合我的项目**：一句话落到 `DataAgent` 怎么实现的。

## 高频关键词速记

| 主题 | 关键词 |
|---|---|
| 核心 | ReAct 循环、Thought-Action-Observation、Function Calling、JSON Schema |
| 框架 | StateGraph、State/Reducer、checkpointer、conditional_edges、Functional API |
| 记忆 | add_messages、thread_id、MemorySaver / AsyncSqliteSaver、摘要压缩 |
| RAG | chunking、embedding、Chroma、Top-K、BM25 + 向量混合检索、RRF、rerank |
| 输出 | 结构化输出、OutputParser、Pydantic、流式 SSE、get_stream_writer |
| 工程 | 只读最小权限、隐式纠错、评测(recall@k/LLM-judge)、Docker、成本 |

---

# 面试前 30 分钟自测清单

- [ ] 能画出来 Agent 执行循环（model → 判断 → tools → 回填 → model）
- [ ] 能讲清 Function Calling 的「意图」和「执行」是分离的
- [ ] 能背出 RAG 的离线建库 + 在线查询两条链路
- [ ] 能说清短时记忆和长时记忆的实现层次
- [ ] 能讲 StateGraph 的 State 和 reducer 是什么
- [ ] 能解释 SSE 和 WebSocket 的选择依据
- [ ] 能说出至少 3 个自己踩过的坑 + 解法
- [ ] 能一句话讲清「什么时候用 Agent、什么时候用 Workflow」
- [ ] 能把上面任一题映射到 `DataAgent` 的具体代码

---

> 附：本仓库关键代码对照表（面试时指路用）
>
> | 主题 | 文件 |
> |---|---|
> | ReAct 循环 + 工具分发 | [DataAgent/agent.py](DataAgent/agent.py) |
> | System Prompt 设计 | [DataAgent/agent.py](DataAgent/agent.py) `SYSTEM_PROMPT` |
> | 消息转换（tool_calls/thinking） | [DataAgent/agent.py](DataAgent/agent.py) `convert_to_ollama` |
> | 记忆（AsyncSqliteSaver） | [DataAgent/app.py](DataAgent/app.py) |
> | RAG 完整链路 | [Agents/RAG_Agent.py](Agents/RAG_Agent.py) |
> | 教学版 ReAct | [Agents/ReAct.py](Agents/ReAct.py) |
> | 最简记忆 | [Agents/Memory_Agent.py](Agents/Memory_Agent.py) |
> | 工具集 | [DataAgent/tools/](DataAgent/tools/) |
