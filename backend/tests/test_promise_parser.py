# -*- coding: utf-8 -*-
"""定时承诺解析器测试（promise_parser / promise_service 纯逻辑部分）"""
from datetime import datetime, timezone

from app.scheduling.promise_parser import extract_timer, strip_timer_tag


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


# ── 陪伴主动线（2026-08-30）：无数字日常句式兜底 ──


def test_vague_meeting():
    for s in ("我去开会了", "开会去了", "去忙了", "去忙会儿"):
        info = _info(s, sender="user")
        assert info is not None, s
        assert info["event_type"] == "ready", s
        m = _minutes(info)
        assert 59 <= m <= 61, s


def test_vague_meal():
    for s in ("去吃饭", "吃饭去了", "去吃饭了", "先去吃饭"):
        info = _info(s, sender="user")
        assert info is not None, s
        assert info["event_type"] == "ready", s
        m = _minutes(info)
        assert 39 <= m <= 41, s


def test_vague_try():
    for s in ("等下试", "等会试", "等会儿试", "一会试", "一会儿试", "待会试", "等会试试"):
        info = _info(s, sender="user")
        assert info is not None, s
        assert info["event_type"] == "ready", s
        m = _minutes(info)
        assert 9 <= m <= 11, s


def test_vague_shower():
    for s in ("去洗澡", "洗个澡", "洗澡去了", "去洗个澡"):
        info = _info(s, sender="user")
        assert info is not None, s
        assert info["event_type"] == "ready", s
        m = _minutes(info)
        assert 29 <= m <= 31, s


def test_vague_not_shadow_numbered():
    """带数字的旧句式仍走 _PATTERNS，行为与顺序不变"""
    info = _info("我去洗20分钟澡")
    assert info is not None and info["event_type"] == "shower"
    m = _minutes(info)
    assert 19 <= m <= 21

    info = _info("我半小时后回来")
    assert info is not None and info["event_type"] == "back"
    m = _minutes(info)
    assert 29 <= m <= 31

    info = _info("等我10分钟，我马上到")
    assert info is not None and info["event_type"] == "back"
    m = _minutes(info)
    assert 9 <= m <= 11


def test_vague_negative_no_promise():
    """无承诺语义句子不得误触发"""
    for s in ("等下再说", "回头聊", "那等会吧", "我先睡了"):
        assert _info(s) is None, s


# ── F1（2026-09-08）：sender 语义与归属收敛 ──


def test_f1_ai_sender_vague_pure_action_none():
    """F1a：AI 自述离开（去吃饭/开会/洗澡）不再生成"到点喊用户"ready 事件"""
    for s in ("我去吃饭了", "吃饭去了", "先去吃饭", "去吃饭",
              "我去开会了", "开会去了", "去忙了", "去忙会儿",
              "我去洗澡了", "洗澡去了", "去洗澡", "洗个澡"):
        assert _info(s, sender="ai") is None, s


def test_f1_user_sender_vague_pure_action_kept():
    """F1a：用户说自己离开 → 保留 ready（原陪伴主动线设计不变）"""
    for s, lo in (("去吃饭", 39), ("开会去了", 59), ("去洗澡", 29)):
        info = _info(s, sender="user")
        assert info is not None and info["event_type"] == "ready", s
        m = _minutes(info)
        assert lo <= m <= lo + 2, s


def test_f1_explicit_tag_and_back_semantics_unaffected():
    """F1a：显式 [timer] 标签与"回来"方向语义不受 sender 分支影响"""
    for sender in ("ai", "user"):
        info = _info("去吃饭，[timer:30m]后回来找你", sender=sender)
        assert info is not None and info["event_type"] == "back", sender
        info = _info("我20分钟后回来", sender=sender)
        assert info is not None and info["event_type"] == "back", sender


def test_f1_extract_ai_timer_source_not_user_message():
    """F1b：AI 侧入口 source_message_id 只能是 AI 消息 id 或 None，绝不绑用户消息"""
    from app.scheduling.promise_parser import extract_ai_timer
    info = extract_ai_timer("我去洗20分钟澡", user_id=1, character_id=1, session_id=1)
    assert info is not None and info["event_type"] == "shower"
    assert info["sender"] == "ai"
    assert info["source_message_id"] is None
    info2 = extract_ai_timer("我半小时后回来", user_id=1, character_id=1, session_id=1,
                             ai_message_id=99)
    assert info2 is not None and info2["source_message_id"] == 99
    # F1a 叠加：AI 自述纯动作句经 AI 侧入口同样不建事件
    assert extract_ai_timer("我去吃饭了", user_id=1, character_id=1, session_id=1) is None


def test_f1_chat_service_ai_call_site_uses_extract_ai_timer():
    """F1b：chat_service AI 侧承诺提取走 extract_ai_timer（签名上不可能再接用户消息 id）"""
    import inspect
    from app.application import chat_service
    src = inspect.getsource(chat_service._run_agent_core)
    assert "extract_ai_timer(" in src

