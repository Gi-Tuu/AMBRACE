# M8 Core sidecar 镜像（仅安装 wheel，不 vendor 源码）
# 构建：docker build -f deploy/core.Dockerfile --build-arg CORE_VERSION=0.62.7 -t yai-agent-core:0.62.7 deploy
FROM python:3.12-slim

ARG CORE_VERSION=0.62.7
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    YAI_TOOL_TIMEOUT=60

# [sidecar] 含 server/openapi/llm；console script 与 python -m yai_core.server 均可
RUN pip install --no-cache-dir "yai-agent-core[sidecar]==${CORE_VERSION}"

EXPOSE 8001
# 不写死密钥；由 compose/环境注入
CMD ["python", "-m", "yai_core.server", "--host", "0.0.0.0", "--port", "8001"]
