# douyin_mcp（抖音渠道插件）

让 AI 拥有自己的抖音账号：发布图文、回复粉丝评论、感知账号动态，还能看懂自己发过的图文（发布/回复默认需你确认）。

## 运行依赖（重要）

| 依赖 | 安装命令 |
|------|----------|
| `playwright`（Python 包） | `backend\.venv\Scripts\python.exe -m pip install playwright` |

- 插件与内核**同进程**运行：依赖必须装进**后端虚拟环境** `backend\.venv`（装到系统 Python 无效）。
- 浏览器用**本机已安装的 Edge**（`channel=msedge`，反检测口径）：**不需要** `playwright install`，不会下载 Chromium。
- 装完重启一次服务器，插件会重新初始化浏览器上下文（首次扫码登录也依赖它）。
- 自检：`backend\.venv\Scripts\python.exe -c "import playwright; print(playwright.__version__)"`；服务日志里不应再出现 `[plugin:douyin_mcp] Edge 预热失败（按需冷启动兜底）: No module named playwright`。

> 2026-09-12 实录：目录改名重建 venv 时漏装该依赖，Edge 预热直接失败；补装 `playwright 1.62.0` 后恢复。
