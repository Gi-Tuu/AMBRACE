# -*- coding: utf-8 -*-
"""世界认知健康度观察脚本（审计第三批第 12 项，2026-08-18 上线观察用）

用途：上线后 1-3 天可重复执行的只读观察（不写库）：
1. 新记忆字段覆盖：memories 中 speaker_type / speaker_id / epistemic_status / why_it_matters 非空占比
   （近 N 天 + 全量，默认近 7 天）；
2. world_facts 增长：按天统计条数 / 来源 / author（近 14 天）；
3. 可靠度闭环：contradiction_count>0 记忆数、reliability_score<0.4 数、
   agent_task_logs trigger 含 fact_check 的执行 ok 率（当前 fact_check 走 tool.executed 事件，
   未写 agent_task_logs，故计数为 0 属预期，请以 memories 的 contradiction 信号为准）。

用法：
  python scripts/observe_cognitive_health.py [--db PATH] [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
  --db    数据库路径（默认自动解析 <项目根>/data/sqlite/ai_companion.db）
  --days  近 N 天窗口（默认 7）；--start/--end 可显式覆盖日期范围
输出：Markdown 表格文本到 stdout；只读，绝不写库。仅用标准库 + sqlite3，无新依赖。
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根 = 本脚本所在目录的上一级（scripts/ -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "sqlite" / "ai_companion.db"

# 新记忆字段覆盖（P2-06 观察）：字段 -> 中文名
MEMORY_FIELDS = [
    ("speaker_type", "speaker_type"),
    ("speaker_id", "speaker_id"),
    ("epistemic_status", "epistemic_status"),
    ("why_it_matters", "why_it_matters"),
]


def _has_table(cur, table: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _has_column(cur, table: str, column: str) -> bool:
    cols = {r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _fmt_pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "0.0%"


def _day_start(days_back: int) -> str:
    return (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")


def _resolve_window(args) -> tuple[str, str]:
    """返回 (start, end) 的 YYYY-MM-DD；--start/--end 优先，否则近 --days 天。"""
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or _day_start(args.days)
    return start, end


def section_memory_fields(cur, start: str, end: str) -> list[str]:
    """1) 新记忆字段覆盖：近 N 天 + 全量非空占比"""
    out = ["### 1. 新记忆字段覆盖（memories）", ""]
    if not _has_table(cur, "memories"):
        out += ["（memories 表不存在，跳过）", ""]
        return out
    lines = ["| 字段 | 窗口内非空 | 窗口内占比 | 全量非空 | 全量占比 |", "|---|---|---|---|---|"]
    present = [f for f, _ in MEMORY_FIELDS if _has_column(cur, "memories", f)]
    for col, _ in MEMORY_FIELDS:
        if col not in present:
            lines.append(f"| {col} | 列不存在 | - | 列不存在 | - |")
            continue
        n_total = cur.execute(
            "SELECT COUNT(*) FROM memories WHERE substr(created_at,1,10) BETWEEN ? AND ?", (start, end)
        ).fetchone()[0]
        n_have = cur.execute(
            f"SELECT COUNT(*) FROM memories WHERE {col} IS NOT NULL AND {col} != '' "
            "AND substr(created_at,1,10) BETWEEN ? AND ?", (start, end)
        ).fetchone()[0]
        a_total = cur.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        a_have = cur.execute(
            f"SELECT COUNT(*) FROM memories WHERE {col} IS NOT NULL AND {col} != ''"
        ).fetchone()[0]
        lines.append(
            f"| {col} | {n_have} | {_fmt_pct(n_have, n_total)} | {a_have} | {_fmt_pct(a_have, a_total)} |"
        )
    if not present:
        lines.append("（memories 表无任何新字段列，P3 未生效或库未迁移）")
    out += lines + [""]
    return out


def section_world_facts(cur, start: str, end: str) -> list[str]:
    """2) world_facts 增长：近 14 天按天/来源/author 统计"""
    out = ["### 2. world_facts 增长", ""]
    if not _has_table(cur, "world_facts"):
        out += ["（world_facts 表不存在，跳过）", ""]
        return out
    total = cur.execute("SELECT COUNT(*) FROM world_facts").fetchone()[0]
    active = cur.execute("SELECT COUNT(*) FROM world_facts WHERE status='active'").fetchone()[0]
    out.append(f"全量：{total} 条（活跃 {active}）")
    out.append("")
    out.append("按天（近 14 天，新增条数）：")
    out.append("| 日期 | 条数 | 活跃 | superseded |")
    out.append("|---|---|---|---|")
    rows = cur.execute(
        "SELECT substr(created_at,1,10) d, COUNT(*), "
        "SUM(status='active'), SUM(status='superseded') "
        "FROM world_facts WHERE substr(created_at,1,10) >= ? GROUP BY d ORDER BY d",
        (_day_start(14),),
    ).fetchall()
    if not rows:
        out.append("| （近 14 天无新增） | 0 | 0 | 0 |")
    for d, cnt, act, sup in rows:
        out.append(f"| {d} | {cnt} | {act or 0} | {sup or 0} |")
    out.append("")
    out.append("按来源（近 14 天）：")
    src_rows = cur.execute(
        "SELECT COALESCE(source,'(空)'), COUNT(*) FROM world_facts "
        "WHERE substr(created_at,1,10) >= ? GROUP BY source ORDER BY COUNT(*) DESC",
        (_day_start(14),),
    ).fetchall()
    if src_rows:
        out.append("| 来源 | 条数 |")
        out.append("|---|---|")
        for s, c in src_rows:
            out.append(f"| {s} | {c} |")
    out.append("")
    out.append("按 author（近 14 天）：")
    auth_rows = cur.execute(
        "SELECT COALESCE(author,'(空)'), COUNT(*) FROM world_facts "
        "WHERE substr(created_at,1,10) >= ? GROUP BY author ORDER BY COUNT(*) DESC",
        (_day_start(14),),
    ).fetchall()
    if auth_rows:
        out.append("| author | 条数 |")
        out.append("|---|---|")
        for a, c in auth_rows:
            out.append(f"| {a} | {c} |")
    out.append("")
    return out


def section_reliability(cur, start: str, end: str) -> list[str]:
    """3) 可靠度闭环：矛盾/低可靠度记忆 + fact_check 可观测计数"""
    out = ["### 3. 可靠度闭环", ""]
    if _has_table(cur, "memories"):
        total = cur.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        contrad = 0
        if _has_column(cur, "memories", "contradiction_count"):
            contrad = cur.execute(
                "SELECT COUNT(*) FROM memories WHERE contradiction_count > 0"
            ).fetchone()[0]
        low_rel = 0
        if _has_column(cur, "memories", "reliability_score"):
            low_rel = cur.execute(
                "SELECT COUNT(*) FROM memories WHERE reliability_score IS NOT NULL "
                "AND reliability_score < 0.4"
            ).fetchone()[0]
        rel_rated = 0
        if _has_column(cur, "memories", "reliability_score"):
            rel_rated = cur.execute(
                "SELECT COUNT(*) FROM memories WHERE reliability_score IS NOT NULL"
            ).fetchone()[0]
        out.append(f"- 记忆总数：{total}")
        out.append(f"- contradiction_count > 0（用户纠正过）：**{contrad}**")
        out.append(f"- reliability_score < 0.4（低可靠度）：**{low_rel}**（已结算评分 {rel_rated} 条）")
        out.append("")
        # 近 N 天新增的纠正信号（闭环真实触发观察）
        new_contrad = 0
        if _has_column(cur, "memories", "contradiction_count"):
            new_contrad = cur.execute(
                "SELECT COUNT(*) FROM memories WHERE contradiction_count > 0 "
                "AND substr(created_at,1,10) BETWEEN ? AND ?", (start, end)
            ).fetchone()[0]
        out.append(f"- 近 {args_days_note(start, end)} 新增/更新的矛盾记忆（contradiction_count>0 且创建于窗口内）：{new_contrad}")
    else:
        out.append("（memories 表不存在，跳过）")
    out.append("")
    # fact_check 相关可观测：agent_task_logs trigger 含 fact_check 的 ok 率
    if _has_table(cur, "agent_task_logs") and _has_column(cur, "agent_task_logs", "trigger"):
        fc_total = cur.execute(
            "SELECT COUNT(*) FROM agent_task_logs WHERE trigger LIKE '%fact_check%'"
        ).fetchone()[0]
        fc_ok = cur.execute(
            "SELECT COUNT(*) FROM agent_task_logs WHERE trigger LIKE '%fact_check%' AND status='ok'"
        ).fetchone()[0]
        out.append("fact_check 可观测（agent_task_logs trigger 含 fact_check）：")
        out.append(f"- 执行次数：{fc_total}，ok 次数：{fc_ok}，ok 率：{_fmt_pct(fc_ok, fc_total)}")
        out.append("> 注：当前 fact_check 走 tool.executed 事件与 memories.contradiction_count 闭环，")
        out.append("> 未写 agent_task_logs；若上方为 0 属预期，请以 contradiction/reliability 信号为准。")
    else:
        out.append("（agent_task_logs 不存在或缺少 trigger 列，fact_check 计数跳过）")
    out.append("")
    return out


def args_days_note(start: str, end: str) -> str:
    if start == end:
        return f"单日 {start}"
    return f"{start}~{end}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="世界认知健康度观察（只读，不写库）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    parser.add_argument("--days", type=int, default=7, help="观察窗口天数（默认 7）")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD（覆盖 --days）")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD（默认今天）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[error] 数据库不存在：{db_path}", file=sys.stderr)
        print(f"[hint] 可通过 --db 指定生产库路径（默认 {DEFAULT_DB}）", file=sys.stderr)
        return 1

    start, end = _resolve_window(args)
    con = sqlite3.connect(str(db_path))
    con.text_factory = str
    cur = con.cursor()
    try:
        print(f"# 世界认知健康度观察（{start} ~ {end}，只读）")
        print("")
        for fn in (section_memory_fields, section_world_facts, section_reliability):
            for line in fn(cur, start, end):
                print(line)
        print("_观察完成（未写任何数据）_")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
