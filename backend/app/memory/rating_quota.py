# -*- coding: utf-8 -*-
"""AI 评星的「每日额度账本」（2026-09-25 建立）。

背景（实测事故）：`ai_rating._daily_rated()` 原用 `memories.updated_at` 近似「今天已评
了几条」；但记忆衰减（`run_memory_decay`，与评星**同拍**执行）会刷新大量 `ai_rated=1`
行的 `updated_at` —— 2026-09-25 19:45 实测：衰减跑完刷新 **1089** 行，紧接着评星读到
「今日已评满」⇒ **rated=0 且不留任何日志**（日志里只有 done 一行）。这正是「评星自
2026-09-20 起静默停摆」的直接原因：候选 4709 条、角色 13 个、LLM 通道与评星函数全部正常。

账本与 DB 解耦：只记「本机今天实际评了几条」，按北京时间日界跨日自动清零。状态文件
`backend/data/ai_rating_quota.json`（与 paused.flag 同级，不入库、不需迁移）。

并发口径（批 A，2026-09-26）：`add` 的读改写已由 `_QUOTA_LOCK` 串行化；`used_today` 同锁读取磁盘及未落盘计数，读查询本身不写盘。
永久拒写时仅维持同进程一致性，ERROR与调用方留痕明确未持久化；重启会丢未落盘部分。
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep

from app.utils.logger import get_logger

_logger = get_logger("memory.rating_quota")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ai_rating_quota.json"
_CN_TZ = timezone(timedelta(hours=8))
_QUOTA_LOCK = threading.Lock()
# Keyed by canonical ledger path and Beijing date; only unpersisted snapshots live here.
_PENDING: dict[tuple[str, str], dict[str, int]] = {}


def _today_key(now: datetime | None = None) -> str:
    """北京日界的日期串（与评分额度的原口径一致）。"""
    return (now or datetime.now(_CN_TZ)).astimezone(_CN_TZ).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        raw = _STATE_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except Exception as e:
        _logger.warning("Read rating quota failed: %s", e)
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        _logger.warning("Unparsable rating quota file; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict) -> bool:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        # Windows 临时占用可能拒绝原子替换；保留同一份完整 tmp，有限重试。
        for attempt in range(3):
            try:
                os.replace(tmp, _STATE_FILE)
                return True
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 2:
                    raise
                sleep(0.05 * (attempt + 1))
    except Exception as e:
        _logger.error("Write rating quota failed; not durable (process restart can lose pending counts): %s", e)
        return False


def _pending_key(today: str) -> tuple[str, str]:
    return (str(_STATE_FILE.resolve()), today)


def _count(counts: dict, key: str) -> int:
    try:
        return int(counts.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _current(today: str) -> dict:
    """Caller holds the lock. Pending consumption must never fall behind disk reads."""
    data = _load()
    if data.get("date") != today:
        data = {"date": today, "counts": {}}
    counts = data.get("counts")
    counts = dict(counts) if isinstance(counts, dict) else {}
    for key, value in _PENDING.get(_pending_key(today), {}).items():
        counts[key] = max(_count(counts, key), value)
    data["counts"] = counts
    return data


def _retire_old_days(today: str) -> None:
    path, _ = _pending_key(today)
    for key in list(_PENDING):
        if key[0] == path and key[1] != today:
            del _PENDING[key]


def used_today(character_id: int, now: datetime | None = None) -> int:
    """同进程今日已消费条数，包含拒写后尚未持久化部分；跨北京日归零。"""
    with _QUOTA_LOCK:
        return _count(_current(_today_key(now))["counts"], str(character_id))


def is_persisted(now: datetime | None = None) -> bool:
    """Whether this process has no pending charges for the current file/day."""
    with _QUOTA_LOCK:
        return _pending_key(_today_key(now)) not in _PENDING


def flush_pending(now: datetime | None = None) -> bool:
    """Retry once per rating batch, even when every character is already at quota."""
    today = _today_key(now)
    with _QUOTA_LOCK:
        _retire_old_days(today)
        key = _pending_key(today)
        if key not in _PENDING:
            return True
        if not _save(_current(today)):
            return False
        del _PENDING[key]
        return True


def add(character_id: int, n: int, now: datetime | None = None) -> int:
    """记n条并返回同进程消费累计；持久化状态由is_persisted单独报告。"""
    if n <= 0:
        return used_today(character_id, now)
    today = _today_key(now)
    with _QUOTA_LOCK:
        _retire_old_days(today)
        data = _current(today)
        counts = data["counts"]
        key = str(character_id)
        counts[key] = _count(counts, key) + n
        pending_key = _pending_key(today)
        if _save(data):
            _PENDING.pop(pending_key, None)
        else:
            _PENDING[pending_key] = {cid: _count(counts, cid) for cid in counts}
        return counts[key]


def diff_observed(observed: dict[int, int], now: datetime | None = None) -> dict[int, tuple[int, int]]:
    """对照「库内观测值」与账本当日值，返回**账本偏低**的差异 {character_id: (账本, 观测)}——只报告，不写账本。

    为什么只报告（2026-09-26 批次 C 复核结论，重要）：原计划是「账本 < 观测就抬到观测」，但**观测源本身是脏的**
    —— ``ai_rated=1 且 last_reinforce_at >= 今日`` 会被**真实强化路径**污染：检索命中
    （``service._apply_reinforce``，channel=retrieve）与主动复习（``memory_review``）也会把老评星行的
    ``last_reinforce_at`` 刷成 now、且不看 ``ai_rated``。于是「今天被强化过的老评星行」会被算成今天评的，
    一旦累计 ≥ 每日限额，自动补齐会把账本一次抬满 ⇒ 当天全部角色 ``quota_full``、评星再度静默停摆
    —— 与 09-25 那次「换了污染列」的故障同构。故本批**只把差异打成 INFO**，账本仍只由 ``add()`` 记账；
    真正的自动补齐需换用「评星留痕」口径（``agent_task_logs`` 里 ``route=ai_rating_char`` 的 ``written`` 求和）。
    跨日（账本 date != 今天）时账本值按 0 起算。任何异常只 WARNING 并返回 {}。
    """
    diff: dict[int, tuple[int, int]] = {}
    try:
        today = _today_key(now)
        with _QUOTA_LOCK:
            data = _current(today)
            counts = data.get("counts")
            if not isinstance(counts, dict):
                counts = {}
            for cid, obs in (observed or {}).items():
                try:
                    key = str(int(cid))
                    want = int(obs)
                    cur = int(counts.get(key, 0))
                except (TypeError, ValueError):
                    continue
                if want > cur:
                    diff[int(cid)] = (cur, want)
    except Exception as e:
        _logger.warning("Rating quota diff failed: %s", e)
        return {}
    return diff
