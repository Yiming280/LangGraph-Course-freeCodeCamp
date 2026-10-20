# Data Agent 后端接口

FastAPI 服务，默认监听 `http://localhost:8001`（环境变量 `DATA_AGENT_PORT` 可覆盖）。

## 启动与代理

```bash
cd DataAgent
python app.py
```

本地开发把以下路径代理到 `8001`：

```
/chat  /stop  /models  /sessions  /memory  /static
```

`/chat` 是 SSE 长连接，代理需保持流式（不缓冲）。

## 1. 聊天

### GET /models

返回可用 LLM 后端，`id` 即发消息时的 `provider` 字段。

```json
{
  "default": "ollama",
  "models": [
    { "id": "ollama", "label": "qwen3.8:27b", "hint": "Ollama 本地推理，无需 Key", "needs_key": false },
    { "id": "openai",  "label": "qwen3.5:9b",  "hint": "代理服务，需 API Key",       "needs_key": true  }
  ]
}
```

### POST /chat（SSE 流式）

请求体：

```json
{
  "message": "分析 t_device_statistical_metrics，画个柱状图",
  "user_id": "alice",           // 可选，多用户隔离
  "session_id": "s-xxxxxxxx-xxxxxxxx",
  "provider": "ollama",         // 可选，省略用服务端 .env 默认
  "api_key": ""                 // 可选，仅 openai 后端需要
}
```

响应 `Content-Type: text/event-stream`，每条事件 `data: {JSON}\n\n`：

| type | payload | 含义 |
|---|---|---|
| `thinking` | `{"type":"thinking","content":"<token>"}` | 思考过程（逐 token） |
| `content` | `{"type":"content","content":"<token>"}` | 最终回答（Markdown，逐 token） |
| `tool_call` | `{"type":"tool_call","name":"...","args":{...}}` | 一次工具调用，args 里可能含 sql/code |
| `tool_result` | `{"type":"tool_result","name":"...","content":"...","error":false}` | 工具执行结果 |
| `chart` | `{"type":"chart","content":"/static/charts/xxx.png"}` | 图表图片 URL |
| `error` | `{"type":"error","content":"..."}` | 错误 |
| `done` | `{"type":"done"}` | 流结束 |

### POST /stop

请求体 `{"session_id":"s-xxx","user_id":"alice"}`，返回 `{"ok":true,"stopped":"s-xxx"}`。用于中断正在进行的流式生成。

## 2. 会话

会话记忆按 `thread_id = user_id:session_id` 隔离。`user_id` 可选：传了则不同用户互不可见；不传则不区分用户。

### GET /sessions?user_id=alice

会话列表，按更新时间倒序：

```json
{
  "sessions": [
    {
      "id": "s-xxx",
      "title": "帮我看看233数据库里有什么可用的数据",
      "preview": "帮你梳理了可用的表…",
      "updated_at": "2026-10-12T07:19:49.754075+00:00",
      "message_count": 11
    }
  ]
}
```

字段：`id` 即 session_id；`title` 取第一条用户消息；`preview` 取最后一条用户/助手消息；`updated_at` 为 ISO 时间（UTC）；`message_count` 为消息条数。

### GET /sessions/{session_id}/messages?user_id=alice&before=..&limit=..

分页读取某会话消息（由新到旧）。`before` 为游标（省略取最新一段），`limit` 为每页条数。返回：

```json
{
  "ok": true,
  "session_id": "s-xxx",
  "messages": [
    { "role": "human", "content": "..." },
    { "role": "ai",    "content": "...", "tool_calls": [{ "id": "call_xxx", "name": "query_database", "args": {} }] },
    { "role": "tool",  "content": "...", "tool_call_id": "call_xxx", "name": "query_database" }
  ],
  "before": 10,
  "has_more": true,
  "total": 30
}
```

`ai` 的 `content` 为最终回答（不含思考过程）；会话不存在返回 `{"ok":false,"error":"会话不存在"}`。

### DELETE /sessions/{session_id}?user_id=alice

删除会话。成功 `{"ok":true,"deleted":"s-xxx"}`，不存在 `{"ok":false,"error":"会话不存在"}`。

### 续聊

没有「新建会话」接口：`session_id` 由调用方生成（如 `s-` + 随机串），续聊传同一个 `session_id`。

## 3. 记忆

### GET /memory/export

下载全部会话打包成的 JSON（`dataagent-memory.json`）。

### POST /memory/import

请求体直接是导出的那份 JSON。返回 `{"ok":true,"imported_sessions":27}`；失败返回 `{"ok":false,"error":"..."}`。

## 4. 其它接口

| 接口 | 说明 |
|---|---|
| `GET/POST/DELETE /databases`、`POST /databases/test` | PostgreSQL 连接管理 |
| `GET /settings`、`POST/DELETE /settings/iotdb`、`POST/DELETE /settings/logs` | 连接配置 / IoTDB / 日志来源 |
| `GET/POST/DELETE /sop` | 维修 SOP 条目管理 |
| `POST /export-report` | 导出会话为 Markdown / PDF |
| `GET /context` | 会话上下文占用（消息数 / 字符数） |
| `GET /errors/stats`、`GET /errors/export`、`DELETE /errors` | 错误统计 |
