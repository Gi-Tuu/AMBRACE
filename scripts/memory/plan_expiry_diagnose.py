"""AMBRACE 存量治理 · 只读诊断脚本（2026-09-09，主动复习「回忆化」批次三交付物）。

口径（交接批次三）：
  A. 候选=event 且时态 plan 且已过期、仍 active（会被主动复习当"新闻"翻出的旧计划）；
     SQL 用将来时标记词 LIKE 预筛（与 maintain_plan_expiry 同口径），Python 侧以
     backend/app/memory/tense.py 规则精判（classify_tense + is_plan_expired）；
  B. 另列 S>=55 高优 event 清单（被复习强化"养成永生"的一次性事件）；
     附 §8.1-B 口径（strength_days>10 OR review_count>3）统计供对照。

安全：全程只读（sqlite3 file:...?mode=ro）；只 SELECT，不写库、不删记忆。
输出：stdout 摘要统计 + 完整清单 CSV / 统计 MD（默认写到 OUTPUT_DIR，可用 --out 覆盖）。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\plan_expiry_diagnose.py
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\plan_expiry_diagnose.py --db D:\\...\\x.db --out D:\\tmp
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from app.memory.tense import (
    PLAN_MARKERS,
    classify_tense,
    is_plan_expired,
    plan_valid_until,
)

DEFAULT_DB = os.path.join(SERVER_DIR, "backend", "data", "sqlite", "ai_companion.db")
# 输出目录：优先读环境变量 AMBRACE_PLAN_EXPIRY_OUT，缺省用项目根同级 output/ 下的固定子目录
DEFAULT_OUT = os.environ.get("AMBRACE_PLAN_EXPIRY_OUT") or os.path.abspath(
    os.path.join(SERVER_DIR, "..", "output", "AMBRACE_存量治理_清单_20260909")
)
HIGH_S_THRESHOLD = 55.0   # 交接口径：S>=55 高优 event 清单
EVENT_S_CAP = 10.0        # §8.2 修复口径：event S 复位上限
EVENT_REVIEW_CAP = 3      # §8.2 修复口径：event 复习次数上限

_COLS = ("id", "user_id", "character_id", "memory_type", "sub_type", "is_core",
         "core_category", "title", "content", "why_it_matters", "importance",
         "strength_days", "review_count", "created_at", "next_review_at",
         "valid_to", "status")


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_obj(r: dict) -> SimpleNamespace:
    """sqlite 行 → tense 规则所需的最小对象面（DATETIME 列在 sqlite 里是 str，先归一为 datetime）。"""
    return SimpleNamespace(
        memory_type=r["memory_type"], sub_type=r["sub_type"],
        is_core=bool(r["is_core"]), core_category=r["core_category"],
        title=r["title"], content=r["content"], why_it_matters=r["why_it_matters"],
        created_at=_parse_dt(r["created_at"]), valid_to=_parse_dt(r["valid_to"]),
    )


def _parse_dt(v):
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("T", " "))
    except ValueError:
        return None


def connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise SystemExit(f"[abort] 数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_active_events(conn: sqlite3.Connection) -> list[dict]:
    """SQL 预筛：active、未归档、event、content 命中任一将来时标记词（扫描窗收窄）。"""
    like = " OR ".join(["content LIKE ?"] * len(PLAN_MARKERS))
    sql = (
        "SELECT id,user_id,character_id,memory_type,sub_type,is_core,core_category,"
        "title,content,why_it_matters,importance,strength_days,review_count,"
        "created_at,next_review_at,valid_to,status "
        f"FROM memories WHERE status='active' AND is_archived=0 AND memory_type='event' AND ({like})"
    )
    rows = conn.execute(sql, tuple(f"%{k}%" for k in PLAN_MARKERS)).fetchall()
    return [dict(r) for r in rows]


def classify_a(rows: list[dict], now: datetime) -> list[dict]:
    """A 口径：tense 精判为 plan 且已过期（is_plan_expired）。"""
    out = []
    for r in rows:
        obj = _row_obj(r)
        try:
            if classify_tense(obj) == "plan" and is_plan_expired(obj, now):
                r = dict(r)
                r["plan_valid_until"] = plan_valid_until(obj, now)
                out.append(r)
        except Exception as e:  # noqa: BLE001  # 单条失败不阻塞诊断（诊断脚本必须全量跑完）
            print(f"[warn] classify 失败 id={r['id']}: {e}")
    return out


def load_b(conn: sqlite3.Connection) -> list[dict]:
    """B 口径：S>=55 的 event（不分状态；stale 也列出供决策参考，修复只动 active 之外另行标注）。"""
    sql = (
        "SELECT id,user_id,character_id,memory_type,sub_type,title,content,"
        "importance,strength_days,review_count,created_at,next_review_at,status "
        "FROM memories WHERE memory_type='event' AND strength_days>=? "
        "ORDER BY strength_days DESC, review_count DESC"
    )
    return [dict(r) for r in conn.execute(sql, (HIGH_S_THRESHOLD,)).fetchall()]


def load_b_repair_scope(conn: sqlite3.Connection) -> list[dict]:
    """§8.2-2 修复口径：event 且 (strength_days>上限 OR review_count>上限)。"""
    sql = (
        "SELECT id,user_id,character_id,title,content,strength_days,review_count,"
        "created_at,next_review_at,status FROM memories "
        "WHERE memory_type='event' AND (strength_days>? OR review_count>?) "
        "ORDER BY review_count DESC"
    )
    return [dict(r) for r in conn.execute(sql, (EVENT_S_CAP, EVENT_REVIEW_CAP)).fetchall()]


def write_csv(path: str, rows: list[dict], cols: list[str]):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="存量治理只读诊断（A 过期计划 / B 高S事件）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--limit-print", type=int, default=20, help="stdout 每类清单最多打印条数")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    now = _now_naive()
    conn = connect_ro(args.db)

    pre = load_active_events(conn)
    a = classify_a(pre, now)
    b = load_b(conn)
    b_repair = load_b_repair_scope(conn)
    conn.close()

    # —— 清单文件 ——
    a_cols = ["id", "user_id", "character_id", "sub_type", "title", "content",
              "importance", "strength_days", "review_count", "created_at",
              "next_review_at", "valid_to", "plan_valid_until", "status"]
    write_csv(os.path.join(args.out, "A_过期计划仍active.csv"), a, a_cols)
    write_csv(os.path.join(args.out, "B_高S事件_S55以上.csv"), b,
              ["id", "user_id", "character_id", "title", "content", "importance",
               "strength_days", "review_count", "created_at", "next_review_at", "status"])
    write_csv(os.path.join(args.out, "B2_修复口径_event超限.csv"), b_repair,
              ["id", "user_id", "character_id", "title", "content", "strength_days",
               "review_count", "created_at", "next_review_at", "status"])

    # —— 统计 ——
    a_by_char: dict[int, int] = {}
    for r in a:
        a_by_char[r["character_id"]] = a_by_char.get(r["character_id"], 0) + 1
    a_high_s = [r for r in a if (r["strength_days"] or 0) >= HIGH_S_THRESHOLD]
    b_active = [r for r in b if r["status"] == "active"]
    b_repair_active = [r for r in b_repair if r["status"] == "active"]
    union_ids = {r["id"] for r in a} | {r["id"] for r in b_repair}
    s_dist = {}
    for r in b:
        bucket = f"S{int((r['strength_days'] or 0) // 10) * 10}-{int((r['strength_days'] or 0) // 10) * 10 + 9}"
        s_dist[bucket] = s_dist.get(bucket, 0) + 1

    lines = []
    lines.append(f"# AMBRACE 存量治理只读诊断 · {now:%Y-%m-%d %H:%M} UTC")
    lines.append("")
    lines.append(f"- 数据库：{args.db}（mode=ro 只读）")
    lines.append(f"- SQL 预筛（active+event+将来时标记词）：{len(pre)} 行；tense 精判后 A 口径：**{len(a)} 行**")
    lines.append(f"- A 按角色分布：{dict(sorted(a_by_char.items()))}")
    lines.append(f"- A 中 S>=55：{len(a_high_s)} 行")
    lines.append(f"- B（event S>=55，不分状态）：{len(b)} 行（其中 active {len(b_active)} 行）")
    lines.append(f"- B S 分布：{dict(sorted(s_dist.items()))}")
    lines.append(f"- B2 §8.2 修复口径（event S>{EVENT_S_CAP:g} 或 review>{EVENT_REVIEW_CAP}）：{len(b_repair)} 行（其中 active {len(b_repair_active)} 行）")
    lines.append(f"- A ∪ B2 影响面（去重 id）：{len(union_ids)} 行")
    lines.append("")
    lines.append("## A 明细（全部，前 50 条）")
    lines.append("")
    lines.append("| id | char | S | review | created_at | content |")
    lines.append("|---|---|---|---|---|---|")
    for r in a[:50]:
        lines.append(f"| {r['id']} | {r['character_id']} | {r['strength_days']} | "
                     f"{r['review_count']} | {str(r['created_at'])[:19]} | {(r['content'] or '')[:40]} |")
    if len(a) > 50:
        lines.append(f"\n（其余 {len(a) - 50} 条见 A_过期计划仍active.csv）")

    md = "\n".join(lines)
    md_path = os.path.join(args.out, "诊断统计.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md + "\n")

    print(md)
    print()
    print(f"[done] 清单已写出：{args.out}")
    print("       - A_过期计划仍active.csv / B_高S事件_S55以上.csv / B2_修复口径_event超限.csv / 诊断统计.md")
    print("[note] 本脚本全程只读，未写库、未删任何记忆。修复请用 plan_expiry_repair.py（默认 dry-run）。")


if __name__ == "__main__":
    main()
