"""Synthetic generic-assertion adapter tests, independent of reminder display."""
import asyncio
import json
import pytest
from sqlalchemy import select,func
from _plan_completion_fixture_c9b import c9b_world as c9b_world, GENERIC_PLAN, GENERIC_DONE
from app.memory import plan_completion_adapter as adapter
from app.models import PlanItem,PlanCompletionEvidence,ProspectiveIntent
from app.scheduling import prospective_intent as pi


@pytest.mark.parametrize("text,target", [
    ("合成探头Z校准已经完成","合成探头Z校准"),
    ("我已经完成了合成探头Z校准","合成探头Z校准"),
    ("合成探头Z校准已做完了","合成探头Z校准"),
    ("合成探头Z校准还没完成",None),
    ("如果合成探头Z校准已经完成，下一步读取结果",None),
    ('示例写着："合成探头Z校准已经完成"',None),
    ("他已经完成了合成探头Z校准",None),
    ("合成探头Z校准已经完成了吗？",None),
    ("这件事已经完成",None),
    ("合成探头Z校准可能已经完成",None),
])
def test_assertion_grammar_is_conservative(text,target):
    claims = adapter.completion_assertions(text)
    assert ([entry[1] for entry in claims] == [target]) if target else not claims


@pytest.mark.parametrize("late", [False,True])
def test_both_write_orders_bind_same_durable_identity(c9b_world,late):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        if not late:
            ident = await w.plan(GENERIC_PLAN,source_id=source)
        await w.signal(GENERIC_DONE)
        await w.message("无关的合成闲聊")
        if late:
            ident = await w.plan(GENERIC_PLAN,source_id=source)
        async with w.factory() as db:
            items = (await db.execute(select(PlanItem))).scalars().all()
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert len(items) == 1
            assert ev.item_id == items[0].id
            assert ev.binding_kind == "literal"
            row = await db.get(ProspectiveIntent,ident)
            assert json.loads(row.cue_terms_json)[adapter.ITEM_KEYS] == [items[0].item_key]
        await w.plan(GENERIC_PLAN,source_id=source)
        await pi.settle_promises_on_user_message(201,301,GENERIC_DONE)
        async with w.factory() as db:
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 1
            assert await db.scalar(select(func.count()).select_from(PlanCompletionEvidence)) == 1
    asyncio.run(case())


def test_same_literal_independent_sources_remain_ambiguous_after_display_merge(c9b_world):
    async def case():
        w = c9b_world
        first = await w.plan(GENERIC_PLAN)
        second = await w.plan(GENERIC_PLAN)
        assert first == second  # Existing display aggregation is allowed.
        await w.signal(GENERIC_DONE)
        async with w.factory() as db:
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 2
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert ev.item_id is None
            row = await db.get(ProspectiveIntent,first)
            assert len(json.loads(row.cue_terms_json)[adapter.ITEM_KEYS]) == 2
    asyncio.run(case())


@pytest.mark.parametrize("merge",[False,True])
def test_ledger_failure_rolls_back_reminder_and_merged_metadata(c9b_world,monkeypatch,merge):
    async def case():
        w = c9b_world
        if merge:
            await w.plan(GENERIC_PLAN)
        original = adapter.register_for_intent
        async def fail_after_write(*args,**kwargs):
            await original(*args,**kwargs)
            raise RuntimeError("synthetic ledger commit failure")
        monkeypatch.setattr(adapter,"register_for_intent",fail_after_write)
        with pytest.raises(RuntimeError,match="synthetic ledger commit failure"):
            await w.plan(GENERIC_PLAN)
        async with w.factory() as db:
            expected = int(merge)
            assert await db.scalar(select(func.count()).select_from(ProspectiveIntent)) == expected
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == expected
            if merge:
                row = (await db.execute(select(ProspectiveIntent))).scalar_one()
                assert len(json.loads(row.cue_terms_json)[adapter.ITEM_KEYS]) == 1
    asyncio.run(case())


def test_similar_target_is_not_bound(c9b_world):
    async def case():
        w = c9b_world
        await w.plan(GENERIC_PLAN)
        await w.signal("设备C的绝缘检测已经完成")
        async with w.factory() as db:
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert ev.item_id is None
    asyncio.run(case())



def test_plan_end_recovers_missed_message_hook_beyond_one_history_page(c9b_world):
    async def case():
        from app.models import ChatMessage
        from datetime import timedelta
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        await w.message(GENERIC_DONE,ago=20)  # No settlement hook was run.
        async with w.factory() as db:
            db.add_all([ChatMessage(session_id=301,sender_type="user",content=f"合成无关记录 {i}",
                created_at=w.now-timedelta(minutes=10)+timedelta(seconds=i)) for i in range(140)])
            await db.commit()
        await w.plan(GENERIC_PLAN,source_id=source)
        async with w.factory() as db:
            item = (await db.execute(select(PlanItem))).scalar_one()
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert ev.item_id == item.id
    asyncio.run(case())



def test_later_extraction_of_second_historical_plan_revokes_literal_uniqueness(c9b_world):
    async def case():
        w = c9b_world
        first = await w.message(GENERIC_PLAN,ago=30)
        second = await w.message(GENERIC_PLAN,ago=20)
        await w.signal(GENERIC_DONE,ago=10)
        await w.plan(GENERIC_PLAN,source_id=first)
        async with w.factory() as db:
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert ev.item_id is not None
        await w.plan(GENERIC_PLAN,source_id=second)
        async with w.factory() as db:
            ev = (await db.execute(select(PlanCompletionEvidence))).scalar_one()
            assert ev.item_id is None and ev.binding_kind == "ambiguous"
            await adapter.reconcile_scope(db,adapter.kernel.PlanScope(101,201,301))
            assert ev.item_id is None  # No tie-breaking by extraction order.
    asyncio.run(case())


@pytest.mark.parametrize("source_text", [
    '示例写着："用户计划完成设备C的绝缘质检"',
    '如果用户计划完成设备C的绝缘质检，我会记录。',
    '他计划完成设备C的绝缘质检',
])
def test_model_output_cannot_fabricate_actor_assertion_from_source(c9b_world,source_text):
    async def case():
        source = await c9b_world.message(source_text,ago=20)
        await c9b_world.plan(GENERIC_PLAN,source_id=source)
        async with c9b_world.factory() as db:
            assert await db.scalar(select(func.count()).select_from(PlanItem)) == 0
    asyncio.run(case())


def test_item_ordinal_comes_from_source_not_model_output_order(c9b_world):
    async def case():
        w = c9b_world
        first = "用户计划完成合成探头Z的校准"
        second = "用户计划完成合成探头Y的校准"
        source = await w.message(first+"；"+second,ago=20)
        await w.plan(second,source_id=source)
        async with w.factory() as db:
            item = (await db.execute(select(PlanItem))).scalar_one()
            assert item.ordinal == 1
            old_key = item.item_key
        await w.plan(first+"；"+second,source_id=source)
        async with w.factory() as db:
            items = (await db.execute(select(PlanItem).order_by(PlanItem.ordinal))).scalars().all()
            assert len(items) == 2
            assert items[1].item_key == old_key
    asyncio.run(case())
