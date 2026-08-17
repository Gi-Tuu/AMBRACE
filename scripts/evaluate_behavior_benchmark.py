# -*- coding: utf-8 -*-
"""行为基准（S-5，2026-08-16）：从真实 DB 统计关系 / AI Life / Agent 指标（零 LLM）

对齐竞品调研报告 9.2/9.3/9.4：
- 关系（9.2）：主动消息频率（近 7 天 trigger=scheduler / motivation / holiday 等执行数）
- AI Life（9.3）：活动执行数 + 兴趣条数（life_activity_logs / life_interests）
- Agent（9.4）：工具执行成功率 + Loop 步数（agent_task_logs / agent_tasks）

用法：
  cd <项目根>/backend
  .venv/Scripts/python.exe ../scripts/evaluate_behavior_benchmark.py [--days 7]
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 允许从 backend 导入 app 包（sqlalchemy 等依赖在 backend venv）
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


async def main(days: int) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import select, func
    from app.db.database import async_session_factory
    from app.models.agent_task_log import AgentTaskLog
    from app.models.agent_task import AgentTask
    from app.models.life import LifeActivityLog, LifeInterest

    since = datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=days)
    async with async_session_factory() as db:
        logs = (await db.execute(
            select(AgentTaskLog).where(AgentTaskLog.created_at >= since)
        )).scalars().all()
        tasks = (await db.execute(
            select(AgentTask).where(AgentTask.created_at >= since)
        )).scalars().all()
        # life_activity_logs 无时间列：全量统计
        acts = (await db.execute(select(LifeActivityLog))).scalars().all()
        interests = (await db.execute(select(LifeInterest))).scalars().all()

    # Agent：工具执行成功率
    ok = sum(1 for l in logs if l.status == "ok")
    total = len(logs)
    # 关系：主动消息执行（trigger=scheduler 为主动行为入口）
    proactive = [l for l in logs if l.trigger == "scheduler"]
    # Loop 步数（agent_tasks 有 progress_json？按 status 分布）
    task_ok = sum(1 for t in tasks if t.status == "done")
    task_total = len(tasks)
    # AI Life
    life_done = sum(1 for a in acts if a.status == "completed")

    print(f"# 行为基准（近 {days} 天，零 LLM）")
    print()
    print("## Agent（9.4）")
    print(f"- 工具执行总次数：{total}，成功（ok）：{ok}，成功率：{ok / total * 100:.1f}%" if total else "- 工具执行总次数：0")
    print(f"- 任务总数：{task_total}，完成（done）：{task_ok}，完成率：{task_ok / task_total * 100:.1f}%" if task_total else "- 任务总数：0")
    print()
    print("## 关系（9.2）")
    print(f"- 主动行为执行数（近 {days} 天，trigger=scheduler）：{len(proactive)}")
    if proactive:
        print(f"- 平均每角色主动次数：{len(proactive) / len({l.character_id for l in proactive}):.1f}（目标参考 3-7 次/周可配置）")
    print()
    print("## AI Life（9.3）")
    print(f"- Life 活动总数：{len(acts)}，已完成：{life_done}")
    print(f"- 兴趣条目总数：{len(interests)}（变化链 trace 见 events interest.updated）")
    print()
    print("*数据源：agent_task_logs / agent_tasks / life_activity_logs / life_interests；指标可扩展。*")


if __name__ == "__main__":
    import asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    asyncio.run(main(args.days))
