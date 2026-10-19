#!/usr/bin/env bash
# Data Agent 后端一键启动（给前端联调用）
#
# 前提：
#   1. 镜像 dataagent:latest 已就绪：
#        - 收到 tarball 后先 `docker load -i dataagent.tar.gz`
#        - 或自己 `docker build -t dataagent:latest .`
#   2. 同目录下有一份 .env（数据库 / IoTDB / LLM 配置，参考 .env.example）
#
# 用法：./run.sh
set -euo pipefail
cd "$(dirname "$0")"

IMAGE="dataagent:latest"
NAME="dataagent"
PORT="${DATA_AGENT_PORT:-8001}"

if [ ! -f .env ]; then
  echo "❌ 缺少 .env 配置文件" >&2
  echo "   请复制 .env.example 为 .env，并填入数据库 / IoTDB / LLM 配置。" >&2
  exit 1
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "❌ 找不到镜像 $IMAGE" >&2
  echo "   先导入镜像：docker load -i dataagent.tar.gz" >&2
  echo "   或本地构建：docker build -t $IMAGE ." >&2
  exit 1
fi

# 幂等：先移除旧容器
docker rm -f "$NAME" >/dev/null 2>&1 || true

docker run -d --name "$NAME" \
  --env-file .env \
  -p "${PORT}:8001" \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/data/charts:/app/static/charts" \
  -v "$(pwd)/data/reports:/app/static/reports" \
  --restart unless-stopped \
  "$IMAGE"

echo "✅ Data Agent 已启动：http://localhost:${PORT}"
echo "   接口协议见 API.md（前端代理 /chat /stop /models /sessions /memory /static 到该地址）"
echo "   查看日志：docker logs -f $NAME"
echo "   停止服务：docker stop $NAME"
