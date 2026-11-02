import asyncio
from test_memory_source_queue_m2b import _seed_and_message


def test_unidentified_queue_item_must_not_invoke_model_when_proof_enabled(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extractor as ex, extraction_worker as worker
    from app.agent import internal_runner
    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    calls = []
    async def fake_model(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "ok"}
    monkeypatch.setattr(internal_runner, "run_internal", fake_model)
    async def run():
        await _seed_and_message(8601, 8611, 8621)
        await ex.add_chat_memory_extraction(8621, 8611, 8601, "synthetic legacy payload", "synthetic reply", source_id=None)
        await worker.process_session(8621, 8611, 8601)
    asyncio.run(run())
    assert calls == [], "proof-enabled worker invoked extraction for a source-less queue item"
