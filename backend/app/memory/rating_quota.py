# -*- coding: utf-8 -*-
"""AI 评星的「每日额度账本」（2026-09-25 建立）。

背景（实测事故）：`ai_rating._daily_rated()` 原用 `memories.updated_at` 近似「今天已评
了几条」；但记忆衰减（`run_memory_decay`，与评星**同拍**执行）会刷新大量 `ai_rated=1`
行的 `updated_at` —— 2026-09-25 19:45 实测：衰减跑完刷新 **1089** 行，紧接着评星读到
「今日已评满」⇒ **rated=0 且不留任何日志**（日志里只有 done 一行）。这正是「评星自
2026-09-20 起静默停摆」的直接原因：候选 4709 条、角色 13 个、LLM 通道与评星函数全部正常。

账本与 DB 解耦：只记「本机今天实际评了几条」，按北京时间日界跨日自动清零。状态文件
`backend/data/ai_rating_quota.json`（与 paused.flag 同级，不入库、不需迁移）。

并发口径（批 A，2026-09-26）：`add` 的读改写已由 `_QUOTA_LOCK` 串行化；`used_today` 为只读、不加锁。
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.utils.logger import get_logger

_logger = get_logger("memory.rating_quota")

_STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "ai_rating_quota.json"
_CN_TZ = timezone(timedelta(hours=8))
_QUOTA_LOCK = threading.Lock()


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


def _save(data: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_name(_STATE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _STATE_FILE)
    except Exception as e:
        _logger.warning("Write rating quota failed: %s", e)


def used_today(character_id: int, now: datetime | None = None) -> int:
    """该角色今天已评条数（跨日自动归零）。"""
    data = _load()
    if data.get("date") != _today_key(now):
        return 0
    counts = data.get("counts") or {}
    if not isinstance(counts, dict):
        return 0
    try:
        return int(counts.get(str(character_id), 0))
    except (TypeError, ValueError):
        return 0


def add(character_id: int, n: int, now: datetime | None = None) -> int:
    """记 n 条；返回该角色今日累计。跨日自动重置。"""
    if n <= 0:
        return used_today(character_id, now)
    today = _today_key(now)
    with _QUOTA_LOCK:
        data = _load()
        if data.get("date") != today:
            data = {"date": today, "counts": {}}
        counts = data.get("counts")
        if not isinstance(counts, dict):
            counts = {}
        key = str(character_id)
        try:
            prev = int(counts.get(key, 0))
        except (TypeError, ValueError):
            prev = 0
        counts[key] = prev + n
        data["counts"] = counts
        _save(data)
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
            data = _load()
            counts = data.get("counts") if data.get("date") == today else None
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
