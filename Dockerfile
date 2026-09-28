# 抖音数据监控工具 - Docker 镜像
# 构建: docker build -t douyin_tool .
# 运行: docker compose up -d
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 系统依赖（Playwright chromium 运行所需最小集）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium

# 应用代码
COPY . .

# 数据卷：登录态 / 监控数据 / 配置持久化
VOLUME ["/app/data", "/app/cookies"]

EXPOSE 8888

# 启动自检在 import app 时自动执行，日志中会打印环境就绪状态
CMD ["python", "app.py"]
