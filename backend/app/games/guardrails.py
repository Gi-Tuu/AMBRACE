"""游戏 AI 局失控保护（引擎无关）。

进程内计数，不做 schema 迁移：
- 同 (round,phase,seat,action,规范化payload) 连续重复 → 三级收敛（换目标 → 强推阶段 → 止血）；
- 单局 AI 决策（≈LLM 调用）总数硬上限 → 止血结束，防止任何形态的空转烧钱。

状态放模块级而非引擎实例：_resume_ai_turns 每轮都重新 engine.load 新建引擎，
只有模块级状态才能跨"重载"连续累计重复序列与总决策数。

阈值档位（口径1，2026-09-06 用户拍板）：NORMAL=现状（含 ≥1 真人局，零行为变化）；
AI_ONLY=全部玩家 ai 的纯空转局（无真人等待，空转=纯 token 消耗）——总上限约 1/10、
更快换招与止血。档位在 _resume_ai_turns 首次获取 guard 时按该局 GamePlayer 判定并
随 guard 固化到局结束（registry 跨 load 保留，不逐轮重判漂移）；判定异常保守回落 NORMAL。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

# ── 阈值档位集中定义（后续调参只改这里；非 bool，不走 runtime_flags）──
# 取值依据（AI_ONLY，2026-09-06）：纯 AI 空转局无真人等待，观测口径=50 次 LLM 调用足够
# 暴露"原地打转"（同动作重复序列 3/5 即触发收敛），总上限 60 为软限后 fallback 兜底余量；
# 与 NORMAL（500/450）保持同一套收敛结构，只缩阈值，不改状态机。
MAX_AI_DECISIONS_PER_SESSION = 500   # NORMAL：单局 AI 决策总数硬上限（≈LLM 调用上限）
AI_DECISION_SOFT_LIMIT = 450         # NORMAL：软上限：之后不再调 LLM，直接 fallback，并告警
SAME_ACTION_STREAK_LIMIT = 5         # NORMAL：连续 5 次同一结构化动作 → 第 1 级：强制换合法动作（fallback）
SAME_ACTION_HARD_LIMIT = 10          # NORMAL：连续 10 次仍推不动 → 第 3 级：止血

AI_ONLY_MAX_DECISIONS = 60           # AI_ONLY：总数硬上限（≈NORMAL 的 1/10+）
AI_ONLY_SOFT_LIMIT = 50              # AI_ONLY：软上限
AI_ONLY_STREAK_LIMIT = 3             # AI_ONLY：更快换招
AI_ONLY_HARD_LIMIT = 5               # AI_ONLY：更快止血

MODE_NORMAL = "normal"
MODE_AI_ONLY = "ai_only"


@dataclass(frozen=True)
class GuardTier:
    max_decisions: int
    soft: int
    streak: int
    hard: int


_TIERS: dict[str, GuardTier] = {
    MODE_NORMAL: GuardTier(MAX_AI_DECISIONS_PER_SESSION, AI_DECISION_SOFT_LIMIT,
                           SAME_ACTION_STREAK_LIMIT, SAME_ACTION_HARD_LIMIT),
    MODE_AI_ONLY: GuardTier(AI_ONLY_MAX_DECISIONS, AI_ONLY_SOFT_LIMIT,
                            AI_ONLY_STREAK_LIMIT, AI_ONLY_HARD_LIMIT),
}


def guard_tier(mode: str) -> GuardTier:
    """档位查询（未知 mode 保守回落 NORMAL）。"""
    return _TIERS.get(mode or MODE_NORMAL, _TIERS[MODE_NORMAL])


class GuardMove(str, Enum):
    NORMAL = "normal"               # 正常使用 LLM 决策
    FORCE_FALLBACK = "force_fb"     # 重复达限：丢弃 LLM 决策，强制换一个合法动作
    FORCE_ADVANCE = "force_adv"     # 换不动：强制推进阶段（timeout/advance）
    ABORT_DRAW = "abort_draw"       # 强推仍原地 / 总数超限：止血结束（平局或无胜负终止，由消费点分流）


def canonical_signature(round_no, phase: str, seat: int, action: str, payload: dict) -> tuple:
    """结构化动作签名。**刻意排除 content**：content 是 LLM 自然语言，每次措辞不同，
    纳入会让"卡在同一动作同一目标"被措辞变化绕过。只保留动作与结构化参数。"""
    pl = {k: v for k, v in (payload or {}).items() if k != "content"}
    norm = json.dumps(pl, ensure_ascii=False, sort_keys=True, default=str)
    return (int(round_no or 0), str(phase or ""), int(seat), str(action or ""), norm)


@dataclass
class SessionGuard:
    decisions: int = 0
    last_sig: tuple | None = None
    streak: int = 0
    did_force_fallback: bool = False   # 本轮重复序列是否已尝试过换目标
    did_force_advance: bool = False    # 是否已尝试过强推阶段
    advanced_at_rp: tuple | None = None  # 强推时的 (round, phase)，用于判断强推是否真的挪动了
    mode: str = MODE_NORMAL            # 阈值档位（口径1）：normal / ai_only，首次获取时判定固化
    mode_locked: bool = False          # 档位是否已固化（固化后局内不再重判，防逐轮漂移）

    def bump_decision(self) -> int:
        self.decisions += 1
        return self.decisions


# session_id -> SessionGuard（进程内；终局清理）
_REGISTRY: dict[int, SessionGuard] = {}


def get_guard(session_id: int) -> SessionGuard:
    g = _REGISTRY.get(session_id)
    if g is None:
        g = SessionGuard()
        _REGISTRY[session_id] = g
    return g


def set_guard_mode(session_id: int, ai_only: bool) -> SessionGuard:
    """判定并固化档位（口径1）：仅在未固化时生效一次；判定异常由调用方保守回落 ai_only=False。"""
    g = get_guard(session_id)
    if not g.mode_locked:
        g.mode = MODE_AI_ONLY if ai_only else MODE_NORMAL
        g.mode_locked = True
    return g


def drop_guard(session_id: int) -> None:
    _REGISTRY.pop(session_id, None)


def guard_before_llm(g: SessionGuard) -> GuardMove:
    """在“本轮已 bump_decision() 之后、即将调 ai_decide 之前”调用（阈值按档位）：
    - 达硬上限 → 止血（也挡住下一次 LLM 调用）；
    - 达软上限未到硬上限 → 不花 LLM 的钱、改走确定性 fallback，但决策仍计数（故最终仍能到硬上限）；
    - 其余 → 正常调 LLM。"""
    tier = guard_tier(g.mode)
    if g.decisions >= tier.max_decisions:
        return GuardMove.ABORT_DRAW
    if g.decisions >= tier.soft:
        return GuardMove.FORCE_FALLBACK  # 接近上限：不再花 LLM 的钱，直接走确定性兜底
    return GuardMove.NORMAL


def guard_after_signature(g: SessionGuard, sig: tuple, current_rp: tuple) -> GuardMove:
    """得到本轮结构化动作签名后调用，决定如何收敛（streak/hard 阈值按档位）。

    - 签名变化（回合/阶段/座位/动作/参数变了）→ 重复序列清零，NORMAL；
    - 连续重复到 STREAK_LIMIT：先 FORCE_FALLBACK 换合法目标；
    - 已换过仍重复：FORCE_ADVANCE 强推阶段；
    - 强推后 (round,phase) 仍停在原地、且累计到 HARD_LIMIT：ABORT_DRAW 止血。
    """
    tier = guard_tier(g.mode)
    if sig != g.last_sig:
        g.last_sig = sig
        g.streak = 1
        # 进入一个新的动作签名：若阶段已相对上次强推发生移动，复位强推标记
        if g.advanced_at_rp is not None and current_rp != g.advanced_at_rp:
            g.did_force_fallback = False
            g.did_force_advance = False
            g.advanced_at_rp = None
        return GuardMove.NORMAL

    g.streak += 1
    if g.streak >= tier.hard:
        return GuardMove.ABORT_DRAW
    if g.did_force_advance:
        # 已强推但签名仍重复：若阶段没挪动，很快到 HARD_LIMIT 止血；期间继续强推
        return GuardMove.FORCE_ADVANCE
    if g.streak >= tier.streak:
        if not g.did_force_fallback:
            g.did_force_fallback = True
            return GuardMove.FORCE_FALLBACK
        return GuardMove.FORCE_ADVANCE
    return GuardMove.NORMAL


def mark_forced_advance(g: SessionGuard, current_rp: tuple) -> None:
    g.did_force_advance = True
    g.advanced_at_rp = current_rp
