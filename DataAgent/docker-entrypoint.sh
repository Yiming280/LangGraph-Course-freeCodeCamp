#!/bin/sh
set -e

# 图表 / 报告输出目录兜底创建（未挂载卷时也要能写）
mkdir -p /app/static/charts /app/static/reports

# 把会写盘的扁平状态文件软链到 /data（若挂载了持久化卷）。
# sqlite 会沿软链目标解析，-wal/-shm 也跟着落到 /data，跨重启不丢。
if [ -d /data ]; then
  for f in agent_memory.sqlite settings.json error_log.jsonl; do
    ln -sfn "/data/$f" "/app/$f"
  done
fi

exec "$@"
