# -*- coding: utf-8 -*-
"""周期任务「持久化到期台账」通用机制（2026-09-26 批次 PT，M1）。

背景：主调度循环里仍有约 15 个周期任务挂在「进程内 tick 计数」（``xxx_counter >= 秒数``）上。
计数只活在进程内存里 ⇒ 进程没连续活满一个周期就归零；重启/卡死重建频繁的时段，任务可以连续
多天不跑（长周期记忆评星就是这么停摆 5 天的）。本模块把判据换成持久化时间戳：每拍问一次
「距上次成功是否已超过间隔」，超了（或从未跑过）就补跑。

模式照搬 app/memory/maintenance_schedule.py（001 已落地的同款），差别只在泛化到多任务——
状态文件是 ``{"<key>": {"last_success", "fail_streak"}}`` 的 JSON，每个任务各占一条、互不影响。
落点 backend/data/periodic_state.json（不入库、不需迁移）。

口径与维护模块一致：**读失败 = 视为从未跑过**（会补跑一次）；**写失败** 记 ERROR 并把这一拍的
判据落到内存兜底，避免「未记账的成功」被反复重跑。所有函数都不向主循环抛异常。
"""
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path

from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

_logger = get_logger("scheduling.periodic_state")

# 状态文件：backend/app/scheduling/ -> backend/ -> backend/data/
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "periodic_state.json"
_STATE_FMT = "%Y-%m-%d %H:%M:%S"

# 失败退避阶梯：第 4 档起不再快速重试，直接等到任务自己的下一个 interval
RETRY_BACKOFF = (timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=60))

# 每个 key 一把不可重入锁。锁与 dict 都必须在模块层长期持有——函数体内每次调用新建，锁形同虚设
_LOCKS: dict[str, asyncio.Lock] = {}
# 写盘失败时的内存兜底（见 _write_entry），进程重启即失效
_LOCAL_STAMPS: dict[str, datetime] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.strptime(str(value)[:19], _STATE_FMT)
    except (TypeError, ValueError):
        return None


def _read_all() -> dict:
    """读整份台账；文件缺失/坏内容/非对象一律当「从未跑过」，不影响主循环。"""
    try:
        raw = _STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return {}
    except Exception as e:  # 权限/编码等
        _logger.warning("Read periodic state failed: %s", e)
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception as e:
        _logger.warning("Unparsable periodic state: %s", e)
        return {}
    if not isinstance(data, dict):
        _logger.warning("Unparsable periodic state: %r", raw[:64])
        return {}
    return data


def _entry(key: str) -> tuple[datetime | None, int]:
    item = _read_all().get(key)
    if not isinstance(item, dict):
        return None, 0
    try:
        streak = max(0, int(item.get("fail_streak") or 0))
    except (TypeError, ValueError):
        streak = 0
    return _parse_ts(item.get("last_success")), streak


def last_done(key: str) -> datetime | None:
    """上次成功执行时间；读不到/坏内容返回 None（视为「从未跑过」）。

    写盘失败时取「文件值 / 内存兜底值」中较晚的那个。
    """
    file_last, _streak = _entry(key)
    local = _LOCAL_STAMPS.get(key)
    if local is None:
        return file_last
    if file_last is None:
        return local
    return max(file_last, local)


def fail_streak(key: str) -> int:
    """当前连续失败次数（读不到返回 0），决定下次失败落在退避阶梯的哪一档。"""
    return _entry(key)[1]


def _write_entry(key: str, last_success: datetime, streak: int) -> None:
    """整表读改写 + 临时文件原子替换（避免半截内容被读到）。

    写失败不静默：记 ERROR 并把这一拍的判据落到内存兜底，否则时间戳不前进会导致每拍重跑。
    """
    data = _read_all()
    data[key] = {"last_success": last_success.strftime(_STATE_FMT), "fail_streak": int(streak)}
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        _logger.error("Write periodic state failed for %s, fallback to in-memory stamp: %s", key, e)
        _LOCAL_STAMPS[key] = last_success


def is_due(key: str, interval: timedelta, now: datetime | None = None) -> bool:
    """是否该跑：从未成功过 → True；``now - last_done >= interval`` → True。"""
    last = last_done(key)
    if last is None:
        return True
    return (now or now_naive_utc()) - last >= interval


def mark_done(key: str, when: datetime | None = None) -> None:
    """记一次成功：刷新 last_success 并把失败连击归零。"""
    _write_entry(key, when or now_naive_utc(), 0)


def mark_failed(key: str, interval: timedelta, when: datetime | None = None) -> None:
    """记一次失败：把 last_success 回拨成「下一个快速重试点」（阶梯 15m→30m→60m，第 4 档起回拨到
    ``when`` 本身，即放弃快速重试、等任务自己的下一个 interval）。"""
    when = when or now_naive_utc()
    streak = fail_streak(key) + 1
    backoff = RETRY_BACKOFF[streak - 1] if streak - 1 < len(RETRY_BACKOFF) else interval
    _write_entry(key, when - interval + backoff, streak)


async def run_if_due(key: str, interval: timedelta,
                     coro_factory: Callable[[], Awaitable[object]],
                     *, reason: str = "") -> bool:
    """到期就 ``await coro_factory()`` 一次，成功才刷新时间戳。返回「这一拍是否真的跑了」。

    不可重入：同一 key 上一拍没跑完则本拍直接 False；锁外预检 + 拿到锁再判一次到期，
    消灭「读状态→执行」之间的双读窗口。不同 key 各用各的锁，互不阻塞。
    任务抛异常只记 WARNING（并按退避阶梯记账）并返回 True，绝不向主循环冒泡。
    """
    lock = _lock_for(key)
    if lock.locked():
        _logger.info("periodic[%s] already running, skip this tick (reason=%s)", key, reason)
        return False
    if not is_due(key, interval):
        return False
    async with lock:
        if not is_due(key, interval):
            return False
        started = now_naive_utc()
        last = last_done(key)
        _logger.info("Periodic task start (key=%s, reason=%s, last=%s)",
                     key, reason, last.strftime(_STATE_FMT) if last else "never")
        try:
            await coro_factory()
        except Exception as e:
            mark_failed(key, interval, when=started)
            _logger.warning("Periodic task failed (key=%s, streak=%d, backoff applied): %s",
                            key, fail_streak(key), e)
            return True
        mark_done(key, when=started)
        _logger.info("Periodic task done (key=%s, reason=%s, interval=%s)", key, reason, interval)
        return True
