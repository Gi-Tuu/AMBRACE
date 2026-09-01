# -*- coding: utf-8 -*-
"""M1-S1 测试：召回出口 flag + _diversify_by_type 类型多样性重排（纯函数）"""
from app.memory.service import _diversify_by_type


def _m(i, t):
    return {"id": i, "type": t, "content": f"m{i}"}


def test_diversify_caps_single_type():
    """单一类型霸榜时每类先取 2 条一轮，其他类型获得出场机会"""
    ranked = [_m(1, "event"), _m(2, "event"), _m(3, "event"), _m(4, "preference"), _m(5, "event")]
    out = _diversify_by_type(ranked, 3)
    types = [m["type"] for m in out]
    assert types == ["event", "event", "preference"], types


def test_diversify_preserves_order_within_type():
    """cap=2：第一轮每类最多 2 条（event1/event3、pref2、insight4），第 5 条补齐"""
    ranked = [_m(1, "event"), _m(2, "preference"), _m(3, "event"), _m(4, "insight"), _m(5, "event")]
    out = _diversify_by_type(ranked, 4)
    assert [m["id"] for m in out] == [1, 2, 3, 4]


def test_diversify_same_count_as_slice():
    """返回条数恒等于 min(len, topk)——与 [:topk] 恒等条数（注入配额不变）"""
    ranked = [_m(i, "event") for i in range(10)]
    for topk in (3, 5, 8):
        assert len(_diversify_by_type(ranked, topk)) == topk
    short = [_m(1, "event"), _m(2, "insight")]
    assert len(_diversify_by_type(short, 5)) == 2


def test_diversify_noop_when_types_diverse():
    ranked = [_m(1, "event"), _m(2, "preference"), _m(3, "insight")]
    out = _diversify_by_type(ranked, 3)
    assert [m["id"] for m in out] == [1, 2, 3]


def test_diversify_edge_cases():
    assert _diversify_by_type([], 5) == []
    assert _diversify_by_type([_m(1, "event")], 0) == []
    assert _diversify_by_type([_m(1, "event")], 3)[0]["id"] == 1


def test_recall_flags_registered():
    """flag 必须在 AGENT_FLAGS 注册（runtime flag 热更新只认已注册键）"""
    from app.agent.loop import AGENT_FLAGS
    assert "recall_top5" in AGENT_FLAGS and AGENT_FLAGS["recall_top5"] is True
    assert "recall_diversify" in AGENT_FLAGS and AGENT_FLAGS["recall_diversify"] is True
