# -*- coding: utf-8 -*-
"""A31/A.3.3 一次性回填：把同组多余的置顶摘要收口（只降级 is_pinned，不物理删）。

背景（2026-10-07 调查报告）：
  历史上同一 (角色, 类型[, sub_type]) 下可能混入多条 is_pinned=1 的摘要。A31 已修「以后只留一条」，
  但存量需要一次性收口（全库实测 6 组）。分桶口径与 A31 的修复保持一致：
    - memory_type == "user_info"：普通印象 summary 与 identity 身份画像分开成两个桶；
    - 其余类型：按 (memory_type, sub_type or "") 分桶。
  每个桶保留「时间最新、同时间取 id 最大」那条，其余 is_pinned=0。

用法（默认只读；写库前自动整库备份）：
    python backend/scripts/repair_pinned_summaries.py            # dry-run，打印将降级的 id 清单
    python backend/scripts/repair_pinned_summaries.py --apply     # 真写（先备份）

路径口径：优先环境变量 AMBRACE_DB，缺省用「仓库根/backend/data/sqlite/ai_companion.db」，不写死作者机器路径。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("AMBRACE_DB", str(ROOT / "backend" / "data" / "sqlite" / "ai_companion.db")))


def bucket(memory_type: str, sub_type: str | None):
    if memory_type == "user_info":
        return ("user_info", sub_type if sub_type == "identity" else "summary")
    return (memory_type, sub_type or "")


def main() -> int:
    apply = "--apply" in sys.argv
    if not DB.exists():
        print(f"[ERROR] 库不存在: {DB}")
        return 2
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    rows = con.execute(
        """
        SELECT id, character_id, memory_type, sub_type, COALESCE(updated_at, created_at) AS t
        FROM memories
        WHERE is_pinned = 1 AND is_archived = 0
        ORDER BY character_id, memory_type, COALESCE(sub_type, ""), t DESC, id DESC
        """
    ).fetchall()
    con.close()

    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r[1],) + bucket(r[2], r[3])
        groups.setdefault(key, []).append(r)

    demote = []
    for key, g in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(g) > 1:
            print(f"[组] char={key[0]} type={key[1]} sub={key[2]} 置顶 {len(g)} 条 → 保留 id={g[0][0]}，降级 {[x[0] for x in g[1:]]}")
        for extra in g[1:]:
            demote.append(extra[0])

    print(f"[{'APPLY' if apply else 'DRY-RUN'}] 置顶 {len(rows)} 条 / 分 {len(groups)} 组 / 将降级 {len(demote)} 条: {demote}")
    if not demote:
        return 0
    if not apply:
        print("（只读演练；确认无误后加 --apply 执行，会先整库备份）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(DB.name + f".bak-pinned-{stamp}")
    # 用 sqlite3 在线 backup API，而不是文件拷贝：库处于 WAL 活跃状态时，
    # 直接拷 .db 可能漏掉还在 -wal 里的最新事务；backup API 取一致性快照。
    _src = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    _dst = sqlite3.connect(str(bak))
    _src.backup(_dst)
    _dst.close()
    _src.close()
    con = sqlite3.connect(str(DB))
    with con:
        con.executemany("UPDATE memories SET is_pinned = 0 WHERE id = ?", [(i,) for i in demote])
    con.close()
    print(f"[OK] 已降级 {len(demote)} 条；备份: {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())