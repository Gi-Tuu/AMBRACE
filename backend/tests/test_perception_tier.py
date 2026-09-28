# -*- coding: utf-8 -*-
"""批 0-2 / M0 · perception_tier 纯函数判据单测（零 IO，不连库）。

覆盖：隔离谓句四种组合与 None 安全、快照标签残留正反例、长词重合的阈值边界/空输入/
脏输入不抛/不 mutate 入参，以及「与注入侧同口径」的常量对齐断言（不复制魔法数）。
"""
from __future__ import annotations

import copy

from app.memory.perception_tier import (
    PERCEPTION_SOURCE,
    SNAPSHOT_MAX_ROWS,
    SNAPSHOT_WINDOW_MINUTES,
    has_snapshot_tag,
    is_quarantined,
    snapshot_overlap,
)

SNAP = "开会迟到五分钟"          # 4 个 4 元组：开会迟到/会迟到五/迟到五分/到五分钟
MEM_HIT_3 = "开会迟到五分也不行"   # 与 SNAP 连续重合 6 字 ⇒ 恰好 3 段
MEM_HIT = "别开会迟到五分钟前"     # 连续重合 7 字 ⇒ 4 段
MEM_MISS_2 = "开会迟到五个人不行"  # 连续重合 5 字 ⇒ 2 段（差一段不命中）
MEM_MISS_1 = "开会真迟到"          # 只撞 1 段


def test_quarantined_perception_inferred():
    assert is_quarantined("perception", "INFERRED") is True


def test_quarantined_perception_missing_status():
    """感知条未写状态（None / 空串）也隔离：默认不是事实。"""
    assert is_quarantined("perception", None) is True
    assert is_quarantined("perception", "") is True


def test_not_quarantined_perception_fact():
    """用户认可（升 FACT）后自动脱隔。"""
    assert is_quarantined("perception", "FACT") is False


def test_not_quarantined_chat_any_status():
    for status in ("FACT", "INFERRED", "PLANNED", "UNVERIFIED", "FICTIONAL", None, ""):
        assert is_quarantined("chat", status) is False


def test_quarantined_none_and_dirty_inputs_safe():
    assert is_quarantined(None, None) is False
    assert is_quarantined("perception", 123) is True   # 脏状态值 ≠ FACT ⇒ 仍隔离，但不抛
    assert is_quarantined(123, "FACT") is False


def test_quarantined_normalizes_case_and_space():
    assert is_quarantined(" Perception ", "inferred") is True
    assert is_quarantined("perception", " fact ") is False


def test_has_snapshot_tag_positive():
    assert has_snapshot_tag("[屏幕 3分钟前] 聊天列表里有新消息") is True
    assert has_snapshot_tag("[12分钟前] 剪贴板内容") is True
    assert has_snapshot_tag("前半句 [通知 0分钟前] 后半句") is True


def test_has_snapshot_tag_negative():
    assert has_snapshot_tag("3分钟前我们聊过这件事") is False      # 无方括号
    assert has_snapshot_tag("[昨天] 开了个会") is False            # 无「N分钟前」
    assert has_snapshot_tag("[屏幕] 无时间标签") is False
    assert has_snapshot_tag("") is False
    assert has_snapshot_tag("开会迟到五分钟") is False


def test_has_snapshot_tag_dirty_inputs():
    for bad in (None, 123, 3.5, ["[3 分钟前]"], {"a": 1}, object()):
        assert has_snapshot_tag(bad) is False


def test_overlap_hit_verbatim_like():
    assert snapshot_overlap(MEM_HIT_3, [SNAP]) is True
    assert snapshot_overlap(MEM_HIT, [SNAP]) is True


def test_overlap_boundary_exactly_three():
    """阈值边界：恰好重合 3 段命中；差一段（2 段）不命中。"""
    assert snapshot_overlap(MEM_HIT_3, [SNAP]) is True
    assert snapshot_overlap(MEM_MISS_2, [SNAP]) is False
    assert snapshot_overlap(MEM_MISS_1, [SNAP]) is False


def test_overlap_empty_inputs():
    assert snapshot_overlap("", [SNAP]) is False
    assert snapshot_overlap(MEM_HIT, []) is False
    assert snapshot_overlap(MEM_HIT, ()) is False
    assert snapshot_overlap("开会迟到五分钟", ["   "]) is False


def test_overlap_none_safe():
    assert snapshot_overlap(None, [SNAP]) is False
    assert snapshot_overlap(MEM_HIT, None) is False
    assert snapshot_overlap(None, None) is False


def test_overlap_dirty_snapshot_items_do_not_raise():
    assert snapshot_overlap(MEM_HIT, [None, 123, 3.5, {"a": 1}, [], object(), "开会迟到五分钟"]) is True
    assert snapshot_overlap(MEM_HIT, [None, 123, object()]) is False


def test_overlap_rejects_non_container_inputs():
    """传成字符串/字节串：不按字符迭代，直接判不命中（防误标）。"""
    assert snapshot_overlap(MEM_HIT, SNAP) is False
    assert snapshot_overlap(MEM_HIT, SNAP.encode("utf-8")) is False
    assert snapshot_overlap(MEM_HIT, 12345) is False


def test_overlap_does_not_accumulate_across_snapshots():
    """两条快照各撞 2 段（合计 4 段）不命中——重合数只在**同一条**快照上累加。"""
    text = "开会迟到五个人不行，午饭吃牛肉了。"
    assert snapshot_overlap(text, [SNAP, "午饭吃牛肉面"]) is False
    # 换成单一条快照本身重合够 3 段 ⇒ 命中（证明不是「整批语料都不算」的实现错误）
    assert snapshot_overlap(text, ["开会迟到五个人不行也"]) is True


def test_overlap_does_not_mutate_inputs():
    items = [SNAP, "午饭吃牛肉面", MEM_HIT]
    text = MEM_MISS_2
    items_before, text_before = copy.deepcopy(items), text
    snapshot_overlap(text, items, min_len=5, min_overlap=2)
    snapshot_overlap(text, items)
    assert items == items_before
    assert text == text_before


def test_overlap_threshold_overrides_and_dirty_thresholds():
    assert snapshot_overlap(MEM_MISS_2, [SNAP], min_len=2, min_overlap=1) is True
    assert snapshot_overlap(MEM_HIT, [SNAP], min_len=20, min_overlap=1) is False
    # 脏阈值退回默认（不抛、也不放宽到低于默认）
    assert snapshot_overlap(MEM_MISS_2, [SNAP], min_len=None, min_overlap="abc") is False
    assert snapshot_overlap(MEM_HIT_3, [SNAP], min_len=-1, min_overlap=0) is True


def test_constants_match_injection_side():
    """窗口/条数必须与注入侧同源（唯一事实源 app/device/port.py:37-38）。"""
    from app.device.port import PERCEPTION_LIMIT, PERCEPTION_MAX_AGE_MINUTES

    assert PERCEPTION_SOURCE == "perception"
    assert SNAPSHOT_WINDOW_MINUTES == PERCEPTION_MAX_AGE_MINUTES
    assert SNAPSHOT_MAX_ROWS == PERCEPTION_LIMIT


def test_source_registered_for_display():
    from app.memory.sources import SOURCE_META, memory_source_meta

    assert "perception" in SOURCE_META
    assert memory_source_meta("perception") == {"label": "手机感知", "icon": "chat"}
    # 未登记来源仍回落「未知」（本批只加 perception 一项）
    assert memory_source_meta("nonexistent_source")["label"] == "未知"
