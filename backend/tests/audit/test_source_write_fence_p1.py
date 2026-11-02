import asyncio
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, replace_source, add, counts, states
from app.memory import source_binding as sb, extractor as ex, extraction_worker as worker, extract_queue as queue
from app.flags.agent_flags import AGENT_FLAGS
from app.models import AICharacter, GlobalUserFact, WorldFact, ProspectiveIntent, Memory


OUTPUTS = [
    ('memory','EVENTS: 合成散步记录 | 3\n'),
    ('stage','STAGE: 合成舞台记录 | 3\n'),
    ('bio','BIO: 合成角色自述\n'),
    ('status','STATUS: 合成角色状态\n'),
    ('relationship','RELATIONSHIP: 合成友谊记录 | 3\n'),
    ('curated','CURATED: 合成知识记录 | fact | 4\n'),
    ('intent','INTENT: 合成到站再说 | cue | 无 | 合成到站 | high\n'),
    ('processed','EVENTS: 无\n'),
]


@pytest.mark.parametrize('kind,output',OUTPUTS)
def test_each_output_rechecks_identity_at_write(world,monkeypatch,kind,output):
    async def run():
        w=world; p=await create_source(w); add(p)
        monkeypatch.setitem(AGENT_FLAGS,'curated_knowledge',kind=='curated')
        monkeypatch.setitem(AGENT_FLAGS,'prospective_intent_enabled',kind=='intent')
        w.llm.return_value=output
        original=sb.guard_extraction_write
        reached=[]
        async def race(db):
            if not reached:
                reached.append(kind)
                await replace_source(w,p)
            await original(db)
        monkeypatch.setattr(sb,'guard_extraction_write',race)
        await worker.process_session(p.sid,p.char,p.user)
        assert reached==[kind], 'The intended write boundary must be reached'
        assert await counts(w)==(0,0,0) and states()!=[('succeeded',)]
        async with w.factory() as db:
            for model in [GlobalUserFact,WorldFact,ProspectiveIntent]:
                assert (await db.scalars(select(model))).all()==[]
            c=await db.get(AICharacter,p.char)
            assert (c.self_statement,c.current_status,c.relationship_summary)==w.initial_character
    asyncio.run(run())


def test_sqlite_lock_closes_validation_to_memory_insert_window(world):
    async def run():
        w=world; p=await create_source(w); add(p)
        w.llm.return_value='EVENTS: 合成写锁保护记录 | 3\n'
        outcomes=[]
        async with w.factory() as db:
            engine=db.bind
        def before_insert(conn,cursor,statement,params,context,many):
            if not statement.lower().startswith('insert into memories'):
                return
            def competing_writer():
                raw=sqlite3.connect(w.path,timeout=0)
                try:
                    raw.execute('UPDATE chat_messages SET content=? WHERE id=?',('合成来源被改',p.id))
                    raw.commit(); outcomes.append('changed')
                except sqlite3.OperationalError as error:
                    outcomes.append('locked' if 'locked' in str(error) else str(error))
                finally:
                    raw.close()
            thread=threading.Thread(target=competing_writer)
            thread.start(); thread.join(5)
            assert not thread.is_alive()
        event.listen(engine.sync_engine,'before_cursor_execute',before_insert)
        try:
            await worker.process_session(p.sid,p.char,p.user)
        finally:
            event.remove(engine.sync_engine,'before_cursor_execute',before_insert)
        assert outcomes==['locked']
        assert await counts(w)==(1,0,1) and states()==[('succeeded',)]
    asyncio.run(run())


@pytest.mark.parametrize('sink',['user_fact','stale'])
def test_user_fact_and_stale_helpers_reject_expired_pin(world,sink):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(db,user_id=p.user,character_id=p.char,
                session_id=p.sid,source_id=p.id,user_message=p.body,expected_uid=p.uid)
        with sb.pin_extraction_source(binding,p.id):
            await replace_source(w,p)
            with pytest.raises(sb.SourceIdentityError):
                if sink=='user_fact':
                    from app.memory.user_facts import upsert_user_fact
                    await upsert_user_fact(p.user,'job','合成设计师')
                else:
                    from app.memory.cross_char_sync import stale_character_slot_memory
                    await stale_character_slot_memory(p.char,'job','合成设计师')
        async with w.factory() as db:
            assert (await db.scalars(select(GlobalUserFact))).all()==[]
    asyncio.run(run())


def test_flag_off_legacy_extraction_keeps_original_path(world,monkeypatch):
    async def run():
        w=world; p=await create_source(w)
        monkeypatch.setitem(AGENT_FLAGS,'memory_source_proof_v1',False)
        result=await ex.extract_single(p.sid,p.char,p.user,p.body,'合成回复',source_id=p.id)
        assert result==2 and await counts(w)==(1,1,1)
    asyncio.run(run())


def test_http_send_forwards_persisted_uid(world,monkeypatch):
    async def run():
        from app.application import chat_service
        from app.models import ChatSession
        w=world
        async with w.factory() as db:
            db.add(ChatSession(id=1,user_id=901,character_id=901))
            await db.commit()
        seen={}
        async def fake_persist(*args,**kwargs):
            return 1, {'_source_event_uid':'uid-from-persist'}
        async def fake_core(*args,**kwargs):
            return {'final_state':{},'final_text':'合成回复','gen_prompt':None,'img_text':None}
        async def fake_post(*args,**kwargs):
            seen.update(kwargs)
        monkeypatch.setattr(chat_service,'_persist_user_message',fake_persist)
        monkeypatch.setattr(chat_service,'_run_agent_core',fake_core)
        monkeypatch.setattr(chat_service,'_run_post_processing',fake_post)
        monkeypatch.setattr(chat_service,'_push_user_notify',AsyncMock())
        monkeypatch.setattr(chat_service,'append_domain_event',AsyncMock())
        await chat_service.send_and_receive(1,901,901,'合成正文')
        assert seen['source_event_uid']=='uid-from-persist'
    asyncio.run(run())


def test_all_outputs_normal_path_with_valid_source(world,monkeypatch):
    async def run():
        w=world; p=await create_source(w); add(p)
        monkeypatch.setitem(AGENT_FLAGS,'curated_knowledge',True)
        monkeypatch.setitem(AGENT_FLAGS,'prospective_intent_enabled',True)
        w.llm.return_value=(
            'USER_INFO: 无\nEVENTS: 合成全部输出事件 | 3\nPREFERENCES: 无\n'
            'STAGE: 合成舞台全部输出 | 3\nBIO: 合成自述全部输出\nSTATUS: 合成状态全部输出\n'
            'RELATIONSHIP: 合成关系全部输出 | 3\nCURATED: 合成知识全部输出 | fact | 4\n'
            'INTENT: 周末去合成公园 | promise | 2026-11-01~2026-11-01 | 合成公园 | high\n')
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(2,1,1) and states()==[('succeeded',)]
        async with w.factory() as db:
            assert len((await db.scalars(select(WorldFact))).all())==1
            assert len((await db.scalars(select(ProspectiveIntent))).all())==1
            c=await db.get(AICharacter,p.char)
            assert c.self_statement=='合成自述全部输出'
            assert c.current_status=='合成状态全部输出'
            assert c.relationship_summary=='合成关系全部输出'
    asyncio.run(run())


def test_replay_under_stale_pin_is_refused_without_reinforcement(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(db,user_id=p.user,character_id=p.char,
                session_id=p.sid,source_id=p.id,user_message=p.body,expected_uid=p.uid)
        kwargs=dict(user_id=p.user,character_id=p.char,memory_type='event',
                    content='合成重放内容',source='chat',source_id=p.id)
        with sb.pin_extraction_source(binding,p.id):
            from app.memory import save_memory
            first=await save_memory(**kwargs)
            with pytest.raises(sb.SourceIdentityError):
                await replace_source(w,p)
                await save_memory(**kwargs)
        async with w.factory() as db:
            rows=(await db.scalars(select(Memory))).all()
            assert len(rows)==1 and rows[0].id==first.id
            assert rows[0].review_count==0
    asyncio.run(run())


def test_persisted_uid_reaches_enqueue_and_same_text_regeneration_is_refused(world):
    async def run():
        w=world; p=await create_source(w)
        from app.application.chat_store import _persist_user_message
        msg_id,info=await _persist_user_message(p.sid,p.user,p.char,'入口链路合成正文')
        assert info['_source_event_uid'] and info['_source_event_uid']!=p.uid
        uid=info['_source_event_uid']
        await replace_source(w,SimpleNamespace(sid=p.sid,id=msg_id,uid=uid,user=p.user,char=p.char,body='入口链路合成正文'))
        await ex.add_chat_memory_extraction(p.sid,p.char,p.user,'入口链路合成正文','合成回复',
                                            source_id=msg_id,source_event_uid=uid)
        assert queue.peek(p.sid)==[]
    asyncio.run(run())


def test_post_processing_forwards_captured_uid(world,monkeypatch):
    async def run():
        from app.application import chat_tooling, chat_service
        seen=[]
        def capture(*args,**kwargs):
            seen.append(kwargs.get('source_event_uid'))
        monkeypatch.setattr(chat_service,'add_chat_memory_extraction',capture)
        monkeypatch.setattr(chat_service,'spawn_background',lambda coro: coro.close() if hasattr(coro,'close') else None)
        await chat_tooling._run_post_processing(
            1,901,901,'合成正文',{},'合成回复',None,None,source_event_uid='fixed-uid')
        assert seen==['fixed-uid']
    asyncio.run(run())


@pytest.mark.parametrize('sink',['user_fact','stale'])
def test_derived_helpers_normal_write_with_valid_pin(world,sink):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(db,user_id=p.user,character_id=p.char,
                session_id=p.sid,source_id=p.id,user_message=p.body,expected_uid=p.uid)
        with sb.pin_extraction_source(binding,p.id):
            if sink=='user_fact':
                from app.memory.user_facts import upsert_user_fact
                change=await upsert_user_fact(p.user,'job','合成设计师')
                assert change is not None
            else:
                from app.models import Memory
                async with w.factory() as db:
                    db.add(Memory(user_id=p.user,character_id=p.char,memory_type='user_info',
                                  content='职业是合成设计师',status='active'))
                    await db.commit()
                from app.memory.cross_char_sync import stale_character_slot_memory
                assert await stale_character_slot_memory(p.char,'job','合成设计师')==1
        async with w.factory() as db:
            if sink=='user_fact':
                row=(await db.scalars(select(GlobalUserFact))).one()
                assert (row.slot,row.value)==('job','合成设计师')
            else:
                row=(await db.scalars(select(Memory))).one()
                assert row.status=='stale'
    asyncio.run(run())


def test_pins_are_task_local_and_cancel_safe(world):
    async def run():
        w=world; a=await create_source(w,user=901,char=901,body='合成并发甲')
        b=await create_source(w,user=902,char=902,body='合成并发乙')
        async def helper(p,value,gate):
            async with w.factory() as db:
                binding=await sb.validate_extraction_source(db,user_id=p.user,character_id=p.char,
                    session_id=p.sid,source_id=p.id,user_message=p.body,expected_uid=p.uid)
            with sb.pin_extraction_source(binding,p.id):
                await gate.wait()
                from app.memory.user_facts import upsert_user_fact
                await upsert_user_fact(p.user,'job',value)
        gate=asyncio.Event()
        tasks=[asyncio.create_task(helper(a,'合成甲职业',gate)),asyncio.create_task(helper(b,'合成乙职业',gate))]
        await asyncio.sleep(0.05); gate.set(); await asyncio.gather(*tasks)
        async with w.factory() as db:
            rows={r.user_id:r.value for r in (await db.scalars(select(GlobalUserFact))).all()}
        assert rows=={901:'合成甲职业',902:'合成乙职业'}
        blocker=asyncio.Event()
        async def slow():
            async with w.factory() as db:
                binding=await sb.validate_extraction_source(db,user_id=a.user,character_id=a.char,
                    session_id=a.sid,source_id=a.id,user_message=a.body,expected_uid=a.uid)
            with sb.pin_extraction_source(binding,a.id):
                await blocker.wait()
        task=asyncio.create_task(slow()); await asyncio.sleep(0.02); task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sb._extraction_source.get() is None
    asyncio.run(run())


def test_flag_off_with_explicit_uid_still_rejects_regeneration(world,monkeypatch):
    async def run():
        w=world; p=await create_source(w)
        monkeypatch.setitem(AGENT_FLAGS,'memory_source_proof_v1',False)
        await replace_source(w,p)
        with pytest.raises(sb.SourceIdentityError):
            await ex.extract_single(p.sid,p.char,p.user,p.body,'合成回复',source_id=p.id,source_event_uid=p.uid)
        assert await counts(w)==(0,0,0)
    asyncio.run(run())


def test_pin_clears_after_rejection_and_does_not_taint_next_task(world):
    async def run():
        w=world; p=await create_source(w)
        async with w.factory() as db:
            binding=await sb.validate_extraction_source(db,user_id=p.user,character_id=p.char,
                session_id=p.sid,source_id=p.id,user_message=p.body,expected_uid=p.uid)
        with pytest.raises(sb.SourceIdentityError):
            with sb.pin_extraction_source(binding,p.id):
                await replace_source(w,p)
                await sb.check_extraction_source()
        next_p=await create_source(w,user=902,char=902)
        add(next_p); await worker.process_session(next_p.sid,next_p.char,next_p.user)
        assert await counts(w)==(1,1,1)
        async with w.factory() as db:
            m=(await db.scalars(select(Memory))).one()
            assert m.user_id==902 and m.source_event_uid==next_p.uid
    asyncio.run(run())
