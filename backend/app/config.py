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

    # ---- 主动交流调度器 ----
    scheduler_idle_interval: int = 300  # 闲置检查间隔（秒）
    scheduler_birthday_interval: int = 600  # 生日检查间隔
    scheduler_holiday_interval: int = 600  # 节日检查间隔
    scheduler_active_hour_start: int = 8  # 活跃时段开始
    scheduler_active_hour_end: int = 23  # 活跃时段结束

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

    # ---- MCP（Model Context Protocol，Phase 1-2，2026-08-26）----
    mcp_connect_timeout: float = 10.0  # MCP Server 连接/初始化/发现超时（秒）
    mcp_call_timeout: int = 30  # 单次 MCP 工具调用超时（秒）
    mcp_reconnect_max: int = 3  # 连接失败最大重试次数（指数退避 1s/2s/4s）
    mcp_http_allow_private: bool = False  # True 显式放行 MCP 内网/本地地址（SSRF 例外）

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
