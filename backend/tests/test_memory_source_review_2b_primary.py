import asyncio
import pytest
from sqlalchemy import text
from test_memory_source_write_m2b import _seed


def test_missing_source_id_is_not_authoritative_fallback(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    async def run():
        await _seed(8101, 8111, 8121)
        try:
            result = await save_memory(user_id=8101, character_id=8111,
                                       memory_type="event", content="synthetic unsourced event",
                                       source="chat", source_id=None)
        except (RuntimeError, ValueError):
            return
        assert result is None, "missing chat source silently wrote a memory"
    asyncio.run(run())


def test_replay_cannot_return_preexisting_memory_after_source_changed(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.db.database import async_session_factory
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    async def run():
        msg_id = await _seed(8102, 8112, 8122, "synthetic original")
        kw = dict(user_id=8102, character_id=8112, memory_type="event",
                  content="synthetic remembered detail", source="chat", source_id=msg_id)
        created = await save_memory(**kw)
        assert created is not None
        async with async_session_factory() as db:
            await db.execute(text("UPDATE chat_messages SET content=:c WHERE id=:id"),
                             {"c": "synthetic edited", "id": msg_id})
            await db.commit()
        try:
            repeated = await save_memory(**kw)
        except (RuntimeError, ValueError):
            return
        assert repeated is None, "stale source replay returned old memory without validating source"
    asyncio.run(run())
