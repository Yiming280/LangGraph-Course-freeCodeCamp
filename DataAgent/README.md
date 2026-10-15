# Data Agent — 流式 Web 数据分析 Skeleton

一个可扩展的 AI 数据分析 Agent：从 PostgreSQL / Apache IoTDB 提取数据、读本机日志、
生成统计图（histogram / bar / pie / line），通过 SSE 流式输出，SQLite 持久化记忆，
并支持把分析结果导出成 Markdown / PDF 报告。**所有工具集中在 `tools/` 文件夹，agent 脚本只声明、不定义。**

## 功能

- 🖥️ **Web 界面 + SSE 流式**：思考过程、正式回答逐 token 输出，图表图片直接内嵌
- 🗄️ **多数据源**：只读 PostgreSQL（多库按别名）、Apache IoTDB 时序库、本机日志（白名单目录）
- 📊 **统计图**：柱状图 / 饼图 / 直方图 / 折线图，中文渲染（Noto Sans CJK）
- 📄 **报告导出**：把对话总结 + 图表导出成 Markdown / PDF（LLM 工具与后端按钮两个入口）
- 🕘 **历史会话**：侧栏列出 / 分页回看 / 删除历史会话，附上下文占用提示
- 📉 **错误统计**：结构化错误分类（Error Taxonomy）+ 前端占比看板
- 🧠 **持久化记忆**：SQLite checkpointer，跨轮 + 重启不丢，会话按 session_id 隔离；支持记忆导出/导入
- 🧩 **工具自动注册**：往 `tools/` 丢一个工具文件即自动可用，无需改 agent 脚本

## 目录结构

```
DataAgent/
├── app.py          # FastAPI + SSE 服务（web 入口 + settings/memory/report/errors 端点）
├── agent.py        # LangGraph 图定义（节点 + 路由 + CLI 调试入口）
├── memory.py       # 记忆导出/导入 + 会话列表/分页/删除
├── error_taxonomy.py # 错误分类法（结构化错误记录 + 聚合统计）
├── tools/          # ★ 所有工具都在这里 —— 自动发现注册
│   ├── __init__.py    # 自动扫描收集 ALL_TOOLS / TOOL_MAP
│   ├── _settings.py   # 连接配置（数据库/IoTDB/日志，settings.json 落盘）
│   ├── database.py    # query_database / list_databases / test_connection（只读）
│   ├── describe_database.py # 一次返回某库全部表/列 schema 概览
│   ├── charts.py      # create_histogram / bar / pie / line
│   ├── iotdb.py       # Apache IoTDB 只读查询
│   ├── log_fetch.py   # 本机日志读取/检索
│   ├── supply_chain.py / gantt.py # 供料拓扑 / 甘特图
│   ├── export_report.py # 报告导出（Markdown / PDF）
│   └── code_runner.py # python_executor（沙箱执行 Python）
├── static/
│   ├── index.html     # SSE 前端（历史侧栏 / 连接配置 / 报告 / 错误统计）
│   ├── charts/        # 生成的图表 PNG（运行时产物）
│   └── reports/       # 导出的报告（运行时产物）
├── requirements.txt
└── .env.example    # LLM_PROVIDER / OLLAMA_* / OPENAI_* / DATABASES / IOTDB_* / LOG_DIR*
```

## 快速开始

```bash
cd DataAgent

# 1. 配置环境变量（复制模板并填写 DB_*）
cp .env.example .env

# 2. 安装依赖（用项目 venv）
uv pip install --python ../.venv/bin/python -r requirements.txt

# 3. 启动（agent_memory.sqlite 会自动创建）
../.venv/bin/python app.py
# 浏览器打开 http://localhost:8000
```
如果出现 `address already in use`的错误，查出占用的进程：
```bash
sudo lsof -i :8001
# 然后kill
sudo kill [PID]
```


试试这些提问：

- 「看一下数据库里有哪些表，简单介绍几个核心业务表」
- 「分析 t_device_statistical_metrics 表，画个柱状图看各字段概况」
- 「t_vehicles 表里按类型统计数量，画个饼图」
- 「画出 t_vehicle_steps 表里里程的分布直方图」
- 「画出最近 7 天每日产量的折线图」
- 「把上面的分析总结导出成 PDF 报告」

> 注：服务默认端口 8000，若与仓库里其他 agent 的 server.py 冲突，改 `app.py` 末尾的 `port=8000`。

## 架构说明

### 流式管道

```
浏览器 → POST /chat (session_id) → LangGraph astream
  ├─ custom 流：model 节点 writer 逐 token 推 {type: thinking/content/tool_call/tool_result/chart}
  └─ SSE:  data: {...}
```

事件类型：

| type | 含义 |
|---|---|
| `thinking` | 思考过程（逐 token） |
| `content` | 最终回答（逐 token，Markdown） |
| `tool_call` | 工具调用（含 `name` 与 `args` 入参，如 SQL/code） |
| `tool_result` | 执行结果（含 `name`/`content`/`error`） |
| `chart` | 生成的图表图片 URL |
| `done` / `error` | 流结束 / 错误 |

### Memory

`thread_id` = 前端的 `session_id`（localStorage 持久化）。checkpointer 用
`AsyncSqliteSaver` 写到 `agent_memory.sqlite`，重启服务记忆仍在。
新建会话点「＋ 新会话」即可清空该会话的记忆（新 session_id）。

## 扩展指南（Skeleton 的核心）

工具自动注册基于 `tools/__init__.py` 的 `pkgutil` 扫描：**新工具只需新建文件，agent 无需任何改动。**

### 添加一个数据分析工具

```python
# tools/my_stats.py
from langchain_core.tools import tool

@tool
def compute_correlation(x: str, y: str) -> str:
    """计算两组数值的皮尔逊相关系数。

    Args:
        x: 数值数组的 JSON 字符串，如 '[1,2,3]'
        y: 数值数组的 JSON 字符串
    """
    import json
    a = json.loads(x); b = json.loads(y)
    n = len(a)
    mx, my = sum(a)/n, sum(b)/n
    cov = sum((ai-mx)*(bi-my) for ai, bi in zip(a, b)) / n
    sx = (sum((ai-mx)**2 for ai in a)/n) ** 0.5
    sy = (sum((bi-my)**2 for bi in b)/n) ** 0.5
    return f"r = {cov/(sx*sy):.4f}"
```

保存后重启服务，`from tools import ALL_TOOLS` 会自动把 `compute_correlation` 加进来。
同时建议把新工具的描述补进 `agent.py` 的 `SYSTEM_PROMPT`，让模型知道它的存在和用法。

### 添加 RAG 工具

同样在 `tools/` 新建（如 `tools/rag.py`）：

1. 写一个 `@tool`（如 `search_docs(query)`），内部用 Chroma / 向量库检索文档并返回相关片段
2. 如果检索需要 embedding 模型，在工具模块里初始化并缓存
3. 更新 `SYSTEM_PROMPT`，告诉模型何时调用 RAG（例如「回答与知识库相关的问题前先 search_docs」）

> 工具模块 import 出错只会打印告警并跳过，不会拖垮服务——可以放心迭代。

### 修改模型 / Ollama 地址

在 `.env` 里改（复制 `.env.example` 后填写）：
- `LLM_PROVIDER` 切换 `ollama` / `openai` 后端；
- Ollama：`OLLAMA_HOST` / `OLLAMA_MODEL` / `OLLAMA_TIMEOUT`（27b 冷启动首 token 较慢，默认 300s）；
- OpenAI 兼容代理：`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL`；
- `TEMPERATURE` / `MAX_OUTPUT_TOKENS` 也可覆盖。网页端每次请求还能用 provider/api_key 临时切换后端。

## 调试

```bash
# 终端里直接跑一个流式 CLI（内存记忆，不写库）
../.venv/bin/python agent.py

# 直连 SSE 看原始事件
curl -N -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"数据库里有哪些表？","session_id":"s1"}'
```
