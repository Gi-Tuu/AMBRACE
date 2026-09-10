# -*- coding: utf-8 -*-
"""清理历史 FK 孤儿行 + ai_moments.character_id=0 哨兵改 NULL（2026-09-09 v3.4.6 第三批数据治理）。

用法：
  .venv\\Scripts\\python.exe scripts/cleanup_orphans.py --dry-run   # 只统计
  .venv\\Scripts\\python.exe scripts/cleanup_orphans.py --apply     # 实际执行（先备份！）

策略：
- 以 PRAGMA foreign_key_check 枚举的违约为全集（SQLite 会列出所有 schema 声明的 FK 违约），
  对每个 (child_table, rowid) 执行 DELETE FROM child WHERE rowid=?（普通孤儿行删除）。
- ai_moments.character_id=0 属「用户动态哨兵」，语义不是孤儿，置 NULL 保留（列已可空）。
- --apply 执行后复核 foreign_key_check 必须为空；非空则报错提示（不自动回滚，人工按备份恢复）。
- 运行期间应停止服务器，避免并发写。
"""
import argparse
import os
import sqlite3
import sys

DB = os.environ.get("AMB_DB", os.path.join(os.path.dirname(__file__), "..", "backend", "data", "sqlite", "ai_companion.db"))


def connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    return con


def collect_orphans(con: sqlite3.Connection) -> dict[str, list[int]]:
    """foreign_key_check 全集：{child_table: [rowid, ...]}"""
    rows = con.execute("PRAGMA foreign_key_check").fetchall()
    out: dict[str, list[int]] = {}
    for r in rows:  # (child_table, rowid, parent_table, ...)
        out.setdefault(r[0], []).append(r[1])
    for t in out:
        out[t] = sorted(set(out[t]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 FK 孤儿行（先备份再 --apply）")
    ap.add_argument("--apply", action="store_true", help="实际删除；缺省=只统计(dry-run)")
    ap.add_argument("--db", default=None, help="数据库路径（默认 backend/data/sqlite/ai_companion.db）")
    args = ap.parse_args()
    db_path = args.db or DB
    if not os.path.exists(db_path):
        print(f"DB 不存在: {db_path}")
        return 2
    con = connect(db_path)
    try:
        orphans = collect_orphans(con)
        total = sum(len(v) for v in orphans.values())
        print("=== FK 孤儿统计 ===")
        for t in sorted(orphans):
            print(f"  {t}: {len(orphans[t])}")
        print(f"  TOTAL orphans: {total}")

        sentinel = con.execute("SELECT COUNT(*) FROM ai_moments WHERE character_id=0").fetchone()[0]
        print(f"ai_moments.character_id=0 sentinel: {sentinel}（将置 NULL 保留）")
        # configs 表的 user_id=0/-1 是「服务器级全局配置」哨兵行，不是孤儿：不删行，
        # 由迁移阶段去掉这几张表的 user_id→users FK（配置归属本就是自由整数语义）。
        CONFIG_TABLES = {"api_configs", "vlm_configs", "speech_configs",
                         "multimodal_configs", "image_gen_configs"}
        cfg_kept = sum(len(orphans.get(c, [])) for c in CONFIG_TABLES)
        if cfg_kept:
            print(f"configs 哨兵保留 {cfg_kept} 行（迁移去 FK，不删除）")

        if not args.apply:
            print("[dry-run] 未写库；确认备份后加 --apply 执行。")
            return 0

        # 迭代清理：删父孤儿会暴露二级孤儿（如孤儿 chat_session 下的 chat_messages），
        # 循环 collect→delete 直到无新增（从叶子收敛到根）。
        deleted = 0
        rounds = 0
        mom_nullable = any(r[1] == "character_id" and r[3] == 0 for r in con.execute("PRAGMA table_info(ai_moments)"))
        upd = 0
        while True:
            orphans = collect_orphans(con)
            todel = {k: v for k, v in orphans.items() if k not in CONFIG_TABLES and k != "ai_moments"}
            n = sum(len(v) for v in todel.values())
            if n == 0:
                break
            rounds += 1
            for tt in sorted(todel):
                for rowid in todel[tt]:
                    cur = con.execute(f'DELETE FROM "{tt}" WHERE rowid=?', (rowid,))
                    deleted += cur.rowcount or 0
            con.commit()
            if rounds > 10:
                print("[warn] 清理轮次超限，仍有孤儿：", {k: len(v) for k, v in todel.items()})
                break
        # ai_moments 哨兵：列可空时置 NULL；仍 NOT NULL 时跳过（迁移重建后再置 NULL）
        if mom_nullable:
            upd = con.execute("UPDATE ai_moments SET character_id=NULL WHERE character_id=0 OR character_id NOT IN (SELECT id FROM ai_characters)").rowcount or 0
            con.commit()
        else:
            print("[skip] ai_moments.character_id 实库仍 NOT NULL——迁移重建可空后再置 NULL")
        print(f"已删除普通孤儿行(迭代 {rounds} 轮): {deleted}")
        print(f"ai_moments.character_id 置 NULL: {upd}")

        left = collect_orphans(con)
        # 允许余留：configs（迁移去 FK）+ ai_moments（迁移可空后置 NULL）
        left_real = {k: v for k, v in left.items() if k not in CONFIG_TABLES and k != "ai_moments"}
        left_total = sum(len(v) for v in left_real.values())
        cfg_left = sum(len(left.get(c, [])) for c in CONFIG_TABLES)
        mom_left = len(left.get("ai_moments", []))
        if left_total:
            print(f"[FAIL] 复核仍有余留孤儿 {left_total} 条: {left_real}")
            print("请从备份恢复或人工处理；不要继续后续步骤。")
            return 1
        print(f"[OK] 非 configs/ai_moments 孤儿已清零（0）；configs 哨兵余 {cfg_left} 行、ai_moments 余 {mom_left} 行待迁移处理。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
