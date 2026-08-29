# -*- coding: utf-8 -*-
"""#63 机制1：弹簧-阻尼情绪单测（persona 基线 / 弹簧分支 / 线性保留 / fatigue 不变 / 心事惩罚边界）。"""
import random
from datetime import datetime, timedelta


from app.agent.loop import AGENT_FLAGS
from app.services import character_state_service as cs

_DIMS = ["mood", "body_temp", "desire", "possessiveness", "fatigue", "sensitivity", "comfort", "anger"]


class _St:
    def __init__(self, st_id, character_id, updated_at, **vals):
        self.id = st_id
        self.character_id = character_id
        self.updated_at = updated_at
        self.last_activity_at = None
        for k in _DIMS:
            setattr(self, k, vals.get(k, 50))


def _mk(st_id, character_id, updated_at, **vals):
    return _St(st_id, character_id, updated_at, **vals)


# ---------------- 人格基线（纯函数，零 LLM 零 DB） ----------------
def test_persona_baseline_cold_below_50():
    base = cs._derive_persona_baseline("高冷内敛", "简洁寡言")
    assert base["mood"] < 50
    assert 45.0 <= base["mood"] <= 65.0


def test_persona_baseline_warm_above_50():
    base = cs._derive_persona_baseline("热情开朗", "活泼俏皮")
    assert base["mood"] > 50
    assert 45.0 <= base["mood"] <= 65.0


def test_persona_baseline_mood_penalty_applied():
    base = cs._derive_persona_baseline("友善", "简洁", mood_penalty=-5.0)
    neutral = cs._derive_persona_baseline("友善", "简洁", mood_penalty=0.0)
    assert base["mood"] == neutral["mood"] - 5.0
    assert 45.0 <= base["mood"] <= 65.0


def test_persona_baseline_anger_bounds():
    hot = cs._derive_persona_baseline("脾气暴躁易怒", "火爆")
    calm = cs._derive_persona_baseline("温柔理性", "沉稳")
    assert hot["anger"] >= calm["anger"]
    assert 0.0 <= hot["anger"] <= 20.0
    assert 0.0 <= calm["anger"] <= 20.0


# ---------------- 弹簧-阻尼分支 ----------------
def test_spring_on_uses_spring_and_writes_velocity(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "spring_emotion_enabled", True)
    random.seed(10)
    now = datetime(2026, 8, 27, 12, 0, 0)
    st = _mk(1, 101, now - timedelta(hours=0.5), mood=20)
    cs._persona_baseline[101] = cs._derive_persona_baseline("外向开朗", "口语化")
    cs._spring_velocity.pop(101, None)
    cs._drifted_at.pop(1, None)
    cs._apply_drift_sync(st, now)
    assert 101 in cs._spring_velocity
    assert "mood" in cs._spring_velocity[101]


def test_spring_off_linear_no_velocity(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "spring_emotion_enabled", False)
    random.seed(10)
    now = datetime(2026, 8, 27, 12, 0, 0)
    st = _mk(2, 102, now - timedelta(hours=0.5), mood=20)
    cs._persona_baseline.pop(102, None)
    cs._spring_velocity.pop(102, None)
    cs._drifted_at.pop(2, None)
    cs._apply_drift_sync(st, now)
    assert 102 not in cs._spring_velocity or "mood" not in cs._spring_velocity.get(102, {})


def test_spring_mood_recovers_toward_baseline(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "spring_emotion_enabled", True)
    random.seed(7)
    now = datetime(2026, 8, 27, 12, 0, 0)
    base = cs._derive_persona_baseline("友善", "简洁")
    # 刚被骂完 mood=20：弹簧应向人格基线回升，但带有惯性（回升慢），且不越过基线
    st = _mk(3, 103, now - timedelta(hours=0.5), mood=20)
    cs._persona_baseline[103] = base
    cs._spring_velocity.pop(103, None)
    cs._drifted_at.pop(3, None)
    cs._apply_drift_sync(st, now)
    assert 20 < st.mood < base["mood"]


def test_spring_fatigue_unchanged(monkeypatch):
    """fatigue 不进弹簧，行为不变（活跃期上升）。"""
    monkeypatch.setitem(AGENT_FLAGS, "spring_emotion_enabled", True)
    random.seed(5)
    now = datetime(2026, 8, 27, 12, 0, 0)
    st = _mk(4, 104, now - timedelta(hours=2), fatigue=40)  # Δt=2h → 活跃上升更明显
    st.last_activity_at = now - timedelta(hours=1)  # 距上次互动 <3h → 活跃期上升
    cs._persona_baseline.pop(104, None)
    cs._spring_velocity.pop(104, None)
    cs._drifted_at.pop(4, None)
    cs._apply_drift_sync(st, now)
    assert st.fatigue > 40  # 活跃期疲劳上升
    assert "fatigue" not in cs._spring_velocity.get(104, {})  # 不进弹簧


def test_mood_baseline_penalty_only_bounds():
    """心事惩罚最高 -5；空心事返回 0。"""
    # 占位：真正 penalty 逻辑在 app/life/preoccupations 测得；此处只保证 penalty 会压低基线
    assert cs._derive_persona_baseline("友善", "", mood_penalty=-5.0)["mood"] < \
           cs._derive_persona_baseline("友善", "", mood_penalty=0.0)["mood"]


# ---------------- P3-4：弹簧撞墙速度置零 ----------------
def test_spring_wall_resets_velocity(monkeypatch):
    """弹簧 cur 越界撞墙时，把撞墙方向的 velocity 置零，避免持续推墙。

    构造：mood 已在 100（上墙），vel=+50 继续上推 → 弹簧迭代会越过 100；
    修复后 cur 被 clamp 到 100 且 vel 被置为 min(0, vel)=0。
    """
    monkeypatch.setitem(AGENT_FLAGS, "spring_emotion_enabled", True)
    random.seed(11)
    now = datetime(2026, 8, 27, 12, 0, 0)
    st = _mk(5, 105, now - timedelta(hours=0.1), mood=100)  # Δt=0.1h → 单步
    cs._persona_baseline[105] = cs._derive_persona_baseline("友善", "简洁")  # mood 基线 ~55
    cs._spring_velocity[105] = {"mood": 50.0}  # 上墙方向高速
    cs._drifted_at.pop(5, None)
    cs._apply_drift_sync(st, now)
    assert st.mood == 100  # 卡在上墙
    assert cs._spring_velocity[105]["mood"] == 0.0  # 撞墙方向速度被置零
