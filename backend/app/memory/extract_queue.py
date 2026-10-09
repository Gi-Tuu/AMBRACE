# -*- coding: utf-8 -*-
"""批量记忆提取的待提取队列（A41，2026-10-10）：从进程内 dict 迁到本机 JSON 落盘。

为什么必须落盘（审计 I8 / §4 批 4「跨重启不丢」）
-----------------------------------------------
原先 `extractor._pending` 与 `extractor._pending_ids` 是进程内的 dict + set：「攒够 `BATCH_SIZE`
条才提取」全靠它们记账。进程重启（改代码重启 / watchdog 拉起 / 崩溃）时队列里那 1~3 条对话
**直接消失**，这批对话的记忆提炼只能等 catchup 在 2 小时窗口内偶然撞上，窗口一过就永久漏做。
落盘后队列与占位集合是同一份磁盘数据，不存在第二副本。

文件形态
--------
`{"sessions": {"<会话 id>": [配对, ...]}, "inflight": {"<源消息 id>": 取出时刻}}`

- `sessions`：排队等凑批的配对（一条 = 一问一答 + ``source_id`` + ``ts``）。
- `inflight`：**已从队列取出、正在逐条提取**的 ``source_id`` 占位。这一步是照抄旧语义
  （旧代码在每条提取的 ``finally`` 里才 ``_pending_ids.discard``）：占位期间 catchup 跳过它，
  防止「主链路正在提取」与「catchup 补采同一条」同时发生。同时**取出**动作本身仍是原子的
  （同步读改写，中间没有 ``await``），所以并发的 ``add`` 不会把同一批配对再刷一遍。

为什么不是「换个 dict」，也不是建表
----------------------------------
- 建表要 alembic 迁移（本批禁止动 `alembic/`）；
- 队列装的是**对话原文**这种大载荷，塞进节流台账 `periodic_state.json` 会把「节流判据」和
  「待办工作项」两种职责混在一个文件里。所以沿用同一条落盘纪律（临时文件 + `os.replace`
  原子替换），但独立成文件。
- **模块内不缓存队列内容**：每次增删都读盘→改→写盘。「跨重启不丢」因此是结构性的，不靠测试技巧。

失败方向
--------
- 坏文件／非对象 ⇒ 判空（当作没排过队）：漏掉的提取由 catchup（2h 窗 + `ProcessedExtraction`
  幂等表）自愈。宁可重做一次，也不把坏 JSON 当成「已提取」永久跳过。
- 超过 `HARD_AGE_SEC` 的滞留配对与在途占位在读取时丢弃：它们早已出了 catchup 窗口，继续占着
  只会**挡住** catchup（占位即跳过），丢掉反而解锁。
- 日志纪律：只打印条数，不打印对话原文。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# backend/app/memory/<本文件> 与同目录的 extractor.py 同深度 ⇒ parents[2] = backend/
_QUEUE_FILE = Path(__file__).resolve().parents[2] / "data" / "extract_queue.json"

HARD_AGE_SEC = 24 * 3600       # 滞留/在途硬上限：超龄直接丢（防文件无界增长，并解锁 catchup）
MAX_PAIRS_PER_SESSION = 200    # 单会话配对上限（旧写法在内存里无所谓，落盘必须有界）；超出丢最旧
MAX_INFLIGHT = 500             # 在途占位上限，超出丢最旧


def _read_doc() -> dict[str, Any]:
    """读队列文件；不存在／坏 JSON／非对象 ⇒ 空结构（判空方向见模块 docstring）。"""
    try:
        raw = _QUEUE_FILE.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        doc = json.loads(raw or "{}")
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def _write_doc(sessions: dict[str, list[dict]], inflight: dict[str, float]) -> bool:
    """原子写（临时文件 + ``os.replace``）；失败返回 False，不抛（提取主链路不能因记账断掉）。"""
    payload = {"sessions": sessions, "inflight": inflight}
    try:
        _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _QUEUE_FILE.with_name(_QUEUE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, _QUEUE_FILE)
        return True
    except Exception:
        return False


def _load() -> tuple[dict[str, list[dict]], dict[str, float]]:
    """读盘并裁剪：超龄配对／超龄在途占位丢掉，单会话超上限的保最近若干条。"""
    doc = _read_doc()
    now = time.time()
    sessions: dict[str, list[dict]] = {}
    raw_sessions = doc.get("sessions")
    if isinstance(raw_sessions, dict):
        for sid, rows in raw_sessions.items():
            if not isinstance(rows, list):
                continue
            keep = [p for p in rows
                    if isinstance(p, dict) and now - float(p.get("ts") or now) < HARD_AGE_SEC]
            if len(keep) > MAX_PAIRS_PER_SESSION:
                keep = keep[-MAX_PAIRS_PER_SESSION:]
            if keep:
                sessions[str(sid)] = keep
    inflight: dict[str, float] = {}
    raw_inflight = doc.get("inflight")
    if isinstance(raw_inflight, dict):
        for uid, at in raw_inflight.items():
            try:
                stamp = float(at)
            except (TypeError, ValueError):
                continue
            if now - stamp < HARD_AGE_SEC:
                inflight[str(uid)] = stamp
        if len(inflight) > MAX_INFLIGHT:
            newest = sorted(inflight.items(), key=lambda kv: -kv[1])[:MAX_INFLIGHT]
            inflight = dict(newest)
    return sessions, inflight


def peek(session_id: int) -> list[dict]:
    """该会话当前排队等凑批的配对（拷贝，调用方改不动内部）。"""
    sessions, _ = _load()
    return list(sessions.get(str(session_id), ()))


def add(session_id: int, pair: dict) -> list[dict]:
    """入队一条配对，返回入队后的该会话队列（省掉调用方再一次读盘）。

    写盘失败 ⇒ 这一条没记上：它同时也不占位 ⇒ catchup 会在 2 小时窗内把它补采。
    """
    key = str(session_id)
    sessions, inflight = _load()
    rows = sessions.setdefault(key, [])
    rows.append(dict(pair))
    _write_doc(sessions, inflight)
    return rows


def take(session_id: int) -> list[dict]:
    """整串取出并登记在途占位（与旧 ``_pending.pop`` + ``_pending_ids`` 保留占位同语义）。"""
    key = str(session_id)
    sessions, inflight = _load()
    rows = sessions.pop(key, [])
    if rows:
        now = time.time()
        for p in rows:
            uid = p.get("source_id")
            if uid is not None:
                inflight[str(uid)] = now
        _write_doc(sessions, inflight)
    return rows


def release_source(uid: Any) -> None:
    """一条配对提取完毕（成功或异常）⇒ 放开它的在途占位（旧 ``_pending_ids.discard``）。"""
    if uid is None:
        return
    sessions, inflight = _load()
    if inflight.pop(str(uid), None) is None:
        return
    _write_doc(sessions, inflight)


def remove_source(session_id: int, uid: Any) -> None:
    """从排队配对里移除指定 ``source_id``（catchup／截断保底已即时提取 ⇒ 主链路不再重复提取）。"""
    key = str(session_id)
    sessions, inflight = _load()
    rows = sessions.get(key)
    if not rows:
        return
    kept = [p for p in rows if p.get("source_id") != uid]
    if len(kept) == len(rows):
        return
    if kept:
        sessions[key] = kept
    else:
        sessions.pop(key, None)
    _write_doc(sessions, inflight)


def pending_source_ids() -> set[int]:
    """占位中的源消息 id（排队 + 在途，catchup 据此跳过防重复）。"""
    sessions, inflight = _load()
    out: set[int] = {int(uid) for uid in inflight if str(uid).lstrip("-").isdigit()}
    for rows in sessions.values():
        for p in rows:
            uid = p.get("source_id")
            if uid is not None:
                out.add(uid)
    return out
