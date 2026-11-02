import asyncio

from sqlalchemy import delete, select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, add
from app.memory import extractor as ex, extraction_worker as worker
from app.models import ProcessedExtraction, StageMemory
from app.models.memory import MemoryWriteReceipt
from .test_source_identity_p1 import replace_source


def test_receipt_is_bound_to_its_own_stage_row(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        async with w.factory() as db:
            first_stage=(await db.scalars(select(StageMemory))).one()
            first_id=first_stage.id
            await db.execute(delete(ProcessedExtraction).where(
                ProcessedExtraction.user_message_id==p.id))
            await db.commit()
        second=await replace_source(w,p)
        add(second)
        await worker.process_session(second.sid,second.char,second.user)
        async with w.factory() as db:
            await db.execute(delete(StageMemory).where(StageMemory.id==first_id))
            await db.commit()
            receipt=(await db.scalars(select(MemoryWriteReceipt).where(
                MemoryWriteReceipt.source_event_uid==p.uid,
                MemoryWriteReceipt.action=='stage_create'))).one()
            matched=await ex._stage_receipt_matches(
                db,receipt_key=receipt.idempotency_key,uid=p.uid,user_id=p.user,
                character_id=p.char,session_id=p.sid,content='合成舞台收藏蓝水晶',
                stage_kind='roleplay',source_id=p.id)
            assert matched is False
            assert len((await db.scalars(select(StageMemory))).all())==1
    asyncio.run(run())


def test_deleted_stage_with_other_same_fields_is_not_acknowledged(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        async with w.factory() as db:
            stages=(await db.scalars(select(StageMemory))).all()
            assert len(stages)==1
            current=stages[0]
            db.add(StageMemory(user_id=p.user,character_id=p.char,session_id=p.sid,
                               content=current.content,stage_kind=current.stage_kind,
                               importance=3,source_id=p.id))
            await db.flush()
            await db.execute(delete(StageMemory).where(StageMemory.id==current.id))
            await db.execute(delete(ProcessedExtraction).where(
                ProcessedExtraction.user_message_id==p.id))
            await db.commit()
        raised=False
        try:
            await ex.extract_single(p.sid,p.char,p.user,p.body,'合成回复',source_id=p.id)
        except RuntimeError:
            raised=True
        async with w.factory() as db:
            stages=len((await db.scalars(select(StageMemory))).all())
            processed=len((await db.scalars(select(ProcessedExtraction))).all())
        assert (raised,stages,processed)==(True,1,0)
    asyncio.run(run())
