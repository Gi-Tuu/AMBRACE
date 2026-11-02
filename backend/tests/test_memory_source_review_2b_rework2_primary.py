import asyncio
import pytest
from sqlalchemy import text
from test_memory_source_write_m2b import _seed


@pytest.mark.parametrize("state,owner", [("unexpected", 8301), ("reallowed_active", 9999)])
def test_corrupt_tombstone_never_authorizes_replay(monkeypatch, state, owner):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.service import save_memory
    from app.db.database import async_session_factory
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    async def run():
        msg_id = await _seed(8301, 8311, 8321, "synthetic source event")
        kw = dict(user_id=8301, character_id=8311, memory_type="event", content="synthetic scoped memory", source="chat", source_id=msg_id)
        mem = await save_memory(**kw)
        assert mem is not None
        async with async_session_factory() as db:
            await db.execute(text("INSERT INTO memory_tombstones(memory_id, owner_user_id, character_id, epoch, revoked_seq, confirm_seq, state, created_at) VALUES (:id,:owner,8311,1,0,0,:state,'2026-01-01')"), {"id": mem.id, "owner": owner, "state": state})
            await db.commit()
        try:
            replay = await save_memory(**kw)
        except (RuntimeError, ValueError):
            return
        assert replay is None, f"unexpected tombstone state/owner authorized replay: {state}, {owner}"
    asyncio.run(run())
