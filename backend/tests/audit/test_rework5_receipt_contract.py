import asyncio

from sqlalchemy import select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, add, counts
from app.flags.agent_flags import AGENT_FLAGS
from app.memory import extraction_worker as worker
from app.memory.receipt import emit_memory_receipt
from app.models import StageMemory
from app.models.memory import MemoryWriteReceipt


async def receipt_rows(w):
    async with w.factory() as db:
        return (await db.scalars(select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.action == 'stage_create'))).all()


def test_observability_receipt_stays_off_when_flag_off(world):
    async def run():
        AGENT_FLAGS['memory_write_receipt'] = False
        emit_memory_receipt(901, None, 'update', reason='synthetic observation')
        await asyncio.sleep(0)
        assert await receipt_rows(world) == []
    asyncio.run(run())


def test_stage_authority_receipt_is_not_gated_by_observation_flag(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        AGENT_FLAGS['memory_write_receipt'] = False
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        rows=await receipt_rows(w)
        assert len(rows)==1 and rows[0].action=='stage_create'
        assert rows[0].authoritative is True
    asyncio.run(run())


def test_legacy_no_uid_stage_writes_no_receipt(world):
    async def run():
        w=world; p=await create_source(w)
        AGENT_FLAGS['memory_source_proof_v1'] = False
        add(p, uid=False)
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        assert await receipt_rows(w)==[]
    asyncio.run(run())


def test_stage_receipt_detail_binds_generation_and_row(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        rows=await receipt_rows(w)
        assert len(rows)==1
        import json
        detail=json.loads(rows[0].detail_json)
        assert detail['stage_id']
        assert detail['stage_created_at']
        assert detail['stage_kind']=='roleplay'
        assert rows[0].source_event_uid==p.uid
        async with w.factory() as db:
            stage=(await db.scalars(select(StageMemory))).one()
        assert stage.id==detail['stage_id']
    asyncio.run(run())
