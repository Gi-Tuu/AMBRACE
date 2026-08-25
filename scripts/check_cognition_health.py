# -*- coding: utf-8 -*-
"""世界认知机制健康度只读检查（审计第三批观察项，2026-08-15）

用途：上线观察 1-3 天，验证新机制线上真实生效：
- 新记忆是否带 speaker/epistemic 标注（写侧）
- world_facts 是否随【状态更新】增长（世界状态折叠）
- stage_memories 是否随剧情/游戏增长（FICTIONAL 隔离）
- reliability 信号是否触发（确认/纠正）
- llm_usage 按用途归因（task 列）
- 主动触发日志节流效果（rejected 量对比）

只读，不改任何数据。
"""
import io
import sqlite3
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import settings as _s
DB = _s.database_url.replace("sqlite+aiosqlite:///", "")  # P1：用配置而非硬编码路径


def q(cur, sql):
    return cur.execute(sql).fetchall()


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    print("==== 世界认知机制健康度检查 ====")
    print()
    print("-- 1) 新记忆 speaker/epistemic 覆盖率（今天新增） --")
    total = q(cur, "SELECT COUNT(*) FROM memories WHERE created_at>='2026-08-15'")[0][0]
    has = q(cur, "SELECT COUNT(*) FROM memories WHERE created_at>='2026-08-15' AND speaker_type IS NOT NULL")[0][0]
    print(f"  今日新增记忆 {total}，带 speaker {has}（{100*has//max(total,1)}%）")
    print()
    print("-- 2) world_facts（世界状态折叠） --")
    wf = q(cur, "SELECT COUNT(*), SUM(status='active') FROM world_facts")[0]
    print(f"  总数 {wf[0]}，活跃 {wf[1]}")
    for r in q(cur, "SELECT predicate, COUNT(*) FROM world_facts GROUP BY predicate"):
        print("   ", r)
    print()
    print("-- 3) stage_memories（剧情/游戏隔离） --")
    for r in q(cur, "SELECT stage_kind, COUNT(*) FROM stage_memories GROUP BY stage_kind"):
        print("   ", r)
    print()
    print("-- 4) 可靠度信号（确认/纠正计数分布） --")
    for r in q(cur, "SELECT confirmation_count, COUNT(*) FROM memories GROUP BY confirmation_count ORDER BY confirmation_count DESC LIMIT 4"):
        print("   confirmation_count", r)
    for r in q(cur, "SELECT contradiction_count, COUNT(*) FROM memories GROUP BY contradiction_count ORDER BY contradiction_count DESC LIMIT 4"):
        print("   contradiction_count", r)
    print()
    print("-- 5) llm_usage 按用途（今日，task 列） --")
    for r in q(cur, "SELECT task, COUNT(*), SUM(prompt_tokens) FROM llm_usage WHERE created_at>='2026-08-15' GROUP BY task ORDER BY COUNT(*) DESC LIMIT 15"):
        print("   ", r)
    print()
    print("-- 6) 主动触发日志节流效果（rejected 每日对比） --")
    for r in q(cur, "SELECT substr(created_at,1,10) d, SUM(decision='approved'), SUM(decision='rejected') FROM proactive_trigger_logs WHERE created_at>='2026-08-14' GROUP BY d ORDER BY d"):
        print("   ", r)
    print()
    print("-- 7) 活跃记忆重复/空值检查 --")
    dup = q(cur, "SELECT COUNT(*) FROM (SELECT content FROM memories WHERE is_archived=0 GROUP BY content HAVING COUNT(*)>1)")[0][0]
    empty = q(cur, "SELECT COUNT(*) FROM memories WHERE is_archived=0 AND content IN ('（无）','（空）','无','空','')")[0][0]
    print(f"   活跃完全重复组 {dup}，空值 {empty}")
    con.close()
    print()
    print("==== 检查完成（只读） ====")


if __name__ == "__main__":
    main()