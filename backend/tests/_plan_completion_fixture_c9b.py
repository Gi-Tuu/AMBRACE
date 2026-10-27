"""Synthetic C9b fixtures; real SQL/services, only the clock and database/file destinations replaced."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from _dbclone import clone_engine, make_session_factory
from app.models.chat import ChatMessage, ChatSession
from app.models.character import AICharacter, ProactiveMessageLog
from app.models.memory import ProspectiveIntent
from app.models.user import User
from app.scheduling import prospective_intent as pi

ARRIVAL_PLAN = "用户到达质检台后扫描设备A"
ARRIVAL_SIGNAL = "我到了质检台"  # Literal API stimulus selected from ARRIVAL_PAT, no user transcript.
GENERIC_PLAN = "用户计划完成设备C的绝缘质检"
GENERIC_DONE = "设备C的绝缘质检已经完成"


@dataclass
class World:
    factory: object
    now: datetime

    @property
    def due(self):
        return (self.now + timedelta(hours=8)).replace(hour=23, minute=59, second=0, microsecond=0)

    async def message(self, text, *, session_id=301, ago=0):
        async with self.factory() as db:
            row = ChatMessage(session_id=session_id, sender_type="user", content=text,
                              created_at=self.now - timedelta(minutes=ago))
            db.add(row)
            await db.commit()
            return row.id

    async def plan(self, text=ARRIVAL_PLAN, *, user_id=101, character_id=201,
                   session_id=301, source_id=None, due=None):
        if source_id is None:
            source_id = await self.message(text, session_id=session_id, ago=20)
        return await pi.upsert_intent(
            user_id=user_id, character_id=character_id, content=text,
            kind="promise", cue_terms=["设备校验"], due_end=self.due if due is None else due,
            source_message_id=source_id, chat_session_id=session_id, side="user",
        )

    async def signal(self, text=ARRIVAL_SIGNAL, *, character_id=201, session_id=301, ago=10):
        await self.message(text, session_id=session_id, ago=ago)
        return await pi.settle_promises_on_user_message(character_id, session_id, text)

    async def state(self, intent_id):
        async with self.factory() as db:
            row = await db.get(ProspectiveIntent, intent_id)
            assert row is not None, "Valid synthetic intent must have been persisted"
            return dict(status=row.status, discharged_at=row.discharged_at,
                        user_id=row.user_id, character_id=row.character_id,
                        session_id=row.chat_session_id, source_id=row.source_message_id,
                        content=row.content)

    async def counts(self):
        async with self.factory() as db:
            values = []
            for model in (ProspectiveIntent, ProactiveMessageLog):
                values.append((await db.execute(select(func.count()).select_from(model))).scalar_one())
            values.append((await db.execute(
                select(func.count()).select_from(ChatMessage).where(ChatMessage.sender_type == "ai")
            )).scalar_one())
            return tuple(values)

    async def candidates(self):
        return {row["pis_id"] for row in await pi.collect_due_promises()}


@pytest.fixture
def c9b_world(monkeypatch, tmp_path):
    import asyncio
    from app.db import database

    engine = clone_engine(tmp_path / "c9b.db")
    factory = make_session_factory(engine)
    world = World(factory, datetime.now(timezone.utc).replace(tzinfo=None))

    async def seed():
        async with factory() as db:
            db.add_all([User(id=101, username="lab_user_a", nickname="Lab A"),
                        User(id=102, username="lab_user_b", nickname="Lab B", parent_id=101),
                        User(id=103, username="lab_user_c", nickname="Lab C")])
            await db.commit()
        async with factory() as db:
            db.add_all([AICharacter(id=201, user_id=101, name="Lab Agent A",
                                   personality="Synthetic", chat_style="plain",
                                   relation_type="assistant", is_active=True),
                        AICharacter(id=202, user_id=101, name="Lab Agent B",
                                   personality="Synthetic", chat_style="plain",
                                   relation_type="assistant", is_active=True),
                        AICharacter(id=203, user_id=103, name="Lab Agent C",
                                   personality="Synthetic", chat_style="plain",
                                   relation_type="assistant", is_active=True)])
            await db.commit()
        async with factory() as db:
            db.add_all([ChatSession(id=301, user_id=101, character_id=201),
                        ChatSession(id=302, user_id=101, character_id=201),
                        ChatSession(id=303, user_id=101, character_id=202),
                        ChatSession(id=304, user_id=103, character_id=203),
                        ChatSession(id=305, user_id=102, character_id=201)])
            await db.commit()

    asyncio.run(seed())

    async def verify_legal_ownership():
        from app.application.tenant_service import ensure_tenant_owner, same_tenant
        async with factory() as db:
            assert await same_tenant(db, 101, 102), "Shared-character case requires a real family membership"
            await ensure_tenant_owner(db, 102, 101)
            assert not await same_tenant(db, 101, 103)

    asyncio.run(verify_legal_ownership())
    monkeypatch.setattr(pi, "async_session_factory", factory)
    monkeypatch.setattr(database, "async_session_factory", factory)
    monkeypatch.setattr(pi, "_now_naive", lambda: world.now)
    from app.memory import extractor, user_facts
    from app.scheduling import periodic_state
    monkeypatch.setattr(extractor, "async_session_factory", factory)
    monkeypatch.setattr(user_facts, "async_session_factory", factory)
    monkeypatch.setattr(periodic_state, "_STATE_FILE", tmp_path / "periodic.json")
    for key in ("_LOCKS", "_LOCAL_STAMPS", "_LOCAL_STREAKS", "_LOCAL_RETRY_AT",
                "_LOCAL_COUNTERS", "_LOCAL_MARKS"):
        monkeypatch.setattr(periodic_state, key, {})
    try:
        yield world
    finally:
        asyncio.run(engine.dispose())
