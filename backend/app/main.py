"""FastAPI 应用入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    emojis_router,
    character_router, chat_router, memory_router, system_router,
    scheduler_router, diary_router, moments_router, uploads_router, relationships_router,
    pets_router,
    phone_router,
    timeline_router,
    images_router,
    user_states_router,
    user_content_router,
    ai_chats_router,
    privacy_router,
    user_location_router,
    phone_desktop_router,
    plugins_router,
    plugin_bridge_router,
    marketplace_router,
    platform_profiles_router,
    chat_groups_router,
    life_router,
    life_home_router,
    voice_router,
    weave_router,
    permissions_router,
    phone_workflows_router,
)
from app.api.ai_api import router as ai_api_router
from app.auth.router import router as auth_router
from app.db.database import init_db
from app.utils.logger import setup_logging, get_logger
from app.scheduler import scheduler as scheduler_engine
from app.utils.version import get_project_version

# 单实例保护：防止多个 uvicorn 同时运行（Windows SO_REUSEADDR 下可共存，导致调度器双跑/重复消息）
import socket as _socket
import os as _os

# P2-2：单实例锁端口可配置（环境变量 INSTANCE_LOCK_PORT，默认 8766；端口冲突时可通过环境变量改端口）
INSTANCE_LOCK_PORT = int(_os.environ.get("INSTANCE_LOCK_PORT", "8766"))


def _acquire_server_lock() -> _socket.socket | None:
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        # 显式关闭 SO_REUSEADDR：Windows 上该选项允许"多个 socket 绑定同一端口"（语义与
        # Linux 不同），不加会绕过单实例锁，导致第二个 uvicorn 也能绑上锁端口、调度器双跑。
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", INSTANCE_LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        return None


_server_lock_sock = _acquire_server_lock()
if _server_lock_sock is None:
    raise RuntimeError(
        f"Another AMBRACE server instance is already running (lock port {INSTANCE_LOCK_PORT}). Exiting."
    )
import sys as _sys
_sys.stderr.write(f"[AMBRACE] app single-instance lock held on 127.0.0.1:{INSTANCE_LOCK_PORT}\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时：初始化日志、数据库、调度器
    setup_logging()
    logger = get_logger("app")
    logger.info("AMBRACE Server starting...")
    await init_db()
    logger.info("Database initialized")

    # 运行时 Feature Flag：DB 覆盖硬编码默认（2026-08-18，开关 API 热更新无需重启）
    try:
        from app.services.flag_service import load_runtime_flags
        _flag_overrides = await load_runtime_flags()
        logger.info('Runtime flags loaded: %d overrides', _flag_overrides)
    except Exception as _fe:
        logger.warning('Runtime flags load failed: %s', _fe)

    # 启动时扫描并加载插件（registry 同步 plugins 表；单个插件异常不影响启动）
    try:
        from app.plugins import registry
        await registry.sync_plugins_db()
        logger.info("Plugins loaded: %d", len(registry._loaded))
        registry.mount_plugin_routers(app)
        # Phase C：插件 action 自动登记为 ToolSpec（抖音发布/评论回复等；幂等覆盖）
        try:
            from app.agent.tools import sync_plugin_tools
            _tool_count = sync_plugin_tools()
            logger.info("Plugin tools registered: %d", _tool_count)
        except Exception as _te:
            logger.warning("Plugin tools sync failed: %s", _te)
    except Exception as e:
        logger.warning("Plugins load failed: %s", e)

    # 注册内置事件订阅者（演进规划 v2 Phase A：memory.written → 织库增量 / life.activity_completed → 朋友圈联动）
    try:
        from app.events import register_builtin_handlers
        register_builtin_handlers()
        logger.info("Event bus builtin handlers registered")
    except Exception as e:
        logger.warning("Event bus handlers register failed: %s", e)

    # 远程市场启动预拉（plans #42 Phase C）：已配置 url 且无缓存时后台拉取一次（失败静默）
    try:
        import asyncio as _aio2
        from app.api.marketplace import prefetch_remote_marketplace
        _aio2.create_task(prefetch_remote_marketplace())
        logger.info("Remote marketplace prefetch scheduled")
    except Exception as e:
        logger.warning("Marketplace prefetch schedule failed: %s", e)

    # Edge 预热（plans #39）：browser_mcp 提供 warmup 时，启动后后台预热常驻浏览器上下文（失败静默）
    try:
        import asyncio as _aio
        import sys as _sys
        _browser_mod = _sys.modules.get("ai_plugin_browser_mcp")
        if _browser_mod is not None and callable(getattr(_browser_mod, "warmup", None)):
            _aio.create_task(_browser_mod.warmup())
            logger.info("browser_mcp Edge warmup scheduled")
    except Exception as e:
        logger.warning("Edge warmup schedule failed: %s", e)

    # 启动主动交流调度器
    scheduler_engine.start()
    logger.info("Proactive scheduler started")

    yield

    # 关闭时：停止调度器、清理资源
    scheduler_engine.stop()
    logger.info("Proactive scheduler stopped")


app = FastAPI(
    title="AMBRACE Server",
    description="拥爱（AMBRACE）自托管后端服务",
    version=get_project_version(),
    lifespan=lifespan,
)

# CORS 配置（允许手机端跨域访问；P2-3：来源可用 CORS_ORIGINS 环境变量配置，逗号分隔，默认 * 全放行；allow_credentials 保持 False）
_CORS_ORIGINS = [o.strip() for o in _os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 图片上传静态目录（必须先创建目录，StaticFiles 要求存在）
import os as _os
from fastapi.staticfiles import StaticFiles
from app.config import settings as _settings
_uploads_dir = str(_settings.PROJECT_ROOT / "data" / "uploads")
_os.makedirs(_uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")

# 注册路由
app.include_router(character_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(system_router)
app.include_router(scheduler_router)
app.include_router(diary_router)
app.include_router(moments_router)
app.include_router(uploads_router)
app.include_router(auth_router)
app.include_router(relationships_router)
app.include_router(pets_router)
app.include_router(phone_router)
app.include_router(timeline_router)
app.include_router(images_router)
app.include_router(user_states_router)
app.include_router(user_content_router)
app.include_router(ai_chats_router)
app.include_router(emojis_router)
app.include_router(privacy_router)
app.include_router(user_location_router)
app.include_router(phone_desktop_router)
app.include_router(plugins_router)
app.include_router(plugin_bridge_router)
app.include_router(marketplace_router)
app.include_router(platform_profiles_router)
app.include_router(chat_groups_router)
app.include_router(life_router)
app.include_router(life_home_router)
app.include_router(voice_router)
app.include_router(weave_router)
app.include_router(permissions_router)
app.include_router(phone_workflows_router)
app.include_router(ai_api_router)


@app.middleware("http")
async def _http_access_log(request: Request, call_next):
    """轻量 HTTP 访问日志（方法/路径/状态/耗时）→ app.log，弥补 uvicorn access log 丢失"""
    import time as _t
    start = _t.perf_counter()
    try:
        resp = await call_next(request)
        get_logger("http.access").info(
            "%s %s -> %d (%.2fs)", request.method, request.url.path, resp.status_code, _t.perf_counter() - start,
        )
        return resp
    except Exception:
        get_logger("http.access").warning(
            "%s %s -> EXC (%.2fs)", request.method, request.url.path, _t.perf_counter() - start,
        )
        raise


@app.get("/")
async def root():
    get_logger("app").debug("Root endpoint called")
    return {"message": "AMBRACE Server is running"}
