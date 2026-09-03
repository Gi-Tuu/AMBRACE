# -*- coding: utf-8 -*-
"""记忆上下文块级基准（Ariadne 模块 E v2，2026-09-04）：真实检索栈 × flag 配置对比裁判台。

与既有基准的分工（Codex 修订要点 4）：
- scripts/diagnostics/evaluate_memory_benchmark.py：检索质量离线模拟评测（自身检索模拟器 + 14 用例，
  零 LLM，评的是「检索器对不对」）；
- 本脚本：**上下文块级**对比——真实检索栈（SQLite 临时库 + 临时 Chroma + bge-m3 本地嵌入 + BM25）
  按 flag 配置（baseline / temporal / peak / …）对同一批陪伴场景用例构建「喂给模型的上下文块」，
  以 gold_points 覆盖率、弃权正确率、注入 token 成本做确定性评分；LLM 裁判（丰富度/易理解性/
  建模度/忠实度四维）为可选扩展位（v2 未接入，需要可用 LLM 配置）。

v2 新增（为模块 D 标定配套）：
- 三模式分项评分（Qdrant 方法论借用）：稠密 / BM25 / 时间路 分别 Recall@5（关键词兜底路并入
  fused 对比），输出到报告——定位「哪一路在哪个类别拖后腿」；
- rerank 分数采集：memory_search trace（memory_trace_debug 默认开）回读 rerank_top 分数，
  输出 gold 命中项 vs 弃权类候选的分数分布 → peak_cutoff 阈值标定依据。

用法（cwd=backend）：
    .venv/Scripts/python.exe ../scripts/diagnostics/memory_context_bench.py \
        --dataset ../scripts/diagnostics/memory_bench_cases_zh.jsonl \
        --configs baseline,temporal,peak --out ../memory_context_bench_report.md

- 全程临时库（DATABASE_URL / chroma 指向临时目录），不碰真实库、零 LLM 调用（--judge 除外）；
- 稠密路失败（模型/依赖缺失）自动降级为「稀疏+关键词+时间路」并记 warning，报告标注；
- 每用例独立 character_id 防串扰；退出码恒 0（报告型脚本，PASS/FAIL 看报告数值）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime

# 自举：把 backend/ 加进 sys.path（脚本可从任意 cwd 运行）
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

CONFIGS: dict[str, dict] = {
    # 配置名 → flag 快照（仅影响检索行为的旗标；模块 D 的 memory_peak_cutoff 在此对比）
    "baseline": {"memory_temporal_recall": False, "memory_peak_cutoff": False},
    "temporal": {"memory_temporal_recall": True, "memory_peak_cutoff": False},
    "peak": {"memory_temporal_recall": False, "memory_peak_cutoff": True},
}


def _norm(s: str) -> str:
    """归一化：去空白（覆盖度匹配用，避免格式化前缀/换行影响）"""
    return re.sub(r"\s+", "", s or "")


def gold_coverage(gold_points: list[str], block: str) -> tuple[list[str], list[str]]:
    """gold 点覆盖判定（纯函数）：gold 点归一化后为块文本子串即算覆盖。返回 (命中, 未命中)。"""
    nb = _norm(block)
    hit, miss = [], []
    for g in gold_points or []:
        (hit if _norm(g) and _norm(g) in nb else miss).append(g)
    return hit, miss


def abstain_ok(block: str, est_tokens: int, *, max_tokens: int = 20) -> bool:
    """弃权判定（纯函数）：期望弃权的用例，上下文块应近空（默认 <20 token 估算）。"""
    return est_tokens <= max_tokens


def parse_case(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    return json.loads(line)


def _seed_date(c: dict, now: datetime) -> datetime:
    d = str(c.get("date") or "").strip()
    if d:
        return datetime.strptime(d, "%Y-%m-%d")
    return now


async def build_store(cases: list[dict]) -> tuple[dict[str, int], dict[int, str]]:
    """把用例种子写入临时库（每用例独立角色）；稠密向量入库失败自动跳过（降级）。

    返回 (cid 映射, memory_id → 种子内容映射)（id→content 供分项评分归属）。
    """
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    from app.memory.embedding import text_embedding
    from app.db.vector_store import add_memory

    char_map: dict[str, int] = {}
    id_content: dict[int, str] = {}
    dense_fail = 0
    for i, case in enumerate(cases):
        cid = 9000 + i
        char_map[case["cid"]] = cid
        async with async_session_factory() as db:
            for j, seed in enumerate(case.get("seeds") or []):
                row = Memory(
                    character_id=cid,
                    user_id=1,
                    memory_type=seed.get("type") or "event",
                    content=seed["content"],
                    importance=float(seed.get("importance") or 50),
                    created_at=_seed_date(seed, datetime.utcnow()),
                    speaker_id=None if (seed.get("speaker") or "user") == "user" else 1,
                    speaker_type=seed.get("speaker") or "user",
                )
                db.add(row)
                await db.flush()
                id_content[row.id] = seed["content"]
                try:
                    emb = await text_embedding(seed["content"])
                    await add_memory(row.id, cid, row.memory_type, seed["content"], emb,
                                     importance=int(seed.get("importance") or 50))
                except Exception:
                    dense_fail += 1
            await db.commit()
    if dense_fail:
        print(f"[WARN] 稠密向量入库失败 {dense_fail} 条（降级为 稀疏+关键词+时间路）", file=sys.stderr)
    return char_map, id_content


async def mode_recall(case: dict, cid: int, id_content: dict[int, str], k: int = 5) -> dict:
    """三模式分项 Recall@k（Qdrant 方法论借用）+ 时间路可用性。

    每模式独立取 top-k 命中 id，经 id→种子内容映射做 gold 覆盖判定；
    记录每模式「gold 命中的种子 id 集合」，用于定位哪一路拖后腿。
    """
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.memory import Memory as MemoryContent
    from app.memory.embedding import text_embedding
    from app.db.vector_store import search_memories as vector_search
    from app.memory.bm25_index import search as bm25_search
    from app.memory.time_query import parse_time_range

    gold = case.get("gold_points") or []

    def _cov(hit_ids: list) -> float:
        if not gold:
            return 0.0
        content = _norm(" ".join(id_content.get(i, "") for i in hit_ids))
        return round(sum(1 for g in gold if _norm(g) in content) / len(gold), 2)

    out: dict = {"case": case["cid"], "category": case["category"]}
    # 稠密路
    try:
        emb = await text_embedding(case["user_turn"])
        vhits = await vector_search(character_id=cid, query_embedding=emb, limit=k)
        out["dense"] = _cov([h["id"] for h in vhits])
    except Exception:
        out["dense"] = None  # 模型/依赖不可用
    # BM25 稀疏路
    try:
        shits = await bm25_search(cid, case["user_turn"], top_k=k)
        out["sparse"] = _cov([mid for mid, _sc in shits])
    except Exception:
        out["sparse"] = None
    # 关键词兜底路（LIKE）
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(MemoryContent).where(
                MemoryContent.character_id == cid,
                MemoryContent.is_archived == False,  # noqa: E712
            )
        )).scalars().all()
    like_hits = [r.id for r in rows if case["user_turn"] in (r.content or "")]
    out["keyword"] = _cov(like_hits[:k])
    # 时间路（解析不出区间 → None）
    tr = parse_time_range(case["user_turn"])
    if tr is not None:
        async with async_session_factory() as db:
            trows = (await db.execute(
                select(MemoryContent).where(
                    MemoryContent.character_id == cid,
                    MemoryContent.is_archived == False,  # noqa: E712
                    MemoryContent.created_at >= tr[0],
                    MemoryContent.created_at < tr[1],
                ).order_by(MemoryContent.importance.desc(), MemoryContent.created_at.desc()).limit(k)
            )).scalars().all()
        out["time"] = _cov([r.id for r in trows])
    else:
        out["time"] = None
    return out


async def _read_rerank_scores(cid: int, retries: int = 6) -> list[tuple[int, float]]:
    """回读该角色最近一次 memory_search trace 的 rerank_top（标定数据源；trace 默认开）。

    enqueue_task_log 为 spawn_background fire-and-forget 异步写——等待+重试避免竞态。"""
    import asyncio as _aio
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.agent import AgentTaskLog
    row = None
    for _ in range(retries):
        async with async_session_factory() as db:
            row = (await db.execute(
                select(AgentTaskLog.steps_json)
                .where(AgentTaskLog.character_id == cid, AgentTaskLog.trigger == "memory_search")
                .order_by(AgentTaskLog.id.desc()).limit(1)
            )).scalar_one_or_none()
        if row:
            break
        await _aio.sleep(0.15)
    if not row:
        return []
    try:
        dbg = json.loads(row)
        return [(int(t["id"]), float(t["score"])) for t in (dbg.get("rerank_top") or [])]
    except Exception:
        return []


async def run_config(cfg_name: str, cases: list[dict], char_map: dict[str, int],
                     id_content: dict[int, str]) -> tuple[list[dict], list[dict]]:
    """按 flag 快照对全部用例跑检索并评分；同时采集 rerank 分数（标定数据）。"""
    from app.agent import loop as agent_loop
    from app.memory import search_memories
    from app.memory.format import format_memory_line
    from app.memory.time_query import parse_time_range

    snapshot = dict(agent_loop.AGENT_FLAGS)
    try:
        for k, v in CONFIGS[cfg_name].items():
            agent_loop.AGENT_FLAGS[k] = v
        rows, scores = [], []
        for case in cases:
            cid = char_map[case["cid"]]
            gold = case.get("gold_points") or []
            t0 = time.monotonic()
            tr = parse_time_range(case["user_turn"])
            hits = await search_memories(
                character_id=cid,
                query=case["user_turn"],
                limit=8,
                time_range=tr,
                trace_meta={"user_id": 1, "bench": cfg_name},
            )
            latency_ms = (time.monotonic() - t0) * 1000
            block = "\n".join(format_memory_line(h, include_speaker=True) for h in hits)
            est_tokens = len(block) // 2
            hit, miss = gold_coverage(gold, block)
            rows.append({
                "cid": case["cid"], "category": case["category"],
                "gold_total": len(gold), "gold_hit": len(hit), "gold_miss": miss,
                "coverage": (len(hit) / len(gold)) if gold else None,
                "hits": len(hits), "est_tokens": est_tokens, "latency_ms": round(latency_ms),
                "abstain_expected": bool(case.get("expect_abstain")),
                "abstain_ok": abstain_ok(block, est_tokens) if case.get("expect_abstain") else None,
            })
            # 标定数据：gold 种子 id 集合（种子内容含 gold 点）
            gold_ids = {mid for mid, content in id_content.items()
                        if any(_norm(g) in _norm(content) for g in gold)}
            rt = await _read_rerank_scores(cid)
            scores.append({
                "cid": case["cid"], "category": case["category"],
                "gold_scores": [s for i, s in rt if i in gold_ids],
                "nongold_scores": [s for i, s in rt if i not in gold_ids],
            })
        return rows, scores
    finally:
        agent_loop.AGENT_FLAGS.clear()
        agent_loop.AGENT_FLAGS.update(snapshot)


def summarize(rows: list[dict]) -> dict:
    gold_rows = [r for r in rows if r["gold_total"]]
    cov = [r["coverage"] for r in gold_rows]
    ab_rows = [r for r in rows if r["abstain_expected"]]
    lat = sorted(r["latency_ms"] for r in rows)
    return {
        "coverage": round(sum(cov) / len(cov), 3) if cov else None,
        "full_recall": round(sum(1 for r in gold_rows if r["gold_hit"] == r["gold_total"]) / len(gold_rows), 3) if gold_rows else None,
        "abstain_ok": round(sum(1 for r in ab_rows if r["abstain_ok"]) / len(ab_rows), 3) if ab_rows else None,
        "avg_tokens": round(sum(r["est_tokens"] for r in rows) / len(rows), 1),
        "p95_latency_ms": lat[max(0, int(len(lat) * 0.95) - 1)] if lat else None,
    }


def calibrate_summary(score_rows: list[dict]) -> dict:
    """分数分布汇总（模块 D 标定依据）：gold 命中项最低分 / 弃权类非命中项最高分。"""
    gold_scores = [s for r in score_rows for s in r["gold_scores"]]
    abstain_nongold = [s for r in score_rows if r["category"] == "abstention" for s in r["nongold_scores"]]
    all_nongold = [s for r in score_rows for s in r["nongold_scores"]]
    return {
        "gold_min": round(min(gold_scores), 1) if gold_scores else None,
        "gold_p10": round(sorted(gold_scores)[max(0, int(len(gold_scores) * 0.1) - 1)], 1) if gold_scores else None,
        "abstain_max": round(max(abstain_nongold), 1) if abstain_nongold else None,
        "nongold_max": round(max(all_nongold), 1) if all_nongold else None,
    }


def render_report(per_config: dict[str, list[dict]], mode_rows: list[dict],
                  calib: dict | None, calib_src: str | None) -> str:
    lines = ["# Memory Context Bench 报告（E v2）", "",
             f"- 生成时间：{datetime.utcnow():%Y-%m-%d %H:%M} UTC",
             "- 评分：gold_points 覆盖率（确定性子串匹配）/ 弃权正确率 / 注入 token 成本 / 检索延迟", ""]
    lines.append("| 配置 | 覆盖率 | 全命中 | 弃权正确率 | 平均 token | P95 延迟(ms) |")
    lines.append("|---|---|---|---|---|---|")
    for name, rows in per_config.items():
        s = summarize(rows)
        lines.append(f"| {name} | {s['coverage']} | {s['full_recall']} | {s['abstain_ok']} | {s['avg_tokens']} | {s['p95_latency_ms']} |")
    # 三模式分项
    cats = sorted({r["category"] for r in mode_rows})
    lines.append("")
    lines.append("## 三模式分项 Recall@5（按类别均值；None=模式不可用/无时间区间）")
    lines.append("| 类别 | dense | sparse | keyword | time |")
    lines.append("|---|---|---|---|---|")
    for cat in cats:
        rows = [r for r in mode_rows if r["category"] == cat]

        def _avg(key):
            vals = [r[key] for r in rows if r[key] is not None]
            return round(sum(vals) / len(vals), 2) if vals else None
        lines.append(f"| {cat} | {_avg('dense')} | {_avg('sparse')} | {_avg('keyword')} | {_avg('time')} |")
    # 标定
    if calib:
        lines.append("")
        lines.append("## peak_cutoff 标定数据（baseline 配置 rerank_top 分数分布）")
        lines.append(f"- gold 命中项最低分：{calib['gold_min']}（P10：{calib['gold_p10']}）")
        lines.append(f"- 弃权类非命中项最高分：{calib['abstain_max']}（全部非命中项最高：{calib['nongold_max']}）")
        lines.append(f"- 建议地板 min_score 应落在 [{calib['abstain_max']}, {calib['gold_min']}] 区间内"
                     if calib["abstain_max"] is not None and calib["gold_min"] is not None and calib["abstain_max"] < calib["gold_min"]
                     else "- ⚠️ 弃权类与 gold 分数区间重叠，地板不可分（需扩数据/调 rerank）")
        if calib_src:
            lines.append(f"- 标定数据源：{calib_src}")
    # 逐用例
    for name, rows in per_config.items():
        lines.append("")
        lines.append(f"## 配置 {name} · 逐用例")
        lines.append("| cid | 类别 | 覆盖 | 命中/总数 | 未命中 | token | 弃权 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in rows:
            lines.append(f"| {r['cid']} | {r['category']} | {r['coverage']} | {r['gold_hit']}/{r['gold_total']} "
                         f"| {','.join(r['gold_miss']) or '—'} | {r['est_tokens']} | {r['abstain_ok']} |")
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser(description="记忆上下文块级基准（E v2）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default="memory_context_bench_report.md")
    ap.add_argument("--configs", default="baseline,temporal,peak")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条用例（冒烟用）")
    ap.add_argument("--skip-modes", action="store_true", help="跳过三模式分项（提速）")
    ap.add_argument("--no-calibrate", action="store_true", help="跳过分数采集")
    args = ap.parse_args()

    with open(args.dataset, encoding="utf-8-sig") as f:
        cases = [c for c in (parse_case(ln) for ln in f) if c]
    if args.limit:
        cases = cases[:args.limit]

    tmp = tempfile.mkdtemp(prefix="memctx_bench_")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + os.path.join(tmp, "t.db").replace(os.sep, "/")
    import app.config as cfg
    cfg.settings.database_url = os.environ["DATABASE_URL"]
    cfg.settings.chroma_persist_dir = os.path.join(tmp, "chroma")
    os.environ["CHROMA_PERSIST_DIR"] = cfg.settings.chroma_persist_dir

    from app.db.database import init_db
    await init_db()

    char_map, id_content = await build_store(cases)
    per_config: dict[str, list[dict]] = {}
    score_rows: list[dict] = []
    for name in [c.strip() for c in args.configs.split(",") if c.strip()]:
        assert name in CONFIGS, f"未知配置 {name}（可选：{','.join(CONFIGS)}）"
        rows, scores = await run_config(name, cases, char_map, id_content)
        per_config[name] = rows
        if name == "baseline":
            score_rows = scores  # 标定数据只在 baseline（无时间路/无 peak）下采集

    mode_rows = [] if args.skip_modes else [await mode_recall(c, char_map[c["cid"]], id_content) for c in cases]
    calib = None if args.no_calibrate or not score_rows else calibrate_summary(score_rows)
    report = render_report(per_config, mode_rows, calib, calib_src=os.environ["DATABASE_URL"])
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n[OK] 报告已写入 {args.out}（临时库 {tmp}）")


if __name__ == "__main__":
    asyncio.run(main())
