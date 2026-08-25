# -*- coding: utf-8 -*-
from app.scheduler.arbiter import _motivation_score, _apply_reflection_bonus, REFLECTION_BONUS


def test_reflection_bonus_adds():
    # 有复盘 → 分数加成（封顶 1.0）
    s = _apply_reflection_bonus(0.5, True)
    assert s == min(1.0, 0.5 + REFLECTION_BONUS)
    assert _apply_reflection_bonus(0.98, True) == 1.0
    # 无复盘 → 不变
    assert _apply_reflection_bonus(0.5, False) == 0.5


def test_reflection_bonus_zero_score_unchanged():
    # 渴望分本身为 0（无状态/异常）→ 有复盘也不加分（避免空转角色无脑唤醒）
    assert _apply_reflection_bonus(0.0, True) == 0.0


def test_reflection_bonus_boundary():
    # 恰好差一点到阈值 → 有复盘可越过阈值（可聊信号生效）
    s = _apply_reflection_bonus(0.55, True)
    assert s >= 0.60


def test_calm_and_recent_low():
    # 状态平静、刚互动过 → 低分（低于 speak 阈值）
    s = _motivation_score(
        attachment=50, curiosity=50, desire=50, mood=50, anger=20,
        fatigue=60, hours_since_activity=1,
    )
    assert 0.0 <= s < 0.5


def test_missing_user_high():
    # 依恋高、久未互动、不疲惫 → 高分（超过 speak 阈值）
    s = _motivation_score(
        attachment=90, curiosity=70, desire=60, mood=60, anger=20,
        fatigue=20, hours_since_activity=30,
    )
    assert s > 0.60


def test_fatigue_suppresses():
    # 同样思念，疲惫高 → 分数更低（不想打扰）
    s1 = _motivation_score(
        attachment=90, curiosity=70, desire=60, mood=60, anger=20,
        fatigue=20, hours_since_activity=30,
    )
    s2 = _motivation_score(
        attachment=90, curiosity=70, desire=60, mood=60, anger=20,
        fatigue=90, hours_since_activity=30,
    )
    assert s2 < s1


def test_time_factor_accumulates():
    # 同一状态，互动越久未发生 → 动机越高（2h 后累积，24h 封顶）
    s1 = _motivation_score(
        attachment=70, curiosity=60, desire=50, mood=55, anger=20,
        fatigue=30, hours_since_activity=1,
    )
    s2 = _motivation_score(
        attachment=70, curiosity=60, desire=50, mood=55, anger=20,
        fatigue=30, hours_since_activity=30,
    )
    assert s2 > s1


def test_sadness_care_impulse():
    # 心情差 → 关怀冲动上升
    s1 = _motivation_score(
        attachment=60, curiosity=50, desire=50, mood=50, anger=20,
        fatigue=30, hours_since_activity=24,
    )
    s2 = _motivation_score(
        attachment=60, curiosity=50, desire=50, mood=20, anger=20,
        fatigue=30, hours_since_activity=24,
    )
    assert s2 > s1


def test_clamped_range():
    s = _motivation_score(
        attachment=0, curiosity=100, desire=100, mood=0, anger=100,
        fatigue=100, hours_since_activity=0,
    )
    assert 0.0 <= s <= 1.0