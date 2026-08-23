# AMBRACE 后端 Docker 镜像（2026-08-23，P2-1）
# 说明：本机未安装 Docker，本文件未实测；需在装有 Docker 的机器验证。
# 构建：docker compose build
# 运行：docker compose up（先确保 backend/models 下有 bge-m3 模型，backend/data 可写）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 后端代码与版本文件
COPY backend backend
COPY VERSION VERSION
COPY .env.example .env.example

WORKDIR /app/backend

EXPOSE 8000

# 单实例锁端口默认 8766（可用 INSTANCE_LOCK_PORT 覆盖）；CORS 默认 *（allow_credentials=False）
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
