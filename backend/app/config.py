"""应用配置管理"""
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings


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

    # ---- 本地图片理解 VLM（Ollama，图像描述用，图片不进 deepseek）----
    vlm_enabled: bool = False  # 默认关闭本地 VLM；开启需在 .env 设 VLM_ENABLED=true
    vlm_api_key: str = ""  # 云端视觉 API Key（OpenAI 兼容，如阿里云百炼 Qwen-VL）；非空时优先走云端
    vlm_base_url: str = "http://127.0.0.1:11434"
    vlm_model: str = "qwen2.5vl:3b"
    vlm_timeout_sec: float = 180.0
    vlm_ollama_exe: str = ""  # Ollama 自愈重启入口（空=不启用；本机路径请在 .env 配 VLM_OLLAMA_EXE）
    vlm_ollama_models_dir: str = ""  # 自愈重启时注入 OLLAMA_MODELS（.env: VLM_OLLAMA_MODELS_DIR）
    vlm_garbage_restart_threshold: int = 2  # 连续垃圾输出达到该次数后重启 Ollama

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
    admin_user_ids: list[int] = [1]  # 服务器级配置管理账号（.env: ADMIN_USER_IDS 逗号分隔，如 "1,2"）

    @field_validator("admin_user_ids", mode="before")
    @classmethod
    def _split_admin_ids(cls, v):
        if isinstance(v, str):
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
