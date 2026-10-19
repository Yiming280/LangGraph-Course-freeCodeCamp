FROM python:3.12-slim

# 中文字体（matplotlib 图表）+ CA 证书（访问 https 代理）
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 只设一个服务默认端口，业务配置一律从外部 env 注入
ENV PYTHONUNBUFFERED=1 \
    DATA_AGENT_PORT=8001

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8001
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "app.py"]
