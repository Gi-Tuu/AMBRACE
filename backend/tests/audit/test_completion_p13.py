import asyncio
import sqlite3
from contextlib import closing

from sqlalchemy import event, select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, replace_source, add, counts, states
from app.memory import extract_queue as queue, extractor as ex, extraction_worker as worker
from app.models import Memory, StageMemory


def make_available():
    with closing(sqlite3.connect(queue.database_path())) as db:
        db.execute("UPDATE extraction_tasks SET available_at=0,state='pending' "
                   "WHERE state='retry'")
        db.commit()


def test_stage_failure_is_not_acked_and_retry_writes_once(world):
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
        assert states()!=[('succeeded',)]
        make_available()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        assert states()==[('succeeded',)]
    asyncio.run(run())


def test_processed_failure_replay_does_not_duplicate_stage(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        async with w.factory() as db:
            await db.execute(__import__('sqlalchemy').text(
                "CREATE TRIGGER fail_processed BEFORE INSERT ON processed_extractions "
                "BEGIN SELECT RAISE(ABORT,'processed_fail'); END"))
            await db.commit()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,0)
        assert states()!=[('succeeded',)]
        async with w.factory() as db:
            await db.execute(__import__('sqlalchemy').text('DROP TRIGGER fail_processed'))
            await db.commit()
        make_available()
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1)
        assert states()==[('succeeded',)]
    asyncio.run(run())


def test_old_worker_cannot_commit_after_replacement_succeeds(world,monkeypatch):
    async def run():
        w=world; p=await create_source(w); add(p)
        entered=asyncio.Event(); release=asyncio.Event(); calls=[]
        async def llm(**kwargs):
            calls.append(1)
            if len(calls)==1:
                entered.set()
                await release.wait()
                return 'EVENTS: 合成旧代事件 | 3\nSTAGE: 合成旧代舞台 | 3\n'
            return 'EVENTS: 合成新代事件 | 3\nSTAGE: 合成新代舞台 | 3\n'
        monkeypatch.setattr(ex,'llm_call',llm)
        old=asyncio.create_task(worker.process_session(p.sid,p.char,p.user))
        await asyncio.wait_for(entered.wait(),5)
        new=await replace_source(w,p)
        add(new)
        await worker.process_session(new.sid,new.char,new.user)
        release.set()
        await asyncio.wait_for(old,5)
        assert await counts(w)==(1,1,1)
        async with w.factory() as db:
            mem=(await db.scalars(select(Memory))).one()
            assert '合成新代事件' in mem.content
            stage=(await db.scalars(select(StageMemory))).one()
            assert '合成新代舞台' in stage.content
    asyncio.run(run())
