import asyncio
from sqlalchemy import text
from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, add, counts, states
from app.memory import extraction_worker as worker

def test_stage_integrity_failure_does_not_ack(world):
    async def run():
        p = await create_source(world); add(p)
        async with world.factory() as db:
            await db.execute(text("CREATE TRIGGER review_reject_stage BEFORE INSERT ON stage_memories BEGIN SELECT RAISE(ABORT, 'synthetic constraint'); END"))
            await db.commit()
        await worker.process_session(p.sid,p.char,p.user)
        mem, stage, processed = await counts(world)
        assert stage == 0
        assert processed == 0, f"processed despite stage constraint failure: {processed}"
        assert states() != [('succeeded',)]
    asyncio.run(run())
