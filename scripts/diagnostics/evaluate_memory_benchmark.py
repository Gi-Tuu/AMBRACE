# -*- coding: utf-8 -*-
"""Memory Benchmark v1（P1-1，2026-08-16）：记忆领域评测集（默认离线零 LLM 档）

维度（参照竞品调研报告 9.1 Memory Benchmark）：
  - single_hop  单跳回忆：问一条具体事实，检索应召回对应记忆
  - multi_hop   多跳回忆：跨两条记忆关联（目标话题 + 相关记忆）
  - temporal    时间一致性：相对时间表述（上周/三个月前）仍可召回
  - update      记忆更新：save_memory 写入语义（create / update / merge）
  - isolation   知识隔离：角色 A 的记忆不泄漏给 B（复用生产 _filter_cross_char_news）

检索召回用离线模拟器（关键词 2-gram + 字符相似度两路，与 search_memories
关键词兜底语义对齐），不依赖生产 DB / 向量库 / LLM；update 语义用与
memory/service.py 一致的阈值规则（字符 0.72 查重 / 同类型 24h 内 0.6 合并）。

用法：
  cd <项目根目录>
  backend/.venv/Scripts/python.exe scripts/evaluate_memory_benchmark.py [--dump-report]

--dump-report：把报告写入 docs/evaluation-memory-benchmark.md（默认仅打印）。
"""
import argparse
import io
import os
import sys
from difflib import SequenceMatcher

# 允许从 backend 导入生产纯函数（知识隔离维度复用 _filter_cross_char_news）
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 种子记忆（id -> 内容；可扩展）──
MEMORIES = [
    {"id": 1, "type": "preference", "content": "用户喜欢喝美式咖啡，每天早上都要来一杯"},
    {"id": 2, "type": "preference", "content": "用户养了一只叫团团的猫，橘色的，很粘人"},
    {"id": 3, "type": "user_info", "content": "用户在杭州工作"},
    {"id": 4, "type": "event", "content": "上周用户去北京出差，见了客户老张，谈得很顺利"},
    {"id": 5, "type": "event", "content": "用户和角色约好这周五一起看电影"},
    {"id": 6, "type": "event", "content": "用户说周五晚上要加班，可能赶不上电影"},
    {"id": 7, "type": "event", "content": "三个月前用户从广州搬家到了上海，现在住在上海"},
    {"id": 8, "type": "user_info", "content": "用户下周生日，想和朋友一起吃饭庆祝"},
]

# ── 评测问题（expect = 期望命中的记忆 id 列表）──
QUESTIONS = [
    # 单跳回忆
    {"dim": "single_hop", "query": "用户喝什么咖啡", "expect": [1]},
    {"dim": "single_hop", "query": "用户养的猫叫什么", "expect": [2]},
    {"dim": "single_hop", "query": "用户在哪里工作", "expect": [3]},
    {"dim": "single_hop", "query": "用户生日什么时候", "expect": [8]},
    # 多跳回忆（目标话题 + 关联记忆）
    {"dim": "multi_hop", "query": "见过的客户老张是谁", "expect": [4]},
    {"dim": "multi_hop", "query": "周五看电影的安排", "expect": [5, 6]},
    {"dim": "multi_hop", "query": "北京出差见的客户是谁", "expect": [4]},
    # 时间一致性（相对时间表述）
    {"dim": "temporal", "query": "用户最近出差去了哪", "expect": [4]},
    {"dim": "temporal", "query": "用户现在住在哪个城市", "expect": [7]},
    # 记忆更新（写入语义）
    {"dim": "update", "existing": "用户喜欢喝美式咖啡，每天早上都要来一杯", "new": "用户现在喝美式咖啡，每天早上来一杯", "expect_op": "update"},
    {"dim": "update", "existing": "用户在杭州工作", "new": "用户调去上海工作了", "expect_op": "merge"},
    {"dim": "update", "existing": "用户养了一只叫团团的橘猫", "new": "用户周末去爬山徒步", "expect_op": "create"},
    # 知识隔离（A 角色近况不泄漏给 B）
    {"dim": "isolation", "texts": ["小遥说后天要去杭州", "用户今天心情不错"], "names": ["小遥"], "expect_keep": [1]},
    {"dim": "isolation", "texts": ["团团是用户养的猫", "用户明天要开会"], "names": ["团团"], "expect_keep": [1]},
]


def _seg2(text: str) -> set[str]:
    """中文 2-gram 分词（去掉空白与标点）"""
    t = "".join(ch for ch in text if ch.strip())
    return {t[i:i + 2] for i in range(max(0, len(t) - 1))}


def _recall_ids(memories: list[dict], query: str) -> list[int]:
    """离线检索模拟：2-gram 关键词重叠 >= 2 或字符相似度 >= 0.5 → 召回"""
    q2 = _seg2(query)
    hits = []
    for m in memories:
        c = m["content"]
        c2 = _seg2(c)
        need = 2 if len(q2) > 2 else 1
        if q2 and len(q2 & c2) >= need:
            hits.append(m["id"])
            continue
        if SequenceMatcher(None, query[:20], c[:40]).ratio() >= 0.5:
            hits.append(m["id"])
    return hits


def _write_op(existing: str, new: str, same_type_recent: bool) -> str:
    """与 memory/service.py 写入语义对齐：字符 0.72 查重=update；同类型 24h 内 0.6=merge；否则 create"""
    a = existing.strip()[:80]
    b = new.strip()[:80]
    if len(a) < 4 or len(b) < 4:
        return "create"
    sim = SequenceMatcher(None, a, b).ratio()
    if sim >= 0.72:
        return "update"
    if same_type_recent and sim > 0.6:
        return "merge"
    return "create"


def _estimate_tokens(texts: list[str]) -> int:
    """检索 token 估算：query + 命中内容，按 2 字符 ≈ 1 token"""
    return sum(len(t) for t in texts) // 2


def run() -> dict:
    stats: dict[str, list[bool]] = {}
    detail: list[dict] = []
    for q in QUESTIONS:
        dim = q["dim"]
        stats.setdefault(dim, [])
        if dim == "update":
            got = _write_op(q["existing"], q["new"], same_type_recent=True)
            ok = got == q["expect_op"]
        elif dim == "isolation":
            from app.scheduler.ai_social import _filter_cross_char_news
            kept = _filter_cross_char_news(q["texts"], q["names"])
            keep_ids = [i for i, t in enumerate(q["texts"]) if t in kept]
            ok = keep_ids == q["expect_keep"]
            got = kept
        else:
            hits = _recall_ids(MEMORIES, q["query"])
            got = hits
            ok = bool(set(hits) & set(q["expect"]))
        stats[dim].append(ok)
        detail.append({"dim": dim, "query": q.get("query") or q.get("case"), "ok": ok, "got": got,
                       "expect": q.get("expect") or q.get("expect_op") or q.get("expect_keep")})
    return {"stats": stats, "detail": detail}


def build_report(result: dict) -> str:
    stats = result["stats"]
    lines = [
        "# Memory Benchmark v1 评测报告",
        "",
        "> 生成：2026-08-16 ｜ 工具：scripts/evaluate_memory_benchmark.py ｜ 默认离线零 LLM 档",
        "> 维度说明：single_hop 单跳回忆 / multi_hop 多跳回忆 / temporal 时间一致性 / update 记忆更新语义 / isolation 知识隔离",
        "",
        "## 汇总",
        "",
        "| 维度 | 通过 / 总数 | 通过率 |",
        "|------|------------|--------|",
    ]
    total_ok = total_n = 0
    for dim, res in stats.items():
        ok = sum(1 for r in res if r)
        total_ok += ok
        total_n += len(res)
        lines.append(f"| {dim} | {ok} / {len(res)} | {ok / len(res) * 100:.0f}% |")
    lines.append(f"| **合计** | **{total_ok} / {total_n}** | **{total_ok / total_n * 100:.0f}%** |")
    lines += ["", "## 明细", ""]
    for d in result["detail"]:
        mark = "✅" if d["ok"] else "❌"
        lines.append(f"- {mark} [{d['dim']}] {d['query']} ｜ 期望 {d['expect']} ｜ 实际 {d['got']}")
    # 检索 token 效率估算
    q_queries = [q.get("query", "") for q in QUESTIONS if q.get("query")]
    q_tokens = [_estimate_tokens([q]) for q in q_queries]
    avg = (sum(q_tokens) / len(q_tokens)) if q_tokens else 0
    lines += ["", "## 检索 token 效率（估算）", "",
              f"- 平均每次检索 query 估算 token：约 {avg:.0f}（2 字符 ≈ 1 token）",
              "- 目标参考：Mem0 ~7K tokens/query（含召回+排序全链路）；本项目离线单查询远低于该量级",
              ""]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Memory Benchmark v1（离线零 LLM 档）")
    ap.add_argument("--dump-report", action="store_true", help="把报告写入 docs/evaluation-memory-benchmark.md")
    args = ap.parse_args()
    result = run()
    report = build_report(result)
    print(report)
    if args.dump_report:
        out = "docs/evaluation-memory-benchmark.md"
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(report)
        print(f"\n报告已写入 {out}")


if __name__ == "__main__":
    main()
