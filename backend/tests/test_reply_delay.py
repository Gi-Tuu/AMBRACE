# -*- coding: utf-8 -*-
"""#63 机制2：动态回复延迟纯函数单测（固定 seed / 边界 / 封顶）。"""
import random

from app.utils.reply_delay import BASE_DELAY, MAX_DELAY, calc_typing_delay, estimate_response_chars


def test_estimate_response_chars_分档():
    assert estimate_response_chars(0) == 12
    assert estimate_response_chars(4) == 12
    assert estimate_response_chars(6) == 30
    assert estimate_response_chars(15) == 70
    assert estimate_response_chars(50) == 120
    assert estimate_response_chars(200) == 200
    assert estimate_response_chars(-5) == 12


def test_calc_typing_delay_固定seed确定性():
    rng = random.Random(42)
    a = calc_typing_delay(60, mood=50, fatigue=50, anger=50, is_short_reply=False, rng=rng)
    rng = random.Random(42)
    b = calc_typing_delay(60, mood=50, fatigue=50, anger=50, is_short_reply=False, rng=rng)
    assert a == b


def test_calc_typing_delay_心情低落更慢():
    rng = random.Random(1)
    low = calc_typing_delay(60, mood=20, fatigue=50, anger=50, rng=rng)
    rng = random.Random(1)
    high = calc_typing_delay(60, mood=80, fatigue=50, anger=50, rng=rng)
    assert low > high


def test_calc_typing_delay_疲惫更慢():
    rng = random.Random(2)
    tired = calc_typing_delay(60, mood=50, fatigue=80, anger=50, rng=rng)
    rng = random.Random(2)
    fresh = calc_typing_delay(60, mood=50, fatigue=20, anger=50, rng=rng)
    assert tired > fresh


def test_calc_typing_delay_怒气更慢():
    rng = random.Random(3)
    angry = calc_typing_delay(60, mood=50, fatigue=50, anger=85, rng=rng)
    rng = random.Random(3)
    calm = calc_typing_delay(60, mood=50, fatigue=50, anger=10, rng=rng)
    assert angry > calm


def test_calc_typing_delay_短句快于长句():
    rng = random.Random(4)
    short = calc_typing_delay(20, mood=50, fatigue=50, anger=50, is_short_reply=True, rng=rng)
    rng = random.Random(4)
    long = calc_typing_delay(200, mood=50, fatigue=50, anger=50, is_short_reply=False, rng=rng)
    assert short < long


def test_calc_typing_delay_封顶8s():
    # 极端输入也绝不越过 MAX_DELAY
    for _ in range(50):
        rng = random.Random()
        d = calc_typing_delay(999, mood=0, fatigue=100, anger=100, rng=rng)
        assert 0.0 <= d <= MAX_DELAY


def test_calc_typing_delay_中性接近基础值():
    # 无随机（spread=0 不可直接注入，故断言在一个合理带宽内）
    rng = random.Random(7)
    d = calc_typing_delay(60, mood=50, fatigue=50, anger=50, rng=rng)
    assert BASE_DELAY * 0.7 <= d <= BASE_DELAY * 1.4
