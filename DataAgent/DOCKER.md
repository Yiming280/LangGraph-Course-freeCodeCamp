# Data Agent Docker 部署与交付

## 镜像里是什么

`dataagent:latest` 是一个「打包好的快照」：精简 Linux + Python 3.12 + 全部依赖 + 一份 `DataAgent/` 源码拷贝。启动时先跑 `docker-entrypoint.sh`（把 `agent_memory.sqlite` 等状态文件软链到挂载的 `/data` 做持久化），再执行 `python app.py` 监听 8001。

**注意**：镜像里的代码是 `docker build` 那一刻的**拷贝**。改源码后要重新 build，容器才会用新代码。

## 开发者自己用

### 构建

```bash
cd DataAgent
docker build -t dataagent:latest .
```

### 启动（等价于 docker-compose，因为本机可能没装 compose 插件）

```bash
docker run -d --name dataagent \
  --env-file .env \
  -p 8001:8001 \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/data/charts:/app/static/charts" \
  -v "$(pwd)/data/reports:/app/static/reports" \
  -v "$(pwd)/domain:/app/domain" \
  -v "$(pwd)/../Agents:/Agents" \
  --restart unless-stopped \
  dataagent:latest
```

> `-v "$(pwd)/../Agents:/Agents"` 是 RAG 美股知识库（可选）；不挂也能启动，只是调用该工具会报错。

### 开发期热重载（改代码立刻生效，不用重 build）

```bash
docker run -d --name dataagent-dev \
  --env-file .env \
  -p 8001:8001 \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/data/charts:/app/static/charts" \
  -v "$(pwd)/data/reports:/app/static/reports" \
  -v "$(pwd):/app" \
  dataagent:latest \
  python -m uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

最后那个 `-v "$(pwd):/app"` 用你的真实源码覆盖镜像里的拷贝，`--reload` 让改 `app.py` 后自动重启。

### 改完交付前

```bash
docker build -t dataagent:latest .   # 重新打包最新代码
```

## 交付给前端同事（一键能跑）

同事只需要 **3 样东西**：镜像 tarball + `run.sh` + 一份 `.env`。

### 1. 打包镜像（开发者机器上执行）

```bash
cd DataAgent
docker save dataagent:latest | gzip > dataagent.tar.gz
```

把 `dataagent.tar.gz`、`run.sh`、`.env`（或让同事按 `.env.example` 自己填）一起发给他。

### 2. 同事机器上执行

```bash
docker load -i dataagent.tar.gz   # 导入镜像，不需要配加速器、不需要 build
chmod +x run.sh
./run.sh                          # 一键启动，监听 8001
```

`run.sh` 会自动：
- 检查镜像和 `.env` 是否存在，缺失时给出中文提示
- 移除旧容器、用 `.env` 配置启动、挂载持久化目录
- 打印启动地址和日志命令

### 3. 前端接入

同事的 Vue 前端把下面路径代理到 `http://localhost:8001` 即可，**不需要访问旧网页 `/`**：

```
/chat  /stop  /models  /sessions  /memory  /static
```

接口协议见 [API.md](API.md)。

## 配置（全部走 .env，镜像内不写死）

`run.sh` / `docker run` 都从 `.env` 读配置。数据库、IoTDB、日志路径、LLM 模型/地址/Key 全部可外部改，改完重启容器生效，无需重 build。变量清单见 `.env.example`，含：

- LLM：`LLM_PROVIDER` `OLLAMA_HOST` `OLLAMA_MODEL` `OPENAI_BASE_URL` `OPENAI_API_KEY` `OPENAI_MODEL` …
- 数据库：`DATABASES` `DEFAULT_DATABASE` `DB_<别名>_HOST/PORT/DBNAME/USER/PASSWORD`
- IoTDB：`IOTDB_HOST/PORT/USER/PASSWORD`
- 日志：`LOG_DIRS`

## 故障排查

**拉不到基础镜像 / 构建超时**：国内直连 Docker Hub 不通，需配镜像加速器。编辑 `/etc/docker/daemon.json` 后重启 docker：

```json
{ "registry-mirrors": ["https://docker.m.daocloud.io"] }
```

```bash
sudo systemctl restart docker
```

DaoCloud 偶发 CDN 超时（重试即可）；更稳的做法是问运维要**内网 registry 镜像源**。备选公共加速器：`docker.1panel.live`、`dockerproxy.net`、`docker.1ms.run`。

**同事用 tarball 交付时不需要加速器**（`docker load` 是本地导入，不联网）。

**没有 `docker compose`**：装 `docker-compose-plugin`，或直接用本文件里的 `docker run` / `run.sh`（不依赖 compose）。
