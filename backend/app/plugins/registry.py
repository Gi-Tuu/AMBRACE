"""插件注册表：扫描目录 → 校验 manifest → 动态加载 main.py → hook 分发

- 插件目录：项目根 plugins/examples/（内置示例，入库/进开源包）
           + backend/data/plugins/（用户安装，不入库、不进开源包）
- 开关/配置持久化在 plugins 表；运行时缓存启用集合，hook 分发零 DB 开销
- 单个插件异常完全隔离，不影响核心链路
"""
import asyncio
import contextvars
import importlib.util
import inspect
import json
import time as _time
from contextlib import contextmanager
from app.utils.logger import get_logger
import sys
from pathlib import Path

from app.plugins.manifest import load_manifest

_logger = get_logger("plugins")

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # backend/app/plugins -> 项目根
EXAMPLE_DIR = PROJECT_ROOT / "plugins" / "examples"
USER_DIR = PROJECT_ROOT / "backend" / "data" / "plugins"

# name -> {"info": {...}, "module": module, "hooks": {hook: [func, ...]}, "actions": {action: func}}
_loaded: dict[str, dict] = {}
# name -> enabled（内存缓存，load 时从 DB 同步）
_enabled: dict[str, bool] = {}
# name -> config dict（内存缓存）
_db_config: dict[str, dict] = {}
# name -> 安装来源/同意元数据缓存（source/source_url/sha256/consented_permissions/consented_at；3.9）
_db_prov: dict[str, dict] = {}
# 当前插件执行上下文（供 sdk 定位）：A2 M4（2026-09-20）由进程级单键 dict 改为 ContextVar。
# 值形如 {"current": 插件名, "user_id": 调用者账号, "tenant_id": 调用者家庭根}；不在插件上下文时为 None。
# 历史坑（full-project-audit P1）：进程级 dict 在「set → await 插件 hook → pop」期间被并发协程覆盖，
# 会让 sdk.get_config()/require_permission 解析到别的插件身份 —— ContextVar 保证各协程互不串身份。
# 本改动 always-on（纯并发正确性修复，单账号行为不变）。
_sdk_ctx: contextvars.ContextVar = contextvars.ContextVar("plugin_sdk_ctx", default=None)


def push_sdk_context(plugin: str, *, user_id: int | None = None,
                     tenant_id: int | None = None) -> contextvars.Token:
    """进入插件上下文，返回 token（必须与 :func:`reset_sdk_context` 成对使用）。"""
    return _sdk_ctx.set({"current": plugin, "user_id": user_id, "tenant_id": tenant_id})


def reset_sdk_context(token: contextvars.Token) -> None:
    """退出插件上下文（reset 回 set 之前的值；token 失效等异常静默，不影响主链路）。"""
    try:
        _sdk_ctx.reset(token)
    except Exception:
        pass


@contextmanager
def sdk_context(plugin: str, *, user_id: int | None = None, tenant_id: int | None = None):
    """with 形态的插件上下文（测试/一次性调用用；与 push/reset 同语义）。"""
    token = push_sdk_context(plugin, user_id=user_id, tenant_id=tenant_id)
    try:
        yield
    finally:
        reset_sdk_context(token)


def current_sdk_context() -> dict:
    """当前插件上下文快照（不在插件上下文时为空 dict；供 sdk 归属断言读取 caller）。"""
    return dict(_sdk_ctx.get() or {})


def _scan_dir(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]


def resolve_plugin_dir(name: str) -> Path | None:
    """在 EXAMPLE_DIR 或 USER_DIR 中查找插件目录（含 manifest.json），返回 Path 或 None（48a 页面托管/卸载用）"""
    for base in (EXAMPLE_DIR, USER_DIR):
        p = base / str(name or "")
        if p.is_dir() and (p / "manifest.json").is_file():
            return p
    return None


def load_plugin_dir(path: Path) -> dict | None:
    """加载单个插件目录，返回 info dict；失败返回 None"""
    try:
        manifest = load_manifest(str(path / "manifest.json"))
        if manifest is None:
            _logger.warning("插件 %s manifest 无效，跳过", path.name)
            return None
        name = manifest["name"]
        plugin_type = str(manifest.get("type", "http") or "http").strip()  # 48c：缺省 http
        main_py = path / "main.py"
        module = None
        if main_py.is_file():
            module_name = f"ai_plugin_{name}"
            spec = importlib.util.spec_from_file_location(module_name, str(main_py))
            if spec is None or spec.loader is None:
                _logger.warning("插件 %s 无法创建 spec，跳过", name)
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            # 先占位再执行 main.py（其中 sdk.hook 注册到本插件名下，避免 KeyError）
            _loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
            _token = push_sdk_context(name)
            try:
                spec.loader.exec_module(module)
            finally:
                reset_sdk_context(_token)
        else:
            # 48c：config-only 加载——无 main.py 但 type ∈ prompt/chat/workflow/hybrid/content 时仍加载 info
            #      （hybrid 页面插件（48a）亦无需 main.py，page 资源由页面托管端点直读磁盘；
            #       X2：content 内容包零代码，数据来自 manifest.content，经 validate_manifest 校验）
            if plugin_type not in ("prompt", "chat", "workflow", "hybrid", "content"):
                _logger.warning("插件 %s 缺 main.py 且非配置型(type=%s)，跳过", name, plugin_type)
                return None
            _loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
        page = str(manifest.get("page", "") or "").strip()
        info = {
            "name": name,
            "version": str(manifest.get("version", "0.0.1")),
            "description": str(manifest.get("description", "")),
            "author": str(manifest.get("author", "")),
            "category": str(manifest.get("category", "plugin")),
            "type": plugin_type,  # 48c：插件类型（http/prompt/chat/workflow/hybrid）
            "icon": str(manifest.get("icon", "") or ""),  # 48a：图标名（≤32 字符）
            "page": page,  # 48a：页面入口相对路径（可选）
            "has_page": bool(page) and (path / page).is_file(),  # 48a：page 非空且入口文件存在
            "hooks": list(manifest.get("hooks", [])),
            "permissions": list(manifest.get("permissions", [])),
            "config": dict(manifest.get("config", {})),
            "usage": str(manifest.get("usage", "") or ""),  # 使用教程（前端扩展页展示；可选）
            # R6（2026-09-09）：中文展示名（可选；未填时由 ability_labels.plugin_label 美化 name 兜底）
            "display_name": str(manifest.get("display_name", "") or ""),
            "hook_timeout": manifest.get("hook_timeout"),  # per-plugin hook 超时（秒，可选；2026-08-16 审计修复）
            # X6-b：策略包只读素材白名单（sdk.get_proactive_context 按此过滤 key）
            "context_keys": list(manifest.get("context_keys") or []),
            "content": dict(manifest.get("content") or {}) if plugin_type == "content" else {},  # X2：内容包数据（已过 schema 校验）
            "path": str(path),
        }
        _loaded[name] = {"info": info, "module": module, "hooks": _loaded[name].get("hooks", {}), "actions": _loaded[name].get("actions", {}), "router": _loaded[name].get("router")}
        # ── T5（2026-09-10）：该插件若注册了 ORM 表，加载即幂等建表 ──────────────
        # checkfirst=True，失败只告警、不影响加载成功（测试/热安装路径的建表保障：
        # 插件表已不在主 Base.metadata，init_db 的 create_all 不再顺带建）。
        try:
            _ensure_plugin_tables_sync()
        except Exception as _e:
            _logger.warning("post-load ensure plugin tables failed (%s): %s", name, _e)
        return info
    except Exception as e:
        _logger.warning("插件 %s 加载失败: %s", path.name, e)
        return None


# ── P3-5：插件表 schema reconcile（2026-09-11）────────────────────────────
def _py_default_literal(col):
    """ORM 列的 Python 端标量 default → SQL 字面量。

    SQLite 的 ``ALTER TABLE ADD COLUMN`` 要求：新增 NOT NULL 列必须带 DEFAULT。
    插件里这些后加列（aweme_id/comment_id/music_mood/post_type/video_path）在
    ORM 上都带 ``default=""``/``default="image"`` 标量默认，据此产出 DEFAULT 子句；
    取不到标量默认则返回 None（调用方改以可空加列，绝不阻塞收敛）。
    """
    try:
        d = getattr(col, "default", None)
        if d is None or not getattr(d, "is_scalar", False):
            return None
        arg = d.arg
        # 注意顺序：bool 是 int 的子类，必须先判 bool
        if isinstance(arg, str):
            return "'" + arg.replace("'", "''") + "'"
        if isinstance(arg, bool):
            return "1" if arg else "0"
        if isinstance(arg, (int, float)):
            return str(arg)
    except Exception:
        return None
    return None


def _reconcile_plugin_schema(eng):
    """把已存在的物理插件表向 ``plugin_metadata`` 当前 ORM 定义收敛：缺列补列、缺索引补索引。

    背景（P3-5）：抖音 5 表历史上由主 alembic baseline/a7b8/b8c9 建过，之后 ORM 加列、
    加 tenant 单列索引未补迁移，而 create_all(checkfirst) 见表在就整体跳过 → 整链重放的
    新库缺列（运行时 no such column）、缺索引。本函数让「无论表由 baseline / 老库 /
    create_all 建」都收敛到当前 ORM；微信表历来由 ORM 建，跑本函数为 0 操作。

    能力边界（SQLite 限制，刻意不做，理由见方案 §2.4）：
    - 不收紧 NOT NULL、不改列类型（需 batch 重建表，风险 > 收益）；
    - 不 DROP 列（向前兼容，旧版本库可能被新版本读到多列）。

    返回 ``(added_cols, added_idx)``，供启动日志核对。
    """
    import sqlalchemy as sa
    from sqlalchemy.dialects import sqlite as sa_sqlite
    from sqlalchemy.schema import CreateIndex

    from app.plugins.plugin_base import plugin_metadata

    added_cols: list[str] = []
    added_idx: list[str] = []
    dialect = sa_sqlite.dialect()

    insp = sa.inspect(eng)
    have_tables = set(insp.get_table_names())
    for tname, tbl in plugin_metadata.tables.items():
        if tname not in have_tables:
            continue  # 缺表交给 create_all 建
        phys_cols = {c["name"] for c in insp.get_columns(tname)}
        # 1) 缺列 → ALTER TABLE ADD COLUMN（NOT NULL 必须带 DEFAULT；否则以可空加列）
        for col in tbl.columns:
            if col.name in phys_cols:
                continue
            coltype = col.type.compile(dialect=dialect)
            lit = _py_default_literal(col)
            if (not col.nullable) and lit is not None:
                ddl = (f"ALTER TABLE {tname} ADD COLUMN {col.name} {coltype} "
                       f"NOT NULL DEFAULT {lit}")
            else:
                ddl = f"ALTER TABLE {tname} ADD COLUMN {col.name} {coltype}"
            with eng.begin() as conn:
                conn.execute(sa.text(ddl))
            added_cols.append(f"{tname}.{col.name}")
        # 2) 缺索引 → CREATE [UNIQUE] INDEX IF NOT EXISTS（让 SQLAlchemy 编译，
        #    自动带 IF NOT EXISTS / UNIQUE / partial WHERE，避免手拼 SQL 出错）
        insp = sa.inspect(eng)  # 补列后反射缓存可能过期，重建 inspector
        phys_idx = {ix["name"] for ix in insp.get_indexes(tname)}
        for idx in tbl.indexes:
            if idx.name is None or idx.name in phys_idx:
                continue
            with eng.begin() as conn:
                conn.execute(CreateIndex(idx, if_not_exists=True))
            added_idx.append(f"{tname}.{idx.name}")
    return added_cols, added_idx


# ── T5：插件独立 metadata 幂等建表（2026-09-10）────────────────────────────
def _ensure_plugin_tables_sync() -> list[str]:
    """对已加载插件注册到 ``plugin_metadata`` 的表幂等建表 + schema 收敛。

    - 同步执行（SQLite CREATE TABLE 毫秒级；插件加载是低频动作）；
    - 生产主路径由 lifespan 在线程池调异步版 :func:`ensure_plugin_tables`；
    - 测试 / 运行时热安装在 :func:`load_plugin_dir` 成功后内联调一次（加载即建表，
      不依赖调用方记得初始化）；
    - ``create_all(checkfirst=True)`` **建缺表**（表已存在即跳过），随后 P3-5
      reconcile **补已存在表的缺列/缺索引** —— 存量库幂等收敛、零数据迁移；版本链
      已建的 douyin 旧表在此补齐 ORM 后加的列与索引，wechat 表在此建立；
      未加载的渠道其表不在 metadata、不会被建。
    - 建表/收敛失败只告警、不抛出（单插件隔离，不拖垮其它插件与内核启动）。

    返回：本次纳入建表的插件表名清单（已存在/新建都算；失败返回空列表）。
    """
    try:
        from app.plugins.plugin_base import plugin_metadata
    except Exception as e:  # pragma: no cover - 防御
        _logger.warning("plugin_metadata import failed: %s", e)
        return []
    if not plugin_metadata.tables:
        return []  # 尚无插件注册任何表（config-only 插件 / 未加载渠道）→ no-op
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        from app.db.migrate import _sync_url  # 复用现成的「去 +aiosqlite / +asyncpg」同步 URL 推导
        url = _sync_url()
    except Exception as e:
        _logger.warning("resolve plugin db url failed: %s", e)
        return []
    names = sorted(plugin_metadata.tables.keys())
    try:
        # 与 migrate.py 一致：短连接、用完即弃，SQLite 下避免连接残留
        eng = create_engine(url, poolclass=NullPool)
        try:
            plugin_metadata.create_all(eng, checkfirst=True)
            # P3-5：create_all 只建缺表、见表在就跳过；再做一次幂等 reconcile，
            # 把 baseline/老库建出的旧插件表补齐 ORM 后加的列与索引（零数据风险、只做加法）
            added_cols, added_idx = _reconcile_plugin_schema(eng)
            if added_cols or added_idx:
                _logger.info(
                    "插件表 schema 收敛：补列 %s；补索引 %s", added_cols, added_idx
                )
            else:
                # 0 操作也留痕：否则线上分不清「跑过没问题」和「根本没执行」
                _logger.info(
                    "插件表 schema 收敛：无缺失（%d 张表，补列 0、补索引 0）", len(names)
                )
        finally:
            eng.dispose()
    except Exception as e:
        _logger.warning("ensure plugin tables failed (%s): %s", names, e)
        return []
    return names


async def ensure_plugin_tables() -> list[str]:
    """lifespan 调用：线程池中幂等建立插件表（不阻塞事件循环）。"""
    return await asyncio.to_thread(_ensure_plugin_tables_sync)


async def sync_plugins_db() -> None:
    """扫描插件目录并同步到 plugins 表（幂等 upsert）；刷新启用/配置缓存"""
    _loaded.clear()
    # X1（2026-08-31）：重扫前清空全部插件来源的游戏注册（main.py 重载时会重新注册；
    # 目录已被删除的插件其注册在此一并清理，防残留幽灵游戏）
    try:
        from app.games.registry import unregister_games_not_in
        unregister_games_not_in(set())
    except Exception:
        pass
    # X3（2026-08-31）：provider 注册同规则清理（重扫前清空插件来源注册，重载时重新注册）
    try:
        from app.providers.registry import unregister_providers_not_in
        unregister_providers_not_in(set())
    except Exception:
        pass
    # X6-b（2026-09-17）：策略类别登记同规则清理（目录被删的插件其登记在此一并清理，防幽灵让位）
    try:
        from app.scheduling.sources.strategy import reset_registrations
        reset_registrations()
    except Exception:
        pass
    _enabled.clear()
    _db_config.clear()
    _db_prov.clear()
    # A2 M4：重扫后可见集/来源全变 → 清运行面缓存（否则 30s 内仍按旧归属过滤）
    clear_runtime_scope_caches()
    # (name, source) 对：示例目录=builtin，用户目录=local（新建行以此落 source；存量行保留已记录来源）
    seen: list[tuple[str, str]] = []
    for _base, _src in ((EXAMPLE_DIR, "builtin"), (USER_DIR, "local")):
        for d in _scan_dir(_base):
            info = load_plugin_dir(d)
            if info:
                seen.append((info["name"], _src))
    if not seen:
        # A2 M6：无插件目录也不能漏掉存量同意回填（一次性、幂等、新表为空才执行）
        try:
            _n = await backfill_plugin_consents_once()
            if _n:
                _logger.info("插件同意回填（无目录分支）：%d 行", _n)
        except Exception as _e:
            _logger.warning("插件同意回填失败（无目录分支）: %s", _e)
        return
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        rows = (await db.execute(select(Plugin))).scalars().all()
        by_name = {r.name: r for r in rows}
        for name, _src in seen:
            info = _loaded[name]["info"]
            row = by_name.get(name)
            if row is None:
                # A2 M1：同步路径（含 builtin）= 服务级，owner 两列显式留 NULL（「内置=服务级」）；
                # 已安装行后续由 record_install_provenance 在 owner 为 NULL 时落安装者，
                # 本函数绝不改写 owner_user_id/owner_tenant_id（重复同步不得覆盖已有 owner）。
                db.add(Plugin(
                    name=name, version=info["version"], description=info["description"],
                    author=info["author"], category=info["category"],
                    type=info.get("type", "http"), enabled=False,
                    config_json=json.dumps(info["config"], ensure_ascii=False),
                    source=_src,
                    owner_user_id=None,
                    owner_tenant_id=None,
                ))
            else:
                row.version = info["version"]
                row.description = info["description"]
                row.author = info["author"]
                row.category = info["category"]
                row.type = info.get("type", "http")  # 48c：sync 时从 manifest 回填 type
        await db.commit()
        # 刷新内存缓存
        rows2 = (await db.execute(select(Plugin))).scalars().all()
        for r in rows2:
            _enabled[r.name] = bool(r.enabled)
            try:
                _db_config[r.name] = json.loads(r.config_json or "{}")
            except Exception:
                _db_config[r.name] = {}
            _db_prov[r.name] = _row_prov(r)
    # A2 M6：存量一致性回填（一次性、幂等、只在新表为空时；owner NULL 的存量行不写）
    try:
        _n = await backfill_plugin_consents_once()
        if _n:
            _logger.info("插件同意回填：%d 行（plugins.consented_permissions → plugin_consents）", _n)
    except Exception as _e:
        _logger.warning("插件同意回填失败: %s", _e)
    _logger.info("插件扫描完成：%d 个插件", len(seen))


def _row_prov(row) -> dict:
    """从 Plugin ORM 行提取来源/同意元数据（3.9），供缓存与接口输出用。

    A2 M3（2026-09-20）：附带安装者归属两列（``owner_user_id`` / ``owner_tenant_id``），
    供 ``list_plugins(viewer_user_id=...)`` 的可见性谓词读取；list_plugins 不把它们写进
    返回的插件 dict，故既有列表输出逐字节不变。
    """
    try:
        _con = json.loads(row.consented_permissions or "[]")
        if not isinstance(_con, list):
            _con = []
    except Exception:
        _con = []
    return {
        "source": str(getattr(row, "source", "builtin") or "builtin"),
        "source_url": getattr(row, "source_url", None),
        "sha256": getattr(row, "sha256", None),
        "consented_permissions": _con,
        "consented_at": row.consented_at.isoformat() if getattr(row, "consented_at", None) else None,
        # A2 M3：归属（NULL=内置/存量/服务级）
        "owner_user_id": getattr(row, "owner_user_id", None),
        "owner_tenant_id": getattr(row, "owner_tenant_id", None),
    }


async def get_plugin_provenance(name: str) -> dict:
    """读取插件来源/同意元数据（DB 为准；无行返回内置默认）。"""
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            return {"source": "builtin", "source_url": None, "sha256": None,
                    "consented_permissions": [], "consented_at": None}
        return _row_prov(row)


def _parse_perms(raw) -> list[str]:
    """JSON 文本 → 权限列表（非法/非数组 → 空列表）。"""
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


async def get_plugin_consented_permissions(name: str, tenant_id: int | None = None) -> list[str]:
    """读取已同意权限集（3.9；A2 M6 起可按租户读）。

    - ``tenant_id`` 非空 → 读 ``plugin_consents(plugin_name, tenant_id)``（新表权威），
      未命中再按 ``get_tenant_consented_permissions`` 的回落规则处理；
    - ``tenant_id`` 为空 → 服务级兼容读（``get_plugin_provenance``，与 M6 前一致）。
    """
    if tenant_id is not None:
        return await get_tenant_consented_permissions(name, tenant_id)
    return list((await get_plugin_provenance(name)).get("consented_permissions", []))


async def get_tenant_consented_permissions(name: str, tenant_id: int | None) -> list[str]:
    """读取「某租户」对该插件的已同意权限集（A2 M6，2026-09-20）。

    判定顺序（新表权威 + 服务级回落）：
    1. ``tenant_id`` 非空：先读 ``plugin_consents(plugin_name=name, tenant_id)``，命中即返回；
    2. 未命中，且该插件为**服务级**（``plugins.owner_tenant_id IS NULL``：内置/存量/未归户）
       → 回落到服务级 ``plugins.consented_permissions``（保持「内置插件全员放行」既有语义，
       否则内置插件会突然要求所有人重新同意）；
    3. 未命中，且插件**已归某家庭**（``owner_tenant_id`` 非 NULL）→ 返回空集（该租户必须自行同意）；
    4. ``tenant_id`` 为空（调用方给不出「调用者租户」，如市场安装既有调用点）→ 按服务级回落，
       与 M6 前逐字节一致（不因拿不到租户把所有人挡在同意页外）。

    读库异常 fail-open 为「无同意」（宁可多问一次，不可静默放行）。
    """
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin, PluginConsent
    _tid = None
    if tenant_id is not None:
        try:
            _tid = int(tenant_id)
        except (TypeError, ValueError):
            _tid = None
    async with async_session_factory() as db:
        if _tid is not None:
            try:
                row = (await db.execute(select(PluginConsent).where(
                    PluginConsent.plugin_name == name,
                    PluginConsent.tenant_id == _tid,
                ))).scalar_one_or_none()
            except Exception as e:  # 表未建/读失败 → 视为未命中（走回落）
                _logger.warning("插件 %s 租户 %s 同意读取失败: %s", name, _tid, e)
                row = None
            if row is not None:
                return _parse_perms(row.permissions_json)
        # 未命中 → 服务级回落规则
        try:
            plugin_row = (await db.execute(
                select(Plugin).where(Plugin.name == name)
            )).scalar_one_or_none()
        except Exception as e:  # 读失败 → 无同意（fail-open 到「需同意」）
            _logger.warning("插件 %s 服务级同意读取失败: %s", name, e)
            return []
        if plugin_row is None:
            return []  # 无插件行 → 无服务级同意
        if getattr(plugin_row, "owner_tenant_id", None) is None:
            return _parse_perms(getattr(plugin_row, "consented_permissions", "[]"))
        if _tid is None:
            # 未知调用者租户的既有调用点（市场安装）：保持改前服务级回落，避免把所有人挡在同意页外。
            return _parse_perms(getattr(plugin_row, "consented_permissions", "[]"))
        return []  # 已归户插件：别的租户的同意不生效


async def record_install_provenance(name: str, *, source: str, source_url: str | None = None,
                                    sha256: str | None = None,
                                    owner_user_id: int | None = None,
                                    owner_tenant_id: int | None = None) -> None:
    """安装/升级成功后记录来源（remote/local/builtin）+ 来源 url + sha256 实际计算值（3.9）
    + 安装者归属（A2 M1，2026-09-20）。

    归属口径：
    - ``owner_user_id`` = 本次安装的调用者账号；``owner_tenant_id`` 缺省时经
      ``family_service.get_family_root_id`` 解析其家庭根（同 session，失败不阻塞安装）；
    - **只在目标列为 NULL 时写入**：内置/存量/服务级（NULL）首次被某账号安装时落该账号，
      重复安装/重复同步绝不会把已有 owner 覆盖（尤其不得覆盖成 NULL）；
    - 不传 owner_user_id（内置同步、存量调用点）→ 两列保持原值（新行即 NULL=服务级）。

    A2 M6（2026-09-20）：若本次安装带 owner_user_id（= 有安装者），把 ``consented_permissions``
    的兼容集按**本次安装者家庭根**写进 ``plugin_consents``（仅在该租户尚无行时；本地路径的
    ``grant_plugin_consent`` 已精确写入，避免用兼容列覆盖/合并出越权放行）。这样远程市场/内置
    市场安装（未透传调用者租户的既有调用点）也能落租户级同意行。
    """
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            row = Plugin(name=name, version="0.0.1", description="", source=source)
            db.add(row)
            await db.flush()
        if source is not None:
            row.source = source
        if source_url is not None:
            row.source_url = source_url
        if sha256 is not None:
            row.sha256 = sha256
        # A2 M1：安装者归属（只在 NULL 时落，不覆盖已有 owner；拿不到就留 NULL）
        _installer_tid = None  # 本次安装者的家庭根（M6 同意租户；与 owner 列是否被改写无关）
        _installer_uid = int(owner_user_id) if owner_user_id is not None else None
        if owner_user_id is not None:
            _uid = int(owner_user_id)
            _installer_tid = owner_tenant_id
            if _installer_tid is None:
                try:
                    from app.application.family_service import get_family_root_id
                    _installer_tid = await get_family_root_id(db, _uid)
                except Exception as _e:  # 家庭根不可用 → 留 NULL，不影响安装/来源记录
                    _logger.warning("插件 %s 归属家庭根解析失败: %s", name, _e)
                    _installer_tid = None
            if row.owner_user_id is None:
                row.owner_user_id = _uid
            if row.owner_tenant_id is None and _installer_tid is not None:
                row.owner_tenant_id = int(_installer_tid)
        # A2 M6：把已同意集按「本次安装者家庭根」落到 plugin_consents（新表权威；兼容列保留）。
        # tenant 取安装者（而非插件旧 owner）：家庭 B 升级/重装家庭 A 的插件时，同意必须记到 B。
        # 覆盖本地 zip / 远程市场 / 内置市场三条路径（它们都已带 owner_user_id 调用本函数）；
        # 内置同步路径（不传 owner_user_id、owner 保持 NULL）不写新表行（= 服务级）。
        # ``only_if_absent``：本地路径的 grant_plugin_consent 已按精确权限集写过本租户行，
        # 此处不重复合并兼容列（避免把别的家庭的服务级同意并进本租户，造成越权放行）。
        if _installer_tid is not None:
            _perms = _parse_perms(getattr(row, "consented_permissions", "[]"))
            if _perms:
                try:
                    await _upsert_plugin_consent(
                        db, name, int(_installer_tid), _perms,
                        getattr(row, "consented_at", None), _installer_uid,
                        only_if_absent=True,
                    )
                except Exception as _e:  # 同意落库失败不影响来源/归属记录（fail-open）
                    _logger.warning("插件 %s 同意按租户落库失败: %s", name, _e)
        await db.commit()
    _db_prov[name] = await get_plugin_provenance(name)


async def _upsert_plugin_consent(db, name: str, tenant_id: int, permissions: list[str],
                                  consented_at=None, consented_by: int | None = None,
                                  only_if_absent: bool = False) -> None:
    """同 session 幂等 upsert 一条「租户级同意」（A2 M6；不 commit，由调用方提交）。

    权限集按「∪ 历次同意」保序去重合并；``consented_at`` 缺省取当前 naive UTC（与库一致）。
    ``only_if_absent=True``：该租户已有行时**不改动**（供 ``record_install_provenance`` 用，
    避免把兼容列的全量并集并进本租户）。
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.plugin import PluginConsent
    _tid = int(tenant_id)
    _perms = [str(x) for x in (permissions or [])]
    _now = consented_at or datetime.now(timezone.utc).replace(tzinfo=None)
    row = (await db.execute(select(PluginConsent).where(
        PluginConsent.plugin_name == name,
        PluginConsent.tenant_id == _tid,
    ))).scalar_one_or_none()
    if row is None:
        db.add(PluginConsent(
            plugin_name=name,
            tenant_id=_tid,
            permissions_json=json.dumps(list(dict.fromkeys(_perms)), ensure_ascii=False),
            consented_at=_now,
            consented_by=int(consented_by) if consented_by else None,
        ))
        return
    if only_if_absent:
        return
    union = list(dict.fromkeys(_parse_perms(row.permissions_json) + _perms))  # 保序去重
    row.permissions_json = json.dumps(union, ensure_ascii=False)
    row.consented_at = _now
    if consented_by:
        row.consented_by = int(consented_by)


async def grant_plugin_consent(name: str, permissions: list[str], *,
                               tenant_id: int | None = None,
                               actor_user_id: int | None = None) -> None:
    """持久化同意：权限并入已同意集（保序去重）+ 更新同意时间（3.9）。

    A2 M6（2026-09-20）：``tenant_id`` 非空时同事务 upsert ``plugin_consents(plugin_name, tenant_id)``
    （租户级同意，新表权威）；``plugins.consented_permissions`` 的写入**保留**（兼容旧读点）。
    ``tenant_id`` 为空（拿不到调用者租户的既有调用点）→ 只写兼容列（不伪造租户）。
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            row = Plugin(name=name, version="0.0.1", description="")
            db.add(row)
            await db.flush()
        try:
            _existing = json.loads(row.consented_permissions or "[]")
            if not isinstance(_existing, list):
                _existing = []
        except Exception:
            _existing = []
        union = list(dict.fromkeys(_existing + list(permissions or [])))  # 保序去重
        row.consented_permissions = json.dumps(union, ensure_ascii=False)
        row.consented_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 与库一致的 naive UTC
        if tenant_id is not None:
            await _upsert_plugin_consent(
                db, name, int(tenant_id), list(permissions or []),
                row.consented_at, actor_user_id,
            )
        await db.commit()
    _db_prov[name] = await get_plugin_provenance(name)


async def backfill_plugin_consents_once() -> int:
    """存量一致性回填（A2 M6，2026-09-20）：``plugins.consented_permissions`` → ``plugin_consents``。

    - **一次性、幂等**：仅当 ``plugin_consents`` **为空**时执行；非空直接返回 0（重复跑 0 变更）；
    - 只回填 ``owner_tenant_id IS NOT NULL``（已归户）且 ``consented_permissions`` 非空的行，
      tenant_id 取「当时的安装租户」``owner_tenant_id``（family 根口径）；
    - ``owner_tenant_id IS NULL``（内置/存量/服务级）→ **不写新表行**，保持「内置插件全员放行」
      的既有语义（否则内置插件会突然要求所有人重新同意）；
    - 返回本次写入行数；读库/表缺失异常 → 0（fail-open，不阻塞启动同步）。
    """
    from sqlalchemy import func, select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin, PluginConsent
    async with async_session_factory() as db:
        try:
            n = int((await db.execute(
                select(func.count()).select_from(PluginConsent)
            )).scalar_one() or 0)
        except Exception as e:  # 表未建/读失败 → 本批 no-op（迁移建表后启动同步再回填）
            _logger.warning("plugin_consents 回填探测失败（表可能未建）: %s", e)
            return 0
        if n > 0:
            return 0  # 新表已有行 → 一次性回填已完成，重复跑 0 变更
        rows = (await db.execute(select(Plugin).where(
            Plugin.owner_tenant_id.isnot(None),
            Plugin.consented_permissions.isnot(None),
        ))).scalars().all()
        written = 0
        for r in rows:
            _perms = _parse_perms(r.consented_permissions)
            if not _perms:
                continue
            await _upsert_plugin_consent(
                db, r.name, int(r.owner_tenant_id), _perms, r.consented_at, r.owner_user_id,
            )
            written += 1
        if written:
            await db.commit()
        return written


def consent_state(manifest_permissions: list[str], stored_permissions: list[str]) -> tuple[str, list[str]]:
    """纯函数：判定同意是否需要。返回 ('empty'|'auto'|'required', needed_list)。

    ``stored_permissions`` 口径（A2 M6）：**调用者租户的已同意集**
    （``get_tenant_consented_permissions(name, caller_tenant_id)`` 的结果：新表命中，
    或按规则回落到服务级兼容列）。

    - empty：manifest 未声明权限 → 无需同意；
    - auto：声明权限 ⊆ 已同意集（升级未新增）→ 自动放行；
    - required：声明了未同意过的权限 → 需用户显式同意（needed 为完整清单）。
    """
    need = sorted(set(manifest_permissions or []))
    if not need:
        return "empty", []
    if set(need) <= set(stored_permissions or []):
        return "auto", []
    return "required", need


def consent_matches(manifest_permissions: list[str], consent: bool, provided_permissions: list[str]) -> bool:
    """同意请求的权限必须与 manifest 实际声明完全一致，否则视为未同意（3.9）。"""
    need = sorted(set(manifest_permissions or []))
    return bool(consent) and sorted(set(provided_permissions or [])) == need


async def resolve_tenant_for_user(user_id: int | None) -> int | None:
    """解析调用者的「家庭根」租户 id（A2 M6；与 ``channel_bindings.tenant_id`` 同口径）。

    入口层（本地 zip 安装）据此把「调用者租户」传给 ``require_plugin_consent``；拿不到
    （无 user_id / 家庭根不可用 / 读库异常）→ None，由同意判定按「未知租户」回落，
    不阻塞安装路径。
    """
    if not user_id:
        return None
    try:
        from app.application.family_service import get_family_root_id
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            tid = await get_family_root_id(db, int(user_id))
        return int(tid) if tid is not None else None
    except Exception as e:
        _logger.warning("插件同意：调用者 %s 家庭根解析失败: %s", user_id, e)
        return None


async def require_plugin_consent(name: str, manifest_permissions: list[str], lang: str,
                                 *, consent: bool = False, provided_permissions: list[str] | None = None,
                                 tenant_id: int | None = None, actor_user_id: int | None = None) -> None:
    """安装/升级执行前的「权限同意」闸（3.9，只设在安装/升级入口，不破坏启动重扫）。

    无权限声明/升级未新增权限 → 直接放行；否则需请求携带 consent=true 且 permissions 与
    manifest 完全一致（不一致视为未同意）→ 记录并持久化同意；否则抛 HTTPException(400)
    返回所需权限清单，供前端弹确认框。

    A2 M6（2026-09-20）：判定读的是**调用者租户**的已同意集（``tenant_id``，由入口用
    ``resolve_tenant_for_user`` 解析后传入）；同意落库同时写 ``plugin_consents``（新表权威）
    与兼容列。``tenant_id=None``（既有市场安装调用点）按服务级回落，保持改前行为。
    插件侧 API 签名不变（同意校验全部在 registry 内部完成）。
    """
    from fastapi import HTTPException
    from app.i18n import tr_lang
    _state, _needed = consent_state(
        manifest_permissions, await get_tenant_consented_permissions(name, tenant_id)
    )
    if _state in ("empty", "auto"):
        return
    if consent_matches(manifest_permissions, consent, provided_permissions):
        await grant_plugin_consent(name, _needed, tenant_id=tenant_id, actor_user_id=actor_user_id)
        return
    raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_consent_required", perms=", ".join(_needed)))


def verify_plugin_signature(manifest: dict, payload: bytes, signature: str | None = None) -> bool:
    """预留：插件签名校验接口（AMBRACE 3.9 插件安全闸）。

    当前未接入签名/公钥体系，恒返回 True（不强制启用）。这是安装/加载校验层的扩展点：
    未来接入插件签名（如对 zip 的 signature 字段做公钥验签）后在此实现，校验失败返回 False，
    调用方据此拒绝安装/加载。文档见 docs/plugin-development.md「安全模型」。
    """
    return True


def plugin_user_scope_enabled() -> bool:
    """A2 M3（2026-09-20）：读 ``plugin_user_scope`` flag（默认关；延迟 import + 异常兜底 False）。

    flag 关 = 插件列表不做任何归属过滤（逐字节旧行为）。本函数是「可见性过滤是否生效」的
    唯一判定口：registry 与 api 层共用同一处判断，避免两处各读一次 flag 造成口径漂移。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("plugin_user_scope", False))
    except Exception:
        return False


def plugin_visible_to_tenant(*, source: str | None, owner_user_id: int | None,
                             owner_tenant_id: int | None,
                             viewer_tenant_id: int | None) -> bool:
    """A2 M3 纯谓词：插件对「某家庭根账号」是否可见（调用方负责 flag 门控）。

    可见 = ``source == "builtin"`` ∪ ``owner_tenant_id == 调用者家庭根``
          ∪ ``owner_user_id IS NULL``（存量/服务级；内置行 owner 两列也为 NULL）。

    ``viewer_tenant_id is None``（调用者家庭根解析失败 / 拿不到）→ **fail-closed**：
    只保留 ``builtin ∪ owner_user_id IS NULL`` 的「最保守集合」，绝不因为拿不到租户就全量放行。
    异常（脏数据/比较失败）同样 fail-closed 返回 False。
    """
    try:
        if str(source or "builtin") == "builtin":
            return True
        if owner_user_id is None:
            return True
        if viewer_tenant_id is None:
            return False
        return owner_tenant_id is not None and int(owner_tenant_id) == int(viewer_tenant_id)
    except (TypeError, ValueError):
        return False


# ── A2 M4（2026-09-20）：运行面按账号过滤（flag plugin_runtime_scope，默认关）──────────────
# 运行面（hook 分发 / 工具登记 / prompt 注入 / 桥与页面 / sdk 归属断言）共用 M3 的可见性谓词
# 与 30s 进程内缓存（仿 app/mcp/ownership.py 的 _CACHE 写法）；缓存**按调用者家庭根**失效。
_RUNTIME_SCOPE_TTL = 30.0
# viewer_tenant_id -> (monotonic_ts, frozenset[插件名])
_visible_names_cache: dict[int | None, tuple[float, frozenset[str]]] = {}
# user_id -> (monotonic_ts, 家庭根 | None)
_caller_tenant_cache: dict[int, tuple[float, int | None]] = {}
# 已告警过的「拿不到 caller」调用点（hook@callsite），避免刷屏
_warned_no_caller: set[str] = set()


def plugin_runtime_scope_enabled() -> bool:
    """A2 M4：读 ``plugin_runtime_scope`` flag（默认关；延迟 import + 异常兜底 False）。

    flag 关 = 运行面逐字节旧行为（hook 全量分发、工具全量登记、无 sdk 归属断言、桥/页面无归属闸）。
    """
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("plugin_runtime_scope", False))
    except Exception:
        return False


def plugin_is_builtin(name: str) -> bool:
    """插件是否内置（source=builtin；无来源记录的行按内置处理，与 M3 谓词口径一致）。"""
    return str((_db_prov.get(name) or {}).get("source") or "builtin") == "builtin"


def clear_runtime_scope_caches() -> None:
    """清空 M4 运行面缓存（插件重扫 / 测试隔离用；sdk 侧字符归属缓存由 sdk 自清）。"""
    _visible_names_cache.clear()
    _caller_tenant_cache.clear()
    _warned_no_caller.clear()


async def resolve_caller_tenant_cached(user_id: int | None) -> int | None:
    """解析调用者家庭根（30s 进程内缓存；无 user_id / 解析失败 → None）。"""
    if not user_id:
        return None
    uid = int(user_id)
    now = _time.monotonic()
    hit = _caller_tenant_cache.get(uid)
    if hit is not None and (now - hit[0]) < _RUNTIME_SCOPE_TTL:
        return hit[1]
    tid = await resolve_tenant_for_user(uid)
    _caller_tenant_cache[uid] = (now, tid)
    return tid


def _visible_plugin_names(viewer_tenant_id: int | None) -> frozenset[str]:
    """某家庭根可见的插件名集合（30s 缓存；复用 M3 纯谓词 :func:`plugin_visible_to_tenant`）。"""
    now = _time.monotonic()
    hit = _visible_names_cache.get(viewer_tenant_id)
    if hit is not None and (now - hit[0]) < _RUNTIME_SCOPE_TTL:
        return hit[1]
    names = frozenset(
        n for n in list(_loaded)
        if plugin_visible_to_tenant(
            source=(_db_prov.get(n) or {}).get("source", "builtin"),
            owner_user_id=(_db_prov.get(n) or {}).get("owner_user_id"),
            owner_tenant_id=(_db_prov.get(n) or {}).get("owner_tenant_id"),
            viewer_tenant_id=viewer_tenant_id,
        )
    )
    _visible_names_cache[viewer_tenant_id] = (now, names)
    return names


async def _runtime_scope_viewer(user_id: int | None, tenant_id: int | None) -> int | None:
    """本次运行面的调用者家庭根：显式 tenant_id 优先，否则按 user_id 走缓存解析。"""
    if tenant_id is not None:
        return int(tenant_id)
    return await resolve_caller_tenant_cached(user_id)


async def plugin_in_runtime_scope(name: str, *, user_id: int | None = None,
                                  tenant_id: int | None = None) -> bool:
    """flag 门控：该插件是否允许进入本次运行面（= 对该调用者可见）。

    - flag 关 → 恒 True（逐字节旧行为）；
    - flag 开 + 拿不到调用者家庭根 → **fail-closed**：只放行内置插件（绝不全放）；
    - flag 开 + 有家庭根 → 走 M3 谓词（内置 ∪ 本家庭安装 ∪ 服务级 owner 为空）。
    """
    if not plugin_runtime_scope_enabled():
        return True
    viewer = await _runtime_scope_viewer(user_id, tenant_id)
    if viewer is None:
        return plugin_is_builtin(name)
    return name in _visible_plugin_names(viewer)


async def plugin_visible_for_caller(name: str, user_id: int | None) -> bool:
    """flag 门控：桥/页面端点用——该插件对调用者是否可见（flag 关 → 恒 True = 旧行为）。"""
    return await plugin_in_runtime_scope(name, user_id=user_id)


def _warn_no_caller_once(hook_name: str, callsite: str) -> None:
    """「拿不到 caller」告警一次（按 hook@调用点去重，避免 hot path 刷屏）。"""
    key = f"{hook_name}@{callsite}"
    if key in _warned_no_caller:
        return
    _warned_no_caller.add(key)
    _logger.warning(
        "plugin_runtime_scope 开但 %s 拿不到调用者（callsite=%s）→ 只向内置插件分发 hook（fail-closed）",
        hook_name, callsite,
    )


async def _resolve_hook_scope(hook_name: str, *, user_id: int | None, tenant_id: int | None,
                              callsite: str) -> tuple[frozenset[str] | None, int | None]:
    """解析本次 hook 分发范围，返回 ``(allowed, viewer_tenant_id)``。

    ``allowed is None`` = 不过滤（flag 关，逐字节旧行为）；flag 开时为「对该调用者可见」的插件名集合；
    拿不到调用者 → fail-closed 只留内置插件，并按调用点告警一次。
    """
    if not plugin_runtime_scope_enabled():
        return None, None
    viewer = await _runtime_scope_viewer(user_id, tenant_id)
    if viewer is None:
        if any(not plugin_is_builtin(n) for n in list(_loaded)):
            _warn_no_caller_once(hook_name, callsite)
        return frozenset(n for n in list(_loaded) if plugin_is_builtin(n)), None
    return _visible_plugin_names(viewer), viewer


async def resolve_viewer_tenant(user_id: int | None) -> int | None:
    """A2 M3：flag 开时解析调用者家庭根，供列表/市场可见性过滤用；flag 关 → 不查库返回 None。

    ``list_plugins`` 是同步函数，无法 await ``get_family_root_id``；家庭根解析必须由异步入口层
    完成，再把结果作为 ``viewer_tenant_id`` 传入。flag 关时直接返回 None（既有行为零额外查库，
    调用方 ``list_plugins`` 也因 flag 关而跳过过滤）。
    """
    if not plugin_user_scope_enabled():
        return None
    return await resolve_tenant_for_user(user_id)


def list_plugins(viewer_user_id: int | None = None, *,
                 viewer_tenant_id: int | None = None) -> list[dict]:
    """合并 manifest 信息 + DB 状态（enabled/config）+ 来源/同意元数据（3.9），按名称排序。

    A2 M3（2026-09-20）可选可见性过滤（plugin_user_scope flag 门控）：
    - ``viewer_user_id is None``（默认，既有调用方）→ **零过滤，逐字节旧行为**；
    - ``viewer_user_id`` 非空且 flag 开 → 只返回该账号可见的插件（谓词见
      :func:`plugin_visible_to_tenant`）；
    - flag 关 → 无论传不传 viewer 都逐字节旧行为（全量列表）；
    - ``viewer_tenant_id`` = 调用者家庭根，由异步入口层 ``await resolve_viewer_tenant(user_id)``
      解析后传入；缺省 None 表示解析失败/未知 → fail-closed 到「内置 ∪ 服务级」。
    """
    _scope_on = viewer_user_id is not None and plugin_user_scope_enabled()
    out = []
    for name, entry in _loaded.items():
        info = dict(entry["info"])
        info["enabled"] = bool(_enabled.get(name, False))
        saved = _db_config.get(name, {})
        merged = dict(info.get("config", {}))
        merged.update(saved)
        info["config"] = merged
        prov = _db_prov.get(name, {})
        info["source"] = prov.get("source", info.get("source", "builtin"))
        info["source_url"] = prov.get("source_url")
        info["sha256"] = prov.get("sha256")
        info["consented_permissions"] = prov.get("consented_permissions", [])
        info["consented_at"] = prov.get("consented_at")
        if _scope_on and not plugin_visible_to_tenant(
            source=info["source"],
            owner_user_id=prov.get("owner_user_id"),
            owner_tenant_id=prov.get("owner_tenant_id"),
            viewer_tenant_id=viewer_tenant_id,
        ):
            continue  # A2 M3：非本家庭安装的非内置插件 → 不可见
        out.append(info)
    out.sort(key=lambda x: x["name"])
    return out


def get_plugin(name: str) -> dict | None:
    for p in list_plugins():
        if p["name"] == name:
            return p
    return None


async def set_plugin_state(name: str, enabled: bool | None = None, config: dict | None = None) -> dict | None:
    """更新插件启用状态/配置（DB + 内存缓存）"""
    if name not in _loaded:
        return None
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        row = (await db.execute(select(Plugin).where(Plugin.name == name))).scalar_one_or_none()
        if row is None:
            info = _loaded[name]["info"]
            row = Plugin(name=name, version=info["version"], description=info["description"],
                         author=info["author"], category=info["category"], enabled=False,
                         config_json="{}")
            db.add(row)
            await db.flush()
        if enabled is not None:
            row.enabled = enabled
        if config is not None:
            saved = {}
            try:
                saved = json.loads(row.config_json or "{}")
            except Exception:
                saved = {}
            saved.update(config)
            row.config_json = json.dumps(saved, ensure_ascii=False)
        await db.commit()
        await db.refresh(row)
        _enabled[name] = bool(row.enabled)
        try:
            _db_config[name] = json.loads(row.config_json or "{}")
        except Exception:
            _db_config[name] = {}
    return get_plugin(name)


def _hook_timeout(timeout: float | None) -> float:
    """解析 hook 超时：显式传入直接用；默认取配置 plugin_hook_timeout（1-60s 收敛）"""
    if timeout is None:
        try:
            from app.config import settings
            timeout = float(getattr(settings, "plugin_hook_timeout", 10.0))
        except Exception:
            timeout = 10.0
        timeout = max(1.0, min(60.0, timeout))
    return timeout


async def _call_hook_bounded(fn, ctx: dict, timeout: float, plugin: str = "", hook_name: str = ""):
    """执行单个 hook（超时门禁，2026-08-16 Phase A）：
    - 异步 hook：asyncio.wait_for 超时中断（任务取消）；
    - 同步 hook：丢默认线程池执行 + wait_for 超时（线程无法强杀，但主流程不再等待）；
    超时统一忽略返回值返回 None；非超时异常原样上抛（由调用方隔离）。"""
    _t0 = _time.monotonic()
    if inspect.iscoroutinefunction(fn):
        coro = fn(ctx)
    else:
        # A2 M4：同步 hook 在默认线程池执行——把当前 contextvars 一并带进线程，否则线程里的
        # sdk.get_config()/require_permission 读不到插件身份（等价于旧进程级 dict 的可见性）。
        _cctx = contextvars.copy_context()
        coro = asyncio.get_running_loop().run_in_executor(None, _cctx.run, fn, ctx)
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        _logger.warning(
            "插件 %s hook %s 超时（已耗时 %.1fs，限制 %ss）已中断并忽略返回值",
            plugin, hook_name, _time.monotonic() - _t0, timeout,
        )
        return None
    if inspect.isawaitable(result):
        try:
            return await asyncio.wait_for(result, timeout=timeout)
        except asyncio.TimeoutError:
            _logger.warning(
                "插件 %s hook %s 返回协程超时（已耗时 %.1fs，限制 %ss）已中断并忽略返回值",
                plugin, hook_name, _time.monotonic() - _t0, timeout,
            )
            return None
    return result


async def run_hook_collect(hook_name: str, ctx: dict, timeout: float | None = None, *,
                           user_id: int | None = None, tenant_id: int | None = None,
                           callsite: str | None = None) -> list[dict]:
    """分发 hook 并收集各插件返回值（异常隔离 + 超时门禁，不阻断主链路）；用于 proactive_candidate 等候选收集

    A2 M4（2026-09-20）：新增 caller 形参（``user_id`` / ``tenant_id``，默认 None = 该调用点拿不到调用者）。
    flag ``plugin_runtime_scope`` 开时只向「对该调用者可见」的插件分发（M3 可见性谓词 + 30s 缓存）；
    flag 开且拿不到 caller → **fail-closed**：只向内置插件分发，并按调用点告警一次。flag 关 = 逐字节旧行为。
    同时把 caller 写进插件上下文（``_sdk_ctx`` 的 user_id/tenant_id），供 sdk 归属断言读取。
    """
    results: list[dict] = []
    if not _loaded:
        return results
    allowed, viewer = await _resolve_hook_scope(
        hook_name, user_id=user_id, tenant_id=tenant_id, callsite=callsite or hook_name,
    )
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        if allowed is not None and name not in allowed:
            continue  # A2 M4：对本调用者不可见 → 不进入本次运行面
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _token = push_sdk_context(name, user_id=user_id, tenant_id=viewer)
            try:
                result = await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
                if result is not None:
                    results.append({"plugin": name, "result": result})
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                reset_sdk_context(_token)
    return results


async def run_hook(hook_name: str, ctx: dict, timeout: float | None = None, *,
                   user_id: int | None = None, tenant_id: int | None = None,
                   callsite: str | None = None) -> None:
    """分发 hook 到所有启用且注册了该 hook 的插件（异常隔离 + 超时门禁，不阻断主链路）

    A2 M4：caller 形参与可见性过滤口径同 :func:`run_hook_collect`（flag 门控，默认关 = 旧行为）。
    """
    if not _loaded:
        return
    allowed, viewer = await _resolve_hook_scope(
        hook_name, user_id=user_id, tenant_id=tenant_id, callsite=callsite or hook_name,
    )
    explicit_timeout = timeout  # 保留调用方显式传入（per-plugin 判断用，2026-08-16 修复死代码）
    timeout = _hook_timeout(timeout)
    for name, entry in list(_loaded.items()):
        funcs = entry.get("hooks", {}).get(hook_name)
        if not funcs or not _enabled.get(name, False):
            continue
        if allowed is not None and name not in allowed:
            continue  # A2 M4：对本调用者不可见 → 不进入本次运行面
        # per-plugin 超时（manifest.hook_timeout）：调用方未显式传时用插件自身配置，否则全局默认
        t = explicit_timeout if explicit_timeout is not None else _hook_timeout(entry.get("info", {}).get("hook_timeout"))
        for fn in funcs:
            _token = push_sdk_context(name, user_id=user_id, tenant_id=viewer)
            try:
                await _call_hook_bounded(fn, ctx, t, plugin=name, hook_name=hook_name)
            except Exception as e:
                _logger.warning("插件 %s hook %s 异常: %s", name, hook_name, e)
            finally:
                reset_sdk_context(_token)


def current_plugin_name() -> str | None:
    return (_sdk_ctx.get() or {}).get("current")


def mount_plugin_routers(app) -> None:
    """挂载各插件的 http_router 到 FastAPI app（lifespan 启动时 sync_plugins_db 后调用）"""
    for name, entry in list(_loaded.items()):
        r = entry.get("router")
        if r is not None:
            try:
                app.include_router(r)
                _logger.info("插件路由已挂载: /api/v1/plugins/%s", name)
            except Exception as e:
                _logger.warning("插件 %s 路由挂载失败: %s", name, e)

def preload_channels() -> int:
    """X5（2026-09-01）：仅加载 manifest 声明 channel 的渠道插件（main.py lifespan 在 init_db
    之前调用——渠道自有 ORM 模型随 main.py 加载注册进插件独立 plugin_metadata（T5），
    并由 load_plugin_dir 内联的 ensure 幂等建表）。
    正式加载仍由 sync_plugins_db 统一重扫（渠道注册为同源替换语义）。返回预加载数。"""
    count = 0
    for d in _scan_dir(EXAMPLE_DIR) + _scan_dir(USER_DIR):
        try:
            mf = json.loads((d / "manifest.json").read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not mf.get("channel"):
            continue
        if load_plugin_dir(d):
            count += 1
    return count


async def run_plugin_action(plugin: str, action: str, payload: dict, user_id: int | None = None) -> bool:
    """调用插件注册的 action（arbiter 执行插件自定义行为，如渠道评论回复）。

    异常隔离返回 False；未注册返回 False；payload 为候选 dict（含 social_event 等）。
    user_id 非空时按插件名映射能力 scope 做权限检查（ask/forbid 拒绝执行）。
    """
    if user_id is not None:
        try:
            from app.application import permission_service
            _scope = permission_service._plugin_scope(plugin)
            _mode = await permission_service.check_mode(user_id, _scope)
            if _mode != "allow":
                _logger.info("plugin action blocked plugin=%s scope=%s mode=%s", plugin, _scope, _mode)
                return False
        except Exception:
            pass
    entry = _loaded.get(plugin)
    fn = (entry or {}).get("actions", {}).get(action)
    if fn is None:
        _logger.warning("插件 %s 未注册 action %s", plugin, action)
        return False
    # A2 M4：把 caller 写进插件上下文（sdk 归属断言读它；tenant_id 由 sdk 按 user_id 懒解析）
    _token = push_sdk_context(plugin, user_id=user_id)
    try:
        result = fn(payload)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception as e:
        _logger.warning("插件 %s action %s 异常: %s", plugin, action, e)
        return False
    finally:
        reset_sdk_context(_token)
