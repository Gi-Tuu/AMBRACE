"""仲裁器 — 统一决策：收集事件源 → 按优先级裁定 → 限额保护 → 执行"""
import json
import time as _time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.chat import ChatMessage
from app.models.character import ProactiveMessageLog, ProactiveSettings  # noqa: F401  # A20 批 1：ProactiveSettings 已随 get_dnd_window 下沉，命名空间保留
from app.models.character import ProactiveStorylineItem
from app.models.character import ProactiveTriggerLog
from app.scheduling.unfinished_topic import run_unfinished_topic  # noqa: F401  # A20 批 4a：分支已下沉 executors/story，命名空间保留
from app.scheduling.life_regression import run_life_regression  # noqa: F401  # 同上
from app.scheduling.prospective_intent import run_prospective_due  # noqa: F401  # Ariadne 模块G（2026-09-04）；批 4a 分支已下沉 executors/story
from app.utils.logger import get_logger
from app.utils.timeutil import app_day_start_utc, now_naive_utc, to_naive_utc

# AMBRACE 3.10：arbiter 事件源 TriggerSource 化——导入 sources 包即触发各源注册
from app.scheduling.sources import SourceContext, all_sources, get_source, to_item_dict

_logger = get_logger("scheduler.arbiter")

# F2-b（2026-08-31）：决策纯函数/常量迁至 domain/proactivity，此处重导出保持兼容
# （monkeypatch arbiter.<name> 仍有效：函数体经本模块命名空间解析）
from app.domain.proactivity.decision import (  # noqa: E402,F401
    CONTEXT_SORT_BONUS,
    MAX_PER_HOUR,
    MIN_PROACTIVE_INTERVAL_MINUTES,
    MOTIVATION_MAX_PER_6H,
    MOTIVATION_MAX_PER_DAY,
    MOTIVATION_SPEAK_THRESHOLD,
    REFLECTION_BONUS,
    REFLECTION_LOOKBACK_DAYS,
    UNREPLIED_COOLDOWN_HOURS,
    UNREPLIED_COOLDOWN_LIMIT,
    USER_ACTIVE_MINUTES,
    _apply_reflection_bonus,
    _context_sort_bonus,
    _in_dnd_window,
    _motivation_score,
    scheduler_gray_character,
)
from app.domain.proactivity.sleep import SLEEP_KEYWORDS, SLEEP_HOUR, SLEEP_SILENCED_TYPES  # noqa: E402,F401
# B1-③（2026-09-04，方案 §5.4）：主动接触意图层纯函数（闲置分级 + 意图选择）
from app.domain.proactivity import outreach as _oc  # noqa: E402
# 2026-09-13（Codex 交接 §二）：outreach 投放口径三闸纯决策层（时段窗口 / 类型配比 / 单会话限频）
from app.domain.proactivity.pacing import (  # noqa: E402,F401
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
from app.application.proactivity_pacing_ports import production_pacing_ports as _pacing_ports  # noqa: F401  # A20 批 2：_pacing_gate 已随本端口下沉 outreach_gates，命名空间保留

# 审计 P1-06：rejected 触发日志节流（同角色同类型最小间隔秒，approved 必记）
REJECTED_LOG_THROTTLE_SECONDS = 300
_rejected_log_cache: dict[tuple[int, str], float] = {}

# B1-③：可走接触意图选择的主动搭话事件类型（调用 generate_proactive_event 的几条）
PROACTIVE_OUTREACH_TYPES = ("greeting", "proactive_chat", "goodnight", "status_update", "motivation")

# A4 批3 M0（2026-09-27，只埋点、零语义）：本次接触的 intent/tier/materials 观测暂存。
# 意图选择发生在 run_tick 汇总层（_annotate_outreach_plan），而 proactive_message_logs 的
# 留痕发生在 3 秒切片循环真正发送时（flush_storyline_items → send_to_session），两处不在同一
# 调用栈，故用「按角色覆盖写、发送时取走」的进程内暂存把三个键送到唯一留痕点。
# 只观测：候选/频控/发送条数一律不读它；摘除本字典与下面两处读写即回到逐字节旧行为。
_OUTREACH_SEND_TRACE: dict[int, dict] = {}




# A20 批 1（2026-10-02）：节流闸与只读查询下沉到 scheduling/gates，此处具名重导出。
# ⚠ 这些名字必须留在本模块命名空间：tests/ 里 197 处 monkeypatch.setattr(arbiter, …)
#   靠的就是「调用方在 arbiter 全局里解析裸名」。删任何一行都会让对应测试退化成真查库（静默变绿）。
from app.scheduling.gates import (  # noqa: F401
    has_user_said_sleep,
    get_hourly_active_count,
    get_last_proactive_time,
    get_motivation_approved_count,
    _cn_hour_now,
    get_daily_sent_count,
    get_session_daily_sent_count,
    get_session_last_sent_at,
    get_recent_proactive_messages,
    unreplied_cooldown_active,
    get_dnd_window,
    is_dnd_now,
    is_user_active,
    get_hours_since_last_user_message,
    inactive_char_skip,
    has_pending_timer,
    has_pending_storyline,
    get_active_characters,
)

# A20 批 2（2026-10-02）：outreach 投放闸与标注下沉 outreach_gates，此处具名重导出。
# ⚠ 理由同批 1：tests/ 的 monkeypatch.setattr(arbiter, …) 靠「调用方在 arbiter 命名空间解析裸名」。
from app.scheduling.outreach_gates import (  # noqa: F401
    _annotate_outreach_plan,
    _collect_outreach_materials,
    _get_recent_outreach_intents,
    _mark_gate,
    _outreach_enabled,
    _pacing_gate,
    _shadow_drive_note,
    _user_active_hours,
)

# A20 批 3a（2026-10-02）：跨类型前置闸下沉 executors/guards，本模块只负责现取注入。
# ⚠ guards 不得直接 import 闸函数：tests/ 的 monkeypatch.setattr(arbiter, …) 靠「调用方在
#   arbiter 命名空间解析裸名」，解析点搬走就会静默绕过打桩去查真库。
from app.scheduling.executors import GateBundle, pre_gates

# ── 事件源采集 ──

_DEFAULT_CTX = SourceContext()

async def collect_timer_events() -> list[dict]:
    """到期定时承诺（最高优先级）。逻辑见 scheduling/sources/timer.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("timer").collect(_DEFAULT_CTX)]


async def collect_special_events() -> list[dict]:
    """生日 / 节日 / 认识纪念日。逻辑见 scheduling/sources/special.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("special").collect(_DEFAULT_CTX)]


# ── A40（A37 批 3，审计 §4 批 3 · C28）：切片 flush 侧的发送前复检 ──────────────────
# 生成侧查过 `is_user_active` / `MAX_PER_HOUR`（`executors/guards.pre_gates`），但切片从生成到真正
# 发出最长可跨 **2 小时**（下面的过期保护窗），期间用户可能已经醒来聊起来、这一小时的额度也可能
# 已被别的通道用掉——而 flush 侧过去一栏都不重读（审计 §2 C28 的「闸③」列写着「无」）。
# 本函数补的就是这"最后一眼"：**阈值全部复用生成侧同一批常量与闸函数**（不自创第二套口径），
# 也**只碰速率/活跃**这两件生成侧已在查的事——新鲜度判定在出口 `send_to_session` 的闸③里做，
# 两边不许互相塞（守卫 tests/test_gates_semantics_split_a37.py 钉这条）。
# 档位（两档默认关，读不到按关）：影子＝只留痕照发；实拦＝命中即不发送，切片**保持 pending**
# 等下一轮重试（超 2h 由上面的过期保护作废），既不计 sent 也不做任何消费标记。
# 读失败＝照发（fail-open），但把读失败写进 INFO：绝不把"我读不到"变成"不发"。
async def _flush_recheck_reason(item_obj) -> str:
    """返回命中的复检原因（``""``＝放行）。读失败一律 ``""``（照发）并 INFO 留痕。"""
    try:
        if await is_user_active(item_obj.character_id, item_obj.user_id):
            return "user_active"
        if await get_hourly_active_count(item_obj.character_id) >= MAX_PER_HOUR:
            return "hourly_cap"
    except Exception as e:
        _logger.info("Storyline flush 复检读失败照发 item=%s: %s", getattr(item_obj, "id", "?"), e)
        return ""
    return ""


async def flush_storyline_items() -> int:
    """快速发送到期的主动事件切片（独立 3 秒循环调用，不走 30 秒仲裁 tick）。

    同一事件的切片正常情况下每个循环至多发一段（group 去重），保持 3 秒间隔；
    停机恢复时可能一次补发多段，属正常追赶。
    """
    from app.scheduling import scheduler as engine
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    # 深夜静默：北京时间 0:00-6:59 不发送剧情线切片（用户睡眠时段不打扰）
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    if cn_now.hour < 7:
        return 0
    async with async_session_factory() as db:
        result = await db.execute(
            select(ProactiveStorylineItem)
            .where(
                ProactiveStorylineItem.status == "pending",
                ProactiveStorylineItem.send_at <= now_naive,
            )
            .order_by(ProactiveStorylineItem.send_at.asc())
            .limit(20)
        )
        items = result.scalars().all()
    sent = 0
    seen_groups: set[str] = set()
    for item_obj in items:
        if item_obj.group_id in seen_groups:
            continue
        seen_groups.add(item_obj.group_id)
        # 过期保护：距计划时间超过 2 小时的切片作废（避免停机后补发一堆旧消息）
        if now_naive - item_obj.send_at > timedelta(hours=2):
            async with async_session_factory() as db:
                db_item = await db.get(ProactiveStorylineItem, item_obj.id)
                if db_item:
                    db_item.status = "expired"
                    await db.commit()
            _logger.info("Storyline item %d expired", item_obj.id)
            continue
        # 用户 21 点后说过睡觉 → 剩余剧情线作废，不再打扰
        try:
            if await has_user_said_sleep(item_obj.character_id, item_obj.user_id):
                async with async_session_factory() as db:
                    db_item = await db.get(ProactiveStorylineItem, item_obj.id)
                    if db_item:
                        db_item.status = "expired"
                        await db.commit()
                _logger.info("Storyline item %d expired (user sleep)", item_obj.id)
                continue
        except Exception as e:
            _logger.warning("Storyline sleep check failed: %s", e)
        # A40 闸③·发送侧复检（两档默认关＝下面整段不执行，零额外查询）
        _g3_shadow, _g3_enforce = engine.gate3_flags()
        if _g3_shadow or _g3_enforce:
            _g3 = await _flush_recheck_reason(item_obj)
            if _g3:
                _logger.info("Storyline flush item=%d 闸③%s reason=%s%s",
                             item_obj.id, "命中" if _g3_enforce else "（影子）", _g3,
                             "" if _g3_enforce else "·照发")
                if _g3_enforce:
                    continue
        _reasoning = (item_obj.reasoning or "").strip() if getattr(item_obj, "reasoning", None) else ""
        _extra = None
        if item_obj.seq == 0:
            # A4 批3 M0：留痕补 intent/tier/materials 三个 JSON 附加键（旧读取方无感，既有键不动）。
            # 无暂存 = 本次未走接触意图链路 → 不写键，payload 与改动前逐字节一致。
            import json as _json
            _meta = {"reasoning": _reasoning} if _reasoning else {}
            _meta.update(_OUTREACH_SEND_TRACE.pop(item_obj.character_id, None) or {})
            if _meta:
                _extra = _json.dumps(_meta, ensure_ascii=False)
        _res = await engine.send_to_session(
            item_obj.session_id, item_obj.character_id, item_obj.user_id,
            item_obj.content, message_type="storyline",
            log_proactive=(item_obj.seq == 0),
            extra_meta=_extra,
            # A40 闸③快照锚：审计 §3.2 明示 `send_at` 就是天然快照时刻（不加列）。
            # 下面 `_res.ok is False` 分支已经保证"被拦⇒保持 pending"，这里只是把锚交出去。
            snapshot_at=item_obj.send_at,
        )
        _sent = getattr(_res, "ok", None)
        if _sent is False:
            # A37 批 1（审计 §1.4 V3）：主题熔断命中时 send_to_session 既不写库也不推送，
            # 而这里过去**照旧把切片标成 sent** ⇒ 一条从没到过用户面前的消息在数据上等于发成功，
            # 且 `proactive_message_logs` 里查不到任何痕迹（静默蒸发）。
            # 现在按返回值定终态：保持 pending 等下一轮（超 2h 由上面的过期保护作废），
            # 不计 sent、不做"开口释放"——没开口就不该释放驱力水位。
            _logger.info("Storyline flush item=%d not sent (reason=%s)，保持 pending",
                         item_obj.id, getattr(_res, "reason", ""))
            continue
        if item_obj.seq == 0 and (_meta or {}).get("intent"):
            # A4 批 3 / T1 M2a（2026-10-01）：开口**部分释放**（只写水位、不参与投放决策；
            # flag 关 ⇒ 零 SQL；异常静默，绝不影响发送链路）
            try:
                from app.application import relational_drive_service as _rds
                if _rds.release_enabled("open", item_obj.character_id):
                    async with async_session_factory() as _rdb:
                        await _rds.apply_open_release(
                            _rdb, item_obj.character_id, item_obj.user_id, _meta.get("intent")
                        )
                        await _rdb.commit()
            except Exception as _e:
                _logger.debug("Drive open release skipped item=%s: %s", item_obj.id, _e)
        async with async_session_factory() as db:
            db_item = await db.get(ProactiveStorylineItem, item_obj.id)
            if db_item:
                db_item.status = "sent"
                await db.commit()
        sent += 1
    if sent:
        _logger.info("Storyline flush sent %d item(s)", sent)
    return sent


async def _session_last_message_at(session_id: int) -> datetime | None:
    """会话最后一条消息的 created_at（naive UTC），供 idle 计算；无消息返回 None。
    P-fix（2026-08-31）：SSE 流式路径落用户/AI 消息时未更新 chat_sessions.updated_at，
    idle 基准改用消息表真实最新时间，避免停留在上次主动消息（send_to_session）导致 idle 虚高。"""
    try:
        async with async_session_factory() as _db:
            _at = (
                await _db.execute(
                    select(func.max(ChatMessage.created_at))
                    .where(ChatMessage.session_id == session_id)
                )
            ).scalar()
            return _at
    except Exception:
        return None


async def collect_rhythm_events() -> list[dict]:
    """随机节律采样：时间窗 + 概率 + 每日上限 + 计时器互斥。逻辑见 scheduling/sources/rhythm.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("rhythm").collect(_DEFAULT_CTX)]


async def collect_state_trigger_events() -> list[dict]:
    """状态触发兜底（优先级 2）：八维状态达阈值 → 主动消息/朋友圈。逻辑见 scheduling/sources/state_trigger.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("state_trigger").collect(_DEFAULT_CTX)]


async def _compute_motivation(character_id: int) -> float:
    """读取角色八维状态计算动机分；无状态/异常返回 0（失败静默，不影响调度）"""
    try:
        from app.models.character import CharacterState
        async with async_session_factory() as db:
            st = (
                await db.execute(
                    select(CharacterState).where(CharacterState.character_id == character_id)
                )
            ).scalar_one_or_none()
        if st is None:
            return 0.0
        hours = 24.0
        if st.last_activity_at is not None:
            try:
                hours = max(0.0, (now_naive_utc() - to_naive_utc(st.last_activity_at)).total_seconds() / 3600.0)
            except Exception:
                pass
        score = _motivation_score(
            attachment=st.attachment, curiosity=st.curiosity, desire=st.desire,
            mood=st.mood, anger=st.anger, fatigue=st.fatigue,
            hours_since_activity=hours,
        )
        # 「渴望+反思」双驱动（plans #41 ②，2026-08-16）：最近一周有复盘 → 加分
        # （有复盘说明角色"心里有在想的计划/总结"，可聊信号更强；失败静默不加分）
        has_reflection = False
        if score > 0.0:
            try:
                from datetime import timedelta as _td
                from app.models.memory import Memory
                from app.utils.timeutil import beijing_day_start_utc
                async with async_session_factory() as _db:
                    _has = (await _db.execute(
                        select(func.count()).where(
                            Memory.memory_type == "ai_reflection",
                            Memory.character_id == character_id,
                            Memory.created_at >= beijing_day_start_utc() - _td(days=REFLECTION_LOOKBACK_DAYS - 1),
                        )
                    )).scalar() or 0
                has_reflection = int(_has) >= 1
            except Exception:
                pass
        return _apply_reflection_bonus(score, has_reflection)
    except Exception as e:
        _logger.warning("compute_motivation failed char=%d: %s", character_id, e)
        return 0.0


async def collect_motivation_events() -> list[dict]:
    """情感渴望驱动的主动唤醒：渴望度 >= 阈值 → 主动搭话候选（priority=1）。逻辑见 scheduling/sources/motivation.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("motivation").collect(_DEFAULT_CTX)]


async def collect_plugin_events() -> list[dict]:
    """插件主动消息候选（proactive_candidate hook，优先级 1；日限额由插件内部维护）。逻辑见 scheduling/sources/plugin.py（AMBRACE 3.10）。"""
    return [ti.to_dict() for ti in await get_source("plugin").collect(_DEFAULT_CTX)]


# ── 仲裁 ──


async def run_tick() -> list[str]:
    """统一调度：收集 → 去重 → 限额 → 执行。返回执行的日志列表"""
    executed = []

    # 认知循环 v2.1：关系标量每日衰减（长期不互动 trust/attachment 下降；失败静默）
    try:
        from app.application.relationship_decay_ports import production_relationship_decay_ports
        from app.domain.relationship.decay import run_relationship_decay
        await run_relationship_decay(ports=production_relationship_decay_ports)
    except Exception:
        pass

    # 1. 收集（AMBRACE 3.10：统一遍历 all_sources()，单源 try/except 不拖垮仲裁）
    all_items: list[dict] = []
    for src in all_sources():
        try:
            all_items.extend(to_item_dict(ti) for ti in await src.collect(_DEFAULT_CTX))
        except Exception as e:
            _logger.warning("source %s collect failed: %s", src.name, e)

    # 2. 合并并按角色分组（同角色保留全部事件，按优先级降序尝试）
    by_char: dict[int, list[dict]] = {}
    for item in all_items:
        char_id = None
        if item["type"] == "timer":
            char_id = item["event"].character_id
        elif item.get("candidate"):
            char_id = item["candidate"].get("character_id")
        if char_id is None:
            continue
        by_char.setdefault(char_id, []).append(item)

    # 3. 逐角色执行：按优先级从高到低依次尝试，命中（执行成功）即止。
    #    修复"状态触发每 tick 无条件占坑（priority=2）饿死节律/复习/关怀（priority=1）"
    #    → 状态触发未命中时，随机节律（主动搭话/发朋友圈等）得以执行。
    executed_chars: set[int] = set()
    # B1-③（方案 §5.4）：run_tick 汇总层统一"意图选择"——素材/最近意图按角色缓存，避免重复采集
    _mats_cache: dict[int, _oc.OutreachMaterials] = {}
    _recent_cache: dict[int, list[str]] = {}
    for char_id, items in sorted(by_char.items()):
        # 渴望度（0-1）作为同优先级下的二级排序键：依恋/好奇/久未互动强的角色先开口
        motivation = await _compute_motivation(char_id)
        for it in items:
            it["motivation"] = max(it.get("motivation", 0.0), motivation)
        # 批 4 M2-b2 防线 2②：本 tick 该角色是否已有 unfinished_topic 候选（供念头池供给侧让位）。
        # 只读已汇总的 items（零额外查询）；让位只作用于 thought 素材，不影响任何频控闸与发送。
        _char_has_unfinished = any(it.get("type") == "unfinished_topic" for it in items)
        items.sort(
            key=lambda it: (
                it["priority"],
                it.get("motivation", 0.0) + _context_sort_bonus(it.get("candidate")),
            ),
            reverse=True,
        )
        for item in items:
            _t0 = _time.monotonic()
            # R1（2026-09-09）：只有 _execute 抛异常才算「真执行失败」；正常 return False = 本轮未触发
            _exec_error = False
            try:
                # B1-③：flag 开 + 主动搭话类型 → 选意图并写回 candidate（flag 关=不动，零变化）
                if await _outreach_enabled((item.get("candidate") or {}).get("user_id")) and item.get("type") in PROACTIVE_OUTREACH_TYPES:
                    await _annotate_outreach_plan(item, char_id, _mats_cache, _recent_cache, _char_has_unfinished)
                ok = await _execute(item, _gates())
            except Exception as e:
                _logger.error("execute %s failed char=%d: %s", item["type"], char_id, e)
                ok = False
                _exec_error = True
            _latency_ms = int((_time.monotonic() - _t0) * 1000)
            # 触发日志（可观测：候选 → 决策 approved/rejected，失败静默）
            try:
                await log_trigger_candidate(item, ok)
            except Exception:
                pass
            # Phase D：arbiter 主动任务 → AgentTask trace（feature flag + 10% 角色灰度；只写不读；失败静默）
            try:
                await _trace_scheduler_task(item, ok, _latency_ms, exec_error=_exec_error)
            except Exception:
                pass
            if ok:
                executed.append(f"{item['type']}(char={char_id})")
                executed_chars.add(char_id)
                break

    return executed


async def log_trigger_candidate(item: dict, executed: bool) -> None:
    """记录触发候选决策到 proactive_trigger_logs（可观测，失败静默）"""
    cand = item.get("candidate") or {}
    char_id = cand.get("character_id")
    if char_id is None and item.get("type") == "timer" and item.get("event") is not None:
        char_id = item["event"].character_id
    if char_id is None:
        return
    # 审计 P1-06：rejected 节流（同角色同类型 5 分钟只记一条，approved 必记；防表膨胀/写放大）
    if not executed:
        import time as _t
        _key = (char_id, item["type"])
        _now = _t.time()
        if _now - _rejected_log_cache.get(_key, 0.0) < REJECTED_LOG_THROTTLE_SECONDS:
            return
        _rejected_log_cache[_key] = _now
    reason = cand.get("trigger_reason") or ""
    if not reason and item.get("type") == "timer" and item.get("event") is not None:
        reason = f"定时承诺: {item['event'].event_type}"
    # P2（2026-08-24）：观测信号——记录该候选注入的最近聊天语境长度（[ctx=0] 表示未注入，便于量化验证承接效果）
    _ctx_len = len((cand.get("last_context") or ""))
    if _ctx_len:
        reason = f"{reason} [ctx={_ctx_len}]" if reason else f"[ctx={_ctx_len}]"
    # B1-③（方案 §5.4）：观测信号——记录本次接触意图（candidate 带 outreach_intent 时才附加；flag 关不附加）
    _outreach = cand.get("outreach_intent")
    if _outreach:
        reason = f"{reason} [outreach={_outreach}]" if reason else f"[outreach={_outreach}]"
    # A4 批3 M1b2：观测信号——影子改判（relational_drive_shadow 开时由 _shadow_drive_note 写在 item 上；
    # 关＝该键不存在 ⇒ 不附加，trigger_reason 与改动前逐字节一致）
    _drive_note = item.get("_drive_note")
    if _drive_note:
        reason = f"{reason} {_drive_note}" if reason else _drive_note
    # 2026-09-13（交接 §三）：投放口径三闸命中 → reject_reason 记 rejected / [gate=...]，
    # 与 trigger_reason 的 [gate=...] 标记同源（_mark_gate 写入），便于按天统计各闸拦截量。
    _gate = item.get("_gate")
    async with async_session_factory() as db:
        # 审计第三批 P2-05：candidate 缺 user_id 时按角色归属自动兜底（防 proactive_trigger_logs 写 NULL）
        uid = cand.get("user_id")
        if not uid:
            from app.agent.trace import resolve_owner_user_id
            uid = await resolve_owner_user_id(char_id, db=db)
        db.add(ProactiveTriggerLog(
            character_id=char_id,
            user_id=uid,
            trigger_type=item["type"],
            trigger_reason=str(reason)[:300] or None,
            priority=int(item.get("priority") or 0),
            decision="approved" if executed else "rejected",
            reject_reason=(
                None if executed
                else (f"rejected / [gate={_gate}]" if _gate else "限额/条件拦截")
            ),
        ))
        await db.commit()


async def _trace_scheduler_task(item: dict, ok: bool, latency_ms: int, *, exec_error: bool = False) -> None:
    """Phase D：arbiter 主动任务写 AgentTask trace（agent_task_logs，先只写不读）。

    - Feature Flag agent_loop_scheduler 关闭时不记录（默认关，一键回退）；
    - 灰度角色（10%）route=scheduler_gray，其余 scheduler，便于对比主动消息质量/成本；
    - R1（2026-09-09，工具轨迹治理 §4.1）：exec_error=``_execute`` 抛错（真进入执行却失败）。
      agent_trace_scheduler_only_executed 开=「本轮未触发」（ok=False 且未抛错）不再写
      agent_task_logs（评估流水看 proactive_trigger_logs，已有 5min 节流）；
      agent_trace_scheduler_mark_exec_error 开=真执行失败记 status=error（而非 blocked）；
    - 失败静默，绝不阻塞调度主链路。
    """
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_loop_scheduler", False):
            return
        # R1 止血：未触发（ok=False 且未抛执行错误）不再写 agent_task_logs（写放大 ~89% 的来源）
        if (not ok) and (not exec_error) and _loop.AGENT_FLAGS.get(
            "agent_trace_scheduler_only_executed", True
        ):
            return
        cand = item.get("candidate") or {}
        char_id = cand.get("character_id")
        ev = item.get("event")
        if char_id is None and item.get("type") == "timer" and ev is not None:
            char_id = getattr(ev, "character_id", None)
        if char_id is None:
            return
        user_id = cand.get("user_id") or (getattr(ev, "user_id", None) if ev is not None else None)
        session_id = cand.get("session_id") or (getattr(ev, "session_id", None) if ev is not None else None)
        gray = scheduler_gray_character(char_id)
        from app.agent import trace as _trace
        # 状态三态（R1）：成功 ok / 真执行失败 error / 其余（仅 flag 关的兼容路径）blocked
        if ok:
            status, err = "ok", None
        elif exec_error and _loop.AGENT_FLAGS.get("agent_trace_scheduler_mark_exec_error", True):
            status, err = "error", "主动任务执行失败（详见日志）"
        else:
            status, err = "blocked", "限额/条件拦截（本轮未触发）"
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=int(char_id),
            user_id=user_id,
            session_id=session_id,
            trigger="scheduler",
            route="scheduler_gray" if gray else "scheduler",
            steps_json=json.dumps(
                [{"action": item["type"], "priority": item.get("priority"), "ok": ok,
                  **({"reason": "exec_error"} if exec_error else {})}],
                ensure_ascii=False,
            ),
            llm_calls=1 if ok else 0,
            tool_calls=0,
            latency_ms=latency_ms,
            status=status,
            error=err,
        )
        # Phase H：灰度角色升级为真实任务记录（agent_tasks：goal/status/result；失败静默）
        if gray:
            from app.agent.task_engine import create_agent_task, update_task
            _tid = await create_agent_task(
                trigger="scheduler", goal=str(item["type"]), character_id=int(char_id),
                user_id=user_id, session_id=session_id,
            )
            await update_task(
                _tid,
                status="done" if ok else "failed",
                progress=[{"action": item["type"], "ok": ok}],
                result={"latency_ms": latency_ms},
                error=err,
            )
    except Exception as e:
        _logger.warning("Scheduler task trace failed: %s", e)


# A20 批 3b（2026-10-02）：timer 执行器与 agent 开关读取下沉 executors，此处具名重导出。
# ⚠ 理由同批 1/2/3a：tests/ 的 monkeypatch.setattr(arbiter, …) 靠「调用方在 arbiter 命名空间
#   解析裸名」；另外 tests/ 有按 arbiter._build_timer_hint / _build_timer_hint_legacy 直接调用
#   话术纯函数的用例——删任何一行都会让引用方拿不到名字（_agent_flag_on 实测零打桩，仍按名保留）。
from app.scheduling.executors.context import agent_flag_on as _agent_flag_on  # noqa: F401
from app.scheduling.executors.timer import (  # noqa: F401
    _build_timer_hint, _build_timer_hint_legacy, _timer_current_anchor,
)


# A20 批 4b（2026-10-02）：outreach 五类与 plugin 分支下沉 executors/，此处具名重导出 plugin 的
# Runtime 薄封装：tests/{test_phase_e,test_d2_df,test_proactive_strategy_pack} 按名
#   arbiter._plugin_proactive_runtime 直接调用；outreach 侧的闸函数也**按 arbiter.<name> 模块属性**
#   解析（桩在 arbiter 命名空间，解析点搬走会静默绕过打桩去查真库，理由同批 1/2/3a）。
from app.scheduling.executors.plugin import _plugin_proactive_runtime  # noqa: F401


def _gates() -> GateBundle:
    """A20 批 3a/3b/4b：**调用时刻现取** arbiter 命名空间里的闸函数与被桩依赖（见方案 §1 R2/R3）。

    裸名解析走 arbiter 全局 ⇒ ``monkeypatch.setattr(arbiter, "is_dnd_now", stub)`` 与
    ``setattr(arbiter, "async_session_factory", fake)``、``setattr(arbiter, "app_day_start_utc",
    sentinel)`` 都仍然生效；执行器（guards / timer / outreach）一律经本 bundle 取依赖，
    **不得自己 import 这些名字**。
    **不要在 import 期把函数对象存起来**（那样桩会被焊死在旧实现上，测试静默变绿）。
    """
    return GateBundle(
        is_dnd_now=is_dnd_now, has_user_said_sleep=has_user_said_sleep,
        is_user_active=is_user_active, hourly_active=get_hourly_active_count,
        pacing_gate=_pacing_gate, mark_gate=_mark_gate,
        session_factory=async_session_factory,
        app_day_start=app_day_start_utc,
    )


async def _execute(item: dict, g: GateBundle | None = None) -> bool:
    """执行单个行为。返回是否真正执行（False=被限额/条件拦截）"""
    _g = g or _gates()
    etype = item["type"]

    # 定时承诺：必须兑现，不受每日上限约束
    if etype == "timer":
        from app.scheduling.executors.timer import run_timer
        return await run_timer(item, _g)

    # ── 跨类型前置闸（A20 批 3a 下沉 executors/guards，闸函数经 _gates() 现取注入）──
    # 免打扰静默 / 夜晚睡眠静默 / 用户活跃 / 每小时限额 / outreach 三闸（timer 已在上面返回）
    blocked = await pre_gates(item, etype, _g)
    if blocked is not None:
        return blocked

    candidate = item["candidate"]
    char_id = candidate["character_id"]

    # ── A20 批 4a/4b：已下沉 executors/ 的 etype 走分派表（未注册 ⇒ None ⇒ 兜底 False）──
    from app.scheduling.executors.dispatch import dispatch as _dispatch_exec
    _handled = await _dispatch_exec(item, etype, candidate, char_id, _g)
    if _handled is not None:
        return _handled
    # ── A20 批 4b：outreach 五类 / plugin 也已下沉 ⇒ 未注册 etype 只剩下面这一条兜底 ──
    return False


# ── 启动时恢复 ──

async def recover_on_startup() -> None:
    """服务器启动时：恢复过期定时承诺（2小时内补发由下一 tick 处理）"""
    from app.scheduling.promise_service import recover_overdue_events
    await recover_overdue_events()