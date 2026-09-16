# -*- coding: utf-8 -*-
"""世界事实（world_facts）存量语义重复合并脚本（2026-09-15）。

背景：策展层（author=system）反复写入同一语义的事实（如「腰」相关 4 条、伴侣身份 6 条），
导致同一角色下活跃事实越攒越多、互相打架。本脚本把同 (character_id, predicate) 分组内
两两相似度 >= 阈值（默认 0.85）的事实视为一族，**保留 asserted_at 最新的一条**，其余置
status='superseded'（留痕不删，与「回忆不等于删除」口径一致）。

用法：
  .venv\\Scripts\\python.exe scripts/dedupe_world_facts.py            # dry-run（默认，只打印计划）
  .venv\\Scripts\\python.exe scripts/dedupe_world_facts.py --apply    # 实际写库（先备份！）

安全默认：不加 --apply 绝不写库；--apply 前请先备份 backend/data/sqlite/ai_companion.db。
运行期间建议停止服务器，避免并发写。
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
DEFAULT_SIMILAR_THRESHOLD = 0.85


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.row_factory = sqlite3.Row
    return con


def normalize(text: str) -> str:
    """归一化文本：去空白/标点、小写（与 app.scheduling.prospective_intent 同口径）。"""
    keep = []
    for ch in (text or "").lower():
        if ch.isspace() or ch in "，。！？、,.!?;；:：\"'“”‘’()（）[]【】~～-—":
            continue
        keep.append(ch)
    return "".join(keep)


def similarity(a: str, b: str) -> float:
    """两段事实文本的相似度 0-1（归一化后 SequenceMatcher 比值，含全等与包含短路）。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb))
    return SequenceMatcher(None, na, nb).ratio()


def _uf_find(parent: list[int], x: int) -> int:
    """并查集查找（带路径压缩）。"""
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _snippet(text: str, n: int = 46) -> str:
    t = " ".join((text or "").split())
    return t[:n] + ("…" if len(t) > n else "")


def find_duplicate_groups(
    con: sqlite3.Connection, threshold: float, char_id: int | None,
) -> list[dict]:
    """同 (character_id, predicate) 分组 → 组内两两相似度 >= threshold 聚成一族。

    返回 [{character_id, predicate, keep: Row, drop: [Row, ...]}, ...]；
    keep = 族内 asserted_at 最新（并列取 id 最大）的一条；drop = 其余成员（按 id 升序）。
    聚合用单链接（并查集式传递）：A~B 且 B~C 时 A/B/C 同族。
    """
    sql = (
        "SELECT id, character_id, user_id, predicate, object_value, author, status, "
        "       asserted_at, created_at "
        "FROM world_facts WHERE status='active'"
    )
    params: list = []
    if char_id is not None:
        sql += " AND character_id = ?"
        params.append(char_id)
    sql += " ORDER BY character_id, predicate, asserted_at, id"
    rows = con.execute(sql, params).fetchall()

    buckets: dict[tuple, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault((r["character_id"], r["predicate"]), []).append(r)

    groups: list[dict] = []
    for (cid, predicate), items in sorted(buckets.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        n = len(items)
        parent = list(range(n))

        for i in range(n):
            for j in range(i + 1, n):
                if similarity(items[i]["object_value"], items[j]["object_value"]) >= threshold:
                    ri, rj = _uf_find(parent, i), _uf_find(parent, j)
                    if ri != rj:
                        parent[ri] = rj

        families: dict[int, list[sqlite3.Row]] = {}
        for i in range(n):
            families.setdefault(_uf_find(parent, i), []).append(items[i])
        for members in families.values():
            if len(members) < 2:
                continue
            ordered = sorted(members, key=lambda r: (str(r["asserted_at"] or ""), r["id"]))
            keep = ordered[-1]                      # asserted_at 最新（并列取 id 最大）
            drop = sorted((m for m in ordered[:-1]), key=lambda r: r["id"])
            groups.append({
                "character_id": cid, "predicate": predicate,
                "keep": keep, "drop": drop,
            })
    return groups


def _fmt_row(r: sqlite3.Row) -> str:
    return (f"    id={r['id']:<5} author={str(r['author']):<7} "
            f"asserted_at={str(r['asserted_at'])[:19]:<19} {_snippet(r['object_value'])}")


def _mark_superseded(con: sqlite3.Connection, drop_ids: list[int], keep_by_drop: dict[int, int]) -> int:
    """把 drop_ids 置 superseded，并回写 superseded_by=保留行 id（留痕不删）。"""
    if not drop_ids:
        return 0
    now_sql = "datetime('now')"
    con.executemany(
        f"UPDATE world_facts SET status='superseded', superseded_by=?, superseded_at={now_sql}, "
        f"updated_at={now_sql} WHERE id=?",
        [(keep_by_drop[i], i) for i in drop_ids],
    )
    con.commit()
    return len(drop_ids)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="世界事实存量语义重复合并（dry-run 默认，--apply 才写；先备份）")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（重复项置 superseded）；缺省=只打印计划(dry-run)")
    ap.add_argument("--db", default=None, help=f"数据库路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--similar-threshold", type=float, default=DEFAULT_SIMILAR_THRESHOLD,
                    help=f"相似度阈值（默认 {DEFAULT_SIMILAR_THRESHOLD}）")
    ap.add_argument("--char-id", type=int, default=None, help="只处理指定角色")
    args = ap.parse_args()

    db_path = args.db or DEFAULT_DB
    if not os.path.exists(db_path):
        print(f"DB 不存在: {db_path}")
        return 2
    con = connect(db_path)
    try:
        groups = find_duplicate_groups(con, args.similar_threshold, args.char_id)
        total_drop = sum(len(g["drop"]) for g in groups)
        print(f"DB: {db_path}")
        print(f"阈值: {args.similar_threshold}    模式: {'APPLY（写库）' if args.apply else 'DRY-RUN（只打印）'}")
        print(f"重复族: {len(groups)} 组    计划停用: {total_drop} 条    保留: {len(groups)} 条")
        if not groups:
            print("\n未发现语义重复的事实，无需处理。")
            return 0
        print("")
        for gi, g in enumerate(groups, 1):
            print(f"[{gi}] character_id={g['character_id']}  predicate={g['predicate']}  "
                  f"族内 {len(g['drop']) + 1} 条 → 保留 1 / 停用 {len(g['drop'])}")
            print(f"  KEEP {_fmt_row(g['keep']).strip()}")
            for d in g["drop"]:
                print(f"  DROP {_fmt_row(d).strip()}")
        if not args.apply:
            print("\n[dry-run] 未写库。确认无误后加 --apply 执行。")
            return 0
        drop_ids = [d["id"] for g in groups for d in g["drop"]]
        keep_by_drop = {d["id"]: g["keep"]["id"] for g in groups for d in g["drop"]}
        n = _mark_superseded(con, drop_ids, keep_by_drop)
        print(f"\n[apply] 已置 superseded: {n} 条（留痕不删）")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
