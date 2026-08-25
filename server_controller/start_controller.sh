#!/usr/bin/env bash
# 拥爱（AMBRACE）服务器控制台（Linux/macOS 启动入口）
# 用法：bash start_controller.sh
# 前置：需先安装依赖（cd scripts && bash setup.sh，或手动创建 backend/.venv 并 pip install -r backend/requirements.txt）
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${SCRIPT_DIR}/../backend/.venv/bin/python"
if [ ! -x "${PY}" ]; then
  echo "错误：未找到 ${PY}"
  echo "请先安装依赖：cd scripts && bash setup.sh"
  echo "（或手动：python3 -m venv ../backend/.venv && ../backend/.venv/bin/pip install -r ../backend/requirements.txt）"
  exit 1
fi
exec "${PY}" "${SCRIPT_DIR}/server_controller.py"
