# -*- coding: utf-8 -*-
"""生成开源发布包（脱敏）：复制 git 跟踪文件 → 排除隐私/内部文件 → 副本脱敏 → 生成 README/LICENSE → 可选 zip

用法：python scripts/make_release.py [--zip]
输出：<release_output>/ai_companion_public/
"""
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = r"release_output"
OUT_DIR = os.path.join(OUT_ROOT, "ai_companion_public")

# git 跟踪清单 = 复制白名单（排除一切未入库的本机生成物）
def tracked_files():
    out = subprocess.run(
        ["git", "-C", PROJECT_ROOT, "ls-files"], capture_output=True, text=True
    ).stdout.splitlines()
    return [f.replace("\\", "/") for f in out if f.strip()]

EXCLUDE_PREFIX = (".agents/", "flutter.bat",  # 内部工具/技能目录与本机脚本，不进开源包
                  "AGENTS.md", "HANDOFF.md",
                  "restart_server.bat", "start_server.bat",
                  # 开发文档与内部规划（含真实用户名/角色名等隐私）不进开源包
                  "docs/")

# 副本内脱敏替换（相对路径 → [(old, new), ...]）
DESENS = {
    "flutter_app/lib/screens/home_screen.dart": [("192.168.x.x", "192.168.x.x")],
    "scripts/make_release.py": [
        (r"release_output", "release_output"),
        ("192.168.x.x", "192.168.x.x"),
    ],
    # 脱敏本机内部路径（开发/评估脚本，发布包不暴露作者磁盘路径）
    "scripts/evaluate_bm25_hybrid.py": [
        ("D:/Codex-Projects/output", "release_output"),
        (r"D:\\Codex-Projects\\output", "release_output"),
    ],
    "scripts/scan_hardcoded_colors.py": [
        (r"D:\\AICompanionServer", "AMBRACE_ROOT"),
    ],
}

README = """# 拥爱（AMBRACE）

自托管的 AI 伙伴陪伴应用：多个可配置的 AI 角色与你聊天、记记忆、发朋友圈、写日记、养宠物，并可选开启手机感知（读屏 / 剪贴板 / 相册 / 通知），让 AI 更贴近你的生活。

## 功能特性
- **多角色陪伴**：自定义 AI 角色（性格 / 头像 / 关系网：朋友、对象等）
- **聊天**：SSE 真流式回复（逐字上屏打字机、无需等完整回复），连续发送，图片理解（默认本地 OCR 识文，可选云端视觉 / 本地 VLM，图片不进聊天 LLM）
- **语音回复**：语音模式下逐句合成——边打字边出声，逐句顺序播放，新语音发出即打断上一轮播报（TTS 合成）
- **群聊**：多角色家庭群聊，@ 指定角色回应；每角色可调话痨度与静音，三层漏斗挑选回应者（@必回 → 概率激活 → 随机兜底），群记忆按发言者归属
- **群聊游戏**：小家「游戏机」与群聊 /play 可玩 6 款游戏（谁是卧底 / 真心话大冒险 / 猜词20问 / 狼人杀 / 骗子酒馆 / 海龟汤）；规则引擎与 AI 分离、信息隔离防作弊，AI 角色当玩家陪你玩，角色心情好还会自己约局；游戏过程实时推送，结束后生成「游乐手札」记录
- **小家**：像素家居（多房间、家具自由摆放 / 编辑，AI 生活可视）
- **织库（织网）**：全景记忆 2.5D / 真 3D 画布（flutter_scene 缩放旋转、卡片纹理、法线标签、连线深浅），卡片整理
- **记忆系统**：向量检索（bge-m3）+ BM25 混合召回、记忆链条（关联查看 / 修改 / 级联软删）、记忆衰减曲线可视化（艾宾浩斯遗忘曲线）、AI 内心世界（记忆召回 / 复盘 / 任务 / 工具轨迹）
- **世界书 / Lorebook**：触发式注入进阶（关键词 / 正则 / 概率 / Inclusion Group 互斥 / 粘性 / 冷却），按角色自定义设定与世界观
- **主动消息**：AI 思念 / 状态触发等主动搭话，自然度评分（低分自动重试或跳过）、用户作息学习（活跃时段推断）、手动触发测试接口；主动消息可输出 [MEMO] 异步记备忘录到小手机（不发给用户）
- **朋友圈**：AI 角色发动态、互相评论（多用户隔离）
- **日记**：每天自动为角色生成第一人称日记
- **宠物系统**：折纸风格小动物，领养 / 喂食 / 玩耍 / 清洁
- **手机感知（Android）**：无障碍读屏、剪贴板、相册最近图片、通知监听、AI 主动提通知
- **权限管理**：三合一标签页（AI 能力权限 / 主账号管理 / 服务器功能管理），首个注册账号自动设为主账号
- **插件系统**：页面型 / 零代码插件 + GitHub 远程插件市场
- **MCP 接入**：标准 Model Context Protocol（stdio / SSE / streamable-http），AI 可调用 MCP 工具，资源 / prompts 注入，调用日志可查
- **消息通知**：App 后台保活（前台服务 + WebSocket 实时推送 + 系统通知，断线自动重连）
- **设计主题（Design Token）**：浅色 / 深色模式 + 多款主题色，外观设置一键切换
- **新手引导**：首次使用 Onboarding 引导（连接服务器 → 注册账号 → 创建角色 → 配置 API Key），完成即可开聊
- **升级更稳**：Alembic 数据库版本链迁移，跨版本升级不丢数据
- **备份**：设置页一键导出备份（数据库 + 配置 zip），附恢复指引

## 技术栈
- 后端：FastAPI + SQLAlchemy(async) + SQLite + ChromaDB + bge-m3（本地向量）+ BM25 混合检索（jieba + rank-bm25）
- 前端：Flutter（**仅支持 Android 构建**，手机感知依赖 Android 原生服务），Provider + Dio + WebSocket + flutter_local_notifications + flutter_background_service（后台保活）
- LLM：任意 OpenAI 兼容端点（默认 DeepSeek，可用户级 BYOK 覆盖）；图片理解默认本地 OCR，可选云端视觉 API 或本地 VLM
- 部署：一键脚本（setup.bat / setup.sh）与 Docker 容器（多架构 amd64 / arm64，镜像 ghcr.io/gi-tuu/ambrace，首次启动自动下载模型）

## 目录结构
```
backend/    FastAPI 服务（app/ 业务代码）
flutter_app/ Flutter 客户端（lib/ 业务代码）
scripts/    运维脚本（server_manager.py 启停/体检/修复，watchdog.py 守护，backup.py 备份，setup.bat/setup.sh 一键部署，uninstall.bat/uninstall.sh 卸载，build_apk.bat/build_apk.sh 一键打包 APK，get_server_info.py 获取服务器地址，init_db.py 手动重建表，download_models.py 下载向量模型，setup_local_vlm.py 可选下载本地识图模型）
server_controller/  桌面控制台（跨平台 tkinter：Windows 双击 start_controller.vbs / Linux·macOS 执行 bash start_controller.sh）
docs/       开发文档（架构 / 规划 / 全景，除 changelog.md（App 更新公告数据源）外不随开源包分发）
```

## 快速开始

### 1. Docker 部署（推荐给已装 Docker 的用户）
- 拉取镜像：`docker pull ghcr.io/gi-tuu/ambrace:latest`；或用仓库根的 `docker-compose.ghcr.yml` 直接跑（免本地构建）：`docker compose -f docker-compose.ghcr.yml up -d`
- 想自行构建镜像（可改代码）：`docker compose up -d`（使用默认 `docker-compose.yml`，`build: .`）
- 首次启动会自动下载向量模型（约 542MB）；数据 / 配置经卷持久化
- 容器监听端口 **8000**；健康检查 `http://127.0.0.1:8000/api/v1/system/ready`
- 说明：模型首次下载需联网；离线可自行挂载 `backend/models` 卷

### 2. 后端（一键部署，推荐）
- Windows：运行 `setup.bat`（双击或在项目根目录执行）
- Linux / macOS：运行 `bash setup.sh`

脚本会自动创建虚拟环境、安装依赖、生成 `.env`（全部留空即可用默认）并自检模型目录。

> ⚠️ **模型说明**：发布包内 `backend/models/` 含向量模型 `bge-m3`（记忆系统必需，本地 ONNX 1024d，约 542MB）与语音转写模型 `whisper-small`（可选功能，缺失时语音转写自动降级）。请使用 Release 完整包部署，或运行 `python scripts/download_models.py` 下载向量模型。
>
> 🎤 **语音转写（可选）**：发送语音消息需要额外安装 `pip install -r backend/requirements-voice.txt`（faster-whisper，懒加载，未安装时语音消息仅存音频不转写，日志会提示安装命令）。

手动方式：
```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
# Linux/macOS: .venv/bin/pip install -r requirements.txt
cp ../.env.example .env                         # 可全部留空（推荐）
```
数据库无需手动建：首次启动服务时自动建库（`backend/data/sqlite/`）。

LLM Key 推荐落库（代码/.env 零密钥）：启动后用主账号登录，进入「设置 → API 配置」填写；
或直接调用管理接口（仅主账号 user_id=1）：
- `PUT /api/v1/system/api-config/server`        服务器级 LLM（聊天必需，推荐）
- `PUT /api/v1/system/image-gen-config/server`  生图（可选）

启动服务（Windows，推荐，自动拉起 watchdog 守护）：
```bash
python scripts/server_manager.py start
```
或手动（先进入 backend 目录，否则无法 import app）：
```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # Windows
# Linux/macOS: .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Linux / macOS 启动（server_manager / watchdog 为 Windows 专属；桌面控制台为跨平台程序，请用下方「2.4 Linux/macOS 部署与守护」）：
```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`.env` 关键配置（详见 `.env.example`）：

| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | LLM API Key（可选：推荐经管理接口落库；填此处为 .env 兜底） |
| `DEEPSEEK_BASE_URL` | API 地址，默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 模型名 |
| `IMAGE_GEN_PROVIDER` | 生图 provider：`dashscope`（通义千问 Qwen-Image）或 `openai`（可选，推荐落库） |
| `SERVER_HOST` / `SERVER_PORT` | 服务监听地址，默认 `0.0.0.0:8000` |
| `DATABASE_URL` | SQLite 路径，默认 `./data/sqlite/ai_companion.db` |
| `CHROMA_PERSIST_DIR` | 向量库目录 |
| `VLM_ENABLED` | 本地识图 VLM 开关，默认 `false`（关闭） |
| `VLM_API_KEY` | 云端视觉 API Key（OpenAI 兼容，如阿里云百炼 Qwen-VL）；非空时优先走云端 |
| `VLM_BASE_URL` / `VLM_MODEL` | 视觉端点与模型名（本地默认 `http://127.0.0.1:11434` / `qwen2.5vl:3b`） |
| `VLM_OLLAMA_EXE` / `VLM_OLLAMA_MODELS_DIR` | 本地 Ollama 路径（自愈重启用，可选） |

### 2.1 图片理解（识图/识文）配置
默认已可用：本地 OCR 识文（随依赖安装，无需额外下载）。自然语言识图可选两种方式：
- **云端视觉（推荐）**：在 `.env` 填 `VLM_API_KEY` + `VLM_BASE_URL`（OpenAI 兼容端点，如阿里云百炼 `https://xxx/compatible-mode/v1`）+ `VLM_MODEL`（如 `qwen-vl-max`），填写后自动优先生效。
- **本地离线**：运行 `python scripts/setup_local_vlm.py` 一键安装 Ollama 并下载 `qwen2.5vl:3b`（约 3.2GB），再把 `.env` 的 `VLM_ENABLED` 改为 `true` 并填 `VLM_OLLAMA_EXE` / `VLM_OLLAMA_MODELS_DIR`。
图片二进制始终只发给本地 OCR 或你指定的视觉端点，**不会**进入聊天 LLM。

### 2.1.5 Windows 防火墙（可选）
局域网/手机访问被拦时，以管理员运行 `scripts\\open_firewall.bat` 放行 8000 端口（Linux 防火墙见 2.4 第 5 步）。

### 2.2 获取服务器地址（手机端填写）
服务器启动后运行 `python scripts/get_server_info.py`，会打印局域网地址（手机与服务器同一 Wi-Fi 时填）：`http://192.168.x.x:8000`；也可直接访问 `http://127.0.0.1:8000/api/v1/system/status` 查看 `lan_ip`。

### 2.3 Tailscale 远程连接（跨网络访问电脑上的服务器）
手机与电脑不在同一 Wi-Fi（用 4G/5G 流量、或人在异地）时，局域网地址不可达，推荐用 **Tailscale** 组网：把两台设备放进同一个加密虚拟局域网，电脑的 8000 端口就像在同一 Wi-Fi 一样可达。

**电脑端（服务器）**
1. 安装 Tailscale：https://tailscale.com/download（Windows 下载安装包，用微软/谷歌/邮箱账号登录即可）
2. 登录后运行 `tailscale ip -4`（或看任务栏 Tailscale 图标里的 IP），得到 `100.x.y.z` 格式的地址
3. 保持电脑开机且**不休眠**：Windows 设置 → 系统 → 电源 → 睡眠改为"从不"；笔记本合盖外接电源时也要允许不休眠，否则远程期间服务器会离线

**手机端**
1. 安装 Tailscale App（应用商店搜 Tailscale）并登录**同一账号**；若给朋友访问，由你在 https://login.tailscale.com 管理后台把朋友设备加入你的网络
2. 打开 Tailscale 的 VPN 开关（状态显示已连接）
3. App「设置 → 服务器地址」填 `http://100.x.y.z:8000`（100.x.y.z 换成电脑的 Tailscale IP）

**验证连通**
- 手机浏览器访问 `http://100.x.y.z:8000/api/v1/system/status`，能看到 JSON 即连通成功
- 手机用 4G/5G 流量也能连（流量走 Tailscale 加密隧道，不需要与电脑同 Wi-Fi）

**常见问题**
- 连不上：先在电脑上确认 `tailscale status` 显示在线、服务器已启动（电脑本机访问 `http://127.0.0.1:8000/api/v1/system/status`）
- 跨账号访问：朋友需被你加入 Tailscale 网络（管理后台 → Users → 邀请），且朋友手机需开启 Tailscale 开关
- 安全提示：默认 HTTP 明文仅适合 Tailscale 私有网络内使用；如需暴露到公网请自行配置 HTTPS 反向代理

### 2.4 Linux/macOS 部署与守护
> 说明：桌面控制台（`server_controller/`）为跨平台 tkinter 程序，Windows / Linux / macOS 均可使用；`scripts/server_manager.py`、`scripts/watchdog.py` 依赖 Windows 特性（pythonw/PowerShell），**仅 Windows 可用**；Linux/macOS 用下方方式直接运行与守护，功能完全一致。

**桌面控制台（三平台通用）**（Windows 完整可用：启停/重启/日志/Ollama；Linux/macOS 支持启停 uvicorn、启停 Ollama 与查看日志，系统级守护请用 systemd/launchd）
- Windows：双击 `server_controller/start_controller.vbs`
- Linux / macOS：`bash server_controller/start_controller.sh`（需先完成下方依赖安装，控制台可启动/停止/重启服务器、启停 Ollama、查看日志，与 Windows 版功能一致）

**1. 安装依赖（前置：Python 3.12+（3.14 亦可））**
```bash
bash setup.sh            # 自动建 venv、装依赖、生成 .env、自检模型目录
```
或手动：
```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # 可全部留空（推荐）
```

**2. 前台启动（快速验证）**
```bash
cd backend
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
首次启动会自动建库（`backend/data/sqlite/`）与加载向量模型（约 30 秒）。浏览器访问 `http://127.0.0.1:8000/api/v1/system/status` 看到 JSON 即成功。

**3. 后台守护（推荐 systemd，Linux）**
新建 `/etc/systemd/system/ai-companion.service`（把 `/path/to/ai_companion_public` 换成你的解压目录）：
```ini
[Unit]
Description=AMBRACE Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/ai_companion_public/backend
ExecStart=/path/to/ai_companion_public/backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
启用并启动：
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ai-companion
sudo systemctl status ai-companion        # 查看状态
journalctl -u ai-companion -f             # 实时日志
```

**4. macOS 后台守护（launchd）**
创建 `~/Library/LaunchAgents/com.aicompanion.server.plist`，plist 的 `ProgramArguments` 指向 `backend/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`，`WorkingDirectory` 指向 `backend`；然后：
```bash
launchctl load ~/Library/LaunchAgents/com.aicompanion.server.plist
```

**5. 防火墙放行 8000 端口**
```bash
# ufw（Ubuntu/Debian）
sudo ufw allow 8000/tcp
# firewalld（CentOS/RHEL/Fedora）
sudo firewall-cmd --permanent --add-port=8000/tcp && sudo firewall-cmd --reload
```

**6. 日志与备份**
- 服务日志：systemd 场景用 `journalctl -u ai-companion`；其余场景 stdout 重定向到文件即可
- 每日备份：`backend/.venv/bin/python scripts/backup.py`（可配合 cron：`0 3 * * * cd /path/to/ai_companion_public && backend/.venv/bin/python scripts/backup.py`）

**7. 局域网/Tailscale 连接**
与 Windows 一致：运行 `backend/.venv/bin/python scripts/get_server_info.py` 获取地址；跨网络用 Tailscale（见 2.3 节，电脑端安装步骤同样适用于 Linux/macOS）。

### 3. 前端（Flutter）
```bash
cd flutter_app
flutter pub get
flutter build apk --debug
```
依赖拉取（`flutter pub get`）的镜像由你本机环境决定：未配置时默认官方 `pub.dev`。中国大陆网络访问不稳定时请先配置镜像：
- Windows（PowerShell）：`setx PUB_HOSTED_URL "https://pub.flutter-io.cn"`，然后**重新打开终端**
- Linux/macOS：`export PUB_HOSTED_URL=https://pub.flutter-io.cn`（可写入 `~/.bashrc`）

安装 APK 后，在设置页填入服务器地址（手机与服务器同一局域网，或经 Tailscale 等组网）。

### 3.1 安卓兼容性
- **最低系统**：Android 7.0（minSdk 24，Shizuku 要求）；完整体验（手机感知/媒体读取）推荐 Android 10 及以上；targetSdk 随 Flutter SDK（35+），编译产物覆盖 arm64-v8a / armeabi-v7a / x86 / x86_64 四种架构（骁龙/天玑/麒麟/展锐等主流芯片均可安装）
- **通知权限**：Android 13+ 首次打开需在系统弹窗中允许"通知"权限，否则收不到横幅/系统通知
- **定位权限（位置信息）**：在「手机感知 → 位置信息」开启"获取地理位置"时，系统会弹出定位授权（建议选"仅使用期间允许"即可）；若提示失败，请检查系统"定位服务"已开启、且应用定位权限未被厂商默认拦截（部分国产 ROM 需到 设置→应用→权限 手动允许）
- **手机感知权限**：需在系统设置中手动开启本应用的无障碍服务与"通知使用权"（各厂商路径不同，常见为：小米/红米 设置→更多设置→无障碍；华为/荣耀 设置→辅助功能→无障碍；OPPO/vivo/iQOO 设置→其他设置→无障碍 / 更多设置→无障碍）
- **后台稳定**：将本应用加入"电池优化白名单"并允许自启动/后台活动，否则息屏后连接与通知可能被系统杀掉（厂商 ROM 差异较大）
- **免编译安装**：不想自己编译时，可直接安装 Release 附件中的 APK（与源码同版本）；自己编译需 Flutter SDK 3.27+、Android SDK 与 **JDK 17+**（AGP 8.3/Gradle 8.7 要求，首次构建会自动下载 Gradle 与依赖）

### 4. 手机感知（可选，Android）
- 在系统设置中开启本应用的无障碍服务、通知使用权（不同 ROM 名称不同，如 iQOO/OriginOS 需手动放行后台）
- 在应用内「手机感知」页打开对应开关（读屏 / 剪贴板 / 相册 / 通知 / AI 主动提通知）
- 需要「电池优化白名单」与「允许后台启动」以保证连接与弹窗稳定

### 5. 从 GitHub 源码部署（不使用 Release 完整包）
源码仓库与发布包有两点差异，补齐后与发布包完全一致：

1. **向量模型（必需）**：`backend/models/bge-m3`（约 542MB）未纳入 git。请从本仓库 **Release 附件**下载 `ai_companion_public.zip`，解压后把 `backend/models/` 整个目录复制到项目根（或直接解压整个包使用）。缺少该目录时后端会因记忆系统无法启动而报错（`setup.bat/sh` 自检也会提示）。
2. **APK（推荐最省事）**：直接下载 Release 附件的 APK 安装即可，无需安装 Flutter/Android SDK、无需任何配置。
   想自己编译也支持（需自装 Flutter SDK 3.27+ 与 Android SDK）：
   - 一键编译：运行 `scripts\\build_apk.bat`（Windows）或 `bash scripts/build_apk.sh`（Linux/macOS）——自动 `pub get`（联网失败自动切国内镜像）、编译、并把 APK 复制到项目根目录的 `output\\` 文件夹；默认打 release 包（约 24MB），加 `--debug` 参数可打 debug 包
   - 手动编译：在 `flutter_app/` 下执行 `flutter pub get`（国内先配置镜像，见上文）→ `flutter build apk --debug`，产物在 `flutter_app/build/app/outputs/flutter-apk/app-debug.apk`

> **签名说明**：未配置密钥时编译自动使用调试签名，可正常安装使用（仅无法上架应用商店）。

其余步骤与"快速开始"完全一致：`setup.bat` / `bash setup.sh` 一键部署后端 → 运行 `get_server_info.py` 获取地址 → App 设置页填服务器地址 → 按需开启手机感知权限。

### 6. 更新（后续版本升级）
按你获取项目的方式二选一：

**从 GitHub 源码更新**
```bash
git pull                                                             # 拉取最新代码
backend/.venv/Scripts/python.exe scripts/server_manager.py restart   # Windows
# Linux: sudo systemctl restart ai-companion
# macOS: launchctl kickstart -k gui/$(id -u)/com.aicompanion.server
```
- 数据库与上传数据在 `backend/data/`（未纳入 git），更新不会动它；`.env` 原样保留
- 数据库结构变更由启动时经 Alembic 版本链自动迁移（跨版本平滑升级、不丢数据），无需手动执行迁移命令
- 依赖有变化时重跑 `setup.bat` / `bash setup.sh` 即可

**从 Release 发布包更新**
1. 下载最新 `ai_companion_public.zip` 解压（可覆盖旧目录，或解压到新目录）
2. 保留数据：把旧目录的 `.env`、`backend/data/`、`backend/models/` 复制到新目录（直接覆盖解压则无需处理）
3. 启动：`backend\\.venv\\Scripts\\python.exe scripts\\server_manager.py start`（Linux/macOS 用 `backend/.venv/bin/python`）
4. APK：直接安装新 APK 覆盖即可（登录态与数据保留在服务器，不受影响）

**卸载**
- Windows：`scripts\\uninstall.bat`（默认保留数据与配置；追加 `--purge` 彻底清除）
- Linux / macOS：`bash scripts/uninstall.sh`（同上）

## 备份与恢复
- **App 一键导出**：设置 → 系统 → 数据备份，点击「导出备份」即可把 SQLite 数据库 + 配置 + 源码快照打包成 zip 保存到手机，并附恢复指引。
- **服务器端自动备份**：`scripts/backup.py` 每日自动备份到 `backups/` 目录（保留最近 14 天），也可手动执行。
- **恢复**：停止服务 → 解压备份 zip 覆盖 `backend/data` → 重新启动服务。

## 支持作者
如果这个项目给你带来了陪伴与快乐，欢迎支持作者 ☕

- **爱发电**：[前往爱发电支持作者](https://www.ifdian.net/a/gituu)
- **微信赞赏**：微信扫描下方赞赏码（金额随意，支持即动力）
- **抖音**：关注作者抖音 `dsOHOTzx`（复制到抖音搜索即可）
- **QQ 群**：加入作者交流群 `1065741798`（复制到 QQ 搜索加群）

![微信赞赏码](flutter_app/assets/reward_qrcode.png)

## 隐私说明
- 图片默认仅本地 OCR 识文；开启识图后，图片二进制只发给本地 VLM 或你自填的视觉 API 端点，**不会**进入聊天 LLM
- 数据默认全部存在你自己的服务器（SQLite + 本地向量库）
- 手机感知数据仅用于注入 AI 上下文，本仓库不包含任何收集上报

## License
[MIT](LICENSE)
"""

LICENSE = """MIT License

Copyright (c) 2026 AMBRACE contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def copy_with_desens(src_root, rel):
    src = os.path.join(src_root, rel.replace("/", os.sep))
    dst = os.path.join(OUT_DIR, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with io.open(src, "rb") as fin:
        data = fin.read()
    # 仅对文本文件做脱敏替换
    if rel.endswith((".py", ".md", ".dart", ".yaml", ".yml", ".json", ".toml", ".txt", ".ini", ".bat", ".ps1", ".properties", ".gradle", ".kts", ".xml", ".plist")):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            for old, new in DESENS.get(rel, []):
                text = text.replace(old, new)
            data = text.encode("utf-8")
    with io.open(dst, "wb") as fout:
        fout.write(data)
    return len(data)


def main():
    files = tracked_files()
    picked = [f for f in files if not f.startswith(EXCLUDE_PREFIX)]
    skipped = [f for f in files if f.startswith(EXCLUDE_PREFIX)]

    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    total = 0
    for rel in picked:
        total += copy_with_desens(PROJECT_ROOT, rel)

    # 捆绑向量模型：backend/models 未纳入 git（体积大），但记忆系统必须依赖，发布包必须携带
    models_src = os.path.join(PROJECT_ROOT, "backend", "models")
    if os.path.isdir(models_src):
        models_dst = os.path.join(OUT_DIR, "backend", "models")
        shutil.copytree(models_src, models_dst, ignore=shutil.ignore_patterns(".git"))  # 排除 HuggingFace 仓库自带的 .git LFS 缓存
        model_mb = 0
        for root2, _, names in os.walk(models_src):
            if os.sep + ".git" in root2:
                continue
            for n in names:
                try:
                    model_mb += os.path.getsize(os.path.join(root2, n))
                except OSError:
                    pass
        print(f"[release] bundled backend/models ({model_mb / 1024 / 1024:.1f} MB)")

    # App 更新公告数据源：docs/changelog.md 是唯一随包分发的 docs 文件（其余内部文档仍排除）
    changelog_src = os.path.join(PROJECT_ROOT, "docs", "changelog.md")
    if os.path.isfile(changelog_src):
        copy_with_desens(PROJECT_ROOT, "docs/changelog.md")
        print("[release] bundled docs/changelog.md")

    # 生成 README / LICENSE
    w(os.path.join(OUT_DIR, "README.md"), README)
    w(os.path.join(OUT_DIR, "LICENSE"), LICENSE)

    # 微信赞赏码（作者放置于项目根目录，发布前替换；不存在则跳过，README 占位图缺失属预期）
    qr_src = os.path.join(PROJECT_ROOT, "reward-qrcode.png")
    if os.path.isfile(qr_src):
        shutil.copy2(qr_src, os.path.join(OUT_DIR, "reward-qrcode.png"))
        print("[release] bundled reward-qrcode.png")

    # 复制后安全扫描：对脱敏后的发布包内容检查密钥/本机路径/局域网 IP（命中告警但不阻止）
    text_exts = (".py", ".md", ".dart", ".yaml", ".yml", ".json", ".toml", ".txt", ".ini", ".bat", ".ps1", ".properties", ".gradle", ".kts", ".xml", ".plist", ".example", ".lock")
    key_re = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}")
    priv_re = re.compile(r"([A-Za-z]:\\|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})")
    # 真实用户名/角色名等隐私词（命中告警，防止内部案例文档混入）
    # 名单从 scripts/.privacy_names 读取（本地维护，不随发布包分发）；文件不存在则跳过
    name_re = None
    name_file = os.path.join(PROJECT_ROOT, "scripts", ".privacy_names")
    if os.path.exists(name_file):
        with io.open(name_file, encoding="utf-8") as f:
            _names = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        if _names:
            name_re = re.compile("|".join(re.escape(n) for n in _names))
    warnings = []
    for root2, _, names in os.walk(OUT_DIR):
        for n in names:
            if not any(n.endswith(e) for e in text_exts) and '.' in n:
                continue  # 有扩展名且非文本 → 跳过；无扩展名（.gitignore/.metadata/VERSION/LICENSE）也扫描
            full = os.path.join(root2, n)
            rel = os.path.relpath(full, OUT_DIR).replace("\\", "/")
            if rel == "scripts/make_release.py":
                continue  # 其 OUT_ROOT 已脱敏；剩余命中是其自身正则模式定义，跳过
            rel = os.path.relpath(full, OUT_DIR).replace("\\", "/")
            try:
                text = io.open(full, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in key_re.finditer(text):
                warnings.append(f"[KEY] {rel}: {m.group(0)[:14]}...")
            for m in priv_re.finditer(text):
                warnings.append(f"[PRIV] {rel}: {m.group(0)}")
            if name_re:
                for m in name_re.finditer(text):
                    warnings.append(f"[NAME] {rel}: {m.group(0)}")
    if warnings:
        print("[release] 以下文件疑似含密钥/隐私（请人工确认后再上传）：")
        for warn in warnings[:30]:
            print("   " + warn)
    else:
        print("[release] 安全扫描通过：发布包无疑似密钥/本机路径/局域网 IP")

    print(f"[release] {len(picked)} files copied, {total / 1024:.1f} KB")
    print(f"[release] excluded {len(skipped)} privacy/internal files: {skipped}")

    if "--zip" in sys.argv:
        zpath = os.path.join(OUT_ROOT, "ai_companion_public.zip")
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, names in os.walk(OUT_DIR):
                for n in names:
                    full = os.path.join(root, n)
                    zf.write(full, os.path.relpath(full, OUT_ROOT))
        print(f"[release] zip: {zpath}")


if __name__ == "__main__":
    main()
