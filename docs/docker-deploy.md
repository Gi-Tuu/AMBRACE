# Docker 部署（公开脱敏版）

> **Public copy — 内部信息已移除，仅供公开仓 docs/**
>
> 生成日期：2026-09-02
>
> 脱敏规则：删除/改写所有内部信息——绝对路径、内部目录名、仓库内部文档引用、个人名/联系方式/服务器地址、
> 真实 .env 值、内部 URL/密钥/端口细节、未公开功能细节。仅保留通用部署步骤（环境变量用占位符）。

## 部署方式

前置：本机安装 Docker Desktop（Windows）并启动（需要 WSL2，首次安装需启用功能并重启）；或使用 Linux/macOS 的 Docker。

```bash
# 在项目根目录
docker compose build   # 构建应用镜像（拉取基础镜像 + 依赖，首次较慢）
docker compose up -d   # 启动（挂载模型目录/数据目录/.env，端口见 compose）
```

验证：

```bash
curl http://<host>:<app_port>/api/v1/system/health   # {"status":"ok"}
curl http://<host>:<app_port>/api/v1/system/ready    # {"status":"ok","db":true,"model":true}
```

## 关键点

- **模型**：嵌入模型通过 volume 挂载，不在镜像内；未下载则 `/ready` 的 model=false，聊天时返回明确报错。
- **配置**：项目根 `.env`（含模型 API Key 等）只读挂载到容器；也支持 `docker run -e <MODEL_API_KEY>=...` 注入
  （不落盘，容器删除即消失）。
- **数据**：数据目录（SQLite + 向量库 + 检索缓存）挂载持久化，升级/重建容器不丢数据。
- **健康检查**：compose healthcheck 走 `/api/v1/system/ready`。
- **单实例锁**：容器内单实例锁端口默认值可通过环境变量覆盖；CORS 默认放开、可通过环境变量收敛。

## 非 root 运行与挂载卷属主对齐

镜像以非特权用户运行（见 Dockerfile `USER <uid>`）。容器挂载的宿主机目录需允许该 uid 写入，否则会触发
「非 root 后写不进挂载卷」的回归：

```bash
# 宿主机（项目根目录）：
chown -R <uid>:<gid> <data_dir> <model_dir>
```

- 数据目录：SQLite + 向量库 + 检索缓存 + 日志，必须对该 uid 可写。
- 模型目录：下载脚本把嵌入模型写回此处，必须对该 uid 可写；未下载时首次启动由入口脚本自动拉取。
- `.env` 以只读（`:ro`）挂载；入口脚本仅在缺失时从示例文件生成默认文件（无需写宿主机 `.env`）。
- 若用 Windows 主机的 Docker Desktop，`chown` 需在 WSL2 侧执行。

## CORS 建议

默认 `CORS_ORIGINS=*` 且 `allow_credentials=False`，仅建议在可信的本地/内网环境使用。公网部署请显式收敛为具体来源：

```bash
# 仅本机 + 局域网设备（手机端填服务器局域网 IP，如 <host>:<app_port>）
CORS_ORIGINS="http://127.0.0.1:<app_port>,http://localhost:<app_port>,http://<lan_ip>:<app_port>"
# 有固定公网域名时（经 HTTPS 反代，客户端实际访问来源）
CORS_ORIGINS="https://<your_domain>"
```

> 说明：CORS 中间件不支持 `:*` 端口通配，请列具体来源；或保留 `*` 但仅在可信网络使用。改动后重启容器生效。

## HTTPS 反向代理（nginx 示例）

生产建议前置 HTTPS 反代，容器内仍为 HTTP:<app_port>（镜像未内置 TLS）。

```nginx
server {
    listen 443 ssl;
    server_name <your_domain>;

    ssl_certificate     /etc/letsencrypt/live/<your_domain>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<your_domain>/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:<app_port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket（通知长连接）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        client_max_body_size 20m;
    }

    # 上传/静态资源（直挂 /uploads，可选）
    location /uploads/ {
        alias <data_dir>/uploads/;
    }
}
```

Caddy 更简单（自动 HTTPS）：

```caddy
<your_domain> {
    reverse_proxy 127.0.0.1:<app_port>
    encode gzip
}
```

> 反代后把 `CORS_ORIGINS` 设为 `https://<your_domain>`（或客户端实际访问来源），否则浏览器跨域会被拒。

## Release zip 校验（sha256）

```bash
# Linux / macOS
sha256sum <release>.zip
# Windows PowerShell
Get-FileHash .\<release>.zip -Algorithm SHA256
```

比对发布说明公布的 sha256 值，一致再解压部署。

> 本文不披露具体镜像仓库、版本号、端口、域名与密钥值，一律用 `<...>` 占位符；实际部署按你的环境填入。
