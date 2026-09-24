# -*- coding: utf-8 -*-
"""控制台删号·第二期第二批：**到期回收站账号的自动清除调度**（2026-09-24 派单）。

第一期管「怎么标记进回收站」（``account_deletion``），第二期第一批管「怎么干净地删掉」
（``account_purge.purge_account``）。本模块只做**触发**：扫到期的回收站账号，挑一个交给
``purge_account`` 去删——**绝不自己写任何 DELETE**，删除顺序 / fail-closed 备份 / 进度账本
全在清除器里，本模块一行都不重复实现。

三道闸（都在**开库之前**判完，不满足即逐字零行为）
----------------------------------------------------
1. **低峰窗口**：默认北京时间 02:00–06:00，可用环境变量 ``ACCOUNT_PURGE_WINDOW="HH:MM-HH:MM"``
   覆盖（支持跨零点；非法值回落默认）。窗口外**一次都不查库**。
2. **每轮最多 N 个账号**：``ACCOUNT_PURGE_MAX_PER_TICK``（默认 1，硬顶 ≤3 防误配）。
3. **最小间隔**：``ACCOUNT_PURGE_MIN_INTERVAL_MINUTES``（默认 60）——两次**实际成功清除**之间的
   最小间隔（被挡/失败不更新，故不占间隔）。

调用方式与「系统身份」
----------------------
唯一动作是 :func:`account_purge.purge_account`（``system_actor=True``）。该内部通道只跳过
「与 actor 身份相关」的两道护栏（删自己 / ``confirm_username``），**保留**「不能删最后一个
server_admin」等数据完整性护栏——命中保留护栏时 ``purge_account`` 抛 4xx，本模块捕获后
记 WARNING 并给该账号累计 ``attempts``；累计到 :data:`MAX_ATTEMPTS` 后只留 warning、不再自动重试。

进程内运行态（``_RUNTIME``）刻意不落库（白名单禁止新增表/列）：
- ``last_purge_at``：上次成功清除时刻（naive UTC）；
- ``attempts``：``{user_id: 连续被挡/失败次数}``，成功清除即清零。
重启后两者都保守从「无」起算——最坏是重启后被挡账号再自动试 ≤5 次，而每次都在任何 DELETE
之前抛 4xx（零数据风险），这是可接受的代价（派单：「重启后保守从 0 起算也行但要写清」）。

并发：**同一时刻只允许一个清除在跑**（``_PURGE_LOCK``）。第二个 tick 见锁被占即跳过（宁可晚一轮，
不并发压 SQLite）；万一两拍同时越过前置检查，后一拍在**拿到锁后再核一次间隔**，防止连着清两个。
挂钩点在 ``app.scheduling.scheduler.scheduler_loop``，每 10 分钟 ``spawn_background``
一拍；关 flag 时该拍立刻返回、不产生任何行为差异。
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text

from app.application import account_deletion, account_purge
from app.utils.logger import get_logger

_logger = get_logger("application.account_purge_scheduler")

#: 系统身份：调度器调 purge_account 时传的 actor_user_id。``admin_audit_service.record`` 把 falsy
#: 的 0 落库为 ``actor_user_id=NULL``（= 系统自动），且不等于任何真实账号 id → 天然不触发「删自己」。
SYSTEM_ACTOR_ID = 0

#: 候选取数上限（一次性多取一点：被挡/超限的要跳过，真正要清的排在后面也挑得到；有界防扫全表）。
CANDIDATE_FETCH_LIMIT = 100
#: 同一「被保留护栏挡住 / 清除失败」账号的最大自动重试次数：累计到达即只留 WARNING、不再自动调 purge。
MAX_ATTEMPTS = 5

# 环境变量口径（全部 os.getenv 现读现解析，便于灰度与热改；非法值一律回落默认，绝不因配置错误放宽护栏）
ENV_WINDOW = "ACCOUNT_PURGE_WINDOW"
ENV_MAX_PER_TICK = "ACCOUNT_PURGE_MAX_PER_TICK"
ENV_MIN_INTERVAL_MINUTES = "ACCOUNT_PURGE_MIN_INTERVAL_MINUTES"

DEFAULT_WINDOW_START = "02:00"        # 北京时间
DEFAULT_WINDOW_END = "06:00"
DEFAULT_MAX_PER_TICK = 1
MAX_PER_TICK_HARD_CAP = 3            # 防误配：无论环境变量给多大，每轮最多 3 个
DEFAULT_MIN_INTERVAL_MINUTES = 60

#: 进程内运行态（重启从「无」起算，见模块 docstring）。测试用 :func:`reset_state` 归零。
_RUNTIME: dict[str, Any] = {
    "last_purge_at": None,   # 上次**实际成功清除**的时刻（naive UTC）
    "attempts": {},          # {user_id: 连续被挡/失败次数}
}
_PURGE_LOCK = asyncio.Lock()  # 同一时刻只允许一个清除在跑


# ── 开关 / 时钟（两处测试注入缝）────────────────────────────────────────────────

def _flag_enabled() -> bool:
    """本批总闸（默认关=逐字零行为）。每次现读 AGENT_FLAGS，热改即生效（与其它调度闸同款）。"""
    from app.agent.loop import AGENT_FLAGS
    return bool(AGENT_FLAGS.get("account_purge_scheduler", False))


def _now() -> datetime:
    """当前 naive UTC（与库内 ``purge_after`` 同口径）。测试经 monkeypatch 本函数注入时间。"""
    return account_deletion.now_utc()


def reset_state() -> None:
    """清空进程内运行态（测试隔离用；正常运行无需调用）。"""
    _RUNTIME["last_purge_at"] = None
    _RUNTIME["attempts"].clear()


# ── 三道闸的解析（非法值回落默认）────────────────────────────────────────────────

def _hhmm_to_min(raw: Any) -> int | None:
    """``"HH:MM"`` → 当日分钟数；格式/范围非法 → None（由调用方回落默认）。"""
    try:
        hh, mm = str(raw).strip().split(":", 1)
        h, m = int(hh), int(mm)
    except (TypeError, ValueError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _window_minutes() -> tuple[int, int]:
    """低峰窗口（本地分钟区间 [start, end)）。``ACCOUNT_PURGE_WINDOW="HH:MM-HH:MM"``，非法回落默认。"""
    default = (_hhmm_to_min(DEFAULT_WINDOW_START), _hhmm_to_min(DEFAULT_WINDOW_END))
    raw = (os.getenv(ENV_WINDOW) or "").strip()
    if "-" not in raw:
        return default
    left, _, right = raw.partition("-")
    start, end = _hhmm_to_min(left), _hhmm_to_min(right)
    if start is None or end is None or start == end:
        return default
    return (start, end)


def _fmt_window() -> str:
    start, end = _window_minutes()
    return "%02d:%02d-%02d:%02d" % (start // 60, start % 60, end // 60, end % 60)


def _in_window(now: datetime) -> bool:
    """now（naive UTC）换算到应用本地时刻是否落在低峰窗口内；支持跨零点窗口。"""
    from app.utils.timeutil import app_tz_offset_hours
    local = now + timedelta(hours=app_tz_offset_hours())
    t = local.hour * 60 + local.minute
    start, end = _window_minutes()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def _max_per_tick() -> int:
    """每轮最多清除的账号数（默认 1，硬顶 ≤3 防误配，下限 1）。"""
    raw = (os.getenv(ENV_MAX_PER_TICK) or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_MAX_PER_TICK
    except ValueError:
        n = DEFAULT_MAX_PER_TICK
    return max(1, min(MAX_PER_TICK_HARD_CAP, n))


def _min_interval_minutes() -> int:
    """两次实际成功清除之间的最小间隔（分钟，默认 60；非法回落默认，负数按 0）。"""
    raw = (os.getenv(ENV_MIN_INTERVAL_MINUTES) or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_MIN_INTERVAL_MINUTES
    except ValueError:
        return DEFAULT_MIN_INTERVAL_MINUTES
    return max(0, n)


def _interval_ok(now: datetime) -> bool:
    last = _RUNTIME.get("last_purge_at")
    if last is None:
        return True
    return (now - last) >= timedelta(minutes=_min_interval_minutes())


# ── 候选查询（本模块唯一的读库点，也是 flag-off 测试的计数打桩点）──────────────────

#: 库内 DateTime 列的存储格式（SQLAlchemy sqlite 侧 storage_format），候选查询按此串比较，
#: 与 ORM 写入 ``purge_after`` 的字符串逐字节对齐（裸 ``:now`` 走 DBAPI 适配器会因微秒有无漂移）。
_DT_STORAGE_FMT = "%Y-%m-%d %H:%M:%S.%f"


async def _fetch_candidates(db, now: datetime, *, limit: int = CANDIDATE_FETCH_LIMIT) -> list[dict[str, Any]]:
    """到期待清的回收站账号：``deleted_at`` 非空、``purge_after <= now``、且该号**没有 done 的 job**。

    「done 永不再入队」用 ``NOT EXISTS(... status='done')`` 表达；被挡住（未 done）的号仍会重新
    入队，靠进程内 ``attempts`` 做退避（见 :func:`tick`）。按 ``purge_after`` 升序 = 最欠的先清。
    """
    rows = (await db.execute(text(
        "SELECT u.id AS id, u.username AS username "
        "FROM users u "
        "WHERE u.deleted_at IS NOT NULL AND u.purge_after IS NOT NULL AND u.purge_after <= :now "
        "AND NOT EXISTS (SELECT 1 FROM account_purge_jobs j "
        "                WHERE j.user_id = u.id AND j.status = :done) "
        "ORDER BY u.purge_after ASC LIMIT :limit"
    ), {"now": now.strftime(_DT_STORAGE_FMT), "done": account_purge.STATUS_DONE,
        "limit": int(limit)})).mappings().all()
    return [{"user_id": int(r["id"]), "username": r["username"]} for r in rows]


def _exc_reason(exc: Exception) -> str:
    """从异常抽一条可读原因（HTTPException.detail 优先），用于 WARNING 与返回摘要。"""
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("error") or detail)
    return str(exc) or type(exc).__name__


# ── 入口：调度循环每一拍调一次 ─────────────────────────────────────────────────────

async def tick() -> dict[str, Any]:
    """满足「开关 + 低峰窗口 + 最小间隔 + 无并发」才扫候选并清除，返回一份自描述摘要。

    **任何早退分支都在开库之前**——开关关时逐字零行为、一次都不查库（派单硬性要求）。
    ``purge_account`` 抛错（含命中 last_server_admin 等保留护栏）→ 捕获、写 WARNING、累计 attempts，
    并**本轮不再试别的账号**（派单：宁可晚一轮）。
    """
    summary: dict[str, Any] = {
        "ran": False, "skipped": None, "flag": _flag_enabled(), "in_window": False,
        "window": _fmt_window(), "candidates": [], "purged": [], "blocked": [],
        "skipped_over_attempts": [],
    }

    # ① 开关（默认关）：关 → 立即返回，不查库、不产生任何行为差异
    if not summary["flag"]:
        summary["skipped"] = "flag_off"
        return summary

    now = _now()
    # ② 低峰窗口：窗口外 → 立即返回，不查库
    if not _in_window(now):
        summary["skipped"] = "out_of_window"
        _logger.info("account purge scheduler: out of window %s (Beijing), skip", summary["window"])
        return summary
    summary["in_window"] = True

    # ③ 最小间隔：距上次成功清除不足 → 本轮跳过（不查库）
    if not _interval_ok(now):
        summary["skipped"] = "min_interval"
        return summary

    # ④ 并发闸：同一时刻只允许一个清除在跑，第二个 tick 直接跳过
    if _PURGE_LOCK.locked():
        summary["skipped"] = "in_flight"
        _logger.info("account purge scheduler: a purge is already in flight, skip this tick")
        return summary

    summary["ran"] = True
    async with _PURGE_LOCK:
        # 拿到锁后再核一次间隔：上面的 ``locked()`` 只是快速跳过，两拍同时越过检查时第二拍会在此
        # 排队——若不再核，上一拍刚清完、这一拍立刻又清一个，60 分钟节流就被击穿了（宁可晚一轮）。
        if not _interval_ok(_now()):
            summary["ran"] = False
            summary["skipped"] = "min_interval"
            return summary
        import app.db.database as _dbmod
        max_per_tick = _max_per_tick()
        async with _dbmod.async_session_factory() as db:
            candidates = await _fetch_candidates(db, now)
        summary["candidates"] = [c["user_id"] for c in candidates]

        purged = 0
        for cand in candidates:
            uid = int(cand["user_id"])
            username = cand.get("username")
            attempts = int(_RUNTIME["attempts"].get(uid, 0))
            if attempts >= MAX_ATTEMPTS:
                # 反复被挡（如唯一 server_admin）：只留 warning，不再自动重试，也不占本轮名额
                summary["skipped_over_attempts"].append({"user_id": uid, "attempts": attempts})
                continue
            async with _dbmod.async_session_factory() as db:
                try:
                    report = await account_purge.purge_account(
                        db, actor_user_id=SYSTEM_ACTOR_ID, target_user_id=uid,
                        system_actor=True)
                except Exception as exc:  # noqa: BLE001 —— 挡下/失败都绝不掀翻主循环
                    _RUNTIME["attempts"][uid] = attempts + 1
                    reason = _exc_reason(exc)
                    summary["blocked"].append({
                        "user_id": uid, "username": username,
                        "reason": reason, "attempts": _RUNTIME["attempts"][uid]})
                    _logger.warning(
                        "account purge scheduler blocked user=%s(%s) attempts=%s/%s: %s",
                        uid, username, _RUNTIME["attempts"][uid], MAX_ATTEMPTS, reason)
                    # 派单：purge 抛错 → 本轮不再试别的账号，交给下一个 tick
                    break
            # 成功：清计数、记录实际清除时刻（供最小间隔判定）
            _RUNTIME["attempts"].pop(uid, None)
            _RUNTIME["last_purge_at"] = _now()
            purged += 1
            summary["purged"].append({
                "user_id": uid, "username": username,
                "job_id": report.get("job_id"),
                "status": report.get("status"),
                "rows_deleted": report.get("rows_deleted"),
                "elapsed_seconds": report.get("elapsed_seconds"),
                "tables": report.get("tables"),
            })
            if purged >= max_per_tick:
                break
    return summary
