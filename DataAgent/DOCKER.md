# Docker 部署

## 镜像里是什么

`dataagent:latest` = Debian + Python 3.12 + 全部依赖 + `DataAgent/` 源码的拷贝。启动时先跑 `docker-entrypoint.sh`（把 `agent_memory.sqlite` 等状态文件软链到 `/data` 持久化），再执行 `python app.py`，监听 8001。

镜像里的代码是 build 那一刻的快照，改了源码要重新 build 才会生效。

## 构建

```bash
docker build -t dataagent:latest .
```

## 启动

```bash
docker run -d --name dataagent \
  --env-file .env \
  -p 8001:8001 \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/data/charts:/app/static/charts" \
  -v "$(pwd)/data/reports:/app/static/reports" \
  --restart unless-stopped \
  dataagent:latest
```

- 要读本机日志：挂日志目录 `-v /path/logs:/logs`，并把 `LOG_DIRS` 指到 `/logs`。
- 要 RAG 美股知识库：挂 `-v "$(pwd)/../Agents:/Agents"`。

## 开发热重载

改代码不想每次重 build，把源码目录挂进容器加 `--reload`：

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

最后那个 `-v "$(pwd):/app"` 用真实源码盖掉镜像里的拷贝，`--reload` 改 `app.py` 自动重启。

## 交付

需要 3 样东西：镜像 tarball、`run.sh`、`.env`。

打包镜像：

```bash
docker save dataagent:latest | gzip > dataagent.tar.gz
```

拿到 tarball + `run.sh` + `.env` 后：

```bash
docker load -i dataagent.tar.gz   # 导入镜像，不联网、不用配加速器
chmod +x run.sh
./run.sh                          # 监听 8001
```

前端把 `/chat /stop /models /sessions /memory /static` 代理到 `http://localhost:8001`，不用管旧的 `/` 页面。接口协议见 [API.md](API.md)。

## 配置

全部走 `.env`，镜像里不写死。数据库 / IoTDB / 日志路径 / LLM 地址和 Key 改 `.env` 后重启容器生效，不用重 build。变量清单见 `.env.example`。

## 故障排查

**拉不到基础镜像**：国内直连 Docker Hub 不通，配镜像加速器。编辑 `/etc/docker/daemon.json` 后重启 docker：

```json
{ "registry-mirrors": ["https://docker.m.daocloud.io"] }
```

```bash
sudo systemctl restart docker
```

DaoCloud 偶发 CDN 超时，重试即可；更稳的问运维要内网镜像源。备选：`docker.1panel.live`、`dockerproxy.net`、`docker.1ms.run`。tarball 交付不联网，不受影响。

**没有 `docker compose`**：直接用上面的 `docker run` 或 `run.sh`，不依赖 compose。
