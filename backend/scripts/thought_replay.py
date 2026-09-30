# -*- coding: utf-8 -*-
"""A4 批 4 M0 —— 念头池 T2「离线回放器」（只读既有表，零行为 / 零接线 / 零落库）。

干什么
------
把设计 §2.1 的六个来源面（F1–F6）抽取规则、§2.2 的入池三道过滤、§2.2 的 novelty/salt
与升级/挤出判据，**在只读的生产库历史数据上跑一遍**，回答三个问题：

① 「如果能抽，能抽出多少条、都来自哪个面」；
② 三道过滤各拦掉多少（池会不会灌爆的唯一实证）；
③ 设计 §5 的三条基线数字（池量 / N3+N4 / 撞句）。

规则一律**直载** ``app.domain.thought``（不另写第二套），本脚本只做取数与统计。

只读纪律（写死，无开关）
------------------------
- 连接一律 ``sqlite3.connect("file:...?mode=ro", uri=True)`` + ``PRAGMA query_only=ON``，
  全脚本对库只发 ``SELECT``；**没有 INSERT/UPDATE/DELETE/DDL 任何一处**，不建表、不加列、
  不动 alembic、不新增 flag、不 import 任何发送/仲裁链路。
- 默认 dry-run（``--dry-run`` 只是把这句话显式打出来，行为与不加一致）；**不存在写库分支**。
- 唯一落盘产物是 Markdown 报告（缺省落 ``$AMBRACE_OUTPUT_DIR`` 或 ``./output``，可用 --out 覆盖），UTF-8 无 BOM。

怎么用
------
    backend\\.venv\\Scripts\\python.exe backend/scripts/thought_replay.py
    # 可选只读开关：--days 30  --limit 4000  --db <路径>  --out <报告路径>  --no-report

口径来源
--------
AMBRACE_批4_念头池T2_详细设计_v1_20260929.md §2.1 / §2.2 / §2.3 / §3.2 / §5 / §7（M0 行）。
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.domain.thought import dynamics as dyn  # noqa: E402
from app.domain.thought import extract as ex  # noqa: E402
from app.domain.thought import filters as fl  # noqa: E402
from app.domain.thought import quota as qt  # noqa: E402

DEFAULT_DB = os.path.join(_BACKEND_ROOT, "data", "sqlite", "ai_companion.db")
REPORT_NAME = "AMBRACE_批4_M0_离线回放报告_20260929.md"


def default_report_path() -> str:
    """报告默认落点：环境变量 AMBRACE_OUTPUT_DIR（缺省 ./output）。

    刻意不写死作者机器路径——仓库要能脱敏公开（发布快照核对项之一）。
    """
    return os.path.join(os.environ.get("AMBRACE_OUTPUT_DIR") or "output", REPORT_NAME)
DEFAULT_DAYS = 30
DEFAULT_LIMIT = 4000
# 方案 F §3.1 第一行的目标入流量：≤420 条/30 天（每（角色×用户）日均 ≤0.5）
QUOTA_TARGET_30D = 420

_BJ = timezone(timedelta(hours=8))
# 撞句基线（§5 第 3 条）的三个对手通道：念头不得与它们抢同一句话
COLLISION_CHANNELS = ("life_share", "unfinished_topic", "memory_review")
# F1 摘要短语的取字段顺序（生产库 output_json 多数为 {}，逐字段降级并在报告注明命中来源）
_ACTIVITY_TEXT_KEYS = ("summary", "description", "narration", "result", "title", "reason")


# ────────────────────────────── 取数（全部只读） ──────────────────────────────
def connect_readonly(db_path: str) -> sqlite3.Connection:
    """只读连接：``mode=ro`` 让任何写操作在驱动层就抛 ``readonly database``。"""
    uri = f"file:{os.path.abspath(db_path).replace(os.sep, '/')}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _ts(value) -> datetime | None:
    """SQLite DATETIME 文本 → naive UTC datetime（兼容带/不带微秒两种写法）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def bj_day(dt: datetime | None) -> str:
    """北京日界（与 dynamics.age_days 同口径）：naive UTC → 北京时间日历日。"""
    if dt is None:
        return "-"
    return dt.replace(tzinfo=timezone.utc).astimezone(_BJ).strftime("%Y-%m-%d")


def fetch_all(con: sqlite3.Connection, limit: int, since: datetime, now: datetime) -> dict:
    """一次把六个面 + 参照表读进内存（只 SELECT，只窗口内）。"""
    cur = con.cursor()
    data: dict[str, list[dict]] = {}

    def rows(sql, args=()):
        return [dict(r) for r in cur.execute(sql, args).fetchall()]

    data["activity"] = rows(
        "SELECT id, character_id, activity_type, status, input_json, output_json, completed_at, started_at"
        " FROM life_activity_logs"
        " WHERE coalesce(completed_at, started_at) >= ?"
        " ORDER BY coalesce(completed_at, started_at) DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    data["reflect"] = rows(
        "SELECT id, character_id, user_id, sub_type, content, epistemic_status, created_at"
        " FROM memories WHERE memory_type='ai_reflection' AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    data["moment"] = rows(
        "SELECT id, character_id, user_id, content, epistemic_status, created_at"
        " FROM memories WHERE memory_type='insight' AND sub_type='moment' AND created_at >= ?"
        " ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    data["user_hook"] = rows(
        "SELECT id, character_id, user_id, topic, status, last_touched_at"
        " FROM conversation_topics WHERE status=? ORDER BY last_touched_at DESC LIMIT ?",
        (ex.F4_ACTIVE_STATUS, limit),
    )
    data["fact"] = rows(
        "SELECT id, character_id, user_id, predicate, object_value, epistemic_status, created_at"
        " FROM world_facts WHERE epistemic_status IN ('INFERRED','UNVERIFIED') AND created_at >= ?"
        " ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    # Memory.epistemic_status 也是 F5 的现成信号（设计 §2.1 F5 行），与 world_facts 分列统计
    data["fact_mem"] = rows(
        "SELECT id, character_id, user_id, title, content, epistemic_status, created_at"
        " FROM memories WHERE epistemic_status IN ('INFERRED','UNVERIFIED') AND created_at >= ?"
        " ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    data["interest"] = rows(
        "SELECT id, character_id, name, level, created_at, updated_at"
        " FROM life_interests WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(sep=" "), limit),
    )
    data["proactive_logs"] = rows(
        "SELECT id, character_id, session_id, message_type, content, created_at"
        " FROM proactive_message_logs WHERE created_at >= ? ORDER BY character_id, created_at LIMIT ?",
        (since.isoformat(sep=" "), limit * 4),
    )
    data["ai_moments"] = rows("SELECT id, character_id, user_id, content, created_at FROM ai_moments")
    data["moment_engage"] = rows(
        "SELECT moment_id, count(*) AS n FROM ("
        " SELECT moment_id FROM moment_comments UNION ALL SELECT moment_id FROM moment_likes"
        ") GROUP BY moment_id"
    )
    # N3 需要「发出后 60 分钟内的用户消息」：只读窗口内会话的用户行
    data["user_msgs"] = rows(
        "SELECT session_id, created_at FROM chat_messages"
        " WHERE sender_type='user' AND session_id IS NOT NULL AND created_at >= ?"
        " ORDER BY session_id, created_at LIMIT ?",
        (since.isoformat(sep=" "), limit * 20),
    )
    return data


# ────────────────────────────── 回放主体 ──────────────────────────────
class Replay:
    """一次回放的中间结果集合（纯内存，不外泄）。"""

    def __init__(self) -> None:
        self.drafts: list[dict] = []
        self.rejected: list[dict] = []
        self.face_raw: Counter[str] = Counter()
        self.face_kept: Counter[str] = Counter()
        self.reject_reasons: Counter[str] = Counter()
        self.reject_detail: Counter[str] = Counter()  # 键＝"来源面|拦截原因"
        self.f1_text_origin: Counter[str] = Counter()
        self.notes: list[str] = []


def _activity_phrase(row: dict, replay: Replay) -> str:
    """F1「取活动摘要短语」：output_json → input_json → activity_type 逐级降级。"""
    for col in ("output_json", "input_json"):
        raw = row.get(col)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        for k in _ACTIVITY_TEXT_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                replay.f1_text_origin[f"{col}.{k}"] += 1
                return v.strip()
    replay.f1_text_origin["activity_type 兜底"] += 1
    return str(row.get("activity_type") or "")


def build_moment_link(data: dict) -> tuple[dict, dict]:
    """``insight/moment`` 记忆 → ``ai_moments`` 行的回溯映射。

    生产库 ``memories.source_id`` 全为 NULL（设计 §2.1 F3 行假定的链接不存在），故按
    「记忆正文里 `发了一条朋友圈: <原文>` 的原文」去 ``ai_moments.content`` 归一匹配。
    匹配不上即 ``moment_id=None``，互动数按「未知」处理（不放水也不硬判冷场）。
    """
    by_text: dict[str, int] = {}
    for m in data["ai_moments"]:
        h = ex.text_hash(m.get("content"))
        by_text.setdefault(h, m["id"])
    moment_ts = {m["id"]: _ts(m.get("created_at")) for m in data["ai_moments"]}
    return {"hash2id": by_text, "ts": moment_ts}


def replay_faces(data: dict, now: datetime, days: int, shared_refs: set[str]) -> Replay:
    """逐面跑抽取规则 + 三道过滤，产出 kept/rejected。"""
    rp = Replay()
    link = build_moment_link(data)
    engage = {r["moment_id"]: int(r["n"]) for r in data["moment_engage"]}

    # 每角色「最近已发」序列（过滤② 与撞句基线共用这一份读法，不新建查询口径）
    sent_by_char: dict[int, list[tuple[datetime, str]]] = defaultdict(list)
    for log in data["proactive_logs"]:
        t = _ts(log.get("created_at"))
        if t:
            sent_by_char[log["character_id"]].append((t, str(log.get("content") or "")))
    for v in sent_by_char.values():
        v.sort()
    sent_ts = {c: [t for t, _ in v] for c, v in sent_by_char.items()}

    def recent_before(char_id: int, when: datetime | None, n: int) -> list[str]:
        if when is None:
            return []
        series = sent_by_char.get(char_id, [])
        if not series:
            return []
        i = bisect.bisect_left(sent_ts[char_id], when)
        return [text for _, text in series[max(0, i - n):i]]

    def emit(drafts: list[dict], when: datetime | None, char_id, recent_n: int = fl.RECENT_SENT_LOOKBACK) -> None:
        if not drafts:
            return
        recent = recent_before(char_id, when, recent_n) if char_id is not None else []
        for d in drafts:
            rp.face_raw[d["source_type"]] += 1
            reason = fl.intake_reject_reason(d["text"], d["epistemic_status"], recent)
            ts = when or now
            nov = dyn.novelty(dyn.age_days(ts, now))
            salt = dyn.salt_of([d["source_type"]])
            if reason:
                d.update({"rejected": reason, "novelty": nov, "salt": salt, "age": dyn.age_days(ts, now)})
                rp.rejected.append(d)
                rp.reject_reasons[reason] += 1
                rp.reject_detail[f"{d['source_type']}|{reason}"] += 1
            else:
                d.update({"rejected": None, "novelty": nov, "salt": salt,
                          "age": dyn.age_days(ts, now), "created_at": ts})
                rp.drafts.append(d)
                rp.face_kept[d["source_type"]] += 1

    # F1 活动产物
    for row in data["activity"]:
        row = dict(row)
        row["summary"] = _activity_phrase(row, rp)
        row["user_id"] = None  # life_activity_logs 无 user 维度（报告第 6 节标注）
        ts = _ts(row.get("completed_at") or row.get("started_at"))
        emit(ex.extract_activity(row, shared_refs), ts, row.get("character_id"))

    # F2 反思/复盘
    for row in data["reflect"]:
        emit(ex.extract_reflection(row), _ts(row.get("created_at")), row.get("character_id"))

    # F3 朋友圈沉淀（互动数经归一文本回溯；回溯不到＝互动未知，不判冷场也不入池）
    # M1 订正（方案 F §5.2）：F3 不再按「朋友圈原文哈希」拦——那样每条 F3 都会被自己那条
    # 原文封锁（自我封锁）。同源重复供给改由**结构化键**在 apply_quota 里拦。
    for row in data["moment"]:
        row = dict(row)
        body = str(row.get("content") or "")
        tail = body.split("发了一条朋友圈:", 1)[-1]
        tail = tail.split("发了一条朋友圈：", 1)[-1].strip()
        mid = link["hash2id"].get(ex.text_hash(tail))
        ts = _ts(row.get("created_at"))
        row["age_days"] = dyn.age_days(ts, now)
        row["engagement_count"] = engage.get(mid) if mid is not None else None
        row["content"] = tail  # 送进 F3 的是「所引原文」（哈希去重与回望文案都以它为准）
        if row["engagement_count"] is None:
            rp.face_raw[ex.SRC_MOMENT] += 1
            rp.reject_reasons["moment_link_missing"] += 1
            continue
        out = ex.extract_moment(row)
        for d in out:
            d["moment_id"] = mid
        emit(out, ts, row.get("character_id"))

    # F4 用户钩子
    for row in data["user_hook"]:
        row = dict(row)
        row["idle_days"] = dyn.age_days(_ts(row.get("last_touched_at")), now)
        emit(ex.extract_user_hook(row), _ts(row.get("last_touched_at")), row.get("character_id"))

    # F5 新事实余波（world_facts 与 Memory.epistemic_status 两路，同面同权重）
    for row in data["fact"]:
        row = dict(row)
        row["value"] = row.get("object_value")
        emit(ex.extract_fact(row), _ts(row.get("created_at")), row.get("character_id"))
    for row in data["fact_mem"]:
        row = dict(row)
        row["value"] = (row.get("title") or row.get("content") or "").strip()
        emit(ex.extract_fact(row), _ts(row.get("created_at")), row.get("character_id"))

    # F6 兴趣演化（无历史快照 ⇒ 只判「窗口内新增」）
    for row in data["interest"]:
        emit(ex.extract_interest(row), _ts(row.get("created_at")), row.get("character_id"))

    return rp


def pool_pressure(rp: Replay, now: datetime, days: int) -> dict:
    """设计 §2.2 升级与挤出判据在回放池上的实演（纯内存，绝不落库）。"""
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for d in rp.drafts:
        bucket = fl.topic_bucket(d["text"]) or ex.normalize_text(d["text"])[:12]
        clusters[(d["character_id"], d["user_id"], bucket)].append(d)

    promotable = 0
    promotable_faces: Counter[str] = Counter()
    for (_c, _u, _b), members in clusters.items():
        faces = sorted({m["source_type"] for m in members})
        salt = dyn.salt_of(faces)
        if dyn.should_promote(salt, faces):
            promotable += 1
            for f in faces:
                promotable_faces[f] += 1

    # 挤出：按（角色, 用户）分组，把全部 kept 当同一时刻的活跃池
    evicted = 0
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for i, d in enumerate(rp.drafts):
        groups[(d["character_id"], d["user_id"])].append(
            {"id": i, "status": dyn.STATUS_SPARK, "salt": d["salt"], "novelty": d["novelty"]}
        )
    for _key, recs in groups.items():
        evicted += len(dyn.evict(recs))

    per_day = Counter()
    for d in rp.drafts:
        per_day[bj_day(d.get("created_at"))] += 1
    pairs = len({(d["character_id"], d["user_id"]) for d in rp.drafts}) or 1
    span = max(1, days)
    return {
        "clusters": len(clusters),
        "promotable_obsession": promotable,
        "promotable_faces": promotable_faces,
        "evicted": evicted,
        "kept": len(rp.drafts),
        "pairs": pairs,
        "daily_per_pair": round(len(rp.drafts) / span / pairs, 3),
        "daily_total": round(len(rp.drafts) / span, 2),
        "busiest_days": per_day.most_common(3),
    }


# ────────────────────────────── §5 三条基线 ──────────────────────────────
def baseline_pool(rp: Replay, pressure: dict, days: int) -> dict:
    """基线 1「池量」：各面可抽条数 + 入池/拦截 + 每角色日均入池。"""
    return {
        "days": days,
        "raw_total": sum(rp.face_raw.values()),
        "kept_total": len(rp.drafts),
        "rejected_total": sum(rp.reject_reasons[r] for r in fl.INTAKE_REASONS),
        "per_day": pressure["daily_total"],
        "per_pair_per_day": pressure["daily_per_pair"],
    }


def apply_quota(rp: Replay, now: datetime, days: int,
                caps: dict[str, int] | None = None,
                min_novelty: float | None = None) -> dict:
    """M1 源侧配额复演（纯内存）：可入池 → 实际落池多少、各闸各丢多少。

    判据与影子供给共用同一个 ``app.domain.thought.quota``（准入 → 每日硬闸 → 结构化键去重），
    差别只在回放不落库。``caps`` / ``min_novelty`` 传 None 即用 quota 的模块级默认值，
    传入即试另一套配额（**只在本次调用内生效，不改磁盘默认值**）。

    ⚠️ 口径差异必须写明：这里的 novelty 按「报告时刻」折算，而生产是每条信号在它**产生的那一刻**
    判定（当天≈1.0）。因此回放看到的准入闸拦截量＝「一次性补抽 30 天历史」时的**保守上界**，
    影子期真实运行的入流只会更少（配额闸不受此影响，按北京日分桶与生产一致）。
    """
    used: dict[tuple, int] = {}
    seen: set[str] = set()
    dropped: Counter[str] = Counter()
    kept_face: Counter[str] = Counter()
    pairs: set[tuple] = set()
    for d in sorted(rp.drafts, key=lambda x: x.get("created_at") or now):
        face = str(d.get("source_type") or "")
        char_id, user_id = d.get("character_id"), d.get("user_id")
        ts = d.get("created_at") or now
        key = qt.dedup_key(char_id, user_id, face, d.get("source_ref"))
        if key in seen:
            dropped[qt.DROP_DUP_KEY] += 1
            continue
        pk = qt.pair_day_key(char_id, user_id, face, ts)
        reason = qt.admit_reject_reason(
            source_type=face, novelty_value=dyn.novelty(dyn.age_days(ts, now)),
            used_today=used.get(pk, 0), caps=caps, min_novelty=min_novelty)
        if reason:
            dropped[reason] += 1
            continue
        seen.add(key)
        used[pk] = used.get(pk, 0) + 1
        kept_face[face] += 1
        pairs.add((char_id, user_id))
    kept = sum(kept_face.values())
    n_pairs = len(pairs) or 1
    return {
        "kept": kept,
        "kept_per_day": round(kept / max(1, days), 2),
        "kept_per_pair_per_day": round(kept / n_pairs / max(1, days), 3),
        "pairs": len(pairs),
        "dropped": dict(dropped),
        "dropped_total": sum(dropped.values()),
        "by_face": dict(kept_face),
        "target": QUOTA_TARGET_30D,
        "meets_target": kept <= QUOTA_TARGET_30D,
    }


def baseline_effect(data: dict, now: datetime, days: int) -> dict:
    """基线 2「效果」：N3（60 分钟回复率）与 N4（日均条数），按 message_type 与角色切。

    窗口判定与设计 §5 指认的现成口径一致（``api/scheduler.py:260`` REPLY_WINDOW_MINUTES=60、
    ``:315`` 窗口判定；本脚本自己算，不调端点，避免起服务）。
    """
    user_by_session: dict[int, list[datetime]] = defaultdict(list)
    for m in data["user_msgs"]:
        t = _ts(m.get("created_at"))
        if t:
            user_by_session[m["session_id"]].append(t)
    for v in user_by_session.values():
        v.sort()

    by_type: dict[str, list[int]] = defaultdict(list)
    by_char: dict[int, list[int]] = defaultdict(list)
    day_count: Counter[str] = Counter()
    window = timedelta(minutes=dyn.REPLY_WINDOW_MINUTES)
    for log in data["proactive_logs"]:
        t = _ts(log.get("created_at"))
        if t is None:
            continue
        series = user_by_session.get(log.get("session_id"), [])
        replied = False
        if series:
            i = bisect.bisect_left(series, t)
            replied = i < len(series) and series[i] <= t + window
        by_type[str(log.get("message_type"))].append(1 if replied else 0)
        by_char[int(log.get("character_id") or 0)].append(1 if replied else 0)
        day_count[(log.get("character_id"), bj_day(t))] += 1

    total = sum(len(v) for v in by_type.values())
    hit = sum(sum(v) for v in by_type.values())
    n4_days = len(day_count) or 1
    return {
        "sends": total,
        "replied": hit,
        "reply_rate_60min": round(hit / total * 100, 2) if total else None,
        "by_type": {
            k: (len(v), sum(v), round(sum(v) / len(v) * 100, 2))
            for k, v in sorted(by_type.items(), key=lambda kv: -len(kv[1]))
        },
        "by_char": {k: (len(v), sum(v), round(sum(v) / len(v) * 100, 2))
                    for k, v in sorted(by_char.items(), key=lambda kv: -len(kv[1]))},
        "n4_total_per_day": round(total / n4_days, 2),
        "n4_per_char_day": round(total / n4_days / (len(by_char) or 1), 3),
        "n4_max_char_day": max(day_count.values()) if day_count else 0,
        "active_chars": len(by_char),
        "active_days": n4_days,
    }


def baseline_collision(rp: Replay, data: dict) -> dict:
    """基线 3「撞句」：候选文本 vs 近 30 天三条对手通道的已发文本（主题桶重叠率）。

    词表口径复用设计 §5 第 3 条点名的 ``proactive_topic_guard`` 关键词桶（本包内同名常量）。
    未命中任何桶的候选记为「不可比」，不算重叠也不算安全——报告同时给出可判比例。
    """
    rival_buckets: set[str] = set()
    rival_n = 0
    life_share_n = 0
    for log in data["proactive_logs"]:
        mt = str(log.get("message_type"))
        if mt not in COLLISION_CHANNELS:
            continue
        rival_n += 1
        if mt == "life_share":
            life_share_n += 1
        b = fl.topic_bucket(log.get("content"))
        if b:
            rival_buckets.add(b)
    judged = overlap = 0
    detail: Counter[str] = Counter()
    for d in rp.drafts:
        b = fl.topic_bucket(d["text"])
        if b is None:
            continue
        judged += 1
        if b in rival_buckets:
            overlap += 1
            detail[b] += 1
    return {
        "rival_sends": rival_n,
        "life_share_sends": life_share_n,
        "rival_buckets": sorted(rival_buckets),
        "candidates": len(rp.drafts),
        "judged": judged,
        "unjudgeable": len(rp.drafts) - judged,
        "overlap": overlap,
        "overlap_rate_of_judged": round(overlap / judged * 100, 2) if judged else None,
        "overlap_rate_of_all": round(overlap / len(rp.drafts) * 100, 2) if rp.drafts else None,
        "by_bucket": detail.most_common(6),
    }


def _quantiles(vals: list[float]) -> dict:
    """p10/p25/p50/p75/p90（线性插值，样本量小也稳定）。"""
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    n = len(s)

    def q(p):
        if n == 1:
            return s[0]
        idx = p * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)

    return {"n": n, "min": round(s[0], 4), "p10": round(q(.10), 4), "p25": round(q(.25), 4),
            "p50": round(q(.50), 4), "p75": round(q(.75), 4), "p90": round(q(.90), 4),
            "max": round(s[-1], 4)}


# ────────────────────────────── 报告渲染 ──────────────────────────────
def render_report(rp: Replay, pressure: dict, b1: dict, b2: dict, b3: dict,
                  days: int, db_path: str, now: datetime) -> str:
    L: list[str] = []
    a = L.append
    face_names = {
        ex.SRC_ACTIVITY: "F1 活动产物", ex.SRC_REFLECT: "F2 反思/复盘", ex.SRC_MOMENT: "F3 朋友圈沉淀",
        ex.SRC_USER_HOOK: "F4 用户钩子", ex.SRC_FACT: "F5 新事实余波", ex.SRC_INTEREST: "F6 兴趣演化",
    }
    a("# A4 批 4 · 念头池 T2 —— M0 离线回放报告")
    a("")
    a(f"- 生成：{now.strftime('%Y-%m-%d %H:%M:%S')}（naive UTC）｜窗口：近 {days} 天"
      f"｜库：`{os.path.basename(db_path)}`（**只读** `mode=ro` + `query_only=ON`）")
    a("- 规则来源：`backend/app/domain/thought/`（纯函数，零 IO）；脚本：`backend/scripts/thought_replay.py`")
    a("- **行为影响：0**。未落库、未建表、未加列、未新增 flag、未接线、未改任何既有文件。")
    a("")
    a("## 0 一页结论")
    a("")
    a(f"1. 近 {days} 天六个面共命中原始信号 **{b1['raw_total']}** 条，过完 §2.2 三道过滤后"
      f"可入池 **{b1['kept_total']}** 条（拦截 {b1['rejected_total']} 条）；"
      f"折合 **每角色×用户每日均 {b1['per_pair_per_day']} 条**"
      f"（全体日均 {b1['per_day']} 条 / 活跃 {pressure['pairs']} 个（角色,用户）组合）。")
    top_face, top_n = max(rp.face_kept.items(), key=lambda kv: kv[1], default=("-", 0))
    share = (top_n / b1["kept_total"] * 100) if b1["kept_total"] else 0.0
    days_to_cap = (dyn.CAP_SPARK / b1["per_pair_per_day"]) if b1["per_pair_per_day"] else float("inf")
    a(f"2. **池会被灌爆，风险面＝{top_face}（占可入池 {share:.1f}%）**：极端假设（全部候选同刻入池）"
      f"下容量顶要挤出 **{pressure['evicted']}** 条＝候选的 "
      f"{(pressure['evicted'] / b1['kept_total'] * 100 if b1['kept_total'] else 0):.0f}%；"
      f"按现速率每（角色×用户）组合 **约 {days_to_cap:.1f} 天就撞满 spark≤{dyn.CAP_SPARK} 的顶**"
      f"（未计 TTL 衰减与挤出后的自然回落）。⇒ M1 落表前必须先给 {top_face} 一路加来源门槛"
      f"（或把容量顶当常态机制），否则「挤桶」会变成主要行为而非兜底护栏（§6 R4）。")
    a(f"3. 跨面养熟（§2.2 `spark→obsession`）在本次回放里成立 **{pressure['promotable_obsession']}** 组"
      f"——这是 T2 相对现状的**唯一净增量**，本次为 0/极小，说明单靠确定性文本聚类难以判定，"
      f"M1 影子期必须换用「同一来源主键被多面指到」的精确归并口径复核。")
    a(f"4. 三条基线：池量见第 2 节；N3 60 分钟回复率 **{b2['reply_rate_60min']}%**"
      f"（{b2['replied']}/{b2['sends']}），N4 每角色日均 **{b2['n4_per_char_day']}** 条；"
      f"撞句重叠率 **{b3['overlap_rate_of_judged']}%**（可判子集）/ "
      f"{b3['overlap_rate_of_all']}%（全体子集）。")
    a("")
    a("## 1 只读与零行为声明")
    a("")
    a("- 连接串 `file:...?mode=ro`，并叠加 `PRAGMA query_only=ON`；全脚本对库**只发 SELECT**"
      "（可 grep 自检：无 INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/executescript）。")
    a("- 不 import `app.db.*` / `app.models.*` / `app.scheduling.*` / `app.application.*`，"
      "因此不存在触达 ORM 或发送链路的通路。")
    a("- 唯一落盘产物＝本文件（UTF-8 无 BOM）。**只读已实测核验**：跑脚本前后对 "
      "`ai_companion.db` 取 (size, mtime, 首 256KB SHA1) 三元组，**逐项完全一致**；"
      "而**不跑脚本**静置 25 秒后同一三元组会变化——漂移来自 8000 端口上的活服务"
      "（`scripts/watchdog.py` 拉起的后端在写自己的库），与本脚本无关。脚本侧不 checkpoint、"
      "不 VACUUM、不开写句柄。")
    a("")
    a("## 2 各来源面的可抽条数（§5 基线 1）")
    a("")
    a("| 面 | 原始信号 | 过三道过滤后可入池 | 入池率 | 咸度权重 w |")
    a("|---|---|---|---|---|")
    for face in ex.SOURCE_TYPES:
        raw = rp.face_raw.get(face, 0)
        kept = rp.face_kept.get(face, 0)
        rate = f"{kept / raw * 100:.1f}%" if raw else "-"
        a(f"| {face_names[face]} | {raw} | {kept} | {rate} | {ex.SALT_WEIGHT_BY_SOURCE[face]} |")
    a(f"| 合计 | {b1['raw_total']} | {b1['kept_total']} | "
      f"{(b1['kept_total'] / b1['raw_total'] * 100 if b1['raw_total'] else 0):.1f}% | — |")
    a("")
    a(f"- 最忙的三天（北京日界，入池条数）：{pressure['busiest_days']}")
    a(f"- F1 摘要短语实际取自：`{dict(rp.f1_text_origin)}`"
      "（生产库 `life_activity_logs.output_json` 多为 `{}`，故大量降级——见第 6 节）")
    a("")
    a("## 3 三道过滤各拦下多少（设计 §2.2）")
    a("")
    a("| 过滤 | 口径 | 拦下 |")
    a("|---|---|---|")
    a(f"| ① 长度 | 归一后 {fl.TEXT_MIN_LEN}–{fl.TEXT_MAX_LEN} 字 | "
      f"{rp.reject_reasons.get(fl.REASON_LENGTH, 0)} |")
    a(f"| ② 撞最近已发 | 与角色近 {fl.RECENT_SENT_LOOKBACK} 条主动消息同主题桶 | "
      f"{rp.reject_reasons.get(fl.REASON_RECENT_OVERLAP, 0)} |")
    a(f"| ③ 设定不入池 | `epistemic_status == FICTIONAL` | "
      f"{rp.reject_reasons.get(fl.REASON_FICTIONAL, 0)} |")
    a(f"| （非 §2.2 项）F3 回溯失败 | `memories.source_id` 全 NULL，无法判定 7 天互动窗 | "
      f"{rp.reject_reasons.get('moment_link_missing', 0)} |")
    a(f"| **三道合计（短路计数，一条只记第一个原因）** | — | **{b1['rejected_total']}** |")
    a("")
    a("## 4 novelty / salt 分布（入池候选）")
    a("")
    nq = _quantiles([d["novelty"] for d in rp.drafts])
    sq = _quantiles([d["salt"] for d in rp.drafts])
    aq = _quantiles([d["age"] for d in rp.drafts])
    a("| 量 | n | min | p10 | p25 | p50 | p75 | p90 | max |")
    a("|---|---|---|---|---|---|---|---|---|")
    for label, qv in (("novelty", nq), ("salt（单面入池初值）", sq), ("age_days（北京日界）", aq)):
        if not qv.get("n"):
            a(f"| {label} | 0 | - | - | - | - | - | - | - |")
            continue
        a(f"| {label} | {qv['n']} | {qv['min']} | {qv['p10']} | {qv['p25']} | {qv['p50']} "
          f"| {qv['p75']} | {qv['p90']} | {qv['max']} |")
    a("")
    a(f"- 参数（全部模块级常量，待标定）：`NOVELTY_E_FOLDING_DAYS={dyn.NOVELTY_E_FOLDING_DAYS}`、"
      f"`SALT_OBSSESSION_THRESHOLD={dyn.SALT_OBSSESSION_THRESHOLD}`、"
      f"`MIN_DISTINCT_HIT_SOURCES={dyn.MIN_DISTINCT_HIT_SOURCES}`、`TTL_DAYS={dyn.TTL_DAYS}`、"
      f"`CAP_SPARK={dyn.CAP_SPARK}` / `CAP_OBSESSION={dyn.CAP_OBSESSION}` / "
      f"`CAP_TOLD_FLAT={dyn.CAP_TOLD_FLAT}`、`SALT_TOLD_FLAT_RATIO={dyn.SALT_TOLD_FLAT_RATIO}`、"
      f"`MAX_TELL_COUNT={dyn.MAX_TELL_COUNT}`、`SALT_BUMP_WEIGHT={dyn.SALT_BUMP_WEIGHT}`。"
      f" 源侧配额（M1 新增）：`FACE_DAY_CAP={qt.FACE_DAY_CAP}`、"
      f"`FACE_DAY_CAP_DEFAULT={qt.FACE_DAY_CAP_DEFAULT}`、`ADMIT_MIN_NOVELTY={qt.ADMIT_MIN_NOVELTY}`。")
    face_nov: dict[str, list[float]] = defaultdict(list)
    for d in rp.drafts:
        face_nov[d["source_type"]].append(d["novelty"])
    a("- 按面的 novelty 中位数：" + "; ".join(
        f"{f}={_quantiles(v)['p50']}" for f, v in sorted(face_nov.items()) if v))
    a("")
    a("## 5 §5 三条基线实测")
    a("")
    a("### 5.1 基线 1 · 池量（离线回放）")
    a("")
    a(f"- 可入池 {b1['kept_total']} 条 / {days} 天 = **日均 {b1['per_day']} 条**，"
      f"每（角色×用户）组合 **日均 {b1['per_pair_per_day']} 条**（组合数 {pressure['pairs']}）。")
    a(f"- 极端假设（全部候选同刻入池）下需挤出 {pressure['evicted']} 条；"
      f"按 {dyn.CAP_SPARK} 的顶，单一组合约 {dyn.CAP_SPARK / max(b1['per_pair_per_day'], 0.001):.0f} "
      f"天触顶（未计 TTL 与挤出后的自然衰减）。")
    q = apply_quota(rp, now, days)
    a(f"- **M1 源侧配额后**：落池 **{q['kept']}** 条 / {days} 天（日均 {q['kept_per_day']}，"
      f"每（角色×用户）日均 **{q['kept_per_pair_per_day']}**），丢弃 {q['dropped_total']} 条"
      f"＝准入 {q['dropped'].get(qt.DROP_ADMIT, 0)} / 每日硬闸 {q['dropped'].get(qt.DROP_QUOTA, 0)}"
      f" / 同源重复 {q['dropped'].get(qt.DROP_DUP_KEY, 0)}；"
      f"目标 ≤{q['target']} 条 ⇒ **{'达标' if q['meets_target'] else '未达标'}**。"
      f"按面留存＝{sorted(q['by_face'].items(), key=lambda kv: -kv[1])}。")
    a("")
    a("### 5.2 基线 2 · 效果（N3 回复率 / N4 日均条数）")
    a("")
    a(f"- **N3（60 分钟回复率，全体基线）= {b2['reply_rate_60min']}%**"
      f"（{b2['replied']}/{b2['sends']}）。这是 §5 指标表 N3 行的「同角色全体基线」参照值；"
      f"「引用念头 vs 未引用」的分组要等 M2 有 `extra_meta.thought_id` 才出得来，**本次取不到**"
      f"（该字段今天不存在）。")
    a(f"- **N4（日均条数）= 每角色日均 {b2['n4_per_char_day']} 条**"
      f"（全体日均 {b2['n4_total_per_day']} 条，活跃角色 {b2['active_chars']} 个、"
      f"有发出的天数 {b2['active_days']} 天，单角色单日峰值 {b2['n4_max_char_day']} 条）。"
      f"达标线「不升 >10%」以此为分母。")
    a("- 按 `message_type` 切分（条数 / 被接住 / 60 分钟回复率%）：")
    a("")
    a("| message_type | 条数 | 60min 有回 | 回复率% |")
    a("|---|---|---|---|")
    for k, (n, hit, rate) in list(b2["by_type"].items())[:18]:
        a(f"| {k} | {n} | {hit} | {rate} |")
    a("")
    a("- 按角色切分（前 10，条数 / 被接住 / 回复率%）：" + "; ".join(
        f"char{k}={v[0]}/{v[1]}/{v[2]}%" for k, v in list(b2["by_char"].items())[:10]))
    a("")
    a("### 5.3 基线 3 · 撞句（候选文本 vs 三通道已发文本）")
    a("")
    a(f"- 对手通道（{'/'.join(COLLISION_CHANNELS)}）近 {days} 天已发 **{b3['rival_sends']}** 条，"
      f"其主题桶集合：`{b3['rival_buckets']}`。")
    a(f"- 候选 {b3['candidates']} 条中可判（命中某主题桶）{b3['judged']} 条、"
      f"不可判 {b3['unjudgeable']} 条；可判子集里与对手桶重叠 **{b3['overlap']}** 条 = "
      f"**{b3['overlap_rate_of_judged']}%**；按全体候选算 **{b3['overlap_rate_of_all']}%**。")
    a(f"- 重叠落在哪些桶：{b3['by_bucket']}")
    a("- 结论：M2-a 前置条件④「与三通道文本重叠率 <5%」——**按可判子集达标/不达标需人工判定**，"
      "因为主题桶口径比设计里的「同一句话」宽得多（同桶≠同句）；本报告同时给出两个分母，"
      "避免用「全体候选」稀释出好看的数字。")
    a("")
    a("## 6 本次取不到 / 口径打折的项（诚实标注）")
    a("")
    a(f"1. **F1「life_share 本次未分享」判不出来**：`proactive_message_logs` 近 {days} 天只有 "
      f"{b3['life_share_sends']} 条 `life_share`，且 `extra_meta` 为 NULL，"
      "无法回溯「哪一次活动被讲掉了」。故 `shared_refs` 传空集 ⇒ 本次 F1 全部按「未分享→spark」处理，"
      "属**高估**（真实入池量只会更少）。M1 挂 event handler 时能当场判定，不受此限。")
    a("2. **F3 的 7 天互动窗回溯失败**：`memories(m_type=insight,sub_type=moment).source_id` 全为 NULL，"
      "只能按正文「发了一条朋友圈: <原文>」去 `ai_moments.content` 归一匹配；匹配失败者已计入"
      "`moment_link_missing`（第 3 节），**不入池也不判冷场**。")
    a("3. **F6「强度上升」不可判**：`life_interests` 无历史快照，只有当前 `level`，故 M0 只按"
      "「窗口内新增」出数（`extract_interest` 的 `prev_level` 分支已实现并单测，等 M1 有漂移前值即可用）。")
    a("4. **F1/F6 无 user 维度**：`life_activity_logs`、`life_interests` 只有 `character_id`，"
      "回放里 user_id 记 None ⇒ 「每（角色×用户）池」的分母偏小，日均值偏高。")
    a("5. **N3 的「引用念头 vs 未引用」分组取不到**：`extra_meta.thought_id` 属 M2 才写入的字段，"
      "今天不存在；本次只能给「同角色全体基线」这一半。")
    a("6. **跨面养熟用近似归并**（第 0 节第 3 条）：按「主题桶 or 归一文本前 12 字」聚簇，"
      "与 M1 的精确幂等键（角色,用户,来源类型,来源主键,文本哈希）不同口径，**只作量级参考**。")
    a("7. **两处常量是就地复制而非 import**（零业务 import 的代价）：`UNFINISHED_TOPIC_KEYWORDS` "
      "复制 `scheduling/unfinished_topic.py:22`、`TOPIC_BUCKETS` 复制 "
      "`scheduling/proactive_topic_guard.py:38`。若生产词表漂移，本域不会自动跟随——"
      "M1 接线时要么改成注入式参数（函数已留 `unfinished_keywords=` / `recent_texts=` 入参），"
      "要么补一条一致性测试。")
    f5_len = rp.reject_detail.get(f"{ex.SRC_FACT}|{fl.REASON_LENGTH}", 0)
    f1_len = rp.reject_detail.get(f"{ex.SRC_ACTIVITY}|{fl.REASON_LENGTH}", 0)
    a(f"8. **过滤① 是本次最大拦截项（{rp.reject_reasons.get(fl.REASON_LENGTH, 0)} 条），且几乎全部"
      f"归因于「正文照抄」**：F5 一路 {f5_len} 条、F1 一路 {f1_len} 条——长度闸拦下的就是"
      "「从长记忆里截一段」这种伪候选。本单已把抽取阶段的截断从 40 字**改回 §2.5 的列宽 120**，"
      "不再由抽取器提前把长文洗白成长度合规的候选（`extract.POOL_TEXT_MAX_LEN` 注释记录了这次订正）。"
      "结论：M1 若要接 F5 的 `Memory.epistemic_status` 一路，必须换成「短标题/短 object 值」来源，"
      "不能靠截断。")
    a("9. **设计 §2.2 的 novelty 公式与常量名不一致（本单照公式实现，未擅自改）**："
      f"`novelty = exp(-age_days / NOVELTY_E_FOLDING_DAYS)`（M1 已按方案 F §5.1 **只改名、"
      f"公式不动**，零行为变更）里的 τ 是 **e 折叠时间**，"
      f"真实减半点在 `τ·ln2`——τ={dyn.NOVELTY_HALFLIFE_DAYS} 天时 novelty≈"
      f"{dyn.novelty(dyn.NOVELTY_HALFLIFE_DAYS):.3f}，要到约 "
      f"{dyn.NOVELTY_HALFLIFE_DAYS * math.log(2):.2f} 天才掉到 0.5。"
      "M1 标定前必须拍板：要么把常量改叫 `NOVELTY_E_FOLDING_DAYS`（保留公式），"
      "要么把公式改成 `0.5 ** (age/HALFLIFE)`（保留「半衰期」语义）。"
      "两种写法在 τ=7 下差一条曲线，**不影响 M0 任何数字**（本次只做分布观测，不参与选择）。")
    a("10. **F3 的 §3.2「原文哈希去重」去重位＝入池文本**（见 `extract_moment` docstring）："
      "设计原文按「朋友圈原文」哈希会把你自己的 F3 全部自我封锁（F3 的输入本就来自朋友圈），"
      "故去重按**成念文本**哈希（回望文案，与原文不同串）。真正防「同一件事重复入池」的位子是"
      "§2.5 的唯一约束 `(角色,用户,来源类型,来源主键,文本哈希)`，M1 落表后自动成立。")
    a("")
    a("## 7 复跑与自检命令")
    a("")
    a("```")
    a("backend/.venv/Scripts/python.exe -m ruff check backend/app")
    a("cd backend && .venv/Scripts/python.exe -m pytest tests/test_thought_m0.py -q --basetemp=.pytest_m4")
    a("backend/.venv/Scripts/python.exe backend/scripts/thought_replay.py   # 默认 dry-run，只出报告")
    a("```")
    a("")
    a("（同 §7 M0 行 DoD：**不落库、不接线、不新增 flag、不新增表**。）")
    a("")
    return "\n".join(L) + "\n"


# ───────────────────────── 参数覆盖（N1 新增 · 只读模拟入口）─────────────────────────
# M1 标定前需要「不落盘地试参数」，故加一个**仅本进程内**覆盖 domain/thought 模块常量的入口，
# 只服务离线回放的假设检验：不改源码默认值、不写库、不接线、退出即自动还原。
# 例：--params "CAP_SPARK=24,NOVELTY_HALFLIFE_DAYS=3.5,W:activity=0.4"
_OVERRIDE_WHITELIST = frozenset({
    # τ 的新名（M1 订正①）；旧名 NOVELTY_HALFLIFE_DAYS 仍保留可用——两者都指向 novelty 的
    # 同一个早绑定默认值槽位，回放历史报告里的 --params 串照旧能复跑。
    "NOVELTY_E_FOLDING_DAYS", "NOVELTY_HALFLIFE_DAYS", "SALT_OBSSESSION_THRESHOLD", "MIN_DISTINCT_HIT_SOURCES",
    "TTL_DAYS", "CAP_SPARK", "CAP_OBSESSION", "CAP_TOLD_FLAT",
    "SALT_TOLD_FLAT_RATIO", "MAX_TELL_COUNT", "SALT_BUMP_WEIGHT",
})

# Python 默认参数在 def 时求值 ⇒ 只 setattr 模块属性到不了「早绑定」的默认值，必须连同
# ``__defaults__`` 一起刷新（否则 novelty()/evict() 仍用旧值，模拟会静默失效）。
_BOUND_DEFAULTS = {
    "NOVELTY_E_FOLDING_DAYS": ("novelty", 0),
    "NOVELTY_HALFLIFE_DAYS": ("novelty", 0),
    "CAP_SPARK": ("evict", 0),
    "CAP_OBSESSION": ("evict", 1),
    "CAP_TOLD_FLAT": ("evict", 2),
}


def parse_params_spec(spec: str | None) -> dict[str, float]:
    """解析 ``--params`` 串 ``k=v,...``；来源权重用 ``W:activity=0.4``。空串/None → {}。"""
    out: dict[str, float] = {}
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "=" not in tok:
            raise ValueError(f"非法参数段（缺 '='）：{tok!r}")
        k, v = (s.strip() for s in tok.split("=", 1))
        if not k:
            raise ValueError(f"非法参数段（缺键名）：{tok!r}")
        try:
            out[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
        except ValueError:
            raise ValueError(f"参数 {k} 的值不是数字：{v!r}") from None
    return out


@contextlib.contextmanager
def param_overrides(overrides: dict[str, float]):
    """进程内临时覆盖 domain/thought 参数（只读模拟，退出自动还原）。

    ⚠️ 关键点：``novelty(halflife_days=NOVELTY_HALFLIFE_DAYS)`` 与
    ``evict(cap_spark=CAP_SPARK, ...)`` 的默认参数是 **def 期求值**（早绑定），
    仅替换模块属性对这两处**无效**，故此处连同 ``__defaults__`` 一起刷新、退出一起还原。
    """
    if not overrides:
        yield {}
        return

    unknown = [k for k in overrides if not k.startswith("W:") and k not in _OVERRIDE_WHITELIST]
    if unknown:
        raise ValueError(f"不允许覆盖的参数（非白名单）：{unknown}；白名单＝{sorted(_OVERRIDE_WHITELIST)}")

    saved_attrs: dict[str, object] = {}
    saved_defaults: dict[str, tuple] = {}
    try:
        for k, v in overrides.items():
            if k.startswith("W:"):
                face = k[2:]
                if face not in dyn.SALT_WEIGHT_BY_SOURCE:
                    raise ValueError(
                        f"未知来源面：{face}；已知＝{sorted(dyn.SALT_WEIGHT_BY_SOURCE)}")
                # 换新 dict，避免就地修改污染 extract 模块的字典对象
                saved_attrs.setdefault("SALT_WEIGHT_BY_SOURCE", dyn.SALT_WEIGHT_BY_SOURCE)
                dyn.SALT_WEIGHT_BY_SOURCE = dict(dyn.SALT_WEIGHT_BY_SOURCE)
                dyn.SALT_WEIGHT_BY_SOURCE[face] = v
                continue
            # 白名单 ⊆ dyn 属性 的一致性，由 tests/test_thought_replay_params.py 的一致性用例守住
            saved_attrs.setdefault(k, getattr(dyn, k))
            setattr(dyn, k, v)
            if k in _BOUND_DEFAULTS:
                fname, idx = _BOUND_DEFAULTS[k]
                fn = getattr(dyn, fname)
                saved_defaults.setdefault(fname, fn.__defaults__)
                d = list(fn.__defaults__ or ())
                d[idx] = v
                fn.__defaults__ = tuple(d)
        yield dict(overrides)
    finally:
        for k, v in saved_attrs.items():
            setattr(dyn, k, v)
        for fname, d in saved_defaults.items():
            getattr(dyn, fname).__defaults__ = d


# ────────────────────────────── CLI ──────────────────────────────
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="念头池 T2 · M0 离线回放（只读，零行为）")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help="回看窗口（天），默认 30")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="每个面最多读多少行，默认 4000")
    p.add_argument("--db", default=DEFAULT_DB, help="sqlite 库路径（只读打开）")
    p.add_argument("--out", default=default_report_path(), help="报告输出路径")
    p.add_argument("--dry-run", action="store_true", help="显式声明 dry-run（本就是唯一行为）")
    p.add_argument("--no-report", action="store_true", help="只打印摘要，不落报告文件")
    p.add_argument("--params", default=None,
                   help="只读模拟：本进程内覆盖 domain/thought 参数，格式 k=v,..."
                        "（来源权重用 W:activity=0.4）。不影响任何磁盘默认值")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    now = dyn.now_utc()
    since = now - timedelta(days=max(1, args.days))

    con = connect_readonly(args.db)
    try:
        data = fetch_all(con, max(1, args.limit), since, now)
    finally:
        con.close()

    shared_refs: set[str] = set()  # 见报告第 6 节第 1 条：现网 life_share 留痕不可回溯
    overrides = parse_params_spec(args.params)
    with param_overrides(overrides):
        rp = replay_faces(data, now, args.days, shared_refs)
        pressure = pool_pressure(rp, now, args.days)
        b1 = baseline_pool(rp, pressure, args.days)
        b2 = baseline_effect(data, now, args.days)
        b3 = baseline_collision(rp, data)
        quota = apply_quota(rp, now, args.days)
        report = render_report(rp, pressure, b1, b2, b3, args.days, args.db, now)
    if not args.no_report:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)

    print(f"[M0 dry-run] 窗口 {args.days} 天｜原始信号 {b1['raw_total']} → 可入池 {b1['kept_total']}"
          f"｜拦截 {b1['rejected_total']}（长度 {rp.reject_reasons.get(fl.REASON_LENGTH, 0)}"
          f"/撞最近 {rp.reject_reasons.get(fl.REASON_RECENT_OVERLAP, 0)}"
          f"/设定 {rp.reject_reasons.get(fl.REASON_FICTIONAL, 0)}）")
    print(f"[M0 基线] N3={b2['reply_rate_60min']}%  N4每角色日均={b2['n4_per_char_day']}  "
          f"撞句重叠率={b3['overlap_rate_of_judged']}%(可判)/{b3['overlap_rate_of_all']}%(全体)  "
          f"养熟组数={pressure['promotable_obsession']}  挤出={pressure['evicted']}")
    print(f"[M1 配额] 可入池 {b1['kept_total']} → 落池 {quota['kept']}（丢弃 {quota['dropped_total']}："
          f"准入 {quota['dropped'].get(qt.DROP_ADMIT, 0)}／每日硬闸 {quota['dropped'].get(qt.DROP_QUOTA, 0)}"
          f"／同源重复 {quota['dropped'].get(qt.DROP_DUP_KEY, 0)}）"
          f"｜日均 {quota['kept_per_day']} 条｜每(角色×用户)日均 {quota['kept_per_pair_per_day']}"
          f"｜目标 ≤{quota['target']} 条/30 天 ⇒ {'达标' if quota['meets_target'] else '未达标'}")
    print(f"[M0 分布] novelty p50={_quantiles([d['novelty'] for d in rp.drafts]).get('p50')}  "
          f"salt p50={_quantiles([d['salt'] for d in rp.drafts]).get('p50')}")
    if overrides:
        _kept = max(1, len(rp.drafts))
        _ev = int(pressure['evicted'])
        print(f"[参数覆盖-指标] 可入池={_kept}  落池(配额后)={quota['kept']}  丢弃={quota['dropped_total']}  挤出={_ev}"
              f"（{_ev * 100.0 / _kept:.2f}%）  "
              f"池量每(角色×用户)日均={b1['per_pair_per_day']}  "
              f"撞句={b3['overlap_rate_of_judged']}%(可判)/{b3['overlap_rate_of_all']}%(全体)  "
              f"养熟组={pressure['promotable_obsession']}  "
              f"novelty p50={_quantiles([d['novelty'] for d in rp.drafts]).get('p50')}")
    print(f"[M0 落盘] 报告 → {('未写（--no-report）' if args.no_report else args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
