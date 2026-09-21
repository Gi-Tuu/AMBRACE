# -*- coding: utf-8 -*-
"""T2 删群补级联回归（v3.4.6 第三轮止血批，2026-09-10）。

背景：delete_group 只删成员/消息/群，漏删 group_memories、漏置空 game_sessions.group_id
→ 有群记忆/群对局的群删不掉（RESTRICT 500）。
本次：显式先删群记忆、置空群对局（历史保留），再删成员/消息/群。
"""
import asyncio
import os

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.application.chat_groups import delete_group

pytestmark = pytest.mark.slow


@pytest.fixture()
def group_db(tmp_path):
    """临时 SQLite 文件库（delete_group 只依赖传入的 AsyncSession）。"""
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)
    yield factory
    asyncio.run(engine.dispose())


def _seed(factory):
    from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage, GroupMemory
    from app.models.character import AICharacter
    from app.models.game import GameSession
    from app.models.user import User

    async def _main():
        # 克隆库默认开 FK（生产同款 PRAGMA）：父行（users/ai_characters/chat_groups）先提交，
        # 子行（成员/消息/群记忆/对局）后提交；取值与条数一字未动。
        async with factory() as db:
            db.add(User(id=24001, username="u_t2", nickname="用户T2", is_admin=True))
            await db.commit()
        async with factory() as db:
            db.add(AICharacter(id=22101, user_id=24001, name="大壮", is_active=True))
            db.add(ChatGroup(id=24101, user_id=24001, name="测试群"))
            await db.commit()
        async with factory() as db:
            db.add(ChatGroupMember(group_id=24101, character_id=22101))
            db.add(ChatGroupMessage(group_id=24101, sender_type="user", content="大家好"))
            # 群记忆：T2 根因之一（group_memories.group_id RESTRICT）
            db.add(GroupMemory(group_id=24101, user_id=24001, content="大家一起吃过饭"))
            # 群对局：T2 根因之二（game_sessions.group_id RESTRICT）；应置空保留历史
            db.add(GameSession(id=24201, user_id=24001, group_id=24101,
                               game_type="undercover", player_mode="multi"))
            await db.commit()

    asyncio.run(_main())


def test_delete_group_带群记忆与群对局可删(group_db):
    _seed(group_db)

    async def _main():
        from app.models.chat import ChatGroup, GroupMemory
        from app.models.game import GameSession
        async with group_db() as db:
            resp = await delete_group(db, 24101, 24001, "zh")
            assert resp == {"status": "ok"}
            # 群已删
            g = (await db.execute(select(ChatGroup).where(ChatGroup.id == 24101))).scalar_one_or_none()
            assert g is None
            # 群记忆已清
            cnt = (await db.execute(
                select(GroupMemory).where(GroupMemory.group_id == 24101)
            )).scalars().all()
            assert cnt == []
            # 对局保留为历史且 group_id 置空
            gs = (await db.execute(select(GameSession).where(GameSession.id == 24201))).scalar_one_or_none()
            assert gs is not None, "历史对局应保留"
            assert gs.group_id is None, "对局应脱离群（group_id 置空）"
    asyncio.run(_main())


def test_delete_group_非本人群404(group_db):
    _seed(group_db)

    async def _main():
        from fastapi import HTTPException
        async with group_db() as db:
            try:
                await delete_group(db, 24101, 99999, "zh")
                raise AssertionError("非本人群应 404")
            except HTTPException as e:
                assert e.status_code == 404
    asyncio.run(_main())
