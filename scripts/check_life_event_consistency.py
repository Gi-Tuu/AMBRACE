# -*- coding: utf-8 -*-
"""事件一致性抽查脚本（S-2，2026-08-16）：Life 活动 → 织库/朋友圈痕迹健康度

事件驱动架构保证一致性（life.activity_completed → memory.written → 织库/朋友圈联动）。
本脚本从真实 DB 抽查最近 N 天：各角色 Life 活动数 vs 私·织库卡片 / AI 朋友圈动态，
输出覆盖率作为一致性健康度参考（不同活动类型落痕路径不同，不强制 100%）。

用法：
  cd <项目根>/backend
  .venv/Scripts/python.exe ../scripts/check_life_event_consistency.py [--days 7]
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
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.life import LifeActivityLog
    from app.models.weave_card import WeaveCard
    from app.models.moment import AIMoment

    since = datetime.now().astimezone().replace(tzinfo=None) - timedelta(days=days)
    async with async_session_factory() as db:
        # life_activity_logs 无时间列：全量统计（数量级小）
        acts = (await db.execute(select(LifeActivityLog))).scalars().all()
        cards = (await db.execute(
            select(WeaveCard).where(WeaveCard.created_at >= since)
        )).scalars().all()
        moments = (await db.execute(
            select(AIMoment).where(AIMoment.is_active == True, AIMoment.created_at >= since)
        )).scalars().all()

    by_char: dict[int, dict] = {}
    for a in acts:
        c = by_char.setdefault(a.character_id, {"acts": 0, "done": 0, "cards": 0, "moments": 0})
        c["acts"] += 1
        if a.status == "completed":
            c["done"] += 1
    for c_ in cards:
        by_char.setdefault(c_.character_id, {"acts": 0, "done": 0, "cards": 0, "moments": 0})["cards"] += 1
    for m in moments:
        by_char.setdefault(m.character_id, {"acts": 0, "done": 0, "cards": 0, "moments": 0})["moments"] += 1

    print(f"# Life 事件一致性抽查（近 {days} 天）")
    print()
    print("| 角色 | 活动数 | 已完成 | 私·织库卡片 | AI 朋友圈 | 痕迹覆盖率 |")
    print("|------|--------|--------|------------|-----------|-----------|")
    total_acts = total_traces = 0
    for cid, v in sorted(by_char.items()):
        acts = v["done"]
        traces = v["cards"] + v["moments"]
        total_acts += acts
        total_traces += traces
        rate = f"{traces / acts * 100:.0f}%" if acts else "-"
        print(f"| {cid} | {v['acts']} | {acts} | {v['cards']} | {v['moments']} | {rate} |")
    if total_acts:
        print()
        print(f"合计：已完成活动 {total_acts}，痕迹（织库卡片+朋友圈）{total_traces}，"
              f"整体覆盖率 {total_traces / total_acts * 100:.0f}%")
    print()
    print("*说明：不同活动类型落痕路径不同（browse/create → 织库；社交 → 朋友圈），覆盖率非 100% 不代表事件缺失，需结合 activity_type 细分。*")


if __name__ == "__main__":
    import asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    asyncio.run(main(args.days))
