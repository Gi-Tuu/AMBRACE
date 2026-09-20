"""应用配置管理"""
from pathlib import Path
from typing import Annotated
from pydantic import field_validator
from pydantic_settings import NoDecode, BaseSettings


class Settings(BaseSettings):
    # ---- DeepSeek / LLM（通用 OpenAI 兼容；LLM_* 缺省回退 deepseek_*，兼容现有 .env）----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-pro"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model_name: str = ""

    # ---- 生图（OpenAI 兼容 images API；豆包/扣子等私有协议后续 provider 扩展）----
    image_gen_provider: str = ""  # openai(images/generations) 或 dashscope(qwen-image chat 生图)
    image_gen_enabled: bool = False
    image_gen_base_url: str = ""
    image_gen_api_key: str = ""
    image_gen_model: str = ""
    image_gen_daily_limit: int = 10

    # ---- 图片理解 VLM（云端视觉 API 优先；本地为占位分支且默认关闭，图片不进 deepseek）----
    vlm_enabled: bool = False  # 本地 VLM 占位：默认关闭；开启需 .env 设 VLM_ENABLED=true 且已部署本地 VLM
    vlm_api_key: str = ""  # 云端视觉 API Key（OpenAI 兼容，如阿里云百炼 Qwen-VL）；非空时优先走云端（推荐）
    vlm_base_url: str = "http://127.0.0.1:11434"  # 本地 VLM 端点（占位，默认关闭时不可达）
    vlm_model: str = "qwen2.5vl:3b"  # 本地 VLM 模型（占位）
    vlm_timeout_sec: float = 180.0
    vlm_ollama_exe: str = ""  # 本地 Ollama 自愈重启入口（占位）；本机路径请在 .env 配 VLM_OLLAMA_EXE
    vlm_ollama_models_dir: str = ""  # 自愈重启注入 OLLAMA_MODELS（占位）（.env: VLM_OLLAMA_MODELS_DIR）
    vlm_garbage_restart_threshold: int = 2  # 本地连续垃圾输出达到该次数后重启本地 VLM（占位）

    # ---- 服务器 ----
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ---- i18n（P3-1，2026-09-18）：界面语言默认值；GameEngine 取不到本局创建者语言时回落此值 ----
    default_lang: str = "zh"  # zh / en

    # ---- 数据库 ----
    database_url: str = "sqlite+aiosqlite:///./data/sqlite/ai_companion.db"

    # ---- ChromaDB ----
    chroma_persist_dir: str = "./data/vector_store"

    # ---- 安全（2026-08-06）----
    auth_secret_key: str = ""  # JWT 签名密钥（.env: AUTH_SECRET_KEY；空=首次启动自动生成并持久化 data/auth_secret.key）
    auth_secret_file: str = "data/auth_secret.key"  # 自动生成密钥的持久化路径（相对 PROJECT_ROOT）
    admin_user_ids: Annotated[list[int], NoDecode] = [1]  # 服务器级配置管理账号（.env: ADMIN_USER_IDS，支持 "1,2" 逗号分隔或 "[1,2]" JSON 数组）

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                # JSON 数组格式（如 [1,3]）
                import json
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [int(x) for x in parsed]
                except Exception:
                    pass
            # 逗号分隔格式（如 1,3）
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, int):
            # pydantic-settings 2.x 对 env 值按字段类型 JSON 解析：ADMIN_USER_IDS=1 会得到 int
            return [v]
        return v

    # ---- 租户口径（账号独立 P1，2026-09-19）----
    # family（默认）= 归属键取家庭根账号（跨家庭隔离 / 家庭内共享）；
    # user = 每账号彻底独立（租户键=账号自身）。统一出口 app/application/tenant_service.py，
    # 所有用户维度资源的归属判断走同一 helper，切口径只改该模块读取处。
    tenant_key_mode: str = "family"

    # /uploads 静态目录鉴权严格模式（账号独立 P1，2026-09-19）。
    # False（默认）= 兼容：无身份的裸 URL 请求放行（App 现状是 Image.network 裸 URL 取图，
    #   置 True 会让所有图片/语音 404）；带身份（Authorization / ?token=）的请求始终按租户比对。
    # True = 严格：租户归属路径的匿名请求一律 404，共享资源（pets_assets/emojis/market/tts）仍放行。
    uploads_require_auth: bool = False

    # ---- 主动交流调度器 ----
    scheduler_idle_interval: int = 300  # 闲置检查间隔（秒）
    scheduler_birthday_interval: int = 600  # 生日检查间隔
    scheduler_holiday_interval: int = 600  # 节日检查间隔
    scheduler_active_hour_start: int = 8  # 活跃时段开始
    scheduler_active_hour_end: int = 23  # 活跃时段结束

    # ---- 应用时区偏移（B1，2026-09-06）：默认 +8 = 北京时间 ----
    # 只影响「用户可感知窗口」（主动消息时段/日记/反思触发等运行时判断）；
    # 库内存储口径保持 UTC-naive 不变（零数据迁移），见 app/utils/timeutil.py 说明。
    app_tz_offset_hours: int = 8  # .env: APP_TZ_OFFSET_HOURS（如海外 VPS 设该值时区）

    # ---- 插件 hook 超时门禁（2026-08-16 Phase A）：默认 10s，1-60s 可配置 ----
    plugin_hook_timeout: float = 10.0

    # ---- 48b 角色开放成 API（/api/v1/ai/chat）----
    plugin_ai_rate_per_min: int = 20  # 每用户每分钟对话次数上限（进程内滑动窗口）
    plugin_ai_rate_per_day: int = 500  # 每用户每天对话次数上限（北京时间日期键）
    plugin_ai_max_tokens: int = 2000  # 单次回复 max_tokens 硬顶
    plugin_ai_require_byok: bool = False  # True 时要求用户配置 BYOK，否则 400「未配置 AI 服务」

    # ---- 48a 插件桥（/api/v1/plugins/{name}/bridge）----
    plugin_bridge_ai_rate_per_min: int = 10  # 桥 ai 每用户每插件每分钟次数上限（进程内滑动窗口）
    plugin_bridge_ai_rate_per_day: int = 200  # 桥 ai 每用户每插件每天次数上限（北京时间日期键）
    plugin_http_timeout: float = 10.0  # 桥 http 代理超时（秒）
    plugin_http_max_bytes: int = 2 * 1024 * 1024  # 桥 http 代理响应大小上限（2MB）
    plugin_http_allow_private: bool = False  # True 显式放行私有/环回/链路本地/云元数据地址（SSRF 例外）
    plugin_http_allow_http: bool = False  # 调试开关：True 放行 http 协议（默认仅 https）

    # ---- 3.9 插件安全闸（2026-09-02）：默认关闭远程市场安装 ----
    # 默认仅允许本地/示例已审核插件；远程市场安装需显式开启（.env: PLUGIN_ALLOW_REMOTE_INSTALL=true）。
    # 该开关只拦截「远程市场」安装/升级；本地 zip 导入与内置示例安装不受影响（后者仍走权限同意流程）。
    plugin_allow_remote_install: bool = False

    # ---- MCP（Model Context Protocol，Phase 1-2，2026-08-26）----
    mcp_connect_timeout: float = 10.0  # MCP Server 连接/初始化/发现超时（秒）
    mcp_call_timeout: int = 30  # 单次 MCP 工具调用超时（秒）
    mcp_reconnect_max: int = 3  # 连接失败最大重试次数（指数退避 1s/2s/4s）
    mcp_http_allow_private: bool = False  # [全局兜底] True 时【全局】放行 MCP 任意内网/本地地址（SSRF 例外，向后兼容）
    # P3-4（2026-09-19）本地回环细粒度放行：本字段保持默认 False 不变；单个 MCP Server 可在其自身配置里用
    # allow_loopback=True 显式标记（列 mcp_servers.allow_loopback，经 /api/v1/mcp/servers 增改/回显）。
    # 两者关系与推荐用法：
    # - mcp_http_allow_private=True 仍是【进程级全局】放行（连 192.168/10./172.16-31/169.254 云元数据服务
    #   也会一起放开），只适合完全自托管/已隔离的可信内网部署，不应为了「本地游戏 MCP（Sims4/Minecraft
    #   的 127.0.0.1）」而打开；
    # - 常规做法：保持本开关 False，只给确需本地回环的单个 Server 置 allow_loopback=True —— 放行范围严格
    #   限于该 Server，且要求 URL 解析结果【全部】是 loopback（127.0.0.0/8 / ::1 / localhost）；其余私网/
    #   链路本地/云元数据地址即便标了 allow_loopback 也照旧拒绝（见 app/mcp/transport.py _resolve_mcp_ip）。

    # ---- FCM 离线推送（2026-08-28）----
    push_fcm_enabled: bool = False  # .env: PUSH_FCM_ENABLED=true 启用 FCM 离线推送
    push_fcm_credentials_path: str = ""  # Firebase 服务账号 JSON 路径（.env: PUSH_FCM_CREDENTIALS_PATH）
    push_fcm_project_id: str = ""  # 仅日志/校验用；实际项目 ID 以服务账号 JSON 为准（.env: PUSH_FCM_PROJECT_ID）
    # 客户端 Firebase 配置（JSON 字符串，来自 Firebase 控制台项目设置→"您的应用"→SDK 设置）
    # 包含 apiKey/appId/messagingSenderId/projectId/storageBucket；.env: PUSH_FCM_CLIENT_CONFIG
    push_fcm_client_config: str = ""

    # ---- 语音流式 ASR（Phase 1，可选）：未配置/未启用/协议未确认时回退本地 whisper ----
    # 百炼实时语音识别（Paraformer 流式）WS 协议与配置字段【待实测确认】；
    # 在协议实测前即使启用也不激活（见 app/voice/asr_provider.py）。
    asr_stream_provider: str = ""  # "" 或 "dashscope_stream"
    asr_stream_enabled: bool = False  # 启用流式 ASR provider
    asr_stream_base_url: str = ""  # 百炼实时识别 WS 端点（协议待实测，勿盲填）
    asr_stream_api_key: str = ""  # 鉴权 key（协议待实测，勿盲填）
    asr_stream_model: str = ""  # paraformer 流式模型名（协议待实测，勿盲填）

    # ---- 表情市场（2026-08-23）：远程表情市场索引 URL（GitHub raw 索引模式）----
    emoji_market_url: str = "https://raw.githubusercontent.com/Gi-Tuu/AMBRACE-emoji/main/index.json"

    # ---- 插件市场（3.13，2026-09-02/03；默认 URL 内置 2026-09-04）：远程插件市场索引 URL（GitHub raw 索引模式）----
    # 默认内置官方仓库 raw 索引（对齐 emoji_market_url）：列表端默认即可展示官方上架插件；
    # 拉取带 1h 内存缓存 + 10s 超时 + 失败降级本地内置（见 api/marketplace.py get_remote_index）。
    # 如需自建市场，用 .env: PLUGIN_MARKET_URL=<你的 index.json raw 地址> 覆盖；
    # 如需回到「仅内置 + 手动刷新」旧行为，设 PLUGIN_MARKET_URL=（空串）。
    # 注意：内置 URL 只影响列表展示，远程安装仍受 plugin_allow_remote_install=False 的安全闸拦截（默认关，须显式开启）。
    plugin_market_url: str = "https://raw.githubusercontent.com/Gi-Tuu/AMBRACE-plugin/main/index.json"

    # ---- 项目根目录 ----
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

    model_config = {"env_file": str(PROJECT_ROOT.parent / ".env"), "extra": "allow"}


settings = Settings()
# 将 SQLite 数据库 URL 解析为绝对路径（避免依赖进程工作目录，P1-4）
_db_url = settings.database_url
if _db_url.startswith("sqlite+aiosqlite:///"):
    _db_path = _db_url[len("sqlite+aiosqlite:///"):]
    if _db_path and not Path(_db_path).is_absolute():
        settings.database_url = "sqlite+aiosqlite:///" + str(
            (settings.PROJECT_ROOT / _db_path).resolve()
        )
# 将 ChromaDB 持久化目录解析为绝对路径（避免依赖进程工作目录）
settings.chroma_persist_dir = str(
    (settings.PROJECT_ROOT / settings.chroma_persist_dir).resolve()
    if not Path(settings.chroma_persist_dir).is_absolute()
    else settings.chroma_persist_dir
)
