# -*- coding: utf-8 -*-
"""Ariadne 模块G：Prospective Intent 状态机 / 读写 / extractor 便车 / TriggerSource / 兑现测试（2026-09-04）。

覆盖要点：
- upsert_intent 幂等：同 source_message_id+content 只一行；promise 无 due 且无 cue 不写；cue 无 cue_terms 不写；
- 状态机：due 到期被 collect_due_promises 捞到；mark_discharged_many 后不再捞；due_end+7 天 → expired；cancel_by_content → cancelled；
- match_cue_intents：子串命中置 matched、返回命中；不命中不动；零 LLM（mock 断言无 chat_completion 调用）；
- extractor 便车：flag 关→不写；flag 开→INTENT/CURATED 落表；CURATED 升级且不重复进 preference memory；
- TriggerSource：prospective_intent_trigger 关→collect 空；开→到期 promise 产出 priority=1 候选；
- run_prospective_due：成功发送→discharged；失败→不 discharged。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import ProspectiveIntent


@pytest.fixture()
def pi_db(monkeypatch):
    """临时库：create_all + 把 database / prospective_intent / extractor / facts 的工厂指向临时工厂。"""
    tmp = tempfile.mkdtemp(prefix="ariadne_g_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=11, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    import app.memory.extractor as ex
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    monkeypatch.setattr(ex, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _rows_of(factory):
    async def _run():
        async with factory() as db:
            rows = (await db.execute(select(ProspectiveIntent).order_by(ProspectiveIntent.id))).scalars().all()
            return [(r.id, r.content, r.kind, r.status, r.cue_terms_json) for r in rows]
    return asyncio.run(_run())


def test_upsert_idempotent_and_conservative(pi_db):
    from app.scheduling.prospective_intent import upsert_intent
    factory = pi_db

    # 同 source_message_id+content 两次 → 只一行
    id1 = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                                    kind="promise", due_end=datetime(2026, 9, 12, 23, 59),
                                    source_message_id=100, chat_session_id=7))
    id2 = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                                    kind="promise", due_end=datetime(2026, 9, 12, 23, 59),
                                    source_message_id=100, chat_session_id=7))
    assert id1 == id2
    rows = _rows_of(factory)
    assert len(rows) == 1

    # promise 缺时间又缺线索 → 不写
    r = asyncio.run(upsert_intent(user_id=1, character_id=11, content="改天请你吃火锅", kind="promise"))
    assert r is None
    # cue 缺线索词 → 不写
    r2 = asyncio.run(upsert_intent(user_id=1, character_id=11, content="樱花开了叫我", kind="cue"))
    assert r2 is None
    rows = _rows_of(factory)
    assert len(rows) == 1


def test_state_machine_collect_discharge_expire_cancel(pi_db):
    from app.scheduling.prospective_intent import (
        upsert_intent, collect_due_promises, mark_discharged_many,
        cancel_by_content, _now_naive,
    )
    factory = pi_db
    now = _now_naive()
    due = now - timedelta(minutes=5)
    past_grace = now - timedelta(days=8)   # due_end 超过 7 天宽限

    fut = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                                    kind="promise", due_end=due, source_message_id=101, chat_session_id=7))
    dup = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅2",
                                    kind="promise", due_end=due, source_message_id=102, chat_session_id=7))
    old = asyncio.run(upsert_intent(user_id=1, character_id=11, content="很久以前的承诺",
                                    kind="promise", due_end=past_grace, source_message_id=103, chat_session_id=7))

    # collect_due_promises 顺手 expire_overdue：old 超 7 天 → expired，不再被捞到
    due_list = asyncio.run(collect_due_promises())
    ids = {c["pis_id"] for c in due_list}
    assert fut in ids and dup in ids
    assert old not in ids  # 已 expired

    # 兑现：discharged 后不再捞
    asyncio.run(mark_discharged_many([fut]))
    due_list2 = asyncio.run(collect_due_promises())
    assert fut not in {c["pis_id"] for c in due_list2}

    # cancel_by_content：用户说算了 → cancelled
    n = asyncio.run(cancel_by_content(11, "算了不用了，下周带你去吃火锅2"))
    assert n >= 1
    rows = {r[0]: r[3] for r in _rows_of(factory)}
    assert rows[fut] == "discharged"
    assert rows[old] == "expired"
    assert rows[dup] == "cancelled"


def test_match_cue_intents_zero_llm(pi_db, monkeypatch):
    from app.scheduling.prospective_intent import upsert_intent, match_cue_intents

    asyncio.run(upsert_intent(user_id=1, character_id=11, content="樱花开了提醒我拍照",
                              kind="cue", cue_terms=["樱花", "开花"], source_message_id=201))

    # mock: 若代码误调 LLM 会在此抛错
    async def _boom(*a, **k):
        raise AssertionError("match_cue_intents 不应调用 LLM")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _boom)

    hits = asyncio.run(match_cue_intents(11, "我看见樱花开了，好美"))
    assert len(hits) == 1 and hits[0].status == "matched"
    # 再命中一次也返回（matched 仍参与）
    hits2 = asyncio.run(match_cue_intents(11, "又提到樱花"))
    assert len(hits2) == 1
    # 不命中 → 空
    hits3 = asyncio.run(match_cue_intents(11, "今天天气不错"))
    assert hits3 == []


def test_extractor_ride_along(pi_db, monkeypatch):
    from app.memory.extractor import extract_single
    import app.memory as memory_mod
    factory = pi_db
    calls = []

    async def _fake_llm(**kw):
        return (
            "USER_INFO: 无 | 1\n"
            "EVENTS: 无 | 1\n"
            "PREFERENCES: 用户喜欢喝美式咖啡 | 5\n"
            "BIO: 无 | 1\n"
            "STATUS: 无 | 1\n"
            "RELATIONSHIP: 无 | 1\n"
            "STAGE: 无 | 1\n"
            "CURATED: 用户喜欢喝美式咖啡 | preference_profile | 5\n"
            "INTENT: 下周带你去吃火锅 | promise | 2026-09-05~2026-09-12 | 无\n"
        )

    async def _fake_save_memory(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)
    monkeypatch.setattr(memory_mod, "save_memory", _fake_save_memory)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "curated_knowledge", True)
    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_enabled", True)

    saved = asyncio.run(extract_single(7, 11, 1, "我喜欢喝美式咖啡，下周也带你去吃火锅", "好呀", source_id=300))

    # CURATED 落 world_facts（kind=preference_profile）
    from app.models.memory import WorldFact
    async def _get_wf():
        async with factory() as db:
            rows = (await db.execute(select(WorldFact).where(WorldFact.kind == "preference_profile"))).scalars().all()
            return [r.object_value for r in rows]
    wf_vals = asyncio.run(_get_wf())
    assert any("喝美式咖啡" in v for v in wf_vals)

    # INTENT 落 prospective_intents（promise 带时间窗）
    pis = _rows_of(factory)
    assert len(pis) >= 1 and any(r[1] == "下周带你去吃火锅" for r in pis)

    # PREFERENCES 互斥：那条已有 CURATED 收录 → 不再进衰减记忆（save_memory 未调用含该词的调用）
    assert not any("喝美式咖啡" in (kw.get("content") or "") for kw in calls)
    assert saved >= 1


def test_extractor_flag_off_zero(pi_db, monkeypatch):
    from app.memory.extractor import extract_single
    factory = pi_db

    async def _fake_llm(**kw):
        return (
            "USER_INFO: 无 | 1\nEVENTS: 无 | 1\nPREFERENCES: 无 | 1\nBIO: 无 | 1\n"
            "STATUS: 无 | 1\nRELATIONSHIP: 无 | 1\nSTAGE: 无 | 1\n"
            "CURATED: 用户喜欢喝美式咖啡 | preference_profile | 5\n"
            "INTENT: 下周带你去吃火锅 | promise | 2026-09-05~2026-09-12 | 无\n"
        )
    monkeypatch.setattr("app.memory.extractor.llm_call", _fake_llm)
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "curated_knowledge", False)
    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_enabled", False)

    asyncio.run(extract_single(7, 11, 1, "我喜欢喝美式咖啡", "好呀", source_id=301))

    from app.models.memory import WorldFact
    async def _count():
        async with factory() as db:
            wf = (await db.execute(select(WorldFact).where(WorldFact.kind != "status"))).scalars().all()
            pis = (await db.execute(select(ProspectiveIntent))).scalars().all()
            return len(wf), len(pis)
    wf_n, pis_n = asyncio.run(_count())
    assert wf_n == 0 and pis_n == 0  # flag 全关：零行为


def test_trigger_source_flag_gate(pi_db, monkeypatch):
    from app.scheduling.sources.prospective_intent import ProspectiveIntentSource
    from app.scheduling.sources.base import SourceContext
    from app.scheduling.prospective_intent import upsert_intent, _now_naive
    from app.agent.loop import AGENT_FLAGS
    asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                              kind="promise", due_end=_now_naive() - timedelta(minutes=1),
                              source_message_id=401, chat_session_id=7))
    src = ProspectiveIntentSource()

    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_trigger", False)
    out_off = asyncio.run(src.collect(SourceContext()))
    assert out_off == []

    monkeypatch.setitem(AGENT_FLAGS, "prospective_intent_trigger", True)
    out_on = asyncio.run(src.collect(SourceContext()))
    assert len(out_on) >= 1
    ti = out_on[0]
    assert ti.type == "prospective_intent" and ti.priority == 1
    assert ti.candidate["pis_id"] and ti.candidate["character_id"] == 11


def test_run_prospective_due_success_discharges(pi_db, monkeypatch):
    from app.scheduling.prospective_intent import upsert_intent, run_prospective_due, collect_due_promises, _now_naive
    factory = pi_db

    pis_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                                       kind="promise", due_end=_now_naive() - timedelta(minutes=1),
                                       source_message_id=501, chat_session_id=7))

    sent = {}
    async def _fake_llm(**kw):
        return "我记得你之前说过下周带你去吃火锅，要不这周末安排上？"
    async def _fake_send(session_id, character_id, user_id, content, message_type="prospective_intent", **kw):
        sent["content"] = content
        sent["type"] = message_type
        return None

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    candidate = {"pis_id": pis_id, "character_id": 11, "user_id": 1, "content": "下周带你去吃火锅",
                 "session_id": 7}
    ok = asyncio.run(run_prospective_due(candidate))
    assert ok is True
    assert sent.get("type") == "prospective_intent"
    # 成功发送 → discharged，不再被 collect 捞到
    due = asyncio.run(collect_due_promises())
    assert pis_id not in {c["pis_id"] for c in due}
    rows = {r[0]: r[3] for r in _rows_of(factory)}
    assert rows[pis_id] == "discharged"


def test_run_prospective_due_failure_keeps_pending(pi_db, monkeypatch):
    from app.scheduling.prospective_intent import upsert_intent, run_prospective_due, _now_naive
    factory = pi_db
    pis_id = asyncio.run(upsert_intent(user_id=1, character_id=11, content="下周带你去吃火锅",
                                       kind="promise", due_end=_now_naive() - timedelta(minutes=1),
                                       source_message_id=601, chat_session_id=7))

    async def _boom(**kw):
        raise RuntimeError("llm down")
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _boom)

    candidate = {"pis_id": pis_id, "character_id": 11, "user_id": 1, "content": "下周带你去吃火锅",
                 "session_id": 7}
    ok = asyncio.run(run_prospective_due(candidate))
    assert ok is False
    rows = {r[0]: r[3] for r in _rows_of(factory)}
    assert rows[pis_id] == "pending"  # 失败不 discharged，下轮重试
