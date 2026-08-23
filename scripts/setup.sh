#!/usr/bin/env bash
# 拥爱（AMBRACE）一键部署（Linux / macOS）
set -e
cd "$(dirname "$0")/.."
echo "============================================"
echo "  拥爱（AMBRACE）一键部署"
echo "============================================"

command -v python3 >/dev/null 2>&1 || { echo "[错误] 未找到 python3，请先安装 Python 3.12+（3.14 也可）"; exit 1; }
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)" || { echo "[错误] 需要 Python 3.12+（3.14 也可）"; exit 1; }

echo "[1/4] 创建虚拟环境..."
[ -d backend/.venv ] || python3 -m venv backend/.venv

echo "[2/4] 安装依赖（首次约 2-5 分钟）..."
backend/.venv/bin/pip install --upgrade pip >/dev/null
backend/.venv/bin/pip install -r backend/requirements.txt

echo "[3/4] 生成配置文件..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  已生成 .env（含全部可选项，留空即可用默认）"
fi

echo "[4/4] 自检..."
if ! backend/.venv/bin/python -c "import tkinter" >/dev/null 2>&1; then
    echo "  [提示] 桌面控制台需要 Tk 支持（Ubuntu/Debian：sudo apt install python3-tk；macOS 用 python.org 安装包自带）"
fi
if [ ! -d backend/models/bge-m3 ]; then
    echo "  [警告] 缺少向量模型目录 backend/models/bge-m3，记忆系统将无法启动"
else
    echo "  [OK] 向量模型目录存在"
fi

echo ""
echo "部署完成！下一步："
echo "  1. 启动服务（Linux/macOS）："
echo "       cd backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
echo "     （Windows 用：backend/.venv/Scripts/python scripts/server_manager.py start）"
echo "  2. 查看连接信息：backend/.venv/bin/python scripts/get_server_info.py"
echo "  3. 登录主账号，在「设置 → API 配置」填写 LLM Key（聊天必需）"
echo "  4. 手机安装 APK，在设置页填入服务器地址"
echo "  （长期运行建议配置 systemd/launchd 守护，详见发布包 README「Linux/macOS 部署」）"
