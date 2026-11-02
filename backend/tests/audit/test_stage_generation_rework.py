import asyncio
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import delete, event, select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, replace_source, add, counts
from .test_queue_identity_p12 import delete_source
from app.memory import extractor as ex, extraction_worker as worker, source_binding as sb
from app.models import ProcessedExtraction, StageMemory
from app.models.memory import MemoryWriteReceipt


async def stage_receipt_count(w):
    async with w.factory() as db:
        return len((await db.scalars(select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.action == 'stage_create'))).all())


def test_same_uid_concurrent_stage_write_is_one(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(
                db,user_id=p.user,character_id=p.char,session_id=p.sid,
                source_id=p.id,user_message=p.body,expected_uid=p.uid)
        async def write():
            with sb.pin_extraction_source(binding,p.id):
                return await ex._save_stage_once(
                    user_id=p.user,character_id=p.char,session_id=p.sid,
                    content='合成并发展台',stage_kind='roleplay',
                    importance=3,source_id=p.id)
        results=await asyncio.gather(write(),write())
        assert sorted(results)==[False,True]
        assert await counts(w)==(0,1,0)
        assert await stage_receipt_count(w)==1
    asyncio.run(run())


def test_new_generation_same_source_id_different_content_writes_second_stage(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        async with w.factory() as db:
            await db.execute(delete(ProcessedExtraction).where(
                ProcessedExtraction.user_message_id==p.id))
            await db.commit()
        new=await replace_source(w,p)
        w.llm.return_value='STAGE: 合成第二代舞台B | 3\n'
        add(new)
        await worker.process_session(new.sid,new.char,new.user)
        async with w.factory() as db:
            stages=(await db.scalars(select(StageMemory))).all()
            assert {s.content for s in stages}=={'合成舞台收藏蓝水晶','合成第二代舞台B'}
            assert len(stages)==2
            assert len((await db.scalars(select(ProcessedExtraction))).all())==1
        assert await stage_receipt_count(w)==2
    asyncio.run(run())


def test_legacy_null_uid_stage_is_preserved_not_attributed(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            db.add(StageMemory(user_id=p.user,character_id=p.char,session_id=p.sid,
                               content='合成历史舞台',stage_kind='roleplay',
                               importance=3,source_id=p.id))
            await db.commit()
        add(p)
        w.llm.return_value='STAGE: 合成历史舞台 | 3\n'
        await worker.process_session(p.sid,p.char,p.user)
        async with w.factory() as db:
            stages=(await db.scalars(select(StageMemory))).all()
            assert len(stages)==2
            assert sum(1 for s in stages if s.content=='合成历史舞台')==2
            assert len((await db.scalars(select(MemoryWriteReceipt))).all())==1
    asyncio.run(run())


def test_stage_failure_leaves_no_receipt_and_retry_writes_one(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        async with w.factory() as db:
            engine=db.bind
        def fail_stage(conn,cursor,statement,params,context,many):
            if 'insert into stage_memories' in statement.lower():
                raise sqlite3.OperationalError('synthetic stage failure')
        event.listen(engine.sync_engine,'before_cursor_execute',fail_stage)
        try:
            await worker.process_session(p.sid,p.char,p.user)
        finally:
            event.remove(engine.sync_engine,'before_cursor_execute',fail_stage)
        assert await counts(w)==(0,0,0)
        assert await stage_receipt_count(w)==0
        with closing(sqlite3.connect(__import__('app.memory.extract_queue',fromlist=['x']).database_path())) as db:
            db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE state='retry'")
            db.commit()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        assert await stage_receipt_count(w)==1
    asyncio.run(run())


def test_stage_scope_mismatch_is_rejected(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(
                db,user_id=p.user,character_id=p.char,session_id=p.sid,
                source_id=p.id,user_message=p.body,expected_uid=p.uid)
        with sb.pin_extraction_source(binding,p.id):
            with pytest.raises(sb.SourceIdentityError):
                await ex._save_stage_once(
                    user_id=902,character_id=902,session_id=p.sid,
                    content='合成错归属',stage_kind='roleplay',
                    importance=3,source_id=p.id)
        assert await counts(w)==(0,0,0)
    asyncio.run(run())


def test_deleted_source_keeps_old_stage_and_new_generation_gets_its_own(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        await delete_source(w,p)
        new=await create_source(w,sid=p.sid)
        w.llm.return_value='STAGE: 合成删除后新代舞台 | 3\n'
        add(new)
        await worker.process_session(new.sid,new.char,new.user)
        async with w.factory() as db:
            stages=(await db.scalars(select(StageMemory))).all()
            assert len(stages)==2
            assert {s.content for s in stages}=={'合成舞台收藏蓝水晶','合成删除后新代舞台'}
    asyncio.run(run())
