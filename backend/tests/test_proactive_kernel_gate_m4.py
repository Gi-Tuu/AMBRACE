# -*- coding: utf-8 -*-
"""M4 合同：主动消息直发通道内核闸单点化（2026-11-04）。

覆盖：九项检查逐项命中、全清放行、取数异常 fail-closed、state_trigger 的
「默认历史口径 vs flag 全量口径」差异、两个通道确实委托同一实现、以及防漂移棘轮
（arbiter 候选阶段 pre_gates 覆盖的检查名必须与本模块登记的清单一致）。
"""
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _patch_arbiter(monkeypatch, **hits):
    """把 arbiter 的内核取数函数替换成「指定命中的桩」，未指定的默认放行。"""
    from app.scheduling import arbiter
    from app.scheduling import triggers as triggers_mod

    active = [{"character_id": 1, "max_daily_proactive": 5}]
    monkeypatch.setattr(arbiter, "get_active_characters", _ret(active))
    monkeypatch.setattr(arbiter, "inactive_char_skip", _ret(bool(hits.get("inactive_char"))))
    monkeypatch.setattr(arbiter, "has_pending_timer", _ret(bool(hits.get("pending_timer"))))
    monkeypatch.setattr(arbiter, "has_pending_storyline", _ret(bool(hits.get("pending_storyline"))))
    monkeypatch.setattr(arbiter, "get_hourly_active_count", _ret(99 if hits.get("hourly_cap") else 0))
    monkeypatch.setattr(arbiter, "get_last_proactive_time", _ret(hits.get("last")))
    monkeypatch.setattr(arbiter, "get_daily_sent_count", _ret(0))
    monkeypatch.setattr(arbiter, "has_user_said_sleep", _ret(bool(hits.get("night_said_sleep"))))
    monkeypatch.setattr(triggers_mod, "get_daily_count", _ret(99 if hits.get("daily_cap") else 0))


def _ret(value):
    async def fake(*args, **kwargs):
        if isinstance(value, Exception):
            raise value
        return value
    return fake


def test_each_check_hits_and_all_clear(monkeypatch):
    from app.scheduling.proactive_kernel import FULL, direct_send_gate_reason

    cases = [
        ("not_eligible", {"active_missing": True}),
        ("inactive_char", {"inactive_char": True}),
        ("pending_timer", {"pending_timer": True}),
        ("pending_storyline", {"pending_storyline": True}),
        ("hourly_cap", {"hourly_cap": True}),
        ("min_interval", {"last": "just-now"}),
        ("daily_cap", {"daily_cap": True}),
        ("night_said_sleep", {"night_said_sleep": True}),
    ]
    from datetime import datetime

    for expected, hit in cases:
        _patch_arbiter(monkeypatch, **{k: v for k, v in hit.items() if k != "active_missing"})
        if hit.get("active_missing"):
            from app.scheduling import arbiter
            monkeypatch.setattr(arbiter, "get_active_characters", _ret([]))
        if "last" in hit:
            from app.scheduling import arbiter
            from app.utils.timeutil import now_naive_utc
            monkeypatch.setattr(arbiter, "get_last_proactive_time",
                                _ret(datetime.now().replace(microsecond=0)))
        reason = asyncio.run(direct_send_gate_reason(1, 1, channel="t", checks=FULL, trigger_type="x"))
        assert reason == expected, (expected, reason)

    _patch_arbiter(monkeypatch)
    assert asyncio.run(direct_send_gate_reason(1, 1, channel="t", checks=FULL, trigger_type="x")) is None


def test_fail_closed_on_error(monkeypatch):
    from app.scheduling import arbiter
    from app.scheduling.proactive_kernel import direct_send_gate_reason

    _patch_arbiter(monkeypatch)
    monkeypatch.setattr(arbiter, "has_pending_timer", _ret(RuntimeError("db down")))
    assert asyncio.run(direct_send_gate_reason(1, 1, channel="t")) == "kernel_gate_error"


def test_state_trigger_profile_defaults_to_legacy_and_flag_upgrades(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.scheduling import state_triggers

    # 默认（flag 关）：三项新检查不生效，历史 6 项照旧
    _patch_arbiter(monkeypatch, inactive_char=True, hourly_cap=True, night_said_sleep=True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_kernel_full_gate", False)
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) is None
    _patch_arbiter(monkeypatch, pending_timer=True)
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) == "pending_timer"

    # flag 开：最严口径生效
    monkeypatch.setitem(AGENT_FLAGS, "proactive_kernel_full_gate", True)
    _patch_arbiter(monkeypatch, inactive_char=True)
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) == "inactive_char"
    _patch_arbiter(monkeypatch, hourly_cap=True)
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) == "hourly_cap"
    _patch_arbiter(monkeypatch, night_said_sleep=True)
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) == "night_said_sleep"


def test_both_channels_delegate_to_single_implementation(monkeypatch):
    from app.scheduling import life_share, proactive_kernel, state_triggers

    async def stub(character_id, user_id, *, channel, checks, trigger_type=None):
        return f"delegated:{channel}"

    monkeypatch.setattr(proactive_kernel, "direct_send_gate_reason", stub)
    assert asyncio.run(life_share._kernel_gate_reason(1, 1)) == "delegated:life_share"
    assert asyncio.run(state_triggers._kernel_gate_reason(1, 1)) == "delegated:state_trigger"


def test_arbiter_pre_gates_checklist_ratchet():
    """防漂移棘轮：arbiter 候选阶段的五道闸必须在源码里真实存在，且与登记清单一致。"""
    from app.scheduling.proactive_kernel import ARBITER_PRE_GATE_CHECKS

    src = (ROOT / "backend" / "app" / "scheduling" / "executors" / "guards.py").read_text(
        encoding="utf-8")
    mapping = {
        "dnd": "is_dnd_now",
        "user_said_sleep": "has_user_said_sleep",
        "user_active": "is_user_active",
        "hourly_cap": "hourly_active",
        "pacing": "pacing_gate",
    }
    assert tuple(mapping) == ARBITER_PRE_GATE_CHECKS, "登记清单与映射表不一致"
    for name, attr in mapping.items():
        assert attr in src, f"arbiter 阶段少了 {name}（{attr}）——闸被删或改名后这里先红"
