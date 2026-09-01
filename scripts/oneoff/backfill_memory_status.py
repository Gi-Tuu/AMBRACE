# -*- coding: utf-8 -*-
"""#70-C：存量向量 metadata.status 回填脚本。

上线顺序：先跑 Alembic 迁移（memories 加 status 列）→ 再跑本脚本回填向量 status →
**再**开 memory_supersede flag（本次不开）。否则旧向量缺 status key 会被状态过滤整批漏掉。

遍历 get_all_vectors_by_character（逐角色），对 metadata 缺 status 的向量补 "active" 并 update；
异常静默（回填失败不影响主链路，只是 flag 开时该角色关联向量的状态过滤不完整）。
"""
import asyncio
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "backend"))

from app.db.vector_store import get_or_create_collection, get_all_vectors_by_character  # noqa: E402


async def backfill_characters() -> int:
    collection = await get_or_create_collection()
    # 从 SQLite 取全部角色 id（源 of truth）；Chroma 只存向量，无角色枚举
    import sqlite3
    from app.config import settings
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    char_ids = []
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT DISTINCT character_id FROM memories").fetchall()
        conn.close()
        char_ids = [r[0] for r in rows if r[0] is not None]
    except Exception as e:
        print(f"[backfill] load character ids failed: {e}", file=sys.stderr)

    fixed = 0
    for cid in char_ids:
        try:
            vecs = await get_all_vectors_by_character(int(cid))
            if not vecs:
                continue
            got = collection.get(ids=[str(mid) for mid in vecs.keys()], include=["metadatas"])
            ids = got.get("ids") or []
            metas = got.get("metadatas") or []
            for i, meta in enumerate(metas):
                m = dict(meta or {})
                if m.get("status") is None:
                    m["status"] = "active"
                    collection.update(ids=[ids[i]], metadatas=[m])
                    fixed += 1
        except Exception as e:
            print(f"[backfill] char={cid} failed: {e}", file=sys.stderr)
    return fixed


if __name__ == "__main__":
    n = asyncio.run(backfill_characters())
    print(f"[backfill] done, backfilled {n} vectors missing status")
