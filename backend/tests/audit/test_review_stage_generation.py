import asyncio
from sqlalchemy import select, delete
from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, replace_source, add
from app.memory import extraction_worker as worker
from app.models import StageMemory, ProcessedExtraction

def test_stage_same_owner_source_id_reuse_is_distinct(world):
    async def run():
        first = await create_source(world)
        add(first)
        await worker.process_session(first.sid, first.char, first.user)
        async with world.factory() as db:
            assert len((await db.scalars(select(StageMemory))).all()) == 1
            await db.execute(delete(ProcessedExtraction).where(ProcessedExtraction.user_message_id == first.id))
            await db.commit()
        second = await replace_source(world, first)
        add(second)
        await worker.process_session(second.sid, second.char, second.user)
        async with world.factory() as db:
            assert world.llm.await_count == 2
            assert len((await db.scalars(select(ProcessedExtraction))).all()) == 1
            stages = (await db.scalars(select(StageMemory))).all()
            assert len(stages) == 2, f"new generation stage was suppressed: {len(stages)}"
    asyncio.run(run())
