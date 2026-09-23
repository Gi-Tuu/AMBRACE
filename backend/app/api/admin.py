"""主账号管理 API（#68 修订：按家庭范围隔离，2026-08-28）

- 主账号 = users.is_admin=1；账号关联通过 users.parent_id 建立家庭关系。
- 主账号只能查看和管理自己家庭内（自己 + 直属子账号）的账号，不能看到其他家庭。
- 判定统一走 app.application.permission_service.is_admin_user（DB 权威 + 30s 缓存，env 兜底）。

账号独立 P1（2026-09-19）：家庭管理（is_admin）与服务器控制台（server_admin）分离。
本文件新增服务器控制台最小件（仅 require_server_admin 可访问）：
- GET  /api/v1/admin/server/accounts：跨家庭账号清单；
- PUT  /api/v1/admin/server/accounts/{id}/server-admin：授予/取消服务器控制台管理员
  （不可取消最后一个，防把控制台锁死）。

账号独立 P2（2026-09-19，契约 §1）：在既有 server/accounts 旁**扩展**控制台管理面（不另起一套）：
- 账号：GET /server/accounts（新增 disabled_at/llm_mode/last_login_at）、
  PUT /server/accounts/{id}/disabled、PUT /server/accounts/{id}/llm-mode、
  PUT /server/accounts/{id}/server-admin（既有，补审计）；
- 默认模型：GET/PUT /server/modalities[/{key}]（四模态唯一出口 llm_config_service）；
- 开关与权限：GET/PUT /server/flags[/{key}]（flag_service + flag_settings 策略）；
- 注册策略：GET/PUT /server/registration（server_settings KV）；
- 审计：GET /server/audit；概览：GET /server/overview。

A8 LLM 额度按账号（2026-09-20）：额度从单行全局扩成「全局默认 + 账号覆盖」——
- GET /server/accounts 每账号附 llm_total_limit（生效值）/ llm_total_limit_own（覆盖值或 null）/
  llm_total_limit_source（user/global/unset）；
- PUT /server/accounts/{id}/llm-limit（null = 清除覆盖回落全局）；
- GET/PUT /server/llm-limit（服务器默认额度，全局 id=1 行）。
生效口径（覆盖 > 全局 > 未设置）与读写一律经 app/application/llm_quota.py（API 不直连额度表）。
铁律（契约 §0）：控制台只调 HTTP API，本文件一律经服务层读写，控制台不得直连 DB；
所有写动作落 admin_audit_log；api_key 永不回传明文（只回 has_api_key）。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import func, select

from app.application.admin_audit_service import list_entries
from app.application.admin_audit_service import record as _audit_record
from app.auth.deps import get_current_user_id, require_server_admin
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.user import User
from app.application.family_service import get_family_member_ids
from app.application import llm_quota
from app.application.permission_service import (
    DEFAULT_LLM_MODE,
    LLM_MODES,
    _invalidate_account_state_cache,
    _invalidate_admin_cache,
    _invalidate_server_admin_cache,
    is_admin_user,
)
from app.utils.logger import get_logger
from app.utils.version import get_project_version

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])
_logger = get_logger("api.admin")


@router.get("/accounts")
async def list_accounts(
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出当前用户家庭内的账号 {id, username, nickname, avatar_url, is_admin, parent_id, is_self}。

    独立主账号只看到自己；有子账号的主账号看到自己 + 直属子账号。
    """
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))

    async with async_session_factory() as db:
        member_ids = await get_family_member_ids(db, user_id)
        rows = (
            await db.execute(
                select(
                    User.id,
                    User.username,
                    User.nickname,
                    User.avatar_url,
                    User.is_admin,
                    User.parent_id,
                )
                .where(User.id.in_(member_ids))
                .order_by(User.id)
            )
        ).all()

    return {
        "accounts": [
            {
                "id": r.id,
                "username": r.username,
                "nickname": r.nickname,
                "avatar_url": r.avatar_url,
                "is_admin": bool(r.is_admin),
                "parent_id": r.parent_id,
                "is_self": r.id == user_id,
            }
            for r in rows
        ]
    }


@router.put("/accounts/{target_user_id}/admin")
async def set_account_admin(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """设置/取消主账号（仅主账号，目标必须在同一家庭内）。

    护栏：
    - 不能操作自己（主账号自身始终是 admin）；
    - 目标必须在当前用户的家庭成员列表内；
    - 子账号可以被授予/取消 admin（主账号决定子账号能否使用管理功能）；
    - 家庭内至少保留一个 admin（取消最后一个 → 400）。
    """
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_enabled_invalid"))
    enabled = bool(body["enabled"])

    # 不能操作自己
    if target_user_id == user_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_cannot_toggle_self"))

    async with async_session_factory() as db:
        # 目标必须在同一家庭内
        member_ids = await get_family_member_ids(db, user_id)
        if target_user_id not in member_ids:
            raise HTTPException(status_code=403, detail=tr_lang(lang, "admin_target_not_in_family"))

        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))

        if enabled:
            target.is_admin = True
        else:
            # 家庭内至少保留一个 admin（主账号自己）
            admin_ids = (
                await db.execute(
                    select(User.id).where(
                        User.is_admin.is_(True),
                        User.id.in_(member_ids),
                    )
                )
            ).scalars().all()
            if target_user_id in admin_ids and len(admin_ids) <= 1:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_keep_one"))
            target.is_admin = False

        await db.commit()

    _invalidate_admin_cache()
    _logger.info("admin account set user=%d enabled=%s by=%d", target_user_id, enabled, user_id)
    return {"status": "ok", "user_id": target_user_id, "is_admin": enabled}


# ── 服务器控制台最小件（账号独立 P1）────────────────────────────────────────────
# is_admin = 家庭主账号（家庭内）；server_admin = 服务器控制台管理员（跨家庭）。
# 依赖 require_server_admin（app/auth/deps.py）→ users.server_admin。


@router.get("/server/accounts")
async def list_server_accounts(
    user_id: int = Depends(require_server_admin),
):
    """服务器控制台账号清单（跨家庭，只读）：全部账号 + 归属关系 + 两级管理标记 + P2 门禁字段。

    P2 扩展（契约 §1.1）：新增 ``disabled_at``（ISO8601 或 null）、``llm_mode``
    （own/default_allowed/blocked）、``last_login_at``。只读、不返回 password_hash。

    ``last_login_at``：契约 §1.1 列出该字段但 §2 数据层未新增同名列（users 现无登录时间列），
    故**恒返回 null**（契约未覆盖，我这么做：不加契约未要求的列，UI 按可空处理）。

    A8（2026-09-20）新增三个额度字段（既有字段一个不少、不改名）：``llm_total_limit``
    （生效值）、``llm_total_limit_own``（账号覆盖值，无覆盖 = null）、
    ``llm_total_limit_source``（user/global/unset）。覆盖值走 llm_quota 一次批量读（不 N+1）。
    """
    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(
                    User.id, User.username, User.nickname, User.avatar_url,
                    User.is_admin, User.server_admin, User.parent_id,
                    User.disabled_at, User.llm_mode,
                ).order_by(User.id)
            )
        ).all()
    overrides = await llm_quota.get_user_overrides([r.id for r in rows])
    global_limit = await llm_quota.get_global_limit()
    accounts = []
    for r in rows:
        _own = overrides.get(r.id)
        _total, _source = _llm_effective(_own, global_limit)
        accounts.append(
            {
                "id": r.id,
                "username": r.username,
                "nickname": r.nickname,
                "avatar_url": r.avatar_url,
                "is_admin": bool(r.is_admin),
                "server_admin": bool(r.server_admin),
                "parent_id": r.parent_id,
                "is_self": r.id == user_id,
                "disabled_at": r.disabled_at.isoformat() if r.disabled_at else None,
                "llm_mode": r.llm_mode or DEFAULT_LLM_MODE,
                "last_login_at": None,
                "llm_total_limit": _total,
                "llm_total_limit_own": _own,
                "llm_total_limit_source": _source,
            }
        )
    return {"accounts": accounts}


def _llm_effective(own: int | None, global_limit: int) -> tuple[int, str]:
    """账号生效额度（覆盖 > 全局 > 未设置）→ ``(total_limit, source)``。

    与 ``llm_quota.resolve_limit`` 同口径（此处是批量版：全局值由调用方读一次后传入，
    避免账号清单里每个账号各读一次全局行）。
    """
    if own is not None:
        return int(own), "user"
    if int(global_limit or 0) > 0:
        return int(global_limit), "global"
    return 0, "unset"


@router.put("/server/accounts/{target_user_id}/server-admin")
async def set_account_server_admin(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """授予/取消服务器控制台管理员（仅 server_admin）。

    护栏：不能取消自己（避免误操作把自己踢出控制台）；不可取消最后一个 server_admin
    （否则控制台再也进不去，只能改库）。
    """
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_enabled_invalid"))
    enabled = bool(body["enabled"])

    if not enabled and target_user_id == user_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_cannot_toggle_self"))

    async with async_session_factory() as db:
        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))

        if not enabled:
            n = (await db.execute(
                select(func.count()).select_from(User).where(User.server_admin.is_(True))
            )).scalar_one()
            if target.server_admin and int(n) <= 1:
                raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_keep_one"))

        _before = {"server_admin": bool(target.server_admin)}
        target.server_admin = enabled
        await _audit_record(db, user_id, "account.server_admin", "user:%d" % target_user_id,
                            _before, {"server_admin": enabled})
        await db.commit()

    _invalidate_server_admin_cache()
    _logger.info("server admin set user=%d enabled=%s by=%d", target_user_id, enabled, user_id)
    return {"status": "ok", "user_id": target_user_id, "server_admin": enabled}


# ── 账号门禁：禁用 / 模型来源策略（账号独立 P2，契约 §1.1 + §4）──────────────────

@router.put("/server/accounts/{target_user_id}/disabled")
async def set_account_disabled(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """启用/禁用账号：禁用写当前 UTC naive 时间，启用置 NULL。

    护栏：不可禁用自己（避免把控制台操作者本身踢下线）。
    生效点：登录 403 + 后续请求在 get_current_user_id 阶段 403（30s 短缓存，见 permission_service）。
    """
    if "disabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "disabled_invalid"))
    disabled = bool(body.get("disabled"))
    if disabled and target_user_id == user_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "account_cannot_disable_self"))

    async with async_session_factory() as db:
        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))
        _before = {"disabled_at": target.disabled_at.isoformat() if target.disabled_at else None}
        target.disabled_at = datetime.now(timezone.utc).replace(tzinfo=None) if disabled else None
        _after = {"disabled_at": target.disabled_at.isoformat() if target.disabled_at else None}
        await _audit_record(db, user_id, "account.disable" if disabled else "account.enable",
                            "user:%d" % target_user_id, _before, _after)
        await db.commit()

    _invalidate_account_state_cache(target_user_id)
    _logger.info("account disabled=%s user=%d by=%d", disabled, target_user_id, user_id)
    return {"status": "ok", "user_id": target_user_id, "disabled": disabled,
            "disabled_at": _after["disabled_at"]}


@router.put("/server/accounts/{target_user_id}/llm-mode")
async def set_account_llm_mode(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """设置账号模型来源策略（契约 §1.1）：``own`` / ``default_allowed`` / ``blocked``；非法值 400。

    生效点在四模态唯一出口 ``llm_config_service.resolve_modality_config``（契约 §4）：
    blocked → 403；own → 不回落服务器默认；default_allowed（默认）→ 现状行为。
    """
    mode = str((body or {}).get("llm_mode") or "").strip().lower()
    if mode not in LLM_MODES:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "llm_mode_invalid"))

    async with async_session_factory() as db:
        target = (
            await db.execute(select(User).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))
        _before = {"llm_mode": target.llm_mode or DEFAULT_LLM_MODE}
        target.llm_mode = mode
        await _audit_record(db, user_id, "account.llm_mode", "user:%d" % target_user_id,
                            _before, {"llm_mode": mode})
        await db.commit()

    _invalidate_account_state_cache(target_user_id)
    _logger.info("account llm_mode=%s user=%d by=%d", mode, target_user_id, user_id)
    return {"status": "ok", "user_id": target_user_id, "llm_mode": mode}


# ── LLM 额度（A8，2026-09-20：全局默认 + 按账号覆盖）──────────────────────────

def _parse_llm_limit(raw, allow_null: bool) -> int | None:
    """body 里的 ``total_limit`` → 非负整数；``None``（仅 allow_null）表示清除覆盖。

    非法（负数 / 非整数 / bool / 字符串数字）→ ValueError，由路由翻译成 400。
    """
    if raw is None:
        if allow_null:
            return None
        raise ValueError("total_limit_invalid")
    if isinstance(raw, bool):
        raise ValueError("total_limit_invalid")
    if isinstance(raw, int):
        v = raw
    elif isinstance(raw, float) and float(raw).is_integer():
        v = int(raw)
    else:
        raise ValueError("total_limit_invalid")
    if v < 0:
        raise ValueError("total_limit_invalid")
    return v


@router.put("/server/accounts/{target_user_id}/llm-limit")
async def set_account_llm_limit(
    target_user_id: int,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """设置/清除某账号的 LLM 额度覆盖（A8）。

    body ``{"total_limit": int|null}``：``null`` = 清除覆盖（回落服务器默认）；
    负数或非整数 → 400；账号不存在 → 404。写动作落审计 ``server.llm_limit.update``
    （before/after 带 ``scope='user'``），返回 resolve 后的 ``{total_limit, source, own}``。

    校验语义与 llm_quota 服务层一致（服务层再兜一道，非法同样 400）。
    """
    if not isinstance(body, dict) or "total_limit" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    try:
        value = _parse_llm_limit(body.get("total_limit"), allow_null=True)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))

    async with async_session_factory() as db:
        exists = (
            await db.execute(select(User.id).where(User.id == target_user_id))
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "user_not_found"))

    _before = await llm_quota.resolve_limit(target_user_id)
    try:
        after = await llm_quota.set_user_limit(target_user_id, value, user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    except Exception as e:  # noqa: BLE001 —— 写失败如实回 500，不静默成功
        _logger.warning("llm limit write failed user=%s: %s", target_user_id, e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "config_invalid"))
    await _audit_record(None, user_id, "server.llm_limit.update", "user:%d" % target_user_id,
                        {"scope": "user", **_before}, {"scope": "user", **after})
    _logger.info("account llm_limit user=%d value=%s by=%d", target_user_id, value, user_id)
    return {"status": "ok", **after}


@router.get("/server/llm-limit")
async def get_server_llm_limit(
    user_id: int = Depends(require_server_admin),
):
    """服务器默认额度（只读，全局 ``llm_usage_limits.id==1`` 行）：0 = 未设置。

    账号无覆盖时回落到这里；控制台账号页展示「服务器默认额度」用本端点。
    """
    total = await llm_quota.get_global_limit()
    return {
        "scope": "global",
        "total_limit": total,
        "source": "global" if total > 0 else "unset",
    }


@router.put("/server/llm-limit")
async def set_server_llm_limit(
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """写服务器默认额度（全局 id=1 行）：body ``{"total_limit": int}``（>=0，0 = 未设置）。

    负数/非整数 → 400；写动作落审计 ``server.llm_limit.update``（before/after 带 ``scope='global'``）。
    App 侧既有 ``PUT /api/v1/system/llm-usage-limit`` 语义不变（本批不动 system.py）。
    """
    if not isinstance(body, dict) or "total_limit" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    try:
        value = _parse_llm_limit(body.get("total_limit"), allow_null=False)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))

    _before = await llm_quota.get_global_limit()
    try:
        after = await llm_quota.set_global_limit(value, user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    except Exception as e:  # noqa: BLE001
        _logger.warning("global llm limit write failed: %s", e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "config_invalid"))
    await _audit_record(None, user_id, "server.llm_limit.update", "global",
                        {"scope": "global", "total_limit": _before},
                        {"scope": "global", **after})
    _logger.info("global llm_limit=%s by=%d", value, user_id)
    return {"status": "ok", **after}


# ── 默认模型（服务器级四模态配置，契约 §1.2）──────────────────────────────────

# 控制台可见的模态键（与 llm_config_service._MODALITY_TABLES 的规范键一致）
_MODALITY_KEYS = ("llm", "image", "vlm", "speech", "multimodal")
# body 任意子集可写的字段（空串＝清空该字段，与既有 system.py 写接口口径一致）
_MODALITY_TEXT_FIELDS = ("base_url", "api_key", "model", "provider")


def _modality_snapshot(cfg) -> dict:
    """模态配置行 → 审计/展示快照（api_key 只留是否已配置，明文永不回传/落库）。"""
    return {
        "enabled": bool(getattr(cfg, "enabled", False)),
        "base_url": getattr(cfg, "base_url", None),
        "api_key": "***" if getattr(cfg, "api_key", None) else None,
        "model": getattr(cfg, "model", None),
        "provider": getattr(cfg, "provider", None),
        "daily_limit": getattr(cfg, "daily_limit", None),
    }


@router.get("/server/modalities")
async def list_server_modalities(
    user_id: int = Depends(require_server_admin),
):
    """四模态服务器级配置清单（只读）：api_key 只回 ``has_api_key``，不回明文。

    数据源：``llm_config_service.get_server_modality_row``（user_id=SERVER_CONFIG_UID 哨兵行）。
    无行的模态返回 enabled=false + 各字段 null（不建行）。
    """
    from app.application.llm_config_service import MODALITY_LABELS, get_server_modality_row

    async with async_session_factory() as db:
        out = []
        for key in _MODALITY_KEYS:
            row = await get_server_modality_row(db, key)
            out.append({
                "key": key,
                "label": MODALITY_LABELS.get(key, key),
                "enabled": bool(getattr(row, "enabled", False)),
                "base_url": getattr(row, "base_url", None),
                "model": getattr(row, "model", None),
                "provider": getattr(row, "provider", None),
                "has_api_key": bool(getattr(row, "api_key", None)),
                "daily_limit": getattr(row, "daily_limit", None),
            })
    return {"modalities": out}


@router.put("/server/modalities/{key}")
async def update_server_modality(
    key: str,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """写服务器级模态配置（body 任意子集；空串＝清空该字段）。

    数据源：``llm_config_service.get_or_create_server_modality_row``（唯一写入口）。
    非 server_admin 的账号即使无自有配置也能直接用服务器默认（验收 1），因此这里是
    「控制台改服务器默认模型」的唯一写路径。
    """
    from app.application.llm_config_service import get_or_create_server_modality_row

    k = (key or "").strip().lower()
    if k == "image_gen":  # 别名归一（与 llm_config_service.normalize_modality 同口径）
        k = "image"
    if k not in _MODALITY_KEYS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "modality_invalid"))
    if not isinstance(body, dict) or not body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    if "daily_limit" in body:
        try:
            daily_limit = max(1, int(body.get("daily_limit")))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))

    async with async_session_factory() as db:
        cfg = await get_or_create_server_modality_row(db, k)
        _before = _modality_snapshot(cfg)
        for field in _MODALITY_TEXT_FIELDS:
            if field in body:
                setattr(cfg, field, (body.get(field) or "").strip() or None)
        if "enabled" in body:
            cfg.enabled = bool(body.get("enabled"))
        if "daily_limit" in body and hasattr(cfg, "daily_limit"):
            cfg.daily_limit = daily_limit
        _after = _modality_snapshot(cfg)
        await _audit_record(db, user_id, "server.modality.update", "modality:" + k, _before, _after)
        await db.commit()

    _logger.info("server modality updated key=%s by=%d enabled=%s", k, user_id, _after["enabled"])
    return {
        "status": "ok", "key": k, "enabled": _after["enabled"], "base_url": _after["base_url"],
        "model": _after["model"], "provider": _after["provider"],
        "has_api_key": _after["api_key"] == "***", "daily_limit": _after["daily_limit"],
    }


# ── 开关与权限（契约 §1.3，本轮核心诉求之一）──────────────────────────────────

def _flag_type_name(v) -> str:
    """flag 值类型名（bool/int/float/其它）——供控制台区分「开关」与「数值型键」。"""
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    return type(v).__name__


@router.get("/server/flags")
async def list_server_flags(
    user_id: int = Depends(require_server_admin),
):
    """全量 Feature Flag + 策略元数据（契约 §1.3）。

    值取 ``AGENT_FLAGS`` 当前生效值（含 DB 覆盖）；``self_service``/``server_locked``/``title``/
    ``desc`` 来自 flag_settings（缺行 = 自助开、未锁定、无标题/描述）。
    额外附 ``value``/``type``（契约未覆盖，我这么做）：数值型键（如 domain_event_retention_days）
    在契约字段里只能被 bool 折叠，控制台会误显示成「开/关」；带上类型后 UI 可只读展示、禁改。
    """
    from app.agent.loop import AGENT_FLAGS
    from app.application.flag_service import get_flag_policies

    async with async_session_factory() as db:
        policies = await get_flag_policies(list(AGENT_FLAGS.keys()), db=db)
    flags = []
    for k, v in AGENT_FLAGS.items():
        p = policies.get(k) or {"self_service": True, "server_locked": False, "title": None, "desc": None}
        typ = _flag_type_name(v)
        flags.append({
            "key": k,
            "enabled": bool(v),
            "type": typ,
            "value": v if typ in ("bool", "int", "float") else str(v),
            "title": p.get("title"),
            "desc": p.get("desc"),
            "self_service": bool(p.get("self_service", True)),
            "server_locked": bool(p.get("server_locked", False)),
        })
    return {"flags": flags}


@router.put("/server/flags/{key}")
async def update_server_flag(
    key: str,
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """写单个开关：任意子集 ``{enabled, self_service, server_locked}``（契约 §1.3）。

    - ``enabled``：经 flag_service.set_runtime_flag 写 runtime_flags + 热更新内存（立即生效）；
    - ``self_service`` / ``server_locked``：写 flag_settings 策略；
    - 非 bool 型键（如 domain_event_retention_days）带 enabled → 400（沿用 flag_service 类型防护口径）；
    - 这是**控制台路径**：``server_locked`` 对它不构成拒绝（锁定＝仅控制台可改，用户侧走
      PUT /api/v1/system/feature-flags/{key} 会被 403）。
    """
    from app.agent.loop import AGENT_FLAGS
    from app.application import flag_service

    if key not in AGENT_FLAGS:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "flag_key_invalid"))
    if not isinstance(body, dict) or not (set(body.keys()) & {"enabled", "self_service", "server_locked"}):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    if "enabled" in body and not isinstance(AGENT_FLAGS[key], bool):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "flag_type_not_bool"))

    _before = await flag_service.get_flag_policy(key)  # 只读（自开会话，先于下方写事务）
    if "enabled" in body:
        ok = await flag_service.set_runtime_flag(key, bool(body.get("enabled")))
        if not ok:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "flag_type_not_bool"))

    async with async_session_factory() as db:
        policy = await flag_service.set_flag_policy(
            key,
            self_service=body.get("self_service") if "self_service" in body else None,
            server_locked=body.get("server_locked") if "server_locked" in body else None,
            db=db,
        )
        _after = {
            "enabled": bool(AGENT_FLAGS.get(key)),
            "self_service": policy["self_service"],
            "server_locked": policy["server_locked"],
        }
        await _audit_record(db, user_id, "server.flag.update", "flag:" + key, _before, _after)
        await db.commit()

    _logger.info("server flag updated key=%s by=%d after=%s", key, user_id, _after)
    return {"status": "ok", "key": key, **_after}


# ── 注册策略（契约 §1.4）──────────────────────────────────────────────────────

@router.get("/server/registration")
async def get_registration_policy(
    user_id: int = Depends(require_server_admin),
):
    """注册策略（只读）：``open``（默认，与现状一致）/ ``invite_only`` / ``closed``。"""
    from app.application.server_settings_service import get_registration_mode

    async with async_session_factory() as db:
        mode = await get_registration_mode(db)
    return {"mode": mode}


@router.put("/server/registration")
async def set_registration_policy(
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """写注册策略：closed → 注册端点 403 可读提示；invite_only → 注册须携带受邀码。"""
    from app.application.server_settings_service import (
        REGISTRATION_MODES,
        get_registration_mode,
        set_registration_mode,
    )

    mode = str((body or {}).get("mode") or "").strip().lower()
    if mode not in REGISTRATION_MODES:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "registration_mode_invalid"))

    async with async_session_factory() as db:
        _before = {"mode": await get_registration_mode(db)}
        await set_registration_mode(db, mode)
        await _audit_record(db, user_id, "server.registration.update", "registration",
                            _before, {"mode": mode})
        await db.commit()

    _logger.info("registration mode=%s by=%d", mode, user_id)
    return {"status": "ok", "mode": mode}


# ── 审计（契约 §1.5）──────────────────────────────────────────────────────────

@router.get("/server/audit")
async def list_admin_audit(
    limit: int = 100,
    user_id: int = Depends(require_server_admin),
):
    """最近审计条目（created_at DESC）：{id,actor_user_id,actor_username,action,target,before,after,created_at}。

    ``limit`` 夹在 1..500（默认 100）。
    """
    async with async_session_factory() as db:
        entries = await list_entries(db, limit)
    return {"entries": entries}


# ── 概览（契约 §1.6）──────────────────────────────────────────────────────────

@router.get("/server/overview")
async def server_overview(
    user_id: int = Depends(require_server_admin),
):
    """控制台首页概览：账号数 / 已禁用数 / 控制台管理员数 / 开启中的 bool 开关数 / 版本。"""
    from app.agent.loop import AGENT_FLAGS

    async with async_session_factory() as db:
        accounts = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        disabled = (await db.execute(
            select(func.count()).select_from(User).where(User.disabled_at.is_not(None))
        )).scalar_one()
        server_admins = (await db.execute(
            select(func.count()).select_from(User).where(User.server_admin.is_(True))
        )).scalar_one()
    return {
        "accounts": int(accounts),
        "disabled": int(disabled),
        "server_admins": int(server_admins),
        "flags_on": sum(1 for v in AGENT_FLAGS.values() if isinstance(v, bool) and v),
        "version": get_project_version(),
    }


# ── 行动通道管理端点（X7-M4e-1，派单 P25）──────────────────────────────────────
# 三条行动开关刻意不在 AGENT_FLAGS/flag_catalog（故上面 PUT /server/flags 认不了它们），两份
# 名单此前也只有设备侧端点（不属控制台前缀契约）。本组端点挂在既有 /server 前缀下补齐：控制台
# 只调 HTTP API。**闸门逻辑一律不在本文件复制**——读写全部经 app.device.actions；校验复用设备侧
# 同一份（app.api.device_actions 里的包名/插件名形态与容量上限）。

async def _admin_tenant_id(user_id: int) -> int | None:
    """当前管理员的家庭根（= 租户）；解析失败返回 None（调用方据此回错，不猜租户）。"""
    from app.application.family_service import get_family_root_id

    try:
        async with async_session_factory() as db:
            return await get_family_root_id(db, user_id)
    except Exception as e:  # noqa: BLE001 —— 读库失败不得凭空指定一个租户
        _logger.warning("device-actions 解析租户失败 user=%s: %s", user_id, e)
        return None


@router.get("/server/device-actions")
async def get_server_device_actions(
    user_id: int = Depends(require_server_admin),
):
    """行动通道总览：三开关生效态 + 本家庭目标白名单 + 插件灰度名单 + 容量上限。

    ``tenant_id`` 取当前管理员家庭根；解析不到 → ``targets`` 空列表且 ``tenant_id`` 为 null
    （与闸门④ fail-closed 同口径）。``switches`` 的生效值按各闸门缺省方向（见 action_flag_states）。
    """
    from app.api.device_actions import MAX_PLUGINS_GRAYLISTED, MAX_TARGETS_PER_TENANT
    from app.device import actions

    tenant_id = await _admin_tenant_id(user_id)
    return {
        "tenant_id": tenant_id,
        "switches": await actions.action_flag_states(),
        "targets": sorted(await actions.configured_targets(tenant_id)),
        "plugins": sorted(await actions.configured_plugins()),
        "limits": {"targets_max": MAX_TARGETS_PER_TENANT, "plugins_max": MAX_PLUGINS_GRAYLISTED},
    }


@router.put("/server/device-actions/switches")
async def put_server_device_action_switch(
    body: dict,
    user_id: int = Depends(require_server_admin),
    lang: str = Header(default="zh"),
):
    """改一条行动开关：body ``{"key": "global|plugin_enabled|force_dry_run", "enabled": bool}``。

    非法 key（或缺 ``enabled``）→ 400；成功 → ``{"ok": true, "switches": {...}}``（同 GET 的
    switches 段，回显写后的生效态）。写库失败 → 200 + ``ok=false``/``store_unavailable``（不静默成功）。
    """
    from app.device import actions

    if not isinstance(body, dict) or "enabled" not in body:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "admin_enabled_invalid"))
    key = str(body.get("key") or "").strip()
    prev = (await actions.action_flag_states()).get(key)
    try:
        result = await actions.set_action_flag(key, bool(body.get("enabled")))
    except ValueError:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    switches = await actions.action_flag_states()
    if not result.get("ok"):
        return {"ok": False, "reason": "store_unavailable", "switches": switches}
    # 契约 §0：所有写动作落 admin_audit_log（X7-M4e-1 收尾补齐）
    await _audit_record(None, user_id, "server.device_actions.switch.update", "flag:" + key,
                        {"enabled": prev}, {"enabled": switches.get(key)})
    return {"ok": True, "switches": switches}


@router.post("/server/device-actions/targets")
async def post_server_device_action_target(
    body: dict,
    user_id: int = Depends(require_server_admin),
):
    """给本家庭放开一个行动目标：复用设备侧同一套校验（包名形态 + ≤128 + 单租户 ≤20 + 幂等）。

    非法 → 200 + ``invalid_target``；超容量 → 200 + ``too_many_targets``；写库失败 → ``store_unavailable``；
    成功 → ``{"ok": true, "targets": [...]}``。租户解析不到 → ``tenant_unresolved``。
    """
    from app.api.device_actions import MAX_TARGETS_PER_TENANT, _valid_target
    from app.device import actions

    tenant_id = await _admin_tenant_id(user_id)
    if tenant_id is None:
        return {"ok": False, "reason": "tenant_unresolved"}
    target = str((body or {}).get("target") or "").strip()
    current = sorted(await actions.configured_targets(tenant_id))
    if not _valid_target(target):
        return {"ok": False, "reason": "invalid_target", "targets": current}
    if len(set(current) | {target}) > MAX_TARGETS_PER_TENANT:
        return {"ok": False, "reason": "too_many_targets", "targets": current}
    if not await actions.allow_target(tenant_id, target):
        return {"ok": False, "reason": "store_unavailable", "targets": current}
    # 契约 §0：写动作落审计（目标只是包名，不含任何用户内容）
    await _audit_record(None, user_id, "server.device_actions.target.add", "target:%s" % target,
                        None, {"tenant_id": tenant_id})
    return {"ok": True, "targets": sorted(await actions.configured_targets(tenant_id))}


@router.delete("/server/device-actions/targets")
async def delete_server_device_action_target(
    body: dict,
    user_id: int = Depends(require_server_admin),
):
    """删除本家庭一个行动目标（幂等）：``{"ok": true, "removed": <行数>, "targets": [...]}``。"""
    from app.device import actions

    tenant_id = await _admin_tenant_id(user_id)
    if tenant_id is None:
        return {"ok": False, "reason": "tenant_unresolved"}
    target = str((body or {}).get("target") or "").strip()
    removed = await actions.remove_target(tenant_id, target)
    await _audit_record(None, user_id, "server.device_actions.target.remove", "target:%s" % target,
                        {"tenant_id": tenant_id}, {"removed": removed})
    return {"ok": True, "removed": removed,
            "targets": sorted(await actions.configured_targets(tenant_id))}


@router.post("/server/device-actions/plugins")
async def post_server_device_action_plugin(
    body: dict,
    user_id: int = Depends(require_server_admin),
):
    """把一个插件放进行动灰度名单（全局不分租户）：校验非空 / ≤64 / 仅 ``[A-Za-z0-9_.-]`` / 上限 50。

    非法 → 200 + ``invalid_plugin``；超容量 → 200 + ``too_many_plugins``；写库失败 → ``store_unavailable``；
    成功 → ``{"ok": true, "plugins": [...]}``。幂等：重复添加不产生第二行。
    """
    from app.api.device_actions import MAX_PLUGINS_GRAYLISTED, PLUGIN_NAME_PATTERN
    from app.device import actions

    name = str((body or {}).get("plugin") or "").strip()
    current = sorted(await actions.configured_plugins())
    if not PLUGIN_NAME_PATTERN.fullmatch(name):
        return {"ok": False, "reason": "invalid_plugin", "plugins": current}
    if len(set(current) | {name}) > MAX_PLUGINS_GRAYLISTED:
        return {"ok": False, "reason": "too_many_plugins", "plugins": current}
    if not await actions.allow_plugin_actions(name):
        return {"ok": False, "reason": "store_unavailable", "plugins": current}
    # 契约 §0：写动作落审计（插件名属配置项，非用户内容）
    await _audit_record(None, user_id, "server.device_actions.plugin.add", "plugin:" + name, None, None)
    return {"ok": True, "plugins": sorted(await actions.configured_plugins())}


@router.delete("/server/device-actions/plugins")
async def delete_server_device_action_plugin(
    body: dict,
    user_id: int = Depends(require_server_admin),
):
    """从一个插件收回行动灰度（幂等）：``{"ok": true, "removed": <行数>, "plugins": [...]}``。"""
    from app.device import actions

    name = str((body or {}).get("plugin") or "").strip()
    removed = await actions.revoke_plugin_actions(name)
    await _audit_record(None, user_id, "server.device_actions.plugin.remove", "plugin:" + name,
                        None, {"removed": removed})
    return {"ok": True, "removed": removed, "plugins": sorted(await actions.configured_plugins())}
