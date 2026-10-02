"""插件目录扫描 / 动态加载 / 库表同步 / 安装溯源（A22 ④a 自 registry 下沉）。

- 本模块**禁止在顶层 import registry**：registry 具名重导出本模块的 12 个名字，顶层回指必成环。
- 对留在 registry 的名字（tests 的打桩锚点 ``_loaded`` / ``_enabled`` / ``_db_config`` /
  ``_db_prov`` / ``USER_DIR`` / ``EXAMPLE_DIR`` / ``_logger``，以及 ``push_sdk_context`` 等）
  一律在**函数体内** ``from app.plugins import registry as _reg`` 现取 ``_reg.<name>``：
  写成裸名或 import 期绑定都会让打在 registry 上的桩静默失效（测试变绿却扫真目录 / 写真库）。
"""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from app.plugins.manifest import load_manifest


def _scan_dir(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]


def resolve_plugin_dir(name: str) -> Path | None:
    """在 EXAMPLE_DIR 或 USER_DIR 中查找插件目录（含 manifest.json），返回 Path 或 None（48a 页面托管/卸载用）"""
    from app.plugins import registry as _reg
    for base in (_reg.EXAMPLE_DIR, _reg.USER_DIR):
        p = base / str(name or "")
        if p.is_dir() and (p / "manifest.json").is_file():
            return p
    return None


def _discard_partial_load(name: str | None) -> None:
    """清掉加载中断留下的占位条目（load_plugin_dir 失败分支调用）。

    2026-09-21：占位条目（``info={}``）没有 "name" 字段，会让后续 ``list_plugins()``
    在 ``out.sort(key=lambda x: x["name"])`` 抛 KeyError —— 进而让 claimed_categories()
    静默返回空集（插件策略包看起来没接管）、插件列表/市场页整体失败。
    """
    from app.plugins import registry as _reg
    if not name:
        return
    entry = _reg._loaded.get(name)
    if entry and not (entry.get("info") or {}).get("name"):
        _reg._loaded.pop(name, None)
        _reg._enabled.pop(name, None)
        sys.modules.pop(f"ai_plugin_{name}", None)


def load_plugin_dir(path: Path) -> dict | None:
    """加载单个插件目录，返回 info dict；失败返回 None"""
    from app.plugins import registry as _reg
    try:
        manifest = load_manifest(str(path / "manifest.json"))
        if manifest is None:
            _reg._logger.warning("插件 %s manifest 无效，跳过", path.name)
            return None
        name = manifest["name"]
        plugin_type = str(manifest.get("type", "http") or "http").strip()  # 48c：缺省 http
        main_py = path / "main.py"
        module = None
        if main_py.is_file():
            module_name = f"ai_plugin_{name}"
            spec = importlib.util.spec_from_file_location(module_name, str(main_py))
            if spec is None or spec.loader is None:
                _reg._logger.warning("插件 %s 无法创建 spec，跳过", name)
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            # 先占位再执行 main.py（其中 sdk.hook 注册到本插件名下，避免 KeyError）
            _reg._loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
            _token = _reg.push_sdk_context(name)
            try:
                spec.loader.exec_module(module)
            finally:
                _reg.reset_sdk_context(_token)
        else:
            # 48c：config-only 加载——无 main.py 但 type ∈ prompt/chat/workflow/hybrid/content 时仍加载 info
            #      （hybrid 页面插件（48a）亦无需 main.py，page 资源由页面托管端点直读磁盘；
            #       X2：content 内容包零代码，数据来自 manifest.content，经 validate_manifest 校验）
            if plugin_type not in ("prompt", "chat", "workflow", "hybrid", "content"):
                _reg._logger.warning("插件 %s 缺 main.py 且非配置型(type=%s)，跳过", name, plugin_type)
                return None
            _reg._loaded[name] = {"info": {}, "module": None, "hooks": {}, "actions": {}, "router": None}
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
        _reg._loaded[name] = {"info": info, "module": module, "hooks": _reg._loaded[name].get("hooks", {}), "actions": _reg._loaded[name].get("actions", {}), "router": _reg._loaded[name].get("router")}
        # ── T5（2026-09-10）：该插件若注册了 ORM 表，加载即幂等建表 ──────────────
        # checkfirst=True，失败只告警、不影响加载成功（测试/热安装路径的建表保障：
        # 插件表已不在主 Base.metadata，init_db 的 create_all 不再顺带建）。
        try:
            _ensure_plugin_tables_sync()
        except Exception as _e:
            _reg._logger.warning("post-load ensure plugin tables failed (%s): %s", name, _e)
        return info
    except Exception as e:
        _reg._logger.warning("插件 %s 加载失败: %s", path.name, e)
        # 2026-09-21 修复（xdist 串味根因）：加载失败**不得留下半成品条目**——旧实现直接 return None，
        # 占位条目（info={}）留在 _loaded 里没有 "name"，之后任何 list_plugins() 都会 KeyError('name')，
        # 连锁把 claimed_categories() 打成空集 + 插件列表整体失败。
        _discard_partial_load(locals().get("name"))
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
    from app.plugins import registry as _reg
    try:
        from app.plugins.plugin_base import plugin_metadata
    except Exception as e:  # pragma: no cover - 防御
        _reg._logger.warning("plugin_metadata import failed: %s", e)
        return []
    if not plugin_metadata.tables:
        return []  # 尚无插件注册任何表（config-only 插件 / 未加载渠道）→ no-op
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        from app.db.migrate import _sync_url  # 复用现成的「去 +aiosqlite / +asyncpg」同步 URL 推导
        url = _sync_url()
    except Exception as e:
        _reg._logger.warning("resolve plugin db url failed: %s", e)
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
                _reg._logger.info(
                    "插件表 schema 收敛：补列 %s；补索引 %s", added_cols, added_idx
                )
            else:
                # 0 操作也留痕：否则线上分不清「跑过没问题」和「根本没执行」
                _reg._logger.info(
                    "插件表 schema 收敛：无缺失（%d 张表，补列 0、补索引 0）", len(names)
                )
        finally:
            eng.dispose()
    except Exception as e:
        _reg._logger.warning("ensure plugin tables failed (%s): %s", names, e)
        return []
    return names


async def ensure_plugin_tables() -> list[str]:
    """lifespan 调用：线程池中幂等建立插件表（不阻塞事件循环）。"""
    return await asyncio.to_thread(_ensure_plugin_tables_sync)


async def sync_plugins_db() -> None:
    """扫描插件目录并同步到 plugins 表（幂等 upsert）；刷新启用/配置缓存"""
    from app.plugins import registry as _reg
    _reg._loaded.clear()
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
    _reg._enabled.clear()
    _reg._db_config.clear()
    _reg._db_prov.clear()
    # A2 M4：重扫后可见集/来源全变 → 清运行面缓存（否则 30s 内仍按旧归属过滤）
    _reg.clear_runtime_scope_caches()
    # (name, source) 对：示例目录=builtin，用户目录=local（新建行以此落 source；存量行保留已记录来源）
    seen: list[tuple[str, str]] = []
    for _base, _src in ((_reg.EXAMPLE_DIR, "builtin"), (_reg.USER_DIR, "local")):
        for d in _scan_dir(_base):
            info = _reg.load_plugin_dir(d)
            if info:
                seen.append((info["name"], _src))
    if not seen:
        # A2 M6：无插件目录也不能漏掉存量同意回填（一次性、幂等、新表为空才执行）
        try:
            _n = await _reg.backfill_plugin_consents_once()
            if _n:
                _reg._logger.info("插件同意回填（无目录分支）：%d 行", _n)
        except Exception as _e:
            _reg._logger.warning("插件同意回填失败（无目录分支）: %s", _e)
        return
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin
    async with async_session_factory() as db:
        rows = (await db.execute(select(Plugin))).scalars().all()
        by_name = {r.name: r for r in rows}
        for name, _src in seen:
            info = _reg._loaded[name]["info"]
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
            _reg._enabled[r.name] = bool(r.enabled)
            try:
                _reg._db_config[r.name] = json.loads(r.config_json or "{}")
            except Exception:
                _reg._db_config[r.name] = {}
            _reg._db_prov[r.name] = _row_prov(r)
    # A2 M6：存量一致性回填（一次性、幂等、只在新表为空时；owner NULL 的存量行不写）
    try:
        _n = await _reg.backfill_plugin_consents_once()
        if _n:
            _reg._logger.info("插件同意回填：%d 行（plugins.consented_permissions → plugin_consents）", _n)
    except Exception as _e:
        _reg._logger.warning("插件同意回填失败: %s", _e)
    _reg._logger.info("插件扫描完成：%d 个插件", len(seen))


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
    from app.plugins import registry as _reg
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
                    _reg._logger.warning("插件 %s 归属家庭根解析失败: %s", name, _e)
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
            _perms = _reg._parse_perms(getattr(row, "consented_permissions", "[]"))
            if _perms:
                try:
                    await _reg._upsert_plugin_consent(
                        db, name, int(_installer_tid), _perms,
                        getattr(row, "consented_at", None), _installer_uid,
                        only_if_absent=True,
                    )
                except Exception as _e:  # 同意落库失败不影响来源/归属记录（fail-open）
                    _reg._logger.warning("插件 %s 同意按租户落库失败: %s", name, _e)
        await db.commit()
    _reg._db_prov[name] = await get_plugin_provenance(name)
