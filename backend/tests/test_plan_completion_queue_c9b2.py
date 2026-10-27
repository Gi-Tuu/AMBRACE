"""Real worker/extractor/SQLite queue failure and ack replay contracts."""
import asyncio
import sqlite3
import pytest
from sqlalchemy import select,func
from _plan_completion_fixture_c9b import c9b_world as c9b_world, GENERIC_PLAN
from app.models import ProcessedExtraction,PlanItem,ProspectiveIntent


@pytest.mark.parametrize("fault",["ledger","receipt","ack","ack_false"])
def test_failed_business_write_or_ack_never_loses_work(c9b_world,monkeypatch,tmp_path,fault):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extract_queue as queue,extraction_worker as worker,extractor
    from app.memory import plan_completion_adapter as adapter
    monkeypatch.setattr(queue,"_QUEUE_FILE",tmp_path/"queue.json")
    monkeypatch.setitem(AGENT_FLAGS,"prospective_intent_enabled",True)
    monkeypatch.setitem(AGENT_FLAGS,"curated_knowledge",False)
    calls = []
    async def llm(**kwargs):
        calls.append(kwargs)
        day = c9b_world.due.strftime("%Y-%m-%d")
        return ("USER_INFO: 无\nEVENTS: 无\nPREFERENCES: 无\nBIO: 无\nSTATUS: 无\n"
                "RELATIONSHIP: 无\nCURATED: 无\n"
                f"INTENT: {GENERIC_PLAN} | promise | {day}~{day} | 设备校验 | high")
    monkeypatch.setattr(extractor,"llm_call",llm)
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        queue.add(301,{"source_id":source,"ts":0,"user_message":GENERIC_PLAN,"ai_response":"合成记录"})
        original = adapter.register_for_intent if fault == "ledger" else queue.complete
        async def broken_ledger(*args,**kwargs):
            await original(*args,**kwargs)
            raise OSError("synthetic ledger write failure")
        def broken_ack(*args,**kwargs):
            raise OSError("synthetic ack write failure")
        async def missing_receipt(source_id):
            return None
        with monkeypatch.context() as patch:
            if fault == "ledger":
                patch.setattr(adapter,"register_for_intent",broken_ledger)
            elif fault == "receipt":
                patch.setattr(extractor,"_mark_processed",missing_receipt)
            elif fault == "ack":
                patch.setattr(queue,"complete",broken_ack)
            else:
                patch.setattr(queue,"complete",lambda pair: False)
            await worker.process_session(301,201,101)
        async with w.factory() as db:
            committed = fault != "ledger"
            assert (await db.get(ProcessedExtraction,source) is not None) is (fault in ("ack","ack_false"))
            for model in (PlanItem,ProspectiveIntent):
                assert await db.scalar(select(func.count()).select_from(model)) == int(committed)
        with sqlite3.connect(queue.database_path()) as db:
            state,payload = db.execute("SELECT state,payload FROM extraction_tasks").fetchone()
            assert state == ("leased" if fault == "ack_false" else "retry")
            assert "source_id" in payload
            # A false lease-token ack leaves durable work until normal expiry recovery.
            db.execute("UPDATE extraction_tasks SET available_at=0,lease_until=0")
        await worker.process_session(301,201,101)
        async with w.factory() as db:
            assert await db.get(ProcessedExtraction,source) is not None
            for model in (PlanItem,ProspectiveIntent):
                assert await db.scalar(select(func.count()).select_from(model)) == 1
        with sqlite3.connect(queue.database_path()) as db:
            assert db.execute("SELECT state,payload FROM extraction_tasks").fetchone() == ("succeeded","{}")
        # Existing idempotent tool runner tries twice before the queue-level retry.
        assert len(calls) == (3 if fault == "ledger" else 2 if fault == "receipt" else 1)
    asyncio.run(case())
