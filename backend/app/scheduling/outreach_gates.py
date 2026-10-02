"""outreach 投放闸与标注（A20 批 2）

本模块自 ``app/scheduling/arbiter.py`` 逐字节搬入（A20 批 2，2026-10-02）。
边界＝**只做 outreach 投放闸判定与留痕标注，不发消息、不做执行**。

logger 名故意保留旧名 ``scheduler.arbiter``（D-1 已定）：台账、告警与排障都按
``scheduler.arbiter`` 关键字检索日志，换名等于排障口径全变。
"""
from datetime import datetime

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.character import ProactiveTriggerLog
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

# B1-③：接触意图选择的纯函数与素材结构（_collect_outreach_materials / _annotate_outreach_plan 用）
from app.domain.proactivity import outreach as _oc
# 2026-09-13（Codex 交接 §二）：outreach 投放口径三闸纯决策层（时段窗口 / 类型配比 / 单会话限频）
from app.domain.proactivity.pacing import (
    FLAG_HOUR_WINDOW,
    FLAG_SESSION_RATE,
    FLAG_TYPE_MIX,
    LOW_YIELD_TYPES,
    SESSION_RATE_TYPES,
    TYPE_MIX_COUNTED_TYPES,
    gate_active,
    hour_window_allows,
    session_rate_allows,
    type_mix_allows,
)
# 架构地图断点 #1 · V2b（2026-09-29）：pacing 读开关走端口，本处只做注入接线（判定逻辑不变）
from app.application.proactivity_pacing_ports import production_pacing_ports as _pacing_ports
# 批 1 已下沉 gates 的时钟与「已发送」计数：搬进来的函数体在本模块命名空间解析这些裸名，
# 故直接从 gates 取（绕回 arbiter 会成环）
from app.scheduling.gates import (
    _cn_hour_now,
    get_daily_sent_count,
    get_session_daily_sent_count,
    get_session_last_sent_at,
)

_logger = get_logger("scheduler.arbiter")


# ── B1-③（方案 §5.4）：主动接触意图层接线辅助（纯函数决策 + IO 素材采集）──


async def _outreach_enabled(user_id=None) -> bool:
    """Feature Flag：proactive_outreach_v2（默认关）。关=intent 不参与、走旧链路零行为。

    batch G：按账号解析（缺 user_id 回落全局值，fail-open）；判据语义不变（默认关→
    不走 intent 路径），仅取值从全局 AGENT_FLAGS 改为按 user_id 的 resolve_flag 解析链。
    """
    try:
        from app.application.flag_service import resolve_flag
        return await resolve_flag("proactive_outreach_v2", user_id)
    except Exception:
        return False


async def _collect_outreach_materials(candidate: dict) -> "_oc.OutreachMaterials":
    """收集本次接触的真实素材（只判断有没有，不拼大段文本；任一失败降级为无）。

    方案 §5.4：open_loop≈有新鲜进行中话题/目标；shared≈有共同经历记忆；
    interest≈有用户兴趣记忆；life≈AI 此刻有生活小事（current_status）。全部 fail-open。
    """
    char_id = candidate.get("character_id")
    user_id = candidate.get("user_id")
    has_open_loop = has_shared = has_interest = has_life = False
    if char_id and user_id:
        try:
            from app.agent.topic_tracker import load_fresh_active_topics_text
            has_open_loop = bool(await load_fresh_active_topics_text(char_id, user_id))
        except Exception:
            pass
        try:
            from app.memory import search_memories
            has_shared = bool(await search_memories(char_id, query="和用户一起经历的事 用户说过的重要的事 用户的近况", limit=2,
                                                    user_id=user_id))  # A2 M0-4：透传调用者（hook ctx）
            has_interest = bool(await search_memories(char_id, query="用户的兴趣爱好偏好和喜欢的东西", limit=2,
                                                      user_id=user_id))  # A2 M0-4：透传调用者（hook ctx）
        except Exception:
            pass
    try:
        has_life = bool(candidate.get("current_status"))
    except Exception:
        pass
    return _oc.OutreachMaterials(has_open_loop, has_shared, has_interest, has_life)


async def _get_recent_outreach_intents(character_id: int, limit: int = 2) -> list[str]:
    """从最近主动触发日志的 trigger_reason 反查历史意图（零 schema 变更）；失败返回空。

    只统计 decision='approved' 的行（已实际通过并执行的主动消息），避免把被限额拒的
    候选重复计入；格式为 trigger_reason 内 `outreach=<intent>`（见 log_trigger_candidate）。
    """
    out: list[str] = []
    try:
        import re as _re
        async with async_session_factory() as _db:
            rows = (await _db.execute(
                select(ProactiveTriggerLog.trigger_reason)
                .where(
                    ProactiveTriggerLog.character_id == character_id,
                    ProactiveTriggerLog.decision == "approved",
                    ProactiveTriggerLog.trigger_reason.is_not(None),
                    ProactiveTriggerLog.trigger_reason.like("%outreach=%"),
                )
                .order_by(ProactiveTriggerLog.created_at.desc())
                .limit(8)
            )).scalars().all()
        for raw in rows:
            _m = _re.search(r"outreach=([a-z_]+)", raw or "")
            if _m and _m.group(1) in _oc.ALL_INTENTS:
                out.append(_m.group(1))
            if len(out) >= limit:
                break
    except Exception:
        pass
    return out


async def _shadow_drive_note(item: dict, char_id: int, user_id: int, plan) -> None:
    """A4 批3 M1b2（影子改判，**不改发送**）：懒结算水位 → 算「若按驱力定调会选哪个」→ 只写留痕。

    口径：
    - settle 时机遵守设计 §R8 懒结算——只结算「本次要处理的（角色, 用户）」，不做全量刷屏；
    - commit：本钩子自开 session ⇒ 自己 commit（M1b1 把仓储层钉成只 add/flush，提交责任在
      持 session 的一方；钩子不提交＝影子水位静默丢失）；
    - flag 关 ⇒ 在开 session 之前先读一次内存闸，连连接都不建立（零额外查询，逐字节旧行为）；
    - candidate 字段 / prompt / 发送条数一律不动：结果只进 item 的日志标记与 M0 观测暂存。
    """
    from app.application import relational_drive_service as _drive
    from app.domain.relational import drives as _dv

    if not _drive.shadow_enabled():
        return
    async with async_session_factory() as db:
        levels = await _drive.settle(db, char_id, user_id)
        await db.commit()
    drive = _dv.top_candidate_drive(levels)
    would = _dv.DRIVE_TO_INTENT.get(drive) if drive else None
    level = float(levels.get(drive) or 0.0) if drive else 0.0
    item["_drive_note"] = (
        f"[drive={drive}:{level:.1f}→would={would or 'none'}|did={plan.intent or 'none'}]"
        if drive else "[drive=none]"
    )
    trace = _OUTREACH_SEND_TRACE.get(char_id)
    if trace is not None:
        # 与 M0 三键同一个 dict（有暂存才写）。影子期口径＝「按驱力会选的」，M2 生效期换成「实际参与的」
        trace["shadow_drive"] = drive or ""
        trace["shadow_intent"] = would or ""
        trace["level_at_send"] = level


async def _annotate_outreach_plan(item: dict, char_id: int, mats_cache: dict, recent_cache: dict,
                                  char_has_unfinished: bool = False) -> None:
    """run_tick 汇总层统一"意图选择"：分级 + 素材前提 + 避开最近意图 → 写回 candidate。

    flag 关时不调用本函数（candidate 不动 → 零变化）。任一步失败均静默回退（intent=None，走旧链路）。

    ``char_has_unfinished``（批 4 M2-b2 防线 2②）：本 tick 该角色是否已有 ``unfinished_topic``
    候选。为 True 时念头池**本 tick 不供给**（让位给 unfinished_topic，避免两个通道在
    「用户上次说了一半的事」上各开口一次）——只影响 thought 素材，不影响意图选择与发送。
    """
    cand = item.get("candidate") or {}
    _uid = cand.get("user_id")
    if not _uid:
        return
    try:
        if char_id not in mats_cache:
            mats_cache[char_id] = await _collect_outreach_materials(cand)
        if char_id not in recent_cache:
            recent_cache[char_id] = await _get_recent_outreach_intents(char_id, limit=2)
        _tier = _oc.staleness_tier(cand.get("idle_minutes"))
        _plan = _oc.select_outreach(_tier, mats_cache[char_id], recent_cache[char_id])
        cand["outreach_intent"] = _plan.intent
        cand["outreach_plan"] = {
            "tier": _plan.tier,
            "allow_active_topics": _plan.allow_active_topics,
            "allow_storyline": _plan.allow_storyline,
            "allow_recall": _plan.allow_recall,
            "memory_query": _plan.memory_query,
            "must_return_question": _plan.must_return_question,
        }
        # A4 批3 M0：把本次实际使用的意图 / 档位 / 素材短标识暂存，供发送留痕点取走（只观测）
        _mats = mats_cache[char_id]
        _OUTREACH_SEND_TRACE[char_id] = {
            "intent": str(_plan.intent or ""),
            "tier": str(_plan.tier or ""),
            "materials": [
                k for k, has in (
                    ("open_loop", _mats.has_open_loop),
                    ("shared", _mats.has_shared_memory),
                    ("interest", _mats.has_user_interest),
                    ("life", _mats.has_life_now),
                ) if has
            ],
        }
        # A4 批3 M1b2：影子改判留痕（旁路观测；单独吞异常——绝不影响上面的意图选择结果与后续发送）
        try:
            await _shadow_drive_note(item, char_id, _uid, _plan)
        except Exception as e:
            _logger.debug("drive shadow skipped char=%d: %s", char_id, e)
        # ── 批 4 M2-b1（2026-10-01）：念头池供料（素材层，设计 §3.1「供给发生在 _annotate_outreach_plan 之内」）──
        # 先判 flag／灰度再查库：v1 关或角色未命中白名单 ⇒ 一次 SQL 都不发（thought_pool_v1_allowed 不查库）。
        # 只在 outreach 管辖类型内供给（本函数仅由 PROACTIVE_OUTREACH_TYPES 候选调用，见 run_tick :939）；
        # 产出物只是 cand["thought"]（文本）+ cand["thought_id"]（供发送留痕绑定/后续释放结算），
        # **不改 intent、不改 plan、不碰任何 §3.1 频控闸**（念头池无发送权，设计 §3.3 红线 1/2）。
        # 批 4 M2-b2（2026-10-01）补两条供给防线（设计 §3.2）：
        #   防线 4（state_trigger 类型白名单）：仅在 PROACTIVE_OUTREACH_TYPES 内供给，其余类型
        #     （timer/special/state_trigger/memory_review/pet_*/unfinished_topic/life_regression/
        #     prospective_intent/plugin）一律不供给——照本函数 :739-741 的早退写法显式再判一道
        #     （run_tick 已按类型门控调用，这里是纵深防御，防未来新增调用点漏判）；
        #   防线 2②（与 unfinished_topic 双向排除·供给侧）：本 tick 该角色已有 unfinished_topic
        #     候选 ⇒ 念头池本 tick 不供给（让位，照 sources/rhythm.py 让位写法），避免两通道抢同一句话。
        try:
            if item.get("type") not in PROACTIVE_OUTREACH_TYPES:
                pass                      # 防线 4：非 outreach 类型不供给（早退，不查库）
            elif char_has_unfinished:
                pass                      # 防线 2②：本 tick 已有 unfinished_topic 候选 ⇒ 让位不供给
            else:
                from app.application.thought_pool_service import thought_pool_v1_allowed, fetch_one_thought
                if thought_pool_v1_allowed(char_id):
                    async with async_session_factory() as _tdb:
                        _th = await fetch_one_thought(_tdb, char_id, _uid, intent=_plan.intent)
                    if _th and _th.get("text"):
                        cand["thought"] = _th["text"]
                        cand["thought_id"] = _th.get("id")
        except Exception as e:
            _logger.debug("thought pool supply skipped char=%d: %s", char_id, e)
    except Exception as e:
        _logger.warning("outreach annotate failed char=%d: %s", char_id, e)


def _mark_gate(item: dict, gate: str) -> None:
    """闸门命中留痕：写 ``candidate.trigger_reason`` 追加 ``[gate=...]``（交接 §三观测口径）。

    run_tick 随后调 ``log_trigger_candidate(item, False)`` → ``proactive_trigger_logs``
    的 trigger_reason 带 ``[gate=hour|type|session_rate]``、reject_reason =
    ``rejected / [gate=...]``，可按天统计各闸拦截量（rejected 行本身受既有 5 分钟节流，
    见 ``log_trigger_candidate``）。
    """
    item["_gate"] = gate
    cand = item.get("candidate")
    if isinstance(cand, dict):
        reason = str(cand.get("trigger_reason") or "")
        marker = f"[gate={gate}]"
        if marker not in reason:
            cand["trigger_reason"] = f"{reason} {marker}".strip()
    _logger.info("Proactive %s skipped: [gate=%s]", item.get("type"), gate)


async def _user_active_hours(user_id) -> list:
    """读用户已学到的活跃时段（user_rhythm **只读**，不触发重学/写库）；无数据/失败 → []。"""
    if not user_id:
        return []
    try:
        from app.scheduling.user_rhythm import get_active_hours
        return await get_active_hours(user_id)
    except Exception:
        return []


async def _pacing_gate(
    item: dict,
    etype: str,
    char_id: int,
    candidate: dict,
    *,
    cn_hour: int | None = None,
    now: datetime | None = None,
) -> str | None:
    """outreach 投放口径三闸（2026-09-13 交接 §二）：返回命中的闸门名（hour/type/session_rate）或 None。

    - 三个开关全关（默认）或角色不在灰度白名单 → 立即 None：**不查库、不拦截、零行为变化**；
    - ① hour：低效类型（ai_care/life_regression/memory_review[/_contextual]）仅 12:00–23:00 投放；
    - ② type：memory_review ≤6/日、ai_care ≤4/日（按已发送计数）；
    - ③ session_rate：同 (character_id, session_id) ≤8/日 且最小间隔 ≥45 分钟（按已发送计数，
      与 ``MAX_PER_HOUR`` 叠加不替换）；候选不带 session_id 时按最新会话兜底；
    - 命中由调用方 ``_mark_gate`` + ``return False`` 走原 rejected 日志链路；
    - 任一步异常 fail-open（返回 None 照常投放），绝不阻塞主动链路。
    """
    try:
        session_id = candidate.get("session_id")
        on_hour = gate_active(char_id, session_id, FLAG_HOUR_WINDOW, ports=_pacing_ports)
        on_mix = gate_active(char_id, session_id, FLAG_TYPE_MIX, ports=_pacing_ports)
        on_rate = gate_active(char_id, session_id, FLAG_SESSION_RATE, ports=_pacing_ports)
        if not (on_hour or on_mix or on_rate):
            return None
        if cn_hour is None:
            cn_hour = _cn_hour_now()

        # ① 时段窗口闸：低效类型窗口外跳过（个性化活跃时段只扩不缩）
        if on_hour and etype in LOW_YIELD_TYPES:
            if not hour_window_allows(etype, cn_hour):
                _hours = await _user_active_hours(candidate.get("user_id"))
                if not hour_window_allows(etype, cn_hour, active_hours=_hours):
                    return "hour"

        # ② 类型配比闸：每角色每日上限（按「已发送」计数）
        if on_mix:
            _counted = TYPE_MIX_COUNTED_TYPES.get(etype)
            if _counted is not None:
                _sent = await get_daily_sent_count(char_id, _counted)
                if not type_mix_allows(etype, _sent):
                    return "type"

        # ③ 单会话限频闸：日上限 + 最小间隔（按「已发送」计数；与 MAX_PER_HOUR 叠加）
        if on_rate and etype in SESSION_RATE_TYPES:
            if session_id is None:
                _uid = candidate.get("user_id")
                if _uid:
                    from app.application.chat_service import get_latest_session_id
                    session_id = await get_latest_session_id(_uid, char_id)
                # 兜底解出会话后按会话维度重算灰度桶（比例 <1 时同一角色不同会话可不同命中）
                if session_id is not None:
                    on_rate = gate_active(char_id, session_id, FLAG_SESSION_RATE,
                                          ports=_pacing_ports)
            if on_rate and session_id is not None:
                _sent = await get_session_daily_sent_count(char_id, session_id)
                _last = await get_session_last_sent_at(char_id, session_id)
                _minutes = None
                if _last is not None:
                    _last_naive = _last.replace(tzinfo=None) if _last.tzinfo else _last
                    _minutes = ((now or now_naive_utc()) - _last_naive).total_seconds() / 60.0
                if not session_rate_allows(_sent, _minutes):
                    return "session_rate"
        return None
    except Exception as e:
        _logger.warning("outreach pacing gate fail-open: %s", e)
        return None


# ── 末尾导入（顺序有意为之）：所有权仍在 arbiter 的两个名字 ──
# _annotate_outreach_plan 的函数体要在**本模块**命名空间解析 PROACTIVE_OUTREACH_TYPES 与
# _OUTREACH_SEND_TRACE（后者由 arbiter 发送侧 pop 取走，必须是同一个 dict 对象）。放在文件最后
# 才打破循环：arbiter 具名重导出会 import 本模块，若在函数定义之前 import arbiter，则「先导入本
# 模块」那条路会因为 arbiter 拿不到尚未定义的 8 个函数而 ImportError。
from app.scheduling.arbiter import PROACTIVE_OUTREACH_TYPES, _OUTREACH_SEND_TRACE  # noqa: F401
