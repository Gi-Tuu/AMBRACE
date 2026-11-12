# -*- coding: utf-8 -*-
"""P1-1（2026-11-11 架构治理）：手动改内容后的证据键闭环。

背景：``update_memory_content`` 只改 ``content`` 与 ``version``，不重算 ``write_key``——
而 ``write_key``（``memory/write_identity.replay_key``）的身份字段**包含 content**，
于是改写后「证据键 ↔ 内容」失配：再次抽取同一内容会被判成新条，重放旧内容则命中被改写行。

本模块把这一步收成一处，由 flag ``memory_content_revision_v1``（默认关）控制：
flag 关 ⇒ 不重算（历史行为逐字不变）；flag 开 ⇒ 重算并返回变更结果，供调用点写回执。
"""
from __future__ import annotations

from app.flags.agent_flags import AGENT_FLAGS
from app.utils.logger import get_logger

FLAG = "memory_content_revision_v1"
REASON = "content edited"

_logger = get_logger("memory.content_revision")


def enabled() -> bool:
    return bool(AGENT_FLAGS.get(FLAG, False))


async def recompute_write_key(db, mem) -> dict:
    """按当前内容重算证据键；返回 {changed, collision, old, new}。

    - 无来源身份（``replay_key`` 返回 None）⇒ 不动；
    - 新键与**别的行**撞车（唯一索引）⇒ 保留旧键并 warning（内容改写不该因键冲突失败）。
    """
    from sqlalchemy import select

    from app.memory.write_identity import replay_key
    from app.models.memory import Memory

    old = mem.write_key
    new = replay_key(mem)
    if not new:
        return {"changed": False, "collision": False, "old": old, "new": None}
    if new == old:
        return {"changed": False, "collision": False, "old": old, "new": new}
    other = (await db.execute(select(Memory).where(
        Memory.write_key == new, Memory.id != mem.id))).scalar_one_or_none()
    if other is not None:
        _logger.warning("content revision: write_key collision, keep old key memory_id=%s", mem.id)
        return {"changed": False, "collision": True, "old": old, "new": new}
    mem.write_key = new
    return {"changed": True, "collision": False, "old": old, "new": new}
