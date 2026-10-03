# -*- coding: utf-8 -*-
"""P1-5 回归（2026-09-28 修复）：到期记忆的 user_id 为 0 / NULL 时，回落到角色归属账号。

原判据（memory_review.collect_review_events 里 if m.user_id）会把这类历史脏数据整条跳过
⇒ 高重要度记忆永远不进主动复习队列（等于被静默）。修复后回落到 ai_characters.user_id；
兜底查不到（角色已删 / owner 为空）仍按原样跳过。
"""
import asyncio
from datetime import datetime, timedelta, timezone


from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.user import User
from app.models.chat import ChatSession
from app.models.memory import Memory
from app.scheduling.memory_review import REVIEW_MIN_IMPORTANCE, collect_review_events

OWNER = 771
CHAR = 771


async def _seed(dirty_user_id):
    async with async_session_factory() as db:
        # 复现「历史脏数据」的成因：当年 foreign_keys 未开时写入了 user_id=0 的行。
        # 测试里临时关 FK 才能把这种行塞进去（沙箱默认 PRAGMA foreign_keys=ON）。
        from sqlalchemy import text as _text
        await db.execute(_text("PRAGMA foreign_keys=OFF"))
        db.add(User(id=OWNER, username="p15_owner", nickname="p15_owner", password_hash="x"))
        db.add(AICharacter(id=CHAR, user_id=OWNER, name="测试角色"))
        db.add(ChatSession(user_id=OWNER, character_id=CHAR, is_active=True))
        db.add(Memory(
            user_id=dirty_user_id,
            character_id=CHAR,
            memory_type="event",
            content="很久以前的一件重要的事",
            importance=REVIEW_MIN_IMPORTANCE + 5,
            next_review_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
        ))
        await db.commit()
        await db.execute(_text("PRAGMA foreign_keys=ON"))


def _candidates():
    out = asyncio.run(collect_review_events())
    return [c["candidate"] for c in out if c["candidate"]["character_id"] == CHAR]


# 注：schema 里 memories.user_id 是 **NOT NULL**（实测：插入 None 会 IntegrityError），
# 所以脏数据的现实形态只有 user_id == 0 一种；修复对 0 同样生效（if not m.user_id 为真）。
def test_user_id_为_0_时回落到角色归属账号():
    asyncio.run(_seed(0))
    c = _candidates()
    assert c, "user_id=0 的到期记忆应仍产生复习候选（修复前被 if m.user_id 整条跳过）"
    assert c[0]["user_id"] == OWNER, "候选应带上角色归属账号，而不是 0"
