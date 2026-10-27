"""Independent read projections: conditions are not business completion."""
import asyncio
import hashlib
import json
import pytest
from sqlalchemy import select,delete
from _plan_completion_fixture_c9b import c9b_world as c9b_world,ARRIVAL_PLAN,GENERIC_PLAN,GENERIC_DONE
from app.models import Memory,PlanItem,ProspectiveIntent,ChatMessage
from app.memory import plan_projection as projection,plan_completion as kernel


async def memory(w,source_id,content,**kwargs):
    async with w.factory() as db:
        row = Memory(user_id=kwargs.pop("user_id",101),character_id=201,memory_type="event",
            source=kwargs.pop("source","chat"),source_id=source_id,content=content,importance=50,**kwargs)
        db.add(row)
        await db.commit()
        return row.id


async def view(w,mid,content):
    return (await projection.project_memory_results([{"id":mid,"content":content}],201,w.factory))[0]["content"]


def test_condition_consumes_reminder_but_business_stays_open(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(ARRIVAL_PLAN,ago=30)
        ident = await w.plan(source_id=source)
        mid = await memory(w,source,ARRIVAL_PLAN)
        await w.signal()
        assert (await w.state(ident))["status"] == "discharged"
        shown = await view(w,mid,ARRIVAL_PLAN)
        assert "前置条件已有满足证据" in shown
        assert "尚无完成证据：扫描设备A" in shown
        assert "已有完成证据：扫描设备A" not in shown
        async with w.factory() as db:
            item = (await db.execute(select(PlanItem))).scalar_one()
            assert not await kernel.item_is_completed(db,item)
            assert (await db.get(Memory,mid)).content == ARRIVAL_PLAN
            row = await db.get(ProspectiveIntent,ident)
            assert json.loads(row.cue_terms_json)[projection.RECEIPTS]
    asyncio.run(case())


def test_partial_obligations_have_distinct_reminder_and_business_views(c9b_world):
    async def case():
        w = c9b_world
        text = "用户到达质检台后扫描设备A；还需要完成设备H的绝缘校验"
        source = await w.message(text,ago=30)
        ident = await w.plan(text,source_id=source)
        mid = await memory(w,source,text)
        await w.signal()
        assert (await w.state(ident))["status"] == "pending"
        from app.scheduling import prospective_intent as pi
        candidates = await pi.collect_due_promises()
        candidate = next(c for c in candidates if c["pis_id"]==ident)
        assert "设备H" in candidate["content"] and "扫描设备A" not in candidate["content"]
        assert await pi._projected_fire_content(dict(candidate)) == candidate["content"]
        await w.signal("设备H的绝缘校验已经完成")
        assert (await w.state(ident))["status"] == "discharged"
        shown = await view(w,mid,text)
        assert "已有完成证据：设备H的绝缘校验" in shown
        assert "尚无完成证据：扫描设备A" in shown
        assert (await w.state(ident))["content"] == text
    asyncio.run(case())


@pytest.mark.parametrize("text",["我到了校准台","我还没到质检台","如果我到了质检台，才开始",'示例写着："我到了质检台"',"他到了质检台"])
def test_wrong_place_or_unasserted_condition_keeps_obligation(c9b_world,text):
    async def case():
        ident = await c9b_world.plan()
        assert await c9b_world.signal(text) == []
        assert (await c9b_world.state(ident))["status"] == "pending"
    asyncio.run(case())


@pytest.mark.parametrize("change",["delete","edit","time","positive_edit","near_time"])
def test_condition_receipt_invalidates_without_resending_consumed_reminder(c9b_world,change):
    async def case():
        from datetime import timedelta
        w = c9b_world
        source = await w.message(ARRIVAL_PLAN,ago=30)
        ident = await w.plan(source_id=source)
        mid = await memory(w,source,ARRIVAL_PLAN)
        signal = await w.message("我到了质检台",ago=10)
        from app.scheduling import prospective_intent as pi
        await pi.settle_promises_on_user_message(201,301,"我到了质检台",user_id=101,source_message_id=signal)
        async with w.factory() as db:
            msg = await db.get(ChatMessage,signal)
            if change == "delete":
                await db.execute(delete(ChatMessage).where(ChatMessage.id==signal))
            elif change == "edit":
                msg.content = "我还没到质检台"
            elif change == "positive_edit":
                msg.content = "我已经到了质检台"
            elif change == "near_time":
                msg.created_at += timedelta(seconds=1)
            else:
                msg.created_at -= timedelta(days=2)
            await db.commit()
        shown = await view(w,mid,ARRIVAL_PLAN)
        assert "前置条件已有满足证据" not in shown
        assert "尚无完成证据" in shown
        assert (await w.state(ident))["status"] == "discharged"
        assert ident not in await w.candidates()
    asyncio.run(case())


def test_forged_receipt_from_family_member_cannot_satisfy_current_actor(c9b_world):
    async def case():
        w = c9b_world
        ident = await w.plan()
        other = await w.message("我到了质检台",session_id=305,ago=10)
        async with w.factory() as db:
            row = await db.get(ProspectiveIntent,ident)
            item = (await db.execute(select(PlanItem))).scalar_one()
            msg = await db.get(ChatMessage,other)
            meta = json.loads(row.cue_terms_json)
            meta[projection.RECEIPTS] = {item.item_key:{"source_id":other,"event_at":msg.created_at.isoformat(),
                "source_hash":hashlib.sha256(msg.content.encode()).hexdigest()}}
            row.cue_terms_json = json.dumps(meta)
            await db.commit()
        assert ident in await w.candidates()
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


@pytest.mark.parametrize("kwargs",[{"source":"moment"},{"group_id":99},{"speaker_type":"character"},{"user_id":102}])
def test_other_origin_or_actor_memory_is_not_projected(c9b_world,kwargs):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        await w.plan(GENERIC_PLAN,source_id=source)
        await w.signal(GENERIC_DONE)
        mid = await memory(w,source,GENERIC_PLAN,**kwargs)
        assert await view(w,mid,GENERIC_PLAN) == GENERIC_PLAN
    asyncio.run(case())


def test_actual_public_memory_search_exposes_verified_status_and_keeps_raw_text(c9b_world,monkeypatch):
    from app.memory import service as svc,retrieve
    from app.flags.agent_flags import AGENT_FLAGS
    for key in ("memory_peak_cutoff","recall_neighbor_block","recall_entity_match","memory_temporal_recall",
                "perception_source_tag","perception_isolate","status_memory_ttl","memory_supersede","memory_trace_debug"):
        monkeypatch.setitem(AGENT_FLAGS,key,False)
    async def embed(text): return [0.0]*8
    async def no_hits(*args,**kwargs): return []
    async def no_entity(*args,**kwargs): return [],{}
    monkeypatch.setattr(svc,"async_session_factory",c9b_world.factory)
    monkeypatch.setattr(svc,"text_embedding",embed)
    monkeypatch.setattr(svc,"vector_search",no_hits)
    monkeypatch.setattr(svc,"bm25_search",no_hits)
    monkeypatch.setattr(retrieve,"_entity_route",no_entity)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect",no_hits)
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        await w.plan(GENERIC_PLAN,source_id=source)
        mid = await memory(w,source,GENERIC_PLAN,speaker_type="user")
        await w.signal(GENERIC_DONE)
        rows = await svc.search_memories(201,"设备C的绝缘质检",limit=3,user_id=101)
        hit = next(row for row in rows if row["id"]==mid)
        assert "已有完成证据：设备C的绝缘质检" in hit["content"]
        async with w.factory() as db:
            assert (await db.get(Memory,mid)).content == GENERIC_PLAN
    asyncio.run(case())



def test_medication_condition_is_not_work_completion_or_arrival(c9b_world):
    async def case():
        w = c9b_world
        text = "用户吃药后记录合成观察结果"
        source = await w.message(text,ago=30)
        ident = await w.plan(text,source_id=source)
        mid = await memory(w,source,text)
        assert await w.signal() == []
        assert (await w.state(ident))["status"] == "pending"
        assert await w.signal("我已经吃药了") == [ident]
        shown = await view(w,mid,text)
        assert "前置条件已有满足证据" in shown
        assert "尚无完成证据：记录合成观察结果" in shown
    asyncio.run(case())


@pytest.mark.parametrize("shape",["keys_type","keys_value","receipt_type","receipt_value"])
def test_corrupt_projection_metadata_never_falls_through_to_legacy_close(c9b_world,shape):
    async def case():
        w = c9b_world
        ident = await w.plan()
        async with w.factory() as db:
            row = await db.get(ProspectiveIntent,ident)
            meta = json.loads(row.cue_terms_json)
            if shape == "keys_type": meta[projection.KEYS] = None
            elif shape == "keys_value": meta[projection.KEYS] = ["not-a-real-identity"]
            elif shape == "receipt_type": meta[projection.RECEIPTS] = "invalid"
            else: meta[projection.RECEIPTS] = {meta[projection.KEYS][0]:True}
            row.cue_terms_json = json.dumps(meta)
            await db.commit()
        assert await w.signal() == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_unknown_compound_clause_prevents_whole_reminder_close(c9b_world):
    async def case():
        w = c9b_world
        text = "用户到达质检台后扫描设备A；另有一个未明确的约定"
        ident = await w.plan(text)
        assert await w.signal() == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_modified_plugin_content_cannot_receive_private_memory_projection(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        await w.plan(GENERIC_PLAN,source_id=source)
        await w.signal(GENERIC_DONE)
        mid = await memory(w,source,GENERIC_PLAN)
        changed = "synthetic plugin replacement"
        assert await view(w,mid,changed) == changed
    asyncio.run(case())



def test_core_and_anchor_context_use_same_verified_read_projection(c9b_world,monkeypatch):
    from app.memory import core
    from app.agent.context.section_memories import _inject_core_anchors_loops
    monkeypatch.setattr(core,"async_session_factory",c9b_world.factory)
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        await w.plan(GENERIC_PLAN,source_id=source)
        mid = await memory(w,source,GENERIC_PLAN,is_core=True)
        async with w.factory() as db:
            row = await db.get(Memory,mid)
            row.importance = 90
            await db.commit()
        await w.signal(GENERIC_DONE)
        core_text,anchors_text,_ = await _inject_core_anchors_loops(201,101,{"core_limit":10,"anchor_limit":5})
        assert "已有完成证据：设备C的绝缘质检" in core_text
        assert "已有完成证据：设备C的绝缘质检" in anchors_text
        async with w.factory() as db:
            assert (await db.get(Memory,mid)).content == GENERIC_PLAN
    asyncio.run(case())



@pytest.mark.parametrize("text",["我到了质检台，这是同事说的","我到了质检台，也许只是演练","设备C的绝缘质检已经完成，这是同事说的"])
def test_trailing_report_or_uncertainty_is_not_current_actor_evidence(c9b_world,text):
    async def case():
        w = c9b_world
        plan = GENERIC_PLAN if text.startswith("设备") else ARRIVAL_PLAN
        ident = await w.plan(plan)
        assert await w.signal(text) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())


def test_legacy_sourced_plan_can_be_indexed_without_guessing_by_text(c9b_world):
    async def case():
        w = c9b_world
        source = await w.message(GENERIC_PLAN,ago=30)
        async with w.factory() as db:
            row = ProspectiveIntent(user_id=101,character_id=201,chat_session_id=301,
                source_message_id=source,content=GENERIC_PLAN,kind="promise",due_end=w.due,
                cue_terms_json=json.dumps({"side":"user","trigger":"clock","terms":[]}),status="pending")
            db.add(row)
            await db.commit()
            ident = row.id
        await w.signal(GENERIC_DONE)
        assert ident not in await w.candidates()
        assert (await w.state(ident))["status"] == "discharged"
    asyncio.run(case())


@pytest.mark.parametrize("signal",["我进门了","我回来了，今天走得早"])
def test_doorstep_alias_only_consumes_condition_not_work(c9b_world,signal):
    async def case():
        w = c9b_world
        text = "用户到达门口后给设备P上电"
        source = await w.message(text,ago=30)
        ident = await w.plan(text,source_id=source)
        mid = await memory(w,source,text)
        assert await w.signal(signal) == [ident]
        shown = await view(w,mid,text)
        assert "前置条件已有满足证据" in shown
        assert "尚无完成证据：给设备P上电" in shown
    asyncio.run(case())


@pytest.mark.parametrize("signal",["我进门了","我回来了，今天走得早",
    "他进门了","同事回来了","我没进门","我回来了，这是同事说的",
    "如果我回来了","我回来了？"])
def test_doorstep_alias_cannot_prove_named_destination(c9b_world,signal):
    async def case():
        w = c9b_world
        ident = await w.plan()
        assert await w.signal(signal) == []
        assert (await w.state(ident))["status"] == "pending"
    asyncio.run(case())
