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
    # A8 方案 B（2026-09-26）：凭据主密钥同样指向会话沙箱，禁止任何测试在 backend/data/ 下
    # 生成/覆盖 secrets.key（app/utils/credential_crypto.py 首次加密时会自动建该文件）。
    os.environ.setdefault("AMBRACE_CREDENTIAL_KEY_FILE",
                          (_SESS_ROOT / "data" / "secrets.key").as_posix())
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
    # T5（2026-09-10）：**渠道/第三方插件自带的业务表**（douyin_* / wechat_ilink_*，共 7 张）改由
    # 独立 plugin_metadata 管理，主 metadata 的 create_all 不再顺带建它们——test_plugin_isolated_metadata
    # 测的就是这一层（DOUYIN/WECHAT 集合与主 metadata 不相交、且只在 plugin_metadata）。
    # ⚠️ 内核自有的 plugin_stores（插件命名空间 KV，app/models/plugin/__init__.py）**不属于**该剥离范围：
    # 它一直在主 Base.metadata 里，主 create_all 照建（init_db 另有幂等 CREATE TABLE IF NOT EXISTS）。
    # 不要把「插件表已剥离」误读成「主 metadata 不含任何 plugin_* 表」（P3-10 注释口径更正，2026-09-17）。
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


@pytest.fixture(autouse=True)
def _restore_agent_flags_after_test():
    """用例结束后把全局 AGENT_FLAGS 还原成本用例开始前的快照（2026-09-25 CI 修复护栏）。

    背景：test_flag_catalog_metadata 断言「flag_catalog ↔ AGENT_FLAGS 双向一致」。此前有 6 处用例用
    `AGENT_FLAGS[k] = x` + `finally: AGENT_FLAGS.pop(k, None)` 还原——`pop` 会把**键**整个删掉
    （不是还原成默认值），于是「谁先跑」决定该断言红不红：本地按文件顺序跑绿，CI 的 xdist 分片把
    污染用例排到前面就红（2026-09-25 第 43 棒 py3.13 档实测）。那 6 处已改成「读原值再写回」，
    这里再加一道快照护栏，让任何新增用例都不会再以「删键 / 改值不还原」的方式污染同进程后续用例。

    就地 clear + update：其他模块持有的是同一个 dict 对象，必须保身份。
    """
    from app.agent.loop import AGENT_FLAGS

    snapshot = dict(AGENT_FLAGS)
    yield
    AGENT_FLAGS.clear()
    AGENT_FLAGS.update(snapshot)


@pytest.fixture(autouse=True)
def _reset_admin_cache_between_tests():
    """每个用例前后清一次权限/门禁进程内缓存（2026-09-17 CI 修复护栏；P2 扩到账号门禁）。

    背景：permission_service.is_admin_user 读会话共享测试库的 users 表，并带 30s 进程内缓存。
    任一用例往共享库写入 id=1 的非主账号用户后，后续所有依赖主账号判定的用例
    （功能开关 / 活性明细 / 生活主页）都会拿到 is_admin=0 → 403 / 404，删该用户时还会被
    残留外键挡住。根因已按用例隔离修掉（私有 tmp_path 库），此处再加一道护栏，
    避免同类跨用例污染以「主账号判定」的形式扩散。

    账号独立 P2（2026-09-19）：server_admin 判定与账号门禁状态（disabled_at / llm_mode）
    同样是 30s 进程内缓存，跨用例残留会让「这个用例禁用某账号」泄漏到后续用例
    （表现为莫名的 403），故一并清除。
    """
    from app.application import permission_service as perm

    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()
    yield
    perm._admin_cache.clear()
    perm._server_admin_cache.clear()
    perm._account_state_cache.clear()


# ---------------------------------------------------------------------------
# CI 无 bge-m3 模型时注入确定性假向量（消除「本地有模型跑向量路径、CI 静默退化成 None」的分叉）。
# - 触发条件：模型缺失（CI 无 backend/models/bge-m3）或显式 AMBRACE_FORCE_FAKE_EMBEDDING=1（本机验证用）。
# - 假向量 1024 维（与 bge-m3 一致）、L2 归一化、按文本 sha256 确定性播种：同文本恒等、不同文本近似正交，
#   足以走通「向量写入 / Chroma 检索 / RRF / 卡片生成」链路，但【不代表真实语义相似度】——
#   断言真实语义召回质量的用例应打 slow 标记、在有模型的环境跑。
# - 用 monkeypatch：用例内若自行 patch 某模块的 text_embedding（如故障注入 _boom_embed），
#   用例级 patch 后生效、teardown 自动恢复到本 fixture 版本，互不污染。
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _deterministic_fake_embedding_when_model_missing(monkeypatch):
    import hashlib
    import sys

    from app.memory import embedding as emb

    force = os.environ.get("AMBRACE_FORCE_FAKE_EMBEDDING") == "1"
    try:
        available = emb.check_model_available()
    except Exception:
        available = False
    if available and not force:
        return  # 本机有真实 bge-m3（且未强制），不替换

    import numpy as np

    DIM = 1024

    def _fake_vec(text: str):
        seed = int(hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16], 16)
        rng = np.random.default_rng(seed)
        v = rng.standard_normal(DIM).astype("float32")
        return (v / max(float(np.linalg.norm(v)), 1e-9)).tolist()

    async def _fake_text_embedding(text: str):
        return await asyncio.to_thread(_fake_vec, text)

    # ① 在替换源头【之前】收集所有模块级早绑定的 app.* 模块（否则替换后再比对会全部失配）
    original = emb.text_embedding
    bound_modules = []
    for name, mod in list(sys.modules.items()):
        if name != "app" and not name.startswith("app."):
            continue
        if getattr(mod, "text_embedding", None) is original:
            bound_modules.append(mod)

    # ② 替换源头 + 所有模块级早绑定（embedding_cache / service / card_generator 等）
    monkeypatch.setattr(emb, "text_embedding", _fake_text_embedding)
    for mod in bound_modules:
        monkeypatch.setattr(mod, "text_embedding", _fake_text_embedding, raising=False)


# ── 早绑定工厂防泄漏（2026-09-21，修 CI 随机红）───────────────────────────────
# 背景：93 个 app.* 模块在 import 期就 `from app.db.database import async_session_factory` 早绑定。
# 若某个模块**首次**被 import 时正好落在别的用例的 monkeypatch 窗口内（该用例把工厂换成自己的临时库），
# 它就会永久绑到那个临时工厂；此后这类用例的「按 `is original` 比对再替换」不再命中它，
# 它的读/写会落到已删除的旧临时库（异常被 fail-open 吞掉），表现为随机的「刚写的行查不到」
# （2026-09-21 实证：test_user_runtime_flags::test_user_facts_read_paths_per_account 在 -n 4 下约 1/2 概率红，
# 诊断显示 flag 解析正常、工厂已补，但 GlobalUserFact 0 行）。
# 修法：在任何 fixture 跑之前，先把 app.* 全量 import 一遍——保证早绑定拿到的是会话沙箱工厂，
# 后续同类替换的 `is` 比对才成立。单个模块导入失败（可选依赖/平台差异）只告警，不阻断收集。
def _preimport_app_modules() -> tuple[int, list[str]]:
    import importlib
    import pkgutil

    import app as _app_pkg

    ok = 0
    failed: list[str] = []
    for m in pkgutil.walk_packages(_app_pkg.__path__, prefix="app."):
        try:
            importlib.import_module(m.name)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - 导入失败不应阻断测试收集
            failed.append(f"{m.name}: {type(exc).__name__}: {exc}")
    return ok, failed


_PREIMPORT_OK, _PREIMPORT_FAILED = _preimport_app_modules()
if _PREIMPORT_FAILED:
    print(f"[conftest] preimport 有 {len(_PREIMPORT_FAILED)} 个模块导入失败（不阻断）："
          + "; ".join(_PREIMPORT_FAILED[:5]))
