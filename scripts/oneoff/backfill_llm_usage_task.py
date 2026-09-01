# -*- coding: utf-8 -*-
"""llm_usage.task 历史 NULL 审计脚本（审计第三批第 10 项 P1-07 收尾）

结论先行：llm_usage 表无 source/消息上下文列，历史 task=NULL 行无法可靠推断用途
（聊天主链路与主动/提取/摘要等均写同一张表，仅凭 user_id/model/token 无法区分），
按审计指令「无法推断的保持 NULL（不编造）」，本脚本**只审计不写库**。

提供两类信息供 Codex 判断：
1. NULL 行总量 / 按天分布；
2. 按 task 列引入时间（--task-since，默认 2026-08-15 即 P1-07 补列日）切分：
   - 补列前的历史行（必然 NULL，无法推断 → 保持）；
   - 补列后仍为 NULL 的行（说明当时存在未传 task 的调用点，即本批已修复的
     extractor/voice 等漏网点，或流式链路不记账——修复后应不再新增）。
仅用标准库 + sqlite3，无新依赖。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "backend" / "data" / "sqlite" / "ai_companion.db"
DEFAULT_TASK_SINCE = "2026-08-15"  # P1-07 补 task 列/调用点传 task 的日期（审计报告基线）


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="llm_usage.task NULL 审计（只读，不写库）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 数据库路径")
    parser.add_argument("--task-since", default=DEFAULT_TASK_SINCE,
                        help="task 列生效起始日期 YYYY-MM-DD（默认 %(default)s）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[error] 数据库不存在：{db_path}", file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db_path))
    con.text_factory = str
    cur = con.cursor()
    try:
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_usage'")
        if cur.fetchone() is None:
            print("[error] llm_usage 表不存在", file=sys.stderr)
            return 1
        cols = {r[1] for r in cur.execute("PRAGMA table_info(llm_usage)").fetchall()}
        if "task" not in cols:
            print("[warn] llm_usage 无 task 列（P1-07 补列尚未执行到该库）", file=sys.stderr)
            return 1

        total = cur.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0]
        null_total = cur.execute("SELECT COUNT(*) FROM llm_usage WHERE task IS NULL OR task=''").fetchone()[0]
        null_tokens = cur.execute(
            "SELECT COALESCE(SUM(total_tokens),0) FROM llm_usage WHERE task IS NULL OR task=''"
        ).fetchone()[0]
        print("# llm_usage task=NULL 审计（只读）")
        print("")
        print(f"- 总行数：{total}")
        print(f"- task 为 NULL/空：**{null_total}**（占总 {100.0 * null_total / max(total, 1):.1f}%，token 合计 {null_tokens}）")
        print(f"- 有 task 的行：{total - null_total}")
        print("")

        print("## NULL 行按天分布（近 30 天）")
        print("| 日期 | NULL 行数 |")
        print("|---|---|")
        rows = cur.execute(
            "SELECT substr(created_at,1,10) d, COUNT(*) FROM llm_usage "
            "WHERE (task IS NULL OR task='') AND substr(created_at,1,10) >= date('now','-30 day') "
            "GROUP BY d ORDER BY d"
        ).fetchall()
        if not rows:
            print("| （近 30 天无 NULL） | 0 |")
        for d, c in rows:
            print(f"| {d} | {c} |")
        print("")

        # 按 task 列生效时间切分
        hist = cur.execute(
            "SELECT COUNT(*) FROM llm_usage WHERE (task IS NULL OR task='') "
            "AND substr(created_at,1,10) < ?", (args.task_since,)
        ).fetchone()[0]
        after = null_total - hist
        print(f"## 按 task 列生效日（{args.task_since}）切分")
        print(f"- 生效前历史 NULL（无法推断，保持 NULL）：**{hist}**")
        print(f"- 生效后仍 NULL（本批已修复 extractor/voice 漏网点，修复后应不再新增）：**{after}**")
        print("")
        print("## 结论（不写库）")
        print("- llm_usage 无 source/消息上下文列，无法按来源推断历史 task；按审计指令保持 NULL 不编造。")
        print("- 本批已为 extractor（记忆提取）补 task=memory、voice 流式链路补 task=chat，并加")
        print("  TASK_PLUGIN_AI 常量供 plan 48 使用；此后新调用应全部带 task。")
        print("- 如需彻底区分「历史未知」与「当前漏网」，可在后续版本为 llm_usage 增加 source 列（不在本批范围）。")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
