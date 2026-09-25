"""C12a：「现状锚 + 时空纪律」共享前置的等价性回归（2026-09-25）。

life_regression / pet_care 的 A7 实现改为调用 app/scheduling/state_guard.py 的薄封装，
本文件钉住「收敛前后逐字等价」：纪律文案单一来源、护栏段顺序、时间行口径、锚的 fail-open
语义。只测纯函数与降级路径，不 stub 数据库 / LLM / 网络。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from app.scheduling import life_regression, pet_care, state_guard

# 有值 / 空串 / 全空白 / None 四种锚形态（None 也必须与收敛前一致：只出纪律段）
ANCHORS = ("人在湛江，腰伤未愈", "", "   \n  ", None)


def test_discipline_text_single_source():
    assert life_regression.STATE_GUARD_DISCIPLINE == state_guard.STATE_GUARD_DISCIPLINE
    assert pet_care.STATE_GUARD_DISCIPLINE == state_guard.STATE_GUARD_DISCIPLINE
    assert "以现状为准" in state_guard.STATE_GUARD_DISCIPLINE


def test_life_regression_segments_match_shared():
    for anchor in ANCHORS:
        assert life_regression._state_guard_segments(anchor) == state_guard.guard_segments(anchor)


def test_pet_care_block_matches_shared():
    for anchor in ANCHORS:
        assert pet_care._state_guard_block(anchor) == state_guard.guard_block(anchor)


def test_guard_block_order_and_empty_anchor():
    blk = state_guard.guard_block("人在湛江")
    assert blk.index("【当前现状】") < blk.index("【时空纪律】")
    assert blk.endswith("\n")
    assert state_guard.guard_block("") == state_guard.STATE_GUARD_DISCIPLINE + "\n"
    assert state_guard.guard_segments(None) == [state_guard.STATE_GUARD_DISCIPLINE]


def test_cn_now_line_fixed_clock(monkeypatch):
    """时间行逐字口径：pet_care 侧=共享实现（无午别），life_regression 侧仍带午别（未合并）。"""
    import app.utils.timeutil as timeutil
    monkeypatch.setattr(timeutil, "app_local_now", lambda: datetime(2026, 9, 25, 15, 5))
    expected = "现在是北京时间 2026年9月25日 星期五 15:05。"
    assert state_guard.cn_now_line() == expected
    assert pet_care._cn_now_line() == expected
    assert "北京时间" in expected
    assert life_regression._cn_now_prefix() == "现在是北京时间 2026年9月25日 星期五 下午 15:05。"


def test_current_state_anchor_forwards_a7_call_shape(monkeypatch):
    import app.memory.current_state as cs
    seen: dict = {}

    async def _fake(**kw):
        seen.update(kw)
        return "TA 当前已知现状：位置：湛江"

    monkeypatch.setattr(cs, "current_user_state_anchor", _fake)
    assert asyncio.run(state_guard.current_state_anchor(13, 3)) == "TA 当前已知现状：位置：湛江"
    assert seen == {"character_id": 13, "user_id": 3, "include_profile_location": True,
                    "max_chars": 200}
    seen.clear()
    asyncio.run(state_guard.current_state_anchor(13, 3, max_chars=50))
    assert seen["max_chars"] == 50


@pytest.mark.parametrize("wrapper", [
    lambda: state_guard.current_state_anchor(13, 3),
    lambda: life_regression._current_anchor(13, 3),
    lambda: pet_care._state_anchor(13, 3),
])
def test_anchor_fail_open_when_underlying_raises(wrapper, monkeypatch):
    import app.memory.current_state as cs

    def _boom(*_a, **_k):
        raise RuntimeError("anchor down")

    monkeypatch.setattr(cs, "current_user_state_anchor", _boom)
    assert asyncio.run(wrapper()) == ""


@pytest.mark.parametrize("wrapper", [
    lambda: state_guard.current_state_anchor(13, 3),
    lambda: life_regression._current_anchor(13, 3),
    lambda: pet_care._state_anchor(13, 3),
])
def test_anchor_normalizes_none_to_empty_string(wrapper, monkeypatch):
    import app.memory.current_state as cs

    async def _none(**_k):
        return None

    monkeypatch.setattr(cs, "current_user_state_anchor", _none)
    assert asyncio.run(wrapper()) == ""
