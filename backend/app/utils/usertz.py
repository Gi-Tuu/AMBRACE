# -*- coding: utf-8 -*-
"""用户时区工具：读取用户 timezone_offset_minutes（分钟，如 480=UTC+8），失败回退 None。

供相对时间解析（app/memory/time_query.parse_time_range 的 ``tz_offset_min``）与
L1 日摘要 today 判定等处接线使用。纯读、失败静默——查不到/异常一律返回 None，
调用方据此回退 UTC 口径（与旧行为一致，安全灰度）。
"""
from __future__ import annotations

import logging

_logger = logging.getLogger("utils.usertz")


async def get_user_tz_offset_min(user_id: int | None) -> int | None:
    """取用户本地时区分钟偏移（如 480=UTC+8）。

    用户未设置（NULL）、无 user_id 或任何查询异常 → 返回 None（调用方回退 UTC）。
    不阻塞主链路：本函数只做一次索引精确查询，失败静默。
    """
    if not user_id:
        return None
    try:
        from sqlalchemy import select
        from app.db.database import async_session_factory
        from app.models.user import User
        async with async_session_factory() as db:
            _off = (await db.execute(
                select(User.timezone_offset_minutes).where(User.id == user_id)
            )).scalar_one_or_none()
        return int(_off) if _off is not None else None
    except Exception as e:
        _logger.warning("user tz load failed uid=%s: %s", user_id, e)
        return None
