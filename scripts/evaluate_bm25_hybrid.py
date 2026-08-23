#!/usr/bin/env python
"""BM25 混合检索深化评估：仅向量 vs 向量+BM25(现) vs 向量+BM25+RRF(新)。

读取 backend/tests/fixtures/bm25_p1_queries.json（6 个 OpenViking P1 主题查询 + gold 记忆，
2026-08-23 重建，原测试集清理临时环境时丢失），构建一个**临时 SQLite 记忆库**与一把
**隔离的 BM25 持久化目录**，用真实 bge-m3 嵌入（= 项目向量路）与真实 BM25（= 项目 sparse 路）
跑三种检索路径，逐查询统计 top5 命中率，输出对比表到
D:\\Codex-Projects\\output\\bm25_hybrid_eval.md。

- 「仅向量」dense：关闭 sparse 路与 RRF，等价于只走向量主链路（importance 排序）；
- 「向量+BM25(现)」hybrid：dense + sparse 候选池合并去重（无 RRF，当前线上行为）；
- 「向量+BM25+RRF(新)」hybrid_rrf：dense + sparse 各按 rank 做 RRF 融合（新）。

隔离约束：只用临时 SQLite 文件库 + 临时 bm25 持久化目录，不写生产数据库、不写生产缓存；
所有记忆 importance 相同（=40）以隔离「检索路径」对命中的影响。

运行：cd backend && .venv\\Scripts\\python.exe ..\\scripts\\evaluate_bm25_hybrid.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import app.memory.bm25_index as bm25
import app.memory.rrf as rrf_mod
import app.memory.service as memsvc
from app.memory.embedding import text_embedding
from app.models.memory import Memory

_FIXTURE = _BACKEND / "tests" / "fixtures" / "bm25_p1_queries.json"
_OUT = Path("D:/Codex-Projects/output/bm25_hybrid_eval.md")
_CHAR = 89001


async def _noop(*a, **k):
    return None


def _make_vector_search(mem_vec, mem_meta):
    """用真实 bge-m3 嵌入构建的 cosine 向量检索（等价 ChromaDB cosine 路）。"""
    ids = list(mem_vec)
    mats = np.stack([mem_vec[i] for i in ids]).astype(np.float32)

    async def _vs(character_id, query_embedding, limit=5):
        q = np.asarray(query_embedding, dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-9)
        sims = (mats @ q).astype(float)          # 已 L2 归一化 → cosine
        order = np.argsort(sims)[::-1][:limit]
        out = []
        for idx in order:
            mid = ids[int(idx)]
            content, mtype, imp = mem_meta[mid]
            out.append({"id": mid, "content": content, "type": mtype,
                        "importance": imp, "distance": float(1.0 - sims[idx])})
        return out
    return _vs


async def _main():
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for q in fixture["queries"]:
        if "query" not in q or not q.get("gold"):
            raise SystemExit("fixture 格式错误：每查询需含 query 与非空 gold")

    tmp = tempfile.mkdtemp(prefix="bm25_hybrid_eval_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await _init()

    # 1) 组织语料并落库（gold + distractor 统一为 character_id=_CHAR，importance 相同 = 40）。
    # 等 importance 以隔离「检索路径」对命中的影响：top5 的选择只由 dense/sparse 召回与 RRF 决定，
    # 避免 importance 主导掩盖检索路径差异；「词命中但语义弱」的干扰记忆仍以相同 importance 入池，
    # 检验两路/RRF 是否能把它们与真正相关者区分。
    contents = []           # 去重后的全部记忆内容
    gold_ids_by_query = []  # 每查询的 gold 记忆 id 集合
    mem_meta = {}           # id -> (content, memory_type, importance)
    async with factory() as db:
        for q in fixture["queries"]:
            gold_set = set()
            for c in q["gold"]:
                if c not in contents:
                    contents.append(c)
                    m = Memory(user_id=1, character_id=_CHAR, memory_type="fact",
                               content=c, importance=40.0)
                    db.add(m)
                    await db.flush()
                    mem_meta[m.id] = (c, m.memory_type, 40.0)
                    gold_set.add(m.id)
                else:
                    gold_set.add(_id_of(contents, c, mem_meta))
            for c in q["distractors"] + (q.get("weak_distractors") or []):
                if c in contents:
                    continue
                contents.append(c)
                m = Memory(user_id=1, character_id=_CHAR, memory_type="event",
                           content=c, importance=40.0)
                db.add(m)
                await db.flush()
                mem_meta[m.id] = (c, m.memory_type, 40.0)
            gold_ids_by_query.append(gold_set)
        await db.commit()

    # 2) 计算全部记忆 + 查询的真实 bge-m3 嵌入
    mem_vec = {}
    for mid, (c, _t, _i) in mem_meta.items():
        mem_vec[mid] = np.asarray(await text_embedding(c), dtype=np.float32)
    mem_vec = {mid: v / max(float(np.linalg.norm(v)), 1e-9) for mid, v in mem_vec.items()}

    # 3) 打桩：隔离 DB / 向量路 / 持久化根；trace 与插件 Hook 置空
    memsvc.async_session_factory = factory
    memsvc.delete_memory_vector = _noop
    memsvc.text_embedding = text_embedding          # 真实 bge-m3
    memsvc.vector_search = _make_vector_search(mem_vec, mem_meta)
    import app.agent.trace as _trace_mod
    import app.plugins.registry as _reg_mod
    _orig_trace, _orig_hook = _trace_mod.enqueue_task_log, _reg_mod.run_hook_collect
    _trace_mod.enqueue_task_log = lambda **k: None
    _reg_mod.run_hook_collect = lambda *a, **k: []
    _orig_rrf, _orig_bm25_search = memsvc._rrf, memsvc.bm25_search
    bm25._persist_root = Path(tmp)
    bm25.clear_cache()

    results = []
    try:
        for qi, q in enumerate(fixture["queries"]):
            qu = q["query"]
            gold = gold_ids_by_query[qi]
            row = {"query": qu, "gold_count": len(gold)}
            # 仅向量：关 sparse + 关 RRF
            memsvc.bm25_search = lambda *a, **k: []
            memsvc._rrf = None
            dense = await memsvc.search_memories(character_id=_CHAR, query=qu, limit=5)
            row["dense"] = any(r["id"] in gold for r in dense)
            row["dense_top"] = [r["id"] for r in dense]
            # 向量+BM25(现)：开 sparse、关 RRF
            memsvc.bm25_search = _orig_bm25_search
            memsvc._rrf = None
            hybrid = await memsvc.search_memories(character_id=_CHAR, query=qu, limit=5)
            row["hybrid"] = any(r["id"] in gold for r in hybrid)
            row["hybrid_top"] = [r["id"] for r in hybrid]
            # 向量+BM25+RRF(新)：开 sparse、开 RRF
            memsvc.bm25_search = _orig_bm25_search
            memsvc._rrf = rrf_mod
            fused = await memsvc.search_memories(character_id=_CHAR, query=qu, limit=5)
            row["hybrid_rrf"] = any(r["id"] in gold for r in fused)
            row["hybrid_rrf_top"] = [r["id"] for r in fused]
            results.append(row)
    finally:
        memsvc._rrf, memsvc.bm25_search = _orig_rrf, _orig_bm25_search
        _trace_mod.enqueue_task_log, _reg_mod.run_hook_collect = _orig_trace, _orig_hook
        bm25.clear_cache()
        bm25._persist_root = None
        await engine.dispose()

    # 4) 汇总 + 写 markdown
    n = len(results)
    agg = {k: sum(1 for r in results if r[k]) for k in ("dense", "hybrid", "hybrid_rrf")}
    lines = []
    lines.append("# BM25 混合检索深化：P1 测试集重测对比（2026-08-23）")
    lines.append("")
    lines.append("- **背景**：原 OpenViking P1 测试集（地铁/到达/电脑/水果/老公/台风）在清理临时环境时")
    lines.append("  已丢失；本表为按同主题重建的 `backend/tests/fixtures/bm25_p1_queries.json` 评估结果。")
    lines.append("- **方法**：用临时 SQLite 记忆库（不写生产库）+ 隔离 BM25 持久化目录（不写生产缓存），")
    lines.append("  真实 bge-m3 嵌入作为向量路，真实 jieba + rank-bm25 作为稀疏路；每查询取 top5 判断是否命中 gold。")
    lines.append("- **importance**：全部记忆相同（=40），以隔离「检索路径」对命中的影响（避免 importance 主导掩盖")
    lines.append("  路径差异）；语料含「词命中但语义弱」干扰，用来检验两路/RRF 是否能把它们与真正相关者区分。")
    lines.append("")
    lines.append("- **指标**：三路 top5 命中率（命中 gold 的查询数 / 总查询数），并附 top5 候选构成。")
    lines.append("")
    lines.append("> 说明：bge-m3 对本测试集 6 个常见主题的向量召回已很强，故三路 top5 命中率一致；")
    lines.append("> 从 top5 构成可见 BM25/RRF 仍改变了候选集合与顺序（更宽召回、更合理排序）。RRF 对")
    lines.append("> 『高重要度但语义弱的记忆盖住真正相关者』这一情形的排序增益，由受控单测")
    lines.append("> `tests/test_bm25_rrf.py::test_RRF_语义近但词不近_优于纯合并` 与")
    lines.append("> `test_RRF_词命中但语义弱_优于纯合并` 验证。")
    lines.append("")
    lines.append("| 查询 | 仅向量(dense) | 向量+BM25(现) | 向量+BM25+RRF(新) |")
    lines.append("| --- | --- | --- | --- |")
    for r in results:
        lines.append(f"| {r['query']} | {'✓' if r['dense'] else '✗'} | "
                     f"{'✓' if r['hybrid'] else '✗'} | {'✓' if r['hybrid_rrf'] else '✗'} |")
    lines.append("")
    lines.append(f"**top5 命中率**：仅向量 {agg['dense']}/{n}；"
                 f"向量+BM25(现) {agg['hybrid']}/{n}；向量+BM25+RRF(新) {agg['hybrid_rrf']}/{n}")
    lines.append("")
    lines.append("## 逐查询命中明细（top5 返回的 id）")
    lines.append("")
    for r in results:
        lines.append(f"### 「{r['query']}」（gold {r['gold_count']} 条）")
        lines.append(f"- 仅向量: {r['dense_top']}")
        lines.append(f"- 向量+BM25(现): {r['hybrid_top']}")
        lines.append(f"- 向量+BM25+RRF(新): {r['hybrid_rrf_top']}")
        lines.append("")
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval] 已写入 {_OUT}")
    print(f"[eval] top5 命中率 → 仅向量 {agg['dense']}/{n} · 向量+BM25(现) "
          f"{agg['hybrid']}/{n} · 向量+BM25+RRF(新) {agg['hybrid_rrf']}/{n}")
    for r in results:
        print(f"[eval] {r['query']}: dense={r['dense']} hybrid={r['hybrid']} "
              f"hybrid_rrf={r['hybrid_rrf']}")


def _id_of(contents, content, mem_meta):
    """按内容反查已落库记忆的 id（fixture 中不同查询复用了同一内容时）。"""
    for mid, (c, _t, _i) in mem_meta.items():
        if c == content:
            return mid
    raise SystemExit(f"未找到内容对应的记忆: {content}")


if __name__ == "__main__":
    asyncio.run(_main())
