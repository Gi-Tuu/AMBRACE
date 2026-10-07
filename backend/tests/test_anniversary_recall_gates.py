# -*- coding: utf-8 -*-
"""断点 #8′ E14（2026-09-29）：纪念日回忆通道补最小组内核闸 + 从主题熔断豁免白名单摘出。

原状：``_anniversary_tick → _check_anniversaries_today`` 的发送循环**零道闸**，且
``anniversary_recall`` 在主题熔断豁免白名单里 ⇒ 全链路无任何频控（生产 shared_events
里 is_anniversary=1 行数为 0，属「有数据就裸奔」）。
现补最小组三道闸 ⑥资格 / ②每小时上限 / ④免打扰，口径全部复用内核现成函数，命中只减不发。

全部用例走 monkeypatch（假闸 + 假发送出口），**不连生产库、不建表**。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent.loop import AGENT_FLAGS
from app.domain.proactivity.decision import MAX_PER_HOUR
from app.scheduling import arbiter, gates
from app.scheduling import proactive_topic_guard as guard
from app.scheduling import scheduler as sched

CID, UID, SID = 101, 7, 9
OTHER_CID = 202
_CN_TZ = timezone(timedelta(hours=8))
_CN_NOON = datetime(2026, 9, 29, 12, 0, tzinfo=_CN_TZ)   # 北京正午：内核 DND 一定不命中
_TEXT = "还记得吗？30天前的今天，我们一起吃粥。"
# 抢下内核原函数（monkeypatch 用例内需要「真口径」对照）
_REAL_IS_DND = arbiter.is_dnd_now


def _ev(eid: int, cid: int = CID, uid: int = UID) -> SimpleNamespace:
    """一条到点纪念日事件（只带被测代码用到的四个字段）。"""
    return SimpleNamespace(id=eid, character_id=cid, user_id=uid,
                           event_time=datetime(2026, 8, 30), title="一起吃粥")


def _cn(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=_CN_TZ)


# ───────────────────────── 桩件 ─────────────────────────
def _const(value):
    """把闸桩成「恒返回 value」的协程函数。"""
    async def _f(*_a, **_k):
        return value
    return _f


def _boom(*_a, **_k):
    async def _f():
        raise RuntimeError("闸取数失败")
    return _f()


class _Session:
    """假异步会话：查询恒返回给定对象，写入全是 no-op（不落任何库）。"""

    def __init__(self, obj=None):
        self.obj = obj

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        obj = self.obj

        class _R:
            def scalar_one_or_none(_self):
                return obj

            def scalars(_self):
                return _self

            def all(_self):
                return []

        return _R()

    def add(self, *_a, **_k):
        return None

    async def flush(self):
        return None

    async def refresh(self, obj, *_a, **_k):
        if getattr(obj, "id", None) is None:
            obj.id = 1
        return None

    async def commit(self):
        return None


@pytest.fixture
def env(monkeypatch):
    """闸默认全放行 + 捕获发送/留痕，逐用例只拨需要的那一道。"""
    sent, rejected = [], []
    due = [_ev(1)]
    char = SimpleNamespace(id=CID, name="小爱")

    async def _check(_db):
        return list(due)
    monkeypatch.setattr("app.memory.shared_events.check_anniversaries", _check)
    monkeypatch.setattr("app.memory.shared_events.anniversary_text", lambda _e: _TEXT)
    monkeypatch.setattr(sched, "async_session_factory", lambda: _Session(char))

    async def _latest(_uid, _cid):
        return SID
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _latest)

    async def _active_chars(*_a, **_k):
        return [{"character_id": CID, "user_id": UID}]
    monkeypatch.setattr(arbiter, "get_active_characters", _active_chars)
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))

    async def _cap_log(item, executed):
        rejected.append({"item": item, "executed": executed})
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _cap_log)

    async def _send(session_id, character_id, user_id, content, message_type="state_trigger", **_k):
        sent.append((session_id, character_id, user_id, content, message_type))
    monkeypatch.setattr(sched, "send_to_session", _send)

    return SimpleNamespace(sent=sent, rejected=rejected, due=due, monkeypatch=monkeypatch)


def _run(env):
    return asyncio.run(sched._check_anniversaries_today())


def _blocked(env, gate):
    """断言：没发出去，且留痕是一条带闸名的 rejected。"""
    assert env.sent == [], f"闸 {gate} 命中却仍发送"
    assert env.rejected, "被拦必须写 rejected 留痕"
    last = env.rejected[-1]
    assert last["executed"] is False
    assert last["item"]["type"] == "anniversary_recall"
    assert last["item"].get("_gate") == gate


# ───────────────────────── ⑥ 资格 enable_proactive ─────────────────────────
def test_not_eligible_blocks(env):
    """角色不在内核合格名单（is_active=False 或关掉主动交流）→ 拦 + 留痕。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    _run(env)
    _blocked(env, "not_eligible")


def test_eligible_character_passes_all_gates(env):
    """「不该拦」：资格通过 + 本小时 0 条 + 非免打扰 → 闸返回 None。"""
    assert asyncio.run(sched._anniversary_gate_reason(CID, _CN_NOON)) is None


def test_eligibility_uses_kernel_active_characters(env):
    """资格口径复用 arbiter.get_active_characters（含 enable_proactive），不是自己查角色表。"""
    seen = []

    async def _spy(*_a, **_k):
        seen.append(1)
        return [{"character_id": CID}]
    env.monkeypatch.setattr(arbiter, "get_active_characters", _spy)
    _run(env)
    assert seen and len(env.sent) == 1


# ───────────────────────── ② 每小时上限（复用内核 MAX_PER_HOUR，不自创阈值） ─────────────────────────
def test_hourly_cap_blocks(env):
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(MAX_PER_HOUR))
    _run(env)
    _blocked(env, "hourly_cap")


def test_hourly_cap_just_below_allows(env):
    """边界：上限-1 放行（口径与内核 ``>= MAX_PER_HOUR`` 完全一致，不多拦一条）。"""
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(MAX_PER_HOUR - 1))
    _run(env)
    assert len(env.sent) == 1 and env.rejected == []


def test_hourly_threshold_not_forked():
    """本通道不定义自己的每小时阈值常量（避免又多一个口径）。"""
    assert not hasattr(sched, "ANNIVERSARY_MAX_PER_HOUR")
    body = _gate_source()
    assert "MAX_PER_HOUR" in body and "get_hourly_active_count" in body


def _gate_source() -> str:
    with open(sched.__file__, encoding="utf-8") as f:
        src = f.read()
    return src.split("async def _anniversary_gate_reason")[1].split(
        "async def _check_anniversaries_today")[0]


# ───────────────────────── ④ 免打扰（内核口径，含角色级配置） ─────────────────────────
def test_dnd_blocks(env):
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(True))
    _run(env)
    _blocked(env, "dnd")


def test_dnd_follows_kernel_window(env):
    """真 ``arbiter.is_dnd_now``：未配免打扰时只挡内核深夜 0–7 点，23:30 放行。"""
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _REAL_IS_DND)
    # A20 批 1b：is_dnd_now 函数体已搬到 gates，块内互调 get_dnd_window 在 gates
    # 命名空间解析；arbiter 侧只剩具名重导出，只打 arbiter 桩看不见。两边同打同一个桩。
    window_none = _const(None)
    env.monkeypatch.setattr(arbiter, "get_dnd_window", window_none)
    env.monkeypatch.setattr(gates, "get_dnd_window", window_none)
    assert asyncio.run(sched._anniversary_gate_reason(CID, _cn(23, 30))) is None
    assert asyncio.run(sched._anniversary_gate_reason(CID, _cn(3, 0))) == "dnd"


def test_dnd_reads_character_config_window(env):
    """角色配了免打扰 13:00–14:00：13:30 拦、23:30 放（原通道压根读不到这份配置）。"""
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _REAL_IS_DND)
    # A20 批 1b：同上——get_dnd_window 由 gates 里的 is_dnd_now 函数体解析，两边同打同一个桩。
    window_13_14 = _const((13 * 60, 14 * 60))
    env.monkeypatch.setattr(arbiter, "get_dnd_window", window_13_14)
    env.monkeypatch.setattr(gates, "get_dnd_window", window_13_14)
    assert asyncio.run(sched._anniversary_gate_reason(CID, _cn(13, 30))) == "dnd"
    assert asyncio.run(sched._anniversary_gate_reason(CID, _cn(23, 30))) is None


def test_gate_uses_beijing_time_not_utc(env):
    """发送循环传下去的是北京时间（UTC 04:00 = 北京 12:00，绝不能按 UTC 判成深夜）。"""
    captured = {}

    async def _spy(_cid, cn_now):
        captured["offset"] = cn_now.utcoffset()
        captured["hour"] = cn_now.hour
        return False
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _spy)

    fixed_utc = datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc if tz is None else fixed_utc.astimezone(tz)
    env.monkeypatch.setattr(sched, "datetime", _DT)

    _run(env)
    assert captured["offset"] == timedelta(hours=8)
    assert captured["hour"] == 12
    assert len(env.sent) == 1


def test_cn_now_taken_once_per_round(env):
    """整轮只取一次时间：N 条事件不会因逐条重取而产生口径漂移。"""
    n = {"dt_calls": 0}

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            n["dt_calls"] += 1
            return _CN_NOON if tz is None else _CN_NOON
    env.due[:] = [_ev(1), _ev(2), _ev(3)]
    env.monkeypatch.setattr(sched, "datetime", _DT)
    _run(env)
    assert len(env.sent) == 3
    assert n["dt_calls"] == 1


# ───────────────────────── 异常 / 脏身份：一律不放行 ─────────────────────────
def test_gate_error_blocks(env):
    """闸取数抛异常 → 判为拦截（宁可不发，也不绕过内核频控）。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    _run(env)
    _blocked(env, "kernel_gate_error")


def test_dnd_query_error_blocks(env):
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _boom)
    _run(env)
    _blocked(env, "kernel_gate_error")


def test_bad_identity_blocks():
    assert asyncio.run(sched._anniversary_gate_reason(0, _CN_NOON)) == "bad_identity"
    assert asyncio.run(sched._anniversary_gate_reason(None, _CN_NOON)) == "bad_identity"
    assert asyncio.run(sched._anniversary_gate_reason("x", _CN_NOON)) == "bad_identity"


# ───────────────────────── 只减不发：不扩大拦截、不影响既有分支 ─────────────────────────
def test_all_gates_pass_still_sends(env):
    _run(env)
    assert len(env.sent) == 1
    assert env.sent[0] == (SID, CID, UID, _TEXT, "anniversary_recall")
    assert env.rejected == []          # 没被拦就不该留 rejected


def test_missing_character_unchanged(env):
    """角色查不到（原有语义）：仍不发、也不新增留痕（本次改动没碰这条分支）。"""
    env.monkeypatch.setattr(sched, "async_session_factory", lambda: _Session(None))
    _run(env)
    assert env.sent == [] and env.rejected == []


def test_no_session_unchanged(env):
    """没有可用会话（原有语义）：不发，且不因改动多出留痕。"""
    async def _none(_uid, _cid):
        return None
    env.monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _none)
    _run(env)
    assert env.sent == [] and env.rejected == []


def test_gate_per_item_only_blocks_hit_item(env):
    """逐条判闸：同轮里合格角色照发、不合格角色被拦（一条被拦不饿死其它条）。"""
    env.due[:] = [_ev(1), _ev(2, cid=OTHER_CID), _ev(3)]
    env.monkeypatch.setattr(arbiter, "get_active_characters",
                            _const([{"character_id": CID}]))
    _run(env)
    assert len(env.sent) == 2 and all(s[1] == CID for s in env.sent)
    assert len(env.rejected) == 1
    assert env.rejected[0]["item"]["candidate"]["character_id"] == OTHER_CID
    assert env.rejected[0]["item"].get("_gate") == "not_eligible"


def test_check_exception_still_isolated(env):
    """整轮异常仍被外层 try 吞掉（纪念日失败静默，绝不掀翻周期循环）。"""
    async def _raise(_db):
        raise RuntimeError("查库失败")
    env.monkeypatch.setattr("app.memory.shared_events.check_anniversaries", _raise)
    assert _run(env) is None
    assert env.sent == []


# ───────────────────────── 留痕字段照内核同款 ─────────────────────────
def test_rejected_uses_kernel_fields(env):
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(9))
    _run(env)
    assert len(env.rejected) == 1
    item = env.rejected[0]["item"]
    assert item["type"] == "anniversary_recall"
    assert item["priority"] == 3
    assert item["candidate"]["character_id"] == CID
    assert item["candidate"]["user_id"] == UID
    assert "[gate=hourly_cap]" in item["candidate"]["trigger_reason"]


def test_rejected_priority_matches_kernel_special_family():
    """priority 与内核采集层节庆同类同档（sources/special.py 的 anniversary=3），不是自创值。"""
    from app.scheduling.sources import special as special_src

    with open(sched.__file__, encoding="utf-8") as f:
        src = f.read()
    assert "_ANNIVERSARY_LOG_PRIORITY = 3" in src
    with open(special_src.__file__, encoding="utf-8") as f:
        assert 'TriggerItem(type="anniversary", priority=3' in f.read()
    # 该常量确实传进了留痕（而不是写死在别处）
    body = src.split("async def _log_anniversary_rejected")[1].split(
        "async def _anniversary_gate_reason")[0]
    assert "_ANNIVERSARY_LOG_PRIORITY" in body


def test_rejected_goes_through_kernel_logger(env):
    """留痕复用 arbiter.log_trigger_candidate（同款字段、同款 rejected 5 分钟节流），不自建表。"""
    with open(sched.__file__, encoding="utf-8") as f:
        src = f.read()
    body = src.split("async def _log_anniversary_rejected")[1].split(
        "async def _anniversary_gate_reason")[0]
    assert "log_trigger_candidate" in body
    assert "ProactiveTriggerLog(" not in body      # 不新建写入路径


def test_each_blocked_item_reports_to_kernel_logger(env):
    """被拦逐条上报内核（节流由内核 log_trigger_candidate 自己管，本通道不再自加一层）。"""
    env.due[:] = [_ev(1), _ev(2), _ev(3)]
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    _run(env)
    assert len(env.rejected) == 3


def test_rejected_log_failure_never_changes_sending(env):
    """留痕写失败 → 主链路不因观测缺口改变发送行为（仍不发），也不抛出去。"""
    env.monkeypatch.setattr(arbiter, "log_trigger_candidate", _boom)
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(True))
    assert _run(env) is None
    assert env.sent == []


def test_rejected_log_failure_does_not_break_later_items(env):
    """留痕异常被吞掉：同轮后续合格条目仍能发出（异常绝不掀翻整轮）。"""
    env.due[:] = [_ev(1), _ev(2)]
    state = {"n": 0}

    async def _first_blocked(*_a, **_k):
        state["n"] += 1
        return [] if state["n"] == 1 else [{"character_id": CID}]
    env.monkeypatch.setattr(arbiter, "get_active_characters", _first_blocked)
    env.monkeypatch.setattr(arbiter, "log_trigger_candidate", _boom)
    _run(env)
    assert len(env.sent) == 1


# ───────────────────────── ② 白名单摘出（选「摘出」而非「豁免但受频控」） ─────────────────────────
def test_anniversary_recall_no_longer_exempt():
    assert "anniversary_recall" not in guard.GUARD_EXEMPT_TYPES


def test_other_holiday_channels_still_exempt():
    """只摘 anniversary_recall 这一条：内核节庆三通道的豁免没被顺手删。"""
    assert guard.GUARD_EXEMPT_TYPES == frozenset({"birthday", "holiday", "anniversary"})


def _patch_send_exit_writes(monkeypatch):
    """把 send_to_session 后半段的写库/推送/离线通知都桩掉（只验主题熔断这一道）。"""
    monkeypatch.setattr(sched, "async_session_factory", lambda: _Session(SimpleNamespace(id=SID)))

    async def _push(*_a, **_k):
        return True
    monkeypatch.setattr("app.ws.connection_manager.push_to_session", _push)

    async def _notify(*_a, **_k):
        return None
    monkeypatch.setattr("app.application.push_service.notify_user", _notify)


def test_send_to_session_now_applies_topic_guard(monkeypatch):
    """出口集成：摘出后 send_to_session 的主题熔断对 anniversary_recall 生效，命中即不写库不推送。"""
    called = []

    async def _suppress(_cid, _content):
        called.append(_content)
        return True, "topic-meal-closed-by-user"
    monkeypatch.setattr(guard, "should_suppress", _suppress)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_topic_guard", True)

    async def _no_db():
        raise AssertionError("被拦后不应写库/推送")
    monkeypatch.setattr(sched, "async_session_factory", _no_db)

    # A37 批 1：抑制分支不再返回 None（裸 return＝静默蒸发），改返回 SendResult(False, "topic_guard")
    _res = asyncio.run(sched.send_to_session(
        SID, CID, UID, _TEXT, message_type="anniversary_recall"))
    assert _res.ok is False and _res.reason == "topic_guard"
    assert called == [_TEXT]


def test_send_to_session_birthday_still_bypasses_guard(monkeypatch):
    """对照：仍在白名单的 birthday 不调熔断、照常写库（没越界扩大拦截）。"""
    called = []

    async def _suppress(_cid, _content):
        called.append(_content)
        return True, "topic-meal-closed-by-user"
    monkeypatch.setattr(guard, "should_suppress", _suppress)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_topic_guard", True)
    _patch_send_exit_writes(monkeypatch)

    asyncio.run(sched.send_to_session(SID, CID, UID, _TEXT, message_type="birthday"))
    assert called == []


def test_send_to_session_anniversary_recall_passes_when_topic_open(monkeypatch):
    """熔断放行时纪念日照发（摘出白名单≠纪念日必被拦；无主题词本来就放行）。"""
    async def _ok(_cid, _content):
        return False, "no-topic"
    monkeypatch.setattr(guard, "should_suppress", _ok)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_topic_guard", True)
    _patch_send_exit_writes(monkeypatch)

    asyncio.run(sched.send_to_session(SID, CID, UID, _TEXT, message_type="anniversary_recall"))


def test_topic_guard_flag_off_keeps_behaviour(monkeypatch):
    """flag 关（默认）：白名单摘出对现网零行为变化，纪念日仍照常发。"""
    called = []

    async def _suppress(_cid, _content):
        called.append(_content)
        return True, "topic-x"
    monkeypatch.setattr(guard, "should_suppress", _suppress)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_topic_guard", False)
    _patch_send_exit_writes(monkeypatch)

    asyncio.run(sched.send_to_session(SID, CID, UID, _TEXT, message_type="anniversary_recall"))
    assert called == []


def test_anniversary_text_has_no_topic_bucket_by_default():
    """纯文案层面：纪念日常规措辞不含主题词 ⇒ 熔断只在标题撞主题时才可能拦（风险面窄）。"""
    assert guard.topic_bucket("还记得吗？30天前的今天，我们一起经历的那件事。时间过得真快。") is None
    assert guard.topic_bucket("还记得吗？30天前的今天，一起吃的晚饭。") == "meal"


# ───────────────────────── 其它通道的既有闸与阈值不动 ─────────────────────────
def test_other_channels_gate_helpers_untouched():
    """本次只动纪念日通道：其它通道的内核闸函数仍在原位。"""
    from app.scheduling import life_share, state_triggers

    assert callable(life_share._kernel_gate_reason)
    assert callable(state_triggers._kernel_gate_reason)
    assert state_triggers.MAX_PER_HOUR == 2      # 本通道不借用它的 2，走内核 MAX_PER_HOUR
