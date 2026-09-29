"""life_followup 缓冲管理（设计稿 §10.4b）。

停用说明（2026-09-29，断点 #12）：本缓冲自即日起停用——life_loop 的两处写入点
（play_game 结算、记忆配额未满）已删除，全仓无任何读取方（pop_followups 零调用、
无定时任务/调度器轮询、无 API 端点、无前端调用），线上实态 32 行全部 status='pending'、
0 行被消费过。核实证据见
output/AMBRACE_断点12_LifeFollowup核实_20260929.md。
真正让角色回聊的是记忆沉淀（section_overlay 读 Memory 表）与主动分享（life_share
读事件 payload 里的 summary），两者都不经过本表。
本模块与 life_followups 表保留作留痕（历史行不删、不写迁移），**勿再新增写点**；
将来若真做「下次上线主动回聊」，需先解决配额闸不判过期的问题（pending 永不减少 ⇒
写满 3 条后 add_followup 永久静默 no-op），再接 pop_followups。
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.models.life import LifeFollowup


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def add_followup(
    db, character_id: int, user_id: int, summary: str,
    action: str, memory_id: int | None, window: str = "next_online",
) -> LifeFollowup | None:
    """动作完成后写入回聊缓冲。每角色每窗口最多 3 条 pending。"""
    count = (await db.execute(
        select(LifeFollowup).where(
            LifeFollowup.character_id == character_id,
            LifeFollowup.trigger_window == window,
            LifeFollowup.status == "pending",
        )
    )).scalars().all()
    if len(count) >= 3:
        return None
    f = LifeFollowup(
        character_id=character_id, user_id=user_id,
        summary=summary[:300], action=action,
        memory_id=memory_id, trigger_window=window,
        not_before=_now() + timedelta(hours=1),
    )
    db.add(f)
    await db.commit()
    return f


async def pop_followups(
    db, character_id: int, window: str, limit: int = 1,
) -> list[LifeFollowup]:
    """时机窗口触发时取出 pending 回聊素材（早安/夜间复盘/下次上线）。"""
    rows = (await db.execute(
        select(LifeFollowup).where(
            LifeFollowup.character_id == character_id,
            LifeFollowup.trigger_window == window,
            LifeFollowup.status == "pending",
            LifeFollowup.not_before <= _now(),
        ).order_by(LifeFollowup.created_at.asc()).limit(limit)
    )).scalars().all()
    for r in rows:
        r.status = "used"
        r.used_at = _now()
    await db.commit()
    return rows
