"""主动交流系统 API — 设置管理"""
from bisect import bisect_right

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.models.character import (
    ProactiveSettings, HolidayPreference,
)
from app.models.character import AICharacter
from app.scheduling.holiday_calendar import get_holidays
from app.scheduling import scheduler as scheduler_engine
from app.utils.logger import get_logger
from app.auth.deps import get_current_user_id
from app.application.tenant_service import tenant_scope_ids
from app.i18n import tr_lang
from app.utils.errors import friendly_llm_error
from app.utils.timeutil import now_naive_utc, to_naive_utc, app_local_now

router = APIRouter(prefix="/api/v1/scheduler", tags=["Scheduler"])
# #28 ③ 手动触发测试接口：独立 router（挂在 /api/v1/proactive，管理员专用）
proactive_router = APIRouter(prefix="/api/v1/proactive", tags=["Proactive"])
_logger = get_logger("api.scheduler")


# ── 角色主动交流设置 ──


async def _check_char_owned(db: AsyncSession, character_id: int, user_id: int, lang: str = "zh"):
    """校验角色归属本账号租户（账号独立 P1：跨家庭 → 404；家庭内共享）。"""
    char_result = await db.execute(
        select(AICharacter).where(
            AICharacter.id == character_id,
            AICharacter.user_id.in_(await tenant_scope_ids(db, user_id)),
        )
    )
    if char_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))


@router.get("/settings/{character_id}")
async def get_settings(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """获取角色的主动交流配置"""
    # 确认角色归属
    await _check_char_owned(db, character_id, user_id, lang)
    char_result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    if not char_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    # 获取或创建默认设置
    result = await db.execute(
        select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ProactiveSettings(character_id=character_id)
        db.add(settings)
        await db.flush()
        await db.commit()
        await db.refresh(settings)

    return {
        "character_id": settings.character_id,
        "enable_proactive": settings.enable_proactive,
        "idle_threshold_minutes": settings.idle_threshold_minutes,
        "frequency": settings.frequency,
        "max_daily_proactive": settings.max_daily_proactive,
        "birthday_enabled": settings.birthday_enabled,
        "holiday_enabled": settings.holiday_enabled,
        "diary_enabled": settings.diary_enabled,
        "moments_enabled": settings.moments_enabled,
        "state_trigger_enabled": settings.state_trigger_enabled,
        "memory_review_enabled": bool(settings.memory_review_enabled),
        "cold_war_enabled": settings.cold_war_enabled,
        "mood_badge_enabled": settings.mood_badge_enabled,
        "image_gen_enabled": bool(settings.image_gen_enabled),
        "active_image_gen_enabled": bool(getattr(settings, "active_image_gen_enabled", False)),
        "privacy_enabled": bool(getattr(settings, "privacy_enabled", True)),
        "privacy_lock_enabled": bool(getattr(settings, "privacy_lock_enabled", True)),
        "reasoning_level": int(getattr(settings, "reasoning_level", 0) or 0),
        "show_tools_enabled": bool(getattr(settings, "show_tools_enabled", False)),
        "moments_comment_enabled": bool(getattr(settings, "moments_comment_enabled", True)),
        "weave_full_inject_enabled": bool(getattr(settings, "weave_full_inject_enabled", False)),
        "dnd_enabled": bool(getattr(settings, "dnd_enabled", False)),
        "dnd_start": str(getattr(settings, "dnd_start", "00:00") or "00:00"),
        "dnd_end": str(getattr(settings, "dnd_end", "07:00") or "07:00"),
        "check_in_enabled": bool(getattr(settings, "check_in_enabled", False)),
        "control_enabled": bool(getattr(settings, "control_enabled", False)),
        "life_enabled": bool(getattr(settings, "life_enabled", True)),
        "life_intensity": str(getattr(settings, "life_intensity", "low") or "low"),
        "life_share_enabled": bool(getattr(settings, "life_share_enabled", True)),
    }


@router.put("/settings/{character_id}")
async def update_settings(
    character_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """更新角色的主动交流配置"""
    await _check_char_owned(db, character_id, user_id, lang)
    char_result = await db.execute(
        select(AICharacter).where(AICharacter.id == character_id)
    )
    if not char_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))

    result = await db.execute(
        select(ProactiveSettings).where(ProactiveSettings.character_id == character_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = ProactiveSettings(character_id=character_id)
        db.add(settings)
        await db.flush()

    # 更新允许的字段
    allowed_fields = {
        "enable_proactive": bool,
        "idle_threshold_minutes": int,
        "frequency": str,
        "max_daily_proactive": int,
        "birthday_enabled": bool,
        "holiday_enabled": bool,
        "diary_enabled": bool,
        "moments_enabled": bool,
        "state_trigger_enabled": bool,
        "memory_review_enabled": bool,
        "cold_war_enabled": bool,
        "mood_badge_enabled": bool,
        "image_gen_enabled": bool,
        "active_image_gen_enabled": bool,
        "privacy_enabled": bool,
        "privacy_lock_enabled": bool,
        "reasoning_level": int,
        "show_tools_enabled": bool,
        "moments_comment_enabled": bool,
        "weave_full_inject_enabled": bool,
        "dnd_enabled": bool,
        "dnd_start": str,
        "life_enabled": bool,
        "life_intensity": str,
        "life_share_enabled": bool,
        "dnd_end": str,
        "check_in_enabled": bool,
        "control_enabled": bool,
    }
    for field, field_type in allowed_fields.items():
        if field in data:
            value = data[field]
            if field == "frequency" and value not in ("low", "medium", "high"):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "frequency_invalid"))
            if field == "reasoning_level" and value not in (0, 1, 2):
                raise HTTPException(status_code=400, detail=tr_lang(lang, "reasoning_invalid"))
            if field in ("dnd_start", "dnd_end"):
                _t = str(value or "")
                try:
                    _h, _m = _t.split(":")
                    if not (0 <= int(_h) <= 23 and 0 <= int(_m) <= 59):
                        raise ValueError
                except Exception:
                    raise HTTPException(status_code=400, detail=tr_lang(lang, "hhmm_invalid", field=field))
            setattr(settings, field, field_type(value))

    await db.commit()
    return {"status": "ok", "message": "设置已更新"}


# ── 主动消息统计 ──


@router.get("/stats")
async def get_proactive_stats(
    character_id: int | None = None,
    days: int = 7,
    reply_scan_limit: int = 200,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """主动消息效果统计：触发/发送/拦截数量 + 用户回复率（按消息后该会话是否有用户回复估算）

    reply_scan_limit：回复率扫描条数上限，默认 200＝与旧实现逐字节一致；传 0＝不截断。
    需要「按 intent/tier 切分 + 60 分钟窗口 + 不截断」的判效口径走只读端点 /stats/outreach。
    """
    from datetime import timedelta
    from sqlalchemy import func as sa_func
    from app.models.chat import ChatMessage
    from app.models.character import ProactiveMessageLog, ProactiveTriggerLog

    days = max(1, min(days, 90))
    since = now_naive_utc() - timedelta(days=days)
    cond_log = [ProactiveMessageLog.created_at >= since]
    cond_trig = [ProactiveTriggerLog.created_at >= since]
    if character_id is not None:
        # 账号独立 P1：带 character_id 时必须先过租户归属（否则可读别家角色的主动统计）。
        # ProactiveMessageLog 无 user_id 列，故此处是唯一闸门。
        await _check_char_owned(db, character_id, user_id)
        cond_log.append(ProactiveMessageLog.character_id == character_id)
        cond_trig.append(ProactiveTriggerLog.character_id == character_id)
    else:
        scope_ids = await tenant_scope_ids(db, user_id)
        cond_trig.append(ProactiveTriggerLog.user_id.in_(scope_ids))
        char_ids_subq = select(AICharacter.id).where(AICharacter.user_id.in_(scope_ids))
        cond_log.append(ProactiveMessageLog.character_id.in_(char_ids_subq))

    sent_rows = (await db.execute(
        select(ProactiveMessageLog).where(*cond_log)
    )).scalars().all()
    total_sent = len(sent_rows)

    # 回复率：每条主动消息后，同一会话是否出现用户新消息
    # （A4 批3 M0：原硬编码「只扫前 200 条」改为可传参，默认 200＝行为不变；传 0＝不截断）
    replied = 0
    for m in (sent_rows[:reply_scan_limit] if reply_scan_limit > 0 else sent_rows):
        if m.session_id is None:
            continue
        n = (await db.execute(
            select(sa_func.count()).where(
                ChatMessage.session_id == m.session_id,
                ChatMessage.sender_type == "user",
                ChatMessage.created_at > m.created_at,
            )
        )).scalar() or 0
        if n > 0:
            replied += 1
    reply_rate = round(replied / total_sent, 2) if total_sent else 0.0

    trig_rows = (await db.execute(
        select(ProactiveTriggerLog).where(*cond_trig)
    )).scalars().all()
    total_triggered = len(trig_rows)
    total_cancelled = sum(1 for t in trig_rows if t.decision == "rejected")

    type_stats: dict[str, int] = {}
    for t in trig_rows:
        type_stats[t.trigger_type] = type_stats.get(t.trigger_type, 0) + 1

    return {
        "total_triggered": total_triggered,
        "total_sent": total_sent,
        "total_cancelled": total_cancelled,
        "reply_rate": reply_rate,
        "trigger_type_stats": type_stats,
    }


# ── 主动消息效果聚合（A4 批3 M0，2026-09-27：只读、按 intent × tier 切分、无条数截断） ──

REPLY_WINDOW_MINUTES = 60  # 判效主口径：主动消息发出后 60 分钟内有用户消息＝被接住


def _reply_flags(times, sent_at, window) -> tuple[bool, bool]:
    """60 分钟窗口判定（A4 批3 M0 主口径；M1b2 按驱力切分复用同一处，**不另写第二套**）。

    times＝该会话用户消息时刻的升序列表，sent_at＝该条主动消息发出时刻（均 naive UTC）。
    返回 (发送之后任意时刻有回复, 首个回复是否落在 (sent_at, sent_at+window] 内)。
    """
    i = bisect_right(times, sent_at)  # 首个严格晚于发送时刻的用户消息
    if i >= len(times):
        return False, False
    return True, times[i] <= sent_at + window


async def collect_outreach_effect_stats(db, *, since, log_cond, days: int) -> dict:
    """只读聚合：近 N 天主动消息按 intent × tier 的发送数 / 60 分钟回复率 / 每角色日均条数。

    口径（判效复算以此为准）：
    - 时间基准 UTC（库内 naive UTC），窗口 = [since, now]，days 由端点夹取；
    - 数据源 = proactive_message_logs 全量扫描（**不带「前 200 条」截断**），分组维度取
      extra_meta 的 intent × tier（A4 批3 M0 留痕补的 JSON 附加键）；老数据与其他发送通道
      没有这两个键 → 归入 ("", "") 一组，不猜测、不回填；
    - 回复判定：同一会话（session_id）内 sender_type='user' 的消息，时刻严格晚于该条主动消息；
      60 分钟窗口 = (发送时刻, 发送时刻 + 60min]，即 59 分钟算接住、61 分钟不算；
      replied_any_time / reply_rate_any 为旧口径（之后任意时刻有回复）参考行，与 GET /stats 一致；
    - 回复率分母 = scorable（有 session_id 且时间可用的条数）；缺 session_id 的只计 sent，
      不臆断为已回复；
    - C1 每角色日均条数 = 该角色窗口内 sent / days。

    A4 批3 M1b2 追加两段影子口径（同样只 SELECT，不因 flag 开关改变本端点行为）：
    - ``shadow_agreement``：把 extra_meta 的 shadow_intent（「若按驱力定开会选什么」）与本行
      intent（实际选定）逐条对比。agree＝两者都在且相等；disagree＝shadow_intent 在而不等于
      intent（含 intent 缺键的行：意图链路没选出意图而驱力会选出，属背离）；missing＝shadow_intent
      缺键或空串（M1b2 之前的老数据、以及无驱力可判的发送）；agreement_rate = agree/(agree+disagree)，
      分母为 0 时给 0.0（缺键不进分母，避免老数据把一致率冲淡）。
    - ``by_shadow_drive``：按 shadow_drive 分组的发送数与 60 分钟回复率，窗口判定与上面同一处
      （``_reply_flags``）；缺键老数据与「无驱力」都归入空串组。
    本函数只 SELECT：不写库、不建表、不改表结构。
    """
    import json as _json
    from datetime import timedelta

    from app.models.chat import ChatMessage
    from app.models.character import ProactiveMessageLog

    rows = (await db.execute(
        select(
            ProactiveMessageLog.created_at,
            ProactiveMessageLog.character_id,
            ProactiveMessageLog.session_id,
            ProactiveMessageLog.extra_meta,
        ).where(*log_cond)
    )).all()

    window = timedelta(minutes=REPLY_WINDOW_MINUTES)
    events = []
    sent_by_char: dict[int, int] = {}
    for created_at, char_id, session_id, meta_raw in rows:
        sent_at = to_naive_utc(created_at)
        try:
            meta = _json.loads(meta_raw) if meta_raw else {}
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        key = (str(meta.get("intent") or ""), str(meta.get("tier") or ""))
        # M1b2：影子留痕两键（缺键＝老数据，归空串）
        drive_key = str(meta.get("shadow_drive") or "")
        events.append((key, drive_key, str(meta.get("shadow_intent") or ""), session_id, sent_at))
        sent_by_char[char_id] = sent_by_char.get(char_id, 0) + 1

    # 一次性取回窗口内相关会话的用户消息时刻，内存二分判窗口（避免逐条 COUNT 的 N+1）
    user_times: dict[int, list] = {}
    session_ids = {sid for _k, _d, _si, sid, _t in events if sid is not None}
    if session_ids:
        for sid, t in (await db.execute(
            select(ChatMessage.session_id, ChatMessage.created_at).where(
                ChatMessage.session_id.in_(session_ids),
                ChatMessage.sender_type == "user",
                ChatMessage.created_at >= since,
            )
        )).all():
            _u = to_naive_utc(t)
            if _u is not None:
                user_times.setdefault(sid, []).append(_u)
        for _lst in user_times.values():
            _lst.sort()

    groups: dict[tuple[str, str], dict[str, int]] = {}
    drive_groups: dict[str, dict[str, int]] = {}
    agree = disagree = missing = 0
    for key, drive, shadow_intent, session_id, sent_at in events:
        st = groups.setdefault(key, {"sent": 0, "scorable": 0, "replied_60m": 0, "replied_any": 0})
        st["sent"] += 1
        # M1b2：影子一致率（与回复窗口无关，按条计）
        if not shadow_intent:
            missing += 1
        elif shadow_intent == key[0]:
            agree += 1
        else:
            disagree += 1
        replied_any = replied_60m = False
        if session_id is not None and sent_at is not None:
            st["scorable"] += 1
            replied_any, replied_60m = _reply_flags(user_times.get(session_id) or [], sent_at, window)
        if replied_any:
            st["replied_any"] += 1
        if replied_60m:
            st["replied_60m"] += 1
        # M1b2：按驱力切分复用同一次判定（同一处窗口口径，不写第二套）
        dt = drive_groups.setdefault(drive, {"sent": 0, "scorable": 0, "replied_60m": 0})
        dt["sent"] += 1
        if session_id is not None and sent_at is not None:
            dt["scorable"] += 1
        if replied_60m:
            dt["replied_60m"] += 1

    return {
        "days": days,
        "window_minutes": REPLY_WINDOW_MINUTES,
        "total_sent": len(rows),
        "groups": [
            {
                "intent": k[0],
                "tier": k[1],
                "sent": v["sent"],
                "scorable": v["scorable"],
                "replied_within_window": v["replied_60m"],
                "reply_rate_60min": round(v["replied_60m"] / v["scorable"], 4) if v["scorable"] else 0.0,
                "replied_any_time": v["replied_any"],
                "reply_rate_any": round(v["replied_any"] / v["scorable"], 4) if v["scorable"] else 0.0,
            }
            for k, v in sorted(groups.items(), key=lambda kv: (-kv[1]["sent"], kv[0]))
        ],
        "per_character_daily": [
            {"character_id": cid, "sent": n, "avg_per_day": round(n / days, 4) if days else 0.0}
            for cid, n in sorted(sent_by_char.items())
        ],
        # ── A4 批3 M1b2：影子改判观测（只读，缺键老数据归空串/missing）──
        "shadow_agreement": {
            "total": agree + disagree + missing,
            "agree": agree,
            "disagree": disagree,
            "missing": missing,
            "agreement_rate": round(agree / (agree + disagree), 4) if (agree + disagree) else 0.0,
        },
        "by_shadow_drive": [
            {
                "drive": k,
                "sent": v["sent"],
                "scorable": v["scorable"],
                "replied_within_window": v["replied_60m"],
                "reply_rate_60min": round(v["replied_60m"] / v["scorable"], 4) if v["scorable"] else 0.0,
            }
            for k, v in sorted(drive_groups.items(), key=lambda kv: (-kv[1]["sent"], kv[0]))
        ],
    }


@router.get("/stats/outreach")
async def get_outreach_stats(
    days: int = 30,
    character_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """A4 批3 M0 只读聚合端点：近 N 天（默认 30，可传参）主动消息效果基线。

    输出 ①按 intent × tier 的发送数 ②按 intent × tier 的 60 分钟窗口回复率
    ③每角色日均条数（C1）④M1b2 影子段：shadow_agreement（驱力会选的意图 vs 实际意图一致率）
    与 by_shadow_drive（按驱力切分的发送数/回复率）；完整口径见 collect_outreach_effect_stats
    的 docstring。
    租户归属与 GET /stats 同口径：带 character_id 先过归属校验（跨家庭 404），
    不带则收敛到本账号租户下的角色。只读，不写库。
    """
    from datetime import timedelta

    from app.models.character import ProactiveMessageLog

    days = max(1, min(days, 365))
    since = now_naive_utc() - timedelta(days=days)
    cond_log = [ProactiveMessageLog.created_at >= since]
    if character_id is not None:
        await _check_char_owned(db, character_id, user_id)
        cond_log.append(ProactiveMessageLog.character_id == character_id)
    else:
        scope_ids = await tenant_scope_ids(db, user_id)
        char_ids_subq = select(AICharacter.id).where(AICharacter.user_id.in_(scope_ids))
        cond_log.append(ProactiveMessageLog.character_id.in_(char_ids_subq))
    return await collect_outreach_effect_stats(db, since=since, log_cond=cond_log, days=days)


@router.get("/stats/semantics")
async def get_semantics_stats(
    user_id: int = Depends(get_current_user_id),
):
    """P0 语义统一 · 第 3 步：Observation / Event 语义影子计数（**只读内存计数，零查询零写库**）。

    与 GET /stats/outreach 的 ``shadow_agreement`` 同一形态：只把「判定结果」摆出来给人看，
    不参与任何生效判定。三段：
    - ``tool_injection``：工具结果进上下文那一跳的标注去向（agent/tools.py 进程内计数）。
      ``label_drop_rate`` = 标注被丢弃比例（flag observation_label_v1 关时恒 1.0，开时恒 0.0），
      分母 = ``injection_total``；``unavailable_rate`` = observation 压根没带认知态的比例。
    - ``domain_event``：领域事件写点前的 actor / origin 软校验（events/store.py 进程内计数）。
      落库值**未被这些判定改动过**，这里只量「归一后值 vs 落库值」的差异与非法占比。
      ``actor_diff_rate`` 分母 = ``actor_total``，``origin_invalid_rate`` 分母 = ``append_total``。
    - ``flags``：本轮相关开关的当前值，便于对照读数的解释口径。

    两个计数面都是**进程内累计、重启归零**（刻意不建表：第 3 步的硬约束是零写库），
    所以数字只代表本进程启动以来的窗口。
    """
    from app.agent.tools import observation_semantics_counters
    from app.events.store import domain_event_semantics_counters
    from app.flags.agent_flags import AGENT_FLAGS

    def _rate(num: int, den: int) -> float:
        return round(num / den, 4) if den else 0.0

    ti = observation_semantics_counters()
    de = domain_event_semantics_counters()
    return {
        "window": "process_uptime（内存计数，重启归零）",
        "flags": {
            "observation_label_v1": bool(AGENT_FLAGS.get("observation_label_v1", False)),
            "actor_semantics_shadow": bool(AGENT_FLAGS.get("actor_semantics_shadow", False)),
            "domain_event_log_enabled": bool(AGENT_FLAGS.get("domain_event_log_enabled", False)),
        },
        "tool_injection": {
            **ti,
            "label_drop_rate": _rate(ti["injection_label_dropped"], ti["injection_total"]),
            "unavailable_rate": _rate(ti["injection_label_unavailable"], ti["injection_total"]),
        },
        "domain_event": {
            **de,
            "actor_diff_rate": _rate(de["actor_diff"], de["actor_total"]),
            "actor_unnormalized_rate": _rate(de["actor_unnormalized"], de["actor_total"]),
            "actor_no_speaker_rate": _rate(de["actor_no_speaker"], de["append_total"]),
            "origin_invalid_rate": _rate(de["origin_invalid"], de["append_total"]),
        },
    }


# ── 手动触发测试（#28 ③，2026-08-24） ──


@proactive_router.post("/trigger/test")
async def trigger_test(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """#28 ③：手动触发一次主动消息候选生成（不实际发送），返回候选内容与自然度评分。仅主账号。

    - 参数：character_id（必填）+ trigger_type（默认 motivation；可选 greeting/proactive_chat/goodnight/status_update）。
    - 复用 arbiter 低优先级主动消息生成链路（generate_proactive_event），但不落库/不发送，便于调试。
    """
    from app.application.permission_service import is_admin_user
    if not await is_admin_user(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "trigger_test_forbidden"))
    character_id = data.get("character_id")
    if not character_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "trigger_test_character_required"))
    trigger_type = str(data.get("trigger_type") or "motivation")
    _ALLOWED_TRIGGERS = ("motivation", "greeting", "proactive_chat", "goodnight", "status_update")
    if trigger_type not in _ALLOWED_TRIGGERS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "trigger_test_invalid_type"))

    char = await db.get(AICharacter, character_id)
    if char is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
    # 账号独立 P1（2026-09-19 审计修正）：此处原先只校验 is_admin，未校验角色归属，
    # 造成「任一主账号可读别家角色私聊历史」的跨租户读；现将角色收敛到本账号租户。
    await _check_char_owned(db, character_id, user_id, lang)

    from app.scheduling.triggers import get_latest_session, get_last_messages
    from app.scheduling.message_generator import generate_proactive_event, score_naturalness

    session = await get_latest_session(char.id, char.user_id)
    context = (await get_last_messages(session["id"])) if session else ""
    try:
        segments, reasoning = await generate_proactive_event(
            character_name=char.name,
            character_bio=char.bio or "",
            character_personality=char.personality or "",
            character_id=char.id,
            user_id=char.user_id,
            current_status=char.current_status or "",
            relationship_summary=char.relationship_summary or "",
            user_name="好友",
            last_context=context,
            behavior=trigger_type,
            return_reasoning=True,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=friendly_llm_error(e))
    except Exception as e:
        _logger.warning("trigger_test generation failed char=%d: %s", char.id, e)
        raise HTTPException(status_code=502, detail=f"生成失败：{e}")
    return {
        "character_id": char.id,
        "user_id": char.user_id,
        "trigger_type": trigger_type,
        "segments": segments,
        "content": "\n".join(segments),
        "naturalness_score": score_naturalness(segments),
        "reasoning": reasoning,
        "sent": False,
    }


# ── 节日管理 ──


@router.get("/holidays/today")
async def get_today_holidays():
    """获取今天的所有节日"""
    holidays = get_holidays(app_local_now().date())
    return {
        "date": app_local_now().date().isoformat(),
        "holidays": holidays,
    }


@router.get("/holidays/blocked")
async def get_blocked_holidays(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """获取用户屏蔽的节日列表"""
    result = await db.execute(
        select(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.enabled == False,
        )
    )
    blocked = result.scalars().all()
    return {
        "blocked": [b.holiday_name for b in blocked],
        "total": len(blocked),
    }


@router.post("/holidays/block")
async def block_holiday(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """屏蔽某个节日"""
    holiday_name = data.get("holiday_name", "").strip()
    if not holiday_name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "holiday_name_required"))

    # upsert
    result = await db.execute(
        select(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.holiday_name == holiday_name,
        )
    )
    pref = result.scalar_one_or_none()
    if pref:
        pref.enabled = False
    else:
        pref = HolidayPreference(user_id=user_id, holiday_name=holiday_name, enabled=False)
        db.add(pref)
    await db.commit()
    return {"status": "ok", "holiday_name": holiday_name, "blocked": True}


@router.post("/holidays/unblock")
async def unblock_holiday(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """取消屏蔽某个节日"""
    holiday_name = data.get("holiday_name", "").strip()
    if not holiday_name:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "holiday_name_required"))

    await db.execute(
        delete(HolidayPreference).where(
            HolidayPreference.user_id == user_id,
            HolidayPreference.holiday_name == holiday_name,
        )
    )
    await db.commit()
    return {"status": "ok", "holiday_name": holiday_name, "blocked": False}


# ── 调度器状态 ──


@router.get("/status")
async def get_scheduler_status():
    """获取调度器运行状态"""
    return {
        "running": scheduler_engine.is_running(),
        "active_hours": f"{scheduler_engine.ACTIVE_HOUR_START}:00-{scheduler_engine.ACTIVE_HOUR_END}:00",
        "idle_check_interval_seconds": scheduler_engine.IDLE_CHECK_INTERVAL,
    }



# ── 事件时钟（定时承诺）管理：列表 + 删除（2026-08-15） ──


@router.get("/timers/{character_id}")
async def list_timers(
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """列出该角色当前未到期的定时承诺（供私聊右上角「事件时钟」展示）。"""
    await _check_char_owned(db, character_id, user_id, lang)
    from datetime import datetime, timezone, timedelta
    from app.models.life import ScheduledEvent as _SE
    result = await db.execute(
        select(_SE).where(
            _SE.character_id == character_id,
            _SE.user_id.in_(await tenant_scope_ids(db, user_id)),
            _SE.status == "pending",
        ).order_by(_SE.trigger_at.asc())
    )
    events = result.scalars().all()
    # 口径自洽：下面把库内 naive UTC 显式提升为 aware（ts.replace(tzinfo=timezone.utc)）后再比较，
    # 全程 aware 运算且不写库，故此处保持 aware now，不必归一为 now_naive_utc()。
    now = datetime.now(timezone.utc)
    cn_tz = timezone(timedelta(hours=8))
    out = []
    for e in events:
        ts = e.trigger_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts <= now:
            continue
        left_min = max(1, int((ts - now).total_seconds() / 60))
        cn = ts.astimezone(cn_tz)
        out.append({
            "id": e.id,
            "owner": e.owner or "ai",
            "event_type": e.event_type or "back",
            "content_hint": (e.content_hint or "").strip() or None,
            "left_minutes": left_min,
            "due_at": f"{cn.year}-{cn.month:02d}-{cn.day:02d} {cn.hour:02d}:{cn.minute:02d}",
        })
    return {"items": out}


@router.delete("/timers/{character_id}/{event_id}")
async def delete_timer(
    character_id: int,
    event_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除一条定时承诺（用户主动取消不必要的计时）。"""
    await _check_char_owned(db, character_id, user_id, lang)
    from app.models.life import ScheduledEvent as _SE
    event = await db.get(_SE, event_id)
    if event is None or event.character_id != character_id or event.user_id not in await tenant_scope_ids(db, user_id):
        raise HTTPException(status_code=404, detail=tr_lang(lang, "timer_not_found"))
    event.status = "cancelled"
    await db.commit()
    return {"status": "ok"}
