"""对话话题追踪（认知架构 v2.1 Conversation State）：本地提取 + 节流 + 注入。

- 只对高重要度话题建档（importance >= TOPIC_MIN_IMPORTANCE=0.6）
- 本地规则提取候选话题（零 LLM），节流防重复（同角色 5 分钟最多 1 次）
- load_active_topics_text 供 context_builder 注入进行中话题
"""
import re
import time

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import ConversationTopic
from app.utils.logger import get_logger

_logger = get_logger("agent.topic_tracker")

TOPIC_MIN_IMPORTANCE = 0.6
THROTTLE_SECONDS = 300          # 同角色 5 分钟最多提取一次
MAX_TOPICS_PER_CHAR = 20        # 每角色最多保留话题数（超出按重要度裁剪）
MAX_INJECT_TOPICS = 3           # 注入上下文最多条数

# B1-③（2026-09-04，方案 §5.2）：主动接触专用「时效」边界——过期话题不再当承接对象
PROACTIVE_FRESH_TOPIC_HOURS = 72      # 普通话题 72h 后不再主动承接（修复远期话题被反复续）
PROACTIVE_GOAL_MAX_DAYS = 14          # 目标类放宽到 14 天（目标本就长期），且显式标注为目标跟进

# 候选话题提取模式：动作+目标（限定长度，避免整句误提取）
_TOPIC_PATTERNS = [
    re.compile(r"(?:我|我们)(?:打算|准备|计划|想)[^，。！？!?,;；]{0,12}(?:参加|去|做|学|写|考|买|开始|尝试|报)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:我要|打算|准备|计划|想)(?:参加|去|做|学|写|考|买|开始|尝试|报)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:我(?:在|正在)(?:追|看|玩|学|做|读)([^，。！？!?,;；\s]{2,14}))"),
    re.compile(r"(?:下周|明天|后天|这个周末|周末|月底|下个月|周[一二三四五六日])(?:要|去|参加|做|考|交|开始|回来)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:\d+月\d+日|\d+号)(?:要|去|参加|考|交|办)([^，。！？!?,;；\s]{2,14})"),
]
_CLEAN_RE = re.compile(r"[的了呢吧吗啊呀嘛哈]$")

# 目标记忆（v2.1 Phase 3a）：长期目标类表达 → 话题标 goal=true
_GOAL_PATTERNS = [
    re.compile(r"(?:我的|我)(?:目标是|梦想是|愿望是|理想是)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:我)?(?:一直想|一直想做|一直想要|一直打算)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:我想实现|我想完成|计划实现|梦想做)([^，。！？!?,;；\s]{2,14})"),
    re.compile(r"(?:我要|想)成为([^，。！？!?,;；\s]{2,14})"),
]

# 感知话题命中时的重要度加成（感知层 topic 非 other 时给 0.8）
_IMPORTANCE_BY_HIT = {True: 0.8, False: 0.6}

_throttle: dict[int, float] = {}


def _extract_candidates(text: str) -> list[tuple[str, bool]]:
    """本地规则提取候选话题（去重、清洗助词）。返回 [(话题, 是否目标), ...]"""
    cands: list[tuple[str, bool]] = []
    for pat in _TOPIC_PATTERNS:
        for m in pat.finditer(text or ""):
            t = _CLEAN_RE.sub("", m.group(1).strip())
            if 2 <= len(t) <= 14 and t not in [c[0] for c in cands]:
                cands.append((t, False))
    for pat in _GOAL_PATTERNS:
        for m in pat.finditer(text or ""):
            t = _CLEAN_RE.sub("", m.group(1).strip())
            if 2 <= len(t) <= 14 and t not in [c[0] for c in cands]:
                cands.append((t, True))
    return cands


def _overlap(a: str, b: str) -> bool:
    """话题相似判断：包含关系或公共子串 >= 4 字"""
    if a in b or b in a:
        return True
    for i in range(len(a) - 3):
        if a[i:i + 4] in b:
            return True
    return False


async def maybe_extract_topics(
    character_id: int,
    user_id: int,
    user_msg: str,
    ai_response: str,
    perception: dict | None = None,
) -> None:
    """从用户消息中提取候选话题并建档/更新（节流 + 高重要度过滤；失败静默）"""
    try:
        now = time.time()
        if now - _throttle.get(character_id, 0) < THROTTLE_SECONDS:
            return
        cands = _extract_candidates(user_msg)
        if not cands:
            # AI 回复兜底提取：仅用户消息无候选时，且排除"啥/什么"开头噪声（如"啥主题"）
            cands = [(c, g) for c, g in _extract_candidates(ai_response)
                     if not (c.startswith("啥") or c.startswith("什么"))]
        if not cands:
            return
        # 记忆架构 v2.1：目标类提取仅开关开启时生效（普通话题属认知 v2.1 全量开放，不受限）
        try:
            from app.models.character import AICharacter
            async with async_session_factory() as _db:
                _flag = (await _db.execute(
                    select(AICharacter.memory_v2_enabled).where(AICharacter.id == character_id)
                )).scalar_one_or_none()
            if not _flag:
                cands = [(c, g) for c, g in cands if not g]
                if not cands:
                    return
        except Exception:
            pass
        _throttle[character_id] = now

        hit_topic = bool(perception and perception.get("topic") not in (None, "", "other"))
        importance = _IMPORTANCE_BY_HIT[hit_topic]
        if len(cands) > 1:
            importance = min(0.9, importance + 0.1)  # 多候选说明话题性强

        from app.utils.timeutil import now_naive_utc as _now
        now_dt = _now()
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
                .order_by(ConversationTopic.importance.desc())
            )).scalars().all()
            existing = [r for r in rows if any(_overlap(r.topic, c) for c, _g in cands)]
            if existing:
                # 已有相似话题：刷新时间与重要度（重复提及视为更值得跟）；目标类升级标记
                for r in existing[:1]:
                    r.last_touched_at = now_dt
                    r.importance = min(0.95, float(r.importance or TOPIC_MIN_IMPORTANCE) + 0.1)
                    if any(g for c, g in cands if _overlap(r.topic, c)):
                        r.goal = True
                        r.progress = r.progress or "进行中"
                await db.commit()
                return
            if len(rows) >= MAX_TOPICS_PER_CHAR:
                return
            for c, is_goal in cands[:2]:
                db.add(ConversationTopic(
                    character_id=character_id,
                    user_id=user_id,
                    topic=c,
                    status="进行中",
                    importance=importance,
                    goal=is_goal,
                    progress="进行中" if is_goal else None,
                ))
            await db.commit()
            _logger.info("Topics extracted: char=%d cands=%s", character_id, cands[:2])
    except Exception as e:
        _logger.warning("Topic extraction failed: %s", e)


# 话题状态自动切换词表：完成 / 搁置（本地零 LLM，命中后把进行中话题标记为对应状态）
_COMPLETION_WORDS = (
    "弄好了", "成功了", "解决了", "办完了", "结束了", "拿到了", "通过了",
    "装好了", "写完了", "搞定了", "做完了", "完成了", "修好了", "考完了",
)
_ABANDON_WORDS = (
    "算了", "不弄了", "放弃了", "先放放", "不参加了", "没时间", "不做了",
    "不搞了", "不想弄了", "先算了", "不学了", "不考了",
)


async def update_topic_resolution(character_id: int, user_id: int, user_msg: str) -> None:
    """用户消息含完成/搁置类词时，自动切换进行中话题状态（失败静默）。

    匹配：消息中直接提到话题词或与话题重叠；完成类词无匹配时兜底最近一条
    （用户省略话题的语境，如只回"弄好了"）。
    """
    try:
        if not user_msg:
            return
        kind = None
        if any(w in user_msg for w in _COMPLETION_WORDS):
            kind = "完成"
        elif any(w in user_msg for w in _ABANDON_WORDS):
            kind = "搁置"
        if not kind:
            return
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
                .order_by(ConversationTopic.last_touched_at.desc())
            )).scalars().all()
            if not rows:
                return
            matched = [r for r in rows if r.topic in user_msg or _overlap(r.topic, user_msg)]
            targets = matched if matched else (rows[:1] if kind == "完成" else [])
            changed = False
            for r in targets:
                r.status = kind
                changed = True
            if changed:
                await db.commit()
                _logger.info("Topics resolved(%s): char=%d targets=%s", kind, character_id,
                             [r.topic for r in targets])
    except Exception as e:
        _logger.warning("Topic resolution failed: %s", e)


async def load_active_goal_queries(character_id: int, user_id: int, limit: int = 3) -> list[str]:
    """目标/未完成路查询（记忆架构 v2.1 Phase 4a）：进行中目标话题 + follow_up 未完成话题文本。

    供记忆多路召回作为额外查询路，让"进行中目标/未完成事项"相关记忆更易被召回。
    """
    try:
        # 记忆架构 v2.1：目标/未完成路检索仅开关开启时生效（关闭=现状链路）
        from app.models.character import AICharacter
        async with async_session_factory() as _db:
            _flag = (await _db.execute(
                select(AICharacter.memory_v2_enabled).where(AICharacter.id == character_id)
            )).scalar_one_or_none()
        if not _flag:
            return []
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.user_id == user_id,
                    ConversationTopic.status == "进行中",
                )
                .order_by(ConversationTopic.importance.desc(), ConversationTopic.last_touched_at.desc())
                .limit(limit)
            )).scalars().all()
        return [r.topic for r in rows if r and r.topic]
    except Exception as e:
        _logger.warning("Active goal queries load failed: %s", e)
        return []


async def load_active_topics_text(character_id: int, user_id: int) -> str:
    """注入文本：进行中话题 top3（带最后提及时间相对描述）"""
    try:
        from datetime import datetime, timezone
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
                .order_by(ConversationTopic.importance.desc(), ConversationTopic.last_touched_at.desc())
                .limit(MAX_INJECT_TOPICS)
            )).scalars().all()
        if not rows:
            return ""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lines = []
        for r in rows:
            last = r.last_touched_at
            last = last.replace(tzinfo=None) if last and last.tzinfo else last
            when = ""
            if last is not None:
                hours = (now - last).total_seconds() / 3600.0
                when = "（今天聊到的）" if hours < 24 else ("（前几天聊到的）" if hours < 72 else "（之前聊到的）")
            mark = "🎯" if r.goal else ""
            lines.append(f"- {mark}{r.topic}{when}")
        return "\n".join(lines)
    except Exception as e:
        _logger.warning("Active topics load failed: %s", e)
        return ""


async def load_fresh_active_topics_text(character_id: int, user_id: int) -> str:
    """B1-③（2026-09-04，方案 §5.2）主动接触专用：仅返回时效内的进行中话题。

    与主聊天 ``load_active_topics_text`` 不同，本函数给进行中话题加时效治理：
    - 普通话题超过 ``PROACTIVE_FRESH_TOPIC_HOURS``（72h）不再主动承接；
    - 目标类（goal）放宽到 ``PROACTIVE_GOAL_MAX_DAYS``（14 天），仍显式标注为「目标」。
    过期的不当作承接对象（修复远期话题被反复续的根因）；主聊天原函数不动。
    仅供主动接触链路调用；任何异常失败静默返回空串。
    """
    try:
        from datetime import datetime, timezone
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(ConversationTopic)
                .where(
                    ConversationTopic.character_id == character_id,
                    ConversationTopic.status == "进行中",
                )
                .order_by(ConversationTopic.importance.desc(),
                          ConversationTopic.last_touched_at.desc())
            )).scalars().all()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lines = []
        for r in rows:
            last = r.last_touched_at
            last = last.replace(tzinfo=None) if last and last.tzinfo else last
            if last is None:
                continue
            age_h = (now - last).total_seconds() / 3600.0
            max_h = PROACTIVE_GOAL_MAX_DAYS * 24 if r.goal else PROACTIVE_FRESH_TOPIC_HOURS
            if age_h > max_h:
                continue  # 过期：不主动续
            when = ("（今天聊到的）" if age_h < 24 else
                    "（前几天聊到的）" if age_h < 72 else "（你之前定下的目标）")
            mark = "🎯" if r.goal else ""
            lines.append(f"- {mark}{r.topic}{when}")
        return "\n".join(lines[:MAX_INJECT_TOPICS])
    except Exception as e:
        _logger.warning("Fresh active topics load failed: %s", e)
        return ""