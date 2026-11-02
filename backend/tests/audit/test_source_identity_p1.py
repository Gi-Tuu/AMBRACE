import asyncio
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, select, text

from .p1_env import env as env, world as world
from app.models import ChatSession, ChatMessage, Memory, StageMemory, ProcessedExtraction, User, AICharacter
from app.memory import source_binding as sb, extractor as ex, extraction_worker as worker, extract_queue as queue
from app.flags.agent_flags import AGENT_FLAGS
from app.agent import internal_runner


async def create_source(w,user=901,char=901,body='合成源消息',sid=None):
    async with w.factory() as db:
        if sid is None:
            session=ChatSession(user_id=user,character_id=char)
            db.add(session); await db.flush(); sid=session.id
        msg=ChatMessage(session_id=sid,sender_type='user',content=body)
        db.add(msg); await db.flush(); await db.refresh(msg)
        uid=await sb.sign_chat_message(db,msg,user_id=user,character_id=char)
        await db.commit()
        return SimpleNamespace(sid=sid,id=msg.id,uid=uid,user=user,char=char,body=body)


async def replace_source(w,old,*,user=None,char=None,body=None):
    user=old.user if user is None else user
    char=old.char if char is None else char
    async with w.factory() as db:
        await db.execute(delete(ChatMessage).where(ChatMessage.id==old.id))
        if user!=old.user or char!=old.char:
            await db.execute(delete(ChatSession).where(ChatSession.id==old.sid))
        await db.commit()
    new=await create_source(w,user,char,old.body if body is None else body,sid=old.sid if (user,char)==(old.user,old.char) else None)
    assert new.id==old.id and new.sid==old.sid and new.uid!=old.uid
    return new


def add(p, uid=True):
    data={'source_id':p.id,'ts':0,'user_message':p.body,'ai_response':'合成回复'}
    if uid: data['source_event_uid']=p.uid
    queue.add(p.sid,data)


async def counts(w):
    async with w.factory() as db:
        return tuple([len((await db.scalars(select(model))).all()) for model in [Memory,StageMemory,ProcessedExtraction]])


def states():
    with closing(sqlite3.connect(queue.database_path())) as db:
        return db.execute('SELECT state FROM extraction_tasks ORDER BY id').fetchall()


@pytest.mark.parametrize('kind',['foreign','same_owner_same_text','missing_uid'])
def test_queued_old_identity_rejected_before_model(world,kind):
    async def run():
        w=world; p=await create_source(w)
        add(p,uid=kind!='missing_uid')
        if kind!='missing_uid':
            await replace_source(w,p,user=902 if kind=='foreign' else 901,char=902 if kind=='foreign' else 901)
        await worker.process_session(p.sid,p.char,p.user)
        assert w.llm.await_count==0 and await counts(w)==(0,0,0)
        assert states()==[('dead',)]
    asyncio.run(run())


def test_delayed_enqueue_does_not_adopt_other_tenant_uid(world):
    async def run():
        w=world; p=await create_source(w)
        await replace_source(w,p,user=902,char=902)
        await ex.add_chat_memory_extraction(p.sid,p.char,p.user,p.body,'合成回复',source_id=p.id)
        await worker.process_session(p.sid,p.char,p.user)
        assert w.llm.await_count==0 and await counts(w)==(0,0,0)
    asyncio.run(run())


def test_runner_transports_expected_uid(world,monkeypatch):
    capture=AsyncMock(return_value=0)
    monkeypatch.setattr(ex,'extract_single',capture)
    asyncio.run(internal_runner._dispatch('memory_extract',{})({'session_id':1,'user_id':901,'character_id':901,'source_id':2,'source_event_uid':'original-uid'}))
    assert capture.call_args.kwargs['source_event_uid']=='original-uid'


@pytest.mark.parametrize('mutation',['generation','content','foreign','disabled','flag_off_generation'])
def test_identity_changed_during_model_blocks_all_writes(world,monkeypatch,mutation):
    async def run():
        w=world; p=await create_source(w); add(p)
        async def llm(**kwargs):
            if mutation in ('generation','flag_off_generation'):
                await replace_source(w,p)
                if mutation=='flag_off_generation': AGENT_FLAGS['memory_source_proof_v1']=False
            elif mutation=='foreign':
                await replace_source(w,p,user=902,char=902)
            else:
                async with w.factory() as db:
                    if mutation=='content':
                        await db.execute(text('UPDATE chat_messages SET content=:c WHERE id=:id'),{'c':'已改写的新来源','id':p.id})
                    else:
                        from datetime import datetime
                        (await db.get(User,901)).disabled_at=datetime(2026,1,1)
                    await db.commit()
            return 'EVENTS: 合成旧内容 | 3\nSTAGE: 合成旧舞台 | 3\nBIO: 合成旧自述\nSTATUS: 合成旧状态\n'
        monkeypatch.setattr(ex,'llm_call',llm)
        await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(0,0,0)
        async with w.factory() as db:
            char=await db.get(AICharacter,901)
            assert (char.self_statement,char.current_status,char.relationship_summary)==w.initial_character
        assert states()!=[('succeeded',)]
    asyncio.run(run())


@pytest.mark.parametrize('shared',[False,True])
def test_valid_source_and_family_shared_character_still_write(world,shared):
    async def run():
        w=world
        if shared:
            async with w.factory() as db:
                (await db.get(User,902)).parent_id=901
                await db.commit()
        p=await create_source(w,user=902 if shared else 901,char=901)
        add(p); await worker.process_session(p.sid,p.char,p.user)
        assert await counts(w)==(1,1,1) and states()==[('succeeded',)]
        async with w.factory() as db:
            m=(await db.scalars(select(Memory))).one()
            assert (m.user_id,m.character_id,m.source_event_uid)==(p.user,p.char,p.uid)
    asyncio.run(run())
