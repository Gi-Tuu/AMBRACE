# -*- coding: utf-8 -*-
"""定时承诺解析器测试（promise_parser / promise_service 纯逻辑部分）"""
from datetime import datetime, timezone

from app.scheduler.promise_parser import extract_timer, strip_timer_tag


def _info(text, sender="ai"):
    return extract_timer(
        text, user_id=1, character_id=1, session_id=1,
        source_message_id=None, sender=sender,
    )


def _minutes(info):
    if info is None:
        return None
    return (info["trigger_at"] - datetime.now(timezone.utc)).total_seconds() / 60


def test_tag_minutes():
    info = _info("我去洗个澡 [timer:20m]")
    assert info is not None
    assert info["event_type"] == "back"
    assert info["sender"] == "ai"
    m = _minutes(info)
    assert 19 <= m <= 21


def test_tag_hours():
    info = _info("[timer:1h]")
    assert info is not None
    m = _minutes(info)
    assert 59 <= m <= 61


def test_tag_cn_unit():
    info = _info("【计时器:30分钟】")
    assert info is not None
    m = _minutes(info)
    assert 29 <= m <= 31


def test_strip_timer_tag():
    clean = strip_timer_tag("我洗完就回来 [timer:20m]")
    assert clean == "我洗完就回来"


def test_shower_ar_digits():
    info = _info("我去洗20分钟澡")
    assert info is not None
    assert info["event_type"] == "shower"
    m = _minutes(info)
    assert 19 <= m <= 21


def test_sleep_half_hour_cn():
    info = _info("我睡半小时")
    assert info is not None
    assert info["event_type"] == "sleep"
    m = _minutes(info)
    assert 29 <= m <= 31


def test_back_home_cn_twenty():
    info = _info("差不多二十分钟到家")
    assert info is not None
    assert info["event_type"] == "back"
    assert info["promise_text"] and "到家" in info["promise_text"]
    m = _minutes(info)
    assert 19 <= m <= 21


def test_back_half_hour_after():
    info = _info("我半小时后回来")
    assert info is not None
    m = _minutes(info)
    assert 29 <= m <= 31


def test_back_ten_something():
    info = _info("我这就往回走，大概十几分钟到")
    assert info is not None
    m = _minutes(info)
    assert 14 <= m <= 16


def test_wait_me():
    info = _info("等我10分钟，我马上到")
    assert info is not None
    m = _minutes(info)
    assert 9 <= m <= 11


def test_user_sender():
    info = _info("我去洗澡，20分钟后回来", sender="user")
    assert info is not None
    assert info["sender"] == "user"


def test_no_timer():
    assert _info("马上回来") is None
    assert _info("我看10分钟视频") is None
    assert _info("今天天气不错") is None


def test_max_clamp():
    info = _info("[timer:48h]")
    assert info is not None
    m = _minutes(info)
    assert 23 * 60 <= m <= 24 * 60

