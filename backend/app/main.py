"""FastAPI 应用入口"""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    emojis_router,
    character_router, chat_router, memory_router, system_router,
    admin_router,
    scheduler_router, proactive_router, diary_router, moments_router, uploads_router, relationships_router,
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
    mcp_router,
    games_router,
    llm_configs_router,
    account_router,
    device_router,
)
from app.api.ai_api import router as ai_api_router
from app.auth.router import router as auth_router
from app.db.database import init_db
from app.db.migrate import ensure_alembic_revision, is_migration_available
from app.utils.logger import setup_logging, get_logger
from app.utils.errors import register_exception_handlers
from app.utils import readiness
from app.scheduling import scheduler as scheduler_engine
from app.utils.version import get_project_version

# 单实例保护：防止多个 uvicorn 同时运行（Windows SO_REUSEADDR 下可共存，导致调度器双跑/重复消息）
import socket as _socket
import os as _os
import asyncio  # lifespan 内后台预热/重连任务与关闭时取消使用

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

    # 启动期组件就绪登记（AMBRACE 3.5）：每次启动先清空旧登记，只反映本次启动。
    # 只治理「启动期组件」可见性，绝不扩大到业务链路——陪伴主回复链路异步任务仍保持
    # 「失败静默、不阻塞回复」铁律（见 docs/dev-changelog 2026-08-16 定调）。
    readiness.reset()
    # ── 启动步骤 1（可选路径）：渠道插件预加载（X5，须在 init_db 前）──────────────
    # 渠道自带 ORM 模型在加载期 import 注册进 Base.metadata，保证 create_all 建表齐全。
    # 分级：可选——失败仅降级登记（/ready 可见），不阻断启动（渠道插件缺失不影响核心）。
    try:
        from app.plugins.registry import preload_channels
        _pre_channels = preload_channels()
        readiness.mark("channel_plugins", _pre_channels is not None)
        if _pre_channels:
            logger.info("Channel plugins preloaded: %d", _pre_channels)
    except Exception as _che:
        readiness.mark("channel_plugins", False, msg=str(_che))
        logger.warning("Channel plugins preload failed: %s", _che)

    # ── 启动步骤 2（关键路径，fail-fast）：数据库初始化 init_db ──────────────────
    # 分级：关键 → fail-fast（直接抛出，令进程启动失败，由 watchdog/管理器拉起并暴露；
    # 不 mark 503——进程已起不来，/ready 无法返回）。数据库是根依赖，不可用则全站不可用。
    await init_db()
    readiness.mark("database", True, critical=True)
    logger.info("Database initialized")

    # ── 启动步骤 3（关键路径，mark critical）：Alembic 版本链对齐 ────────────────
    # 存量库 stamp head；落后于 head 时 upgrade；已在 head 则 no-op。
    # 分级：关键 → mark critical。迁移对齐保证 schema 正确性；失败不硬崩（保留 init_db
    # 幂等建表兜底），但标记 critical 使 /ready 返回 503，避免「启动成功但 schema 静默错位」。
    if is_migration_available():
        try:
            _mig = await ensure_alembic_revision()
            readiness.mark("alembic", True, critical=True)
            logger.info("Alembic revision aligned: %s", _mig)
        except Exception as _mie:
            readiness.mark("alembic", False, critical=True, msg=str(_mie))
            logger.warning("Alembic revision align failed: %s", _mie)
    else:
        readiness.mark("alembic", True, critical=True, msg="alembic not present")
        logger.info("Alembic not present; skip database migration versioning")

    # ── 启动步骤 4（可选路径）：运行时 Feature Flag ───────────────────────────────
    # DB 覆盖硬编码默认（2026-08-18，开关 API 热更新无需重启）。失败仅降级登记。
    try:
        from app.application.flag_service import load_runtime_flags
        _flag_overrides = await load_runtime_flags()
        readiness.mark("runtime_flags", True)
        logger.info('Runtime flags loaded: %d overrides', _flag_overrides)
    except Exception as _fe:
        readiness.mark("runtime_flags", False, msg=str(_fe))
        logger.warning('Runtime flags load failed: %s', _fe)

    # ── 启动步骤 5（可选路径）：插件扫描加载 + 挂载 + 工具登记 ────────────────────
    # registry 同步 plugins 表；单个插件异常不影响启动。失败仅降级登记（/ready 可见）。
    try:
        from app.plugins import registry
        await registry.sync_plugins_db()
        readiness.mark("plugins", True)
        logger.info("Plugins loaded: %d", len(registry._loaded))
        registry.mount_plugin_routers(app)
        # Phase C：插件 action 自动登记为 ToolSpec（渠道发布/评论回复等；幂等覆盖）
        try:
            from app.agent.tools import sync_plugin_tools
            _tool_count = sync_plugin_tools()
            logger.info("Plugin tools registered: %d", _tool_count)
        except Exception as _te:
            logger.warning("Plugin tools sync failed: %s", _te)
    except Exception as e:
        readiness.mark("plugins", False, msg=str(e))
        logger.warning("Plugins load failed: %s", e)

    # ── 启动步骤 6（可选路径）：内置工具注册 ──────────────────────────────────────
    # AMBRACE 重构步骤 8：把 4 个内置工具执行入口登记到 ToolRegistry（幂等可重入）。
    try:
        from app.tools import register_builtin_tools
        from app.agent.tools import list_tools as _list_tools
        _registered = register_builtin_tools()
        readiness.mark("builtin_tools", True)
        logger.info("Builtin tools registered: %d (total %d)", _registered, len(_list_tools()))
    except Exception as _bte:
        readiness.mark("builtin_tools", False, msg=str(_bte))
        logger.warning("Builtin tools register failed: %s", _bte)

    # ── 启动步骤 7（可选路径）：内置事件订阅者注册 ────────────────────────────────
    # 演进规划 v2 Phase A：memory.written → 织库增量 / life.activity_completed → 朋友圈联动。
    try:
        from app.events import register_builtin_handlers
        register_builtin_handlers()
        readiness.mark("event_handlers", True)
        logger.info("Event bus builtin handlers registered")
    except Exception as e:
        readiness.mark("event_handlers", False, msg=str(e))
        logger.warning("Event bus handlers register failed: %s", e)

    # ── 启动步骤 8（可选路径）：远程市场启动预拉（后台任务，失败静默）───────────────
    # plans #42 Phase C：已配置 url 且无缓存时后台拉取一次。
    try:
        from app.api.marketplace import prefetch_remote_marketplace
        asyncio.create_task(prefetch_remote_marketplace())
        readiness.mark("marketplace_prefetch", True)
        logger.info("Remote marketplace prefetch scheduled")
    except Exception as e:
        readiness.mark("marketplace_prefetch", False, msg=str(e))
        logger.warning("Marketplace prefetch schedule failed: %s", e)

    # ── 启动步骤 9（可选路径）：browser_mcp Edge 预热（后台任务，失败静默）─────────
    # plans #39：browser_mcp 提供 warmup 时，启动后后台预热常驻浏览器上下文。
    try:
        _browser_mod = _sys.modules.get("ai_plugin_browser_mcp")
        if _browser_mod is not None and callable(getattr(_browser_mod, "warmup", None)):
            asyncio.create_task(_browser_mod.warmup())
            readiness.mark("edge_warmup", True)
            logger.info("browser_mcp Edge warmup scheduled")
        else:
            # 未加载 browser 插件属正常（可选组件），记就绪但不 critical。
            readiness.mark("edge_warmup", True, msg="browser_mcp not loaded")
    except Exception as e:
        readiness.mark("edge_warmup", False, msg=str(e))
        logger.warning("Edge warmup schedule failed: %s", e)

    # ── 启动步骤 10（可选路径）：bge-m3 向量模型预热（后台任务，失败静默）──────────
    # 记忆检索/写入首条延迟：后台线程加载，模型缺失/失败静默。
    try:
        from app.memory.embedding import warmup_embedding
        asyncio.create_task(warmup_embedding())
        readiness.mark("embedding_warmup", True)
        logger.info("bge-m3 embedding warmup scheduled")
    except Exception as e:
        readiness.mark("embedding_warmup", False, msg=str(e))
        logger.warning("Embedding warmup schedule failed: %s", e)

    # ── 启动步骤 11（关键路径，fail-fast）：启动主动交流调度器 ──────────────────
    # 分级：关键 → fail-fast。主动交流引擎是核心交付能力，启动失败即进程失败（原实现
    # 无 try/except 即为 fail-fast，保持）；成功 mark critical 供 /ready 反映。
    scheduler_engine.start()
    readiness.mark("scheduler", True, critical=True)
    logger.info("Proactive scheduler started")

    # ── 启动步骤 12（可选路径）：MCP 接入（后台重连）─────────────────────────────
    # 启动后台重连 auto_connect=True 的 MCP Server（失败不阻塞启动，仅降级登记）；关闭时清理。
    _mcp_task = None
    try:
        from app.mcp.manager import mcp_manager, preset_defaults
        # 部署级预置（可选）：读取 backend/data/mcp_servers.json，按 user_id=1 预置配置（存在同名跳过）
        try:
            _preset = await preset_defaults()
            if _preset:
                logger.info("MCP preset seeded: %d servers", _preset)
        except Exception as _pe:
            logger.warning("MCP preset failed: %s", _pe)
        _mcp_task = asyncio.create_task(mcp_manager.reconnect_all())
        readiness.mark("mcp", True)
        logger.info("MCP reconnect_all scheduled")
    except Exception as e:
        readiness.mark("mcp", False, msg=str(e))
        logger.warning("MCP reconnect schedule failed: %s", e)

    # 至此 12 个启动步骤均已登记（关键路径失败已 fail-fast，走不到这里）；overall 为
    # 信息位，供探针确认「启动流程完整走完」。
    readiness.mark("overall", True, msg="startup steps complete")

    yield

    # 关闭时：停止调度器、清理资源
    scheduler_engine.stop()
    logger.info("Proactive scheduler stopped")

    # MCP 关闭：取消后台重连任务 + 断开所有连接（清理 stdio 子进程）
    if _mcp_task is not None and not _mcp_task.done():
        _mcp_task.cancel()
        try:
            await _mcp_task
        except (asyncio.CancelledError, Exception):
            pass
    try:
        from app.mcp.manager import mcp_manager
        await mcp_manager.shutdown()
        logger.info("MCP manager shutdown")
    except Exception as e:
        logger.warning("MCP shutdown failed: %s", e)


app = FastAPI(
    title="AMBRACE Server",
    description="拥爱（AMBRACE）自托管后端服务",
    version=get_project_version(),
    lifespan=lifespan,
)

# 全局异常处理（3.3）：统一错误体 {ok,error{code,message,detail}} + 堆栈只进日志，不向客户端泄漏；
# detail 同时保留在顶层兼容位（旧前端/测试仍可读 detail 字段），不破坏 401/403/404/422 语义。
register_exception_handlers(app)

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
from fastapi.staticfiles import StaticFiles
from app.config import settings as _settings
_uploads_dir = str(_settings.PROJECT_ROOT / "data" / "uploads")
_os.makedirs(_uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")

# 注册路由（单一清单循环注册；顺序即路由匹配优先级，调整前须确认无前缀遮蔽）
ROUTERS = [
    character_router, chat_router, memory_router, system_router, admin_router,
    scheduler_router, proactive_router, diary_router, moments_router, uploads_router,
    auth_router, relationships_router, pets_router, phone_router, timeline_router,
    images_router, user_states_router, user_content_router, ai_chats_router, emojis_router,
    privacy_router, user_location_router, phone_desktop_router, plugins_router,
    plugin_bridge_router, marketplace_router, platform_profiles_router, chat_groups_router,
    life_router, life_home_router, voice_router, weave_router, permissions_router,
    phone_workflows_router, ai_api_router, mcp_router, games_router, llm_configs_router,
    account_router, device_router,
]
for _r in ROUTERS:
    app.include_router(_r)


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
