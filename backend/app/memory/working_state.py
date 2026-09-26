# -*- coding: utf-8 -*-
"""工作记忆三桶结构化状态（M3-a 纯函数层，2026-09-01）。

docs/archive/architecture/设计_M3工作记忆_20260901.md：每 (user_id, character_id) 一条活跃 Memory 行
（memory_type="working_state"，content=结构化 JSON），三桶结构：

    {"version": 1,
     "ongoing":             [{"topic": ..., "detail": ..., "evidence_ids": [Memory.id], "updated_at": ...}],
     "relationship_notes":  [{"note": ..., "evidence_ids": [...], "updated_at": ...}],
     "open_questions":      [{"question": ..., "evidence_ids": [...], "updated_at": ...}]}

本模块只做纯数据变换（无 IO）：LLM 输出"完整期望三桶"，服务端 diff 出
add/update/resolve 语义（设计 §3.2 的实现细化），证据门控 + 桶上限在这里强制。
"""
from __future__ import annotations

import copy
from datetime import datetime

from app.utils.timeutil import (
    app_tz_offset_hours,
    now_naive_utc,
    shift_utc_naive,
    to_naive_utc,
)

BUCKET_LIMITS = {"ongoing": 3, "relationship_notes": 3, "open_questions": 3}
IDENTITY_KEY = {"ongoing": "topic", "relationship_notes": "note", "open_questions": "question"}
BUCKETS = tuple(BUCKET_LIMITS)

# 批 F-F2（2026-09-26）：短跨度（日级以内）未来口吻词表。下周/下个月这类长跨度**一律不判过期**，
# 避免误杀真实未来计划。历史事故：char13 的 ongoing 项 detail 写「sam提醒明天要面试新生…明早…」，
# 落笔那天之后已翻日 ⇒ 「明天」早就过了，却被现状锚每轮注入、反复复读。
_SHORT_FUTURE_WORDS = ("明天", "明早", "今天晚些", "次日", "后天")


def _has_short_future_word(*texts) -> bool:
    return any(w in (t or "") for t in texts for w in _SHORT_FUTURE_WORDS)


def _local_date(dt: datetime):
    """UTC（naive 或 aware）→ 应用本地时区日历日（口径统一走 timeutil，不手搓偏移）。"""
    return shift_utc_naive(to_naive_utc(dt), app_tz_offset_hours()).date()


def _parse_stamp(value) -> datetime | None:
    """条目的 updated_at（ISO 串 / datetime）→ datetime；解析不出来返回 None（保守：不判过期）。"""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def expire_stale_future_clauses(items, *, now=None) -> tuple[list, list]:
    """剔除「已翻日的短跨度未来口吻」ongoing 条目 → (保留项, 过期项)。

    判据：条目 topic/detail 含明天/明早/今天晚些/次日/后天，**且**其 updated_at 的应用本地
    日历日早于 now 的本地日历日（写下它的那天已经过了 ⇒ 口中的「明天/后天」必然已到期）。
    保守边界：长跨度表述（下周/下个月）保留；updated_at 缺失或解析不出来一律保留。
    now 缺省取 now_naive_utc()，仅供测试注入（UTC naive，与库内口径一致）。
    """
    if now is None:
        now = now_naive_utc()
    now_day = _local_date(now)
    kept: list = []
    expired: list = []
    for item in items or []:
        if not isinstance(item, dict) or not _has_short_future_word(
                item.get("topic"), item.get("detail")):
            kept.append(item)
            continue
        stamp = _parse_stamp(item.get("updated_at"))
        if stamp is None or _local_date(stamp) >= now_day:
            kept.append(item)
        else:
            expired.append(item)
    return kept, expired


def empty_state() -> dict:
    return {"version": 1, **{b: [] for b in BUCKETS}}


def _identity(entry: dict, bucket: str) -> str:
    return str(entry.get(IDENTITY_KEY[bucket]) or "").strip()


def _norm_entry(raw: dict, bucket: str) -> dict | None:
    """LLM 输出条目 → 规范化条目；身份键缺失/证据空返回 None。"""
    if not isinstance(raw, dict):
        return None
    ident = _identity(raw, bucket)
    if not ident:
        return None
    ev = raw.get("evidence_ids") or []
    if not isinstance(ev, list):
        return None
    ids = []
    for e in ev:
        try:
            i = int(e)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in ids:
            ids.append(i)
    if not ids:
        return None
    entry = {IDENTITY_KEY[bucket]: ident[:120]}
    detail = raw.get("detail")
    if isinstance(detail, str) and detail.strip():
        entry["detail"] = detail.strip()[:200]
    entry["evidence_ids"] = ids
    return entry


def validate_desired(raw) -> dict | None:
    """LLM 原始输出 → 规范化期望三桶；形状完全非法返回 None（fail-open 跳过本轮）。"""
    if not isinstance(raw, dict):
        return None
    desired: dict = {"version": 1}
    for bucket in BUCKETS:
        items = raw.get(bucket)
        entries: list[dict] = []
        if isinstance(items, list):
            for item in items[: BUCKET_LIMITS[bucket] * 2]:  # 上限裁剪在 apply 层做，这里防极端输入
                e = _norm_entry(item, bucket) if isinstance(item, dict) else None
                if e is not None:
                    entries.append(e)
        desired[bucket] = entries
    return desired


def apply_desired(
    current: dict | None,
    desired: dict,
    valid_evidence: set[int],
    now_iso: str,
) -> tuple[dict | None, dict]:
    """把期望三桶 diff 到当前状态，返回 (新 content, 统计)。

    - 期望条目按身份键匹配当前条目：命中=update（合并证据，保留旧证据）、未命中=add；
    - 当前条目未出现在期望中=resolve（移除）；
    - 新增/更新条目的 evidence_ids 只保留真实存在的 Memory.id（证据门控）；零证据条目丢弃；
    - 桶超上限裁掉最旧（updated_at 最小）；
    - 结果与 current 完全一致 → 返回 (None, stats)（调用方不写新行）。
    """
    cur = copy.deepcopy(current) if isinstance(current, dict) else empty_state()
    stats = {"added": 0, "updated": 0, "carried": 0, "resolved": 0, "dropped_no_evidence": 0}
    new_state: dict = {"version": 1}

    for bucket in BUCKETS:
        cur_entries = {(_identity(e, bucket) or f"#{i}"): e
                       for i, e in enumerate(cur.get(bucket) or [])}
        carried_ids: set[str] = set()
        out: list[dict] = []
        for raw in desired.get(bucket) or []:
            e = _norm_entry(raw, bucket) if isinstance(raw, dict) else None
            if e is None:
                stats["dropped_no_evidence"] += 1
                continue
            ident = _identity(e, bucket)
            old = cur_entries.get(ident)
            merged_ev = list(e["evidence_ids"])
            if old is not None:
                for oid in old.get("evidence_ids") or []:
                    if oid not in merged_ev:
                        merged_ev.append(oid)
            kept = [i for i in merged_ev if i in valid_evidence] or merged_ev[:0]
            if not kept:
                if old is None:
                    stats["dropped_no_evidence"] += 1
                    continue
                # 携带条目旧证据全部失效：保留旧证据（写入时已校验过），不丢弃历史状态
                kept = list(old.get("evidence_ids") or [])
            if old is None:
                stats["added"] += 1
                entry = {**e, "evidence_ids": kept, "updated_at": now_iso}
            else:
                changed = kept != (old.get("evidence_ids") or []) or e.get("detail") != old.get("detail")
                stats["updated" if changed else "carried"] += 1
                if changed:
                    # W3（2026-09-01）：仅证据或正文真的变化才刷新 updated_at；carried 原样保留
                    # 旧条目，否则「无变化不写」恒失效、按 updated_at 的老化挤除也失灵。
                    entry = {**old, **{k: v for k, v in e.items() if k != "evidence_ids"},
                             "evidence_ids": kept, "updated_at": now_iso}
                else:
                    entry = old
            carried_ids.add(ident)
            out.append(entry)
        # 当前有而期望没有 → resolve（自然消失）
        for ident, old in cur_entries.items():
            if ident not in carried_ids:
                stats["resolved"] += 1
        # 上限：证据最多的前 N 条（同数量保留最新；设计 §3.2"挤掉最旧且 lowest evidence"）
        out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        out.sort(key=lambda x: -len(x.get("evidence_ids") or []))
        new_state[bucket] = out[: BUCKET_LIMITS[bucket]]

    if new_state == cur and isinstance(current, dict):
        return None, stats
    if not any(new_state[b] for b in BUCKETS):
        return None, stats  # 全空 = 无有效状态，不写
    return new_state, stats
