# -*- coding: utf-8 -*-
"""CI/测试提速第 2 批（试点）：会话级 SQLite 模板库 + 页级克隆 helper。

用途
----
后端全量 pytest 里，每个「临时文件库」用例的 fixture 都要
``Base.metadata.create_all`` 现建 112 张表落盘（Windows 文件系统 + 112 表/索引
≈ 1.6 s/例），而用例逻辑本身只要几十毫秒。本 helper 把「建表」一次化（每进程一份
模板库），每例只做一次 SQLite 在线 backup（页级拷贝，实测 ≈ 15 ms/例）。

为什么不直接用 ``:memory:``
---------------------------
1. 本 helper 返回的 engine 用 ``NullPool``（与生产一致）——NullPool 下**每条连接都是
   独立的内存库**，建在 ``:memory:`` 上的表在下一条连接里就不见了（丢表）；
2. ``app/db/engine.py`` 的 PRAGMA listener 明确跳过 ``:memory:``（见其 G1 段注释），
   所以 ``:memory:`` 库测不到 ``foreign_keys=ON`` / WAL 的真实级联语义。
   故一律用**真实文件库**（克隆目标路径由调用方传入，通常是 pytest 的 tmp_path 下）。

迁移 / 回填类测试【禁止】使用本 helper
--------------------------------------
模板库是 ``create_all`` 建出来的「当前 ORM 全量 schema」，**没有** alembic 版本链、
也没有任何历史库形态。凡是要真跑 alembic（``command.upgrade`` /
``ensure_alembic_revision``）、验证迁移脚本 / schema 收敛 / 存量数据回填的测试，
都必须自己从真实历史库起步；用本 helper 克隆出来的库意味着迁移路径根本没被执行，
测试会「假绿」。真跑 alembic 的测试文件（提速第 2 批派单 §2.3 列出的 11 个）一个都不许改。

实现要点
--------
- 模板：每进程一个 ``mkdtemp`` 根目录；同步 ``create_engine`` + 一次 ``create_all``
  （首次约 1 s，之后复用）；
- 克隆：``sqlite3`` 在线 backup（``source.backup(target)``，页级拷贝、含 WAL 一致快照）
  到调用方给的用例文件库；
- 返回 ``create_async_engine("sqlite+aiosqlite:///<dst>", poolclass=NullPool)``，并对该
  engine **逐连接**注册生产同款 PRAGMA——``app/db/engine.py`` 的 listener 只挂在它自己的
  单例 engine 上，自建 engine 不吃，必须自己注册；
- ``with_plugins=True``：模板需含插件独立表。插件表在
  ``app.plugins.plugin_base.plugin_metadata``（**不在** ``Base.metadata``），生产由
  ``registry._ensure_plugin_tables_sync()`` 建；此处先确保插件模型已注册
  （按被测文件原有做法加载插件目录），再 ``plugin_metadata.create_all(checkfirst=True)``。
"""
from __future__ import annotations

import atexit
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# 生产同款 PRAGMA（与 app/db/engine.py 的 _sqlite_pragmas 逐条对齐；顺序也保持一致）。
# 自建 engine 不继承那个 listener，故在每条新连接的 "connect" 事件里重放一遍。
_PRAGMAS = (
    "PRAGMA journal_mode=WAL;",         # 写前日志，单写多读并发
    "PRAGMA synchronous=NORMAL;",       # WAL 下 NORMAL 安全且更快
    "PRAGMA busy_timeout=10000;",       # 毫秒级，与 connect_args.timeout 双保险
    "PRAGMA wal_autocheckpoint=1000;",  # 每 1000 页自动 checkpoint，防 -wal 无限增长
    "PRAGMA foreign_keys=ON;",          # P4（2026-09-09）：引擎强制外键
)

# ``with_plugins=True`` 时按被测文件原有做法加载的插件目录（只有 douyin_mcp 注册了插件表；
# 见 plugins/examples/douyin_mcp/douyin_models.py → PluginBase/plugin_metadata）。
_PLUGIN_MODULE = "ai_plugin_douyin_mcp"
_PLUGIN_DIR_NAME = "douyin_mcp"

# 每进程一个 mkdtemp 根目录（进程结束尽力清理；多进程并行时互不干扰）。
_ROOT: Path | None = None
# with_plugins -> 模板库路径（每进程最多两份：纯主表 / 主表+插件表）。
_TEMPLATES: dict[bool, Path] = {}


def _root() -> Path:
    """惰性创建每进程唯一的模板根目录（atexit 尽力清理，失败不影响测试）。"""
    global _ROOT
    if _ROOT is None:
        _ROOT = Path(tempfile.mkdtemp(prefix="ambrace_dbclone_"))
        atexit.register(shutil.rmtree, str(_ROOT), True)  # ignore_errors=True：Windows 句柄延迟释放不报错
    return _ROOT


def _build_template(with_plugins: bool) -> Path:
    """同步建好模板库（一次 create_all，约 1 s），返回模板文件路径。"""
    import app.models  # noqa: F401  # 注册全部模型到 Base.metadata
    from app.models.base import Base

    dst = _root() / ("template_plugins.db" if with_plugins else "template.db")
    eng = create_engine(f"sqlite:///{dst.as_posix()}", poolclass=NullPool)
    try:
        Base.metadata.create_all(eng)
        if with_plugins:
            _ensure_plugin_models()
            from app.plugins.plugin_base import plugin_metadata
            plugin_metadata.create_all(eng, checkfirst=True)
    finally:
        eng.dispose()
    return dst


def _ensure_plugin_models() -> None:
    """确保渠道插件模型已注册进 ``plugin_metadata``（已装载则复用，不重复 exec）。

    ``registry.load_plugin_dir`` 内部会 ``exec_module`` 插件 main.py；重复调用会重跑一遍
    模块级代码，故这里按 ``sys.modules`` 去重——与 test_plugin_tenant_scope_m0.py 里
    ``douyin_mod`` fixture 的「已装载则复用」口径一致。
    """
    if _PLUGIN_MODULE in sys.modules:
        return
    from app.plugins import registry
    registry.load_plugin_dir(registry.EXAMPLE_DIR / _PLUGIN_DIR_NAME)


def _template(with_plugins: bool) -> Path:
    """取（必要时建）本进程的模板库路径。"""
    hit = _TEMPLATES.get(with_plugins)
    if hit is None or not hit.exists():
        hit = _build_template(with_plugins)
        _TEMPLATES[with_plugins] = hit
    return hit


def _clone(src_path: Path, dst_path: Path) -> None:
    """SQLite 在线 backup：页级拷贝 + WAL 一致快照（不读业务行，只拷页）。"""
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dst_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _register_pragmas(engine) -> None:
    """给自建 async engine 逐连接注册生产同款 PRAGMA（NullPool → 每连接都会执行一次）。"""
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 由 engine 回调
        cur = dbapi_conn.cursor()
        try:
            for stmt in _PRAGMAS:
                cur.execute(stmt)
        finally:
            cur.close()


def clone_engine(dst_path, with_plugins: bool = False, pragmas: bool = True):
    """把会话级模板库克隆到 ``dst_path``（用例自己的文件库），返回新的 async engine。

    参数
    ----
    dst_path : str | os.PathLike
        克隆目标文件路径，由调用方传入（通常 ``tmp_path / "x.db"``）。父目录不存在时自动创建。
    with_plugins : bool
        True 时模板额外含插件独立表（``plugin_metadata``；本 helper 会先加载 douyin_mcp）。
    pragmas : bool
        默认 True：新 engine 的每条连接执行生产同款 PRAGMA（含 ``foreign_keys=ON``）。
        仅在「开 FK 会让该文件无法在不动种子数据的前提下跑通」时才可以关——关掉前请先问
        「是不是该把缺失的父行补进种子」（补父行才是更正确的口径）。

    返回
    ----
    AsyncEngine
        ``create_async_engine("sqlite+aiosqlite:///<dst_path>", poolclass=NullPool)``；
        调用方负责在 teardown 里 ``asyncio.run(engine.dispose())``（或 ``engine.sync_engine.dispose()``）。
    """
    dst = Path(dst_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    _clone(_template(with_plugins), dst)
    engine = create_async_engine(f"sqlite+aiosqlite:///{dst.as_posix()}", poolclass=NullPool)
    if pragmas:
        _register_pragmas(engine)
    return engine


def make_session_factory(engine):
    """与各测试文件原写法一致的会话工厂（``expire_on_commit=False``）。"""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
