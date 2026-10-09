# -*- coding: utf-8 -*-
'''一次性运维脚本（A49 收尾）：把「已失效却仍挂着置顶」的记忆取消置顶。

现场（2026-10-09，用户报「印象重新生成失败」）：memories 里 5 条 status=stale 的行仍是
is_pinned=1 AND is_archived=0（id 5104／5557／6649／5972／7417）。代码查「已有置顶」带
status == active（current_facts_active_only 默认开）⇒ 看不见它们；DB 部分唯一索引
ux_memories_pinned_active 只认 is_pinned／is_archived ⇒ 重生成走 INSERT 时撞唯一约束。
代码侧已修（summary._release_bucket_pins 会在插入前按索引口径放开同桶），本脚本把存量清干净，
免得某个桶一直不重生成、那条 stale 置顶就一直占着位子。

口径：只动 is_pinned（1→0）；不动内容、不物理删行、不动 is_archived。
默认 dry-run；--apply 前用 sqlite3 在线 backup() 整库备份。
'''
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / 'backend' / 'data' / 'sqlite' / 'ai_companion.db'

SELECT_SQL = '''
select id, character_id, memory_type, coalesce(sub_type, ''), coalesce(status, 'NULL'),
       substr(coalesce(updated_at, created_at), 1, 19), substr(content, 1, 30)
  from memories
 where is_pinned = 1 and is_archived = 0
   and (status is null or status <> 'active')
 order by character_id, memory_type, sub_type
'''


def find_rows(conn):
    return conn.execute(SELECT_SQL).fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正写库（默认只演练）')
    ap.add_argument('--db', default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print('[停止] 找不到库：%s' % db)
        return 2
    conn = sqlite3.connect('file:%s?mode=ro' % db.as_posix(), uri=True)
    try:
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        if 'memories' not in tables:
            print('[SKIP] 这个库没有 memories 表（插件裸 schema 库）')
            return 0
        rows = find_rows(conn)
    finally:
        conn.close()

    for r in rows:
        print('  [待改] id=%s char=%s %s/%s status=%s updated=%s 正文=%s' % r)
    print('[%s] 仍挂置顶的非 active 行 = %d' % ('APPLY' if args.apply else 'DRY-RUN', len(rows)))
    if not args.apply:
        print('（只读演练；确认无误后加 --apply 执行，会先整库备份）')
        return 0
    if not rows:
        return 0

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = str(db) + '.bak-stalepin-' + stamp
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(bak)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print('[backup] 整库备份完成: %s' % bak)

    conn = sqlite3.connect(str(db))
    try:
        with conn:
            cur = conn.execute(
                "update memories set is_pinned = 0, updated_at = datetime('now') "
                "where is_pinned = 1 and is_archived = 0 "
                "and (status is null or status <> 'active')")
            n = cur.rowcount
        left = len(find_rows(conn))
    finally:
        conn.close()
    print('[OK] 取消置顶 %d 条；复跑口径剩余 %d 条' % (n, left))
    return 0


if __name__ == '__main__':
    sys.exit(main())