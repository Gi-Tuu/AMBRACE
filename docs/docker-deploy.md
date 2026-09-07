# Docker 部署与实测（2026-08-23）

> 本机已完成 Docker Desktop（WSL2）实测，除「未配置 API Key 时聊天」外全部通过；聊天链路在临时注入 DeepSeek Key 后也已通过（真实 LLM 回复 + 记忆写入）。

## 部署方式

前置：本机安装 Docker Desktop（Windows）并启动（需要 WSL2，首次安装需启用功能并重启）。

```bash
# 在项目根目录
docker compose build   # 构建 ambrace:latest（拉 python:3.12-slim + 依赖，首次约 30s-10min）
docker compose up -d   # 启动（挂载 models/data/.env，端口 8000）
```

验证：

```bash
curl http://localhost:8000/api/v1/system/health   # {"status":"ok"}
curl http://localhost:8000/api/v1/system/ready    # {"status":"ok","db":true,"model":true}
```

## 关键点

- **模型**：`backend/models/bge-m3`（542MB）通过 volume 挂载，不在镜像内；未下载则 `/ready` 的 model=false，聊天时返回明确报错（P0-3 修复生效）。
- **配置**：项目根 `.env`（含 `DEEPSEEK_API_KEY` 等）只读挂载到 `/app/.env`；也支持 `docker run -e DEEPSEEK_API_KEY=...` 注入（不落盘，容器删除即消失）。
- **数据**：`backend/data`（SQLite + ChromaDB + BM25 缓存）挂载持久化，升级/重建容器不丢数据。
- **健康检查**：compose healthcheck 走 `/api/v1/system/ready`。
- **锁端口**：容器内单实例锁默认 8766（`INSTANCE_LOCK_PORT` 可覆盖）；CORS 默认 `*`（`CORS_ORIGINS` 可配置）。

## 实测记录（本机 Windows 11 + Docker Desktop 4.87 + WSL2 2.7.12）

| 步骤 | 结果 |
|---|---|
| `docker compose build` | ✅ 构建成功 |
| 容器启动 | ✅ |
| `/health` | ✅ ok |
| `/ready` | ✅ db=true, model=true（bge-m3 可用） |
| 注册 / 登录 / 建角色 | ✅ |
| 发消息（无 Key） | ✅ 400 + 明确提示（P1-4 生效） |
| 发消息（临时注入 Key） | ✅ 200，AI 真实回复，memories_updated=true |

## 已知边界

- **P0-1（实测修正）**：`chromadb.Client | None` 在 Python<3.14 会 import 崩溃（本机 3.14 因延迟注解不崩，Docker 3.12 复现）；已修复（`from __future__ import annotations`），镜像重建验证通过。
- **k8s**：AMBRACE 是单机自托管应用，Docker Compose 已足够，不需要 Kubernetes。

## 非 root 运行与挂载卷属主对齐（3.4）

镜像以非特权用户 `ambrace`（uid/gid 10001）运行（见 Dockerfile `USER ambrace`）。容器挂载的宿主机目录需允许该 uid 写入，否则会触发「非 root 后写不进挂载卷」的回归：

```bash
# 宿主机（项目根目录）：
chown -R 10001:10001 backend/data backend/models
```

- `backend/data`：SQLite + ChromaDB + BM25 缓存 + 日志（`setup_logging` 写 `data/logs`），必须对 uid 10001 可写。
- `backend/models`：`download_models.py` 下载 bge-m3 后写回此处，必须对 uid 10001 可写；未下载时首次启动由 entrypoint 自动拉取。
- `.env` 以只读（`:ro`）挂载；entrypoint 仅在缺失时从 `.env.example` 生成默认文件（无需写宿主机 `.env`）。
- 若用 Windows 主机的 Docker Desktop，`chown` 需在 WSL2 侧执行：`wsl -e chown -R 10001:10001 backend/data backend/models`。

## CORS_ORIGINS 建议（3.4）

默认 `CORS_ORIGINS=*` 且 `allow_credentials=False`，仅建议在可信的本地/内网环境使用。公网部署请显式收敛为具体来源：

```bash
# 仅本机 + 局域网设备（手机端填服务器局域网 IP，如 192.168.1.10:8000）
CORS_ORIGINS="http://127.0.0.1:8000,http://localhost:8000,http://192.168.1.10:8000"
# 有固定公网域名时（经 HTTPS 反代，App 实际访问来源）
CORS_ORIGINS="https://ambrace.example.com"
```

> 说明：Starlette 的 CORSMiddleware 不支持 `:*` 端口通配，请列具体来源；或保留 `*` 但仅在可信网络使用。改动后重启容器生效。

## HTTPS 反向代理（nginx 示例，3.4）

生产建议前置 HTTPS 反代，容器内仍为 HTTP:8000（镜像未内置 TLS）。

```nginx
server {
    listen 443 ssl;
    server_name ambrace.example.com;

    ssl_certificate     /etc/letsencrypt/live/ambrace.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ambrace.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket（通知长连接 /api/v1/system/notifications/ws）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        client_max_body_size 20m;
    }

    # 上传/静态资源（直挂 /uploads，可选）
    location /uploads/ {
        alias /srv/ambrace/backend/data/uploads/;
    }
}
```

Caddy 更简单（自动 HTTPS）：

```caddy
ambrace.example.com {
    reverse_proxy 127.0.0.1:8000
    encode gzip
}
```

> 反代后把 `CORS_ORIGINS` 设为 `https://ambrace.example.com`（或 App 实际访问来源），否则浏览器跨域会被拒。

## Release zip 校验（sha256，3.4）

```bash
# Linux / macOS
sha256sum ambrace-<version>.zip
# Windows PowerShell
Get-FileHash .\ambrace-<version>.zip -Algorithm SHA256
```

比对发布说明公布的 sha256 值，一致再解压部署。
