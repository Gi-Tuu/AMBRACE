# -*- coding: utf-8 -*-
"""A1 双跑校验（只读，2026-09-19）：证明「开账号过滤后，本账号自己的记忆一条不丢，只丢他人的」。

做法：对每个角色（可 --character N 指定）取该角色若干条真实记忆的向量作 query，分别用
    旧 where（只按 character_id）
    新 where（character_id + {"user_id": {"$in": scope}}）
检索 top-N，比较结果集 same / only_old / only_new；对 only_old 逐条标注其 memories.user_id。

判定：only_old 全部属于「非 scope 账号」或「孤儿」= PASS；出现任何「属于 scope 账号」的
丢失 = FAIL（说明向量 metadata.user_id 有漏——开 flag 前必须先跑
scripts/memory/backfill_vector_user_id.py --apply 回填）。

只读：不写库、不改向量。scope 默认取 ai_characters.user_id（角色 owner），可 --user 覆盖。
--assume-backfilled：模拟「回填已完成」——用 memories.user_id 代替向量 metadata 参与新 where
过滤，用于验证 PASS 判定通路（默认关，按向量真实 metadata 判定）。

用法（项目根目录下）：
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\vector_scope_dualrun.py
    backend\\.venv\\Scripts\\python.exe scripts\\memory\\vector_scope_dualrun.py --character 6 --samples 3 --topk 10
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.db.vector_store import COLLECTION_NAME, get_client  # noqa: E402


def _ro_conn() -> sqlite3.Connection:
    path = settings.database_url.replace("sqlite+aiosqlite:///", "").replace("\\", "/")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _load_sql() -> tuple[dict[int, tuple[int | None, int | None]], dict[int, int | None]]:
    """只读加载 memories(id -> (character_id, user_id)) 与 ai_characters(id -> user_id)。"""
    conn = _ro_conn()
    try:
        mem = {int(i): (int(c) if c is not None else None, int(u) if u else None)
               for i, c, u in conn.execute("SELECT id, character_id, user_id FROM memories")}
        owner = {int(i): (int(u) if u else None)
                 for i, u in conn.execute("SELECT id, user_id FROM ai_characters")}
    finally:
        conn.close()
    return mem, owner


def _own_samples(char_id: int, user_id: int, samples: int) -> list[int]:
    """取该角色、该账号的若干条真实记忆 id（按 importance 降序；只取非归档常规记忆）。"""
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT id FROM memories WHERE character_id=? AND user_id=?"
            " AND is_archived=0 AND memory_type!='working_state'"
            " ORDER BY importance DESC LIMIT ?",
            (char_id, user_id, samples),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def _query(col, embedding, topk: int, where: dict) -> tuple[list[int], dict[int, dict]]:
    """只读 query；返回 (ids, id->metadata)。异常返回空（只读脚本不抛）。"""
    try:
        res = col.query(query_embeddings=[embedding], n_results=topk,
                        where=where, include=["metadatas"])
    except Exception:
        return [], {}
    ids = [int(x) for x in (res.get("ids") or [[]])[0]]
    metas = (res.get("metadatas") or [[]])[0]
    return ids, {i: dict(m or {}) for i, m in zip(ids, metas)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="A1 向量账号 scope 双跑校验（只读；默认按向量真实 metadata 判定）")
    parser.add_argument("--character", type=int, action="append", default=None,
                        help="只校验指定角色 id（可重复；默认全部角色）")
    parser.add_argument("--user", type=int, default=None,
                        help="覆盖 scope 账号（默认取 ai_characters.user_id）")
    parser.add_argument("--samples", type=int, default=3, help="每角色取样查询数（默认 3）")
    parser.add_argument("--topk", type=int, default=10, help="每次检索 top-N（默认 10）")
    parser.add_argument("--max-chars", type=int, default=0, help="最多校验几个角色（0=全部）")
    parser.add_argument("--assume-backfilled", action="store_true",
                        help="模拟回填完成：用 memories.user_id 代替向量 metadata 过滤")
    args = parser.parse_args(argv)

    mem, owner = _load_sql()
    try:
        col = get_client().get_collection(COLLECTION_NAME)
    except Exception as e:
        print(f"[error] 打开集合失败（{settings.chroma_persist_dir}）：{e}", file=sys.stderr)
        return 1

    if args.character:
        chars = list(dict.fromkeys(args.character))
    else:
        chars = sorted({c for c, _u in mem.values() if c is not None})
    if args.max_chars and len(chars) > args.max_chars:
        chars = chars[:args.max_chars]

    tot = {"same": 0, "only_old": 0, "only_new": 0, "in_scope_lost": 0,
           "other": 0, "orphan": 0, "meta_missing": 0, "queries": 0}
    print(f"[dualrun] persist_dir={settings.chroma_persist_dir}"
          f" mode={'assume-backfilled' if args.assume_backfilled else 'real-metadata'}")
    print("char | scope | samples | same | only_old | only_new | in_scope_lost | verdict")

    for char_id in chars:
        scope = [args.user] if args.user is not None else (
            [owner[char_id]] if owner.get(char_id) is not None else [])
        if not scope:
            print(f"{char_id} | - | 0 | - | - | - | - | SKIP(no owner)")
            continue
        scope_set = set(scope)
        sample_ids = _own_samples(char_id, scope[0], args.samples)
        if not sample_ids:
            print(f"{char_id} | {scope} | 0 | - | - | - | - | SKIP(no own memory)")
            continue
        try:
            got = col.get(ids=[str(i) for i in sample_ids], include=["embeddings"])
        except Exception:
            got = {}
        gids = got.get("ids") or []
        gembs = got.get("embeddings")
        if gembs is None:
            gembs = []
        old_where = {"character_id": char_id}
        new_where = {"$and": [{"character_id": char_id}, {"user_id": {"$in": scope}}]}

        c_same = c_old = c_new = c_lost = 0
        used = 0
        for i, doc_id in enumerate(gids):
            if i >= len(gembs) or gembs[i] is None:
                continue
            used += 1
            old_ids, old_metas = _query(col, gembs[i], args.topk, old_where)
            if args.assume_backfilled:
                # 反事实：假设向量 metadata.user_id 已按回填口径写好
                new_ids = [x for x in old_ids
                           if x in mem and mem[x][1] is not None and mem[x][1] in scope_set]
            else:
                new_ids, _ = _query(col, gembs[i], args.topk, new_where)
            o, n = set(old_ids), set(new_ids)
            c_same += len(o & n)
            c_new += len(n - o)
            for x in sorted(o - n):
                c_old += 1
                m = mem.get(x)
                if m is None:
                    tot["orphan"] += 1
                elif m[1] is not None and m[1] in scope_set:
                    c_lost += 1
                    tot["in_scope_lost"] += 1
                else:
                    tot["other"] += 1
                meta = old_metas.get(x) or {}
                if meta.get("user_id") is None:
                    tot["meta_missing"] += 1
        if not used:
            print(f"{char_id} | {scope} | 0 | - | - | - | - | SKIP(no embedding)")
            continue
        verdict = "PASS" if c_lost == 0 else "FAIL"
        tot["same"] += c_same
        tot["only_old"] += c_old
        tot["only_new"] += c_new
        tot["queries"] += used
        print(f"{char_id} | {scope} | {used} | {c_same} | {c_old} | {c_new} | {c_lost} | {verdict}")

    verdict = "PASS" if tot["in_scope_lost"] == 0 else "FAIL"
    print("-" * 72)
    print(f"TOTAL queries={tot['queries']} same={tot['same']} only_old={tot['only_old']} "
          f"only_new={tot['only_new']}")
    print(f"only_old 归因：非 scope 账号={tot['other']} 孤儿={tot['orphan']} "
          f"属于 scope 账号(丢失)={tot['in_scope_lost']}")
    if not args.assume_backfilled:
        print(f"only_old 中向量 metadata 缺 user_id 的条数：{tot['meta_missing']}"
              "（>0 表示回填未执行/有漏）")
    print(f"VERDICT: {verdict}")
    if verdict == "FAIL" and not args.assume_backfilled and tot["meta_missing"] > 0:
        print("[hint] 先跑 backfill_vector_user_id.py --apply（备份后），再复跑本脚本应转 PASS。")
    return 0 if verdict == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
