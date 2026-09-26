# -*- coding: utf-8 -*-
"""长周期记忆维护「按时间戳补齐」调度（2026-09-25 建立）。

背景（实测事故）：记忆衰减与 AI 自主评星原先挂在 scheduler 的「6 小时进程内计数」
（``decay_counter >= 21600``）上。计数器只活在进程内存里 ⇒ 只要进程没连续活满 6 小时，
这一拍就永远不来。代价：**记忆评星从 2026-09-20 起停摆 5 天**——09-20 之后新建的 760 条
记忆 0 条被评过星；同期候选记忆 4709 条、活跃角色 13 个、每日限额只用了 2-3 条、
LLM 通道 200 OK、评星函数手工调用完全正常 ⇒ 唯一原因就是「没被调度触发」。

本模块把判据从「进程内计数」换成「持久化时间戳」：启动时与每 6 小时各问一次
「距上次成功执行是否已超过间隔」，超了（或从未跑过）就补跑。状态落在
``backend/data/last_memory_maintenance``（与 paused.flag 同级；不入库、不需迁移）。

批 A（2026-09-26）补的两件事：同拍重入用模块级 ``_MAINT_LOCK`` 串行化（锁内再判一次到期，
消灭「读状态→执行」之间的双读窗口）；失败不再固定 15 分钟重试，按 ``RETRY_BACKOFF`` 阶梯
15m→30m→60m→6h 封顶回拨。状态文件升级为 JSON ``{"last_success", "fail_streak"}``，
读取端向后兼容旧的纯文本时间戳。
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

_logger = get_logger("memory.maintenance")

# 状态文件：backend/app/memory/ -> backend/ -> backend/data/
_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "last_memory_maintenance"
_STATE_FMT = "%Y-%m-%d %H:%M:%S"

# 长周期间隔（与原先 6 小时一拍一致）
INTERVAL = timedelta(hours=6)
# 失败后的首个重试间隔（退避阶梯第一档；不占用整个 6 小时，也避免每轮 tick 都打 LLM）
RETRY_AFTER = timedelta(minutes=15)
# 失败退避阶梯（2026-09-26 用户拍板 15m→30m→60m→6h 封顶，第 4 档起回落正常拍子）
RETRY_BACKOFF = (RETRY_AFTER, timedelta(minutes=30), timedelta(minutes=60), INTERVAL)

# 不可重入锁（必须在模块导入期创建：函数体内每次调用都会新建，锁就形同虚设）
_MAINT_LOCK = asyncio.Lock()
# 写盘失败时的内存兜底（见 _write_state），进程重启即失效
_LOCAL_LAST_SUCCESS: datetime | None = None


def _parse_ts(value) -> datetime | None:
    try:
        return datetime.strptime(str(value)[:19], _STATE_FMT)
    except (TypeError, ValueError):
        return None


def _read_state() -> tuple[datetime | None, int]:
    """读状态文件 → (last_success, fail_streak)。JSON 优先，向后兼容旧纯文本；读不出视为「从未跑过」。"""
    try:
        raw = _STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, 0
    except Exception as e:  # 权限/编码等异常不该影响主循环
        _logger.warning("Read memory maintenance state failed: %s", e)
        return None, 0
    if not raw:
        return None, 0
    try:
        data = json.loads(raw)
    except Exception:
        data = None
    if isinstance(data, dict):
        try:
            streak = max(0, int(data.get("fail_streak") or 0))
        except (TypeError, ValueError):
            streak = 0
        return _parse_ts(data.get("last_success")), streak
    last = _parse_ts(raw)  # 旧格式：文件里只有一行纯文本时间戳
    if last is None:
        _logger.warning("Unparsable memory maintenance state: %r", raw[:64])
    return last, 0


def last_run_at() -> datetime | None:
    """上次成功执行时间；文件缺失/损坏一律返回 None（视为「从未跑过」）。

    对外签名与语义不变。写盘失败时取内存兜底，返回「文件值与内存值中较晚的那个」。
    """
    file_last, _streak = _read_state()
    if _LOCAL_LAST_SUCCESS is None:
        return file_last
    if file_last is None:
        return _LOCAL_LAST_SUCCESS
    return max(file_last, _LOCAL_LAST_SUCCESS)


def fail_streak() -> int:
    """当前连续失败次数（读不到返回 0），决定下次失败落在退避阶梯的哪一档。"""
    return _read_state()[1]


def _write_state(when: datetime, fail_streak: int = 0) -> None:
    """写状态（先写临时文件再原子替换，避免半截内容被读到）。

    写盘失败不再静默（低-2）：记 ERROR 并把 when 落到内存兜底，避免时间戳不前进导致
    每 5 分钟重跑一次全表 decay；进程重启后兜底失效 ⇒ 会补跑一次，可接受。
    """
    global _LOCAL_LAST_SUCCESS
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps({"last_success": when.strftime(_STATE_FMT),
                                   "fail_streak": int(fail_streak)}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        _logger.error("Write memory maintenance state failed, fallback to in-memory stamp: %s", e)
        _LOCAL_LAST_SUCCESS = when


def is_due(now: datetime | None = None, interval: timedelta = INTERVAL) -> bool:
    """是否该跑：从未跑过 → True；距上次 ≥ interval → True。"""
    last = last_run_at()
    if last is None:
        return True
    return (now or now_naive_utc()) - last >= interval


async def run_if_due(*, reason: str = "tick", interval: timedelta = INTERVAL) -> bool:
    """到期就跑一次「记忆衰减 + AI 评星」，成功才刷新时间戳。返回是否真的跑了。

    不可重入：已在跑时这一拍直接跳过（返回 False）；锁外预检后，拿到锁**再判一次** is_due，
    消灭「读状态→执行」之间的双读窗口。
    失败不占满整个 6 小时：按 RETRY_BACKOFF 阶梯回拨时间戳（15m→30m→60m→6h 封顶）。
    """
    if _MAINT_LOCK.locked():
        _logger.info("maintenance already running, skip this tick (reason=%s)", reason)
        return False
    if not is_due(interval=interval):
        return False
    async with _MAINT_LOCK:
        if not is_due(interval=interval):
            return False
        started = now_naive_utc()
        last = last_run_at()
        _logger.info(
            "Periodic memory maintenance start (reason=%s, last=%s)",
            reason, last.strftime(_STATE_FMT) if last else "never",
        )
        ok = True
        detail = []
        try:
            from app.memory import run_memory_decay
            await run_memory_decay()
            detail.append("decay=ok")
        except Exception as e:
            ok = False
            detail.append("decay=fail(%s)" % e)
        try:
            from app.memory.ai_rating import run_ai_rating
            rated = await run_ai_rating()
            detail.append("rated=%s" % rated)
        except Exception as e:
            ok = False
            detail.append("rating=fail(%s)" % e)
        if ok:
            _write_state(started, fail_streak=0)
            _logger.info("Periodic memory maintenance done (reason=%s, streak=0, backoff=%s, %s)",
                         reason, interval, ", ".join(detail))
            return True
        streak = fail_streak() + 1
        backoff = RETRY_BACKOFF[min(streak - 1, len(RETRY_BACKOFF) - 1)]
        _write_state(started - interval + backoff, fail_streak=streak)
        _logger.info("Periodic memory maintenance done (reason=%s, streak=%d, backoff=%s, %s)",
                     reason, streak, backoff, ", ".join(detail))
        return True
