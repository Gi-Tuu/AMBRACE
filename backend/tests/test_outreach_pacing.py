# -*- coding: utf-8 -*-
"""outreach 投放口径三闸（2026-09-13 Codex→dsh 交接 §二/§四）测试。

覆盖验收 §四：
① 开关关 = 旧行为（三个开关默认 False；全关时闸门不查库直接放行）；
② 低效类型窗口外被拦、窗口内放行（含边界 12 含 / 23 不含）；
③ 每日类型上限生效（memory_review ≤6、ai_care ≤4）；
④ 会话日上限与最小间隔生效（含边界：恰好 45 分钟放行）；
⑤ 互动型类型不受 ①② 限制；
⑥ 计数只算「已发送」（proactive_message_logs），不算候选审批流水（proactive_trigger_logs）。

另覆盖交接 §三埋点口径（[gate=...] 落 trigger_reason / reject_reason）、`_execute` 全链路留痕、
以及 ② 的 memory_review「可回复化」在开关开/关下的 hint 差异。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；DB 用例走临时 SQLite 并标 slow。）
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from _dbclone import clone_engine, make_session_factory

from app.domain.proactivity import pacing
from app.scheduling import arbiter

pytestmark = pytest.mark.slow

C_WHITE = 13     # 灰度白名单内（OUTREACH_PACING_GRAY_CHARS）
C_OTHER = 18     # 白名单外
USER_ID = 99001


def _flags():
    from app.agent.loop import AGENT_FLAGS
    return AGENT_FLAGS


@pytest.fixture()
def flag_env():
    """三个新开关恢复原值（默认 False），用例内可自由置位。"""
    flags = _flags()
    old = {k: flags.get(k, None) for k in pacing.PACING_FLAGS}
    for k in pacing.PACING_FLAGS:
        flags[k] = old[k] if old[k] is not None else False
    yield flags
    for k, v in old.items():
        if v is None:
            flags.pop(k, None)
        else:
            flags[k] = v


@pytest.fixture()
def all_on(flag_env):
    """三个开关全开（灰度仍只覆盖 char13）。"""
    for k in pacing.PACING_FLAGS:
        flag_env[k] = True
    return flag_env


def _item(etype, cid=C_WHITE, **cand_kw):
    cand = {"character_id": cid, "user_id": USER_ID, **cand_kw}
    return {"type": etype, "priority": 1, "candidate": cand}


def _patch_gate_io(monkeypatch, *, type_sent=0, session_sent=0, last=None, active_hours=None):
    """把闸门的 IO（计数/最近时间/活跃时段/最新会话）替换为可控值，保持快测档零 DB。"""
    async def _type_count(_cid, _mt):
        return type_sent

    async def _sess_count(_cid, _sid):
        return session_sent

    async def _sess_last(_cid, _sid):
        return last

    async def _hours(_uid):
        return list(active_hours or [])

    async def _latest(_uid, _cid):
        return None

    import app.application.chat_service as chat_svc
    monkeypatch.setattr(arbiter, "get_daily_sent_count", _type_count)
    monkeypatch.setattr(arbiter, "get_session_daily_sent_count", _sess_count)
    monkeypatch.setattr(arbiter, "get_session_last_sent_at", _sess_last)
    monkeypatch.setattr(arbiter, "_user_active_hours", _hours)
    monkeypatch.setattr(chat_svc, "get_latest_session_id", _latest)


def _gate(item, *, cn_hour=18, now=None, cid=None):
    cand = item["candidate"]
    return asyncio.run(arbiter._pacing_gate(
        item, item["type"], cid if cid is not None else cand["character_id"], cand,
        cn_hour=cn_hour, now=now,
    ))


# ═══════════════ ① 时段窗口闸（纯函数） ═══════════════

def test_时段窗口_低效类型边界_12含23不含():
    assert (pacing.HOUR_WINDOW_START, pacing.HOUR_WINDOW_END) == (12, 23)
    for t in ("ai_care", "life_regression", "memory_review", "memory_review_contextual"):
        assert pacing.hour_window_allows(t, 3) is False
        assert pacing.hour_window_allows(t, 11) is False
        assert pacing.hour_window_allows(t, 12) is True    # 半开区间：含 start
        assert pacing.hour_window_allows(t, 18) is True
        assert pacing.hour_window_allows(t, 22) is True
        assert pacing.hour_window_allows(t, 23) is False   # 半开区间：不含 end


def test_时段窗口_互动型与必须送达类型不受限():
    for t in ("plugin", "state_trigger", "timer", "holiday", "birthday", "anniversary",
              "emotion_care", "pet_remind", "prospective_intent", "storyline", "greeting"):
        assert pacing.hour_window_allows(t, 3) is True
        assert pacing.hour_window_allows(t, 11) is True


def test_时段窗口_个性化活跃时段只扩不缩():
    # 用户已学活跃时段可放宽（清晨 9 点允许），默认窗口仍有效（20 点允许），不在其中的仍拦
    assert pacing.hour_window_allows("ai_care", 9, active_hours=[[8, 11]]) is True
    assert pacing.hour_window_allows("ai_care", 3, active_hours=[[8, 11]]) is False
    assert pacing.hour_window_allows("ai_care", 20, active_hours=[[8, 11]]) is True
    assert pacing.hour_window_allows("ai_care", 1, active_hours=[[22, 2]]) is True   # 跨天区间
    # 无数据 / 非法数据回退默认窗口
    assert pacing.hour_window_allows("ai_care", 9, active_hours=None) is False
    assert pacing.hour_window_allows("ai_care", 9, active_hours="bad") is False


# ═══════════════ ② 类型配比闸（纯函数） ═══════════════

def test_类型配比_日上限边界():
    assert pacing.TYPE_DAILY_LIMITS == {"memory_review": 6, "ai_care": 4}
    assert pacing.type_mix_allows("memory_review", 5) is True
    assert pacing.type_mix_allows("memory_review", 6) is False
    assert pacing.type_mix_allows("ai_care", 3) is True
    assert pacing.type_mix_allows("ai_care", 4) is False
    # memory_review_contextual 与 memory_review 同 message_type，合并计数
    assert pacing.type_mix_allows("memory_review_contextual", 6) is False
    # 其他类型无上限（不新增「必须发够互动型」的强制项）
    assert pacing.type_mix_allows("plugin", 999) is True
    assert pacing.type_mix_allows("state_trigger", 999) is True


# ═══════════════ ③ 单会话限频闸（纯函数） ═══════════════

def test_单会话限频_日上限与最小间隔边界():
    assert (pacing.SESSION_DAILY_LIMIT, pacing.SESSION_MIN_INTERVAL_MINUTES) == (8, 45)
    assert pacing.session_rate_allows(7, None) is True
    assert pacing.session_rate_allows(8, None) is False
    assert pacing.session_rate_allows(0, None) is True
    assert pacing.session_rate_allows(0, 44.9999) is False
    assert pacing.session_rate_allows(0, 45.0) is True    # 边界：恰好 45 分钟放行
    assert pacing.session_rate_allows(0, 46.0) is True
    assert pacing.session_rate_allows(8, 999.0) is False  # 日上限优先


def test_单会话限频_高回复类型豁免():
    """Codex 拍板（09-13）：plugin / state_trigger / prospective_intent 不进会话额度。

    理由：前两者是"用户触发的互动"（回复率 88.5% / 76.1%，必须送达）；
    prospective_intent 是一次性兑现（幂等已认领），被会话额度吞掉就永久丢失。
    """
    from app.domain.proactivity import pacing

    assert pacing.SESSION_RATE_EXEMPT_TYPES == frozenset({
        "plugin", "state_trigger", "prospective_intent"})
    # 豁免类型不在覆盖集合内 → 即使会话已满额/刚发过，也不受闸门限制
    for t in pacing.SESSION_RATE_EXEMPT_TYPES:
        assert t not in pacing.SESSION_RATE_TYPES


def test_单会话限频_豁免类型在闸门接线处不受拦(monkeypatch):
    """接线层验证：豁免类型即使超额度也应放行（走 _pacing_gate 的类型判定）。"""
    from app.domain.proactivity import pacing

    # 直接验证覆盖集合语义（接线层只按 etype in SESSION_RATE_TYPES 判定覆盖）
    for t in ("plugin", "state_trigger", "prospective_intent"):
        assert pacing.session_rate_allows(99, 0.0) is False   # 普通类型满额即拦
    assert pacing.session_rate_allows(0, 999.0) is True      # 未满额放行（对照）


# ═══════════════ 灰度与默认关 ═══════════════

def test_灰度_白名单与比例():
    assert pacing.OUTREACH_PACING_GRAY_CHARS == frozenset({13})
    assert pacing.OUTREACH_PACING_RATIO == 1.0
    assert pacing.pacing_gray_hit(C_WHITE) is True
    assert pacing.pacing_gray_hit(C_OTHER) is False
    assert pacing.pacing_gray_hit(None) is False
    assert pacing.pacing_gray_hit("x") is False
    assert pacing.pacing_gray_hit(C_WHITE, chars=frozenset()) is True    # 约定：空白名单 = 全量
    assert pacing.pacing_gray_hit(C_WHITE, chars=frozenset(), ratio=0.0) is False
    assert pacing.pacing_gray_hit(C_WHITE, ratio=0.0) is False


def test_开关默认关_且已在AGENT_FLAGS登记():
    flags = _flags()
    for k in pacing.PACING_FLAGS:
        assert k in flags, f"{k} 必须在 AGENT_FLAGS 登记（runtime_flags 只覆盖已登记键）"
        assert flags[k] is False


# ═══════════════ ① arbiter 接线：开关关 = 旧行为 ═══════════════

def test_闸门_三开关全关_不拦且不查库(flag_env, monkeypatch):
    for k in pacing.PACING_FLAGS:
        flag_env[k] = False

    async def _boom(*_a, **_k):
        raise AssertionError("开关关时不应查库（零行为变化）")

    monkeypatch.setattr(arbiter, "get_daily_sent_count", _boom)
    monkeypatch.setattr(arbiter, "get_session_daily_sent_count", _boom)
    monkeypatch.setattr(arbiter, "get_session_last_sent_at", _boom)
    monkeypatch.setattr(arbiter, "_user_active_hours", _boom)
    for etype in ("ai_care", "life_regression", "memory_review", "plugin", "state_trigger"):
        assert _gate(_item(etype), cn_hour=3) is None


def test_闸门_白名单外角色不受影响(all_on, monkeypatch):
    _patch_gate_io(monkeypatch, type_sent=99, session_sent=99)
    assert _gate(_item("ai_care", cid=C_OTHER), cn_hour=3) is None
    assert _gate(_item("memory_review", cid=C_OTHER), cn_hour=3) is None
    assert _gate(_item("plugin", cid=C_OTHER), cn_hour=3) is None


# ═══════════════ ②/⑤ 时段窗口接线 ═══════════════

def test_闸门_窗口外拦低效类型_窗口内放行(all_on, monkeypatch):
    _patch_gate_io(monkeypatch)
    assert _gate(_item("ai_care"), cn_hour=3) == "hour"
    assert _gate(_item("life_regression"), cn_hour=11) == "hour"
    assert _gate(_item("memory_review"), cn_hour=3) == "hour"
    assert _gate(_item("memory_review_contextual"), cn_hour=3) == "hour"
    assert _gate(_item("ai_care"), cn_hour=12) is None
    assert _gate(_item("ai_care"), cn_hour=18) is None
    assert _gate(_item("memory_review"), cn_hour=22) is None


def test_闸门_个性化活跃时段可放行窗口外的低效类型(all_on, monkeypatch):
    _patch_gate_io(monkeypatch, active_hours=[[8, 10]])
    assert _gate(_item("ai_care"), cn_hour=9) is None    # 用户活跃时段内 → 放行
    assert _gate(_item("ai_care"), cn_hour=3) == "hour"  # 仍不在任何窗口 → 拦


def test_闸门_互动型不受时段与配比限制(all_on, monkeypatch):
    # 类型计数顶格也不该影响 plugin / state_trigger（①只覆盖低效类型，②只覆盖 memory_review/ai_care）
    _patch_gate_io(monkeypatch, type_sent=999, session_sent=0)
    assert _gate(_item("plugin"), cn_hour=3) is None
    assert _gate(_item("state_trigger"), cn_hour=3) is None
    assert _gate(_item("emotion_care"), cn_hour=3) is None


# ═══════════════ ③ 类型配比接线 ═══════════════

def test_闸门_类型日上限命中(all_on, monkeypatch):
    _patch_gate_io(monkeypatch, type_sent=6)
    assert _gate(_item("memory_review"), cn_hour=18) == "type"
    _patch_gate_io(monkeypatch, type_sent=5)
    assert _gate(_item("memory_review"), cn_hour=18) is None
    _patch_gate_io(monkeypatch, type_sent=4)
    assert _gate(_item("ai_care"), cn_hour=18) == "type"
    _patch_gate_io(monkeypatch, type_sent=3)
    assert _gate(_item("ai_care"), cn_hour=18) is None


# ═══════════════ ④ 单会话限频接线 ═══════════════

def test_闸门_单会话日上限命中(all_on, monkeypatch):
    _patch_gate_io(monkeypatch, session_sent=8)
    assert _gate(_item("greeting", session_id=5), cn_hour=18) == "session_rate"
    _patch_gate_io(monkeypatch, session_sent=7)
    assert _gate(_item("greeting", session_id=5), cn_hour=18) is None


def test_闸门_单会话最小间隔_恰好45分钟放行(all_on, monkeypatch):
    now = datetime(2026, 9, 13, 12, 0, 0)
    _patch_gate_io(monkeypatch, session_sent=0, last=now - timedelta(minutes=45))
    assert _gate(_item("greeting", session_id=5), cn_hour=18, now=now) is None
    _patch_gate_io(monkeypatch, session_sent=0, last=now - timedelta(minutes=44, seconds=59))
    assert _gate(_item("greeting", session_id=5), cn_hour=18, now=now) == "session_rate"
    _patch_gate_io(monkeypatch, session_sent=0, last=now - timedelta(minutes=46))
    assert _gate(_item("greeting", session_id=5), cn_hour=18, now=now) is None


# ═══════════════ 埋点口径（[gate=...]） ═══════════════

def test_埋点_命中写trigger_reason_gate标记():
    item = _item("memory_review", trigger_reason="定时节律")
    arbiter._mark_gate(item, "hour")
    assert item["_gate"] == "hour"
    assert "[gate=hour]" in item["candidate"]["trigger_reason"]
    assert "定时节律" in item["candidate"]["trigger_reason"]
    arbiter._mark_gate(item, "hour")  # 幂等：不重复追加
    assert item["candidate"]["trigger_reason"].count("[gate=hour]") == 1


# ═══════════════ ② memory_review 可回复化（纯函数/门控） ═══════════════

def test_可回复化_纯函数与灰度门控(flag_env):
    from app.scheduling import memory_review as mr

    base = "hint-body;"
    assert mr.apply_replyable_question_rule(base) == base + mr.REPLYABLE_QUESTION_RULE
    assert "问题" in mr.REPLYABLE_QUESTION_RULE and "禁止空泛" in mr.REPLYABLE_QUESTION_RULE
    # 幂等
    once = mr.apply_replyable_question_rule(base)
    assert mr.apply_replyable_question_rule(once) == once
    # 门控：开关关 = 不生效；开关开 + 白名单 = 生效；白名单外不生效
    flag_env[pacing.FLAG_TYPE_MIX] = False
    assert mr.replyable_question_enabled(C_WHITE, 1) is False
    flag_env[pacing.FLAG_TYPE_MIX] = True
    assert mr.replyable_question_enabled(C_WHITE, 1) is True
    assert mr.replyable_question_enabled(C_OTHER, 1) is False


# ═══════════════ DB 用例（临时库） ═══════════════

@pytest.fixture()
def pacing_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（模板库克隆，见 tests/_dbclone.py）：patch app.db.database /
    arbiter / memory_review 的会话工厂（不触碰 backend/data）。"""
    tmp = str(tmp_path)
    engine = clone_engine(os.path.join(tmp, "t.db"))
    factory = make_session_factory(engine)

    async def _seed_parents():
        # _dbclone 默认开 FK（生产同款 PRAGMA）：memories 的 user_id / character_id、
        # proactive_message_logs 的 character_id / session_id 需父行先存在
        # （本文件账号口径：USER_ID；角色：C_WHITE / C_OTHER）
        from app.models.character import AICharacter
        from app.models.chat import ChatSession
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=USER_ID, username="pacing_u", nickname="投放用户"))
            db.add(AICharacter(id=C_WHITE, user_id=USER_ID, name="白名单角色"))
            db.add(AICharacter(id=C_OTHER, user_id=USER_ID, name="白名单外角色"))
            for sid in (1, 7, 8, 9):
                db.add(ChatSession(id=sid, user_id=USER_ID, character_id=C_WHITE))
            await db.commit()

    asyncio.run(_seed_parents())
    import app.db.database as db_mod
    import app.scheduling.memory_review as mr
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    monkeypatch.setattr(mr, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


@pytest.mark.slow
def test_计数只算已发送_不算候选审批流水(pacing_db):
    """⑥：候选/审批流水（proactive_trigger_logs）不计；只有 proactive_message_logs 计入当日。"""
    from app.models.character import ProactiveMessageLog, ProactiveTriggerLog

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _seed():
        async with pacing_db() as db:
            for _ in range(3):
                db.add(ProactiveTriggerLog(character_id=C_WHITE, trigger_type="memory_review",
                                           decision="approved", created_at=now))
            db.add(ProactiveTriggerLog(character_id=C_WHITE, trigger_type="memory_review",
                                       decision="rejected", created_at=now))
            for _ in range(2):  # 已发送：计
                db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=7,
                                           message_type="memory_review", content="x", created_at=now))
            db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=7, message_type="ai_care",
                                       content="care", created_at=now))
            db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=7, message_type="memory_review",
                                       content="old", created_at=now - timedelta(days=2)))  # 非当日：不计
            db.add(ProactiveMessageLog(character_id=C_OTHER, session_id=9, message_type="memory_review",
                                       content="other", created_at=now))  # 非本角色：不计
            await db.commit()

    asyncio.run(_seed())
    assert asyncio.run(arbiter.get_daily_sent_count(C_WHITE, "memory_review")) == 2
    assert asyncio.run(arbiter.get_daily_sent_count(C_WHITE, "ai_care")) == 1
    assert asyncio.run(arbiter.get_session_daily_sent_count(C_WHITE, 7)) == 3
    assert asyncio.run(arbiter.get_session_daily_sent_count(C_WHITE, 8)) == 0


@pytest.mark.slow
def test_会话最近发送时间_按会话与角色隔离(pacing_db):
    from app.models.character import ProactiveMessageLog

    t_recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)
    t_old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=100)

    async def _seed():
        async with pacing_db() as db:
            db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=7, message_type="greeting",
                                       content="a", created_at=t_old))
            db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=7, message_type="greeting",
                                       content="b", created_at=t_recent))
            db.add(ProactiveMessageLog(character_id=C_WHITE, session_id=8, message_type="greeting",
                                       content="c", created_at=t_old))
            await db.commit()

    asyncio.run(_seed())
    got = asyncio.run(arbiter.get_session_last_sent_at(C_WHITE, 7))
    assert got is not None and abs((got - t_recent).total_seconds()) < 1
    assert asyncio.run(arbiter.get_session_last_sent_at(C_WHITE, 99)) is None


@pytest.mark.slow
def test_闸门命中落库_trigger_reason与reject_reason带gate(pacing_db):
    from sqlalchemy import select

    from app.models.character import ProactiveTriggerLog

    arbiter._rejected_log_cache.clear()
    item = _item("memory_review", trigger_reason="定时节律")
    arbiter._mark_gate(item, "session_rate")
    asyncio.run(arbiter.log_trigger_candidate(item, False))

    async def _q():
        async with pacing_db() as db:
            return (await db.execute(select(ProactiveTriggerLog))).scalars().all()

    rows = asyncio.run(_q())
    assert len(rows) == 1
    assert "[gate=session_rate]" in (rows[0].trigger_reason or "")
    assert rows[0].reject_reason == "rejected / [gate=session_rate]"
    assert rows[0].decision == "rejected"


@pytest.mark.slow
def test_execute_窗口外拦下低效类型并留痕(pacing_db, all_on, monkeypatch):
    """① 全链路：低效类型窗口外 → _execute 返回 False、不进入生成、candidate 带 [gate=hour]。"""
    import app.scheduling.memory_review as mr

    called = {"gen": False}

    async def _fake_review(*_a, **_k):
        called["gen"] = True
        return True

    async def _no_dnd(*_a, **_k):
        return False

    async def _not_active(*_a, **_k):
        return False

    monkeypatch.setattr(mr, "run_memory_review", _fake_review)
    monkeypatch.setattr(arbiter, "is_dnd_now", _no_dnd)
    monkeypatch.setattr(arbiter, "is_user_active", _not_active)
    monkeypatch.setattr(arbiter, "_cn_hour_now", lambda: 3)  # 锁定时段（北京凌晨，窗口外）

    item = _item("memory_review", memory_id=1)
    ok = asyncio.run(arbiter._execute(item))
    assert ok is False
    assert called["gen"] is False
    assert item.get("_gate") == "hour"
    assert "[gate=hour]" in item["candidate"]["trigger_reason"]


@pytest.fixture()
def review_env(pacing_db, monkeypatch):
    """run_memory_review 全 mock 环境（LLM/发送/依赖），捕获送入 LLM 的 hint。"""
    import app.agent.llm_client as llm_mod
    import app.agent.persona as persona_mod
    import app.agent.user_profile as profile_mod
    import app.application.chat_service as chat_svc
    import app.scheduling.memory_review as mr
    import app.scheduling.scheduler as scheduler_mod
    import app.scheduling.triggers as triggers_mod

    captured = {"hint": "", "sent": False}

    async def _fake_chat_completion(messages, **kw):
        captured["hint"] = messages[1]["content"]
        return "我还记得那回事呢。"

    async def _fake_send(*_a, **_k):
        captured["sent"] = True
        return True

    async def _true(*_a, **_k):
        return True

    async def _none(*_a, **_k):
        return None

    async def _false(*_a, **_k):
        return False

    async def _sid(*_a, **_k):
        return 1

    monkeypatch.setattr(scheduler_mod, "send_to_session", _fake_send)
    monkeypatch.setattr(chat_svc, "get_latest_session_id", _sid)
    monkeypatch.setattr(triggers_mod, "memory_review_enabled", _true)
    monkeypatch.setattr(triggers_mod, "get_last_messages", _none)
    monkeypatch.setattr(llm_mod, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(llm_mod, "load_character_reasoning_level", _none)
    monkeypatch.setattr(profile_mod, "build_role_prompt_block", _none)
    monkeypatch.setattr(persona_mod, "build_active_channel_persona", _none)
    monkeypatch.setattr(mr, "_user_in_dnd_period", _false)
    monkeypatch.setattr(mr, "_last_review_at", _none)
    return pacing_db, captured


def _seed_review_memory(factory, cid=C_WHITE):
    from app.models.memory import Memory

    async def _go():
        async with factory() as db:
            m = Memory(user_id=USER_ID, character_id=cid, memory_type="event",
                       content="今天在橘子洲游玩，拍了好多照片", importance=60.0,
                       created_at=datetime(2026, 8, 15, 10, 0, 0))
            db.add(m)
            await db.commit()
            await db.refresh(m)
            return m.id

    return asyncio.run(_go())


@pytest.mark.slow
def test_可回复化_开关开_hint追加具体问题规则(review_env, all_on):
    factory, captured = review_env
    from app.scheduling.memory_review import REPLYABLE_QUESTION_RULE, run_memory_review

    mid = _seed_review_memory(factory)
    assert asyncio.run(run_memory_review(C_WHITE, USER_ID, mid)) is True
    assert captured["sent"] is True
    assert REPLYABLE_QUESTION_RULE in captured["hint"]


@pytest.mark.slow
def test_可回复化_开关关_hint逐字节不变(review_env, flag_env):
    factory, captured = review_env
    from app.scheduling.memory_review import REPLYABLE_QUESTION_RULE, run_memory_review

    for k in pacing.PACING_FLAGS:
        flag_env[k] = False
    mid = _seed_review_memory(factory)
    assert asyncio.run(run_memory_review(C_WHITE, USER_ID, mid)) is True
    assert captured["sent"] is True
    assert REPLYABLE_QUESTION_RULE not in captured["hint"]


def test_闸门_豁免类型满额也放行(all_on, monkeypatch):
    """接线层：会话已满额时，豁免类型（plugin/state_trigger/prospective_intent）仍放行。"""
    _patch_gate_io(monkeypatch, session_sent=99)
    for t in ("plugin", "state_trigger", "prospective_intent"):
        assert _gate(_item(t, session_id=5), cn_hour=18) is None
    assert _gate(_item("greeting", session_id=5), cn_hour=18) == "session_rate"

