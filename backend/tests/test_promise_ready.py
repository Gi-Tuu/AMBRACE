"""事件时钟「XX 后完成某事」句式测试（2026-08-15）

背景：用户 111（user_id=3）说"估计五分钟后就能吃上饭了！"未触发事件时钟——
现有正则只覆盖"回来/到家/找你"等去向动词，完成类句式漏匹配。
"""
from app.scheduler.promise_parser import extract_timer


def _extract(text):
    return extract_timer(text, user_id=3, character_id=6, session_id=11, sender="user")


def test_ready_meal_phrase():
    r = _extract("估计五分钟后就能吃上饭了！")
    assert r is not None
    assert r["event_type"] == "ready"
    assert r["promise_text"]


def test_ready_generic():
    assert _extract("半小时就能完成")["event_type"] == "ready"
    assert _extract("20分钟搞定")["event_type"] == "ready"
    assert _extract("几分钟就好了")["event_type"] == "ready"


def test_existing_phrases_unaffected():
    assert _extract("我洗完澡20分钟回来")["event_type"] == "back"
    assert _extract("我去睡半小时")["event_type"] == "sleep"
    assert _extract("等我10分钟")["event_type"] == "back"


def test_non_promise_not_matched():
    assert _extract("嗯，大概要多久？好了叫我") is None
    assert _extract("今晚和你一起做饭吧") is None


def test_ai_call_you_promise():
    """AI 说「二十分钟吧…好了我叫你」→ 到点 AI 叫用户（用户111今天场景）"""
    r = _extract("二十分钟吧，粥快得很。你躺着，好了我叫你。")
    assert r is not None
    assert r["event_type"] == "ready"
    assert "好了我叫你" in (r["promise_text"] or "")


def test_user_ask_duration_not_promise():
    """用户问「大概要多久？好了叫我」是问句，不触发（AI 回复才是承诺）"""
    assert _extract("嗯，大概要多久？好了叫我") is None


def test_vague_time_not_matched():
    """无承诺语义的模糊话不触发（等下/睡醒 但有叫你/喊你 已由兜底覆盖）"""
    assert _extract("等下再说") is None
    assert _extract("回头聊") is None


def _minutes_of(r):
    from datetime import datetime, timezone
    trig = r["trigger_at"]
    if trig.tzinfo is None:
        trig = trig.replace(tzinfo=timezone.utc)
    return round((trig - datetime.now(timezone.utc)).total_seconds() / 60)


def test_vague_dengxia_default_10():
    r = _extract("等下饭好了叫你。")
    assert r is not None and r["event_type"] == "ready"
    assert _minutes_of(r) == 10


def test_vague_shuixing_default_30():
    r = _extract("睡醒了喊你。")
    assert r is not None and r["event_type"] == "ready"
    assert _minutes_of(r) == 30


def test_vague_kanwan_default_30():
    r = _extract("那你先看会电视，我弄完这集叫你")
    assert r is not None and r["event_type"] == "ready"
    assert _minutes_of(r) == 30


def test_vague_no_promise_not_matched():
    assert _extract("等会再说") is None
    assert _extract("回头聊") is None
    assert _extract("那等会吧") is None
    assert _extract("我先睡了晚安") is None
