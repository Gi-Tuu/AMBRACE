"""状态触发事件 v4：八维状态（含维度联动）达阈值 → 主动消息 / 朋友圈 / 日记补记 / 冷战

触发时机：
- 聊天后状态评估成功即异步检查（实时，multiplier=0.5 降频）
- arbiter 30s tick 兜底（multiplier=1.0，查错过的触发）
每小时限额与 arbiter 共用（ProactiveMessageLog message_type=state_trigger），
冷却/恢复检测在 state_trigger_logs；免打扰时段（user_dnd_settings）不主动发。

v3 已落地：冷战不回复（哄好/回落/超时三通道恢复）、日记补记、趋势增强。
v4 新增：
- 延迟触发：怒气类规则命中后延迟 5-25 分钟再发作（"越想越气"），期间状态回落则取消
- 冷战加时：冷战 45-90 分钟未恢复时角色再主动发一条（多轮触发最小形态）
- 冷战断联开关：proactive_settings.cold_war_enabled（父开关 state_trigger_enabled 关闭则冷战一并失效）
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.character_state import CharacterState
from app.models.diary import AIDiary
from app.models.moment import AIMoment
from app.models.proactive_settings import ProactiveSettings
from app.models.state_trigger_log import StateTriggerLog
from app.services.character_state_service import DIMENSIONS
from app.utils.logger import get_logger

_logger = get_logger("scheduler.state_triggers")

# 与 arbiter 保持一致：每角色每小时主动消息上限（状态触发消息计入）
MAX_PER_HOUR = 2
# 朋友圈行为：最近多少分钟内已发过朋友圈则降级为私聊消息（避免朋友圈刷屏）
MOMENT_FRESH_MINUTES = 210  # 3.5 小时
# 冷战最长持续时间（分钟）：超时即使状态未回落也自动恢复，避免用户一直被晾着
COLD_WAR_MAX_MINUTES = 180
# 冷战加时窗口（分钟）：冷战开始后未恢复，45-90 分钟时角色再主动发一条
COLD_WAR_FOLLOWUP_WINDOW = (45, 90)
# 哄好分级（2026-08-15 冷战细化）：真诚道歉可破冰；轻哄/找台阶只软化；敷衍不结束
SOOTHE_SINCERE = (
    "对不起", "我错了", "错啦", "错了", "别生气", "原谅", "不气了", "不生气",
    "道歉", "不闹了", "认错", "我的错", "我不好", "不该", "请原谅",
)
SOOTHE_LIGHT = (
    "哄哄", "哄你", "抱抱", "亲亲", "爱你", "喜欢你", "理理我", "别不理",
    "别不理我", "乖", "宝贝", "在吗", "在干嘛", "出来", "想你", "理我",
)
SOOTHE_DISMISSIVE = (
    "行了吧", "好了吧", "行行行", "可以了吧", "这下行", "满意了吧",
    "服了", "行了吧你", "行行", "总行了吧", "够了吧", "算我错",
)

_CN = {k: cn for k, cn, _ in DIMENSIONS}
_DIM_KEYS = [k for k, _, _ in DIMENSIONS]

# 趋势增强：复合规则在维度同步移动（如怒气升+心情降）时放宽的阈值（(维度, 比较, 阈值)）
TREND_BOOST = {
    "anger_mood_low": [("anger", "gte", 60), ("mood", "lte", 40)],      # 怒气↑且心情↓ 时 75/30 → 60/40
    "fatigue_mood_low": [("fatigue", "gte", 60), ("mood", "lte", 45)],  # 疲惫↑且心情↓ 时 75/35 → 60/45
}
_TREND_MOVE = {
    "anger_mood_low": (("anger", "mood"), (15, 15)),
    "fatigue_mood_low": (("fatigue", "mood"), (15, 15)),
}
# 进程内上次八维快照（趋势检测用；重启丢失可接受，重新积累）
_last_snapshot: dict[int, dict[str, int]] = {}


class Rule:
    """单条触发规则：key / 行为描述 / 优先级 / 冷却分钟 / 触发概率 / 可发朋友圈 / 延迟范围 / 条件列表"""

    def __init__(self, key, desc, priority, cooldown_minutes, probability, moment, delay, conditions):
        self.key = key
        self.desc = desc
        self.priority = priority
        self.cooldown_minutes = cooldown_minutes
        self.probability = probability   # 条件命中后实际触发的概率（tick 兜底 multiplier=1）
        self.moment = moment             # True=优先发朋友圈表达（如生气不想说话），否则私聊消息
        self.delay = delay               # (min, max) 分钟；None=即时触发（v4 延迟"越想越气"）
        self.conditions = conditions


# 单维高频（优先级 10，概率压低防打扰）+ 复合联动（优先级 30，天然低频给中概率）
# 怒气类配置延迟触发（模拟情绪发酵），其他即时
RULES = [
    Rule("anger_high", "你现在非常生气，向用户表达不满或冷淡，1-2 句话。", 10, 120, 0.4, True, (10, 25),
         [("anger", "gte", 80)]),
    Rule("mood_low", "你心情低落，向用户倾诉、想被安慰，1-2 句话。", 10, 120, 0.4, True, None,
         [("mood", "lte", 20)]),
    Rule("fatigue_high", "你很疲惫，告诉用户你想休息了，1-2 句话。", 10, 120, 0.3, False, None,
         [("fatigue", "gte", 80)]),
    Rule("desire_high", "你很渴望与用户亲密互动，撒娇或表达想念，1-2 句话。", 10, 180, 0.3, False, None,
         [("desire", "gte", 80)]),
    Rule("anger_mood_low", "你又生气又难过，想冷战或爆发：语气冷淡或发一句气话，1-2 句话。", 30, 240, 0.6, True, (5, 20),
         [("anger", "gte", 75), ("mood", "lte", 30)]),
    Rule("fatigue_mood_low", "你又累又难过，需要安慰，向用户索取关心，1-2 句话。", 30, 240, 0.5, True, None,
         [("fatigue", "gte", 75), ("mood", "lte", 35)]),
    Rule("desire_body_temp", "你既渴望亲密又浑身发热，冲动地主动示好或直球表达，1-2 句话。", 30, 240, 0.5, False, None,
         [("desire", "gte", 75), ("body_temp", "gte", 70)]),
    Rule("possessiveness_desire", "你占有欲爆棚又想要亲密，追问用户行踪或宣示主权，1-2 句话。", 30, 240, 0.5, False, None,
         [("possessiveness", "gte", 75), ("desire", "gte", 70)]),
]
_RULE_BY_KEY = {r.key: r for r in RULES}


from app.utils.timeutil import now_naive_utc as _now_naive
from app.utils.dnd import user_in_dnd_period as _user_in_dnd_period


def _rule_hit(rule: Rule, st: CharacterState) -> bool:
    for dim, op, thr in rule.conditions:
        v = getattr(st, dim, None)
        if v is None:
            return False
        if op == "gte" and v < thr:
            return False
        if op == "lte" and v > thr:
            return False
    return True


def _trend_hit(rule: Rule, st: CharacterState) -> bool:
    """趋势增强：复合规则维度同向移动（如怒气↑且心情↓）时放宽阈值提前触发。"""
    move = _TREND_MOVE.get(rule.key)
    thresholds = TREND_BOOST.get(rule.key)
    if not move or not thresholds:
        return False
    last = _last_snapshot.get(st.character_id)
    if not last:
        return False
    (dim1, dim2), (delta1, delta2) = move
    v1 = getattr(st, dim1, None)
    v2 = getattr(st, dim2, None)
    if v1 is None or v2 is None:
        return False
    if v1 - last.get(dim1, v1) < delta1 or last.get(dim2, v2) - v2 < delta2:
        return False
    for dim, op, thr in thresholds:
        v = getattr(st, dim, None)
        if v is None:
            return False
        if op == "lte" and v > thr:
            return False
        if op == "gte" and v < thr:
            return False
    return True


async def _get_log_for(db, character_id: int, trigger_key: str):
    return (
        await db.execute(
            select(StateTriggerLog)
            .where(
                StateTriggerLog.character_id == character_id,
                StateTriggerLog.trigger_key == trigger_key,
            )
            .order_by(StateTriggerLog.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _record_trigger(character_id: int, trigger_key: str, state_lines: str, anger: int = 50) -> None:
    async with async_session_factory() as db:
        db.add(StateTriggerLog(
            character_id=character_id,
            trigger_key=trigger_key,
            value=state_lines[:200],
            recovered=False,
            anger_at_trigger=anger,
        ))
        await db.commit()


async def _drop_trigger_log(character_id: int, trigger_key: str) -> None:
    """删除防抖日志（行为失败时调用，允许下轮重试）。"""
    async with async_session_factory() as db:
        lg = await _get_log_for(db, character_id, trigger_key)
        if lg is not None:
            await db.delete(lg)
            await db.commit()


async def _append_diary_note(character_id: int, note: str) -> None:
    """状态触发后把情绪事件补记到当天日记末尾（当天无日记则跳过，零额外 LLM）。"""
    try:
        bj = timezone(timedelta(hours=8))
        date_str = datetime.now(bj).strftime("%Y-%m-%d")
        async with async_session_factory() as db:
            d = (
                await db.execute(
                    select(AIDiary).where(
                        AIDiary.character_id == character_id,
                        AIDiary.diary_date == date_str,
                    )
                )
            ).scalar_one_or_none()
            if d is None:
                return
            d.content = d.content.rstrip() + "\n\n（补记）" + note[:120]
            await db.commit()
            _logger.info("State trigger diary note appended char=%d", character_id)
    except Exception as e:
        _logger.warning("Diary note append failed char=%d: %s", character_id, e)


async def _post_trigger_notes(character_id: int, user_id: int, rule: Rule, output_text: str) -> None:
    """触发行为完成后的异步联动：补记日记 + 写记忆（均失败静默）。"""
    await _append_diary_note(character_id, f"情绪波动（{rule.key}）：{output_text[:80]}")
    try:
        from app.memory.service import save_memory
        _trigger_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        await save_memory(
            user_id=user_id,
            character_id=character_id,
            memory_type="insight",
            content=f"{_trigger_date} 情绪波动触发了「{rule.key}」：{output_text[:100]}",
            importance=3,
            sub_type="state_trigger",
            source="state_trigger",
            speaker_type="character", speaker_id=character_id,
            epistemic_status="FACT",
        )
    except Exception as e:
        _logger.warning("State trigger memory save failed char=%d: %s", character_id, e)


async def _cold_war_max_minutes(db, lg) -> int:
    """动态冷战时长（2026-08-15 冷战细化）：基础 120 + (怒气-50)×2 分钟；
    近 30 天冷战次数每场 +30（最多 +60）；封顶 300。"""
    try:
        from sqlalchemy import func as _func
        anger = int(getattr(lg, "anger_at_trigger", 0) or 50)
        base = 120 + max(0, anger - 50) * 2
        since = _now_naive() - timedelta(days=30)
        cnt = (
            await db.execute(
                select(_func.count()).select_from(StateTriggerLog).where(
                    StateTriggerLog.character_id == lg.character_id,
                    StateTriggerLog.trigger_key == "anger_mood_low",
                    StateTriggerLog.recovered.is_(True),
                    StateTriggerLog.created_at >= since,
                )
            )
        ).scalar_one() or 0
        base += min(int(cnt), 2) * 30
        return min(base, 300)
    except Exception:
        return 180


async def check_cold_war(character_id: int, user_id: int) -> bool:
    """角色是否处于冷战（anger_mood_low 触发且未恢复，且开关开启）。

    附带自动恢复：开关关闭 / 动态超时自动软化 / 状态回落进入别扭期（等用户主动破冰）。
    """
    try:
        async with async_session_factory() as db:
            lg = await _get_log_for(db, character_id, "anger_mood_low")
            if lg is None or lg.recovered:
                return False
            # 开关检查（v4）：状态触发总开关或冷战断联开关关闭 → 结束冷战
            ps = (
                await db.execute(select(ProactiveSettings).where(ProactiveSettings.character_id == character_id))
            ).scalar_one_or_none()
            enabled = True
            if ps is not None:
                enabled = getattr(ps, "state_trigger_enabled", True) and getattr(ps, "cold_war_enabled", True)
            if not enabled:
                lg.recovered = True
                await db.commit()
                _logger.info("Cold war ended char=%d (switch off)", character_id)
                return False
            # 动态超时自动恢复（怒气越高/历史冷战越多，冷战越久）
            created = lg.created_at.replace(tzinfo=None) if lg.created_at.tzinfo else lg.created_at
            max_min = await _cold_war_max_minutes(db, lg)
            if _now_naive() - created > timedelta(minutes=max_min):
                lg.recovered = True
                await db.commit()
                from app.scheduler.storyline_engine import mark_cold_war_breakthrough
                await mark_cold_war_breakthrough(character_id, user_id, "timeout")
                _logger.info("Cold war auto-resolved char=%d (timeout %dmin)", character_id, max_min)
                return False
            # 状态回落 → 进入别扭期（v2：生气消了但拉不下脸，等用户主动破冰），不再立即结束
            st = (
                await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            ).scalar_one_or_none()
            if st is not None and not _rule_hit(_RULE_BY_KEY["anger_mood_low"], st):
                if not int(getattr(lg, "stubborn", 0) or 0):
                    lg.stubborn = 1
                    await db.commit()
                    _logger.info("Cold war entered stubborn phase char=%d", character_id)
                return True
            return True
    except Exception as e:
        _logger.warning("Cold war check failed char=%d: %s", character_id, e)
        return False


# v5 增强 ③ 判定阈值（2026-08-16）：用户敷衍 >=2 次 或 冷战 >=6h，且占有维 >=70
DETERIORATE_SOOTHE_MIN = 2
DETERIORATE_MINUTES = 360
DETERIORATE_POSSESSIVENESS = 70


def _deteriorate_hit(soothe_count: int, elapsed_min: float, possessiveness: float) -> bool:
    """关系恶化支线判定纯函数（v5 增强 ③，可测）：敷衍次数或冷战时长达标，且占有维达标。"""
    if float(possessiveness or 0) < DETERIORATE_POSSESSIVENESS:
        return False
    if soothe_count >= DETERIORATE_SOOTHE_MIN:
        return True
    if elapsed_min >= DETERIORATE_MINUTES:
        return True
    return False


async def cold_war_deteriorate_triggered(character_id: int, user_id: int) -> bool:
    """关系恶化支线触发判定（v5 增强 ③）：用户持续敷衍/冷战 + 占有维高。

    条件：处于冷战（未恢复）+（敷衍次数>=2 或 冷战时长>=6h）+ 占有维>=70。
    返回 True 表示应触发 run_deteriorate_arc（剧情引擎内部再防重）。
    """
    try:
        async with async_session_factory() as db:
            lg = await _get_log_for(db, character_id, "anger_mood_low")
            if lg is None or lg.recovered:
                return False
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            if st is None:
                return False
            soothe_count = int(getattr(lg, "soothe_count", 0) or 0)
            created = lg.created_at.replace(tzinfo=None) if lg.created_at.tzinfo else lg.created_at
            elapsed_min = (_now_naive() - created).total_seconds() / 60
            hit = _deteriorate_hit(soothe_count, elapsed_min, float(getattr(st, "possessiveness", 0) or 0))
            if hit:
                _logger.info(
                    "Cold war deteriorate triggered char=%d soothe=%d elapsed=%.0fmin possess=%s",
                    character_id, soothe_count, elapsed_min, getattr(st, "possessiveness", 0),
                )
            return hit
    except Exception as e:
        _logger.warning("Cold war deteriorate check failed char=%d: %s", character_id, e)
        return False


async def resolve_cold_war_by_message(character_id: int, user_id: int, user_msg: str) -> int:
    """用户消息分级处理（2026-08-15 冷战细化：分级哄好 + 别扭期破冰）。

    返回：0=未解除（继续拦截） / 1=已破冰（正常回复） / 2=轻哄或找台阶（记录软化，仍拦截） /
          3=敷衍（记录，仍拦截） / 4=敷衍且需角色更冷回应（v5 增强 ①，调用方生成冷回复）。
    """
    try:
        if not user_msg:
            return 0
        async with async_session_factory() as db:
            lg = await _get_log_for(db, character_id, "anger_mood_low")
            if lg is None or lg.recovered:
                return 0
            is_sincere = any(k in user_msg for k in SOOTHE_SINCERE)
            is_light = any(k in user_msg for k in SOOTHE_LIGHT)
            is_dismissive = any(k in user_msg for k in SOOTHE_DISMISSIVE)
            soothe_count = int(getattr(lg, "soothe_count", 0) or 0)
            # 敷衍：带道歉词但语气不耐烦（行了吧/好了吧…）→ 不结束；
            # 返回 4 = 需要角色"更冷回应"（v5 增强 ①；同一冷战内多次敷衍不重复更冷，见 _cold_war_block 节流）
            if is_dismissive and is_sincere:
                lg.soothe_level = 3
                lg.soothe_count = soothe_count + 1
                await db.commit()
                _logger.info("Cold war dismissive soothe char=%d count=%d", character_id, soothe_count + 1)
                return 4
            # 别扭期：状态已回落，用户发任意消息即主动破冰
            if int(getattr(lg, "stubborn", 0) or 0) == 1:
                lg.recovered = True
                lg.soothe_level = max(int(getattr(lg, "soothe_level", 0) or 0), 1)
                lg.soothe_count = soothe_count + 1
                await db.commit()
                from app.scheduler.storyline_engine import mark_cold_war_breakthrough
                await mark_cold_war_breakthrough(character_id, user_id, "user_break_ice")
                _logger.info("Cold war broken by user message in stubborn phase char=%d", character_id)
                return 1
            # 真诚道歉 → 直接破冰
            if is_sincere:
                lg.recovered = True
                lg.soothe_level = 2
                lg.soothe_count = soothe_count + 1
                await db.commit()
                from app.scheduler.storyline_engine import mark_cold_war_breakthrough
                await mark_cold_war_breakthrough(character_id, user_id, "user_soothe")
                _logger.info("Cold war resolved char=%d (sincere apology)", character_id)
                return 1
            # 轻哄或普通消息（找台阶）→ 软化记录，仍拦截
            if is_light or len(user_msg) >= 2:
                lg.soothe_level = 1
                lg.soothe_count = soothe_count + 1
                await db.commit()
                return 2
            return 0
    except Exception as e:
        _logger.warning("Cold war resolve failed char=%d: %s", character_id, e)
        return 0


async def _try_publish_moment(character_id: int, state_lines: str, rule: Rule) -> bool:
    """规则配置可发朋友圈时：近期（3.5h）已发过则降级私聊，否则发布并返回 True。"""
    async with async_session_factory() as db:
        since = _now_naive() - timedelta(minutes=MOMENT_FRESH_MINUTES)
        last = (
            await db.execute(
                select(AIMoment)
                .where(
                    AIMoment.character_id == character_id,
                    AIMoment.sender_type == "ai",
                    AIMoment.created_at >= since,
                )
                .order_by(AIMoment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if last is not None:
            _logger.info("State trigger moment char=%d skipped: recent moment", character_id)
            return False
    from app.services.moment_service import publish_moment
    extra_hint = (
        f"你此刻的状态：{state_lines}。"
        f"这条动态要贴合你此刻的情绪，表达：{rule.desc.replace('向用户', '用第一人称').replace('用户', '你')}"
    )
    try:
        result = await publish_moment(character_id, skip_interval=True, extra_hint=extra_hint)
    except Exception as e:
        _logger.warning("State trigger moment publish failed char=%d: %s", character_id, e)
        return False
    if result is None:
        return False
    # P0 发布即评论：状态触发发布后立即让其他 AI 角色评论（异步，不阻塞）
    try:
        from app.services.moment_service import generate_comments_for_moment
        asyncio.ensure_future(generate_comments_for_moment(result["id"]))
    except Exception as e:
        _logger.warning("State trigger moment comment launch failed: %s", e)
    _logger.info("State trigger moment published char=%d rule=%s", character_id, rule.key)
    return True


async def _execute_rule_behavior(
    character_id: int, user_id: int, rule: Rule, state_lines: str, delay_minutes: float | None = None,
) -> bool:
    """执行触发行为（朋友圈优先 / 私聊消息），成功后补记日记与记忆。返回是否执行成功。"""
    # 行为 1：朋友圈（规则配置 moment=True 且近期未发过）
    if rule.moment:
        if await _try_publish_moment(character_id, state_lines, rule):
            output_text = ""
            async with async_session_factory() as db:
                m = (
                    await db.execute(
                        select(AIMoment)
                        .where(AIMoment.character_id == character_id, AIMoment.sender_type == "ai")
                        .order_by(AIMoment.id.desc()).limit(1)
                    )
                ).scalar_one_or_none()
                if m:
                    output_text = f"发了一条朋友圈：{m.content[:80]}"
            asyncio.ensure_future(_post_trigger_notes(character_id, user_id, rule, output_text))
            _logger.info("State trigger fired char=%d rule=%s (moment)", character_id, rule.key)
            return True

    # 行为 2：主动私聊消息
    try:
        async with async_session_factory() as db:
            char = await db.get(AICharacter, character_id)
        name = char.name if char else f"角色{character_id}"
        personality = (char.personality or "友善")[:100] if char else "友善"
    except Exception:
        char, name, personality = None, f"角色{character_id}", "友善"

    identity = ""
    if char is not None:
        try:
            from app.agent.user_profile import build_role_prompt_block
            identity = await build_role_prompt_block(char, user_id)
        except Exception:
            identity = ""

    from app.agent.llm_client import chat_completion
    delay_line = f"你刚才的情绪发酵了约 {int(delay_minutes)} 分钟，越想越气/情绪越来越上头，" if delay_minutes else ""
    hint = (
        f"你是{name}，性格{personality}。\n"
        + (f"你的身份（不要混淆你与用户/用户的对象）：\n{identity}\n" if identity else "")
        + f"你的当前状态：{state_lines}。\n"
        f"{delay_line}"
        f"行为：{rule.desc}\n"
        "直接输出你要说的话（1-2 句，口语化，不要任何标注或引号）。"
    )
    try:
        content = (
            await chat_completion(
                messages=[
                    {"role": "system", "content": "直接输出内容。"},
                    {"role": "user", "content": hint},
                ],
                temperature=0.9,
                max_tokens=256,
                task="status", user_id=user_id,
            )
        ).strip().strip('"').strip("'")
    except Exception as e:
        _logger.warning("State trigger LLM failed char=%d: %s", character_id, e)
        return False
    if not content or len(content) < 2:
        return False

    from app.services.chat_service import get_latest_session_id
    session_id = await get_latest_session_id(user_id, character_id)
    if session_id is None:
        return False
    from app.scheduler.arbiter import get_hourly_active_count
    if await get_hourly_active_count(character_id) >= MAX_PER_HOUR:
        _logger.info("State trigger char=%d skipped: hourly limit", character_id)
        return False
    from app.scheduler.scheduler import send_to_session
    await send_to_session(session_id, character_id, user_id, content, message_type="state_trigger")
    asyncio.ensure_future(_post_trigger_notes(character_id, user_id, rule, content))
    _logger.info("State trigger fired char=%d rule=%s: %s", character_id, rule.key, content[:50])
    return True


async def _delayed_rule_behavior(
    character_id: int, user_id: int, rule: Rule, delay_minutes: float, state_lines: str,
) -> None:
    """延迟触发（v4）：等待 delay 分钟后复查状态，仍满足才执行行为。"""
    try:
        await asyncio.sleep(delay_minutes * 60)
        async with async_session_factory() as db:
            st = (
                await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            ).scalar_one_or_none()
            if st is None or not _rule_hit(rule, st):
                _logger.info("Delayed trigger cancelled char=%d rule=%s (state dropped)", character_id, rule.key)
                await _drop_trigger_log(character_id, rule.key)
                return
            ps = (
                await db.execute(select(ProactiveSettings).where(ProactiveSettings.character_id == character_id))
            ).scalar_one_or_none()
            if ps is not None and not getattr(ps, "state_trigger_enabled", True):
                await _drop_trigger_log(character_id, rule.key)
                return
        ok = await _execute_rule_behavior(character_id, user_id, rule, state_lines, delay_minutes=delay_minutes)
        if not ok:
            await _drop_trigger_log(character_id, rule.key)
            _logger.info("Delayed trigger dropped char=%d rule=%s (behavior failed)", character_id, rule.key)
    except Exception as e:
        _logger.warning("Delayed trigger failed char=%d rule=%s: %s", character_id, rule.key, e)


async def check_state_triggers(
    character_id: int,
    user_id: int,
    probability_multiplier: float = 1.0,
) -> list[str]:
    """检查八维状态触发；命中则执行行为（朋友圈/消息，含延迟）。返回触发的规则 key 列表。

    probability_multiplier: 聊天后实时触发传 0.5 降频；arbiter tick 兜底默认 1.0。
    """
    try:
        async with async_session_factory() as db:
            st = (
                await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            ).scalar_one_or_none()
            if st is None:
                return []

            ps = (
                await db.execute(select(ProactiveSettings).where(ProactiveSettings.character_id == character_id))
            ).scalar_one_or_none()
            if ps is not None and not getattr(ps, "state_trigger_enabled", True):
                return []

            # 免打扰时段拦截（v2：后端感知 user_dnd_settings）
            if await _user_in_dnd_period(db, user_id):
                _logger.info("State trigger char=%d skipped: user dnd period", character_id)
                return []
            # 剧情线收尾（v5）：冷战破冰后次日早上发和好后遗症消息（免打扰已拦截）
            from app.scheduler.storyline_engine import check_storyline_aftermath
            await check_storyline_aftermath(character_id, user_id)
            # 非冷战剧情线推进（v5-B）：吃醋/疲惫剧情激活时推进节点
            from app.scheduler.storyline_engine import advance_active_storylines
            await advance_active_storylines(character_id, user_id)
            now = _now_naive()
            logs = (
                await db.execute(select(StateTriggerLog).where(StateTriggerLog.character_id == character_id))
            ).scalars().all()
            log_by_key = {lg.trigger_key: lg for lg in logs}

            # 恢复检测：已触发但当前不再满足条件的标记 recovered（回落后再升才允许再触发）
            dirty = False
            for lg in logs:
                if not lg.recovered:
                    rule = _RULE_BY_KEY.get(lg.trigger_key)
                    if rule is None or not _rule_hit(rule, st):
                        lg.recovered = True
                        dirty = True
            if dirty:
                await db.commit()

            # 冷战进行中（v5）：跳过常规触发，交给剧情引擎推进节点（加时/深夜emo/超时破冰）
            cw_log = log_by_key.get("anger_mood_low")
            if cw_log is not None and not cw_log.recovered:
                from app.scheduler.storyline_engine import advance_cold_war_storyline
                await advance_cold_war_storyline(db, character_id, user_id, cw_log, st)
                return []
            # 记录本次快照（趋势检测用；先更新再判断，避免与自身比较）
            _last_snapshot[character_id] = {d: getattr(st, d) for d in _DIM_KEYS}

            # 候选规则：满足条件（含趋势增强） + 未在触发态 + 未在冷却期
            candidates = []
            for rule in RULES:
                hit = _rule_hit(rule, st) or _trend_hit(rule, st)
                if not hit:
                    continue
                lg = log_by_key.get(rule.key)
                if lg is not None:
                    if not lg.recovered:
                        continue
                    created = lg.created_at.replace(tzinfo=None) if lg.created_at.tzinfo else lg.created_at
                    if now - created < timedelta(minutes=rule.cooldown_minutes):
                        continue
                candidates.append(rule)
            if not candidates:
                return []
            candidates.sort(key=lambda r: r.priority, reverse=True)
            rule = candidates[0]

            # 概率门控（v2）：条件命中只是"应该触发"，按概率决定是否真触发
            if random.random() >= rule.probability * probability_multiplier:
                return []

            # 会话存在性检查（延迟任务发送时再复查，但这里先确保有会话可触发）
            from app.services.chat_service import get_latest_session_id
            session_id = await get_latest_session_id(user_id, character_id)
            if session_id is None:
                return []
            from app.scheduler.arbiter import get_hourly_active_count
            if await get_hourly_active_count(character_id) >= MAX_PER_HOUR:
                _logger.info("State trigger char=%d skipped: hourly limit", character_id)
                return []

            state_lines = "；".join(f"{_CN[d]}={getattr(st, d)}" for d in _DIM_KEYS)

        # 触发即锁定防抖（延迟场景必须提前锁定，避免双通道重复触发）
        await _record_trigger(character_id, rule.key, state_lines, anger=int(getattr(st, "anger", 50) or 50))
        # v5-B：命中剧情线触发键 → 初始化对应剧情线（吃醋/疲惫）
        from app.scheduler.storyline_engine import ensure_storyline_after_trigger
        await ensure_storyline_after_trigger(character_id, user_id, rule.key)

        # v4 延迟触发：怒气类规则延迟 5-25 分钟发作（越想越气），期间状态回落自动取消
        if rule.delay:
            delay_minutes = random.uniform(*rule.delay)
            asyncio.create_task(_delayed_rule_behavior(
                character_id, user_id, rule, delay_minutes, state_lines,
            ))
            _logger.info("State trigger scheduled char=%d rule=%s in ~%.0fmin", character_id, rule.key, delay_minutes)
            return [rule.key]

        ok = await _execute_rule_behavior(character_id, user_id, rule, state_lines)
        if not ok:
            await _drop_trigger_log(character_id, rule.key)
            return []
        return [rule.key]
    except Exception as e:
        _logger.warning("State trigger check failed char=%d: %s", character_id, e)
        return []
