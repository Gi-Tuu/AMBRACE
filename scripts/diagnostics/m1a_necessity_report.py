# -*- coding: utf-8 -*-
"""M1-a 工作记忆必要性 · 观测报告（纯分析，零生产改动，2026-09-01）。

用途：M3 工作记忆立项的必要性判定数据源（docs/设计_M3工作记忆_20260901.md §7）。
从既有 memory_search trace（agent_task_logs，M1-S11 + #70-B 已在写）统计：

    「查询词与该角色最近 3 天消息高度重合，但向量召回 miss」的比例

- 高度重合：query 的内容 token（CJK 2-gram + 拉丁词）在最近 3 天消息语料中出现的比例 ≥ 阈值（默认 0.6）；
- 向量召回 miss：route=keyword（向量+BM25 双路皆空走 LIKE 兜底）或 returned==0；
- 判定标准：2 周后 ratio ≥ 15% → M3 正式立项动工；不达标则延期。

用法（backend/.venv/Scripts/python.exe，任意目录）：
    python scripts/diagnostics/m1a_necessity_report.py                # 默认最近 14 天
    python scripts/diagnostics/m1a_necessity_report.py --days 7       # 指定窗口
    python scripts/diagnostics/m1a_necessity_report.py --samples 20   # 输出样本 query
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]


def _resolve_db() -> Path:
    try:
        sys.path.insert(0, str(PROJECT / "backend"))
        from app.config import settings
        url = settings.database_url
        if url.startswith("sqlite"):
            p = url.split("///", 1)[-1].split("?", 1)[0]
            if p and p != ":memory:":
                return Path(p).resolve()
    except Exception:
        pass
    return (PROJECT / "backend" / "data" / "sqlite" / "ai_companion.db").resolve()


_TOKEN_RE = re.compile(r"[a-zA-Z]{2,}|[\u4e00-\u9fff]{2,}")


def _query_tokens(query: str) -> set[str]:
    """query 内容 token：CJK 连续段取 2-gram（覆盖词内组合）+ 拉丁词原样。"""
    tokens: set[str] = set()
    for seg in _TOKEN_RE.findall(query or ""):
        if seg.isascii():
            tokens.add(seg.lower())
        else:
            for i in range(len(seg) - 1):
                tokens.add(seg[i:i + 2])
    return tokens


def _msg_tokens(text: str) -> set[str]:
    """消息语料 token：CJK 2-gram + 拉丁词（与 query 同口径）。"""
    return _query_tokens(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14, help="统计窗口（天，按 trace created_at）")
    ap.add_argument("--recent-days", type=int, default=3, help="「最近 N 天消息」窗口（相对每条 trace 时间）")
    ap.add_argument("--overlap-threshold", type=float, default=0.6, help="高度重合阈值（token 覆盖比例）")
    ap.add_argument("--samples", type=int, default=10, help="输出 miss 样本条数")
    args = ap.parse_args()

    db = _resolve_db()
    if not db.exists():
        print(f"[ERR] 数据库不存在: {db}")
        return
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    since = (datetime.utcnow() - timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    traces = conn.execute(
        "SELECT character_id, created_at, steps_json FROM agent_task_logs "
        "WHERE trigger='memory_search' AND status='ok' AND created_at >= ? "
        "ORDER BY created_at ASC",
        (since,),
    ).fetchall()
    print(f"窗口: 最近 {args.days} 天 | memory_search trace: {len(traces)} 条")

    # 每角色消息语料缓存：created_at 相对窗口在分析时计算（trace 时间 - 3 天 ~ trace 时间）
    corpus_cache: dict[tuple, str] = {}

    def _recent_corpus(character_id: int, trace_time: str) -> str:
        key = (character_id, trace_time[:13])  # 按小时粒度缓存
        if key in corpus_cache:
            return corpus_cache[key]
        try:
            base = datetime.fromisoformat(trace_time.replace(" ", "T").split(".")[0])
        except Exception:
            return ""
        t0 = (base - timedelta(days=args.recent_days)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT cm.content AS content FROM chat_messages cm "
            "JOIN chat_sessions cs ON cm.session_id = cs.id "
            "WHERE cs.character_id=? AND cm.created_at>? AND cm.created_at<=? "
            "ORDER BY cm.id DESC LIMIT 200",
            (character_id, t0, trace_time),
        ).fetchall()
        corpus = " ".join(r["content"] for r in rows)
        corpus_cache[key] = corpus
        return corpus

    total = high_overlap = miss_and_overlap = 0
    miss_samples: list[dict] = []
    route_stat: dict[str, int] = {}
    for tr in traces:
        try:
            dbg = json.loads(tr["steps_json"] or "{}")
        except Exception:
            continue
        query = str(dbg.get("query") or "")
        route = str(dbg.get("route") or "")
        returned = dbg.get("returned")
        returned_n = len(returned) if isinstance(returned, list) else (int(returned or 0))
        route_stat[route] = route_stat.get(route, 0) + 1
        if not query.strip():
            continue
        total += 1
        q_tokens = _query_tokens(query)
        if not q_tokens:
            continue
        corpus = _recent_corpus(tr["character_id"], tr["created_at"])
        c_tokens = _msg_tokens(corpus)
        if not c_tokens:
            overlap = 0.0
        else:
            overlap = sum(1 for tk in q_tokens if tk in c_tokens) / len(q_tokens)
        if overlap < args.overlap_threshold:
            continue
        high_overlap += 1
        # 向量召回 miss：双路皆空（keyword 兜底）或零返回
        if route == "keyword" or returned_n == 0:
            miss_and_overlap += 1
            if len(miss_samples) < args.samples * 3:
                miss_samples.append({
                    "query": query[:60], "route": route, "overlap": round(overlap, 2),
                    "character_id": tr["character_id"], "at": tr["created_at"],
                })

    print(f"有效 query: {total} | 高度重合: {high_overlap} | 重合且回归 miss: {miss_and_overlap}")
    print(f"route 分布: {route_stat}")
    if high_overlap:
        ratio = miss_and_overlap / high_overlap * 100
        print(f"M1-a 判定比例 = {miss_and_overlap}/{high_overlap} = {ratio:.1f}%  （立项阈值 ≥15%）")
        print("判定：" + ("≥15% → 支持正式立项" if ratio >= 15 else "<15% → 暂不支持（继续观测或复核阈值/口径）"))
    if miss_samples:
        print("—— miss 样本（截前若干条）——")
        for s in miss_samples[: args.samples]:
            print("  ", json.dumps(s, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
