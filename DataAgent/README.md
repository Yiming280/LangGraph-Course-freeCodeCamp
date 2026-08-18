# Data Agent — 流式 Web 数据分析 Skeleton

一个可扩展的 AI 数据分析 Agent：从 PostgreSQL 提取数据、生成统计图（histogram / bar / pie）、
通过 SSE 流式输出，并用 SQLite 持久化记忆。**所有工具集中在 `tools/` 文件夹，agent 脚本只声明、不定义。**

## 功能

- 🖥️ **Web 界面 + SSE 流式**：思考过程、正式回答逐 token 输出，图表图片直接内嵌
- 🗄️ **数据库提取**：只读 SELECT 查询 `agile_dispatch` 库，先探索 schema 再取数
- 📊 **统计图**：柱状图 / 饼图 / 直方图，中文渲染（Noto Sans CJK）
- 🧠 **持久化记忆**：SQLite checkpointer，跨轮 + 重启不丢，会话按 session_id 隔离
- 🧩 **工具自动注册**：往 `tools/` 丢一个工具文件即自动可用，无需改 agent 脚本

## 目录结构

```
DataAgent/
├── app.py          # FastAPI + SSE 服务（web 入口）
├── agent.py        # LangGraph 图定义（节点 + 路由 + CLI 调试入口）
├── tools/          # ★ 所有工具都在这里 —— 自动发现注册
│   ├── __init__.py # 自动扫描收集 ALL_TOOLS / TOOL_MAP
│   ├── database.py    # query_database（只读 SELECT）
│   ├── charts.py      # create_histogram / create_bar_chart / create_pie_chart
│   └── code_runner.py # python_executor（沙箱执行 Python）
├── static/
│   ├── index.html     # SSE 前端
│   └── charts/        # 生成的图表 PNG（运行时产物）
├── requirements.txt
└── .env.example    # OLLAMA_HOST / DB_* 配置模板
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

> 注：服务默认端口 8000，若与仓库里其他 agent 的 server.py 冲突，改 `app.py` 末尾的 `port=8000`。

## 架构说明

### 流式管道

```
浏览器 → POST /chat (session_id) → LangGraph astream
  ├─ custom 流：model 节点 writer 逐 token 推 {type: thinking/content/tool_status/chart}
  └─ SSE:  data: {...}
```

事件类型：

| type | 含义 |
|---|---|
| `thinking` | 思考过程（逐 token） |
| `content` | 正式回答（逐 token） |
| `tool_status` | 工具调用进度 |
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

在 `agent.py` 顶部的常量里改：`OLLAMA_HOST` / `MODEL` / `TEMPERATURE`。

## 调试

```bash
# 终端里直接跑一个流式 CLI（内存记忆，不写库）
../.venv/bin/python agent.py

# 直连 SSE 看原始事件
curl -N -X POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"数据库里有哪些表？","session_id":"s1"}'
```
