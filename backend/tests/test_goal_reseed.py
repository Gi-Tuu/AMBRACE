"""AI 生活目标：无进行中目标时自动播种（2026-08-15）

背景：目标全部完成后 _n_g==0 条件不满足（历史目标仍计数），角色进入"无目标"状态。
修复：改为无 active 目标时播种，且优先按热门兴趣生成个性化目标。
"""
import inspect

from app.life.life_tick import _phase3_hook


def test_goal_reseed_logic():
    """验证播种决策逻辑（源码断言关键分支存在）"""
    src = inspect.getsource(_phase3_hook)
    # 无 active 目标判定（原来只看历史总数，现在看进行中）
    assert 'LifeGoal.status == "active"' in src
    # 热门兴趣优先播种
    assert "i.level >= 45" in src
    # 兜底保证至少 2 个
    assert "_sown >= 2" in src
    # 兴趣 → 目标类型映射
    assert '"creative"' in src and '"explore"' in src and '"skill"' in src


def test_interest_goal_map_consistent():
    """兴趣映射的目标类型必须能被 advance_goal 的活动推进（GOAL_ACTIVITY_MAP）"""
    from app.life.goal import GOAL_ACTIVITY_MAP
    assert "creative" in GOAL_ACTIVITY_MAP
    assert "explore" in GOAL_ACTIVITY_MAP
    assert "skill" in GOAL_ACTIVITY_MAP
