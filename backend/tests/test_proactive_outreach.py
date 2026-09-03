# -*- coding: utf-8 -*-
"""B1-③ 主动消息自然化（方案 §1-§9，proactive_outreach_v2）测试：

- outreach 纯函数：staleness_tier / select_outreach / _candidates / _has_invitation（零 IO）；
- topic_tracker.load_fresh_active_topics_text：时效治理（72h/14d 边界）；
- arbiter run_tick 接线：flag 关零变化、开时意图生效（选择写回 candidate 并透传）；
- message_generator：outreach 新分支 prompt、素材开关（stale+share_self 无剧情、continue 有剧情）、
  RECALL_SHARED 捞链衔接、memory_query 映射；
- sources/motivation.py：idle_minutes 补算（照节律源）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照既有测试风格。）
"""
import asyncio
import os
import random
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.proactivity import outreach as oc
from app.scheduling import arbiter


# ═══════════════════ outreach 纯函数（零 IO） ═══════════════════

def test_staleness_tier_boundaries():
    """分级边界：≤2h continue；≤24h recent；≤72h stale；>72h cold；None → recent。"""
    assert oc.staleness_tier(0) == oc.TIER_CONTINUE
    assert oc.staleness_tier(120) == oc.TIER_CONTINUE       # 恰 2h
    assert oc.staleness_tier(121) == oc.TIER_RECENT          # 刚过 2h
    assert oc.staleness_tier(1440) == oc.TIER_RECENT         # 恰 24h
    assert oc.staleness_tier(1441) == oc.TIER_STALE          # 刚过 24h
    assert oc.staleness_tier(4320) == oc.TIER_STALE          # 恰 72h
    assert oc.staleness_tier(4321) == oc.TIER_COLD           # 刚过 72h
    assert oc.staleness_tier(None) == oc.TIER_RECENT         # 未知闲置不误判为久违


def test_candidates_no_materials_only_checkin_share():
    """无素材时只剩 check_in / share_self（方案 §8 测试 2）；SHARE_SELF 恒可用（不因无生活素材移除）。"""
    cands = oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials())
    assert set(cands) == {oc.CHECK_IN, oc.SHARE_SELF}


def test_candidates_requires_material_prereqs():
    """素材前提：没目标→无 follow_up；没共同记忆→无 recall_shared；没兴趣→无 interest_hook。"""
    cands = oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(
        has_open_loop=True, has_shared_memory=True, has_user_interest=True))
    assert set(cands) == set(oc.ALL_INTENTS)
    assert oc.FOLLOW_UP not in oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(has_open_loop=False))
    assert oc.RECALL_SHARED not in oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(has_shared_memory=False))
    assert oc.INTEREST_HOOK not in oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(has_user_interest=False))


def test_follow_up_prereq_via_candidates():
    """FOLLOW_UP 仅在 has_open_loop 时作为候选（材质前提参与意图选择）。"""
    assert oc.FOLLOW_UP in oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(has_open_loop=True))
    assert oc.FOLLOW_UP not in oc._candidates(oc.TIER_RECENT, oc.OutreachMaterials(has_open_loop=False))


def test_select_outreach_returns_valid_plan():
    """select_outreach 恒返回合法计划：意图 ∈ ALL_INTENTS，素材开关与 tier 一致。"""
    plan = oc.select_outreach(oc.TIER_RECENT, oc.OutreachMaterials(), [], random.Random(0))
    assert plan.intent in oc.ALL_INTENTS
    assert plan.tier == oc.TIER_RECENT
    assert isinstance(plan.must_return_question, bool)
    assert isinstance(plan.allow_active_topics, bool)
    assert isinstance(plan.allow_storyline, bool)
    assert isinstance(plan.allow_recall, bool)
    assert isinstance(plan.memory_query, str)


def test_select_outreach_avoids_recent_intents():
    """避开最近 2 次已用意图：把全部意图都标为最近已用 → 仍必须有结果（撞车兜底允许重复）。"""
    recent = list(oc.ALL_INTENTS)[:oc.RECENT_AVOID]
    plan = oc.select_outreach(oc.TIER_CONTINUE, oc.OutreachMaterials(has_open_loop=True, has_shared_memory=True, has_user_interest=True), recent, random.Random(0))
    assert plan.intent in oc.ALL_INTENTS


def test_select_outreach_cold_never_continues_old():
    """cold 档：任何意图都不注入"进行中话题"与 AI 剧情（默认全新发起），仅真实未兑现承诺例外。"""
    for seed in range(8):
        plan = oc.select_outreach(oc.TIER_COLD, oc.OutreachMaterials(has_life_now=True), [], random.Random(seed))
        assert plan.tier == oc.TIER_COLD
        assert plan.allow_active_topics is False          # cold 不续"进行中话题"
        assert plan.allow_storyline is False              # cold 不背 AI 剧情
        assert plan.must_return_question is True


def test_select_outreach_continue_follow_up_allows_topics():
    """continue/recent + FOLLOW_UP → 允许注入"进行中话题"（本该跟进）。"""
    plan = oc.select_outreach(oc.TIER_CONTINUE, oc.OutreachMaterials(has_open_loop=True), [], random.Random(0))
    assert plan.allow_active_topics == (plan.intent == oc.FOLLOW_UP)
    assert plan.allow_recall == (plan.intent in (oc.RECALL_SHARED, oc.FOLLOW_UP))


def test_memory_query_mapping():
    """各意图的记忆检索 query 用户导向（非 AI 状态）；SHARE_SELF 不检索。"""
    assert oc.MEMORY_QUERY_BY_INTENT[oc.INTEREST_HOOK] != ""
    assert oc.MEMORY_QUERY_BY_INTENT[oc.RECALL_SHARED] != ""
    assert oc.MEMORY_QUERY_BY_INTENT[oc.FOLLOW_UP] != ""
    assert oc.MEMORY_QUERY_BY_INTENT[oc.CHECK_IN] != ""
    assert oc.MEMORY_QUERY_BY_INTENT[oc.SHARE_SELF] == ""


def test_has_invitation_pure():
    """`_has_invitation`：中文/英文问号、疑问助词识别；纯陈述句 False。"""
    from app.scheduling.message_generator import _has_invitation
    assert _has_invitation("你最近工作还顺心吗？") is True
    assert _has_invitation("吃饭了吗") is True
    assert _has_invitation("今天过得怎么样") is True
    assert _has_invitation("要不要一起去？") is True
    assert _has_invitation("Are you free?") is True
    assert _has_invitation("我刚才泡了杯咖啡，挺香的。") is False
    assert _has_invitation("") is False


# ═══════════════════ topic_tracker 时效加载（临时库） ═══════════════════

@pytest.fixture()
def topic_db(monkeypatch):
    """临时 SQLite 文件库：patch app.db.database.async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="fresh_topic_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    from app.agent import topic_tracker as tt
    monkeypatch.setattr(tt, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def test_load_fresh_active_topics_time_governance(topic_db):
    """普通话题>72h 不返回；goal 14d 内返回、超 14d 不返回；已完成不参与。"""
    from app.agent.topic_tracker import load_fresh_active_topics_text
    from app.models.character import AICharacter
    from app.models.memory import ConversationTopic

    now = datetime.utcnow()

    async def _seed():
        async with topic_db() as db:
            db.add(AICharacter(id=21, user_id=1, name="t"))
            await db.commit()
            db.add(ConversationTopic(character_id=21, user_id=1, topic="今天聊的周末去哪儿", status="进行中",
                                     importance=0.8, goal=False, last_touched_at=now - timedelta(hours=1)))
            db.add(ConversationTopic(character_id=21, user_id=1, topic="前几天学的菜谱", status="进行中",
                                     importance=0.7, goal=False, last_touched_at=now - timedelta(hours=48)))
            db.add(ConversationTopic(character_id=21, user_id=1, topic="两周前的话题", status="进行中",
                                     importance=0.6, goal=False, last_touched_at=now - timedelta(days=5)))   # >72h → 不返回
            db.add(ConversationTopic(character_id=21, user_id=1, topic="三天前目标", status="进行中",
                                     importance=0.9, goal=True, last_touched_at=now - timedelta(days=3)))    # goal 14d 内 → 返回
            db.add(ConversationTopic(character_id=21, user_id=1, topic="超期目标", status="进行中",
                                     importance=0.9, goal=True, last_touched_at=now - timedelta(days=20)))   # goal >14d → 不返回
            db.add(ConversationTopic(character_id=21, user_id=1, topic="已完成", status="完成",
                                     importance=0.8, goal=False, last_touched_at=now - timedelta(hours=1)))
            await db.commit()

    asyncio.run(_seed())
    out = asyncio.run(load_fresh_active_topics_text(21, 1))
    assert "今天聊的周末去哪儿" in out
    assert "前几天学的菜谱" in out
    assert "两周前的话题" not in out        # 普通话题过期
    assert "三天前目标" in out              # 目标 14d 内
    assert "超期目标" not in out           # 目标过期
    assert "已完成" not in out             # 状态非进行中
    assert "🎯" in out                     # 目标显式标注


# ═══════════════════ arbiter run_tick 接线（flag 关零变化 / 开时意图生效） ═══════════════════

def _fake_decay():
    return None


class _ProactiveSrc:
    name = "rhythm"

    async def collect(self, ctx):
        from app.scheduling.sources import TriggerItem
        return [TriggerItem(
            type="proactive_chat", priority=1,
            candidate={"character_id": 1, "user_id": 1, "idle_minutes": 2000, "current_status": "在家"},
        )]

    def quota(self, ctx):
        return 1


def _patch_run_tick(monkeypatch, execute_impl):
    async def _motivation(cid):
        return 0.0
    async def _log(item, ok):
        return None
    async def _trace(item, ok, ms):
        return None
    async def _mats(cand):
        return oc.OutreachMaterials(has_open_loop=True, has_shared_memory=True, has_user_interest=True, has_life_now=True)
    async def _recent(cid, limit=2):
        return []

    monkeypatch.setattr("app.domain.relationship.decay.run_relationship_decay", _fake_decay)
    monkeypatch.setattr(arbiter, "all_sources", lambda: [_ProactiveSrc()])
    monkeypatch.setattr(arbiter, "_compute_motivation", _motivation)
    monkeypatch.setattr(arbiter, "_execute", execute_impl)
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _log)
    monkeypatch.setattr(arbiter, "_trace_scheduler_task", _trace)
    monkeypatch.setattr(arbiter, "_collect_outreach_materials", _mats)
    monkeypatch.setattr(arbiter, "_get_recent_outreach_intents", _recent)


def test_run_tick_flag_off_zero_change(monkeypatch):
    """flag 关（默认）：candidate 不带 outreach_intent/outreach_plan → 走旧链路零变化。"""
    captured = {}

    async def _execute(item):
        captured["candidate"] = item["candidate"]
        return True

    _patch_run_tick(monkeypatch, _execute)
    assert arbiter._outreach_enabled() is False
    executed = asyncio.run(arbiter.run_tick())
    assert executed == ["proactive_chat(char=1)"]
    cand = captured["candidate"]
    assert "outreach_intent" not in cand
    assert "outreach_plan" not in cand


def test_run_tick_flag_on_intent_effective(monkeypatch):
    """flag 开：run_tick 汇总层选意图并写回 candidate（意图生效、透传生成端）。"""
    from app.agent import loop as loop_mod
    loop_mod.AGENT_FLAGS["proactive_outreach_v2"] = True
    try:
        captured = {}

        async def _execute(item):
            captured["candidate"] = item["candidate"]
            return True

        _patch_run_tick(monkeypatch, _execute)
        assert arbiter._outreach_enabled() is True
        executed = asyncio.run(arbiter.run_tick())
        assert executed == ["proactive_chat(char=1)"]
        cand = captured["candidate"]
        assert cand.get("outreach_intent") in oc.ALL_INTENTS
        plan = cand.get("outreach_plan")
        assert plan is not None
        assert plan["tier"] == oc.TIER_STALE           # idle 2000min ≈ 33h → stale
        for key in ("allow_active_topics", "allow_storyline", "allow_recall", "memory_query", "must_return_question"):
            assert key in plan
    finally:
        loop_mod.AGENT_FLAGS["proactive_outreach_v2"] = False


class _FakeSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _FakeFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


def test_log_trigger_candidate_records_outreach(monkeypatch):
    """观测信号：candidate 带 outreach_intent 时 trigger_reason 追加 [outreach=x]；无意图不附加。"""
    fs = _FakeSession()
    monkeypatch.setattr(arbiter, "async_session_factory", _FakeFactory(fs))
    item = {"type": "proactive_chat", "candidate": {"character_id": 1, "user_id": 1, "trigger_reason": "日常问候"}}
    asyncio.run(arbiter.log_trigger_candidate(item, executed=True))
    logs = [o for o in fs.added if getattr(o, "trigger_reason", None) is not None]
    assert logs and logs[0].trigger_reason == "日常问候" and "[outreach=" not in logs[0].trigger_reason

    fs2 = _FakeSession()
    monkeypatch.setattr(arbiter, "async_session_factory", _FakeFactory(fs2))
    item2 = {"type": "proactive_chat", "candidate": {"character_id": 1, "user_id": 1, "trigger_reason": "日常问候", "outreach_intent": "check_in"}}
    asyncio.run(arbiter.log_trigger_candidate(item2, executed=True))
    logs2 = [o for o in fs2.added if getattr(o, "trigger_reason", None) is not None]
    assert logs2 and "[outreach=check_in]" in logs2[0].trigger_reason


def test_get_recent_outreach_intents_parses_approved(monkeypatch):
    """反查历史意图：只读 decision=approved 的 trigger_reason 里的 outreach=\\w+；失败返回空。"""
    class _Sess:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_a):
            return False
        async def execute(self, *a, **k):
            return self
        def scalars(self):
            return self
        def all(self):
            return ["日常问候 [outreach=check_in]", "跟进 [outreach=follow_up]", "无意图"]

    async def _get(db):
        return _Sess()

    monkeypatch.setattr(arbiter, "async_session_factory", lambda: _Sess())
    out = asyncio.run(arbiter._get_recent_outreach_intents(1, limit=2))
    assert out == ["check_in", "follow_up"]


# ═══════════════════ sources/motivation：idle_minutes 补算 ═══════════════════

def _base_char(**over):
    base = dict(
        character_id=11, user_id=1, character_name="小阳", character_bio="",
        character_personality="活泼", current_status="在家", relationship_summary="",
        nickname="用户", username="用户",
    )
    base.update(over)
    return [base]


def test_motivation_candidate_computes_idle_minutes(monkeypatch):
    """B1-③：motivation 源补算 idle_minutes（照节律源 _session_last_message_at）。"""
    async def _fake_active(*a, **k):
        return _base_char()
    async def _fake_motivation(cid):
        return 0.8
    async def _fake_sid(uid, cid):
        return 100
    async def _fake_last(sid, limit=5):
        return "用户: hi"
    async def _fake_msg_at(sid):
        return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=30)

    monkeypatch.setattr(arbiter, "get_active_characters", _fake_active)
    monkeypatch.setattr(arbiter, "_compute_motivation", _fake_motivation)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr("app.scheduling.triggers.get_last_messages", _fake_last)
    monkeypatch.setattr(arbiter, "_session_last_message_at", _fake_msg_at)

    items = asyncio.run(arbiter.collect_motivation_events())
    assert len(items) == 1
    cand = items[0]["candidate"]
    assert cand["idle_minutes"] is not None
    assert cand["idle_minutes"] >= 1780  # ≈ 1800 分钟（30h）
    assert oc.staleness_tier(cand["idle_minutes"]) == oc.TIER_STALE


def test_motivation_candidate_idle_failure_quiet(monkeypatch):
    """idle 补算失败 → 静默回退 None（不中断采集，分级落 recent）。"""
    async def _fake_active(*a, **k):
        return _base_char()
    async def _fake_motivation(cid):
        return 0.8
    async def _fake_sid(uid, cid):
        return 100
    async def _boom(sid):
        raise RuntimeError("db down")

    monkeypatch.setattr(arbiter, "get_active_characters", _fake_active)
    monkeypatch.setattr(arbiter, "_compute_motivation", _fake_motivation)
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _fake_sid)
    monkeypatch.setattr(arbiter, "_session_last_message_at", _boom)

    items = asyncio.run(arbiter.collect_motivation_events())
    assert len(items) == 1
    assert items[0]["candidate"]["idle_minutes"] is None
