# M8 Windows 安装/启动（骨架）

> 目标：单产品安装后一条命令启动；Core 作为 wheel sidecar 由 launcher 拉起，不要求用户装第二个项目。

## 前置

- Python 3.12/3.13（AMBRACE venv 3.14.2 已验证 Core 0.62.7 可启动）；
- 已解压/安装 AMBRACE 到 `InstallDir`，`backend/.venv` 就绪；
- 模型配置由安装器/用户提供：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `LLM_MODEL`（OpenAI-compatible）。

## 模型配置（首次）

编辑 `InstallDir\config\ambrace.env`：

```
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

优先级：`CORE_LLM_* > OPENAI_* > LLM_* > DEEPSEEK_*`；未配置时脚本以 `YAI_SIDECAR_OFFLINE=1` 启动并提示配置向导（不崩溃），配置后重启即可。

## 启动

```powershell
pwsh -NoProfile -File deploy\windows\start.ps1 -InstallDir D:\AiCompanion -CoreVersion 0.62.7
# 需要设备/记忆写工具时加 -AllowWrites
```

脚本会：

1. 校验 `backend/.venv`；
2. 生成/复用 `secrets/ambrace_service_token.txt` 与 `secrets/yai_service_key.txt`（不入库、不进日志）；
3. 如未安装则 `pip install "yai-agent-core[sidecar]==<版本>"`；
4. 起 AMBRACE 8000，等 health；
5. 以 `CORE_START_MODE=local` 起 `app.core_sidecar`，等 `/readyz` + 期望工具数（0/4）。

## 停止

```powershell
pwsh -NoProfile -File deploy\windows\stop.ps1 -InstallDir D:\AiCompanion
```

先 `POST /v1/shutdown`（service-key）优雅停 Core，再停 8000，最后清理残留 `yai_core.server`。

## 验收

- 默认（无 `-AllowWrites`）：Core `/v1/tools` = 0，UI 徽标「工具服务：在线 0/0」；
- `-AllowWrites` + `OPENAPI_ALLOW_WRITES=true`：4 工具；
- 停止后 `netstat -ano | findstr :8001` 无监听、无 `yai_core.server` 进程。
