# -*- coding: utf-8 -*-
"""断点 #7 方案 A（2026-09-29）：life_share 直发路径补齐内核闸 + rejected 留痕。

口径来源：output/AMBRACE_断点7_life_share旁路核实_20260929.md §4/§6。
全部用例走 monkeypatch（假 session + 假 arbiter 闸），**不连生产库、不建表**。
每道闸一条「会被拦」；另有「不该拦」用例守住「只减不发 = 正常仍能发」。
"""
import asyncio
from types import SimpleNamespace
from datetime import timedelta

import pytest

from app.scheduling import arbiter
from app.scheduling import life_share
from app.utils.timeutil import now_naive_utc

CID, UID = 101, 1
_PAYLOAD = {"data": {"user_id": UID, "character_id": CID, "activity_type": "create",
                     "summary": "画了一张水彩风景", "importance": 60}}


# ───────────────────────── 假 DB（零 SQL、零文件） ─────────────────────────
class _Result:
    def scalar(self):
        return 0

    def scalar_one_or_none(self):
        return None

    def all(self):
        return []


class _Session:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _Result()

    def add(self, obj, *_a, **_k):
        self.added.append(obj)

    async def commit(self):
        return None


def _factory():
    return _Session()


def _const(value):
    """把闸桩成「恒返回 value」的协程函数。"""
    async def _f(*_a, **_k):
        return value
    return _f


def _boom(*_a, **_k):
    async def _f():
        raise RuntimeError("闸取数失败")
    return _f()


# ───────────────────────── 环境桩：闸默认全放行 ─────────────────────────
@pytest.fixture
def env(monkeypatch):
    from app.agent.loop import AGENT_FLAGS

    sent, rejected = [], []
    monkeypatch.setitem(AGENT_FLAGS, "life_share_enabled", True)
    monkeypatch.setattr(life_share, "async_session_factory", _factory)
    monkeypatch.setattr(life_share, "_quota_ok", _const(True))
    monkeypatch.setattr(life_share, "should_share", lambda *a, **k: (True, 0.3))
    monkeypatch.setattr(life_share, "_naturalness_flag", lambda: False)
    monkeypatch.setattr(life_share, "_generate_share", _const("我刚画完一张图，超好看的！"))

    # 原有三条 arbiter 门控：放行
    for name in ("is_dnd_now", "is_user_active", "unreplied_cooldown_active"):
        monkeypatch.setattr(f"app.scheduling.arbiter.{name}", _const(False))

    # 新增内核闸的复用来源：默认全部「未命中」
    async def _active_chars(*_a, **_k):
        return [{"character_id": CID, "user_id": UID, "max_daily_proactive": 5}]
    monkeypatch.setattr(arbiter, "get_active_characters", _active_chars)
    monkeypatch.setattr(arbiter, "inactive_char_skip", _const(False))
    monkeypatch.setattr(arbiter, "has_pending_timer", _const(False))
    monkeypatch.setattr(arbiter, "has_pending_storyline", _const(False))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr(arbiter, "get_last_proactive_time", _const(None))
    monkeypatch.setattr(arbiter, "get_daily_sent_count", _const(0))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(False))
    monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(0))

    # 留痕：捕获内核记录函数的入参（不写库）
    async def _cap_log(item, executed):
        rejected.append({"item": item, "executed": executed})
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _cap_log)

    async def _sid(*_a, **_k):
        return 7
    async def _send(session_id, character_id, user_id, content, message_type="state_trigger"):
        sent.append((session_id, character_id, user_id, content, message_type))
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)

    return SimpleNamespace(sent=sent, rejected=rejected, monkeypatch=monkeypatch)


def _run():
    asyncio.run(life_share.on_activity_completed(dict(_PAYLOAD)))


def _blocked(env, gate):
    """断言：没发出去，且留痕是一条带闸名的 rejected。"""
    assert env.sent == [], f"闸 {gate} 命中却仍发送"
    assert env.rejected, "被拦必须写 rejected 留痕"
    last = env.rejected[-1]
    assert last["executed"] is False
    assert last["item"]["type"] == "life_share"
    assert last["item"].get("_gate") == gate


# ───────────────────────── ① 90 分钟最小间隔 ─────────────────────────
def test_min_interval_blocks(env):
    """距上一条主动消息 30 分钟 → 拦（生产 3 条样本正是这种违例）。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=30)))
    _run()
    _blocked(env, "min_interval")


def test_min_interval_boundary_allows(env):
    """「不该拦」：间隔 91 分钟 > 90 → 仍应发出。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=91)))
    _run()
    assert len(env.sent) == 1


# ───────────────────────── ② 每小时上限 ─────────────────────────
def test_hourly_cap_blocks(env):
    from app.domain.proactivity.decision import MAX_PER_HOUR
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(MAX_PER_HOUR))
    _run()
    _blocked(env, "hourly_cap")


def test_hourly_cap_below_allows(env):
    """「不该拦」：本小时 1 条（< 上限 2）→ 仍应发出。"""
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(1))
    _run()
    assert len(env.sent) == 1


# ───────────────────────── ③ 角色每日总上限 ─────────────────────────
def test_daily_cap_blocks(env):
    """内核口径当日 5 条已满（max_daily_proactive=5）→ 拦。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(5))
    _run()
    _blocked(env, "daily_cap")


def test_daily_cap_counts_own_life_share(env):
    """节律类 4 条 + life_share 当日已发 1 条 = 5 → 拦（补齐「本通道不占额度」的缺口）。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(4))
    env.monkeypatch.setattr(arbiter, "get_daily_sent_count", _const(1))
    _run()
    _blocked(env, "daily_cap")


def test_daily_cap_below_allows(env):
    """「不该拦」：4（节律）+ 0（本通道）< 5 → 仍应发出。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(4))
    _run()
    assert len(env.sent) == 1


# ───────────────────────── ④ 资格 enable_proactive ─────────────────────────
def test_not_eligible_blocks(env):
    """角色不在内核 active 名单（关掉主动交流 / 已停用）→ 拦。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    _run()
    _blocked(env, "not_eligible")


# ───────────────────────── ⑤ 夜间「说睡觉」静默 ─────────────────────────
def test_night_said_sleep_blocks(env):
    env.monkeypatch.setattr(arbiter, "has_user_said_sleep", _const(True))
    _run()
    _blocked(env, "night_said_sleep")


# ───────────────────────── ⑥ 久未互动停发 + 计时器/剧情线互斥 ─────────────────────────
def test_inactive_char_blocks(env):
    env.monkeypatch.setattr(arbiter, "inactive_char_skip", _const(True))
    _run()
    _blocked(env, "inactive_char")


def test_pending_timer_blocks(env):
    """AI 有未到期定时承诺（正在「洗澡/睡觉」）→ 不插话。"""
    env.monkeypatch.setattr(arbiter, "has_pending_timer", _const(True))
    _run()
    _blocked(env, "pending_timer")


def test_pending_storyline_blocks(env):
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    _run()
    _blocked(env, "pending_storyline")


# ───────────────────────── 异常口径：不放行 ─────────────────────────
def test_gate_error_blocks(env):
    """闸取数抛异常 → 判为拦截（宁可不发，照 sources/strategy.py:376-432）。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    _run()
    _blocked(env, "kernel_gate_error")


def test_kernel_gate_error_reason_directly(env):
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    assert asyncio.run(life_share._kernel_gate_reason(CID, UID)) == "kernel_gate_error"


def test_bad_identity_blocks(env):
    """缺 id → 不放行（不拿脏身份去查内核闸）。"""
    assert asyncio.run(life_share._kernel_gate_reason(0, UID)) == "bad_identity"
    assert asyncio.run(life_share._kernel_gate_reason(None, UID)) == "bad_identity"


# ───────────────────────── 全通过仍能发（不扩大拦截） ─────────────────────────
def test_all_gates_pass_still_sends(env):
    assert asyncio.run(life_share._kernel_gate_reason(CID, UID)) is None
    _run()
    assert len(env.sent) == 1
    assert env.sent[0][1] == CID and env.sent[0][4] == "life_share"
    assert env.rejected == []  # 没被拦就不该留 rejected


def test_probability_miss_is_silent(env):
    """概率没抽中不是「闸」：不发、也不留 rejected（防写放大）。"""
    env.monkeypatch.setattr(life_share, "should_share", lambda *a, **k: (False, 0.0))
    _run()
    assert env.sent == [] and env.rejected == []


# ───────────────────────── 留痕内容 ─────────────────────────
def test_rejected_uses_kernel_fields(env):
    """留痕字段照内核同款：trigger_type / priority=5 / trigger_reason 带闸名。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=5)))
    _run()
    assert len(env.rejected) == 1
    item = env.rejected[0]["item"]
    assert item["type"] == "life_share"
    assert item["priority"] == 5
    assert item["candidate"]["character_id"] == CID
    assert item["candidate"]["user_id"] == UID
    assert "[gate=min_interval]" in item["candidate"]["trigger_reason"]


def test_existing_gates_also_leave_rejected_trace(env):
    """原有三条门控（DND/用户活跃/未回复冷却）此前只打日志，现在同样落 rejected。"""
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(True))
    _run()
    _blocked(env, "dnd")
    env.monkeypatch.setattr(arbiter, "is_dnd_now", _const(False))
    env.monkeypatch.setattr(arbiter, "unreplied_cooldown_active", _const(True))
    _run()
    _blocked(env, "unreplied")


def test_rejected_log_failure_never_blocks_flow(env, monkeypatch):
    """留痕写失败（内核记录函数抛异常）→ 主链路照常，不因观测缺口而改发送行为。"""
    async def _fail(*_a, **_k):
        raise RuntimeError("留痕写不进库")
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _fail)
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    _run()  # 不应抛错（on_activity_completed 内部已吞，且 _log_rejected 自带兜底）
    assert env.sent == []
