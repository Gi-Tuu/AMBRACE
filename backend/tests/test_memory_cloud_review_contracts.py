"""Primary reviewer contracts; synthetic evidence and isolated SQLite only."""
import asyncio
from datetime import datetime
import pytest
from app.memory import extract_queue as q
from app.memory import extractor as ex
from app.memory import extraction_worker as worker

@pytest.mark.parametrize("case", ["valid", "foreign_source", "deleted_user", "disabled_user"])
def test_worker_rechecks_durable_source_and_live_account(monkeypatch, tmp_path, case):
    from _dbclone import clone_engine, make_session_factory
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat import ChatSession, ChatMessage
    engine = clone_engine(tmp_path / "review.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(q, "_QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(ex, "_mark_batch_taken", lambda *args: None)
    calls = []
    async def run(tool, payload, **kwargs):
        calls.append(payload)
        await ex._mark_processed(payload["source_id"])
        return {"status": "ok"}
    monkeypatch.setattr("app.agent.internal_runner.run_internal", run)
    async def scenario():
        async with factory() as db:
            db.add_all([User(id=31001, username="SYNTHETIC_REVIEW_A", nickname="SYNTHETIC_A"), User(id=31002, username="SYNTHETIC_REVIEW_B", nickname="SYNTHETIC_B")])
            await db.flush()
            db.add_all([AICharacter(id=32001, user_id=31001, name="SYNTHETIC_CHAR_A"), AICharacter(id=32002, user_id=31002, name="SYNTHETIC_CHAR_B")])
            await db.flush()
            db.add_all([ChatSession(id=33001, user_id=31001, character_id=32001), ChatSession(id=33002, user_id=31002, character_id=32002)])
            await db.flush()
            db.add_all([ChatMessage(id=34001, session_id=33001, sender_type="user", content="SYNTHETIC_SOURCE_A"), ChatMessage(id=34002, session_id=33002, sender_type="user", content="SYNTHETIC_SOURCE_B")])
            user = await db.get(User, 31001)
            if case == "deleted_user": user.deleted_at = datetime(2026, 1, 1)
            elif case == "disabled_user": user.disabled_at = datetime(2026, 1, 1)
            await db.commit()
        q.add(33001, {"source_id": 34002 if case == "foreign_source" else 34001, "ts": 0, "user_message": "SYNTHETIC_QUEUE_TEXT", "ai_response": "SYNTHETIC_AI"})
        await worker.drain_due()
        assert len(calls) == (1 if case == "valid" else 0), ("must validate source ownership/account before extraction", case, calls)
    try: asyncio.run(scenario())
    finally: asyncio.run(engine.dispose())
