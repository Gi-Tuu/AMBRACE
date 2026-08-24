#!/usr/bin/env bash
# 拥爱（AMBRACE）卸载（Linux / macOS）
set -e
cd "$(dirname "$0")/.."
echo "============================================"
echo "  拥爱（AMBRACE）卸载"
echo "============================================"

PURGE=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
  esac
done

echo "[1/3] 停止服务与守护进程..."
# server_manager.py 的端口查询基于 PowerShell（Windows 专用），Linux/macOS 直接用 fuser/lsof 停 8000
if [ "$(uname -s)" = "Darwin" ]; then
  lsof -ti:8000 | xargs kill 2>/dev/null || true
  lsof -ti:8766 | xargs kill 2>/dev/null || true
elif [ "$(uname -s)" != "MINGW"* ] && [ "$(uname -s)" != "MSYS"* ]; then
  fuser -k 8000/tcp 2>/dev/null || true
  fuser -k 8766/tcp 2>/dev/null || true
else
  if [ -x backend/.venv/bin/python ]; then
    backend/.venv/bin/python scripts/server_manager.py stop || true
  elif command -v python3 >/dev/null 2>&1; then
    python3 scripts/server_manager.py stop || true
  fi
fi

echo "[2/3] 清理项目文件..."
KEEP_DIR="$(mktemp -d)"
trap 'rm -rf "$KEEP_DIR"' EXIT

if [ "$PURGE" = "1" ]; then
  echo "  [模式] 全量清除（--purge）：数据、配置、模型、依赖一并删除"
else
  echo "  [模式] 保留数据：.env、backend/data、backend/models 将保留"
  [ -f .env ] && mv .env "$KEEP_DIR/env"
  [ -d backend/data ] && mv backend/data "$KEEP_DIR/data"
  [ -d backend/models ] && mv backend/models "$KEEP_DIR/models"
fi

if [ "$PURGE" = "1" ]; then
  # 全量清除：删除项目内全部内容（含 scripts，POSIX 下运行中的脚本 fd 仍有效）
  find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf -- {} +
else
  # 保留模式：删除除 scripts（卸载器自身所在目录）外的全部内容
  find . -mindepth 1 -maxdepth 1 ! -name 'scripts' ! -name '.git' -exec rm -rf -- {} +
  mkdir -p backend
  [ -f "$KEEP_DIR/env" ] && mv "$KEEP_DIR/env" .env
  [ -d "$KEEP_DIR/data" ] && mv "$KEEP_DIR/data" backend/data
  [ -d "$KEEP_DIR/models" ] && mv "$KEEP_DIR/models" backend/models
fi

echo "[3/3] 完成"
if [ "$PURGE" = "1" ]; then
  echo "  已全量卸载。如需重新部署：重新解压发布包后运行 bash setup.sh"
else
  echo "  程序文件已卸载；数据与配置保留在："
  echo "    - .env"
  echo "    - backend/data（数据库 / 上传文件）"
  echo "    - backend/models（向量模型）"
  echo "  卸载器保留在 scripts/uninstall.sh，"
  echo "  如需彻底清除以上内容，请运行：bash scripts/uninstall.sh --purge"
fi
