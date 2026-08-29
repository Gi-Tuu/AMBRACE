# -*- coding: utf-8 -*-
"""Life Loop 分层决策器纯逻辑测试（decision.py，零 LLM / 零 IO）。

覆盖（2026-08-26）：
- 夜间睡眠时段 / 夜间高精力发呆
- 低体力强制睡眠 / 高疲劳低体力休息 / 高怒气独处
- 外出门控：夜间 / 低体力 / 傍晚强制回家；人在外不再室内活动
- 冷却（_cooldown_ok）同名动作间隔 & 跨动作不限
- 宠物报警（hungry）优先消费
- 聊天驱动意图优先（priority 高于需求阈值）
- 进行中目标推进
- 需求阈值加权随机（不低于 idle 兜底合理）
"""
from app.life.decision import decide, StateSnapshot, ACTIONS, _cooldown_ok

_NEEDS = {k: 50 for k in (
    "curiosity", "productivity", "relaxation", "social",
    "creativity", "learning", "reflection", "entertainment",
)}


def _snap(**over) -> StateSnapshot:
    base = dict(
        character_id=1, user_id=1, energy=70, focus=50,
        needs=dict(_NEEDS), phase="afternoon", mood=50,
        fatigue=30, anger=10, location="home", current_room="living",
    )
    base.update(over)
    return StateSnapshot(**base)


# ─────────── ① 硬约束：夜间 / 低体力 ───────────

def test_夜间睡眠时段():
    assert decide(_snap(phase="sleep", energy=40)).action == "sleep"


def test_夜间高精力发呆():
    assert decide(_snap(phase="sleep", energy=90)).action == "idle"


def test_低体力强制睡眠():
    assert decide(_snap(energy=10, phase="morning")).action == "sleep"


def test_高疲劳低体力休息():
    assert decide(_snap(energy=40, fatigue=70)).action == "rest"


def test_高怒气独处():
    assert decide(_snap(anger=80, mood=90)).action == "idle"


# ─────────── ② 外出门控（2026-08-26 修正） ───────────

def test_外出门控_夜间强制回家():
    assert decide(_snap(location="world", phase="sleep", energy=90)).action == "return_home"


def test_外出门控_低体力强制回家():
    assert decide(_snap(location="friend", energy=30, phase="afternoon")).action == "return_home"


def test_外出门控_傍晚强制回家():
    assert decide(_snap(location="outside", phase="evening", energy=80)).action == "return_home"


def test_外出门控_人在外不再室内活动():
    """location != home 时：即使需求强烈偏向室内动作（学习/创作/浏览），也绝不产出室内动作。"""
    needs = dict(_NEEDS)
    needs["learning"] = 95
    needs["creativity"] = 95
    snap = _snap(location="world", energy=85, phase="afternoon", needs=needs)
    act = decide(snap).action
    # 室内动作 set：eat/study/create/browse/watch_show/coffee/rest/pet_play/sleep/idle
    assert act not in {"eat", "study", "create", "browse", "watch_show", "coffee",
                       "rest", "pet_play", "sleep", "idle"}
    assert act in {"go_out", "walk", "visit_friend", "return_home"}


def test_外出门控_需求高时仍可选外出型():
    needs = dict(_NEEDS)
    needs["curiosity"] = 95
    needs["social"] = 95
    needs["relaxation"] = 90
    snap = _snap(location="world", energy=85, phase="afternoon", needs=needs)
    act = decide(snap).action
    assert act in {"go_out", "walk", "visit_friend", "return_home"}


# ─────────── ③ 冷却（_cooldown_ok） ───────────

def test_冷却_同名动作间隔不足():
    s = _snap(last_action="walk", last_action_tick=1)
    assert _cooldown_ok("walk", s) is False  # 6 tick 未到


def test_冷却_同名动作间隔足够():
    s = _snap(last_action="walk", last_action_tick=6)
    assert _cooldown_ok("walk", s) is True


def test_冷却_跨动作不受限():
    s = _snap(last_action="eat", last_action_tick=0)
    assert _cooldown_ok("walk", s) is True


def test_冷却_决策不重复选择同名动作():
    # last_action=study、tick 不足：study 冷却中，不应被选中
    needs = dict(_NEEDS)
    needs["learning"] = 96
    needs["curiosity"] = 96
    snap = _snap(energy=90, phase="morning", needs=needs, last_action="study", last_action_tick=0)
    action = decide(snap).action
    # 同一 tick 内 study 已在冷却；决策可落在其它动作（加权/兜底 idle）
    assert "study" not in (action,)


# ─────────── ④ 事件触发：宠物报警 / 聊天意图 / 目标 ───────────

def test_宠物报警优先消费():
    snap = _snap(pet_alerts=[{"hungry": True}])
    d = decide(snap)
    assert d.action == "pet_play"
    assert d.params.get("pet_alert") == "hungry"


def test_聊天驱动意图优先():
    snap = _snap(pending_intents=[{"id": 5, "action_type": "go_out"}])
    d = decide(snap)
    assert d.action == "go_out"
    assert d.params.get("intent_id") == 5


def test_聊天意图_冷却不符回退():
    """意图动作恰在冷却中 → 不消费该意图（落入后续档位）。"""
    snap = _snap(pending_intents=[{"id": 5, "action_type": "go_out"}],
                 last_action="go_out", last_action_tick=0)
    d = decide(snap)
    assert d.action != "go_out"


def test_进行中目标推进():
    snap = _snap(active_goals=[{"id": 1, "type": "creative"}])
    d = decide(snap)
    assert d.action == "create"
    assert d.params.get("goal_id") == 1


# ─────────── ⑤ 需求阈值 / 兜底 ───────────

def test_需求阈值_产出非空动作():
    needs = dict(_NEEDS)
    needs["curiosity"] = 96
    needs["social"] = 40
    snap = _snap(energy=90, phase="afternoon", needs=needs, last_action="idle")
    action = decide(snap).action
    assert action in ACTIONS


def test_无候选兜底idle():
    # 所有需求极低 + 健康项正常 → 无候选 → idle
    needs = {k: 10 for k in _NEEDS}
    snap = _snap(energy=80, phase="afternoon", needs=needs, mood=60)
    assert decide(snap).action == "idle"


def test_动作表包含return_home():
    assert "return_home" in ACTIONS
    assert ACTIONS["return_home"].location_to == "home"
