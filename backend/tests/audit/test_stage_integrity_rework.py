import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, add, counts
from app.memory import extractor as ex, extraction_worker as worker, extract_queue as queue, source_binding as sb
from app.models import Memory
from app.models.memory import MemoryWriteReceipt


def available():
    with closing(sqlite3.connect(queue.database_path())) as db:
        db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' WHERE state='retry'")
        db.commit()


def receipt_key(p, kind='roleplay', content='合成完整性舞台'):
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]
    return f"stage:{p.uid}:{kind}:{digest}"


async def receipt_count(w):
    async with w.factory() as db:
        return len((await db.scalars(select(MemoryWriteReceipt).where(
            MemoryWriteReceipt.action == 'stage_create'))).all())


def test_receipt_insert_failure_rolls_back_stage_and_retries(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        async with w.factory() as db:
            await db.execute(__import__('sqlalchemy').text(
                "CREATE TRIGGER reject_stage_receipt BEFORE INSERT ON memory_write_receipts "
                "WHEN NEW.action='stage_create' "
                "BEGIN SELECT RAISE(ABORT,'synthetic receipt failure'); END"))
            await db.commit()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(0,0,0)
        assert await receipt_count(w)==0
        async with w.factory() as db:
            await db.execute(__import__('sqlalchemy').text('DROP TRIGGER reject_stage_receipt'))
            await db.commit()
        available()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        assert await receipt_count(w)==1
    asyncio.run(run())


def test_receipt_without_matching_stage_is_not_treated_as_idempotent(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(
                db,user_id=p.user,character_id=p.char,session_id=p.sid,
                source_id=p.id,user_message=p.body,expected_uid=p.uid)
            digest=hashlib.sha256('合成完整性舞台'.encode('utf-8')).hexdigest()
            db.add(MemoryWriteReceipt(
                character_id=p.char,memory_id=None,action='stage_create',
                reason='stage output',detail_json=json.dumps({
                    'stage_kind':'roleplay','content_sha256':digest}),
                source_event_uid=p.uid,owner_user_id=p.user,
                actor_kind='character',actor_user_id=p.char,
                authoritative=True,idempotency_key=receipt_key(p)))
            await db.commit()
        with sb.pin_extraction_source(binding,p.id):
            with pytest.raises(RuntimeError):
                await ex._save_stage_once(
                    user_id=p.user,character_id=p.char,session_id=p.sid,
                    content='合成完整性舞台',stage_kind='roleplay',
                    importance=3,source_id=p.id)
        assert await counts(w)==(0,0,0)
    asyncio.run(run())


def test_character_cascade_removes_stage_receipt(world):
    async def run():
        from app.application.character_cascade import cascade_delete_character
        w=world; p=await create_source(w); add(p)
        await worker.process_session(p.sid,p.char,p.user)
        assert await receipt_count(w)==1
        async with w.factory() as db:
            await cascade_delete_character(db,p.char)
            await db.commit()
        assert await receipt_count(w)==0
    asyncio.run(run())


def test_stage_receipt_is_not_read_by_fact_history(world):
    async def run():
        from app.memory.facts import build_fact_history
        w=world
        async with w.factory() as db:
            mem=Memory(user_id=901,character_id=901,memory_type='event',
                       content='合成事实历史',status='active')
            db.add(mem)
            await db.flush()
            db.add(MemoryWriteReceipt(
                character_id=901,memory_id=None,action='stage_create',
                reason='stage output',detail_json='{}',owner_user_id=901,
                actor_kind='character',actor_user_id=901,
                authoritative=True,idempotency_key='stage:audit:fact-history'))
            await db.commit()
            history=await build_fact_history(db,mem.id,[901])
        assert history['corrections']==[]
    asyncio.run(run())


def test_receipt_insert_conflict_converges_to_one_stage(world):
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
                    content='合成完整性舞台',stage_kind='roleplay',
                    importance=3,source_id=p.id)
        results=await asyncio.gather(write(),write())
        assert sorted(results)==[False,True]
        assert await counts(w)==(0,1,0)
        assert await receipt_count(w)==1
    asyncio.run(run())
