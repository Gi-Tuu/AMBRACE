#!/bin/sh
# AMBRACE 容器入口（2026-08-23 GHCR）
# 方案：镜像不内置 backend/models/bge-m3（约 542MB，体积大、架构无关）；首次启动若模型缺失，
# 自动执行 scripts/download_models.py 拉取（来自 GitHub Release 或 HuggingFace），挂载卷缓存在宿主机。
# 下载失败不阻断服务（记忆/检索功能需在容器内补跑 download_models.py 或挂载已含模型的卷）。
set -e

# 2026-08-24（新机部署报告 P1-3）：用户直接 docker compose up 且未先跑 setup 时，
# Docker 会把宿主机不存在的 ./.env 自动创建成【目录】，容器内 /app/.env 失效。
# 此处兜底：目录改名保留，缺失时从 .env.example 生成默认文件。
if [ -d /app/.env ]; then
  echo "[docker-entrypoint] 警告：/app/.env 是目录（Docker 自动创建），重命名为 .env.docker-dir"
  mv /app/.env /app/.env.docker-dir
fi
if [ ! -f /app/.env ]; then
  cp /app/.env.example /app/.env
  echo "[docker-entrypoint] 已从 .env.example 创建默认 .env（请按需填写 API Key）"
fi

MODEL_TOKENIZER="/app/backend/models/bge-m3/tokenizer.json"
MODEL_ONNX="/app/backend/models/bge-m3/onnx/model_int8.onnx"

if [ -f "$MODEL_TOKENIZER" ] && [ -f "$MODEL_ONNX" ]; then
  echo "[docker-entrypoint] 检测到 bge-m3 向量模型，跳过下载。"
else
  echo "[docker-entrypoint] bge-m3 向量模型缺失，首次启动自动下载（约 542MB）..."
  if python /app/scripts/download_models.py; then
    echo "[docker-entrypoint] 向量模型下载完成。"
  else
    echo "[docker-entrypoint] 向量模型下载失败（网络受限或未联网）。" >&2
    echo "[docker-entrypoint] 服务照常启动；记忆/检索功能需在容器内重跑 scripts/download_models.py" >&2
    echo "[docker-entrypoint] 或挂载已含模型的 backend/models 卷到 /app/backend/models。" >&2
  fi
fi

# 启动 FastAPI 服务（保持前台 PID=1，接收信号）
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
