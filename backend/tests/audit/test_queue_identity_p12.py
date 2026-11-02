import asyncio
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source, replace_source, add, counts, states
from app.api.chat import delete_message
from app.memory import extract_queue as queue, extractor as ex, extraction_worker as worker
from app.models import Memory, ProcessedExtraction


def rows():
    with closing(sqlite3.connect(queue.database_path())) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('SELECT * FROM extraction_tasks ORDER BY id')]


def legacy_payload(p):
    return {'source_id':p.id,'ts':0,'user_message':p.body,'ai_response':'合成旧回复'}


async def delete_source(w,p):
    async with w.factory() as db:
        await delete_message(p.id,db=db,user_id=p.user,lang='en')


@pytest.mark.parametrize('terminal',['dead','succeeded'])
def test_new_uid_is_not_blocked_by_legacy_terminal_key(world,terminal):
    async def run():
        w=world; p=await create_source(w,body='合成同一句来源')
        queue.add(p.sid,legacy_payload(p))
        from app.flags.agent_flags import AGENT_FLAGS
        if terminal=='dead':
            claim,=queue.take(p.sid); assert queue.abandon(claim,'synthetic_dead')
        else:
            AGENT_FLAGS['memory_source_proof_v1']=False
            await worker.process_session(p.sid,p.char,p.user)
            assert states()==[('succeeded',)]
            assert (await counts(w))[0]==1
        await delete_source(w,p)
        new=await create_source(w,sid=p.sid,body='合成同一句来源')
        assert (new.sid,new.id)==(p.sid,p.id) and new.uid!=p.uid
        AGENT_FLAGS['memory_source_proof_v1']=True
        add(new)
        assert len(rows())==2
        await worker.process_session(new.sid,new.char,new.user)
        assert states()[0]==(terminal,)
        assert states()[1]==('succeeded',)
        async with w.factory() as db:
            mem=(await db.scalars(select(Memory))).all()
            proc=(await db.scalars(select(ProcessedExtraction))).all()
        assert len(mem)==1 and mem[0].source_event_uid==new.uid
        assert [r.user_message_id for r in proc]==[new.id]
    asyncio.run(run())


def test_same_uid_replay_remains_one_task_and_legacy_scope_unchanged(world):
    async def run():
        w=world; p=await create_source(w); add(p); add(p)
        assert len(rows())==1 and rows()[0]['event_key']==f'source:{p.uid}'
        legacy=await create_source(w,user=902,char=902,body='合成旧口径')
        queue.add(legacy.sid,legacy_payload(legacy))
        assert queue.is_pending(legacy.id,None) is True
        assert queue.is_pending(legacy.id,legacy.uid) is False
        claim,=queue.take(legacy.sid); queue.abandon(claim,'synthetic')
        assert queue.is_pending(legacy.id,None) is True
        assert queue.is_pending(legacy.id,legacy.uid) is False
    asyncio.run(run())


def test_remove_source_with_uid_spares_legacy_evidence(world):
    async def run():
        w=world; p=await create_source(w)
        queue.add(p.sid,legacy_payload(p))
        new=await replace_source(w,p)
        add(new)
        queue.remove_source(new.sid,new.id,new.uid)
        states_by_key={r['event_key']:r['state'] for r in rows()}
        assert states_by_key[f'message:{p.id}']=='pending'
        assert states_by_key[f'source:{new.uid}']=='succeeded'
    asyncio.run(run())


def test_catchup_uses_exact_uid_not_dead_source_id(world,monkeypatch):
    async def run():
        w=world; p=await create_source(w,body='合成补采旧句')
        queue.add(p.sid,legacy_payload(p))
        claim,=queue.take(p.sid); queue.abandon(claim,'synthetic_dead')
        await delete_source(w,p)
        new=await create_source(w,sid=p.sid,body='合成补采旧句')
        async with w.factory() as db:
            from app.models.chat import ChatMessage
            db.add(ChatMessage(session_id=new.sid,sender_type='ai',content='合成补采回复'))
            await db.commit()
        monkeypatch.setattr(ex,'_throttle_open',lambda cid:True)
        await ex.catchup_extract_all()
        async with w.factory() as db:
            mem=(await db.scalars(select(Memory))).all()
            proc=(await db.scalars(select(ProcessedExtraction))).all()
        assert len(mem)==1 and mem[0].source_event_uid==new.uid
        assert [r.user_message_id for r in proc]==[new.id]
    asyncio.run(run())
