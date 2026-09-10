"""AMBRACE 存量治理 · 过期计划/高S事件 修复脚本（2026-09-09，批次三交付物）。

修复内容（方案 §8.2，与 L1-L4 在线机制同口径）：
  1) A：已过期、仍 active 的未来计划 event → status='stale'（#70-C 语义：退出主动复习/
     无条件注入，检索保留、降权、可怀旧）、valid_to=tense 实际有效期、next_review_at=NULL；
  2) B2：event 且 (strength_days>10 或 review_count>3) → strength_days=MIN(S,10)、
     next_review_at=NULL（S 复位回事件初始档、停止主动复习轮转；不删任何内容）。

安全设计（交接 §0.1：脚本本身不执行写库，交 Codex 由用户拍板后运行）：
  - 默认 dry-run：只读连接（mode=ro），只 SELECT + 输出统计与计划变更清单，不写任何东西；
  - --apply 才开读写连接，且要求：
      a) 交互输入大写 YES 确认（--yes 跳过仅限自动化，仍需 --apply）；
      b) 先备份：受影响行（A ∪ B2）整行存入 memories_bak_20260909（幂等，重复插入跳过）；
      c) 单事务（BEGIN IMMEDIATE）内完成全部 UPDATE，异常整体回滚；
  - 附 restore 回滚 SQL 与向量侧同步指引（Chroma metadata 状态 + bm25 失效）。

用法：
  dry-run（默认，只读）：
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\plan_expiry_repair.py
  真正执行（需人工拍板）：
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\plan_expiry_repair.py --apply
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from plan_expiry_diagnose import (
    DEFAULT_DB,
    DEFAULT_OUT,
    EVENT_REVIEW_CAP,
    EVENT_S_CAP,
    HIGH_S_THRESHOLD,
    _now_naive,
    classify_a,
    connect_ro,
    load_active_events,
    load_b_repair_scope,
)


def load_b2_fix_scope(conn: sqlite3.Connection) -> list[dict]:
    """§8.2-2 修复口径（严格照方案）：event 且 review_count>3（被复习"养"高的才复位；
    仅 S>10 但 review_count<=3 的初始档不动，避免误伤新写入的事件）。"""
    sql = (
        "SELECT id,user_id,character_id,title,content,strength_days,review_count,"
        "created_at,next_review_at,status FROM memories "
        "WHERE memory_type='event' AND review_count>? "
        "ORDER BY review_count DESC"
    )
    return [dict(r) for r in conn.execute(sql, (EVENT_REVIEW_CAP,)).fetchall()]


BAK_TABLE = "memories_bak_20260909"


def _restore_sql() -> str:
    return f"""-- 回滚（恢复 bak 表行现场；执行后还需把向量状态标回 active，见脚本输出指引）
UPDATE memories SET
  status        = (SELECT b.status        FROM {BAK_TABLE} b WHERE b.id = memories.id),
  next_review_at= (SELECT b.next_review_at FROM {BAK_TABLE} b WHERE b.id = memories.id),
  valid_to      = (SELECT b.valid_to       FROM {BAK_TABLE} b WHERE b.id = memories.id),
  strength_days = (SELECT b.strength_days  FROM {BAK_TABLE} b WHERE b.id = memories.id)
WHERE id IN (SELECT id FROM {BAK_TABLE});"""


def _vector_sync_guide(a_ids: list[int], out_dir: str) -> str:
    ids_file = os.path.join(out_dir, "A_ids_for_vector_stale.txt")
    with open(ids_file, "w", encoding="utf-8") as f:
        f.write(",".join(str(i) for i in a_ids))
    return f"""-- 向量侧同步（置 stale 后必须做，否则 Chroma 检索仍按 active 召回）：
-- A 清单 id 已写到 {ids_file}
-- 在项目根目录用后端 venv 执行（复用 supersede._mark_vectors，失败静默可重跑）：
  backend\\.venv\\Scripts\\python.exe -c "import asyncio,sys;sys.path.insert(0,'backend');from app.memory.supersede import _mark_vectors,_bm25_invalidate_safe;ids=[int(x) for x in open(r'{ids_file}',encoding='utf-8').read().split(',') if x];asyncio.run(_mark_vectors(ids,{{i:'stale' for i in ids}}));[asyncio.run(_bm25_invalidate_safe(c)) for c in {{13,2,3,6,11,14,15,16}}]"
-- 回滚时把 'stale' 换回 'active' 再跑一遍即可。"""


def main():
    ap = argparse.ArgumentParser(description="存量修复（默认 dry-run 只读；--apply 才写库）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--apply", action="store_true", help="真正写库（默认关闭=仅 dry-run）")
    ap.add_argument("--yes", action="store_true", help="跳过交互确认（仍需 --apply）")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    now = _now_naive()

    # ── 1) 只读阶段：算出 A / B2 计划变更集（dry-run 与 apply 共用同一口径）──
    conn_ro = connect_ro(args.db)
    a = classify_a(load_active_events(conn_ro), now)
    b2 = load_b2_fix_scope(conn_ro)  # §8.2 修复口径：event 且 review_count>3
    b2_list = load_b_repair_scope(conn_ro)  # §8.1-B 清单口径（仅统计对照，不据此 UPDATE）
    conn_ro.close()
    a_ids = [r["id"] for r in a]
    b2_ids = [r["id"] for r in b2]
    union_ids = sorted(set(a_ids) | set(b2_ids))
    a_both = sum(1 for r in a if (r["review_count"] or 0) > EVENT_REVIEW_CAP)
    b2_s_reset = sum(1 for r in b2 if (r["strength_days"] or 0) > EVENT_S_CAP)
    a_high_s = sum(1 for r in a if (r["strength_days"] or 0) >= HIGH_S_THRESHOLD)

    print(f"[dry-run] 数据库: {args.db}")
    print(f"[dry-run] 统计时间: {now:%Y-%m-%d %H:%M} UTC")
    print(f"[dry-run] A  过期计划仍 active（将置 stale + valid_to + next_review_at=NULL）: {len(a)} 行"
          f"（其中 S>=55: {a_high_s}，review>{EVENT_REVIEW_CAP}: {a_both}）")
    print(f"[dry-run] B2 修复口径 event review>{EVENT_REVIEW_CAP}（S 复位<=10 + next_review_at=NULL）: {len(b2)} 行"
          f"（其中 S>{EVENT_S_CAP:g} 需复位: {b2_s_reset}）")
    print(f"[dry-run] （对照）§8.1-B 清单口径 event S>{EVENT_S_CAP:g} 或 review>{EVENT_REVIEW_CAP}: {len(b2_list)} 行"
          f"——仅诊断清单，修复不按此口径（避免误伤 review<=3 的初始档）")
    print(f"[dry-run] A ∪ B2 备份影响面（{BAK_TABLE}）: {len(union_ids)} 行")
    sample = a[:10]
    if sample:
        print("[dry-run] A 样例（前 10）:")
        for r in sample:
            print(f"  id={r['id']} char={r['character_id']} S={r['strength_days']} review={r['review_count']} "
                  f"created={str(r['created_at'])[:19]} → stale(valid_to={str(r['plan_valid_until'])[:19]}) "
                  f"| {(r['content'] or '')[:30]}")
    with open(os.path.join(args.out, "repair_dryrun_report.txt"), "w", encoding="utf-8") as f:
        f.write(f"AMBRACE 存量修复 dry-run · {now:%Y-%m-%d %H:%M} UTC\n"
                f"db={args.db}\nA={len(a)} B2={len(b2)} union={len(union_ids)} "
                f"a_high_s={a_high_s} a_both={a_both}\nA ids: {','.join(map(str, a_ids))}\n"
                f"B2 ids: {','.join(map(str, b2_ids))}\n")
    print(f"[dry-run] 完整 id 清单已写: {os.path.join(args.out, 'repair_dryrun_report.txt')}")

    if not args.apply:
        print()
        print("[note] 当前为 dry-run，未写库。确认影响面后由用户拍板，交 Codex 以 --apply 执行。")
        print("[note] 执行前请确保已做整库备份（scripts\\backup.py）；脚本自身仅备份受影响行到 "
              f"{BAK_TABLE}。")
        return

    # ── 2) 写库阶段（--apply）：确认 → 备份 → 单事务 UPDATE ──
    if not args.yes:
        ans = input(f"即将修改 {len(a)} 行为 stale、{len(b2)} 行复位 S，备份 {len(union_ids)} 行到 {BAK_TABLE}。"
                    f"确认请输入大写 YES：").strip()
        if ans != "YES":
            print("[abort] 未确认，已退出（未写任何数据）。")
            return

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 0) 备份受影响行（幂等：bak 里已有的 id 不重复插）
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BAK_TABLE} AS SELECT * FROM memories WHERE 0")
        conn.execute(
            f"INSERT INTO {BAK_TABLE} SELECT m.* FROM memories m "
            f"WHERE m.id IN ({','.join('?' * len(union_ids))}) "
            f"AND m.id NOT IN (SELECT id FROM {BAK_TABLE})",
            union_ids,
        )
        bak_n = conn.execute(f"SELECT COUNT(*) FROM {BAK_TABLE}").fetchone()[0]

        # 1) A：过期计划置 stale + valid_to 实际有效期 + 停止复习轮转
        for r in a:
            conn.execute(
                "UPDATE memories SET status='stale', next_review_at=NULL, valid_to=? WHERE id=?",
                (r["plan_valid_until"].strftime("%Y-%m-%d %H:%M:%S"), r["id"]),
            )
        # 2) B2：被复习抬高的 event S 复位（≤10）+ 停止复习轮转（不删内容；§8.2 口径 review>3）
        conn.execute(
            f"UPDATE memories SET strength_days=MIN(strength_days, {EVENT_S_CAP}), next_review_at=NULL "
            f"WHERE memory_type='event' AND review_count>{EVENT_REVIEW_CAP}"
        )
        b2_updated = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[abort] 事务失败已整体回滚，未产生任何变更: {e}")
        raise
    finally:
        conn.close()

    print()
    print(f"[done] 已提交：A 置 stale {len(a)} 行；B2 S 复位 {b2_updated} 行；备份表 {BAK_TABLE} 现有 {bak_n} 行。")
    print()
    print("== 后续必做 ==")
    print(_vector_sync_guide(a_ids, args.out))
    print()
    print("== 回滚（restore）==")
    print(_restore_sql())


if __name__ == "__main__":
    main()
