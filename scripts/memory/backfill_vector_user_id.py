# -*- coding: utf-8 -*-
"""A1 向量库「账号归属」：存量向量 metadata.user_id 回填脚本（2026-09-19）。

只读 SQLite（memories.id -> memories.user_id），对每条向量补/修 metadata 的 user_id：
    collection.update(ids=[...], metadatas=[合并后的 meta])
不动 embeddings / documents；只允许动 settings.chroma_persist_dir。

用法（项目根目录下）：
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\backfill_vector_user_id.py [--batch 200] [--limit N]
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\backfill_vector_user_id.py --apply

- --batch N：update 批次大小（默认 200）。
- --limit N：只处理前 N 条向量（小批试跑）。
- 默认 dry-run（只统计不写库）；必须显式 --apply 才真正写。
- 幂等：--apply 跑过一次后，第二次运行应为「本次补写 0 / 不一致修正 0」。
- 孤儿向量（memories 表查不到）跳过并单独计数。

上线顺序：先备份生产向量库 -> 本脚本 --apply 回填 -> 再开 vector_user_scope flag。
"""
import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.db.vector_store import get_or_create_collection  # noqa: E402


def _db_path() -> str:
    """从 settings.database_url 解析 SQLite 文件路径（只读打开用）。"""
    return settings.database_url.replace("sqlite+aiosqlite:///", "")


def _load_memory_users() -> dict[int, int | None]:
    """只读加载 memories.id -> user_id 映射（mode=ro，绝不写库）。"""
    path = _db_path().replace("\\", "/")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {int(i): (int(u) if u else None)
                for i, u in conn.execute("SELECT id, user_id FROM memories")}
    finally:
        conn.close()


async def run(batch: int, limit: int | None, apply: bool) -> int:
    users = _load_memory_users()
    collection = await get_or_create_collection()
    got = collection.get(include=["metadatas"])
    ids = list(got.get("ids") or [])
    metas = list(got.get("metadatas") or [])
    if limit is not None:
        ids, metas = ids[:limit], metas[:limit]

    stats = {"total": len(ids), "has_user_id": 0, "fill": 0, "fix": 0,
             "orphan": 0, "unresolved": 0}
    pending: list[tuple[str, dict]] = []
    for doc_id, meta in zip(ids, metas):
        m = dict(meta or {})
        try:
            mem_id = int(doc_id)
        except Exception:
            stats["orphan"] += 1
            continue
        if mem_id not in users:
            stats["orphan"] += 1   # 孤儿向量：SQL 查不到，跳过
            continue
        uid = users[mem_id]
        if uid is None:
            stats["unresolved"] += 1   # 有行但 user_id 为空：不编造，跳过
            continue
        cur = m.get("user_id")
        if cur is None:
            stats["fill"] += 1
        else:
            stats["has_user_id"] += 1
            try:
                same = int(cur) == uid
            except Exception:
                same = False
            if same:
                continue
            stats["fix"] += 1   # 已有 user_id 但与 memories.user_id 不一致 -> 修正
        m["user_id"] = uid
        pending.append((doc_id, m))

    applied = 0
    failed = 0
    if apply and pending:
        for start in range(0, len(pending), batch):
            chunk = pending[start:start + batch]
            try:
                collection.update(
                    ids=[c[0] for c in chunk],
                    metadatas=[c[1] for c in chunk],
                )
                applied += len(chunk)
            except Exception as e:
                failed += len(chunk)
                print(f"[error] update batch @{start} failed: {e}", file=sys.stderr)

    print(f"[{'apply' if apply else 'dry-run'}] chroma_persist_dir={settings.chroma_persist_dir}")
    print(f"  扫描向量总数   : {stats['total']}")
    print(f"  已有 user_id   : {stats['has_user_id']}")
    print(f"  本次补写       : {stats['fill']}")
    print(f"  不一致修正     : {stats['fix']}")
    print(f"  孤儿跳过       : {stats['orphan']}")
    print(f"  无归属跳过     : {stats['unresolved']}")
    print(f"  失败数         : {failed}")
    if apply:
        print(f"  实际写入       : {applied}")
    else:
        print("  [dry-run] 未写库；显式 --apply 才执行 update（本批只允许 --dry-run）")
    return failed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="A1 存量向量 metadata.user_id 回填（默认 dry-run，必须显式 --apply 才写库）")
    parser.add_argument("--batch", type=int, default=200, help="update 批次大小（默认 200）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条向量（小批试跑）")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只统计不写）")
    args = parser.parse_args(argv)
    if args.batch < 1:
        print("[error] --batch 必须 >= 1", file=sys.stderr)
        return 1
    if args.limit is not None and args.limit < 0:
        print("[error] --limit 必须 >= 0", file=sys.stderr)
        return 1
    try:
        failed = asyncio.run(run(args.batch, args.limit, args.apply))
    except Exception as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
