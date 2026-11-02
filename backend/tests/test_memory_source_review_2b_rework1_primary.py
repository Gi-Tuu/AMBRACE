import asyncio
from sqlalchemy import text
from test_memory_source_write_m2b import _seed


def test_forgotten_memory_replay_must_respect_tombstone(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.memory import source_events
    from app.db.database import async_session_factory
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    async def run():
        msg_id = await _seed(8201, 8211, 8221, "synthetic source")
        kw = dict(user_id=8201, character_id=8211, memory_type="event", content="synthetic forgotten fact", source="chat", source_id=msg_id)
        mem = await save_memory(**kw)
        assert mem is not None
        async with async_session_factory() as db:
            await db.run_sync(lambda sd: source_events.forget_memory(sd.connection(), memory_id=mem.id, owner_user_id=8201, character_id=8211))
            await db.commit()
            row = (await db.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=:id"), {"id": mem.id})).scalar_one()
            assert row == "closed"
        try:
            replay = await save_memory(**kw)
        except (RuntimeError, ValueError):
            return
        assert replay is None, "replay returned a forgotten memory despite a closed tombstone"
    asyncio.run(run())
