"""A7：主动消息「时空护栏」补全的纯函数与降级路径回归（2026-09-24）。

背景：life_regression 与 pet_care 的主动通道此前没有现状锚、没有回忆纪律，角色会把
用户的旧地点当成现在提（实测：用户 09-09 09:02 已说回湛江，09-09 10:43 仍问「你那边
长沙最近还闷热吗」；09-12 04:27「咋了轩，今天长沙又热得离谱？」）。

本文件只测纯函数与 fail-open 降级路径，不 stub 数据库 / LLM / 网络。
"""
from __future__ import annotations

import asyncio

from app.scheduling import life_regression
from app.scheduling import pet_care


def test_life_regression_segments_anchor_then_discipline():
    segs = life_regression._state_guard_segments("腰伤未愈，人在湛江")
    assert len(segs) == 2
    assert segs[0].startswith("【当前现状】") and "湛江" in segs[0]
    assert segs[1].startswith("【时空纪律】")


def test_life_regression_segments_without_anchor_keeps_discipline():
    segs = life_regression._state_guard_segments("")
    assert len(segs) == 1
    assert segs[0].startswith("【时空纪律】")


def test_life_regression_segments_whitespace_anchor_is_empty():
    segs = life_regression._state_guard_segments("   \n  ")
    assert len(segs) == 1 and segs[0].startswith("【时空纪律】")


def test_pet_care_block_current_state_before_discipline():
    blk = pet_care._state_guard_block("人在湛江")
    assert blk.index("【当前现状】") < blk.index("【时空纪律】")
    assert blk.endswith("\n")


def test_pet_care_block_without_anchor_only_discipline():
    # 注意：纪律文案本身会提到「【当前现状】」，所以只能断言「不是以现状段开头」，
    # 不能断言整个块不含这四个字。
    blk = pet_care._state_guard_block("")
    assert blk.startswith("【时空纪律】")
    assert not blk.startswith("【当前现状】")


def test_discipline_text_identical_across_channels():
    assert pet_care.STATE_GUARD_DISCIPLINE == life_regression.STATE_GUARD_DISCIPLINE
    assert "以现状为准" in life_regression.STATE_GUARD_DISCIPLINE


def test_cn_now_line_mentions_beijing_time():
    line = pet_care._cn_now_line()
    assert "北京时间" in line and "星期" in line


def test_anchors_fail_open_when_current_state_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("anchor down")

    import app.memory.current_state as cs
    monkeypatch.setattr(cs, "current_user_state_anchor", boom)
    assert asyncio.run(life_regression._current_anchor(13, 3)) == ""
    assert asyncio.run(pet_care._state_anchor(13, 3)) == ""
