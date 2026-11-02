import asyncio
from test_memory_source_queue_m2b import _seed_and_message


def test_worker_rejects_queued_text_that_differs_from_signed_message(monkeypatch):
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
        msg_id = await _seed_and_message(8501, 8511, 8521)
        await ex.add_chat_memory_extraction(8521, 8511, 8501, "different synthetic payload", "synthetic reply", source_id=msg_id)
        await worker.process_session(8521, 8511, 8501)
    asyncio.run(run())
    assert calls == [], "worker invoked extraction with queued text that differs from signed source"
