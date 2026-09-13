# -*- coding: utf-8 -*-
"""前瞻约定（prospective_intents）存量清理脚本（2026-09-13，交接 ④）。

只做**状态标记**（留痕不删，与「回忆不等于删除」口径一致），不物理删除任何行：
  1) 时效超窗：status=pending、due_end 非空且 due_end < now - N 小时 → stale
     （N = --window-hours，默认 12，与 app.scheduling.prospective_intent.PROMISE_ACTIVE_WINDOW_HOURS 对齐；
      默认覆盖 kind=promise,cue，可 --kinds 收窄；--window-hours 0 = 所有已过 due_end 的行）；
  2) 无 due 陈旧：status=pending、due_start/due_end 都为 NULL 且 created_at < now - M 天 → stale
     （M = --nodue-days，默认 30）；
  3) 同义重复：同角色 + 同 due 窗口 + 文本高度相近的 pending promise → 只保留一条
     （优先保留 side=self 的口径正确条，其次最早一条），其余置 stale
     （阈值 --similar-threshold，默认 0.8）。仅 --apply 时执行。

用法：
  .venv\\Scripts\\python.exe scripts/cleanup_prospective_intents.py            # dry-run（默认，只统计）
  .venv\\Scripts\\python.exe scripts/cleanup_prospective_intents.py --apply    # 实际写库（先备份！）

安全默认：不加 --apply 绝不写库；--apply 前请先备份 backend/data/sqlite/ai_companion.db
（backups/ 已有既有惯例，如 backups/YYYYMMDD.zip）。运行期间建议停止服务器，避免并发写。
"""
import argparse
import os
import sqlite3
import sys
from difflib import SequenceMatcher

DEFAULT_DB = os.environ.get(
    "AMB_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data", "sqlite", "ai_companion.db"),
)
DEFAULT_WINDOW_HOURS = 12
DEFAULT_NODUE_DAYS = 30
DEFAULT_SIMILAR_THRESHOLD = 0.8


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def normalize(text: str) -> str:
    """归一化文本：去空白/标点、小写（用于同义重复判定）。"""
    keep = []
    for ch in (text or "").lower():
        if ch.isspace() or ch in "，。！？、,.!?;；:：\"'“”‘’()（）[]【】~～-—":
            continue
        keep.append(ch)
    return "".join(keep)


def similar(a: str, b: str, threshold: float) -> bool:
    """两段 intent 文本是否高度相近（归一化全等 / 长度接近的包含 / SequenceMatcher 比值）。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        if min(len(na), len(nb)) / max(len(na), len(nb)) >= threshold:
            return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


# ① 主体口径（与 app.scheduling.prospective_intent.classify_intent_side 同规则，脚本自包含）
_SELF_PREFIXES = ("我承诺", "我答应", "我保证", "我来", "我会", "我去", "我要",
                  "我将", "我负责", "我准备", "我打算")
_USER_PREFIXES = ("用户", "对方", "ta", "TA", "他", "她")


def classify_side(content: str, kind: str) -> str:
    """脚本侧 side 判定（self|user）；仅用于合并时优先保留口径正确的那条。"""
    t = (content or "").strip()
    if kind == "cue":
        return "user"
    if t.startswith(_USER_PREFIXES):
        return "user"
    if t.startswith(_SELF_PREFIXES):
        return "self"
    if "用户" in t or "对方" in t:
        return "user"
    if any(p in t for p in ("我承诺", "我答应", "我保证")):
        return "self"
    return "user"


def find_overdue(
    con: sqlite3.Connection, window_hours: float, char_id: int | None, kinds: tuple[str, ...],
) -> list[sqlite3.Row]:
    """① 超窗 pending（due_end < now - N 小时；默认覆盖 promise/cue，见 --kinds）。"""
    placeholders = ",".join("?" for _ in kinds)
    sql = (
        "SELECT id, character_id, content, kind, due_start, due_end, status, created_at "
        "FROM prospective_intents "
        "WHERE status='pending' AND due_end IS NOT NULL "
        f"  AND kind IN ({placeholders}) "
        "  AND julianday(due_end) < julianday('now', ?)"
    )
    params: list = [*kinds, f"-{float(window_hours)} hours"]
    if char_id is not None:
        sql += " AND character_id = ?"
        params.append(char_id)
    sql += " ORDER BY character_id, due_end"
    return con.execute(sql, params).fetchall()


def find_stale_nodue(con: sqlite3.Connection, nodue_days: float, char_id: int | None) -> list[sqlite3.Row]:
    """② 无 due 且创建超 M 天的 pending。"""
    sql = (
        "SELECT id, character_id, content, kind, due_start, due_end, status, created_at "
        "FROM prospective_intents "
        "WHERE status='pending' AND due_start IS NULL AND due_end IS NULL "
        "  AND julianday(created_at) < julianday('now', ?)"
    )
    params: list = [f"-{float(nodue_days)} days"]
    if char_id is not None:
        sql += " AND character_id = ?"
        params.append(char_id)
    sql += " ORDER BY character_id, created_at"
    return con.execute(sql, params).fetchall()


def find_similar_groups(con: sqlite3.Connection, threshold: float, char_id: int | None) -> list[list[sqlite3.Row]]:
    """③ 同角色 + 同 due 窗口 + 文本高度相近的 pending promise 分组（每组保留第一条）。"""
    sql = (
        "SELECT id, character_id, content, kind, due_start, due_end, status, created_at "
        "FROM prospective_intents "
        "WHERE status='pending' AND kind='promise' "
    )
    params: list = []
    if char_id is not None:
        sql += " AND character_id = ?"
        params.append(char_id)
    sql += " ORDER BY character_id, due_start, due_end, id"
    rows = con.execute(sql, params).fetchall()
    buckets: dict[tuple, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault((r["character_id"], r["due_start"], r["due_end"]), []).append(r)
    groups: list[list[sqlite3.Row]] = []
    for items in buckets.values():
        # 合并优先保留 side=self（AI 自述承诺，口径正确；避免留下被误标 user 的重复条），
        # 同 side 时保留最早（id 最小）。
        items = sorted(items, key=lambda r: (0 if classify_side(r["content"], r["kind"]) == "self" else 1, r["id"]))
        consumed: set[int] = set()
        for i, keep in enumerate(items):
            if keep["id"] in consumed:
                continue
            dup = [keep]
            for other in items[i + 1:]:
                if other["id"] in consumed:
                    continue
                if similar(keep["content"], other["content"], threshold):
                    dup.append(other)
                    consumed.add(other["id"])
            if len(dup) > 1:
                consumed.add(keep["id"])
                groups.append(dup)
    return groups


def _fmt(rows: list[sqlite3.Row]) -> None:
    for r in rows:
        side = classify_side(r["content"], r["kind"])
        print(f"    id={r['id']:<5} char={r['character_id']:<4} kind={r['kind']:<7} side={side:<4} "
              f"due_end={r['due_end']} created={r['created_at']}  {str(r['content'])[:46]}")


def _mark_stale(con: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    con.executemany(
        "UPDATE prospective_intents SET status='stale', updated_at=CURRENT_TIMESTAMP WHERE id=?",
        [(i,) for i in ids],
    )
    con.commit()
    return len(ids)


def main() -> int:
    ap = argparse.ArgumentParser(description="前瞻约定存量清理（dry-run 默认，--apply 才写；先备份）")
    ap.add_argument("--apply", action="store_true", help="实际写库（置 stale）；缺省=只统计(dry-run)")
    ap.add_argument("--db", default=None, help=f"数据库路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS,
                    help=f"时效超窗阈值小时数（默认 {DEFAULT_WINDOW_HOURS}，与 PROMISE_ACTIVE_WINDOW_HOURS 对齐）")
    ap.add_argument("--nodue-days", type=float, default=DEFAULT_NODUE_DAYS,
                    help=f"无 due 陈旧阈值天数（默认 {DEFAULT_NODUE_DAYS}）")
    ap.add_argument("--similar-threshold", type=float, default=DEFAULT_SIMILAR_THRESHOLD,
                    help=f"同义重复相似度阈值（默认 {DEFAULT_SIMILAR_THRESHOLD}）")
    ap.add_argument("--char-id", type=int, default=None, help="只处理指定角色")
    ap.add_argument("--kinds", default="promise,cue",
                    help="规则① 纳入的 kind（逗号分隔，默认 promise,cue；'promise' 则只清时间型承诺）")
    ap.add_argument("--no-dedupe", action="store_true", help="不处理同义重复（只做 1/2 两条状态标记）")
    args = ap.parse_args()

    kinds = tuple(k.strip() for k in (args.kinds or "").split(",") if k.strip()) or ("promise", "cue")
    db_path = args.db or DEFAULT_DB
    if not os.path.exists(db_path):
        print(f"DB 不存在: {db_path}")
        return 2

    con = connect(db_path)
    try:
        overdue = find_overdue(con, args.window_hours, args.char_id, kinds)
        nodue = find_stale_nodue(con, args.nodue_days, args.char_id)
        groups = [] if args.no_dedupe else find_similar_groups(con, args.similar_threshold, args.char_id)

        print("=== 前瞻约定存量清理（dry-run 默认）===")
        print(f"DB: {db_path}")
        print(f"阈值: window_hours={args.window_hours} nodue_days={args.nodue_days} "
              f"similar_threshold={args.similar_threshold} kinds={','.join(kinds)} char_id={args.char_id}")

        print(f"\n[1] 时效超窗 pending（due_end < now - {args.window_hours}h，kinds={','.join(kinds)}）→ stale: {len(overdue)} 条")
        _fmt(overdue)

        print(f"\n[2] 无 due 且创建超 {args.nodue_days} 天 pending → stale: {len(nodue)} 条")
        _fmt(nodue)

        merged_extra = [r for g in groups for r in g[1:]]
        print(f"\n[3] 同义重复 pending promise 分组: {len(groups)} 组，"
              f"拟合并（优先保留 side=self，其次最早）= {len(merged_extra)} 条 → stale")
        for g in groups:
            print(f"  -- char={g[0]['character_id']} due={g[0]['due_start']}~{g[0]['due_end']} keep=id{g[0]['id']}")
            _fmt(g)

        total = len({r["id"] for r in overdue} | {r["id"] for r in nodue} | {r["id"] for r in merged_extra})
        print(f"\n合计拟置 stale: {total} 条（不删行、只改 status，留痕可检索）")

        if not args.apply:
            print("[dry-run] 未写库；确认已备份后加 --apply 执行（建议先停服务器）。")
            return 0

        n1 = _mark_stale(con, [r["id"] for r in overdue])
        n2 = _mark_stale(con, [r["id"] for r in nodue])
        n3 = _mark_stale(con, [r["id"] for r in merged_extra])
        print(f"[apply] 已置 stale：超窗 {n1} + 无 due {n2} + 同义重复 {n3} = {n1 + n2 + n3} 条")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
