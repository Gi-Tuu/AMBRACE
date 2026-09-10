# -*- coding: utf-8 -*-
# ─────────────────────────────────────────────────────────────────────────────
# 测试沙箱引导（必须在任何 import app.* 之前执行）
# 原因：app.config.settings / app.db.engine / vector_store 都是「import 时单例」，
#       fixture/hook 阶段再改环境变量已太晚（引擎已绑定生产库）。
# 策略：在进程最早期把 DATABASE_URL / CHROMA_PERSIST_DIR / AUTH_SECRET_KEY 指向
#       会话级临时【文件库】。不能用 :memory:：NullPool 下每连接是独立内存库会丢表，
#       且 engine.py 对 :memory: 不注册 WAL / foreign_keys=ON listener，测不到真实级联。
# ─────────────────────────────────────────────────────────────────────────────
import os
import gc
import shutil
import tempfile
import time
from pathlib import Path

# 逃生舱：AMBRACE_TEST_KEEP_PROD=1 时不劫持（仅本机刻意对真实库调试用，CI/日常默认隔离）
if os.environ.get("AMBRACE_TEST_KEEP_PROD") != "1":
    # 2026-09-10 pytest 测试卫生纪律的显式例外：会话级一次性目录，会话结束 rmtree + 兜底清理。
    # tempfile.mkdtemp 属模块级引导，早于任何 fixture，无法用 tmp_path；本目录生命周期在
    # 下方 _init_test_schema 会话 teardown 与 pytest_sessionfinish 兜底中闭环（防止 %TEMP% 积累）。
    _SESS_ROOT = Path(tempfile.mkdtemp(prefix="ambrace_pytest_"))
    _DB_DIR = _SESS_ROOT / "data" / "sqlite"
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    _DB_FILE = (_DB_DIR / "test.db").as_posix()                 # 正斜杠绝对路径 C:/.../test.db
    (_SESS_ROOT / "vector_store").mkdir(parents=True, exist_ok=True)
    _CHROMA_DIR = (_SESS_ROOT / "vector_store").as_posix()

    # 允许 CI 显式指定库路径；否则用会话临时库
    os.environ["DATABASE_URL"] = os.environ.get("AMBRACE_TEST_DB_URL",
                                                f"sqlite+aiosqlite:///{_DB_FILE}")
    os.environ["CHROMA_PERSIST_DIR"] = _CHROMA_DIR
    # 固定测试 JWT 密钥：auth/config.py 优先读 AUTH_SECRET_KEY，彻底不读/不写 data/auth_secret.key
    os.environ.setdefault("AUTH_SECRET_KEY",
                          "pytest-only-secret-0123456789abcdef0123456789abcdef")
else:
    _SESS_ROOT = None

# ── 以下才是原有 import（pytest / app.*）──
import asyncio

import pytest

import app.memory.bm25_index as bm25
from app.events.bus import event_bus


def _rmtree_retry(path: Path, attempts: int = 12, delay: float = 0.5) -> None:
    """Windows 下 aiosqlite/chroma 句柄可能稍有延迟才被真正释放，rmtree 首次常因句柄占用失败；
    这里带重试（幂等），最终仍以 ignore_errors 兜底，确保不向 pytest 抛错、也不留下积累源。
    先前置 gc.collect() 触发孤儿 aiosqlite 连接对象的析构（其 worker 线程持有 socket 句柄）。"""
    gc.collect()
    for _ in range(attempts):
        try:
            if path.exists():
                shutil.rmtree(path, ignore_errors=False)
            return
        except Exception:
            time.sleep(delay)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def pytest_configure(config):
    """注册自定义标记，避免「需要真实微信扫码联调」的 live 用例产生未注册标记告警。"""
    config.addinivalue_line("markers", "live: 需要真实微信扫码/真机联调，默认不运行（PR3 阻塞项）")


def pytest_sessionfinish(session, exitstatus):
    """会话结束兜底清理（幂等）：pytest 中途异常退出（KeyboardInterrupt/SIGKILL/未走 session
    teardown）时，_init_test_schema 的 rmtree 可能没跑，导致 %TEMP% 遗留本会话沙箱。
    此处先尽力释放引擎 / Chroma 句柄（Windows 下句柄占用会导致 rmtree 静默失败），
    再删除【本会话自己的】沙箱目录（_SESS_ROOT）。

    纪律（2026-09-10）：只允许清理本会话目录，禁止 glob 删除全部 ambrace_pytest_*——
    多个 pytest 会话并发时会互删沙箱（表现为大量 "unable to open database file"）。
    历史遗留目录由维护者手工清理，不在测试收尾里"顺手"做。
    """
    if os.environ.get("AMBRACE_TEST_KEEP_PROD") == "1":
        return
    try:
        from app.db import vector_store as _vs
        if getattr(_vs, "_client", None) is not None:
            _vs._client.close()
            _vs._client = None
    except Exception:
        pass
    try:
        from app.db.engine import engine
        asyncio.run(engine.dispose())
    except Exception:
        pass
    if _SESS_ROOT is not None:
        _rmtree_retry(_SESS_ROOT)


@pytest.fixture(scope="session", autouse=True)
def _init_test_schema():
    """会话开始时给全局库建表（幂等，兼容干净环境/CI 缺失生产库）。"""
    from sqlalchemy import text
    from app.db.database import init_db, engine
    from app.db.migrate import ensure_alembic_revision

    # 顶部引导已把 DATABASE_URL 指向会话沙箱库，init_db + 对齐 alembic 均作用其上
    asyncio.run(init_db())
    asyncio.run(ensure_alembic_revision())
    # T5（2026-09-10）：插件表改由独立 plugin_metadata 管理，主 create_all 不再顺带建；
    # 会话库统一兜底一次（已加载到的渠道插件幂等建表；各测试 load_plugin_dir 内也会内联建，
    # 此处为「未显式加载插件却用到插件表」路径的保险）。
    from app.plugins import registry as _plugin_registry
    asyncio.run(_plugin_registry.ensure_plugin_tables())

    # 防回归断言：当前引擎必须连到会话临时库，绝不允许悄悄连回生产库（问题 A 硬保证）
    if _SESS_ROOT is not None:
        async def _assert_db_path():
            async with engine.connect() as conn:
                rows = (await conn.execute(text("PRAGMA database_list"))).fetchall()
            path = rows[0][2]
            assert str(_SESS_ROOT.as_posix()) in path.replace("\\", "/"), \
                f"测试连接到了非沙箱库: {path}（沙箱={_SESS_ROOT}），请检查 env 注入时序"
        asyncio.run(_assert_db_path())

    yield

    # 结束：先释放引擎句柄（Windows 上 -wal/-shm 占用会导致 rmtree 失败），再删沙箱。
    # Chroma 单例客户端同样持有沙箱 vector_store 文件句柄（chroma.sqlite3 / data_level0.bin），
    # 会话末必须 close()，否则 rmtree(ignore_errors=True) 静默失败 → %TEMP% 残留。
    async def _shutdown():
        try:
            await engine.dispose()
        except Exception:
            pass
    asyncio.run(_shutdown())
    try:
        from app.db import vector_store as _vs
        if getattr(_vs, "_client", None) is not None:
            _vs._client.close()
            _vs._client = None
    except Exception:
        pass
    if _SESS_ROOT is not None:
        _rmtree_retry(_SESS_ROOT)


@pytest.fixture(autouse=True)
def _isolate_bm25_persist(tmp_path):
    """每个测试把 BM25 持久化根重定向到临时目录，测试间互不污染、不写生产缓存。"""
    bm25._persist_root = Path(tmp_path)
    yield
    bm25._persist_root = None


@pytest.fixture(autouse=True)
def _register_builtin_tools():
    """每个测试把 4 个内置工具注册到 ToolRegistry（幂等可重入）。

    AMBRACE 步骤 8：内置工具执行入口迁到 app/tools/builtin，由 register_builtin_tools() 显式注册。
    """
    from app.tools import register_builtin_tools

    register_builtin_tools()
    yield


@pytest.fixture(autouse=True)
def _reset_event_bus_after_test():
    """用例结束后清空全局事件总线订阅者，防止内置订阅者残留污染后续用例。"""
    yield
    event_bus._subscribers = {}
