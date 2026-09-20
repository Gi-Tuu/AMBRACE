# -*- coding: utf-8 -*-
"""LLM 额度服务（A8，2026-09-20）：全局默认 + 按账号覆盖的唯一解析出口。

口径（覆盖 > 全局 > 未设置）：
- ``user_llm_limits`` 有该账号的行 → **生效值 = 覆盖值**，``source='user'``（覆盖值可以是 0，
  与「无覆盖」靠「有无行」区分，不看值）；
- 无覆盖行且全局 ``llm_usage_limits.id==1`` 的 ``total_limit > 0`` → ``source='global'``；
- 都没有 → ``total_limit=0``、``source='unset'``（沿用既有语义：0 = 未设置）。

边界（严格遵守）：
- 本模块**只做数据**，不抛 HTTPException；HTTP 语义（400/404）由 ``app/api/admin.py`` 决定；
- 入参非法（负数 / 非整数）抛 ``ValueError``，由 API 层翻译成 400；
- DB 异常一律 fail-open 记 warning：额度是旁路指标，不能因为额度读失败把控制台或聊天打挂
  （读失败按「无覆盖 / 全局 0」处理，写失败抛 ``RuntimeError`` 让 API 层返回 500 而非静默成功）。

App 侧读额度（``app/application/system.py`` 的 llm usage 统计）由后续接线调用 ``resolve_limit``；
本批不改 system.py。
"""
from sqlalchemy import delete, select

from app.db.database import async_session_factory
from app.models.agent import LlmUsageLimit, UserLlmLimit
from app.utils.logger import get_logger

_logger = get_logger("llm_quota")


def _as_limit(value) -> int:
    """入参 → 非负整数（bool 视为非法）；非法抛 ValueError（API 层翻译成 400）。"""
    if isinstance(value, bool) or value is None:
        raise ValueError("total_limit_invalid")
    if isinstance(value, int):
        v = value
    elif isinstance(value, float) and float(value).is_integer():
        v = int(value)
    else:
        raise ValueError("total_limit_invalid")
    if v < 0:
        raise ValueError("total_limit_invalid")
    return v


async def get_global_limit() -> int:
    """全局默认额度（``llm_usage_limits.id==1`` 的 ``total_limit``）；无行 = 0 = 未设置。"""
    try:
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(LlmUsageLimit.total_limit).where(LlmUsageLimit.id == 1)
                )
            ).scalar_one_or_none()
        return int(row) if row is not None else 0
    except Exception as e:  # noqa: BLE001 —— fail-open：额度读失败按「未设置」处理
        _logger.warning("get_global_limit failed: %s", e)
        return 0


async def get_user_overrides(user_ids: list[int]) -> dict[int, int]:
    """批量读账号覆盖（一次 SQL，避免控制台账号页 N+1）；返回 ``{user_id: total_limit}``。"""
    ids = [int(u) for u in (user_ids or [])]
    if not ids:
        return {}
    try:
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(UserLlmLimit.user_id, UserLlmLimit.total_limit)
                    .where(UserLlmLimit.user_id.in_(ids))
                )
            ).all()
        return {int(r[0]): int(r[1]) for r in rows}
    except Exception as e:  # noqa: BLE001 —— fail-open：读失败 = 全部无覆盖（回落全局）
        _logger.warning("get_user_overrides failed ids=%s: %s", ids, e)
        return {}


async def resolve_limit(user_id: int) -> dict:
    """解析某账号的生效额度：``{total_limit:int, source:'user'|'global'|'unset', own:int|None}``。"""
    uid = int(user_id)
    own = (await get_user_overrides([uid])).get(uid)
    if own is not None:
        return {"total_limit": int(own), "source": "user", "own": int(own)}
    total = await get_global_limit()
    if total > 0:
        return {"total_limit": int(total), "source": "global", "own": None}
    return {"total_limit": 0, "source": "unset", "own": None}


async def set_user_limit(user_id: int, total_limit: int | None, by_user_id: int | None) -> dict:
    """设置/清除某账号的额度覆盖。

    ``total_limit=None`` → **删除覆盖行**（回落全局）；否则 upsert（校验 >=0）。
    返回 ``{user_id, total_limit, source, own}``（resolve 后的结果，供 API 直接回传与落审计）。
    """
    uid = int(user_id)
    if total_limit is None:
        try:
            async with async_session_factory() as db:
                await db.execute(delete(UserLlmLimit).where(UserLlmLimit.user_id == uid))
                await db.commit()
        except Exception as e:  # noqa: BLE001 —— 写失败必须让调用方知道（不静默成功）
            _logger.warning("clear user llm limit failed user=%s: %s", uid, e)
            raise RuntimeError("llm_limit_write_failed") from e
        return {"user_id": uid, **(await resolve_limit(uid))}

    v = _as_limit(total_limit)
    try:
        async with async_session_factory() as db:
            row = (
                await db.execute(select(UserLlmLimit).where(UserLlmLimit.user_id == uid))
            ).scalar_one_or_none()
            if row is None:
                db.add(UserLlmLimit(user_id=uid, total_limit=v, updated_by=by_user_id))
            else:
                row.total_limit = v
                row.updated_by = by_user_id
            await db.commit()
    except Exception as e:  # noqa: BLE001
        _logger.warning("set user llm limit failed user=%s: %s", uid, e)
        raise RuntimeError("llm_limit_write_failed") from e
    return {"user_id": uid, **(await resolve_limit(uid))}


async def set_global_limit(total_limit: int, by_user_id: int | None) -> dict:
    """写全局默认额度（upsert ``llm_usage_limits.id==1``；沿用既有语义 0 = 未设置）。"""
    v = _as_limit(total_limit)
    try:
        async with async_session_factory() as db:
            row = (
                await db.execute(select(LlmUsageLimit).where(LlmUsageLimit.id == 1))
            ).scalar_one_or_none()
            if row is None:
                db.add(LlmUsageLimit(id=1, total_limit=v, updated_by=by_user_id))
            else:
                row.total_limit = v
                row.updated_by = by_user_id
            await db.commit()
    except Exception as e:  # noqa: BLE001
        _logger.warning("set global llm limit failed: %s", e)
        raise RuntimeError("llm_limit_write_failed") from e
    return {"scope": "global", "total_limit": v, "source": "global" if v > 0 else "unset"}
