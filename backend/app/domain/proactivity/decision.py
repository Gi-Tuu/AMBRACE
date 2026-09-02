"""proactivity 决策层（F2-b，2026-08-31）：离线主动行为的**纯决策**——限额常量与打分函数。

自 arbiter.py 原样搬移；arbiter 保留同名重导出（兼容面与 monkeypatch 接缝不变）。
边界（写进 docstring 强约束）：本模块零 IO、不感知 DB/FastAPI；IO（查询/发送/限额执行）
留在 scheduling/arbiter.py，后续整体迁入本包（scheduling 只管到点触发）。
"""
# 防刷屏：每角色每小时最多 N 条主动消息（随机+定时+节日共同计入）
MAX_PER_HOUR = 2
# 主动搭话最小间隔（分钟）：同角色两次主动消息至少间隔这么久，避免短时间内连发/重复
MIN_PROACTIVE_INTERVAL_MINUTES = 90
# 用户活跃判定：最近 N 分钟内用户发过消息则暂停主动行为
USER_ACTIVE_MINUTES = 10
# 连续不回复冷却：最近 N 条主动消息用户均未回复 → 暂停主动搭话（防骚扰，2026-08-12）
UNREPLIED_COOLDOWN_LIMIT = 2
UNREPLIED_COOLDOWN_HOURS = 24
# 情感渴望驱动的主动唤醒（2026-08-15）：渴望度 >= 阈值才生成主动搭话候选（按本项目 0-1 数值校准）
MOTIVATION_SPEAK_THRESHOLD = 0.60
# 「渴望+反思」双驱动（plans #41 ②，2026-08-16）：最近一周有复盘的角色，渴望分加成（心里有事想聊）
# 加成固定 0.08（不随复盘内容变化，只把"有复盘"作为可聊信号），仍受限额/冷却/免打扰约束
REFLECTION_BONUS = 0.08
REFLECTION_LOOKBACK_DAYS = 7
# 独立想念通道（#33，2026-08-17）：渴望驱动主动消息独立配额——每 6h 最多 1 条 + 每日 ≤2 条，
# 不占普通每小时 2 条额度（观察期 motivation approved=0，全被普通限额拦截）；仍受连续不回复 24h 冷却约束
MOTIVATION_MAX_PER_6H = 1
MOTIVATION_MAX_PER_DAY = 2
# P0-2（2026-08-24）：排序加权——候选带最近聊天语境（last_context 非空）时动机分轻微加权，
# 让「刚有聊天」的承接类消息更容易优先，避免高渴望总出无语境消息；改动小、可回退
CONTEXT_SORT_BONUS = 0.05


# 北京时间 21 点后，用户说过"睡觉"则当天主动交流提前关闭
SLEEP_HOUR = 21
# 仅明确"要去睡/已睡"意图才触发当晚静默（去掉"困了/休息了/困死"等易误伤的非入睡表达）
SLEEP_KEYWORDS = ("睡觉", "睡了", "晚安", "要睡了", "先睡了", "去睡了", "睡啦", "睡觉了", "睡了哦", "睡吧", "去睡觉", "我先睡", "睡觉去", "睡了哈")



def _in_dnd_window(cn_minute: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= cn_minute < end
    return cn_minute >= start or cn_minute < end  # 跨天时段（如 23:00-08:00）


def _motivation_score(
    attachment: float, curiosity: float, desire: float,
    mood: float, anger: float, fatigue: float, hours_since_activity: float,
) -> float:
    """渴望度（0-1）纯计算：依恋/好奇/亲密欲望/情绪低落累积 + 久未互动加成，疲惫抑制。
    对「思念/好奇/亲密渴望/情绪低落」加权，久未互动累积、疲惫抑制；复用 character_states 已有维度（数值 0-100）。
    时间因子：2 小时后线性累积，24 小时满。"""
    attachment = max(0.0, min(1.0, attachment / 100.0))
    curiosity = max(0.0, min(1.0, curiosity / 100.0))
    desire = max(0.0, min(1.0, desire / 100.0))
    anger_n = max(0.0, min(1.0, anger / 100.0))
    sadness = max(0.0, (50.0 - mood) / 50.0) * (1.0 - 0.5 * anger_n)
    fatigue = max(0.0, min(1.0, fatigue / 100.0))
    time_factor = min(1.0, max(0.0, (hours_since_activity - 2.0) / 22.0))
    base = (
        0.35 * attachment + 0.22 * curiosity
        + 0.13 * desire + 0.15 * sadness
    )
    return max(0.0, min(1.0, (base + 0.25 * time_factor) * (1.0 - 0.35 * fatigue)))


def _apply_reflection_bonus(score: float, has_reflection: bool) -> float:
    """「渴望+反思」双驱动加分（plans #41 ②，2026-08-16）：最近一周有复盘的角色渴望分加成。

    纯函数便于测试：score 0-1，有复盘 +REFLECTION_BONUS 且封顶 1.0；无复盘/score=0 不变。
    """
    if score <= 0.0 or not has_reflection:
        return max(0.0, min(1.0, score))
    return max(0.0, min(1.0, score + REFLECTION_BONUS))


def _context_sort_bonus(candidate: dict | None) -> float:
    """P0-2（2026-08-24）：排序加权纯函数——候选带最近聊天语境（last_context 非空）时动机分 +CONTEXT_SORT_BONUS。

    让「刚有聊天」的承接类消息（节律/motivation 且已注入最近语境）在同优先级下更容易优先，
    避免高渴望但无语境的消息抢占；纯函数便于测试，无候选/无语境返回 0。"""
    if not candidate:
        return 0.0
    if candidate.get("last_context"):
        return CONTEXT_SORT_BONUS
    return 0.0


def scheduler_gray_character(character_id: int) -> bool:
    """Phase D 10% 角色灰度：按角色 id 取模，稳定分桶（同一角色始终同组，便于对比）"""
    return (int(character_id) % 10) == 0
