# -*- coding: utf-8 -*-
"""断点 #8′ E10（2026-09-29）：state_triggers「聊天后即时」私聊直发补齐内核闸 + rejected 留痕。

口径来源：output/AMBRACE_断点8prime_其他直发点核实_20260929.md §2 第 1 行、§3「E10」。
被测接线：app/scheduling/state_triggers.py:660-666（发送点 :668，判定函数 :507）。
写法参照同批次的 tests/test_life_share_gates.py。
全部用例走 monkeypatch（假 session + 假 arbiter 闸），**不连生产库、不建表、不打 LLM**。
每道新闸一条「会被拦」；另有「不该拦」用例守住「只减不发 = 正常仍能发」。
"""
import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.scheduling import arbiter
from app.scheduling import state_triggers
from app.utils.timeutil import now_naive_utc

CID, UID = 11, 3
# fatigue_high：moment=False ⇒ 一定走私聊消息分支（朋友圈分支不归本单管）
RULE_KEY = "fatigue_high"
_STATE_LINES = "疲惫=85；心情=40"


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

    async def get(self, *_a, **_k):
        return None

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
    sent, rejected = [], []

    monkeypatch.setattr(state_triggers, "async_session_factory", _factory)
    monkeypatch.setattr(state_triggers, "_post_trigger_notes", _const(None))
    monkeypatch.setattr(state_triggers, "_after_trigger_create_preoccupation", _const(None))

    async def _chat(**_kw):
        return "我有点累了，想先歇一会儿。"
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _chat)
    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _const(""))
    monkeypatch.setattr("app.agent.persona.build_active_channel_persona", _const(""))
    monkeypatch.setattr("app.scheduling.triggers.get_last_messages", _const(""))

    async def _sid(*_a, **_k):
        return 99
    async def _send(session_id, character_id, user_id, content, message_type="state_trigger"):
        sent.append((session_id, character_id, user_id, content, message_type))
    monkeypatch.setattr("app.application.chat_service.get_latest_session_id", _sid)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _send)

    # 内核闸复用来源：默认全部「未命中」
    async def _active_chars(*_a, **_k):
        return [{"character_id": CID, "user_id": UID, "max_daily_proactive": 5}]
    monkeypatch.setattr(arbiter, "get_active_characters", _active_chars)
    monkeypatch.setattr(arbiter, "has_pending_timer", _const(False))
    monkeypatch.setattr(arbiter, "has_pending_storyline", _const(False))
    monkeypatch.setattr(arbiter, "get_last_proactive_time", _const(None))
    monkeypatch.setattr(arbiter, "get_daily_sent_count", _const(0))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(0))
    monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(0))

    # 留痕：捕获内核记录函数的入参（不写库）
    async def _cap_log(item, executed):
        rejected.append({"item": item, "executed": executed})
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _cap_log)

    return SimpleNamespace(sent=sent, rejected=rejected, monkeypatch=monkeypatch)


def _run(cid=CID, uid=UID, rule_key=RULE_KEY):
    rule = state_triggers._RULE_BY_KEY[rule_key]
    return asyncio.run(state_triggers._execute_rule_behavior(cid, uid, rule, _STATE_LINES))


def _blocked(env, gate):
    """断言：没发出去，且留痕是一条带闸名的 rejected。"""
    assert env.sent == [], f"闸 {gate} 命中却仍发送"
    assert env.rejected, "被拦必须写 rejected 留痕"
    last = env.rejected[-1]
    assert last["executed"] is False
    assert last["item"]["type"] == "state_trigger"
    assert last["item"].get("_gate") == gate


# ───────────────────────── ① 90 分钟最小间隔 ─────────────────────────
def test_min_interval_blocks(env):
    """距上一条主动消息 30 分钟 → 拦（原直发分支只判 DND + 每小时，这种违例能发出去）。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=30)))
    assert _run() is False
    _blocked(env, "min_interval")


def test_min_interval_boundary_allows(env):
    """「不该拦」：间隔 91 分钟 > 90 → 正常仍能发。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=91)))
    assert _run() is True
    assert len(env.sent) == 1


def test_first_ever_message_allows(env):
    """「不该拦」：从未发过主动消息（last=None）→ 间隔闸不该误伤。"""
    assert _run() is True
    assert len(env.sent) == 1


# ───────────────────────── ② 角色每日总上限 ─────────────────────────
def test_daily_cap_blocks(env):
    """内核口径当日 5 条已满（max_daily_proactive=5）→ 拦。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(5))
    assert _run() is False
    _blocked(env, "daily_cap")


def test_daily_cap_counts_own_state_trigger(env):
    """节律类 4 条 + 本通道当日已发 1 条 = 5 → 拦（补「state_trigger 不占额度」的缺口）。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(4))
    env.monkeypatch.setattr(arbiter, "get_daily_sent_count", _const(1))
    assert _run() is False
    _blocked(env, "daily_cap")


def test_daily_cap_below_allows(env):
    """「不该拦」：4（节律）+ 0（本通道）< 5 → 正常仍能发。"""
    env.monkeypatch.setattr("app.scheduling.triggers.get_daily_count", _const(4))
    assert _run() is True
    assert len(env.sent) == 1


# ───────────────────────── ③ 资格 enable_proactive（历史事故点） ─────────────────────────
def test_not_eligible_blocks(env):
    """角色不在内核 active 名单（is_active=0 / 无资格）→ 拦。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _const([]))
    assert _run() is False
    _blocked(env, "not_eligible")


def test_enable_proactive_zero_always_blocks(env):
    """enable_proactive=0 必拦：内核 get_active_characters 会滤掉这类角色（triggers.py:57-58），
    「聊天后即时」分支过去不查资格 ⇒ char2/char3 各被直发过 12/1 条。现在必须拦且留痕。"""
    async def _others_only(*_a, **_k):
        # 名单里只有别的角色（= 本角色 enable_proactive=0 被滤掉）
        return [{"character_id": CID + 50, "user_id": UID, "max_daily_proactive": 5}]
    env.monkeypatch.setattr(arbiter, "get_active_characters", _others_only)
    assert _run() is False
    _blocked(env, "not_eligible")
    assert "[gate=not_eligible]" in env.rejected[-1]["item"]["candidate"]["trigger_reason"]


# ───────────────────────── ④ pending 互斥（计时器 / 未发完剧情切片） ─────────────────────────
def test_pending_timer_blocks(env):
    """AI 有未到期定时承诺（正在「洗澡/睡觉」）→ 不插话。"""
    env.monkeypatch.setattr(arbiter, "has_pending_timer", _const(True))
    assert _run() is False
    _blocked(env, "pending_timer")


def test_pending_storyline_blocks(env):
    """还有未发送完的剧情切片 → 不与剧情抢同一次打扰。"""
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    assert _run() is False
    _blocked(env, "pending_storyline")


# ───────────────────────── 既有闸不受影响 ─────────────────────────
def test_hourly_cap_still_blocks(env):
    """每小时上限（改动前就有）仍生效：命中即不发。"""
    from app.domain.proactivity.decision import MAX_PER_HOUR
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(MAX_PER_HOUR))
    assert _run() is False
    assert env.sent == []


def test_hourly_below_allows(env):
    """「不该拦」：本小时 1 条（< 上限 2）→ 正常仍能发。"""
    env.monkeypatch.setattr(arbiter, "get_hourly_active_count", _const(1))
    assert _run() is True
    assert len(env.sent) == 1


# ───────────────────────── 异常口径：不放行 ─────────────────────────
def test_gate_error_blocks(env):
    """闸取数抛异常 → 判为拦截（宁可不发，照 life_share 断点 #7 同口径）。"""
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    assert _run() is False
    _blocked(env, "kernel_gate_error")


def test_kernel_gate_error_reason_directly(env):
    env.monkeypatch.setattr(arbiter, "get_active_characters", _boom)
    assert asyncio.run(state_triggers._kernel_gate_reason(CID, UID)) == "kernel_gate_error"


def test_bad_identity_blocks(env):
    """缺 id → 不放行（不拿脏身份去查内核闸）。"""
    assert asyncio.run(state_triggers._kernel_gate_reason(0, UID)) == "bad_identity"
    assert asyncio.run(state_triggers._kernel_gate_reason(None, UID)) == "bad_identity"
    assert asyncio.run(state_triggers._kernel_gate_reason(CID, 0)) == "bad_identity"


# ───────────────────────── 全通过仍能发（不扩大拦截） ─────────────────────────
def test_all_gates_pass_returns_none_and_sends(env):
    assert asyncio.run(state_triggers._kernel_gate_reason(CID, UID)) is None
    assert _run() is True
    assert len(env.sent) == 1
    assert env.sent[0][1] == CID and env.sent[0][4] == "state_trigger"
    assert env.rejected == []  # 没被拦就不该留 rejected


# ───────────────────────── 留痕内容 ─────────────────────────
def test_rejected_uses_kernel_fields(env):
    """留痕字段照内核同款：trigger_type / priority=2（与 sources/state_trigger.py 同值）
    / candidate 带 character_id+user_id / trigger_reason 带 [gate=...]。"""
    env.monkeypatch.setattr(arbiter, "get_last_proactive_time",
                            _const(now_naive_utc() - timedelta(minutes=5)))
    _run()
    assert len(env.rejected) == 1
    item = env.rejected[0]["item"]
    assert item["type"] == "state_trigger"
    assert item["priority"] == 2
    assert item["candidate"]["character_id"] == CID
    assert item["candidate"]["user_id"] == UID
    assert item["candidate"]["trigger_reason"].startswith(f"{RULE_KEY}:[gate=")
    assert "[gate=min_interval]" in item["candidate"]["trigger_reason"]


def test_rejected_log_failure_never_blocks_flow(env):
    """留痕写失败（内核记录函数抛异常）→ 主链路不因观测缺口而崩（发送行为仍按闸结论）。"""
    async def _fail(*_a, **_k):
        raise RuntimeError("留痕写不进库")
    env.monkeypatch.setattr(arbiter, "log_trigger_candidate", _fail)
    env.monkeypatch.setattr(arbiter, "has_pending_storyline", _const(True))
    assert _run() is False
    assert env.sent == []
