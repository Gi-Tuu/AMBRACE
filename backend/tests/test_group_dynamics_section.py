# -*- coding: utf-8 -*-
"""#72 BUG-G1 测试：group_dynamics_section 私聊注入群聊动态时过滤游戏消息、归属正确。

覆盖：
- 群消息只取 msg_type == 'normal'：game_event / game_say 播报不进私聊上下文；
- 过滤后 character_id 为空的正常消息（用户本人）归属为「用户」；
- 未知角色 id 的 normal 消息回退为「角色」（而非被兜底成「用户」）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
import tempfile
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent.context.section_overlay import group_dynamics_section


@pytest.fixture()
def gd_db(monkeypatch):
    """临时 SQLite 库：seed 一个用户 + 一个群的成员与若干 normal/game 群消息，patch 会话工厂。"""
    import app.models  # noqa: F401
    from app.models.base import Base

    tmp = tempfile.mkdtemp(prefix="group_dyn_")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    from app.models.character import AICharacter
    from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage
    from app.models.user import User

    async def _seed():
        async with factory() as db:
            u = User(id=1, username="tester", nickname="测试")
            db.add(u)
            c1 = AICharacter(id=1, user_id=1, name="小阳")
            c2 = AICharacter(id=2, user_id=1, name="小冰")
            db.add(c1)
            db.add(c2)
            grp = ChatGroup(id=1, user_id=1, name="家庭群聊")
            db.add(grp)
            db.add(ChatGroupMember(group_id=1, character_id=1))
            db.add(ChatGroupMember(group_id=1, character_id=2))
            ts = datetime(2026, 9, 4, 12, 0)
            # 正常群消息（应进上下文）
            db.add(ChatGroupMessage(
                group_id=1, sender_type="user", character_id=None,
                content="我们明天去哪里玩", created_at=ts, msg_type="normal"))
            db.add(ChatGroupMessage(
                group_id=1, sender_type="ai", character_id=1,
                content="去海边吧", created_at=ts, msg_type="normal"))
            # 游戏播报（不应进私聊上下文）
            db.add(ChatGroupMessage(
                group_id=1, sender_type="ai", character_id=None,
                content="【游戏】海盗船事件", created_at=ts, msg_type="game_event"))
            db.add(ChatGroupMessage(
                group_id=1, sender_type="ai", character_id=2,
                content="【游戏】我猜是狼人", created_at=ts, msg_type="game_say"))
            await db.commit()

    asyncio.run(_seed())

    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def test_group_dynamics_过滤游戏消息且归属正确(gd_db):
    blocks = asyncio.run(group_dynamics_section({"character_id": 1}, {}))
    assert blocks, "应返回群聊动态块"
    text = "\n".join(blocks)
    # 正常群消息进入上下文
    assert "我们明天去哪里玩" in text
    assert "去海边吧" in text
    # 游戏播报/游戏发言不进私聊上下文
    assert "【游戏】" not in text
    assert "游戏播报" not in text and "海盗船事件" not in text and "我猜是狼人" not in text
    # 归属：用户 normal 消息（character_id 为空）标「用户」；已过滤系统播报不再兜底成用户
    assert "[用户" in text
    assert "小阳" in text  # 角色消息用名字


def test_group_dynamics_未知角色id_normal消息回退为角色(gd_db, monkeypatch):
    import app.models  # noqa: F401
    from app.models.chat import ChatGroupMessage

    async def _add_unknown():
        async with gd_db() as db:
            db.add(ChatGroupMessage(
                group_id=1, sender_type="ai", character_id=99,
                content="陌生人插话", created_at=datetime(2026, 9, 4, 12, 0),
                msg_type="normal"))
            await db.commit()

    asyncio.run(_add_unknown())
    blocks = asyncio.run(group_dynamics_section({"character_id": 1}, {}))
    text = "\n".join(blocks)
    # 未知角色 id 的 normal 消息归属为「角色」，而不是被兜底成「用户」
    assert "陌生人插话" in text
    assert "[角色" in text
