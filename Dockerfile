# AMBRACE 后端 Docker 镜像（2026-08-23，P2-1）
# 说明：已于 2026-08-23 在 Docker Desktop 4.87 + WSL2 实测通过（构建/启动/健康/聊天全链路）。
# 构建：docker compose build
# 运行：docker compose up
# GHCR 模型方案：镜像不内置 backend/models/bge-m3（约 542MB，体积大且架构无关）；容器启动时由
#   scripts/docker_entrypoint.sh 检测模型缺失则自动执行 scripts/download_models.py 拉取
#   （首启下载，compose 挂载卷缓存在宿主机，下载一次后续复用），保证 docker compose up 即可用。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖（利用层缓存）：requirements.txt 是由 pip-compile 生成的「钉版锁文件」（== 版本），
# 保证每次构建依赖一致可复现。注：未启用 --require-hashes（requirements.in 头部已说明多平台
# wheel + 多架构 buildx 构建会使单平台哈希失效），此处以版本钉版实现可复现安装，未引入假安全。
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 后端代码、版本文件、示例环境、运维脚本（download_models.py / docker_entrypoint.sh）
COPY backend backend
COPY VERSION VERSION
COPY .env.example .env.example
COPY scripts scripts

WORKDIR /app/backend

EXPOSE 8000

# 非 root 运行（3.4）：非特权用户 ambrace(uid 10001)，数据/模型目录归其所属。
# 挂载卷 ./backend/data 与 ./backend/models 的宿主机属主需与 uid 10001 对齐（见 docs/docker-deploy.md
#  的「chown -R 10001:10001 backend/data backend/models」），否则容器内写不进挂载卷（非 root 回归）。
RUN groupadd -r ambrace && useradd -r -g ambrace -u 10001 -d /app -s /usr/sbin/nologin ambrace \
 && mkdir -p /app/backend/data /app/backend/models \
 && chown -R ambrace:ambrace /app
USER ambrace

# 单实例锁端口默认 8766（可用 INSTANCE_LOCK_PORT 覆盖）；CORS 默认 *（allow_credentials=False）
# 入口脚本：模型缺失时首次启动自动下载，随后前台启动 uvicorn
CMD ["sh", "/app/scripts/docker_entrypoint.sh"]
