# -*- coding: utf-8 -*-
"""陪伴主动线（2026-08-30）：ready 承诺结果跳过 — ready_result_seen 纯函数测试"""
from app.scheduling.promise_parser import ready_result_seen


# ── hint 分类命中 → True ──


def test_meeting_hint_hits():
    hint = "去开会了"
    for text in ("开完了", "刚开好", "散会了", "忙完啦", "开完会了，好累"):
        assert ready_result_seen([text], hint) is True, text


def test_meal_hint_hits():
    hint = "去吃饭"
    for text in ("吃完了", "吃好啦", "刚吃过", "吃饱了"):
        assert ready_result_seen([text], hint) is True, text


def test_shower_hint_hits():
    for hint in ("去洗澡", "洗个澡", "去洗个澡", "洗澡去了"):
        for text in ("洗完了", "洗好啦", "洗过了", "洗好了"):
            assert ready_result_seen([text], hint) is True, (hint, text)


def test_try_hint_hits():
    hint = "等下试"
    for text in ("试完了", "试好了", "试过了", "行了", "好了", "搞定了"):
        assert ready_result_seen([text], hint) is True, text


def test_fallback_hint_hits():
    for text in ("好了", "搞定了", "结束了", "完成了", "回来了"):
        assert ready_result_seen([text]) is True, text


def test_multi_message_any_hit():
    """多条消息中任一条命中即 True（按时间顺序传入）"""
    assert ready_result_seen(["在忙", "还没呢", "吃完了"], "去吃饭") is True


# ── 未命中 → False ──


def test_no_hit_returns_false():
    assert ready_result_seen(["我去看会电视"], "去吃饭") is False


def test_cross_category_no_false_positive():
    """吃饭 hint 不被开会类结果词命中（分类关键词互不串扰）"""
    assert ready_result_seen(["开完了"], "去吃饭") is False
    assert ready_result_seen(["吃完了"], "去开会") is False


def test_empty_list_returns_false():
    assert ready_result_seen([], "去吃饭") is False


def test_empty_hint_uses_fallback():
    """hint 为空走兜底关键词组"""
    assert ready_result_seen(["好了"]) is True
    assert ready_result_seen(["回来了"]) is True
    assert ready_result_seen(["吃完了"]) is False  # 兜底组无"吃完"


def test_busy_hint_goes_meeting():
    """hint 含"忙"（无"开会"）也走 meeting 关键词组"""
    assert ready_result_seen(["忙完了"], "去忙了") is True
