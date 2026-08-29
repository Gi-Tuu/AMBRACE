"""Life Loop 行为决策器（纯函数，零 LLM）。

输入 StateSnapshot → 输出 Decision（action + 参数）。
设计稿 §2：硬约束过滤 → 阈值触发 → 事件触发 → 优先级排序 → 随机扰动 → 冷却配额。
2026-08-26 修正：外出 location 门控（location != home 时只允许外出型动作或 return_home，
夜间/低体力/傍晚强制回家，杜绝「人在外面却在室内活动」的状态矛盾）。
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field

# ── 动作定义 ──────────────────────────────────────────────
# 每个动作：触发条件、副作用参数、是否可见、是否落记忆、冷却（tick 数）
@dataclass
class ActionDef:
    name: str
    label: str
    energy_cost: int = 0          # 正=消耗，负=恢复
    needs_satisfied: dict = field(default_factory=dict)  # {"social": 15, ...}
    mood_delta: int = 0
    location_to: str | None = None     # 执行后 location
    room_to: str | None = None         # 执行后 current_room
    visible: bool = False              # 是否可能面向用户
    memory: bool = False               # 是否落记忆
    memory_importance: int = 3
    cooldown_ticks: int = 2            # 至少间隔几个 tick
    llm_copy: bool = False             # 是否需要 LLM 文案（受 life_loop_llm 控制）
    followup_window: str | None = None  # "next_online" / "morning" / "night_review"

# 动作表（MVP 用保守集；Phase 2 再加外出/社交/宠物）
ACTIONS: dict[str, ActionDef] = {
    "sleep": ActionDef("sleep", "睡觉", energy_cost=-8, mood_delta=3,
                       location_to="home", room_to="bedroom", cooldown_ticks=4),
    "rest": ActionDef("rest", "休息", energy_cost=-5, mood_delta=5,
                      needs_satisfied={"relaxation": 15}, cooldown_ticks=2),
    "eat": ActionDef("eat", "吃饭", energy_cost=2, mood_delta=5,
                     needs_satisfied={"relaxation": 5}, room_to="kitchen", cooldown_ticks=4),
    "study": ActionDef("study", "学习", energy_cost=6, mood_delta=2,
                       needs_satisfied={"learning": 18, "curiosity": 8},
                       room_to="bedroom", memory=True, cooldown_ticks=3),
    "create": ActionDef("create", "创作", energy_cost=8, mood_delta=5,
                        needs_satisfied={"creativity": 20},
                        room_to="living", memory=True, memory_importance=4,
                        visible=True, llm_copy=True, followup_window="next_online",
                        cooldown_ticks=4),
    "browse": ActionDef("browse", "浏览", energy_cost=4, mood_delta=3,
                        needs_satisfied={"curiosity": 15, "entertainment": 10},
                        room_to="living", memory=True, cooldown_ticks=2),
    "watch_show": ActionDef("watch_show", "看剧", energy_cost=-2, mood_delta=8,
                            needs_satisfied={"entertainment": 18, "relaxation": 10},
                            room_to="living", cooldown_ticks=3),
    "coffee": ActionDef("coffee", "喝咖啡", energy_cost=-3, mood_delta=2,
                        needs_satisfied={"productivity": 8},
                        room_to="kitchen", cooldown_ticks=6),
    "walk": ActionDef("walk", "散步", energy_cost=5, mood_delta=8,
                      needs_satisfied={"relaxation": 12, "social": 5},
                      location_to="outside", room_to="exit",
                      visible=True, memory=True, llm_copy=True,
                      followup_window="next_online", cooldown_ticks=6),
    "go_out": ActionDef("go_out", "出门", energy_cost=8, mood_delta=10,
                        needs_satisfied={"curiosity": 20, "social": 10},
                        location_to="world", room_to="exit",
                        visible=True, memory=True, memory_importance=4,
                        llm_copy=True, followup_window="next_online", cooldown_ticks=8),
    "visit_friend": ActionDef("visit_friend", "拜访朋友", energy_cost=7, mood_delta=10,
                              needs_satisfied={"social": 25},
                              location_to="friend", room_to="exit",
                              visible=True, memory=True, memory_importance=4,
                              llm_copy=True, followup_window="next_online", cooldown_ticks=8),
    "pet_play": ActionDef("pet_play", "陪宠物", energy_cost=3, mood_delta=8,
                          needs_satisfied={"social": 10}, visible=True,
                          memory=True, cooldown_ticks=3),
    # Phase 2（2026-08-26）：角色自主开局（需决策器满足条件 + life_loop 特殊处理）
    "play_game": ActionDef("play_game", "玩桌游", energy_cost=5, mood_delta=8,
                           needs_satisfied={"social": 15, "entertainment": 15},
                           visible=True, memory=True, memory_importance=4,
                           llm_copy=True, followup_window="next_online",
                           cooldown_ticks=12),
    "idle": ActionDef("idle", "发呆", energy_cost=-1, mood_delta=1,
                      needs_satisfied={}, cooldown_ticks=0),
    # 修正 2026-08-26：外出状态回程动作（决策器 location 门控使用）
    "return_home": ActionDef("return_home", "回家", energy_cost=-4, mood_delta=2,
                             location_to="home", room_to="living",
                             memory=False, cooldown_ticks=2),
}

# ── 状态快照 ──────────────────────────────────────────────
@dataclass
class StateSnapshot:
    character_id: int
    user_id: int
    energy: int
    focus: int
    needs: dict[str, int]
    phase: str               # sleep/morning/afternoon/evening
    mood: int                # 八维 mood（只读）
    fatigue: int
    anger: int
    location: str
    current_room: str
    last_action: str | None = None
    last_action_tick: int = 0   # 距今几个 tick
    active_goals: list[dict] = field(default_factory=list)
    due_schedules: list[dict] = field(default_factory=list)
    pet_alerts: list[dict] = field(default_factory=list)
    pending_intents: list[dict] = field(default_factory=list)  # 聊天驱动意图
    user_active_recently: bool = False   # 最近 30 分钟有交互
    dnd: bool = False                    # 勿扰模式
    play_game_available: bool = False    # 当日自主开局<1局 + 同用户活跃角色>=2 + 免打扰外 + 用户不在场

@dataclass
class Decision:
    action: str
    params: dict = field(default_factory=dict)
    reason: str = ""
    visible: bool = False

# ── 决策器 ──────────────────────────────────────────────
def decide(snap: StateSnapshot) -> Decision:
    """纯函数决策。返回 Decision；零 LLM、零 IO。"""

    # ① 硬约束：外出状态门控（修正 2026-08-26：人在外时禁止室内动作，避免"人在外面却在客厅看剧"）
    if snap.location not in (None, "", "home"):
        if snap.phase == "sleep" or snap.energy < 60 or snap.phase == "evening":
            return Decision("return_home", reason="come_back")
        out_acts = [
            n for n, a in ACTIONS.items()
            if a.location_to in ("world", "friend", "outside")
            and _cooldown_ok(n, snap)
        ]
        cands = [(n, _score_action(n, ACTIONS[n], snap)) for n in out_acts]
        cands = [(n, s) for n, s in cands if s > 0]
        if cands:
            total = sum(s for _, s in cands)
            r = random.uniform(0, total)
            upto = 0.0
            chosen = cands[-1][0]
            for name, score in cands:
                upto += score
                if upto >= r:
                    chosen = name
                    break
            return Decision(chosen, reason="still_outside")
        return Decision("return_home", reason="outside_finished")

    # ① 硬约束：夜间睡眠时段
    if snap.phase == "sleep":
        if snap.energy < 80:
            return Decision("sleep", reason="night_sleep")
        return Decision("idle", reason="night_idle")

    # ② 健康硬需求
    if snap.energy < 20:
        return Decision("sleep", reason="energy_critical")
    if snap.fatigue > 65 and snap.energy < 50:
        return Decision("rest", reason="fatigue_rest")
    if snap.anger > 60:
        return Decision("idle", reason="anger_time_alone")  # 不打扰

    # ③ 事件触发（确定性，高优先）
    # 3a. 宠物报警
    if snap.pet_alerts:
        alert = snap.pet_alerts[0]
        if alert.get("hungry"):
            return Decision("pet_play", params={"pet_alert": "hungry"}, reason="pet_hungry")
    # 3b. 日程到点
    if snap.due_schedules:
        sch = snap.due_schedules[0]
        title = sch.get("title", "")
        if "吃" in title or "饭" in title:
            return Decision("eat", reason="schedule_due")
        if "睡" in title or "休息" in title:
            return Decision("sleep", reason="schedule_due")
        return Decision("study", params={"schedule_title": title}, reason="schedule_due")
    # 3c. 聊天驱动意图（最高优先事件档）
    if snap.pending_intents:
        intent = snap.pending_intents[0]
        mapping = {
            "go_out": "go_out", "walk": "walk", "eat": "eat",
            "visit_friend": "visit_friend", "pet_care": "pet_play",
            "create": "create", "study": "study",
        }
        act = mapping.get(intent.get("action_type", ""))
        if act and _cooldown_ok(act, snap):
            return Decision(act, params={"intent_id": intent.get("id")},
                            reason="chat_intent")
    # 3d. 进行中目标
    if snap.active_goals:
        goal = snap.active_goals[0]
        gtype = goal.get("type", "")
        goal_action = {"creative": "create", "skill": "study",
                       "explore": "browse", "growth": "study"}.get(gtype)
        if goal_action and _cooldown_ok(goal_action, snap):
            return Decision(goal_action, params={"goal_id": goal.get("id")},
                            reason="goal_progress")

    # ④ 需求阈值
    candidates: list[tuple[str, float]] = []
    for name, act in ACTIONS.items():
        if name in ("sleep", "idle"):
            continue
        if name == "play_game" and not snap.play_game_available:
            continue  # Phase 2：自主开局需满足当日限额/同用户角色数/免打扰/用户不在场
        if not _cooldown_ok(name, snap):
            continue
        if act.location_to and snap.user_active_recently and act.visible:
            continue  # 用户在场时不做外出型打扰动作
        score = _score_action(name, act, snap)
        if score > 0:
            candidates.append((name, score))

    if not candidates:
        return Decision("idle", reason="no_candidate")

    # ⑤ 加权随机（非贪心取 max）
    total = sum(s for _, s in candidates)
    r = random.uniform(0, total)
    upto = 0.0
    chosen = candidates[-1][0]
    for name, score in candidates:
        upto += score
        if upto >= r:
            chosen = name
            break

    return Decision(chosen, reason="weighted_choice")


def _score_action(name: str, act: ActionDef, snap: StateSnapshot) -> float:
    """动作效用分：需求渴求度 × 时段加成 × 状态门槛 × 随机扰动。"""
    if snap.energy < max(act.energy_cost, 5) and act.energy_cost > 0:
        return 0.0
    score = 0.0
    for need, satisfy in act.needs_satisfied.items():
        val = snap.needs.get(need, 50)
        if val >= 60:  # 需求超过 60 才考虑满足
            score += (val - 40) * satisfy / 20.0
    # 时段加成
    phase_bonus = {
        "morning": {"study": 1.3, "eat": 1.2, "coffee": 1.4},
        "afternoon": {"browse": 1.2, "walk": 1.2, "create": 1.1},
        "evening": {"watch_show": 1.4, "create": 1.3, "visit_friend": 1.2},
    }.get(snap.phase, {})
    score *= phase_bonus.get(name, 1.0)
    # 心情低时倾向安慰型动作
    if snap.mood < 40 and name in ("walk", "watch_show", "pet_play", "visit_friend"):
        score *= 1.5
    # 随机扰动 0.7-1.3
    score *= random.uniform(0.7, 1.3)
    return round(score, 2)


def _cooldown_ok(name: str, snap: StateSnapshot) -> bool:
    """冷却检查：同一动作间隔 N 个 tick。"""
    if snap.last_action != name:
        return True
    return snap.last_action_tick >= ACTIONS[name].cooldown_ticks
