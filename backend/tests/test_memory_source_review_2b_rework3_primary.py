import asyncio
from sqlalchemy import text
from test_memory_source_queue_m2b import _seed_and_message


def test_worker_rejects_source_content_edited_after_enqueue(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker, extractor as ex
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    async def run():
        msg_id = await _seed_and_message(8401, 8411, 8421)
        async with async_session_factory() as db:
            msg = await db.get(ChatMessage, msg_id)
            uid = msg.source_event_uid
            await db.execute(text("UPDATE chat_messages SET content=:content WHERE id=:id"), {"content":"synthetic edited source", "id":msg_id})
            await db.commit()
            session = await db.get(ChatSession, 8421)
            return await worker._source_belongs_to_session(ex, session, msg_id, uid)
    assert asyncio.run(run()) is False, "worker accepted edited source before model invocation"
