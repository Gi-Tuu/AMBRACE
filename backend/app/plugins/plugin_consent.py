"""插件同意（consent）与能力权限判定（A22 第九刀 · ④b，2026-10-03 自 `plugins/registry.py` 逐字节搬入）。

边界：本模块只管「插件声明了哪些权限 / 某租户同意过哪些 / 还需不需要同意 / 能力位是否放行」，
以及安装期的签名扩展点。**插件扫描、加载、库表同步与安装溯源在 `plugin_store.py`（④a）**；
租户可见性与运行时作用域（`plugin_*_enabled` / `plugin_visible_*` / hook scope）在 `plugin_scope.py`（④c）；
本模块与之的接缝只有一条：`resolve_tenant_for_user` 由 plugin_scope 经 `_reg.` 现取（registry 重导出）。

搬家口径（与 A20／A22 前八刀一致，由 `tests/test_arbiter_seam.py` 的接缝守卫钉住）：
- 函数体**逐字节照搬**：不改签名、不合并相似逻辑（`get_plugin_consented_permissions` 与
  `get_tenant_consented_permissions` 的回落口径不同，禁止顺手合并）；
- **R2**：引用仍留在 registry 的名字（`_logger` / `_db_prov` / `get_plugin_provenance`）一律在**函数体内**
  `from app.plugins import registry as _reg` 后写 `_reg.<name>`——tests 里
  `setattr(registry, "_logger", …)` 2 处、`setattr(registry, "_db_prov", …)` 5 处打的是 registry 上的名字；
- **R3（本刀第一次踩过的坑）**：**同批搬走**的函数互相调用**也要**走 `_reg.<name>`。
  搬家前两者同居 registry、经 registry 全局解析，`setattr(registry, "get_tenant_consented_permissions", …)`
  能生效；若新模块里写成裸名，就会解析到 plugin_consent 自己的那份 ⇒ 桩静默失效
  （实测被 `test_plugin_capability_notes_m0.py` 两条用例当场抓出）；
- registry 侧**具名重导出**本模块 12 个名字，`registry.<name>` 的既有调用点
  （`api/marketplace.py` / `api/plugins.py` / `plugin_store.py` 的 `_reg.` 回指）一律不变。
"""
import json

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
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
    if tenant_id is not None:
        return await _reg.get_tenant_consented_permissions(name, tenant_id)
    return list((await _reg.get_plugin_provenance(name)).get("consented_permissions", []))


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
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
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
                _reg._logger.warning("插件 %s 租户 %s 同意读取失败: %s", name, _tid, e)
                row = None
            if row is not None:
                return _reg._parse_perms(row.permissions_json)
        # 未命中 → 服务级回落规则
        try:
            plugin_row = (await db.execute(
                select(Plugin).where(Plugin.name == name)
            )).scalar_one_or_none()
        except Exception as e:  # 读失败 → 无同意（fail-open 到「需同意」）
            _reg._logger.warning("插件 %s 服务级同意读取失败: %s", name, e)
            return []
        if plugin_row is None:
            return []  # 无插件行 → 无服务级同意
        if getattr(plugin_row, "owner_tenant_id", None) is None:
            return _reg._parse_perms(getattr(plugin_row, "consented_permissions", "[]"))
        if _tid is None:
            # 未知调用者租户的既有调用点（市场安装）：保持改前服务级回落，避免把所有人挡在同意页外。
            return _reg._parse_perms(getattr(plugin_row, "consented_permissions", "[]"))
        return []  # 已归户插件：别的租户的同意不生效


async def has_capability_permission(plugin_name: str, tenant_id: int | None,
                                    capability_id: str) -> bool:
    """能力级权限只读判定（X7-M3，2026-09-22）：插件在某租户下**是否已同意**该设备能力。

    fail-closed：下列条件**全部**成立才返回 True，任一不成立即 False（且绝不抛）：
    1. ``capability_id`` 是已登记能力（``app.device.capabilities``；未知 id → False）；
    2. 插件**已安装**（``plugins`` 表有行，即已装/已同步到库）且**未停用**（``enabled`` 为真）；
       这里以库中行为准（``set_plugin_state`` 先写库再刷内存缓存，库是持久权威）；
    3. 拿得到**具体租户**（``tenant_id`` 为空或非整数 → False）。能力级授权不采用
       「未知调用者走服务级回落」那条既有兼容路径：拿不到租户就拒绝，宁可多拒不可误放；
    4. 该租户的**已同意集**含该能力对应的权限名 ``device:<capability>:read``——已同意集取既有
       口径 :func:`get_tenant_consented_permissions`（``plugin_consents`` 新表权威 + 内置/服务级
       插件的服务级回落），与安装期同意判定同一份口径，本函数既不另立一套也不放宽它。

    只读查询：不写库、不改任何既有函数签名与语义。放行与拒绝**各记一条 INFO 审计日志**，
    字段固定 ``plugin=<name> tenant=<id> capability=<id> allowed=<true|false>``。
    """
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.device.capabilities import get_capability
    from app.models.plugin import Plugin

    _spec = get_capability(capability_id)
    _tid: int | None = None
    if tenant_id is not None:
        try:
            _tid = int(tenant_id)
        except (TypeError, ValueError):
            _tid = None
    _allowed = False
    if _spec is not None and _tid is not None:
        try:
            async with async_session_factory() as db:
                _row = (await db.execute(
                    select(Plugin).where(Plugin.name == plugin_name)
                )).scalar_one_or_none()
        except Exception as e:  # 读库失败 → 按「未安装」处理（fail-closed）
            _reg._logger.warning("插件 %s 能力级权限判定读插件行失败: %s", plugin_name, e)
            _row = None
        if _row is not None and bool(_row.enabled):
            _perms = await _reg.get_tenant_consented_permissions(plugin_name, _tid)
            _allowed = _spec.permission in _perms
    _reg._logger.info("plugin=%s tenant=%s capability=%s allowed=%s",
                 plugin_name, tenant_id, capability_id, "true" if _allowed else "false")
    # 批 8 块 B（2026-10-01）：**只记不判** —— 留痕不得改变返回值（异常一律吞掉）。
    # 拒绝原因分级与上面 INFO 同口径，供 capability-audit 做「声明 vs 事实」对账。
    # 用 obs_event_now（本轮内写完）而不是 obs_event（fire-and-forget）：本函数会被裸 TestClient /
    # 一次性 asyncio.run 调用，后台写会在 loop 关闭后回灌成 PytestUnhandledThreadExceptionWarning。
    try:
        from app.memory.observability import obs_event_now

        if _allowed:
            _reason = "consented"
        elif _spec is None:
            _reason = "unknown_capability"
        elif _tid is None:
            _reason = "no_tenant"
        else:
            _row_ref = locals().get("_row")
            if _row_ref is None:
                _reason = "not_installed"
            elif not bool(getattr(_row_ref, "enabled", False)):
                _reason = "disabled"
            else:
                _reason = "not_consented"
        await obs_event_now(None, "plugin_capability", {
            "plugin": plugin_name,
            "permission": (_spec.permission if _spec is not None else str(capability_id)),
            "capability": str(capability_id),
            "decision": "allow" if _allowed else "deny",
            "reason": _reason,
        })
    except Exception:
        pass
    return _allowed


async def _upsert_plugin_consent(db, name: str, tenant_id: int, permissions: list[str],
                                  consented_at=None, consented_by: int | None = None,
                                  only_if_absent: bool = False) -> None:
    """同 session 幂等 upsert 一条「租户级同意」（A2 M6；不 commit，由调用方提交）。

    权限集按「∪ 历次同意」保序去重合并；``consented_at`` 缺省取当前 naive UTC（与库一致）。
    ``only_if_absent=True``：该租户已有行时**不改动**（供 ``record_install_provenance`` 用，
    避免把兼容列的全量并集并进本租户）。
    """
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
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
    union = list(dict.fromkeys(_reg._parse_perms(row.permissions_json) + _perms))  # 保序去重
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
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
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
            await _reg._upsert_plugin_consent(
                db, name, int(tenant_id), list(permissions or []),
                row.consented_at, actor_user_id,
            )
        await db.commit()
    _reg._db_prov[name] = await _reg.get_plugin_provenance(name)


async def backfill_plugin_consents_once() -> int:
    """存量一致性回填（A2 M6，2026-09-20）：``plugins.consented_permissions`` → ``plugin_consents``。

    - **一次性、幂等**：仅当 ``plugin_consents`` **为空**时执行；非空直接返回 0（重复跑 0 变更）；
    - 只回填 ``owner_tenant_id IS NOT NULL``（已归户）且 ``consented_permissions`` 非空的行，
      tenant_id 取「当时的安装租户」``owner_tenant_id``（family 根口径）；
    - ``owner_tenant_id IS NULL``（内置/存量/服务级）→ **不写新表行**，保持「内置插件全员放行」
      的既有语义（否则内置插件会突然要求所有人重新同意）；
    - 返回本次写入行数；读库/表缺失异常 → 0（fail-open，不阻塞启动同步）。
    """
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
    from sqlalchemy import func, select
    from app.db.database import async_session_factory
    from app.models.plugin import Plugin, PluginConsent
    async with async_session_factory() as db:
        try:
            n = int((await db.execute(
                select(func.count()).select_from(PluginConsent)
            )).scalar_one() or 0)
        except Exception as e:  # 表未建/读失败 → 本批 no-op（迁移建表后启动同步再回填）
            _reg._logger.warning("plugin_consents 回填探测失败（表可能未建）: %s", e)
            return 0
        if n > 0:
            return 0  # 新表已有行 → 一次性回填已完成，重复跑 0 变更
        rows = (await db.execute(select(Plugin).where(
            Plugin.owner_tenant_id.isnot(None),
            Plugin.consented_permissions.isnot(None),
        ))).scalars().all()
        written = 0
        for r in rows:
            _perms = _reg._parse_perms(r.consented_permissions)
            if not _perms:
                continue
            await _reg._upsert_plugin_consent(
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
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
    if not user_id:
        return None
    try:
        from app.application.family_service import get_family_root_id
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            tid = await get_family_root_id(db, int(user_id))
        return int(tid) if tid is not None else None
    except Exception as e:
        _reg._logger.warning("插件同意：调用者 %s 家庭根解析失败: %s", user_id, e)
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
    from app.plugins import registry as _reg   # A22 ④b：留原模块与同批搬走的名字一律调用时刻现取
    from fastapi import HTTPException
    from app.i18n import tr_lang
    _state, _needed = _reg.consent_state(
        manifest_permissions, await _reg.get_tenant_consented_permissions(name, tenant_id)
    )
    if _state in ("empty", "auto"):
        return
    if _reg.consent_matches(manifest_permissions, consent, provided_permissions):
        await _reg.grant_plugin_consent(name, _needed, tenant_id=tenant_id, actor_user_id=actor_user_id)
        return
    raise HTTPException(status_code=400, detail=tr_lang(lang, "plugin_consent_required", perms=", ".join(_needed)))


def verify_plugin_signature(manifest: dict, payload: bytes, signature: str | None = None) -> bool:
    """预留：插件签名校验接口（AMBRACE 3.9 插件安全闸）。

    当前未接入签名/公钥体系，恒返回 True（不强制启用）。这是安装/加载校验层的扩展点：
    未来接入插件签名（如对 zip 的 signature 字段做公钥验签）后在此实现，校验失败返回 False，
    调用方据此拒绝安装/加载。文档见 docs/plugin-development.md「安全模型」。
    """
    return True
