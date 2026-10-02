"""用量统计、面板、成本估算与额度应用服务（A22 第一刀，2026-10-02）。

本模块自 ``app/application/system.py`` 逐字节搬入。边界＝LLM 用量统计 / 面板 /
成本估算 / 额度；不做配置读写、不碰备份。

实测发现 tests 仍在 ``system`` 模块替换 ``_read_usage_window``、``_cost_estimate``
与 ``_TOKEN_PRICE_RANGES``，故这三处保留旧模块属性回指，避免重导出后测试桩静默失效。
"""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.i18n import tr_lang
from app.application.permission_service import is_admin_user
from app.utils.logger import get_logger
from app.utils.timeutil import app_local_now

_logger = get_logger("application.system")


async def get_llm_usage(
    user_id: int,
):
    """token 用量统计：今日/近7天/本月/累计 + 按模型汇总 + 剩余额度（#68 P6 组聚合）

    主账号：统计范围 = 自己 + 直属子账号（user_id IN family_member_ids）+ user_id IS NULL 服务器级行；
    子账号：仅统计自己，不返回 by_user。
    """
    from sqlalchemy import or_
    from app.db.database import async_session_factory
    from app.models.agent import LlmUsage  # A8：额度读数改走 llm_quota.resolve_limit，本函数不再直接读额度表
    from app.models.user import User
    from app.application.family_service import is_sub_account, get_family_member_ids

    # B-TZ 修复（2026-09-01 审查）：库里 created_at 是 UTC naive（func.now()）。
    # 窗口按用户本地日历语义切，再统一转成 UTC naive 参与比较——否则本地
    # 00:00-08:00 的"今日/本月"会错窗（本地零点=前一天 16:00 UTC）。
    from datetime import timezone as _tz

    def _to_utc_naive(local_dt: datetime) -> datetime:
        """带 tzinfo 的本地时间 → UTC naive（供库内 UTC naive 列比较）。"""
        return local_dt.astimezone(_tz.utc).replace(tzinfo=None)

    # P3-12（2026-09-17）：原为裸 datetime.now()——取的是**服务器 OS 本地时区**，与本仓
    # 「用户可感知窗口统一走 app_local_now()（按 settings.APP_TZ_OFFSET_HOURS）」的规约不一致
    # （见 app/utils/timeutil.py 顶部：正因分散定义出过"北京日期当 UTC 零点"的 8 小时窗口偏差）。
    # 此处语义就是"用户本地日历的今日/近 7 日/本月"，故改用 app_local_now()；
    # 默认 +8 且服务器 OS 同区时与旧值逐字节等价，跨区部署时才真正纠正。
    # （备份 zip 文件名的日期键同属「用户可感知」口径 —— 批 2b 起生产端
    #  scripts/backup.py 与三处消费端（本模块 trigger_backup / download_backup、
    #  application/account_purge.py 的前置备份）统一走 backup_day_key()＝应用本地时区。）
    now_local = app_local_now()
    today0 = _to_utc_naive(datetime(now_local.year, now_local.month, now_local.day, tzinfo=now_local.tzinfo))
    week0 = today0 - timedelta(days=6)
    month0 = _to_utc_naive(datetime(now_local.year, now_local.month, 1, tzinfo=now_local.tzinfo))

    async with async_session_factory() as db:
        is_sub = await is_sub_account(db, user_id)
        if is_sub:
            scope_ids = [user_id]
            include_server = False
        else:
            scope_ids = await get_family_member_ids(db, user_id)
            include_server = True
        cond = LlmUsage.user_id.in_(scope_ids)
        if include_server:
            cond = or_(cond, LlmUsage.user_id.is_(None))
        rows = (await db.execute(select(LlmUsage).where(cond))).scalars().all()
        nickname_map: dict[int, str] = {}
        if not is_sub and scope_ids:
            users = (await db.execute(select(User).where(User.id.in_(scope_ids)))).scalars().all()
            nickname_map = {u.id: (u.nickname or u.username or str(u.id)) for u in users}

    total = today = week = month = 0
    by_model: dict[str, int] = {}
    by_user_map: dict[int, int] = {}
    for r in rows:
        t = r.total_tokens or 0
        total += t
        created = r.created_at
        if created:
            if created >= today0:
                today += t
            if created >= week0:
                week += t
            if created >= month0:
                month += t
        if r.model:
            by_model[r.model] = by_model.get(r.model, 0) + t
        if r.user_id is not None:
            by_user_map[r.user_id] = by_user_map.get(r.user_id, 0) + t

    by_user = []
    if not is_sub:
        by_user = [
            {"user_id": uid, "nickname": nickname_map.get(uid, str(uid)), "total": by_user_map.get(uid, 0)}
            for uid in scope_ids
            if by_user_map.get(uid, 0) > 0
        ]

    # A4 批 5 / T6 成本与缓存护栏 M0 项 3（2026-09-27）：按任务归因的用量桶 by_task。
    # 依据：llm_usage.task 列早已存在（写入侧归因，审计 P1-07），但读端只有 by_model/by_user，
    # 看不出「哪个用途吃掉多少 token」，成本/缓存护栏无法定位大头。这里是**纯读端聚合**
    # （rows 已在内存），不新增列、不写迁移。整块 fail-open：聚合异常按空处理只记 WARNING，
    # 不让用量接口 500（记账与观测不得让调用失败）。task 为空的行归到 "(untagged)"，
    # 与「未知用途」区分开，避免把无归因用量误算进某个真实任务。
    by_task: list[dict] = []
    try:
        _task_acc: dict[str, dict[str, int]] = {}
        for r in rows:
            _tk = (r.task or "")[:30] or "(untagged)"
            _b = _task_acc.setdefault(_tk, {"calls": 0, "total": 0, "prompt": 0, "completion": 0})
            _b["calls"] += 1
            _b["total"] += r.total_tokens or 0
            _b["prompt"] += r.prompt_tokens or 0
            _b["completion"] += r.completion_tokens or 0
        # 批8 块 D：桶形状**一字未动**（既有测试钉死精确相等，且只增不减是本接口口径）；
        # 面板要用的窗口聚合另走 usage_panel（下方返回键），不在这条全表载入上扩窗口（D-2）。
        by_task = [
            {"task": k, **v}
            for k, v in sorted(_task_acc.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
        ]
    except Exception as e:
        _logger.warning("llm usage by_task aggregate failed: %s", e)
        by_task = []

    # A4 批 5 / T6 M2 项 2（2026-09-27）：按渠道归因的用量桶 by_channel（app / wechat_ilink / server）。
    # 与 by_task 同一段纯内存聚合写法（rows 已在内存，零新查询）。channel 为 NULL 的行**不回填**
    # （勘察 §4：回填会把「渠道归因上线前的历史」与「将来某处漏传」永久混进同一格，失去排错能力），
    # 这里单独归 (unknown) 桶——沿用 by_task 的 (untagged) 口径：无归因不与真实取值混读。
    # 整块 fail-open：聚合异常按空处理只记 WARNING，不让用量接口 500。
    by_channel: list[dict] = []
    try:
        _chan_acc: dict[str, dict[str, int]] = {}
        for r in rows:
            _ch = (r.channel or "")[:30] or _USAGE_UNKNOWN
            _b = _chan_acc.setdefault(_ch, {"calls": 0, "total": 0, "prompt": 0, "completion": 0})
            _b["calls"] += 1
            _b["total"] += r.total_tokens or 0
            _b["prompt"] += r.prompt_tokens or 0
            _b["completion"] += r.completion_tokens or 0
        by_channel = [
            {"channel": k, **v}
            for k, v in sorted(_chan_acc.items(), key=lambda kv: (-kv[1]["total"], kv[0]))
        ]
    except Exception as e:
        _logger.warning("llm usage by_channel aggregate failed: %s", e)
        by_channel = []

    # A8（2026-09-20）：额度改为按账号生效（覆盖 > 全局 > 未设置），与服务器控制台同口径 ——
    # 统一走 app/application/llm_quota.resolve_limit（额度表唯一读写出口）；控制台给某账号设过
    # 覆盖时，App 这里显示的就是该账号的真实额度（并回传 limit_source 便于前端区分来源）。
    from app.application import llm_quota
    _quota = await llm_quota.resolve_limit(user_id)
    limit = int(_quota.get("total_limit") or 0)
    remaining = (limit - total) if (limit and limit > 0) else None
    # 批8 块 D M0：窗口用量面板（近 N 天，按用途/按渠道 + 服务端占比 + estimated/money 说明）。
    # 走 usage_panel 的「窗口一次 SELECT」这条读法，**不是**在上面 rows 全表载入上扩窗口（D-2）；
    # 失败只让本段退回空结构，既有字段照常返回（观测不得让读数端 500）。
    try:
        panel = await usage_panel(user_id, _PANEL_DEFAULT_DAYS)
    except Exception as e:
        _logger.warning("llm usage panel failed user_id=%s: %s", user_id, e)
        panel = _blank_usage_panel(_PANEL_DEFAULT_DAYS)
        panel["error"] = "usage_panel_unavailable"
    return {
        "total_limit": limit,
        "limit_source": _quota.get("source"),
        "used_total": total,
        "remaining": remaining,
        "today": today,
        "week": week,
        "month": month,
        "by_model": [{"model": k, "total": v}
                     for k, v in sorted(by_model.items(), key=lambda kv: -kv[1])],
        "by_user": by_user,
        # T6-M0 项 3：只增不减——by_task 是新增项，上面既有字段口径一字未动（前端/既有测试不受影响）
        "by_task": by_task,
        # T6-M2 项 2：同样只增不减（by_channel 与 by_task 同构，NULL 归 (unknown)）
        "by_channel": by_channel,
        # 批8 块 D M0：窗口面板（口径/失败处理见上方注释；只增键，既有字段一字未动）
        "usage_panel": panel,
        "can_edit_limit": await is_admin_user(user_id),
    }


# ── A4 批 5 / T6 M1 项 1：分用途 / 分自然日 / 分模型的用量报表（服务器控制台只读）────
# 依据：M0 项 3 的 by_task 只挂在 App 侧 get_llm_usage（家庭范围、只到 total 一项），控制台要的是
# 「固定窗口内、四件套 token 全量 + 估算行留痕」的完整报表，才能回答「成本大头在哪个用途、
# 流式估算占了多大比例」。纯读端聚合：不加列、不建表、不写迁移。
_USAGE_UNTAGGED = "(untagged)"   # 与 M0 项 3 同哨兵：task 为空单独成桶，不混进真实用途
_USAGE_UNKNOWN = "(unknown)"     # provider / model / 日期缺失的行归这里（与「无归因」同一思路）


def _usage_metrics_blank() -> dict:
    """四件套 + calls 的空桶（报表与面板共用同一形状，避免两处各写一份键名）。"""
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "reasoning_tokens": 0}


def _usage_window_bounds(days: int) -> dict:
    """days → 窗口四界（本地日界 + UTC naive），口径与 usage_report 建立时一字未变。

    库内 created_at 是 UTC naive，窗口按 ``app_local_now()`` 的应用本地日历切
    （days=N 含今天，向前推 N-1 个本地日界），自然日也按同一 offset 归桶。
    """
    from app.utils.timeutil import app_tz_offset_hours, now_naive_utc

    offset = app_tz_offset_hours()
    now_local = app_local_now()
    start_local = datetime(
        now_local.year, now_local.month, now_local.day, tzinfo=now_local.tzinfo
    ) - timedelta(days=days - 1)
    return {
        "days": days,
        "offset": offset,
        "now_local": now_local,
        "start_local": start_local,
        "start_utc": start_local.astimezone(timezone.utc).replace(tzinfo=None),
        "end_utc": now_naive_utc(),
    }


def _usage_window_descriptor(w: dict) -> dict:
    """窗口四界 → 对外可读形状（键名/精度与原 usage_report.window 完全一致）。"""
    return {
        "days": w["days"],
        "tz_offset_hours": w["offset"],
        "start_local": w["start_local"].isoformat(timespec="seconds"),
        "end_local": w["now_local"].isoformat(timespec="seconds"),
        "start_utc": w["start_utc"].isoformat(timespec="seconds"),
        "end_utc": w["end_utc"].isoformat(timespec="seconds"),
    }


async def _read_usage_window(db, start_utc: datetime, end_utc: datetime,
                             scope_cond=None) -> list:
    """窗口内用量行的一次 SELECT（只取聚合所需列）；``scope_cond=None`` ＝全局（控制台口径）。

    这是块 D 唯一的技术债防线：面板/报表一律走「窗口 + 指定列」这条读法，
    **不得**沿用 get_llm_usage 的 ``select(LlmUsage)`` 全表载入（设计 §1.4 D-2）。
    """
    from app.models.agent import LlmUsage

    stmt = select(
        LlmUsage.task, LlmUsage.channel, LlmUsage.provider, LlmUsage.model, LlmUsage.created_at,
        LlmUsage.prompt_tokens, LlmUsage.completion_tokens,
        LlmUsage.total_tokens, LlmUsage.reasoning_tokens,
    ).where(LlmUsage.created_at >= start_utc, LlmUsage.created_at <= end_utc)
    if scope_cond is not None:
        stmt = stmt.where(scope_cond)
    return (await db.execute(stmt)).all()


def _aggregate_usage_rows(rows: list, offset: int) -> tuple[dict, dict, dict, dict, dict]:
    """窗口行 → (task_acc, chan_acc, day_acc, model_acc, total)；纯函数、不碰库。

    分桶哨兵：task 空 → (untagged)、channel/provider/model/日期缺失 → (unknown)，
    「无归因」不与真实取值混读（channel 历史行不回填，见迁移 b4c5d6e7f8a9）。
    """
    from app.utils.timeutil import shift_utc_naive

    def _split(r) -> tuple:
        metrics = {
            "prompt_tokens": r.prompt_tokens or 0,
            "completion_tokens": r.completion_tokens or 0,
            "total_tokens": r.total_tokens or 0,
            "reasoning_tokens": r.reasoning_tokens or 0,
        }
        day = (shift_utc_naive(r.created_at, offset).date().isoformat()
               if r.created_at else _USAGE_UNKNOWN)
        key_model = ((r.provider or "")[:30] or _USAGE_UNKNOWN,
                     (r.model or "")[:50] or _USAGE_UNKNOWN)
        return metrics, day, key_model

    task_acc: dict[str, dict] = {}
    chan_acc: dict[str, dict] = {}
    day_acc: dict[str, dict] = {}
    model_acc: dict[tuple[str, str], dict] = {}
    total_b = _usage_metrics_blank()
    for r in rows:
        metrics, day, key_model = _split(r)
        for acc, key in ((task_acc, (r.task or "")[:30] or _USAGE_UNTAGGED),
                         (chan_acc, (getattr(r, "channel", None) or "")[:30] or _USAGE_UNKNOWN),
                         (day_acc, day), (model_acc, key_model)):
            b = acc.setdefault(key, _usage_metrics_blank())
            b["calls"] += 1
            for f, v in metrics.items():
                b[f] += v
        total_b["calls"] += 1
        for f, v in metrics.items():
            total_b[f] += v
    return task_acc, chan_acc, day_acc, model_acc, total_b


def _usage_emit(acc: dict, naming) -> list[dict]:
    """桶 → 列表：total_tokens 降序，同额按名称升序（输出稳定，便于回归比对）。"""
    return [
        {**naming(k), **b}
        for k, b in sorted(acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ]


async def usage_report(days: int = 7) -> dict:
    """窗口内 LLM 用量报表：total + by_task + by_channel + by_day + by_model + estimated_calls（只读）。

    口径与 ``get_llm_usage`` 一致：库内 created_at 是 UTC naive，窗口按 ``app_local_now()``
    的应用本地日历切（days=N 含今天，向前推 N-1 个本地日界），自然日也按本地日界归桶。
    SQL 侧不做方言相关的日期函数——窗口内一次 SELECT + 内存分桶（与既有读端同法）。
    estimated_calls 取 agent_task_logs.route="usage_estimated"（M0 项 2(b) obs_event 写入点），
    用于把「估算行」与「实测行」分开看（llm_usage 没有估算标记列，只能靠这条留痕对账）。

    全程只 SELECT；fail-open：读库/聚合异常返回空结构 + WARNING，不让控制台 500。
    days 的上下限校验在 API 层（app/api/admin.py）做，本函数按已校验值处理。
    """
    from sqlalchemy import func

    from app.application import system as _sys
    from app.db.database import async_session_factory
    from app.models.agent import AgentTaskLog

    w = _usage_window_bounds(days)

    result = {
        "window": _usage_window_descriptor(w),
        "total": _usage_metrics_blank(),
        "by_task": [],
        "by_channel": [],   # T6-M2：空结构也要带这个键（fail-open 返回体口径一致）
        "by_day": [],
        "by_model": [],
        "estimated_calls": 0,
    }

    try:
        async with async_session_factory() as db:
            rows = await _sys._read_usage_window(db, w["start_utc"], w["end_utc"])
            result["estimated_calls"] = int((await db.execute(
                select(func.count()).select_from(AgentTaskLog).where(
                    AgentTaskLog.route == "usage_estimated",
                    AgentTaskLog.created_at >= w["start_utc"],
                    AgentTaskLog.created_at <= w["end_utc"],
                )
            )).scalar_one() or 0)
    except Exception as e:
        _logger.warning("usage report read failed days=%s: %s", days, e)
        return result

    task_acc, chan_acc, day_acc, model_acc, total_b = _aggregate_usage_rows(rows, w["offset"])
    result["total"] = total_b
    result["by_task"] = _usage_emit(task_acc, lambda k: {"task": k})
    # T6-M2 项 2：chan_acc 已在上面分桶，这里必须吐出（与 by_task 同排序口径：用量降序）
    result["by_channel"] = _usage_emit(chan_acc, lambda k: {"channel": k})
    # by_day 不跟随「用量降序」：时间序列按日期升序才是可读的报表形态（其余三桶仍按用量降序）
    result["by_day"] = [{"date": k, **day_acc[k]} for k in sorted(day_acc)]
    result["by_model"] = _usage_emit(model_acc, lambda k: {"provider": k[0], "model": k[1]})
    return result


# ── A4 批 8 / 块 D「费用面板」M0（2026-09-30）：按账号的窗口用量读数（纯读、零新 schema 依赖）──
# 与 usage_report 的关系：**同一套聚合内核**（_read_usage_window + _aggregate_usage_rows +
# _usage_emit），差别只在这一处——面板按账号范围过滤（设计 §2.4「安全口径」）。
# 面板绝不沿 get_llm_usage 的全表载入扩窗口（D-2），也绝不重写 SQL 口径（D4）。
_PANEL_DEFAULT_DAYS = 7
_PANEL_MIN_DAYS = 1
_PANEL_MAX_DAYS = 90   # 与 admin.py:1152 控制台报表同一上限，两处窗口不各说各话


def _panel_days_or_raise(raw: object) -> int:
    """days 校验：脏输入/越界 → ValueError（API 层映射 400），**不静默夹取**。

    照控制台报表的口径（app/api/admin.py 对 days 1..90 越界返回 400 而不是夹到边界）：
    静默夹取会让「我要看 365 天」变成「看到 90 天」却毫无提示。
    """
    try:
        days = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("days 必须是 1-%d 的整数" % _PANEL_MAX_DAYS)
    if days < _PANEL_MIN_DAYS or days > _PANEL_MAX_DAYS:
        raise ValueError("days 必须在 %d-%d 之间" % (_PANEL_MIN_DAYS, _PANEL_MAX_DAYS))
    return days


def _panel_estimated_segment() -> dict:
    """估算可区分性说明：行级「实测 / 估算」**如实 unavailable**，不编数字。

    已知断点 D-1：流式上游不回 usage 时 llm_client 记的是估算行（``estimated=True``），
    但 ``llm_usage`` 没有估算标记列（models/agent/__init__.py LlmUsage），标记只落在
    ``agent_task_logs`` route="usage_estimated" 的观测明细里（obs_event 的第一参是
    character_id、不带 user_id）⇒ 既无法按账号过滤，也没有可 join 的行级外键。
    补列属 M1 之后的独立决策（设计 §8 待拍板 4），本轮面板只做**诚实声明**。
    """
    return {
        "status": "unavailable",
        "reason": "no_estimated_column",
        "per_row": "unavailable",
        "join_key": None,
        "note": ("估算行与实测行在 llm_usage 里同形（无估算列）；留痕在 agent_task_logs"
                 " route=usage_estimated 但不带账号与行级外键 ⇒ 本面板不按账号给估算数"),
    }


def _panel_money_segment() -> dict:
    """金额段：维持 unavailable（无价目表），且**绝不把单轮预算投影说成历史花费**。

    单一事实源是 _cost_estimate / _price_range_for / _TOKEN_PRICE_RANGES（刻意留空，见其注释）。
    本轮「费用估算」的 basis 是 ``full_effective_budget_input_only``＝一轮、输入侧、预算用满的
    **投影**，与本面板的「窗口历史用量」是两套语义 ⇒ 这里不复用那个字符串（设计 §2.4：
    混在一个字段名里就是第二套真相），金额字段一律 None。
    """
    from app.application import system as _sys

    return {
        "status": "unavailable",
        "reason": ("no_price_table" if not _sys._TOKEN_PRICE_RANGES else "priced_history_basis_undefined"),
        "currency": _PRICE_CURRENCY,
        "amount_low": None,
        "amount_high": None,
        "basis": None,
        "is_historical_spend": False,
        "note": "无价目表 ⇒ 金额段不出数；既有 cost_estimate 段是单轮预算投影，不是历史花费",
    }


def _blank_usage_panel(days: int) -> dict:
    """面板空结构（窗口照出、桶为空）：读库失败与「本就没数据」共用同一形状，App 端一套渲染。"""
    w = _usage_window_bounds(days)
    return {
        "window": _usage_window_descriptor(w),
        "scope": {"account_only": False, "includes_server_rows": True},
        "total": _usage_metrics_blank(),
        "by_task": [],
        "by_channel": [],
        "estimated": _panel_estimated_segment(),
        "money": _panel_money_segment(),
        "error": "",
    }


async def usage_panel(user_id: int, days: int = _PANEL_DEFAULT_DAYS) -> dict:
    """本账号窗口内用量面板读数：total + by_task + by_channel（含占比）+ estimated + money（纯读）。

    - **数据源**＝llm_usage 唯一事实源，聚合复用 usage_report 的「窗口一次 SELECT + 分桶」内核；
      禁止沿 get_llm_usage 的全表载入扩窗口（设计 §1.4 D-2，本块唯一技术债防线）。
    - **账号范围**＝与 get_llm_usage 一致：主账号＝自己 + 直属子账号 + user_id IS NULL 的
      服务器级行；子账号＝仅自己。家庭共享（group_owner_id）口径本轮**不出**（§8 待拍板 5）。
    - **占比 share**＝该桶 total_tokens ÷ 窗口 total_tokens（服务端算，前端零本地计算）；
      窗口总额为 0 时 share 给 0.0，不做除零兜底数字。
    - **estimated / money** 见两个段函数：不编数字、不冒充实测/历史花费。
    - fail-open：读库/聚合异常返回空结构 + WARNING，不让读数端 500（观测不得让调用失败）。
    """
    from sqlalchemy import or_

    from app.application.family_service import get_family_member_ids, is_sub_account
    from app.db.database import async_session_factory
    from app.models.agent import LlmUsage
    from app.application import system as _sys

    result = _blank_usage_panel(days)
    w = _usage_window_bounds(days)
    try:
        async with async_session_factory() as db:
            is_sub = await is_sub_account(db, user_id)
            if is_sub:
                scope_ids = [user_id]
                include_server = False
            else:
                scope_ids = await get_family_member_ids(db, user_id)
                include_server = True
            cond = LlmUsage.user_id.in_(scope_ids)
            if include_server:
                cond = or_(cond, LlmUsage.user_id.is_(None))
            rows = await _sys._read_usage_window(db, w["start_utc"], w["end_utc"], cond)
    except Exception as e:
        _logger.warning("usage panel read failed user_id=%s days=%s: %s", user_id, days, e)
        result["error"] = "usage_panel_read_failed"
        return result

    result["scope"] = {"account_only": bool(is_sub), "includes_server_rows": bool(include_server)}
    task_acc, chan_acc, _day_acc, _model_acc, total_b = _aggregate_usage_rows(rows, w["offset"])
    result["total"] = total_b
    denom = total_b["total_tokens"]

    def _with_share(buckets: list[dict]) -> list[dict]:
        for b in buckets:
            b["share"] = round(b["total_tokens"] / denom, 3) if denom > 0 else 0.0
        return buckets

    result["by_task"] = _with_share([
        {**{"task": k, "key": k}, **b} for k, b in
        sorted(task_acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ])
    result["by_channel"] = _with_share([
        {**{"channel": k, "key": k}, **b} for k, b in
        sorted(chan_acc.items(), key=lambda kv: (-kv[1]["total_tokens"], str(kv[0])))
    ])
    return result


async def update_llm_usage_limit(
    body: dict,
    user_id: int,
    lang: str,
):
    """设置**本账号**的免费额度覆盖（tokens，服务器管理员；0=额度为 0，非「清除」）。

    A8（2026-09-20）：额度从单行全局扩成「全局默认 + 账号覆盖」——
    - App 侧写的是**自己账号**的覆盖行（不再改服务器全局默认）：全局默认由服务器控制台管理
      （PUT /api/v1/admin/server/llm-limit），控制台还可逐账号设/清除覆盖；
    - 与控制台同口径、同一出口：写走 llm_quota.set_user_limit，读走 resolve_limit（覆盖 > 全局 > 未设置）。
    """
    from app.application import system as _sys

    await _sys._require_server_admin(user_id, lang)
    from app.application import llm_quota
    from app.db.database import async_session_factory
    try:
        limit = max(0, int(body.get("total_limit") or 0))
    except Exception:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "total_limit_invalid"))
    _before = await llm_quota.resolve_limit(user_id)
    try:
        await llm_quota.set_user_limit(user_id, limit, user_id)
    except Exception:
        raise HTTPException(status_code=500, detail=tr_lang(lang, "config_invalid"))
    async with async_session_factory() as db:
        await _sys._audit(db, user_id, "server.llm_usage_limit.update", "llm_usage_limit",
                     _before, {"scope": "user", "total_limit": limit})
        await db.commit()
    after = await llm_quota.resolve_limit(user_id)
    return {"total_limit": int(after.get("total_limit") or 0), "limit_source": after.get("source")}


# 与 context_builder._apply_system_total_quota 写埋点时的 route 同名（改埋点名此处同步）
_CLIP_ROUTE = "quota_clipped_sections"
_CLIP_WINDOW_HOURS = 24
# T5 M0 项2（2026-09-27）装配尾部留痕：每轮真装配写一条「system 总字符 + 本次生效预算」，
# 不依赖 provider usage ⇒ 它就是 S2 读数端「最近一轮实际占用」的样本源。
_USAGE_ROUTE = "system_total_chars"
# Y2（2026-09-29，S2 尾巴）：每段注入体量留痕（agent/context/__init__.py 的 obs_event），
# 每轮装配一条，detail.sections 只带 chars 最大的前 16 段（埋点侧截断，见该文件注释）。
_SECTION_ROUTE = "section_budget"

BREAKDOWN_DEFAULT_SAMPLES = 20
BREAKDOWN_MAX_SAMPLES = 50
# 每轮埋点里最多带 16 段（超出被截断），响应条数也按这个上限收口
_BREAKDOWN_TURN_SECTIONS = 16

# 单价区间表：键 = model 名 → provider 名 → "default"，值 = 每百万 input token 的（低, 高）价。
# **刻意留空**：项目内目前没有任何价目来源（llm_usage 只记 token 不记价、user_llm_configs 无价目列、
# 配置文件也没有）⇒ 硬编码一个数字＝编造价目，读数端宁可不报。接价目时只改这张表，
# 估算算式与 unavailable 状态机不动（见 _cost_estimate 的 reason 三态）。
_TOKEN_PRICE_RANGES: dict[str, tuple[float, float]] = {}
_PRICE_CURRENCY = "CNY"
_PER_MILLION_TOKENS = 1_000_000


def _unknown_usage(reason: str = "no_sample") -> dict:
    """无样本时的占用口径：只报「未知」+ 为什么未知，绝不拿预算值倒推一个占用数。"""
    return {"status": "unknown", "reason": reason, "system_chars": None, "est_tokens": None}


def _empty_breakdown(samples: int) -> dict:
    """无样本的体量段：samples=0 + items 空，App 侧据此显示「暂无样本」而不是 0。"""
    return {
        "status": "no_sample",
        "samples": 0,
        "samples_limit": samples,
        "sections_scope": "top%d_per_turn" % _BREAKDOWN_TURN_SECTIONS,
        "keys_total": 0,
        "items": [],
    }


def _clamp_breakdown_samples(raw: object) -> int:
    """请求样本数 → 合法值：脏输入/越界一律夹到 [1, MAX]（与档位夹紧同一口径，不报错）。"""
    try:
        n = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return BREAKDOWN_DEFAULT_SAMPLES
    return max(1, min(n, BREAKDOWN_MAX_SAMPLES))


def _aggregate_section_breakdown(details: list[dict], samples_limit: int) -> dict:
    """把 N 条 section_budget 埋点聚成「按 key 的均值/最大/空次数/样本数」。

    纯函数（不碰库），口径与埋点侧对齐：一条埋点 = 一轮装配；某 key 在该轮没出现就不计入
    它的 samples（「出现过几次」与「其中几次是空」分开报，混成一个分母会让人误读空率）。
    """
    acc: dict[str, dict] = {}
    for detail in details:
        for sec in (detail.get("sections") or []):
            if not isinstance(sec, dict):
                continue
            key = str(sec.get("key") or "")[:60]
            if not key:
                continue
            try:
                chars = int(sec.get("chars"))
            except (TypeError, ValueError):
                continue
            chars = max(0, chars)
            slot = acc.setdefault(
                key, {"samples": 0, "chars_sum": 0, "chars_max": 0, "empty_count": 0})
            slot["samples"] += 1
            slot["chars_sum"] += chars
            slot["chars_max"] = max(slot["chars_max"], chars)
            if chars <= 0 or sec.get("empty") is True:
                slot["empty_count"] += 1
    if not acc:
        return _empty_breakdown(samples_limit)
    items = [
        {
            "key": key,
            "samples": slot["samples"],
            "avg_chars": int(round(slot["chars_sum"] / slot["samples"])),
            "max_chars": slot["chars_max"],
            "empty_count": slot["empty_count"],
        }
        for key, slot in acc.items()
    ]
    items.sort(key=lambda x: (-x["avg_chars"], x["key"]))
    top = items[:_BREAKDOWN_TURN_SECTIONS]
    peak = top[0]["avg_chars"] if top else 0
    # 条形长度也交给服务端：前端自己按最大值算比例会和「均值/最大并存」这套数打架
    for item in top:
        item["share"] = round(item["avg_chars"] / peak, 3) if peak > 0 else 0.0
    return {
        "status": "ok",
        "samples": len(details),
        "samples_limit": samples_limit,
        "sections_scope": "top%d_per_turn" % _BREAKDOWN_TURN_SECTIONS,
        "keys_total": len(items),
        "items": top,
    }


def _price_range_for(model: str | None, provider: str | None):
    """查单价区间：model → provider → default，命中返回 (区间, 命中的键)；都没命中返回 None。"""
    from app.application import system as _sys

    if not _sys._TOKEN_PRICE_RANGES:
        return None
    for name in (model, provider, "default"):
        if not name:
            continue
        rng = _sys._TOKEN_PRICE_RANGES.get(str(name))
        if isinstance(rng, (tuple, list)) and len(rng) == 2:
            return (float(rng[0]), float(rng[1])), str(name)
    return None


def _cost_estimate(budget_tokens: int, model: str | None, provider: str | None) -> dict:
    """按当前生效预算估「一轮输入侧」的费用区间（区间＝价目低/高两界，不给单点承诺）。

    口径写进响应（basis/assumptions）：按本档预算**用满**、**只算输入**、不含输出与工具调用；
    缺价目时 status=unavailable + reason，任何金额字段都是 None——不编数字。
    """
    from app.application import system as _sys

    payload = {
        "status": "unavailable",
        "reason": "no_price_table",
        "currency": _PRICE_CURRENCY,
        "basis": "full_effective_budget_input_only",
        "budget_tokens": budget_tokens,
        "model": model,
        "price_source": None,
        "per_million_low": None,
        "per_million_high": None,
        "per_turn_low": None,
        "per_turn_high": None,
    }
    hit = _price_range_for(model, provider)
    if hit is None:
        payload["reason"] = (
            "no_price_table" if not _sys._TOKEN_PRICE_RANGES else "model_unpriced")
        return payload
    (low, high), source = hit
    low, high = min(low, high), max(low, high)
    factor = budget_tokens / _PER_MILLION_TOKENS
    payload.update(
        status="ok",
        reason="",
        price_source=source,
        per_million_low=round(low, 6),
        per_million_high=round(high, 6),
        per_turn_low=round(low * factor, 6),
        per_turn_high=round(high * factor, 6),
    )
    return payload
