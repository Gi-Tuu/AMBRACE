# -*- coding: utf-8 -*-
"""主动接触意图层（纯决策，零 DB/IO）：闲置新鲜度分级 + 接触意图选择（AMBRACE B1-③）。

方案 §1-§9（豆包 → Codex 修订稿）：主动消息应在「正确时间」自然接触用户、经营关系，
而不是被迫承接上文/演 AI 自己的剧。本模块与 decision.py 同级、同风格：IO（取素材/读最近
意图/生成）留在 scheduling 层，本模块只做「给我分级和素材清单，我还你本次接触计划」的
纯计算，便于单测（flag ``proactive_outreach_v2`` 默认关 = 零行为）。

边界（写进 docstring 强约束）：本模块零 IO、不感知 DB/FastAPI；不 import 任何调度/模型模块。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

# ── 闲置新鲜度分级（方案 §3）──
TIER_CONTINUE = "continue"   # 刚分开 ≤2h
TIER_RECENT = "recent"       # 小别 2h–24h
TIER_STALE = "stale"         # 隔了一两天 24h–72h
TIER_COLD = "cold"           # 久违 >72h

CONTINUE_MAX_HOURS = 2.0
RECENT_MAX_HOURS = 24.0
STALE_MAX_HOURS = 72.0

# ── 接触意图（方案 §4）──
CHECK_IN = "check_in"          # 关心问候
SHARE_SELF = "share_self"      # 分享自己
RECALL_SHARED = "recall_shared"  # 回忆共同经历
FOLLOW_UP = "follow_up"        # 跟进未完成
INTEREST_HOOK = "interest_hook"  # 兴趣开新题
ALL_INTENTS = (CHECK_IN, SHARE_SELF, RECALL_SHARED, FOLLOW_UP, INTEREST_HOOK)

# 各意图对应的记忆检索 query（用户导向，而非 AI 状态；方案 §4 / §5.1）
MEMORY_QUERY_BY_INTENT = {
    INTEREST_HOOK: "用户的兴趣爱好偏好和喜欢的东西",
    RECALL_SHARED: "和用户一起经历的事、用户说过的重要事情、用户的近况",
    FOLLOW_UP: "用户的计划目标约定和未完成的事",
    CHECK_IN: "用户最近的状态心情和作息",
    SHARE_SELF: "",  # 分享自己不靠检索用户记忆
}

# 各分级下的基础权重（不含素材前提过滤）
TIER_WEIGHTS: dict[str, dict[str, float]] = {
    TIER_CONTINUE: {FOLLOW_UP: 3.0, SHARE_SELF: 3.0, CHECK_IN: 2.0, RECALL_SHARED: 1.0, INTEREST_HOOK: 1.0},
    TIER_RECENT:   {CHECK_IN: 3.0, SHARE_SELF: 3.0, INTEREST_HOOK: 2.0, RECALL_SHARED: 1.5, FOLLOW_UP: 1.5},
    TIER_STALE:    {CHECK_IN: 2.5, INTEREST_HOOK: 2.5, RECALL_SHARED: 2.0, SHARE_SELF: 2.0, FOLLOW_UP: 1.0},
    TIER_COLD:     {CHECK_IN: 3.0, INTEREST_HOOK: 3.0, SHARE_SELF: 2.0, RECALL_SHARED: 1.5, FOLLOW_UP: 1.0},
}
# cold 档：默认全新发起，唯一允许「续旧」的是真实未兑现承诺（FOLLOW_UP）；其余意图仍可用，
# 但它们对应的素材开关（allow_active_topics/allow_storyline）在 cold 下会被置 False，
# 只允许「回忆式」由头（RECALL_SHARED 走记忆，不延续剧情）。
COLD_ALLOWED = (CHECK_IN, INTEREST_HOOK, SHARE_SELF, RECALL_SHARED, FOLLOW_UP)
RECENT_AVOID = 2  # 避开最近 N 次已用意图（防"每次都一个路数"）


def staleness_tier(idle_minutes: int | None) -> str:
    """闲置分钟 → 新鲜度分级（纯函数，方案 §3）。

    None（未知闲置）视为 recent（不误判为久违导致全新发起过头）。
    边界：≤2h continue；≤24h recent；≤72h stale；>72h cold。
    """
    if idle_minutes is None:
        return TIER_RECENT
    h = idle_minutes / 60.0
    if h <= CONTINUE_MAX_HOURS:
        return TIER_CONTINUE
    if h <= RECENT_MAX_HOURS:
        return TIER_RECENT
    if h <= STALE_MAX_HOURS:
        return TIER_STALE
    return TIER_COLD


@dataclass(frozen=True)
class OutreachMaterials:
    """生成前收集到的真实素材（IO 层填，纯决策层只读）。全部 False/空=只有关心/分享可用。"""
    has_open_loop: bool = False       # 有未过期的目标/承诺/约定（fresh）
    has_shared_memory: bool = False   # 有合格的"用户相关/共同经历"记忆
    has_user_interest: bool = False   # 有用户兴趣/偏好记忆
    has_life_now: bool = False        # AI 此刻有正在发生的生活小事


@dataclass(frozen=True)
class OutreachPlan:
    """本次接触计划：意图 + 分级 + 给生成器的素材开关 + 记忆检索 query + 是否必须抛回问题。"""
    intent: str
    tier: str
    allow_active_topics: bool   # 是否允许注入"进行中话题"（续旧）
    allow_storyline: bool       # 是否允许注入 AI 剧情
    allow_recall: bool          # 是否允许翻旧（回忆/跟进）
    memory_query: str           # 本次记忆检索应用的 query（用户导向，而非 AI 状态）
    must_return_question: bool  # 是否必须把话头抛回用户


def _candidates(tier: str, m: OutreachMaterials) -> list[str]:
    """按素材前提筛掉本次不可用的意图（纯函数，方案 §4 / §5.1）。

    - 没未兑现承诺 → 剔除 FOLLOW_UP；没共同经历记忆 → 剔除 RECALL_SHARED；
      没用户兴趣记忆 → 剔除 INTEREST_HOOK；
    - SHARE_SELF 恒可用（可配 current_status/life），无生活素材也保留（方案 §4 表）；
    - cold 档走 COLD_ALLOWED 白名单（含全部意图，保留计划语义；真正"不续旧"由
      select_outreach 的素材开关落实）；
    - 兜底：永远能关心（CHECK_IN）。
    """
    out = list(TIER_WEIGHTS[tier].keys())
    if not m.has_open_loop:
        out = [x for x in out if x != FOLLOW_UP]
    if not m.has_shared_memory:
        out = [x for x in out if x != RECALL_SHARED]
    if not m.has_user_interest:
        out = [x for x in out if x != INTEREST_HOOK]
    if tier == TIER_COLD:
        out = [x for x in out if x in COLD_ALLOWED]
    if not out:  # 兜底：永远能关心
        out = [CHECK_IN]
    return out


def select_outreach(
    tier: str,
    materials: OutreachMaterials,
    recent_intents: list[str] | None = None,
    rng: random.Random | None = None,
) -> OutreachPlan:
    """选本次接触意图（纯函数，方案 §4 / §5.1）：权重 × 素材前提 × 避开最近意图。

    - 按 tier 给基础权重；再用素材前提把没素材的意图剔除；
    - 避开最近 ``RECENT_AVOID`` 次已用意图（都撞车则允许重复，保证有结果）；
    - 剩余候选按权重加权随机，保证自然不刻板；``rng`` 可注入以便单测确定性。
    """
    rng = rng or random.Random()
    recent = [x for x in (recent_intents or []) if x in ALL_INTENTS][:RECENT_AVOID]
    cands = _candidates(tier, materials)
    varied = [x for x in cands if x not in recent] or cands  # 都撞车则允许重复，保证有结果
    weights = [TIER_WEIGHTS[tier][x] for x in varied]
    intent = rng.choices(varied, weights=weights, k=1)[0]

    # 按意图 + 分级决定素材开关（方案 §4）
    allow_active_topics = intent == FOLLOW_UP and tier in (TIER_CONTINUE, TIER_RECENT)
    allow_storyline = intent == SHARE_SELF and tier == TIER_CONTINUE
    # 只有"回忆/跟进"才允许翻旧，且 stale 以上必须带时间锚点（由生成提示块落实）
    allow_recall = intent in (RECALL_SHARED, FOLLOW_UP)
    memory_query = MEMORY_QUERY_BY_INTENT.get(intent, "")
    must_return_question = intent in (CHECK_IN, FOLLOW_UP, INTEREST_HOOK, RECALL_SHARED, SHARE_SELF)
    return OutreachPlan(
        intent=intent,
        tier=tier,
        allow_active_topics=allow_active_topics,
        allow_storyline=allow_storyline,
        allow_recall=allow_recall,
        memory_query=memory_query,
        must_return_question=must_return_question,
    )
