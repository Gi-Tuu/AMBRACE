import asyncio
import time
from test_memory_source_queue_m2b import _seed_and_message


def test_old_queue_item_missing_event_uid_does_not_invoke_model(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extraction_worker as worker, extract_queue as queue
    from app.agent import internal_runner
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    async def fake_model(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok"}
    monkeypatch.setattr(internal_runner, "run_internal", fake_model)
    async def run():
        msg_id = await _seed_and_message(8701, 8711, 8721)
        queue.add(8721, {"user_message": "queue source", "ai_response": "synthetic reply", "source_id": msg_id, "source_event_uid": None, "ts": time.time()})
        await worker.process_session(8721, 8711, 8701)
    asyncio.run(run())
    assert calls == [], "worker accepted legacy queue item with missing event UID"
