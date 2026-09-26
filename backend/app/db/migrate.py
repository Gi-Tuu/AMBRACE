# -*- coding: utf-8 -*-
"""Alembic 迁移集成：启动时把当前数据库对齐到版本链 head。

设计决策（渐进式，避免把 90+ 条手工迁移重写为版本链的高风险）：
- ``init_db()``（app/db/database.py）仍是幂等兼容层：首次建库（create_all）+ 增量补丁
  （PRAGMA 幂等 ALTER）继续生效，保证存量与全新部署的 schema 都到当前模型状态，**不动**。
- 本模块负责「版本记账」：把当前库的 alembic_version 对齐到 head。3.8 收尾（渐进版，
  2026-09-03）把「无版本」分支细分为三类，避免远古库被直接 stamp 后缺列：
  - **全新空库（无任何表）** → stamp head（不重放，版本记账）；
  - **非空老库（有表无版本，schema 落后）** → alembic upgrade head 整链重放：版本链已具备
    has_table / has_index / has_column 全守卫 + bootstrap 补列迁移，可对已存在表安全重放，
    补齐远古库缺的列（修复「只 stamp 不重放」造成的缺列漂移）；
  - **当前 schema 库（有表无版本，但列已在 init_db/create_all 建到当前模型）** → stamp head
    （不重放；用「是否已含链上新增列」判别，避免每会话重放 16 条迁移）。
  - 已知旧版本且落后于 head → alembic upgrade head（不变）；
  - 孤儿版本（版本号不在本链）→ stamp head --purge（清旧历史再标，理由见函数内）。
- 使用同步引擎（源自 settings.database_url，去掉 +aiosqlite）执行，避免与异步引擎混用；
  Alembic 迁移脚本本身跑在同步引擎上（SQLite 用内置 sqlite3 驱动）。
- 未来新增 schema 变更：改模型 → ``alembic revision --autogenerate`` 生成修订 → 入版本链；
  init_db 不要再新增手工 ALTER。
"""
import asyncio
import logging
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect as sa_inspect, text
from sqlalchemy.pool import NullPool

from app.config import settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"
_ALEMBIC_DIR = _BACKEND_ROOT / "alembic"

# 「当前 schema」判别哨兵：『只由版本链引入、init_db 从不添加』的表/列。
# 全新/当前库（current create_all）含全部这些表/列；远古库（pre-alembic 旧库）缺其中若干。
# 命中全部 → 判定为当前 schema → stamp（不重放）；缺任一 → 判定为落后 → upgrade head。
# 注：create_all 路径会建出的表用「表级哨兵」（如 A5 的 user_runtime_flags），其余为列级哨兵。
_CURRENT_SCHEMA_SENTINELS: list[tuple[str, str]] = [
    ("users", "parent_id"),
    ("memories", "group_id"),
    ("memories", "status"),
    ("memories", "superseded_by"),
    ("memories", "derived_from_ids"),
    ("memories", "valid_from"),
    ("memories", "valid_to"),
    ("ai_characters", "talkativeness"),
    ("ai_characters", "talkativeness_locked"),
    ("ai_characters", "user_llm_config_id"),
    ("chat_group_members", "muted"),
    ("chat_group_messages", "game_session_id"),
    ("chat_group_messages", "msg_type"),
    ("life_states", "current_room"),
    ("life_states", "location"),
    ("life_states", "location_updated_at"),
    ("llm_usage", "config_id"),
    ("llm_usage", "group_owner_id"),
    ("lorebook_entries", "cooldown_rounds"),
    ("lorebook_entries", "inclusion_group"),
    ("lorebook_entries", "is_regex"),
    ("lorebook_entries", "probability"),
    ("lorebook_entries", "sticky_rounds"),
    ("plugins", "source"),
    ("plugins", "source_url"),
    ("plugins", "sha256"),
    ("plugins", "consented_permissions"),
    ("plugins", "consented_at"),
    # ── A2 M1 插件归户（2026-09-20）：plugins.owner_user_id 只由迁移链 add_column 引入，
    # init_db 的 create_all 会建（当前模型含该列）但远古库不会 —— 老库（有表无版本号）缺此列时
    # 必须判「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺列（select 直接报错）。
    ("plugins", "owner_user_id"),
    # ── A2 M6 插件同意按租户（2026-09-20）：plugin_consents 是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （registry.get_tenant_consented_permissions / backfill 直接 select 报错，租户级同意静默失效）。
    ("plugin_consents", "plugin_name"),
    # ── P3-4（2026-09-19）：MCP 本地回环细粒度放行（mcp_servers.allow_loopback）──
    # 该列只由 Alembic 迁移链 add_column 引入、init_db 从不添加：老库（有表但无版本号）缺此列时
    # 必须判为「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺列（select 直接报错）。
    ("mcp_servers", "allow_loopback"),
    # ── A5 用户级开关覆盖（2026-09-19）：user_runtime_flags 是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （flag_service.resolve_flag/get_user_flags 直接 select 报错，用户级覆盖静默失效）。
    ("user_runtime_flags", "user_id"),
    # ── A8 LLM 额度按账号（2026-09-20）：user_llm_limits 同样是【只由迁移链 create_table 引入】的表 ──
    # 老库（有表但无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表
    # （llm_quota.resolve_limit / get_user_overrides 直接 select 报错，账号级额度静默失效）。
    ("user_llm_limits", "user_id"),
    # ── 一机多主 / 渠道绑定 per-账号化（2026-09-05，28→31；T5 2026-09-10 回退到 29）──
    # channel_bindings 是【主表】（app/models/channel/__init__.py），保留。
    ("channel_bindings", "tenant_id"),
    # T5（2026-09-10）：以下两条为【插件表】，已移出主 Base.metadata、不在主 schema 判别内 ——
    # 插件表的存在性由 registry.ensure_plugin_tables() 幂等保证（未装载渠道=本就不该有表），
    # 若继续列为主哨兵，未装某渠道的部署会被反复判「落后」而去 upgrade，
    # 但版本链并不建 wechat_ilink_*（baseline 只建 douyin_*），反而造成无意义重放。
    # ("wechat_ilink_bindings", "tenant_id"),   # 移除（插件表，registry 建）
    # ("douyin_accounts", "tenant_id"),         # 移除（插件表；版本链 baseline 仍建、_migration_chain_tables 自动判别仍覆盖）
    # ── X7-M1 结构化承载（2026-09-22，派单 P9）：phone_snapshots.payload_json ──
    # 该列由迁移 f4a5b6c7d8e9 add_column 引入；老库（有表无版本号）缺此列时必须判「落后」走
    # upgrade head 补列，否则会被 stamp 到 head 却永久缺列（select PhoneSnapshot 直接报错）。
    ("phone_snapshots", "payload_json"),
    # ── X7-M4c-3 行动名单落库（2026-09-23，派单 P19）：两张【只由迁移链 create_table 引入】的表 ──
    # device_action_targets 是闸门④（目标白名单）的权威来源、device_action_plugins 是闸门③a
    # （逐插件灰度）的权威来源。老库缺表时若不判「落后」，会被 stamp 到 head 却永久缺表——
    # 读库异常被 fail-closed 吞成「名单为空＝全拒」，运维看到的是拒而不是缺表，问题被掩盖。
    ("device_action_targets", "target"),
    ("device_action_plugins", "plugin_name"),
    # ── 控制台删号·第一期地基（2026-09-24，派单「删号第一批」）：users 回收站两列 ──
    # 这两列【只由迁移 f6a7b8c9d0e1 add_column 引入】。老库（有表但无版本号）缺列时若不判
    # 「落后」，会被 stamp 到 head 却永久缺列——标记删除直接 UPDATE 不存在的列（500），
    # 而 GET /server/accounts 读 deleted_at 也会报错，删号功能整体不可用。
    ("users", "deleted_at"),
    ("users", "purge_after"),
    # ── 控制台删号·第二期第一批（2026-09-24，派单「物理清除器」）：account_purge_jobs 进度账本 ──
    # 该表【有 ORM 模型】（app/models/user/__init__.py::AccountPurgeJob，2026-09-24 Codex 复核补）：
    # 哨兵表必须在 metadata 里，否则「主 ORM 建齐、只缺插件表」的库会被判落后（T5 同口径，见
    # tests/test_migrate_schema_detect.py::test_manual_sentinel_ignores_plugin_tables）；
    # 老库补齐由迁移 f7b8c9d0e1f2 的 create_table 负责。
    # 老库（有表无版本号）缺此表时若不判「落后」，会被 stamp 到 head 却永久缺表：清除器无法
    # 记账，表现为「删一半进程被重启后无从续跑」——正是这张表要防的那件事。
    ("account_purge_jobs", "user_id"),
]


def _migration_chain_tables(cfg: Config) -> set[str]:
    """遍历版本链全部迁移脚本，正则收集 ``create_table('x'`` 的表名（含插件/渠道等非 ORM 表）。

    §6.3（2026-09-09）：把「当前 schema」判别从人工列哨兵升级为自动比对——版本链建过的表
    必须全部存在；缺任一即判落后走 upgrade head（迁移自带 has_table 守卫，幂等补齐）。
    避免未来新增「只在某迁移 create_table、不在主 ORM」的表时，忘加哨兵列导致新库永久缺表。
    """
    import re

    script = ScriptDirectory.from_config(cfg)
    names: set[str] = set()
    for rev in script.walk_revisions():
        # rev.path 一般为相对 script_location；拼绝对路径读取，读不到则跳过（保守）。
        path = Path(rev.path) if Path(rev.path).is_absolute() else (_ALEMBIC_DIR / rev.path)
        if not path.exists():
            continue
        try:
            txt = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        names.update(re.findall(r"create_table\(\s*[\'\"]([a-zA-Z0-9_]+)[\'\"]", txt))
    return names


def _schema_is_current_auto(sync_url: str, cfg: Config) -> bool:
    """自动比对「版本链建过的全部表」vs 实际库表：need ⊆ have 才算当前 schema。

    与 _schema_is_current（人工 31 列哨兵）取「与」使用——两判都过才 stamp，任一存疑
    （表缺失/版本链不可读）都走 upgrade head，保守且幂等。
    """
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        insp = sa_inspect(engine)
        have = set(insp.get_table_names())
        need = _migration_chain_tables(cfg)
        if not need:
            return False  # 版本链为空/不可读 → 保守判非当前，走 upgrade 补齐
        return need.issubset(have)
    finally:
        engine.dispose()


def _alembic_config() -> Config:
    """构造 Alembic Config，脚本位置指向 backend/alembic。"""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return cfg


def _sync_url() -> str:
    """把异步 DB URL 转为 Alembic 同步引擎 URL。"""
    url = settings.database_url
    if url.startswith("sqlite+aiosqlite"):
        return url.replace("sqlite+aiosqlite", "sqlite", 1)
    if url.startswith("postgresql+asyncpg"):
        return url.replace("postgresql+asyncpg", "postgresql", 1)
    return url


def _current_rev(sync_url: str) -> str | None:
    """读取当前库的 alembic_version；无版本表则返回 None。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        if not sa_inspect(engine).has_table("alembic_version"):
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
            return row[0] if row else None
    finally:
        engine.dispose()


def _has_any_table(sync_url: str) -> bool:
    """判断库中是否存在任何用户表（排除 alembic_version 自身）。"""
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        tables = sa_inspect(engine).get_table_names()
        return any(t != "alembic_version" for t in tables)
    finally:
        engine.dispose()


def _schema_is_current(sync_url: str) -> bool:
    """判别库是否已是当前 schema（链上新增的哨兵列全部存在）。

    仅当「每一张相关表都存在且含对应列」才返回 True；任一表缺失/列缺失 → False（判为落后库）。
    保守取向：宁可判为「落后」去 upgrade head（正确且幂等），不误判为「当前」去 stamp。
    """
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        insp = sa_inspect(engine)
        for table, column in _CURRENT_SCHEMA_SENTINELS:
            try:
                if not insp.has_table(table):
                    return False
                col_names = {c["name"] for c in insp.get_columns(table)}
                if column not in col_names:
                    return False
            except Exception:
                # 表被锁 / 无法反射 → 安全取向：判为未确认当前 → 走 upgrade（保守）。
                return False
        return True
    finally:
        engine.dispose()


@contextmanager
def _alembic_logging_guard():
    """让 alembic 的 ``fileConfig`` 副作用不污染应用日志。

    env.py 会按 alembic.ini 调 ``fileConfig``：它会把 root logger 的 handlers 换成
    「console」、level 压到 WARN，并在默认 ``disable_existing_loggers=True`` 下把已存在
    的应用 logger 全部置 ``disabled``。结果是「本次启动若真的跑了迁移，之后 app.log
    一条不写」——服务进程活着、端口在听、却全无日志（2026-09-25 实测事故：日志停在
    ``Database initialized`` 之后的 Alembic 对齐那一步）。

    这里在进入前对日志设施做快照，退出时逐项还原；无论成败都在 ``finally`` 还原。
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved: dict[str, tuple[bool, int, bool]] = {}
    for name, obj in list(logging.root.manager.loggerDict.items()):
        if isinstance(obj, logging.Logger):
            saved[name] = (obj.disabled, obj.level, obj.propagate)
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_root_level)
        for name, (disabled, level, propagate) in saved.items():
            obj = logging.root.manager.loggerDict.get(name)
            if isinstance(obj, logging.Logger):
                obj.disabled = disabled
                obj.level = level
                obj.propagate = propagate
        # 快照里**没有**的 alembic* logger 是 fileConfig 期间新建的（env.py 加载时才产生）：
        # 还原逻辑管不到它们，会在进程里留下一份没人管理的 handler/logger，属未收尾的全局污染。
        for name in [n for n in logging.root.manager.loggerDict
                     if (n == "alembic" or n.startswith("alembic.")) and n not in saved]:
            obj = logging.root.manager.loggerDict.pop(name, None)
            if not isinstance(obj, logging.Logger):
                continue
            for handler in obj.handlers:
                try:
                    handler.close()
                except Exception:
                    pass
            obj.handlers.clear()


def _ensure_alembic_revision_sync() -> str:
    """同步执行对齐，返回动作描述（stamped / re-stamped / upgraded / already_at_head）。

    外层套 ``_alembic_logging_guard``：alembic 的 env.py 会 fileConfig（按 alembic.ini
    重配 root 日志、并禁用已存在的 logger），跑完必须把应用日志原样还原，否则「启动时
    跑过迁移」会让后端日志整体静默（2026-09-25 实测事故）。
    """
    with _alembic_logging_guard():
        return _ensure_alembic_revision_sync_inner()


def _ensure_alembic_revision_sync_inner() -> str:
    cfg = _alembic_config()

    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    sync_url = _sync_url()
    cur = _current_rev(sync_url)

    if cur == head:
        return "already_at_head"

    # 判断当前版本号是否在本版本链中（已知 revision）。存量库可能带一个历史遗留/被移除的
    # alembic 试验版本号（例如本机真实库的 78d3405d8b58），该版本不在本链中 → 当作 orphaned。
    known = None
    if cur is not None:
        try:
            known = script.revision_map.get_revision(cur)
        except Exception:
            known = None

    if cur is None:
        # 无 alembic_version：细分三类。
        if not _has_any_table(sync_url):
            # 全新空库：无任何表 → 仅标记为已迁移到 head（不重放）。
            command.stamp(cfg, head)
            return f"stamped:{head}"
        if _schema_is_current(sync_url) and _schema_is_current_auto(sync_url, cfg):
            # 有表、无版本，且 schema 已是当前（init_db/create_all 已建到当前模型）→ 标记，不重放。
            # §6.3：人工列哨兵（_schema_is_current）与版本链建表全集自动比对取「与」，
            # 防新增非 ORM 表漏加哨兵时新库被误判当前而永久缺表。
            command.stamp(cfg, head)
            return f"stamped:{head}"
        # 非空老库：schema 落后（缺链上新增列）→ 整链重放补齐（守卫幂等，安全）。
        command.upgrade(cfg, head)
        return f"upgraded:->{head}"

    if known is None:
        # 存量库带一个本版本链之外的版本号（历史遗留/被移除的 alembic 试验）：
        # init_db 已把 schema 对齐到当前模型；用 purge=True 清掉旧历史后重新 stamp 到 head，
        # 绕开 alembic 解析「孤儿版本号」时的失败。理由：该版本号不在本链，upgrade 无法定位
        # 其迁移路径；而 schema 已由 init_db 幂等层对齐，故清旧历史按当前 head 记账最稳。
        command.stamp(cfg, head, purge=True)
        return f"re-stamped:{cur}->{head}"

    # 已知旧版本，且落后于 head → 执行升级（应用后续 schema 变更）。
    command.upgrade(cfg, head)
    return f"upgraded:{cur}->{head}"


async def ensure_alembic_revision() -> str:
    """启动时对齐数据库版本链（在线程池中执行，不阻塞事件循环）。

    幂等：库已在 head 时为 no-op；无版本时按「空库/当前库/落后老库」分流 stamp 或 upgrade。
    返回动作描述字符串，调用方（main.py lifespan）可记录日志。
    """
    return await asyncio.to_thread(_ensure_alembic_revision_sync)


def is_migration_available() -> bool:
    """判断 Alembic 是否就绪（alembic.ini 与脚本目录存在）。供 main.py 防御性判断。"""
    return _ALEMBIC_INI.exists() and (_ALEMBIC_DIR / "env.py").exists()
