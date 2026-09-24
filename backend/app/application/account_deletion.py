# -*- coding: utf-8 -*-
"""控制台删号·第一期第一批：标记删除 / 恢复 / 体量预览（2026-09-24 派单第 3 项）。

两阶段删除（方案 v2 §四）：**第一期只删不增**，本模块只负责「标记进回收站」与「恢复」，
真正的物理清除是第二期（清除器不在本批范围，越界即违规）。

- :func:`preview_deletion` —— ``POST .../delete-dry-run``：**零写入、零状态变化**，
  回包 = cascade 体量清单（表 × 行数）+ 模式 + 生效例外 + 判定不出归属的行数 + 护栏结论；
- :func:`mark_deleted` —— ``POST .../delete``：写 ``deleted_at`` / ``purge_after``，
  同时写 ``disabled_at``（复用 P2 门禁：登录 403 + 既有 token 请求 403），落审计；
- :func:`restore` —— ``POST .../restore``：清空上述字段（回收站恢复）。

护栏（v2 §3.2 护栏表，全部复用既有语义）
------------------------------------------
1. 不能删自己（同 ``PUT /server/accounts/{id}/disabled``，admin.py:296）；
2. 不能删最后一个 server_admin（同 admin.py:261-266，否则控制台再也进不去）；
3. 不能删「家庭里最后一个 is_admin 而家庭还有其他存活成员」（同 admin.py:144-152 的
   「至少保留一个主账号」口径；**独立账号名下无人时不触发**——否则最常见的那类账号删不掉）；
4. 家庭根名下还有子账号 → 拒绝（v2：先处理成员，避免整户被一个删号动作带走）；
5. ``confirm_username`` 必须与目标用户名**逐字符相等**（防手滑点错行）；
6. ``purge_now=true`` 仅当「清除器实际会删的行数」小于阈值（默认 :data:`PURGE_NOW_ROW_THRESHOLD`）
   才允许——判据是**按表去重后**的行数（多列命中同一行只算一次），不是各列相加。

护栏 1–4 在 dry-run 里**不抛错**，而是回包 ``guards``（运维要先看清为什么不能删）；
只有真正 ``delete`` 才落成 4xx。
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select

from app.application import user_cascade
from app.application.admin_audit_service import record as _audit_record
from app.application.family_service import get_family_member_ids
from app.i18n import tr_lang
from app.models.user import User

#: 回收站宽限期（天）：到点后第二期的清除器才真正物理删。
GRACE_DAYS = 7
#: ``purge_now=true`` 的行数上限（可用环境变量 ADMIN_PURGE_NOW_ROW_THRESHOLD 调整，便于灰度）。
PURGE_NOW_ROW_THRESHOLD = 2000
_ENV_THRESHOLD = "ADMIN_PURGE_NOW_ROW_THRESHOLD"

# 文案：唯一真源在 app/i18n.py（控制台删号·第三期回填——原先本模块与 account_purge 各有一份
# 本地表，已逐字并入 i18n._MESSAGES，此处只留一个带 None 兜底的薄封装）。


def _msg(lang: str, key: str, **kw: Any) -> str:
    return tr_lang(lang or "zh", key, **kw)


def now_utc() -> datetime:
    """UTC naive（项目约定：库里时间一律 naive UTC，比较时补 tzinfo）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def purge_now_row_threshold() -> int:
    """立即清除行数阈值（环境变量非法值回落默认，绝不因配置错误放开护栏）。"""
    raw = (os.getenv(_ENV_THRESHOLD) or "").strip()
    try:
        n = int(raw) if raw else PURGE_NOW_ROW_THRESHOLD
    except ValueError:
        return PURGE_NOW_ROW_THRESHOLD
    return max(0, n)


def scope_of(target: User) -> str:
    """家庭根（``parent_id`` 为空）→ 整户带走；子账号 → 只删这个成员。"""
    return (user_cascade.SCOPE_DELETE_SUB_ACCOUNT if target.parent_id
            else user_cascade.SCOPE_DELETE_FAMILY_ROOT)


async def _load_user(db, target_user_id: int) -> User:
    target = (await db.execute(select(User).where(User.id == int(target_user_id)))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail=_msg("zh", "user_not_found"))
    return target


async def check_guards(db, *, actor_user_id: int, target: User, lang: str = "zh") -> list[str]:
    """护栏 1–4 的**结论列表**（空 = 可删）。dry-run 用它回包，delete 用它抛错。

    只读，绝不改状态；每条都对应 v2 护栏表的一行，顺序即拒绝优先级。
    """
    reasons: list[str] = []
    if int(actor_user_id) == int(target.id):
        reasons.append(_msg(lang, "cannot_delete_self"))

    if target.server_admin:
        n = (await db.execute(
            select(func.count()).select_from(User).where(
                User.server_admin.is_(True), User.id != target.id, User.deleted_at.is_(None)
            )
        )).scalar_one()
        if int(n) <= 0:
            reasons.append(_msg(lang, "last_server_admin"))

    member_ids = await get_family_member_ids(db, int(target.id))
    others = [i for i in member_ids if i != int(target.id)]
    if others:
        # 家庭还有其他存活成员：不能把最后一个主账号删掉（护栏 3）
        n_admin = (await db.execute(
            select(func.count()).select_from(User).where(
                User.id.in_(others), User.is_admin.is_(True), User.deleted_at.is_(None)
            )
        )).scalar_one()
        if int(n_admin) <= 0:
            reasons.append(_msg(lang, "family_last_admin"))
        if not target.parent_id:
            # 家庭根 + 名下有成员 → 拒绝（护栏 4）。子账号带成员是不可能的（parent_id 非空即叶子）。
            reasons.append(_msg(lang, "family_root_has_members", n=len(others)))
    return reasons


def _volume_tables(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """两族清单合并成「表 × 行数 × 命中列」的体量视图（同表两族各列合到一行，行数取去重值）。"""
    merged: dict[str, dict[str, Any]] = {}
    for family, key in (("user", "user_family"), ("character", "character_family")):
        for entry in plan[key]:
            row = merged.setdefault(entry["table"], {"table": entry["table"], "rows": entry["rows"],
                                                     "columns": []})
            for col in entry["columns"]:
                row["columns"].append({
                    "column": col["column"], "family": family, "kind": col["kind"],
                    "rows": col["rows"], "deletable": col["deletable"],
                    **({"rows_matched": col["rows_matched"]} if "rows_matched" in col else {}),
                    **({"rows_user": col["rows_user"], "rows_character": col["rows_character"],
                        "rows_undetermined": col["rows_undetermined"]}
                       if col["kind"] == user_cascade.KIND_DUAL else {}),
                })
    return sorted(merged.values(), key=lambda t: (-int(t["rows"]), t["table"]))


async def build_plan(db, *, target: User) -> dict[str, Any]:
    """跑一次库结构自动发现（纯读）。第二期清除器复用同一入口，判据不会两套。"""
    return await user_cascade.discover_purge_plan(
        db, user_id=int(target.id), scope=scope_of(target)
    )


async def preview_deletion(db, *, actor_user_id: int, target_user_id: int,
                           lang: str = "zh") -> dict[str, Any]:
    """dry-run：回「会带走哪些表、各多少行、哪些被例外保住、哪些判定不出」。**零写入**。

    本函数与 :func:`_load_user` / :func:`check_guards` / :func:`build_plan` 全程只有 SELECT；
    调用方（路由）也不 commit —— 「删之前先看清体量」是 v2 把删号变成可运维动作的关键。
    """
    target = await _load_user(db, target_user_id)
    guards = await check_guards(db, actor_user_id=actor_user_id, target=target, lang=lang)
    plan = await build_plan(db, target=target)
    threshold = purge_now_row_threshold()
    totals = plan["totals"]
    return {
        "user_id": int(target.id),
        "username": target.username,
        "nickname": target.nickname,
        "mode": plan["scope"],
        "already_deleted": target.deleted_at is not None,
        "grace_days": GRACE_DAYS,
        "guards": guards,
        "may_delete": not guards,
        "totals": totals,
        "tables": _volume_tables(plan),
        "exceptions": plan["exceptions"],
        "undetermined_speaker_rows": plan["undetermined_speaker_rows"],
        "warnings": plan["warnings"],
        "character_ids": plan["character_ids"],
        "purge_now_allowed_below": threshold,
        "purge_now_would_be_allowed": bool(totals["row_count"] < threshold),
        "dry_run": True,
    }


async def mark_deleted(db, *, actor_user_id: int, target_user_id: int, body: dict,
                       lang: str = "zh") -> dict[str, Any]:
    """标记删除（进回收站）：``deleted_at`` + ``disabled_at`` = now，``purge_after`` = now+7d。

    ``body``：``confirm_username``（必填，逐字符相等）、``purge_now``（可选，行数超阈值即拒绝）。
    已在回收站的账号重复标记 → 400（否则宽限期会被悄悄重置）。
    写审计（含用户名 + 体量快照），并失效账号门禁缓存——否则既有 token 还能用 30 秒。
    """
    target = await _load_user(db, target_user_id)
    for reason in await check_guards(db, actor_user_id=actor_user_id, target=target, lang=lang):
        raise HTTPException(status_code=400, detail=reason)
    if target.deleted_at is not None:
        # 已在回收站：重复标记会把宽限期重新推 7 天（等于悄悄延后物理清除），故直接拒绝
        raise HTTPException(status_code=400, detail=_msg(lang, "already_deleted"))

    confirm = body.get("confirm_username") if isinstance(body, dict) else None
    if not isinstance(confirm, str) or not confirm.strip():
        raise HTTPException(status_code=400, detail=_msg(lang, "confirm_username_required"))
    if confirm != target.username:
        raise HTTPException(status_code=400, detail=_msg(lang, "confirm_username_mismatch"))

    plan = await build_plan(db, target=target)
    totals = plan["totals"]
    purge_now = bool(isinstance(body, dict) and body.get("purge_now"))
    threshold = purge_now_row_threshold()
    if purge_now and not totals["row_count"] < threshold:
        raise HTTPException(status_code=400, detail=_msg(
            lang, "purge_now_over_threshold", rows=totals["row_count"], limit=threshold))

    now = now_utc()
    purge_at = now if purge_now else now + timedelta(days=GRACE_DAYS)
    before = {
        "username": target.username,
        "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
        "disabled_at": target.disabled_at.isoformat() if target.disabled_at else None,
        "purge_after": target.purge_after.isoformat() if target.purge_after else None,
    }
    target.deleted_at = now
    target.disabled_at = now  # 复用 P2 门禁：回收站里的账号既不能登录也不能用旧 token
    target.purge_after = purge_at
    await _audit_record(db, actor_user_id, "account.delete", "user:%d" % int(target.id), before, {
        "username": target.username,
        "mode": plan["scope"],
        "deleted_at": now.isoformat(),
        "purge_after": purge_at.isoformat(),
        "purge_now": purge_now,
        "volume": totals,
        "top_tables": [{"table": t["table"], "rows": t["rows"]} for t in _volume_tables(plan)[:20]],
    })
    await db.commit()

    from app.application.permission_service import _invalidate_account_state_cache
    _invalidate_account_state_cache(int(target.id))
    return {
        "status": "ok",
        "user_id": int(target.id),
        "username": target.username,
        "mode": plan["scope"],
        "deleted_at": now.isoformat(),
        "disabled_at": now.isoformat(),
        "purge_after": purge_at.isoformat(),
        "purge_now": purge_now,
        "totals": totals,
        "undetermined_speaker_rows": plan["undetermined_speaker_rows"],
    }


async def restore(db, *, actor_user_id: int, target_user_id: int, lang: str = "zh") -> dict[str, Any]:
    """从回收站恢复：清空 ``deleted_at`` / ``purge_after`` / ``disabled_at``，写审计。

    幂等（未删除的账号重复调用仍是 200，不报错）。``disabled_at`` 一并清空是刻意的：
    回收站里的禁用态是标记删除时写的，恢复必须让账号真正可用；
    确需单独禁用走 ``PUT /server/accounts/{id}/disabled``（审计 ``before`` 里留有原值可查）。
    """
    target = await _load_user(db, target_user_id)
    before = {
        "username": target.username,
        "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
        "disabled_at": target.disabled_at.isoformat() if target.disabled_at else None,
        "purge_after": target.purge_after.isoformat() if target.purge_after else None,
    }
    changed = any(before[k] is not None for k in ("deleted_at", "disabled_at", "purge_after"))
    target.deleted_at = None
    target.purge_after = None
    target.disabled_at = None
    await _audit_record(db, actor_user_id, "account.restore", "user:%d" % int(target.id), before, {
        "username": target.username,
        "deleted_at": None, "disabled_at": None, "purge_after": None,
        "changed": changed,
    })
    await db.commit()
    from app.application.permission_service import _invalidate_account_state_cache
    _invalidate_account_state_cache(int(target.id))
    return {"status": "ok", "user_id": int(target.id), "username": target.username,
            "restored": changed, "deleted_at": None, "purge_after": None, "disabled_at": None}
