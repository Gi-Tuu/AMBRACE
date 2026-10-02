# -*- coding: utf-8 -*-
"""断点 #8′ E12（2026-09-29）：phone_auto_notify「通知提及」直发点补齐内核闸 + 通道可关。

口径来源：output/AMBRACE_断点8prime_其他直发点核实_20260929.md §1.1(E12)、§3。
原状：HTTP 驱动（POST /phone/perception/auto → handle_auto_report → _trigger_mention）直发，
只有硬编码 23–8 点 + 每用户 30 分钟 + 说睡觉，缺 ⑥资格 ①90 分钟 ②每小时 ④DND ⑦pending。
全部用例走 monkeypatch（假 arbiter 闸 + 假发送出口），**不连生产库、不建表**。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agent.loop import AGENT_FLAGS
from app.application import phone_auto_notify_service as svc
from app.scheduling import arbiter, gates
from app.utils.timeutil import now_naive_utc

CID, UID, SID = 101, 7, 9
ITEMS = [{"package": "com.calendar", "app": "日历", "title": "周会", "text": "下午三点"}]
_CHAR = SimpleNamespace(id=CID, name="小爱", personality="友善", chat_style="自然")
# 打桩前抢下内核原函数（monkeypatch 用例内需要「真口径」对照）
_REAL_IS_DND = arbiter.is_dnd_now


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
    """假异步会话：查询恒返回给定 state，写入全是 no-op（不落任何库）。"""

    def __init__(self, state):
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        state = self.state

        class _R:
            def scalar_one_or_none(_self):
                return state

            def scalars(_self):
                return _self

            def all(_self):
                return []

        return _R()

    async def get(self, *_a, **_k):
        return None

    def add(self, *_a, **_k):
        return None

    async def commit(self):
        return None

    async def refresh(self, *_a):
        return None


def _state_factory(state):
    return lambda: _Session(state)


def _report_state(prev_fps: list[str], last_trigger_at=None):
    return SimpleNamespace(
        id=1, user_id=UID, fingerprints=json.dumps(prev_fps),
        last_trigger_at=last_trigger_at, updated_at=None,
    )


@pytest.fixture
def env(monkeypatch):
    """闸默认全放行 + 捕获发送/留痕，逐用例只拨需要的那一道。"""
    sent, rejected = [], []
    monkeypatch.setitem(AGENT_FLAGS, "phone_auto_notify_mention", True)

    async def _pick(*_a, **_k):
        return (_CHAR, SimpleNamespace(id=SID), SID)
    monkeypatch.setattr(svc, "_select_character", _pick)
    monkeypatch.setattr(svc, "_generate_mention", _const("下午那个会别忘了，要不要我提醒你？"))

    async def _active_chars(*_a, **_k):
        return [{"character_id": CID, "user_id": UID}]
    monkeypatch.setattr(arbiter, "get_active_characters", _active_chars)
    monkeypatch.setattr(arbiter, "has_pending_timer", _const(False))
    monkeypatch.setattr(arbiter, "has_pending_storyline", _const(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "get_last_proactive_time", _const(None))
    monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))

    async def _cap_log(item, executed):
        rejected.append({"item": item, "executed": executed})
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _cap_log)

    async def _send(session_id, character_id, user_id, content, message_type="state_trigger"):
        sent.append((session_id, character_id, user_id, content, message_type))
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)

    return SimpleNamespace(sent=sent, rejected=rejected, monkeypatch=monkeypatch)


def _run(env) -> bool:
    return asyncio.run(svc._trigger_mention(UID, ITEMS))


def _blocked(env, gate):
    """断言：没发出去，且留痕是一条带闸名的 rejected。"""
    assert env.sent == [], f"闸 {gate} 命中却仍发送"
    assert env.rejected, "被拦必须写 rejected 留痕"
    last = env.rejected[-1]
    assert last["executed"] is False
    assert last["item"]["type"] == "notification_mention"
    assert last["item"].get("_gate") == gate


def _freeze_cn_now(env, hour: int, minute: int = 0):
    """把模块内取北京时间的那一处 datetime.now 钉死（只影响 svc 命名空间）。"""
    fixed = datetime(2026, 9, 29, hour, minute, tzinfo=timezone(timedelta(hours=8)))

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)
    env.monkeypatch.setattr(svc, "datetime", _DT)


# ───────────────────────── ⑥ 资格 enable_proactive ─────────────────────────
def test_not_eligible_blocks(env):
    """选中角色不在内核合格名单（关掉主动交流）→ 拦 + 留痕（历史 9 条里 8 条正是这种角色）。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    assert _run(env) is False
    _blocked(env, "not_eligible")


def test_eligible_character_passes_gate(env):
    """「不该拦」：角色在内核名单里 → 资格闸放行。"""
    assert asyncio.run(svc._kernel_gate_reason(CID, UID)) is None


# ───────────────────────── ① 90 分钟最小间隔 ─────────────────────────
def test_min_interval_blocks(env):
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=30)))
    assert _run(env) is False
    _blocked(env, "min_interval")


def test_min_interval_boundary_allows(env):
    """「不该拦」：距上一条 91 分钟 > 90 → 仍应发出。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=91)))
    assert _run(env) is True
    assert len(env.sent) == 1


# ───────────────────────── ② 每小时上限 ─────────────────────────
def test_hourly_cap_blocks(env):
    from app.domain.proactivity.decision import MAX_PER_HOUR
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(MAX_PER_HOUR))
    assert _run(env) is False
    _blocked(env, "hourly_cap")


def test_hourly_cap_below_allows(env):
    """「不该拦」：本小时 1 条（< 上限 2）→ 仍应发出。"""
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(1))
    assert _run(env) is True


# ───────────────────────── ④ 免打扰：内核口径取代硬编码 23–8 点 ─────────────────────────
def test_dnd_blocks(env):
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(True))
    assert _run(env) is False
    _blocked(env, "dnd")


def test_hardcoded_quiet_hours_gone(env):
    """硬编码 23:00–08:00 判据已删除（发送侧不再自己判钟点）。"""
    assert not hasattr(svc, "_in_quiet_hours")
    assert not hasattr(svc, "QUIET_START_HOUR") and not hasattr(svc, "QUIET_END_HOUR")


def test_dnd_follows_kernel_not_hardcoded_window(env):
    """北京 23:30：旧硬编码会静默，内核默认口径（未配置免打扰＝只挡 0–7 点）放行；
    凌晨 03:00：内核拦 ⇒ 行为与 arbiter.is_dnd_now 一致，而非与 23–8 点一致。"""
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _REAL_IS_DND)
    # A20 批 1b：is_dnd_now 函数体已搬到 gates，块内互调 get_dnd_window 在 gates
    # 命名空间解析；arbiter 侧只剩具名重导出，只打 arbiter 桩看不见。两边同打同一个桩。
    window_none = _const(None)
    env.monkeypatch.setattr(arbiter, "get_dnd_window", window_none)
    env.monkeypatch.setattr(gates, "get_dnd_window", window_none)

    _freeze_cn_now(env, 23, 30)
    assert _run(env) is True, "23:30 内核不拦，不该再被硬编码 23–8 点挡掉"
    assert len(env.sent) == 1

    env.sent.clear()
    _freeze_cn_now(env, 3, 0)
    assert _run(env) is False
    _blocked(env, "dnd")


def test_dnd_reads_character_config_window(env):
    """角色配了免打扰 13:00–14:00：14:30 放行 / 13:30 拦（原硬编码时段完全看不到这份配置）。"""
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _REAL_IS_DND)
    # A20 批 1b：同上——get_dnd_window 由 gates 里的 is_dnd_now 函数体解析，两边同打同一个桩。
    window_13_14 = _const((13 * 60, 14 * 60))
    env.monkeypatch.setattr(arbiter, "get_dnd_window", window_13_14)
    env.monkeypatch.setattr(gates, "get_dnd_window", window_13_14)

    _freeze_cn_now(env, 23, 30)   # 落在旧硬编码窗口内，但角色没配这个时段
    assert _run(env) is True
    env.sent.clear()
    env.rejected.clear()

    _freeze_cn_now(env, 13, 30)
    assert _run(env) is False
    _blocked(env, "dnd")


# ───────────────────────── ⑦ pending 互斥 ─────────────────────────
def test_pending_timer_blocks(env):
    """AI 有未到期定时承诺（正在「洗澡/睡觉」）→ 不插话。"""
    env.monkeypatch.setattr(arbiter, "has_pending_timer", _const(True))
    assert _run(env) is False
    _blocked(env, "pending_timer")


def test_pending_storyline_blocks(env):
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    assert _run(env) is False
    _blocked(env, "pending_storyline")


# ───────────────────────── 夜间说过睡觉（原判定并入闸簇，同样留痕） ─────────────────────────
def test_night_said_sleep_blocks(env):
    env.monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(True))
    assert _run(env) is False
    _blocked(env, "night_said_sleep")


# ───────────────────────── 异常/脏身份口径：不放行 ─────────────────────────
def test_gate_error_blocks(env):
    """闸取数抛异常 → 判为拦截（宁可不发，也不绕过内核频控）。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    assert _run(env) is False
    _blocked(env, "kernel_gate_error")


def test_bad_identity_blocks():
    assert asyncio.run(svc._kernel_gate_reason(0, UID)) == "bad_identity"
    assert asyncio.run(svc._kernel_gate_reason(None, UID)) == "bad_identity"
    assert asyncio.run(svc._kernel_gate_reason(CID, 0)) == "bad_identity"


# ───────────────────────── 全通过仍能发（只减不发，不扩大拦截） ─────────────────────────
def test_all_gates_pass_still_sends(env):
    assert _run(env) is True
    assert len(env.sent) == 1
    assert env.sent[0] == (SID, CID, UID, env.sent[0][3], "notification_mention")
    assert env.rejected == []          # 没被拦就不该留 rejected


def test_empty_generation_blocks_with_trace(env):
    """生成为空 → 不发 + 留痕（原实现只 return，静默无痕迹）。"""
    env.monkeypatch.setattr(svc, "_generate_mention", _const(""))
    assert _run(env) is False
    _blocked(env, "empty")


# ───────────────────────── 留痕内容 ─────────────────────────
def test_rejected_uses_kernel_fields(env):
    """留痕字段照内核同款：trigger_type / priority / candidate 双 id / trigger_reason 带闸名。"""
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(9))
    _run(env)
    assert len(env.rejected) == 1
    item = env.rejected[0]["item"]
    assert item["type"] == "notification_mention"
    assert item["priority"] == 5
    assert item["candidate"]["character_id"] == CID
    assert item["candidate"]["user_id"] == UID
    assert "[gate=hourly_cap]" in item["candidate"]["trigger_reason"]


def test_rejected_log_failure_never_blocks_flow(env):
    """留痕写失败（内核记录函数抛异常）→ 主链路不因观测缺口而改变发送行为。"""
    async def _fail(*_a, **_k):
        raise RuntimeError("留痕写不进库")
    env.monkeypatch.setattr(arbiter, "log_trigger_candidate", _fail)
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    assert _run(env) is False
    assert env.sent == []


# ───────────────────────── 通道开关 phone_auto_notify_mention ─────────────────────────
def test_flag_off_stops_whole_channel(env):
    """关＝整条通道不发：连选人/生成都不走，零 LLM、零留痕。"""
    called = []

    async def _spy(*_a, **_k):
        called.append(1)
        return (_CHAR, SimpleNamespace(id=SID), SID)
    env.monkeypatch.setattr(svc, "_select_character", _spy)
    env.monkeypatch.setitem(AGENT_FLAGS, "phone_auto_notify_mention", False)
    assert _run(env) is False
    assert called == [] and env.sent == [] and env.rejected == []


def test_flag_default_on_and_registered(env):
    """默认 True＝保持现状（现状已在上线，默认关属行为回归）；键已进字典与展示目录。"""
    from app.application.flag_catalog import FLAG_CATALOG

    assert AGENT_FLAGS.get("phone_auto_notify_mention") is True
    assert "phone_auto_notify_mention" in FLAG_CATALOG
    assert svc._mention_enabled() is True
    assert _run(env) is True


def test_missing_flag_key_defaults_to_on(monkeypatch):
    """字典里没这个键时按「开」处理（不提供「忘了登记就静默停功能」的路径）。"""
    stripped = {k: v for k, v in AGENT_FLAGS.items() if k != "phone_auto_notify_mention"}
    monkeypatch.setattr("app.agent.loop.AGENT_FLAGS", stripped)
    assert svc._mention_enabled() is True


# ───────────────────────── 调用链：handle_auto_report 回报口径 ─────────────────────────
def test_report_blocked_by_gate_reports_not_triggered(env):
    """被闸拦下 → 接口回 triggered=False，且不回写 last_trigger_at（否则白占 30 分钟节流）。"""
    env.monkeypatch.setattr(svc, "async_session_factory", _state_factory(_report_state(["ffffffff"])))
    env.monkeypatch.setattr(svc, "_persist_notification_snapshots", _const(None))
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(True))
    out = asyncio.run(svc.handle_auto_report(UID, ITEMS))
    assert out["triggered"] is False and out["new_count"] == 1
    assert env.sent == []
    _blocked(env, "dnd")


def test_report_when_gates_pass_triggers(env):
    env.monkeypatch.setattr(svc, "async_session_factory", _state_factory(_report_state(["ffffffff"])))
    env.monkeypatch.setattr(svc, "_persist_notification_snapshots", _const(None))
    out = asyncio.run(svc.handle_auto_report(UID, ITEMS))
    assert out["triggered"] is True
    assert len(env.sent) == 1 and env.sent[0][4] == "notification_mention"


def test_report_first_upload_still_baseline_only(env):
    """首次上报（无基线）依旧只建基线：不发、不判闸（本次改动没碰这条语义）。"""
    env.monkeypatch.setattr(svc, "async_session_factory", _state_factory(_report_state([])))
    out = asyncio.run(svc.handle_auto_report(UID, ITEMS))
    assert out["triggered"] is False
    assert env.sent == [] and env.rejected == []
