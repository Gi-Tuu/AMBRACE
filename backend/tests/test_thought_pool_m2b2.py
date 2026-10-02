# -*- coding: utf-8 -*-
"""批 4 M2-b2（2026-10-01）：念头池抢话防线 6 条（确定性判据，不靠模型）。

派单：``output/AMBRACE_批4M2b2_抢话防线_派单_转发用_20261001.md``
设计稿：``output/AMBRACE_批4_念头池T2_详细设计_v1_20260929.md`` §3.2（六条防线表）

六条防线逐条钉（改任一即红）：
  1. 与 ``life_share`` 同源二选一：分享成功 ⇒ 只写一条 ``spent`` 留痕行；未成功 ⇒ 才 ``spark`` 入池；
  2. 与 ``unfinished_topic`` 双向排除：①入池时命中词表的进行中话题不入池；②供给时本 tick 已有
     unfinished_topic 候选 ⇒ 念头池让位不供给；
  3. 与 ``memory_review`` 不抢同一句：F4/F5 只引用话题/事实线索，**不把 Memory.id 绑进念头文本**；
  4. 与 ``state_trigger`` 类型白名单：只在 ``PROACTIVE_OUTREACH_TYPES`` 内供给，其余类型一律不供给；
  5. 与 ``life_regression`` 去重：F1 入池前比对近 24h 生活回灌候选集，已被回灌讲过 ⇒ 标 ``faded``；
  6. 不写不改 ``AICharacter.current_status``：念头只进上下文/谈资，不用谈资制造新事实。

另钉两条派单硬约束：**flag 关＝零调用零查询**（新增的防线 1/5 查询都在 ``shadow_enabled`` 早退之后）、
**同源只写一条 spent**（分享成功 ⇒ 池里恰好 1 行 spent，不多写 spark）。

口径与纪律：临时库一律 pytest ``tmp_path`` 私有 SQLite（``_dbclone`` 克隆模板库）；全程不碰
backend/data 生产库、不调模型、不走网络。flag 用 monkeypatch 临时置开，用例间互不污染。
"""
from __future__ import annotations

import asyncio
import inspect
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.application import thought_pool_service as svc
from app.domain.thought import dynamics as dyn
from app.domain.thought import extract as ex
from app.flags.agent_flags import AGENT_FLAGS

_CHAR, _USER = 13, 1
_SHADOW = svc.FLAG_KEY                          # thought_pool_shadow
_V1 = svc.V1_FLAG_KEY                           # thought_pool_v1


# ══════════════════════════════════════════════ 0. 公共桩 / 真库环境

class _NoSession:
    """假 session 工厂：一旦被调用即记账并报错——钉「flag 关＝一次 SQL 都不发」。"""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("flag 关时挂点不应建立 session / 发任何 SQL")


class _DummyCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture()
def pool_env(tmp_path):
    from _dbclone import clone_engine, make_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    engine = clone_engine(tmp_path / "b2.db")
    factory = make_session_factory(engine)

    async def _init():
        async with factory() as db:
            db.add(User(id=_USER, username="b2_u1", nickname="主人"))
            db.add(AICharacter(id=_CHAR, user_id=_USER, name="小暖", is_active=True))
            await db.commit()

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


def _pool_rows(factory) -> list[tuple]:
    from app.models.character import ThoughtPool

    async def _run():
        async with factory() as db:
            got = (await db.execute(select(ThoughtPool).order_by(ThoughtPool.id))).scalars().all()
            return [(r.source_type, r.source_ref, r.status, r.character_id) for r in got]
    return asyncio.run(_run())


def _patch_session(monkeypatch, module_path: str, factory):
    import importlib
    mod = importlib.import_module(module_path)
    monkeypatch.setattr(mod, "async_session_factory", factory)


def _activity_payload(*, ref=101, mem_id=201, act="create", summary="今天把阳台的茉莉换了盆"):
    return {"data": {"character_id": _CHAR, "user_id": _USER, "activity_type": act,
                     "artifact_id": ref, "memory_id": mem_id, "summary": summary}}


# ══════════════════════════════════════════════ 防线 1：与 life_share 同源二选一

def _seed_life_share_approved(factory, *, created=None):
    from app.models.character import ProactiveTriggerLog
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(ProactiveTriggerLog(
                character_id=_CHAR, user_id=_USER, trigger_type="life_share",
                trigger_reason="create:今天把阳台的茉莉换了盆", priority=5, decision="approved",
                created_at=created or now_naive_utc(),
            ))
            await db.commit()
    asyncio.run(_run())


def test_life_share_succeeded_reads_approved_log(pool_env):
    """判据：近窗内有 approved 的 life_share 触发留痕 ⇒ 判定本次分享成功。"""
    from app.utils.timeutil import now_naive_utc
    _seed_life_share_approved(pool_env)

    async def _run():
        async with pool_env() as db:
            return await svc.life_share_succeeded(db, _CHAR, now=now_naive_utc())
    assert asyncio.run(_run()) is True


def test_life_share_succeeded_false_when_no_approved(pool_env):
    """无 approved 留痕（含只有 rejected）⇒ 判定未成功。"""
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with pool_env() as db:
            return await svc.life_share_succeeded(db, _CHAR, now=now_naive_utc())
    assert asyncio.run(_run()) is False


def test_defense1_shared_activity_writes_single_spent(pool_env, monkeypatch):
    """★ 防线 1 + 派单自证②：分享成功 ⇒ 池里**恰好一条 spent** 留痕行，不再写 spark。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _SHADOW, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)
    _seed_life_share_approved(pool_env)

    asyncio.run(handlers._on_thought_pool_activity(_activity_payload()))
    rows = _pool_rows(pool_env)
    assert len(rows) == 1, f"同源应只写一条留痕行，实际 {rows}"
    assert rows[0][0] == ex.SRC_ACTIVITY and rows[0][2] == dyn.STATUS_SPENT
    # spent 行永不参与选择（取一条只取 ACTIVE_STATUSES）
    async def _fetch():
        async with pool_env() as db:
            return await svc.fetch_one_thought(db, _CHAR, _USER)
    monkeypatch.setitem(AGENT_FLAGS, _V1, True)
    assert asyncio.run(_fetch()) is None, "spent 留痕行不得被取用"


def test_defense1_unshared_activity_writes_spark(pool_env, monkeypatch):
    """分享未成功 ⇒ 才以 spark 入池（对照面）。"""
    from app.events import handlers
    monkeypatch.setitem(AGENT_FLAGS, _SHADOW, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)
    # 不种 approved 留痕 ⇒ life_share 未成功
    asyncio.run(handlers._on_thought_pool_activity(_activity_payload(mem_id=999)))
    rows = _pool_rows(pool_env)
    assert len(rows) == 1 and rows[0][2] == dyn.STATUS_SPARK


# ══════════════════════════════════════════════ 防线 5：与 life_regression 去重

def test_defense5_regressed_activity_marked_faded(pool_env, monkeypatch):
    """F1 入池前比对近 24h 生活回灌候选集：该活动记忆已在候选集 ⇒ spark 直接标 faded（不删行）。"""
    from app.events import handlers
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc
    monkeypatch.setitem(AGENT_FLAGS, _SHADOW, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)

    async def _seed():
        async with pool_env() as db:
            # 近 24h、source=life、importance 达标 ⇒ 落在 life_regression 候选集（既有读法）
            db.add(Memory(id=201, character_id=_CHAR, user_id=_USER, memory_type="event",
                          content="把阳台的茉莉换了盆", source="life", importance=3,
                          created_at=now_naive_utc() - timedelta(hours=2)))
            await db.commit()
    asyncio.run(_seed())

    # 未被 life_share 分享（无 approved 留痕），但已被回灌通道覆盖 ⇒ faded
    asyncio.run(handlers._on_thought_pool_activity(_activity_payload(mem_id=201)))
    rows = _pool_rows(pool_env)
    assert len(rows) == 1 and rows[0][2] == dyn.STATUS_FADED, f"应标 faded，实际 {rows}"


def test_defense5_non_regressed_activity_stays_spark(pool_env, monkeypatch):
    """活动记忆不在回灌候选集（importance 不达标）⇒ 正常 spark（对照面）。"""
    from app.events import handlers
    from app.models.memory import Memory
    from app.utils.timeutil import now_naive_utc
    monkeypatch.setitem(AGENT_FLAGS, _SHADOW, True)
    _patch_session(monkeypatch, "app.events.handlers", pool_env)

    async def _seed():
        async with pool_env() as db:
            db.add(Memory(id=202, character_id=_CHAR, user_id=_USER, memory_type="event",
                          content="随手浇了浇花", source="life", importance=1,   # < MIN_IMPORTANCE(3)
                          created_at=now_naive_utc() - timedelta(hours=2)))
            await db.commit()
    asyncio.run(_seed())

    asyncio.run(handlers._on_thought_pool_activity(_activity_payload(ref=102, mem_id=202,
                                                                     summary="随手浇了浇花")))
    rows = _pool_rows(pool_env)
    assert len(rows) == 1 and rows[0][2] == dyn.STATUS_SPARK


# ══════════════════════════════════════════════ 防线 2①：入池侧 unfinished_topic 词表排除

@pytest.mark.parametrize("topic,expect_in", [
    ("他上次说想学摄影", True),          # 未命中词表 ⇒ 入池
    ("下次再聊摄影的事", False),          # 命中「下次」⇒ 不入池（已被 unfinished_topic 占用）
    ("有空一起看展", False),              # 命中「有空」⇒ 不入池
    ("他最近在准备考试", True),
])
def test_defense2a_intake_excludes_unfinished_keyword_topic(topic, expect_in):
    """入池侧：进行中话题若被 unfinished_topic 词表命中 ⇒ extract_user_hook 不出候选（双向排除①）。"""
    row = {"id": 1, "topic": topic, "status": ex.F4_ACTIVE_STATUS, "idle_days": 5.0,
           "character_id": _CHAR, "user_id": _USER}
    drafts = ex.extract_user_hook(row)
    assert bool(drafts) is expect_in


def test_defense2a_wordlist_matches_production():
    """词表与生产 ``unfinished_topic.UNFINISHED_KEYWORDS`` 同源（防两处漂移）。"""
    from app.scheduling.unfinished_topic import UNFINISHED_KEYWORDS
    assert ex.UNFINISHED_TOPIC_KEYWORDS == UNFINISHED_KEYWORDS


# ══════════════════════════════════════════════ 防线 2② + 4：供给侧让位 + 类型白名单

class _StubPlan:
    intent = "check_in"
    tier = "recent"
    allow_active_topics = False
    allow_storyline = False
    allow_recall = False
    memory_query = ""
    must_return_question = True


def _stub_annotate(monkeypatch, *, fetch_spy):
    """把 _annotate_outreach_plan 的重活桩掉，只留念头供给判定可测。"""
    from app.domain.proactivity import outreach as _oc
    from app.scheduling import arbiter
    from app.scheduling import outreach_gates as og

    async def _fake_mats(cand):
        return _oc.OutreachMaterials(False, False, False, False)

    async def _fake_recent(cid, limit=2):
        return []

    async def _fake_shadow(*a, **k):
        return None

    monkeypatch.setattr(arbiter, "_collect_outreach_materials", _fake_mats)
    monkeypatch.setattr(arbiter, "_get_recent_outreach_intents", _fake_recent)
    monkeypatch.setattr(arbiter, "_shadow_drive_note", _fake_shadow)
    monkeypatch.setattr(arbiter._oc, "staleness_tier", lambda x: "recent")
    monkeypatch.setattr(arbiter._oc, "select_outreach", lambda *a, **k: _StubPlan())
    monkeypatch.setattr(arbiter, "async_session_factory", lambda: _DummyCtx())
    # A20 批 2 R4：本用例测的 _annotate_outreach_plan 已搬到 outreach_gates，函数体的这四个裸名
    # 全在 outreach_gates 命名空间解析 ⇒ 只打 arbiter 等于没打（念头供给会真查库）。两侧同打。
    monkeypatch.setattr(og, "_collect_outreach_materials", _fake_mats)
    monkeypatch.setattr(og, "_get_recent_outreach_intents", _fake_recent)
    monkeypatch.setattr(og, "_shadow_drive_note", _fake_shadow)
    monkeypatch.setattr(og, "async_session_factory", lambda: _DummyCtx())
    monkeypatch.setattr(svc, "thought_pool_v1_allowed", lambda cid, sid=None: True)

    async def _fake_fetch(db, cid, uid, *, intent=None):
        fetch_spy.append(cid)
        return {"id": 7, "text": "换了盆的茉莉"}
    monkeypatch.setattr(svc, "fetch_one_thought", _fake_fetch)
    return arbiter


def test_defense2b_supply_yields_to_unfinished(monkeypatch):
    """供给侧让位：本 tick 已有 unfinished_topic 候选 ⇒ 念头池不供给（连 fetch 都不调）。"""
    spy: list = []
    arbiter = _stub_annotate(monkeypatch, fetch_spy=spy)
    item = {"type": "greeting", "candidate": {"user_id": _USER, "idle_minutes": 100}}
    asyncio.run(arbiter._annotate_outreach_plan(item, _CHAR, {}, {}, char_has_unfinished=True))
    assert "thought" not in item["candidate"], "让位时不得供给念头"
    assert spy == [], "让位时连 fetch_one_thought 都不应调用"


def test_defense2b_supply_provides_when_no_unfinished(monkeypatch):
    """对照面：无 unfinished_topic 候选 ⇒ 正常供给念头素材。"""
    spy: list = []
    arbiter = _stub_annotate(monkeypatch, fetch_spy=spy)
    item = {"type": "greeting", "candidate": {"user_id": _USER, "idle_minutes": 100}}
    asyncio.run(arbiter._annotate_outreach_plan(item, _CHAR, {}, {}, char_has_unfinished=False))
    assert item["candidate"].get("thought") == "换了盆的茉莉"
    assert item["candidate"].get("thought_id") == 7
    assert spy == [_CHAR]


@pytest.mark.parametrize("etype", ["state_trigger", "memory_review", "timer", "life_regression",
                                   "unfinished_topic", "plugin", "prospective_intent"])
def test_defense4_non_outreach_type_not_supplied(monkeypatch, etype):
    """防线 4：非 PROACTIVE_OUTREACH_TYPES 的类型一律不供给念头（纵深防御，早退不查库）。"""
    spy: list = []
    arbiter = _stub_annotate(monkeypatch, fetch_spy=spy)
    item = {"type": etype, "candidate": {"user_id": _USER, "idle_minutes": 100}}
    asyncio.run(arbiter._annotate_outreach_plan(item, _CHAR, {}, {}, char_has_unfinished=False))
    assert "thought" not in item["candidate"], f"{etype} 不应被供给念头"
    assert spy == [], f"{etype} 不应触发 fetch_one_thought"


@pytest.mark.parametrize("etype", list(("greeting", "proactive_chat", "goodnight",
                                        "status_update", "motivation")))
def test_defense4_outreach_types_supplied(monkeypatch, etype):
    """防线 4 对照面：outreach 管辖的五类正常供给。"""
    spy: list = []
    arbiter = _stub_annotate(monkeypatch, fetch_spy=spy)
    item = {"type": etype, "candidate": {"user_id": _USER, "idle_minutes": 100}}
    asyncio.run(arbiter._annotate_outreach_plan(item, _CHAR, {}, {}, char_has_unfinished=False))
    assert item["candidate"].get("thought") == "换了盆的茉莉", f"{etype} 应被供给念头"


def test_defense4_whitelist_equals_arbiter_constant():
    """类型白名单与 ``arbiter.PROACTIVE_OUTREACH_TYPES`` 同源（防两处漂移）。"""
    from app.scheduling.arbiter import PROACTIVE_OUTREACH_TYPES
    assert set(PROACTIVE_OUTREACH_TYPES) == {"greeting", "proactive_chat", "goodnight",
                                             "status_update", "motivation"}


# ══════════════════════════════════════════════ 防线 3：不绑 Memory.id / 不产 memory_review 文案

def test_defense3_fact_text_binds_no_memory_id():
    """F5 念头文本只含事实线索，**不含 Memory.id**、不含「你上次提到」类记忆引用措辞。"""
    row = {"id": 424242, "value": "他最近换了工作", "epistemic_status": "INFERRED",
           "character_id": _CHAR, "user_id": _USER}
    drafts = ex.extract_fact(row)
    assert len(drafts) == 1
    text = drafts[0]["text"]
    assert "424242" not in text, "Memory.id 不得绑进念头文本"
    assert "你上次提到" not in text and "记得你" not in text
    assert "他最近换了工作" in text          # 只引用事实线索本身


def test_defense3_user_hook_text_binds_no_memory_id():
    """F4 念头文本＝话题本身，不含任何 Memory.id。"""
    row = {"id": 999888, "topic": "他上次说想学摄影", "status": ex.F4_ACTIVE_STATUS,
           "idle_days": 5.0, "character_id": _CHAR, "user_id": _USER}
    drafts = ex.extract_user_hook(row)
    assert len(drafts) == 1
    assert "999888" not in drafts[0]["text"]


def test_defense3_injection_text_has_no_memory_review_phrasing():
    """注入文本不产 memory_review 类「复习/你上次提到」措辞（不与复习通道抢同一句）。"""
    text = svc.build_injection_text({"text": "他最近换了工作"})
    for banned in ("你上次提到", "记得你说过", "复习", "memory_review", "条念头", "念头池"):
        assert banned not in text


# ══════════════════════════════════════════════ 防线 6：不写不改 current_status

def test_defense6_thought_subsystem_never_writes_current_status():
    """念头池子系统（服务层 + 三挂点函数）源码本体零 ``current_status`` 赋值/引用。"""
    from app.events import handlers
    from app.application import chat_service, character_state_service as css

    # 服务层整文件：唯一写口只写 thought_pool，绝不碰 current_status
    svc_src = Path(svc.__file__).read_text(encoding="utf-8")
    assert "current_status" not in svc_src, "thought_pool_service 不得引用 current_status"

    # 三挂点函数（含 F1 去重判据）逐函数扫
    hook_fns = [
        handlers._on_thought_pool_activity, handlers._in_life_regression_window,
        chat_service._settle_thought_pool_turn, css._supply_thought_pool_periodic,
    ]
    for fn in hook_fns:
        src = inspect.getsource(fn)
        assert "current_status" not in src, f"{fn.__name__} 不得写/改 current_status"
        assert not re.search(r"current_status\s*=", src), f"{fn.__name__} 不得赋值 current_status"


# ══════════════════════════════════════════════ 派单自证①：flag 关＝零调用零查询（含新增防线查询）

def test_flag_off_event_hook_zero_sql_with_new_defenses(monkeypatch):
    """flag 关 ⇒ F1 挂点首行返回：防线 1/5 的新增查询（life_share 留痕 / 回灌候选集）一次都不发。"""
    from app.events import handlers
    assert AGENT_FLAGS[_SHADOW] is False
    no_sess = _NoSession()
    _patch_session(monkeypatch, "app.events.handlers", no_sess)
    asyncio.run(handlers._on_thought_pool_activity(_activity_payload()))
    assert no_sess.calls == 0, "flag 关时 F1 挂点（含防线 1/5 查询）不应建 session"


def test_flag_off_life_share_succeeded_not_reached(monkeypatch):
    """flag 关 ⇒ life_share_succeeded 根本不会被调到（早退在其之前）。"""
    from app.events import handlers
    called = {"n": 0}

    async def _spy(*a, **k):
        called["n"] += 1
        return False
    monkeypatch.setattr(svc, "life_share_succeeded", _spy)
    _patch_session(monkeypatch, "app.events.handlers", _NoSession())
    asyncio.run(handlers._on_thought_pool_activity(_activity_payload()))
    assert called["n"] == 0
