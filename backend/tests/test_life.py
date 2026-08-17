"""Life Engine 纯逻辑测试：状态公式 / 时段 / 活动效用（2026-08-12）"""

from app.life.life_state import clamp, phase_of, settle_energy, settle_focus, settle_needs, default_needs
from app.life.activity import activity_score, ACTIVITIES


def test_phase_of_边界():
    assert phase_of(0) == "sleep"
    assert phase_of(6) == "sleep"
    assert phase_of(7) == "morning"
    assert phase_of(12) == "afternoon"
    assert phase_of(18) == "evening"
    assert phase_of(22) == "evening"
    assert phase_of(23) == "sleep"


def test_energy_结算():
    assert settle_energy(70, "sleep") == 75
    assert settle_energy(98, "sleep") == 100  # clamp
    assert settle_energy(70, "morning") == 68  # 白天 -2
    assert settle_energy(10, "afternoon", activity_cost=12) == 0  # 低于下限 clamp
    assert settle_energy(50, "evening", activity_cost=5) == 43


def test_focus_与需求():
    f = settle_focus(50)
    assert 0 <= f <= 100
    needs = default_needs()
    out = settle_needs(needs, {"curiosity": 15})
    for k in needs:
        assert 0 <= out[k] <= 100
    # 满足后 curiosity 应低于自然增长范围（至少被扣 15）
    assert out["curiosity"] <= needs["curiosity"] + 8 - 15


def test_activity_score_门槛与需求():
    needs = {k: 50 for k in ACTIVITIES["rest"]["needs"]}
    # energy 不够 -> 0
    assert activity_score("organize_memory", needs, energy=3, phase="morning") == 0
    # 需求高 -> 分数更高（同 energy 同时段）
    needs_high = dict(needs)
    needs_high["reflection"] = 90
    needs_high["productivity"] = 90
    lo = activity_score("organize_memory", {**needs, "reflection": 10, "productivity": 10}, 80, "morning")
    hi = activity_score("organize_memory", needs_high, 80, "morning")
    assert hi > lo
    # clamp 兜底
    assert clamp(-5) == 0
    assert clamp(150) == 100


def test_phase2_活动定义完整():
    """Phase 2：5 种活动齐备，依赖能力与产物子类型正确"""
    assert set(ACTIVITIES) == {
        "rest", "organize_memory", "reflect", "social_prepare", "browse", "create", "learn",
    }
    # browse/learn 依赖 browser 能力；create 无硬依赖（image_gen=allow 时生图否则纯文字）
    assert ACTIVITIES["browse"]["scopes"] == ["browser"]
    assert ACTIVITIES["learn"]["scopes"] == ["browser"]
    assert ACTIVITIES["create"]["scopes"] == []
    # 产物子类型：create 走 life_event、browse/learn 走 note
    assert ACTIVITIES["create"]["sub_type"] == "life_event"
    assert ACTIVITIES["browse"]["sub_type"] == "note"
    assert ACTIVITIES["learn"]["sub_type"] == "note"
    # energy 门槛兜底（新活动同样受 energy_cost 约束）
    for name in ("browse", "create", "learn"):
        assert activity_score(name, {k: 50 for k in ACTIVITIES[name]["needs"]}, energy=1, phase="morning") == 0


def test_phase3_兴趣衰减成长():
    from app.life.interest import decay_level, grow_level, interest_status
    # 衰减：每小时 2%（0.98**72 ≈ 0.23，3 天降到约 1/4；公式与表默认一致）
    lv = decay_level(50, 72)
    assert 10 <= lv <= 14
    # 约 34 小时降到一半
    lv_half = decay_level(50, 34)
    assert 23 <= lv_half <= 27
    # 无时间流逝不变
    assert decay_level(50, 0) == 50
    # 成长钳制
    assert grow_level(95, 10) == 100
    assert grow_level(5, -10) == 0  # 理论不会为负（delta 恒正，纯防呆）
    # 状态分档
    assert interest_status(80) == "hot"
    assert interest_status(30) == "active"
    assert interest_status(3) == "dormant"


def test_phase3_目标推进闭环():
    from app.life.goal import GOAL_ACTIVITY_MAP
    # 类型 → 推进活动映射完整
    assert GOAL_ACTIVITY_MAP["relationship"] == "social_prepare"
    assert GOAL_ACTIVITY_MAP["creative"] == "create"
    assert GOAL_ACTIVITY_MAP["growth"] == "organize_memory"
    assert GOAL_ACTIVITY_MAP["explore"] == "browse"
    assert GOAL_ACTIVITY_MAP["skill"] == "learn"


def test_phase2_create_时段加成():
    """Phase 2：evening 创作加成，且其余时段无加成"""
    needs = {k: 90 for k in ACTIVITIES["create"]["needs"]}
    base = activity_score("create", needs, 80, "morning")
    assert base > 0
    # 直接比较（random 0.8-1.2 可能波动，用多次采样验证期望更大）
    import statistics
    scores_morning = [activity_score("create", needs, 80, "morning") for _ in range(60)]
    scores_evening = [activity_score("create", needs, 80, "evening") for _ in range(60)]
    assert statistics.mean(scores_evening) > statistics.mean(scores_morning)
