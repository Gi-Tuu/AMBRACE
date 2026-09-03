# -*- coding: utf-8 -*-
"""B1-② 记忆链条建链器（方案 §13.5 / 阶段 C2）：存量记忆幂等回填脚本。

只处理 ``chain_id IS NULL`` 的可建链记忆（event/insight），按时间升序分批挂链；
可重复运行（已挂链跳过，link_new_memory 幂等）；可选 ``--character`` 指定角色。

**安全红线**：本脚本**默认不触碰真实库**——必须显式传 ``--db-url`` 指向临时库/显式参数库；
未传 ``--db-url`` 时直接报错退出（不默认 DATABASE_URL 里的生产库）。

用法（示例，均在 backend 外、scripts 目录下运行）：
    python scripts/backfill_memory_chains.py --db-url "sqlite+aiosqlite:///D:/Codex-Projects/output/_tmp_backfill_b1b.db"
    python scripts/backfill_memory_chains.py --db-url <url> --character 12 --batch 500
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# 让 backend 包可导入（脚本位于 <repo>/scripts/，取其父目录/backend）
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "backend"))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="记忆链条存量回填（B1-② C2，幂等可重跑）")
    ap.add_argument(
        "--db-url", required=True,
        help="显式数据库 URL（sqlite+aiosqlite:///绝对路径 等）。必须传入，缺省直接报错，不回退生产库。",
    )
    ap.add_argument("--character", type=int, default=None, help="只回填指定角色（默认全部）")
    ap.add_argument("--batch", type=int, default=200, help="每批处理条数（默认 200）")
    return ap.parse_args()


async def run(character_id: int | None, batch: int) -> int:
    """回填主流程：仅处理 chain_id IS NULL 的可建链记忆；返回处理条数。"""
    # import 须在设置 DATABASE_URL 之后（config 读环境变量决定引擎指向）
    from sqlalchemy import select

    from app.agent.loop import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.memory.chain_builder import CHAINABLE_TYPES, link_new_memory
    from app.memory.embedding import text_embedding
    from app.models.memory import Memory

    # 回填脚本强制启用建链器（不依赖运行时 flag；仅本进程生效，不改配置/数据库）
    AGENT_FLAGS["memory_chain_builder"] = True

    processed = 0
    while True:
        async with async_session_factory() as db:
            q = (
                select(Memory)
                .where(Memory.chain_id.is_(None), Memory.memory_type.in_(tuple(CHAINABLE_TYPES)))
                .order_by(Memory.created_at.asc())
                .limit(batch)
            )
            if character_id:
                q = q.where(Memory.character_id == character_id)
            rows = (await db.execute(q)).scalars().all()
        if not rows:
            break
        for m in rows:  # 顺序挂链，保证父先于子（created_at 升序）
            try:
                emb = await text_embedding(m.content or "")
                await link_new_memory(m.id, emb)
            except Exception as _e:  # 单条失败不阻断整批（幂等可重跑）
                print(f"[backfill] skip id={m.id}: {_e}", file=sys.stderr)
            processed += 1
        print(f"[backfill] batch done, processed={processed}", flush=True)
    print(f"[backfill] all done, total={processed}", flush=True)
    return processed


def main() -> int:
    args = parse_args()
    # 先显式设置 DATABASE_URL，再导入任何 app 模块（config 在导入时读环境变量）
    os.environ["DATABASE_URL"] = args.db_url
    try:
        return asyncio.run(run(args.character, args.batch))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
