"""主动搭话类执行器 — greeting / proactive_chat / goodnight / status_update / motivation（A20 批 4b）

自 ``arbiter._execute`` 逐字节搬入的一条分支（五键共用，2026-10-02）。口径不变：**剧情线模式**
——一次生成完整剧情 → 切片落库（第一段 pending 立即由 3 秒循环发）→ 由 ``flush_storyline_items``
逐条发送；本函数只负责「生成 + 排队」，返回 True＝已排队，不＝已发出。

机械改写只有三类（与任务书一致，判定/文案/返回值一字未改）：

1. 分支体缩进归零，``etype`` 由 ``item["type"]`` 取（原梯子里它是分支条件里的循环变量）；
   ``if etype == "motivation":`` 的子分支原样留在本函数内。
2. 被 tests/ 打桩的两个依赖经 :class:`GateBundle` 现取：取库 ``g.session_factory()``
   （``setattr(arbiter, "async_session_factory", …)`` 实测 21 处）、应用日界 ``g.app_day_start()``
   （``setattr(arbiter, "app_day_start_utc", …)``，见 tests/test_daily_window_alignment.py）。
   本模块**不得** import 这两名（``from app.db.database import`` / ``from app.utils.timeutil
   import app_day_start_utc``），否则桩静默失效 ⇒ 退化成真查库。
3. 批 1 下沉 ``scheduling/gates`` 的节流闸（``inactive_char_skip`` / ``get_motivation_approved_count``
   / ``get_last_proactive_time`` / ``unreplied_cooldown_active`` / ``get_recent_proactive_messages``）
   与观测暂存 ``_OUTREACH_SEND_TRACE`` **经 ``arbiter`` 模块属性调用**（函数体内 import，不上提顶层）。
   理由与 ``outreach_gates`` 那批同名闸函数相反，别搞混：这些名字的桩**仍在 arbiter 侧**
   （tests/ 里 ``setattr(arbiter, "get_last_proactive_time", …)`` 等），改到 ``gates`` 侧解析会当场绕过；
   而「经模块属性取」恰恰让桩继续生效——这也正是 ``life_share`` / ``state_triggers`` /
   ``sources/strategy`` 一直在用的写法（如 ``arbiter.inactive_char_skip(cid)``）。

``generate_proactive_event`` / ``get_rhythm_weight`` / ``maybe_extract_topics`` 原本就是分支体内的
局部 import（tests/ 打的是各自模块属性），照原样留在函数内。logger 名故意保留
``scheduler.arbiter``（D-1）。
"""
import uuid
from datetime import datetime, timedelta, timezone

from app.domain.proactivity import outreach as _oc
from app.domain.proactivity.decision import (
    MIN_PROACTIVE_INTERVAL_MINUTES,
    MOTIVATION_MAX_PER_6H,
    MOTIVATION_MAX_PER_DAY,
)
from app.models.character import ProactiveStorylineItem
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler
from app.utils.async_tasks import spawn_background
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc, to_naive_utc

_logger = get_logger("scheduler.arbiter")


@handler("greeting")
@handler("proactive_chat")
@handler("goodnight")
@handler("status_update")
@handler("motivation")
async def run_outreach_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 主动搭话类：greeting / proactive_chat / goodnight / status_update
    # 改为"剧情线"模式：一次生成完整剧情 → 切片 → 按时间逐条发送
    from app.scheduling import arbiter          # 闸函数与观测暂存的桩仍在 arbiter 侧（见模块 docstring ③）
    etype = item["type"]
    # B1-③ 配额让位（2026-09-08）：近 24h 无任何用户消息的角色直接停发——不生成候选、
    # 不消耗每日配额（approved 计数不增）、不发起 LLM 生成；额度留给有互动的角色。
    if await arbiter.inactive_char_skip(char_id):
        _logger.info(
            "Proactive msg char=%d skipped: inactive char (no user msg within %sh)",
            char_id, _oc.INACTIVE_CHAR_WINDOW_HOURS,
        )
        return False
    from app.scheduling.message_generator import generate_proactive_event
    # #28 ②：用户作息学习——低优先级主动消息在学到的活跃时段外降优先级/推迟（arbiter 时段权重）
    try:
        _uid = candidate.get("user_id")
        if _uid:
            from app.scheduling.user_rhythm import get_rhythm_weight
            _cn_hour = datetime.now(timezone(timedelta(hours=8))).hour
            if await get_rhythm_weight(_uid, _cn_hour) <= 0.0:
                _logger.info("Proactive msg char=%d skipped: user rhythm off-peak", char_id)
                return False
    except Exception as _e:
        _logger.warning("user_rhythm check failed: %s", _e)
    # 独立想念通道（#33，2026-08-17）：motivation 走独立配额（每 6h 1 条 + 每日 ≤2 条，
    # 不占普通每小时 2 条额度、跳过 90 分钟最小间隔）；其余类型保留最小间隔保护
    if etype == "motivation":
        _now_u = datetime.now(timezone.utc).replace(tzinfo=None)
        if await arbiter.get_motivation_approved_count(char_id, _now_u - timedelta(hours=6)) >= MOTIVATION_MAX_PER_6H:
            _logger.info("Proactive msg char=%d skipped: motivation 6h limit", char_id)
            return False
        if await arbiter.get_motivation_approved_count(char_id, g.app_day_start()) >= MOTIVATION_MAX_PER_DAY:
            _logger.info("Proactive msg char=%d skipped: motivation daily limit", char_id)
            return False
    else:
        # 最小间隔保护：避免同一角色短时间内连发（内容也容易重复）
        last_proactive = await arbiter.get_last_proactive_time(char_id)
        if last_proactive is not None:
            if now_naive_utc() - to_naive_utc(last_proactive) < timedelta(minutes=MIN_PROACTIVE_INTERVAL_MINUTES):
                _logger.info("Proactive msg char=%d skipped: min interval", char_id)
                return False

    # 连续不回复冷却：最近几条主动消息用户均未回复 → 暂停主动搭话 24h（防骚扰）
    if await arbiter.unreplied_cooldown_active(char_id, candidate["user_id"]):
        _logger.info("Proactive msg char=%d skipped: unreplied cooldown", char_id)
        return False
    context = candidate.get("last_context", "")
    previous_messages = await arbiter.get_recent_proactive_messages(char_id, 2)
    # B1-③：读 run_tick 汇总层选好的接触意图（flag 关/未标注 → None，走旧链路零变化）
    outreach_intent = candidate.get("outreach_intent")
    outreach_plan = candidate.get("outreach_plan")
    if not outreach_intent:
        # A4 批3 M0：本次未走接触意图链路 → 丢弃可能残留的观测暂存（宁可少留痕，也不给旧链路发送错标 intent/tier）
        arbiter._OUTREACH_SEND_TRACE.pop(char_id, None)
    segments, event_reasoning = await generate_proactive_event(
        character_name=candidate["character_name"],
        character_bio=candidate["character_bio"],
        character_personality=candidate["character_personality"],
        character_id=char_id,
        user_id=candidate["user_id"],
        current_status=candidate.get("current_status", ""),
        relationship_summary=candidate.get("relationship_summary", ""),
        user_name=candidate["nickname"] or candidate["username"],
        last_context=context,
        previous_messages=previous_messages,
        idle_minutes=candidate.get("idle_minutes"),
        behavior=etype,
        return_reasoning=True,
        outreach_intent=outreach_intent,
        outreach_plan=outreach_plan,
        session_id=candidate["session_id"],
        thought=candidate.get("thought"),  # 批 4 M2-b1：念头池素材（flag 关 ⇒ 恒 None ⇒ 逐字节旧 prompt）
    )
    if not segments:
        return False
    # 落库排队：第一段立即发送，其余每 3 秒发一段（同一次事件，按顺序切开）
    group_id = uuid.uuid4().hex
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    async with g.session_factory() as db:
        for seq, content in enumerate(segments):
            db.add(ProactiveStorylineItem(
                character_id=char_id,
                session_id=candidate["session_id"],
                user_id=candidate["user_id"],
                group_id=group_id,
                seq=seq,
                content=content[:500],
                reasoning=(event_reasoning if seq == 0 else None),
                send_at=now_naive + timedelta(seconds=seq * 3),
                status="pending",
            ))
        await db.commit()
    _logger.info("Proactive event queued for char=%d (%d segments)", char_id, len(segments))

    # 认知循环 v2.1：主动消息也参与话题追踪（让话题状态随主动/被动共同演进；失败静默）
    try:
        from app.agent.topic_tracker import maybe_extract_topics
        spawn_background(
            maybe_extract_topics(
                char_id, candidate["user_id"], "", " ".join(segments),
            ),
            name=f"topics-{char_id}",
        )
    except Exception:
        pass
    return True
