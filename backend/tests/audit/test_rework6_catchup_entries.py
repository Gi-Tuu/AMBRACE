import asyncio

import pytest
from sqlalchemy import delete, select

from .p1_env import env as env, world as world
from .test_source_identity_p1 import create_source
from app.flags.agent_flags import AGENT_FLAGS
from app.memory import extractor as ex
from app.models import ChatMessage, ChatSession, ProcessedExtraction


async def seed_entry(w, content, *, reuse=False):
    async with w.factory() as db:
        session=ChatSession(user_id=901,character_id=901)
        db.add(session)
        await db.flush()
        msg=ChatMessage(session_id=session.id,sender_type='user',content=content)
        db.add(msg)
        await db.flush()
        if reuse:
            old_id=msg.id
            ai=ChatMessage(session_id=session.id,sender_type='ai',content='合成回复')
            db.add(ai)
            await db.flush()
            await db.execute(delete(ChatMessage).where(ChatMessage.id.in_([old_id,ai.id])))
            msg=ChatMessage(session_id=session.id,sender_type='user',content=content)
            db.add(msg)
            await db.flush()
            assert msg.id==old_id
        db.add(ChatMessage(session_id=session.id,sender_type='ai',content='合成回复'))
        await db.commit()
        return session.id, msg.id


@pytest.mark.parametrize('kind',['image','file','voice','emoji','batch'])
@pytest.mark.parametrize('proof',[True,False])
def test_catchup_nontext_or_batch_is_only_extracted_without_proof(world,kind,proof):
    async def run():
        w=world
        AGENT_FLAGS['memory_source_proof_v1']=proof
        await seed_entry(w, f'合成{kind}入口正文')
        await ex.catchup_extract_all()
        if proof:
            assert w.llm.await_count==0
            async with w.factory() as db:
                assert (await db.scalars(select(ProcessedExtraction))).all()==[]
        else:
            assert w.llm.await_count==1
            async with w.factory() as db:
                assert len((await db.scalars(select(ProcessedExtraction))).all())==1
    asyncio.run(run())


def test_catchup_normal_text_with_proof_is_extracted(world):
    async def run():
        w=world
        p=await create_source(w,body='合成普通文本补采')
        async with w.factory() as db:
            db.add(ChatMessage(session_id=p.sid,sender_type='ai',content='合成回复'))
            await db.commit()
        await ex.catchup_extract_all()
        assert w.llm.await_count==1
        async with w.factory() as db:
            assert len((await db.scalars(select(ProcessedExtraction))).all())==1
    asyncio.run(run())


def test_catchup_nontext_reused_id_with_proof_is_not_extracted(world):
    async def run():
        w=world
        AGENT_FLAGS['memory_source_proof_v1']=True
        _,mid=await seed_entry(w,'合成图片复用正文',reuse=True)
        await ex.catchup_extract_all()
        assert w.llm.await_count==0
        async with w.factory() as db:
            assert await db.get(ChatMessage,mid) is not None
            assert (await db.scalars(select(ProcessedExtraction))).all()==[]
    asyncio.run(run())
