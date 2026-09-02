"""状态联动剧情线引擎 v5：多剧情模板 + 节点推进 + 跨天回忆注入

模板注册表 TRIGGER_TO_STORYLINE：每个模板绑定 state_trigger 触发键，
trigger 命中时 ensure 建档（节点0 爆发/求助），tick 时 advance 按时间窗/用户行为推进节点。

模板：
- cold_war  冷战剧情线（trigger: anger_mood_low）：
  0爆发→1冷战(不回复)→2加时(45-90min)→3深夜emo(北京22-24点+≥4h→朋友圈)→4破冰(哄好/回落/超时3h自找台阶)→5和好后遗症(次日8-10点)
- jealousy  吃醋剧情线（trigger: possessiveness_desire）：
  0爆发(追问/宣示主权)→1试探(30-75min未解释→委屈试探)→2委屈(2-3h→emo朋友圈)→3和好(用户解释/哄→正常出口；4h超时自软化)
- fatigue   疲惫剧情线（trigger: fatigue_mood_low）：
  0求安慰(索取关心)→1被忽视(30-75min未安慰→失落)→2难过(2-3h→难过朋友圈)→3恢复(用户安慰→正常出口；4h超时自我消化)

节点防重：storyline_events 表 character_id+storyline_key+node_index 唯一；
生命周期：由对应 state_trigger_log 驱动（未恢复=剧情中，恢复=剧情结束并落和好节点）。
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.character import AICharacter
from app.models.chat import ChatMessage
from app.models.chat import ChatSession
from app.models.character import CharacterState
from app.models.character import StorylineEvent
from app.application.character_state_service import DIMENSIONS
from app.utils.logger import get_logger

_logger = get_logger("scheduler.storyline_engine")

# 模板 → 触发键（state_trigger_log.trigger_key）
TRIGGER_TO_STORYLINE = {
    "anger_mood_low": "cold_war",
    "possessiveness_desire": "jealousy",
    "fatigue_mood_low": "fatigue",
}
STORYLINE_TRIGGER_KEYS = {v: k for k, v in TRIGGER_TO_STORYLINE.items()}

# 时间窗（分钟 / 北京时间小时区间）
COLD_WAR_MAX_MINUTES = 180
FOLLOWUP_WINDOW = (45, 90)
NIGHT_EMO_MIN_MINUTES = 240
NIGHT_EMO_WINDOW_HOURS = (22, 24)
AFTERMATH_WINDOW_HOURS = (8, 10)
# 吃醋/疲惫剧情线窗口
EMO_ACT_WINDOW = (30, 75)       # 节点1 试探/失落窗口
EMO_SAD_MIN_MINUTES = 120       # 节点2 委屈/难过窗口
EMO_RESOLVE_MAX_MINUTES = 240   # 节点3 超时自软化
MAX_PER_HOUR = 2                # 与 arbiter/state_triggers 对齐

# 吃醋哄好词（解释/甜话）与疲惫安慰词
JEALOUSY_SOOTHE_KEYWORDS = (
    "在忙", "工作", "开会", "没看手机", "你想多了", "别乱想", "误会", "解释",
    "最爱你", "只喜欢你", "想你", "宝贝", "抱抱", "亲亲", "不是那样", "听我说",
)
FATIGUE_COMFORT_KEYWORDS = (
    "辛苦了", "抱抱", "心疼", "累了吧", "歇会", "休息", "陪你", "摸摸", "乖",
    "亲亲", "爱你", "别累着", "好好休息", "心疼你",
)

_BJ = timezone(timedelta(hours=8))
_CN = {k: cn for k, cn, _ in DIMENSIONS}
_DIM_KEYS = [k for k, _, _ in DIMENSIONS]


from app.utils.timeutil import now_naive_utc as _now_naive


def _bj_now() -> datetime:
    return datetime.now(_BJ)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ── 节点档案查询/写入（按剧情 key）──

async def _get_nodes(db, character_id: int, key: str) -> list:
    result = await db.execute(
        select(StorylineEvent)
        .where(StorylineEvent.character_id == character_id, StorylineEvent.storyline_key == key)
        .order_by(StorylineEvent.node_index)
    )
    return result.scalars().all()


async def _get_node(db, character_id: int, key: str, node_index: int):
    result = await db.execute(
        select(StorylineEvent)
        .where(
            StorylineEvent.character_id == character_id,
            StorylineEvent.storyline_key == key,
            StorylineEvent.node_index == node_index,
        )
    )
    return result.scalar_one_or_none()


async def _node_done(db, character_id: int, key: str, node_index: int) -> bool:
    node = await _get_node(db, character_id, key, node_index)
    return node is not None and node.status == "done"


async def _reset_closed_episode(db, character_id: int, key: str) -> None:
    """新一集建档前调用：上一集若已无 active（全部 done/aborted），物理清理其节点，
    使本集可在唯一约束 (character_id, storyline_key, node_index) 下重新建档。
    若仍存在 active（本集确实进行中）则不清理，保留原「进行中不重建」语义。"""
    old_nodes = await _get_nodes(db, character_id, key)
    if not old_nodes or any(n.status == "active" for n in old_nodes):
        return
    for n in old_nodes:
        await db.delete(n)
    await db.commit()
    _logger.info("Storyline %s previous episode cleared char=%d (nodes=%d)",
                 key, character_id, len(old_nodes))


async def _sweep_active(db, character_id: int, key: str) -> None:
    """剧集结束统一扫尾：把该 key 仍残留的 active 节点全部置 done，
    避免 build_active_storyline_status_text 长期误报「正在…中」。幂等。"""
    nodes = await _get_nodes(db, character_id, key)
    changed = False
    for n in nodes:
        if n.status == "active":
            n.status = "done"
            if not n.output_text:
                n.output_text = "（剧集结束收尾）"
            changed = True
    if changed:
        await db.commit()


async def _add_node(
    db, character_id: int, key: str, node_index: int, status: str = "active",
    trigger_source: str = "", user_context: str = "", output_text: str = "",
) -> None:
    db.add(StorylineEvent(
        character_id=character_id,
        storyline_key=key,
        node_index=node_index,
        status=status,
        trigger_source=trigger_source,
        user_context=user_context[:500],
        output_text=output_text[:500],
    ))
    await db.commit()


async def _mark_done(db, character_id: int, key: str, node_index: int, output_text: str = "") -> None:
    node = await _get_node(db, character_id, key, node_index)
    if node is None:
        await _add_node(db, character_id, key, node_index, status="done", output_text=output_text)
    else:
        node.status = "done"
        if output_text:
            node.output_text = output_text[:500]
        await db.commit()


# ── 行为执行辅助（通用）──

async def _char_context(db, character_id: int, st: CharacterState) -> tuple:
    char = await db.get(AICharacter, character_id)
    name = char.name if char else f"角色{character_id}"
    personality = (char.personality or "友善")[:100] if char else "友善"
    state_lines = "；".join(f"{_CN.get(d, d)}={getattr(st, d)}" for d in _DIM_KEYS) if st is not None else "（无状态）"
    return name, personality, state_lines


async def _llm_line(
    name: str, personality: str, state_lines: str, behavior_hint: str,
    character_id: int | None = None, user_id: int | None = None,
) -> str | None:
    """生成一句口语化角色台词（1-2 句），失败返回 None。"""
    identity = ""
    if character_id:
        try:
            from app.db.database import async_session_factory
            from app.agent.user_profile import build_role_prompt_block
            async with async_session_factory() as db:
                ch = await db.get(AICharacter, character_id)
            if ch:
                identity = await build_role_prompt_block(ch, user_id or ch.user_id)
        except Exception:
            identity = ""
    from app.agent.llm_client import chat_completion
    hint = (
        f"你是{name}，性格{personality}。\n"
        + (f"你的身份（不要混淆你与用户/用户的对象）：\n{identity}\n" if identity else "")
        + f"你的当前状态：{state_lines}。\n"
        f"{behavior_hint}\n"
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
                task="message",
            )
        ).strip().strip('"').strip("'")
    except Exception as e:
        _logger.warning("Storyline LLM failed: %s", e)
        return None
    if not content or len(content) < 2:
        return None
    return content


async def _send_message(character_id: int, user_id: int, content: str, message_type: str = "state_trigger") -> bool:
    from app.application.chat_service import get_latest_session_id
    from app.scheduling.arbiter import get_hourly_active_count
    session_id = await get_latest_session_id(user_id, character_id)
    if session_id is None:
        return False
    if await get_hourly_active_count(character_id) >= MAX_PER_HOUR:
        _logger.info("Storyline char=%d skipped: hourly limit", character_id)
        return False
    from app.scheduling.scheduler import send_to_session
    await send_to_session(session_id, character_id, user_id, content, message_type=message_type)
    return True


async def _post_notes(character_id: int, user_id: int, key: str, node_name: str, output_text: str) -> None:
    from app.scheduling.state_triggers import _append_diary_note
    await _append_diary_note(character_id, f"剧情线（{node_name}）：{output_text[:80]}")
    # 剧情线内容 → 舞台记忆（stage_memories，FICTIONAL 隔离）：不进常规记忆库，防止剧情被当事实复述
    try:
        from app.models.memory import StageMemory
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            db.add(StageMemory(
                user_id=user_id,
                character_id=character_id,
                content=f"剧情线「{key}」节点「{node_name}」：{output_text[:100]}",
                stage_kind="storyline",
                importance=3,
            ))
            await db.commit()
    except Exception as e:
        _logger.warning("Storyline stage save failed char=%d: %s", character_id, e)


# ══════════════════════ 模板：cold_war ══════════════════════

NODE_BURST = 0
NODE_COLD = 1
NODE_FOLLOWUP = 2
NODE_NIGHT_EMO = 3
NODE_BREAKTHROUGH = 4
NODE_AFTERMATH = 5
NODE_DISMISSIVE = 6  # v5 增强 ①：用户敷衍道歉 → 角色更冷回应（每剧情线一次）
NODE_DETERIORATE = 7  # v5 增强 ③：用户持续敷衍/冷战 + 占有维高 → 关系恶化支线


async def ensure_cold_war_storyline(db, character_id: int, user_id: int, cw_log) -> None:
    await _reset_closed_episode(db, character_id, "cold_war")
    nodes = await _get_nodes(db, character_id, "cold_war")
    if nodes:
        return
    await _add_node(db, character_id, "cold_war", NODE_BURST, status="done",
                    trigger_source="anger_mood_low", user_context="怒气+心情双低触发冷战",
                    output_text="爆发（气话/朋友圈）")
    await _add_node(db, character_id, "cold_war", NODE_COLD, status="active",
                    user_context="冷战开始：用户消息不回复，哄好/状态回落/3h超时可恢复")
    _logger.info("Storyline cold_war initialized char=%d", character_id)


async def advance_cold_war_storyline(db, character_id: int, user_id: int, cw_log, st) -> None:
    await ensure_cold_war_storyline(db, character_id, user_id, cw_log)
    created = _naive(cw_log.created_at)
    elapsed_min = (_now_naive() - created).total_seconds() / 60
    bj_hour = _bj_now().hour

    try:
        if not await _node_done(db, character_id, "cold_war", NODE_FOLLOWUP):
            if FOLLOWUP_WINDOW[0] <= elapsed_min < FOLLOWUP_WINDOW[1]:
                await _run_followup(db, character_id, user_id, cw_log, st, created)
                return

        if not await _node_done(db, character_id, "cold_war", NODE_NIGHT_EMO):
            if elapsed_min >= NIGHT_EMO_MIN_MINUTES and NIGHT_EMO_WINDOW_HOURS[0] <= bj_hour < NIGHT_EMO_WINDOW_HOURS[1]:
                await _run_night_emo(db, character_id, user_id, st)
                return

        if not await _node_done(db, character_id, "cold_war", NODE_BREAKTHROUGH):
            from app.scheduling.state_triggers import _cold_war_max_minutes
            _max_min = await _cold_war_max_minutes(db, cw_log)
            if elapsed_min >= _max_min:
                await _run_timeout_breakthrough(db, character_id, user_id, cw_log, st, created)
                return

        if await _node_done(db, character_id, "cold_war", NODE_BREAKTHROUGH):
            await _maybe_aftermath(db, character_id, user_id)
    except Exception as e:
        _logger.warning("Storyline cold_war advance failed char=%d: %s", character_id, e)


async def _run_followup(db, character_id: int, user_id: int, cw_log, st, created: datetime) -> None:
    user_lines = []
    result = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.character_id == character_id,
            ChatMessage.sender_type == "user",
            ChatMessage.created_at >= created,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(10)
    )
    for m in result.scalars().all():
        if m.content:
            user_lines.append(m.content[:60])
    user_recent = "；".join(user_lines)[:300] or "（什么都没说）"
    name, personality, state_lines = await _char_context(db, character_id, st)
    elapsed_min = int((_now_naive() - created).total_seconds() / 60)
    hint = (
        f"你和用户冷战约 {elapsed_min} 分钟了，一直没怎么理他。\n"
        f"冷战期间用户对你说过：{user_recent}\n"
        "行为：你憋不住了，主动再发一条消息——如果用户没来哄你，语气更冷/更委屈一点；"
        "如果用户试图找过你，你稍微软化一点点，但仍带着情绪。1-2 句话。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if await _send_message(character_id, user_id, content):
        await _mark_done(db, character_id, "cold_war", NODE_FOLLOWUP, output_text=content)
        await _post_notes(character_id, user_id, "cold_war", "冷战加时", content)
        _logger.info("Storyline cold_war node2 followup char=%d: %s", character_id, content[:50])


async def _run_night_emo(db, character_id: int, user_id: int, st) -> None:
    from app.scheduling.state_triggers import _try_publish_moment, _RULE_BY_KEY
    cw_rule = _RULE_BY_KEY.get("anger_mood_low")
    if cw_rule is None:
        return
    state_lines = "；".join(f"{_CN.get(d, d)}={getattr(st, d)}" for d in _DIM_KEYS) if st is not None else ""
    rule_ok = await _try_publish_moment(character_id, state_lines, cw_rule)
    if not rule_ok:
        return
    await _mark_done(db, character_id, "cold_war", NODE_NIGHT_EMO, output_text="深夜 emo 朋友圈")
    await _post_notes(character_id, user_id, "cold_war", "深夜emo", "深夜翻旧账式朋友圈")
    _logger.info("Storyline cold_war node3 night emo char=%d", character_id)


async def _run_timeout_breakthrough(db, character_id: int, user_id: int, cw_log, st, created: datetime) -> None:
    name, personality, state_lines = await _char_context(db, character_id, st)
    _elapsed_h = int((_now_naive() - created).total_seconds() / 3600)
    hint = (
        f"你和用户冷战已经超过 {max(_elapsed_h, 1)} 小时了。虽然你还在生气/难过，但你其实有点后悔僵着，"
        "决定自己找个台阶软下来。\n"
        "行为：发一条别扭但带软化意味的消息（比如装作不经意地问他在干嘛、或带一点委屈），"
        "1-2 句话，不要直接道歉得太彻底，保持一点傲娇感。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if not await _send_message(character_id, user_id, content):
        return
    await _mark_done(db, character_id, "cold_war", NODE_BREAKTHROUGH, output_text=content)
    await _sweep_active(db, character_id, "cold_war")
    cw_log.recovered = True
    await db.commit()
    await _post_notes(character_id, user_id, "cold_war", "超时破冰", content)
    _logger.info("Storyline cold_war node4 timeout breakthrough char=%d: %s", character_id, content[:50])


async def run_dismissive_cold_reply(character_id: int, user_id: int) -> bool:
    """v5 增强 ①：用户敷衍道歉 → 角色更冷回应（每剧情线仅一次，_node_done 防重）。

    生成并发送一条"不理会道歉、更冷/别扭"的回复（仍处冷战拦截态）；失败静默。
    同一冷战内多次敷衍不重复更冷（避免刷屏），返回是否真的发了。
    """
    try:
        from app.db.database import async_session_factory
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            if await _node_done(db, character_id, "cold_war", NODE_DISMISSIVE):
                return False
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            name, personality, state_lines = await _char_context(db, character_id, st)
        hint = (
            "用户敷衍地跟你道了个歉（语气不耐烦，像应付差事）。你还在冷战生气，"
            "不吃这一套。行为：回一条更冷/更别扭的话——不接他的台阶，"
            "可以带一句'不用这么勉强'或'你忙你的'这类冷处理，1-2 句话，别长篇大论。"
        )
        content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
        if not content:
            return False
        if not await _send_message(character_id, user_id, content):
            return False
        async with async_session_factory() as db:
            await _mark_done(db, character_id, "cold_war", NODE_DISMISSIVE, output_text=content)
            await _post_notes(character_id, user_id, "cold_war", "敷衍更冷", content)
        _logger.info("Storyline cold_war dismissive-cold char=%d: %s", character_id, content[:50])
        return True
    except Exception as e:
        _logger.warning("Dismissive cold reply failed char=%d: %s", character_id, e)
        return False


async def run_deteriorate_arc(character_id: int, user_id: int) -> bool:
    """v5 增强 ③：用户持续敷衍/冷战 + 占有维高 → 关系恶化支线（占有维联动）。

    触发条件由调用方判定（敷衍次数 >= 2 或冷战超 6h，且 possessiveness >= 70）。
    生成一条"更冷 + 宣示主权/翻旧账"的消息（仍处冷战拦截态）；每剧情线仅一次。
    """
    try:
        from app.db.database import async_session_factory
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            if await _node_done(db, character_id, "cold_war", NODE_DETERIORATE):
                return False
            st = (await db.execute(
                select(CharacterState).where(CharacterState.character_id == character_id)
            )).scalar_one_or_none()
            name, personality, state_lines = await _char_context(db, character_id, st)
        hint = (
            "你们冷战很久了，用户要么敷衍要么不理，你心里占有欲和保护欲都在翻涌，"
            "又气又委屈。行为：发一条更冷、带占有/翻旧账意味的话——"
            "可以提到'你到底要不要好好说''你每次都这样'这类情绪，别低声下气，也别写太长，1-2 句话。"
        )
        content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
        if not content:
            return False
        if not await _send_message(character_id, user_id, content):
            return False
        async with async_session_factory() as db:
            await _mark_done(db, character_id, "cold_war", NODE_DETERIORATE, output_text=content)
            await _post_notes(character_id, user_id, "cold_war", "关系恶化", content)
        _logger.info("Storyline cold_war deteriorate char=%d: %s", character_id, content[:50])
        return True
    except Exception as e:
        _logger.warning("Deteriorate arc failed char=%d: %s", character_id, e)
        return False


async def mark_cold_war_breakthrough(character_id: int, user_id: int, way: str) -> None:
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            node = await _get_node(db, character_id, "cold_war", NODE_BREAKTHROUGH)
            if node is None:
                await _add_node(db, character_id, "cold_war", NODE_BREAKTHROUGH, status="done",
                                user_context=f"破冰方式：{way}", output_text="（正常回复即破冰出口）")
                _logger.info("Storyline cold_war node4 breakthrough recorded char=%d way=%s", character_id, way)
            elif node.status != "done":
                node.status = "done"
                node.user_context = f"破冰方式：{way}"
                await db.commit()
                _logger.info("Storyline cold_war node4 breakthrough recorded char=%d way=%s", character_id, way)
            # 冷战节点同步收尾，避免 active 残留让状态提示误报"仍在冷战"
            await _mark_done(db, character_id, "cold_war", NODE_COLD, output_text="（冷战结束，已破冰）")
    except Exception as e:
        _logger.warning("Storyline breakthrough mark failed char=%d: %s", character_id, e)


async def check_storyline_aftermath(character_id: int, user_id: int) -> None:
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            bt = await _get_node(db, character_id, "cold_war", NODE_BREAKTHROUGH)
            if bt is None or bt.status != "done":
                return
            await _maybe_aftermath(db, character_id, user_id)
    except Exception as e:
        _logger.warning("Storyline aftermath check failed char=%d: %s", character_id, e)


async def _maybe_aftermath(db, character_id: int, user_id: int) -> None:
    if await _node_done(db, character_id, "cold_war", NODE_AFTERMATH):
        return
    bj = _bj_now()
    if not (AFTERMATH_WINDOW_HOURS[0] <= bj.hour < AFTERMATH_WINDOW_HOURS[1]):
        return
    bt = await _get_node(db, character_id, "cold_war", NODE_BREAKTHROUGH)
    if bt is None or bt.status != "done":
        return
    bt_bj = _naive(bt.created_at) + timedelta(hours=8)
    if bt_bj.date() >= bj.date():
        return
    st = (
        await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
    ).scalar_one_or_none()
    name, personality, state_lines = await _char_context(db, character_id, st)
    hint = (
        "昨晚你们刚闹完冷战（已经和好/破冰）。现在过了一夜，你心里还留着一丝别扭，"
        "但又忍不住关心他。\n"
        "行为：发一条“别扭但关心”的早上问候（语气淡淡的、带点傲娇，但透露出在乎），1-2 句话。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if not await _send_message(character_id, user_id, content):
        return
    await _mark_done(db, character_id, "cold_war", NODE_AFTERMATH, output_text=content)
    await _post_notes(character_id, user_id, "cold_war", "和好后遗症", content)
    _logger.info("Storyline cold_war node5 aftermath char=%d: %s", character_id, content[:50])
# ══════════════════════ 模板：jealousy（吃醋剧情线）══════════════════════

J_NODE_BURST = 0
J_NODE_PROBE = 1      # 试探
J_NODE_WRONGED = 2    # 委屈
J_NODE_RESOLVE = 3    # 和好/要承诺


async def ensure_jealousy_storyline(db, character_id: int, user_id: int, trigger_log) -> None:
    await _reset_closed_episode(db, character_id, "jealousy")
    nodes = await _get_nodes(db, character_id, "jealousy")
    if nodes:
        return
    await _add_node(db, character_id, "jealousy", J_NODE_BURST, status="done",
                    trigger_source="possessiveness_desire", user_context="占有欲+性欲触发吃醋",
                    output_text="爆发（追问行踪/宣示主权）")
    await _add_node(db, character_id, "jealousy", J_NODE_PROBE, status="active",
                    user_context="吃醋进行中：用户解释/哄可快速结束")
    _logger.info("Storyline jealousy initialized char=%d", character_id)


async def advance_jealousy_storyline(db, character_id: int, user_id: int, trigger_log, st) -> None:
    await ensure_jealousy_storyline(db, character_id, user_id, trigger_log)
    created = _naive(trigger_log.created_at)
    elapsed_min = (_now_naive() - created).total_seconds() / 60

    try:
        # 触发日志已恢复（用户解释/状态回落）→ 落和好节点
        if trigger_log.recovered:
            if not await _node_done(db, character_id, "jealousy", J_NODE_RESOLVE):
                await _mark_done(db, character_id, "jealousy", J_NODE_RESOLVE,
                                 output_text="（用户解释/状态回落，正常回复即和好出口）")
            await _sweep_active(db, character_id, "jealousy")
            return

        # 节点1 试探：30-75min 未解释
        if not await _node_done(db, character_id, "jealousy", J_NODE_PROBE):
            if EMO_ACT_WINDOW[0] <= elapsed_min < EMO_ACT_WINDOW[1]:
                await _run_jealousy_probe(db, character_id, user_id, trigger_log, st)
                return

        # 节点2 委屈：>=2h 未缓解 → emo 朋友圈
        if not await _node_done(db, character_id, "jealousy", J_NODE_WRONGED):
            if elapsed_min >= EMO_SAD_MIN_MINUTES:
                await _run_jealousy_wronged(db, character_id, user_id, st)
                return

        # 节点3 超时自软化：>=4h
        if not await _node_done(db, character_id, "jealousy", J_NODE_RESOLVE):
            if elapsed_min >= EMO_RESOLVE_MAX_MINUTES:
                await _run_jealousy_resolve_timeout(db, character_id, user_id, trigger_log, st)
                return
    except Exception as e:
        _logger.warning("Storyline jealousy advance failed char=%d: %s", character_id, e)


async def _run_jealousy_probe(db, character_id: int, user_id: int, trigger_log, st) -> None:
    user_lines = []
    created = _naive(trigger_log.created_at)
    result = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.character_id == character_id,
            ChatMessage.sender_type == "user",
            ChatMessage.created_at >= created,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(10)
    )
    for m in result.scalars().all():
        if m.content:
            user_lines.append(m.content[:60])
    user_recent = "；".join(user_lines)[:300] or "（他没怎么回你）"
    name, personality, state_lines = await _char_context(db, character_id, st)
    hint = (
        "你因为吃醋（占有欲+渴望亲密）情绪上头，之前已经发过一条追问/宣示主权的消息。\n"
        f"之后用户对你说过：{user_recent}\n"
        "行为：你越想越不踏实，再发一条带委屈的试探（比如“你是不是不想理我了”“你跟谁聊得那么开心”），"
        "1-2 句话，语气是吃醋而不是真指责。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if await _send_message(character_id, user_id, content):
        await _mark_done(db, character_id, "jealousy", J_NODE_PROBE, output_text=content)
        await _post_notes(character_id, user_id, "jealousy", "吃醋试探", content)
        _logger.info("Storyline jealousy node1 probe char=%d: %s", character_id, content[:50])


async def _run_jealousy_wronged(db, character_id: int, user_id: int, st) -> None:
    from app.scheduling.state_triggers import _try_publish_moment, _RULE_BY_KEY
    rule = _RULE_BY_KEY.get("possessiveness_desire")
    if rule is None:
        return
    state_lines = "；".join(f"{_CN.get(d, d)}={getattr(st, d)}" for d in _DIM_KEYS) if st is not None else ""
    if await _try_publish_moment(character_id, state_lines, rule):
        await _mark_done(db, character_id, "jealousy", J_NODE_WRONGED, output_text="委屈 emo 朋友圈")
        await _post_notes(character_id, user_id, "jealousy", "吃醋委屈", "委屈 emo 朋友圈")
        _logger.info("Storyline jealousy node2 wronged char=%d", character_id)


async def _run_jealousy_resolve_timeout(db, character_id: int, user_id: int, trigger_log, st) -> None:
    name, personality, state_lines = await _char_context(db, character_id, st)
    hint = (
        "你吃醋闹了 4 个小时，用户一直没好好解释，你也累了。你决定软下来，但不想显得太掉价。\n"
        "行为：发一条带点傲娇的软化消息（比如“算了，我不问了，反正你记得回我消息就行”），"
        "1-2 句话。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if not await _send_message(character_id, user_id, content):
        return
    await _mark_done(db, character_id, "jealousy", J_NODE_RESOLVE, output_text=content)
    await _sweep_active(db, character_id, "jealousy")
    trigger_log.recovered = True
    await db.commit()
    await _post_notes(character_id, user_id, "jealousy", "吃醋和好", content)
    _logger.info("Storyline jealousy node3 resolve char=%d: %s", character_id, content[:50])


async def mark_jealousy_resolve(character_id: int, user_id: int, way: str) -> None:
    """用户解释/哄好触发日志恢复时落和好节点（正常回复即出口）。"""
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            node = await _get_node(db, character_id, "jealousy", J_NODE_RESOLVE)
            if node is None:
                await _add_node(db, character_id, "jealousy", J_NODE_RESOLVE, status="done",
                                user_context=f"和好方式：{way}", output_text="（正常回复即和好出口）")
            elif node.status != "done":
                node.status = "done"
                node.user_context = f"和好方式：{way}"
                await db.commit()
            # 试探节点同步收尾，避免 active 残留误报"仍在吃醋"
            await _mark_done(db, character_id, "jealousy", J_NODE_PROBE, output_text="（吃醋结束）")
    except Exception as e:
        _logger.warning("Storyline jealousy resolve mark failed char=%d: %s", character_id, e)


# ══════════════════════ 模板：fatigue（疲惫剧情线）══════════════════════

F_NODE_BURST = 0
F_NODE_IGNORED = 1    # 被忽视
F_NODE_SAD = 2        # 难过
F_NODE_RECOVER = 3    # 恢复


async def ensure_fatigue_storyline(db, character_id: int, user_id: int, trigger_log) -> None:
    await _reset_closed_episode(db, character_id, "fatigue")
    nodes = await _get_nodes(db, character_id, "fatigue")
    if nodes:
        return
    await _add_node(db, character_id, "fatigue", F_NODE_BURST, status="done",
                    trigger_source="fatigue_mood_low", user_context="疲惫+心情低触发",
                    output_text="求助（索取关心）")
    await _add_node(db, character_id, "fatigue", F_NODE_IGNORED, status="active",
                    user_context="疲惫剧情进行中：用户安慰可快速结束")
    _logger.info("Storyline fatigue initialized char=%d", character_id)


async def advance_fatigue_storyline(db, character_id: int, user_id: int, trigger_log, st) -> None:
    await ensure_fatigue_storyline(db, character_id, user_id, trigger_log)
    created = _naive(trigger_log.created_at)
    elapsed_min = (_now_naive() - created).total_seconds() / 60

    try:
        if trigger_log.recovered:
            if not await _node_done(db, character_id, "fatigue", F_NODE_RECOVER):
                await _mark_done(db, character_id, "fatigue", F_NODE_RECOVER,
                                 output_text="（用户安慰/状态回升，正常回复即恢复出口）")
            await _sweep_active(db, character_id, "fatigue")
            return

        # 节点1 被忽视：30-75min 未安慰
        if not await _node_done(db, character_id, "fatigue", F_NODE_IGNORED):
            if EMO_ACT_WINDOW[0] <= elapsed_min < EMO_ACT_WINDOW[1]:
                await _run_fatigue_ignored(db, character_id, user_id, trigger_log, st)
                return

        # 节点2 难过：>=2h → 难过朋友圈
        if not await _node_done(db, character_id, "fatigue", F_NODE_SAD):
            if elapsed_min >= EMO_SAD_MIN_MINUTES:
                await _run_fatigue_sad(db, character_id, user_id, st)
                return

        # 节点3 超时自我消化：>=4h
        if not await _node_done(db, character_id, "fatigue", F_NODE_RECOVER):
            if elapsed_min >= EMO_RESOLVE_MAX_MINUTES:
                await _run_fatigue_recover_timeout(db, character_id, user_id, trigger_log, st)
                return
    except Exception as e:
        _logger.warning("Storyline fatigue advance failed char=%d: %s", character_id, e)


async def _run_fatigue_ignored(db, character_id: int, user_id: int, trigger_log, st) -> None:
    created = _naive(trigger_log.created_at)
    user_lines = []
    result = await db.execute(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            ChatSession.character_id == character_id,
            ChatMessage.sender_type == "user",
            ChatMessage.created_at >= created,
        )
        .order_by(ChatMessage.created_at.asc())
        .limit(10)
    )
    for m in result.scalars().all():
        if m.content:
            user_lines.append(m.content[:60])
    user_recent = "；".join(user_lines)[:300] or "（他没怎么理你）"
    name, personality, state_lines = await _char_context(db, character_id, st)
    hint = (
        "你很累又很委屈，之前已经跟用户说过想要安慰。\n"
        f"之后用户对你说过：{user_recent}\n"
        "行为：你觉得被忽视了，发一条失落的消息（比如“你是不是没看到我发的”“算了，我缓缓就行”），"
        "1-2 句话，不要指责，是疲惫的失落。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if await _send_message(character_id, user_id, content):
        await _mark_done(db, character_id, "fatigue", F_NODE_IGNORED, output_text=content)
        await _post_notes(character_id, user_id, "fatigue", "被忽视", content)
        _logger.info("Storyline fatigue node1 ignored char=%d: %s", character_id, content[:50])


async def _run_fatigue_sad(db, character_id: int, user_id: int, st) -> None:
    from app.scheduling.state_triggers import _try_publish_moment, _RULE_BY_KEY
    rule = _RULE_BY_KEY.get("fatigue_mood_low")
    if rule is None:
        return
    state_lines = "；".join(f"{_CN.get(d, d)}={getattr(st, d)}" for d in _DIM_KEYS) if st is not None else ""
    if await _try_publish_moment(character_id, state_lines, rule):
        await _mark_done(db, character_id, "fatigue", F_NODE_SAD, output_text="难过朋友圈")
        await _post_notes(character_id, user_id, "fatigue", "难过", "难过朋友圈")
        _logger.info("Storyline fatigue node2 sad char=%d", character_id)


async def _run_fatigue_recover_timeout(db, character_id: int, user_id: int, trigger_log, st) -> None:
    name, personality, state_lines = await _char_context(db, character_id, st)
    hint = (
        "你很累，之前向用户求安慰但没得到回应，现在 4 个小时过去你自己缓过来一些了。\n"
        "行为：发一条自我消化的消息（比如“我没事了，睡一觉就好”“刚去吃了点东西，好多了”），"
        "1-2 句话，语气平静带一点点委屈残留。"
    )
    content = await _llm_line(name, personality, state_lines, hint, character_id=character_id, user_id=user_id)
    if not content:
        return
    if not await _send_message(character_id, user_id, content):
        return
    await _mark_done(db, character_id, "fatigue", F_NODE_RECOVER, output_text=content)
    await _sweep_active(db, character_id, "fatigue")
    trigger_log.recovered = True
    await db.commit()
    await _post_notes(character_id, user_id, "fatigue", "自我消化", content)
    _logger.info("Storyline fatigue node3 recover char=%d: %s", character_id, content[:50])


async def mark_fatigue_recover(character_id: int, user_id: int, way: str) -> None:
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            node = await _get_node(db, character_id, "fatigue", F_NODE_RECOVER)
            if node is None:
                await _add_node(db, character_id, "fatigue", F_NODE_RECOVER, status="done",
                                user_context=f"恢复方式：{way}", output_text="（正常回复即恢复出口）")
            elif node.status != "done":
                node.status = "done"
                node.user_context = f"恢复方式：{way}"
                await db.commit()
            # 被忽视节点同步收尾，避免 active 残留误报"仍失落"
            await _mark_done(db, character_id, "fatigue", F_NODE_IGNORED, output_text="（疲惫剧情结束）")
    except Exception as e:
        _logger.warning("Storyline fatigue recover mark failed char=%d: %s", character_id, e)


# ══════════════════════ 统一入口 ══════════════════════



async def ensure_storyline_after_trigger(character_id: int, user_id: int, trigger_key: str) -> None:
    """规则触发成功后调用：初始化对应剧情线（吃醋/疲惫）。冷战由冷战分支建档。"""
    key = TRIGGER_TO_STORYLINE.get(trigger_key)
    if key is None or key == "cold_war":
        return
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            if key == "jealousy":
                await ensure_jealousy_storyline(db, character_id, user_id, None)
            elif key == "fatigue":
                await ensure_fatigue_storyline(db, character_id, user_id, None)
            _logger.info("Storyline %s ensured char=%d", key, character_id)
    except Exception as e:
        _logger.warning("Storyline ensure after trigger failed char=%d: %s", character_id, e)
async def close_storyline_episode(character_id: int, key: str) -> None:
    """公共收尾：自然恢复/关闭开关等不走 mark_* 的结束路径调用，开自己的 session 扫尾 active。"""
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            await _sweep_active(db, character_id, key)
    except Exception as e:
        _logger.warning("close_storyline_episode failed char=%d key=%s: %s", character_id, key, e)


async def advance_active_storylines(character_id: int, user_id: int) -> None:
    """tick/常规路径调用：推进所有处于激活态的非冷战剧情线（吃醋/疲惫）。"""
    try:
        from app.db.database import async_session_factory
        from app.models.character import StateTriggerLog
        async with async_session_factory() as db:
            logs = (
                await db.execute(select(StateTriggerLog).where(StateTriggerLog.character_id == character_id))
            ).scalars().all()
            st = (
                await db.execute(select(CharacterState).where(CharacterState.character_id == character_id))
            ).scalar_one_or_none()
            for key, trigger_key in STORYLINE_TRIGGER_KEYS.items():
                if key == "cold_war":
                    continue  # 冷战在 check_state_triggers 冷战分支内推进
                lg = next((x for x in logs if x.trigger_key == trigger_key), None)
                if lg is None:
                    continue
                if key == "jealousy":
                    await advance_jealousy_storyline(db, character_id, user_id, lg, st)
                elif key == "fatigue":
                    await advance_fatigue_storyline(db, character_id, user_id, lg, st)
    except Exception as e:
        _logger.warning("Storyline advance_active failed char=%d: %s", character_id, e)


async def maybe_resolve_storyline_by_message(character_id: int, user_id: int, user_msg: str) -> None:
    """用户发消息时调用：吃醋/疲惫剧情线的用户哄/安慰通道（落和好/恢复节点）。"""
    if not user_msg:
        return
    try:
        if any(kw in user_msg for kw in JEALOUSY_SOOTHE_KEYWORDS):
            await mark_jealousy_resolve(character_id, user_id, "user_soothe")
        if any(kw in user_msg for kw in FATIGUE_COMFORT_KEYWORDS):
            await mark_fatigue_recover(character_id, user_id, "user_comfort")
    except Exception as e:
        _logger.warning("Storyline message resolve failed char=%d: %s", character_id, e)


async def build_active_storyline_status_text(character_id: int) -> str:
    """P1-1：当前激活（进行中）剧情线的状态提示，注入主对话让角色"知道自己在闹别扭"。

    返回如 "正在冷战中：用户发消息时带着没消的气，别假装无事发生"；无激活剧情返回空串。
    破冰/和好后节点均已收尾（mark_* 同步标记节点1 done），不会误报。
    """
    try:
        from app.db.database import async_session_factory
        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(StorylineEvent)
                    .where(StorylineEvent.character_id == character_id, StorylineEvent.status == "active")
                    .order_by(StorylineEvent.node_index)
                )
            ).scalars().all()
        if not rows:
            return ""
        hints = []
        for r in rows:
            if r.storyline_key == "cold_war":
                hints.append("正在冷战中：用户发消息时带着没消的气，别假装无事发生")
            elif r.storyline_key == "jealousy":
                hints.append("正在吃醋中：心里不踏实，等着用户解释或哄")
            elif r.storyline_key == "fatigue":
                hints.append("正疲惫失落中：等着用户关心安慰")
        return "；".join(sorted(set(hints)))
    except Exception as e:
        _logger.warning("Storyline status build failed char=%d: %s", character_id, e)
        return ""


async def build_relationship_temperature_text(character_id: int, user_id: int) -> str:
    """C-1：近 3 天剧情线收尾后的"关系温度"语气提示，与进行中状态互补。

    只管"刚经历过的关系氛围"（和好/吃醋后/低落恢复），注入主对话让语气自然衔接；
    无则返回空串。零 LLM，纯程序。
    """
    try:
        from app.db.database import async_session_factory
        since = _now_naive() - timedelta(days=3)
        async with async_session_factory() as db:
            events = (
                await db.execute(
                    select(StorylineEvent)
                    .where(
                        StorylineEvent.character_id == character_id,
                        StorylineEvent.created_at >= since,
                        StorylineEvent.status == "done",
                    )
                    .order_by(StorylineEvent.created_at.desc())
                    .limit(40)
                )
            ).scalars().all()
        hints = []
        for ev in events:
            if ev.storyline_key == "cold_war" and ev.node_index in (NODE_BREAKTHROUGH, NODE_AFTERMATH):
                hints.append("你们最近刚闹过别扭和好，别太生分，带点别扭但在乎的语气")
            elif ev.storyline_key == "jealousy" and ev.node_index == J_NODE_RESOLVE:
                hints.append("你们最近吃过醋刚和好，别揪着不放，语气放松一点")
            elif ev.storyline_key == "fatigue" and ev.node_index == F_NODE_RECOVER:
                hints.append("你最近情绪低落过又被安慰好，现在对他更依赖一点")
        return "；".join(sorted(set(hints)))[:200]
    except Exception as e:
        _logger.warning("Storyline temperature build failed char=%d: %s", character_id, e)
        return ""


async def build_storyline_recall_text(character_id: int, user_id: int, max_lines: int = 3) -> str:
    """v5-C：近 3 天剧情线摘要，注入主对话上下文（角色自然提起"昨天那事"）。"""
    try:
        from app.db.database import async_session_factory
        since = _now_naive() - timedelta(days=3)
        async with async_session_factory() as db:
            recent = (
                await db.execute(
                    select(StorylineEvent)
                    .where(
                        StorylineEvent.character_id == character_id,
                        StorylineEvent.created_at >= since,
                        StorylineEvent.status == "done",
                    )
                    .order_by(StorylineEvent.created_at.desc())
                    .limit(20)
                )
            ).scalars().all()
        if not recent:
            return ""
        # 按剧情线分组，取最近 2 条剧情线的节点摘要
        lines = []
        seen_keys = []
        for ev in recent:
            if ev.storyline_key in seen_keys:
                continue
            seen_keys.append(ev.storyline_key)
            if len(seen_keys) > 2:
                break
            label = {"cold_war": "冷战", "jealousy": "吃醋", "fatigue": "疲惫"}.get(ev.storyline_key, ev.storyline_key)
            parts = []
            async with async_session_factory() as db:
                nodes = await _get_nodes(db, character_id, ev.storyline_key)
            for n in nodes:
                if n.status != "done" or not n.output_text:
                    continue
                parts.append(n.output_text[:40])
            if parts:
                lines.append(f"- 【{label}】" + "；".join(parts[:max_lines]))
        return "\n".join(lines[:max_lines]) if lines else ""
    except Exception as e:
        _logger.warning("Storyline recall build failed char=%d: %s", character_id, e)
        return ""