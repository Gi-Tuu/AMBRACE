"""memory.maintain（F4 拆分，2026-08-31 自 service.py 迁入）。

接缝已摘除（2026-09-02）：改为顶部/函数顶显式 import（async_session_factory/
delete_memory_vector/bm25_invalidate 等可被 monkeypatch(service, ...) 的名字于函数顶延迟
import 自 app.memory.service，调用时解析；其余稳定名字模块级 import）。
不再经 _sync_seams 把 service 命名空间同步进 globals。
"""
from sqlalchemy import select

from app.models.memory import Memory
from app.memory.decay import retention_pct
from app.memory.service import (
    S_DEFAULT,
    _active_status_clause,
    _logger,
    _now_naive,
    star_from_pct,
)


async def list_memories(
    user_id: int | None = None,
    character_id: int | None = None,
    memory_type: str | None = None,
    skip: int = 0,
    limit: int = 800,
) -> tuple[list[dict], int]:
    """列出记忆（按 importance 排序）；顺带惰性衰减。返回 (切片结果, 总数)。

    重构说明（2026-08-03）：原实现逐条开 session + commit（N+1，3000 条记忆时
    数十秒），且 _apply_decay 的 commit 使 ORM 属性过期、session 关闭后再访问
    抛 DetachedInstanceError（GET /api/v1/memories 500）。改为：单 session 内
    snapshot 全部字段，衰减批量落库（一次 commit），session 外只操作快照。
    """
    from datetime import datetime
    from app.memory.service import async_session_factory, delete_memory_vector

    now = _now_naive()
    async with async_session_factory() as db:
        query = select(Memory).where(Memory.is_archived == False, _active_status_clause())
        # M3-a（2026-09-01）：工作记忆行不进常规记忆列表（注入走 M1-c 预留分区，M3-b 另行灰度）
        if memory_type != "working_state":
            query = query.where(Memory.memory_type != "working_state")
        if user_id is not None:
            # 严格按用户隔离（置顶摘要写入时 user_id 已为角色拥有者）
            query = query.where(Memory.user_id == user_id)
        if character_id:
            query = query.where(Memory.character_id == character_id)
        if memory_type:
            query = query.where(Memory.memory_type == memory_type)

        result = await db.execute(query)
        memories = result.scalars().all()

        # snapshot 字段（session 内取值，之后不再碰 ORM 对象，避免 detached 访问）
        rows = [{
            "id": m.id, "user_id": m.user_id, "character_id": m.character_id,
            "memory_type": m.memory_type, "sub_type": m.sub_type,
            "source": m.source, "source_id": m.source_id,
            "title": m.title, "content": m.content,
            "importance": float(m.importance or 0),
            "is_pinned": bool(m.is_pinned),
            "is_locked": bool(m.is_locked),
            "is_archived": bool(m.is_archived),
            "delete_at": m.delete_at, "created_at": m.created_at,
            "updated_at": m.updated_at, "decay_base_at": m.decay_base_at,
            "why_it_matters": m.why_it_matters,
            "strength_days": float(m.strength_days or S_DEFAULT),
            "last_reinforce_at": m.last_reinforce_at,
            "next_review_at": m.next_review_at,
            "review_count": int(m.review_count or 0),
            "_obj": m,
        } for m in memories]

        # 惰性衰减展示（艾宾浩斯）：读列表只计算实时保留率用于展示，不写库、不刷新遗忘起点
        #（真正的结算+刷新起点由 6h 定时 run_memory_decay 负责；读列表刷新起点会让遗忘停滞）
        # 2026-08-08 修复：原实现读一次列表=全部记忆轻复习，97% 记忆 last_reinforce_at 恒为近 1 天，
        # 遗忘曲线几乎不可感知；展示值实时按 R=exp(-Δt/S) 计算，仅到期删除落库。
        removed_ids = set()
        for row in rows:
            if row["is_pinned"] or row["is_locked"]:
                continue
            obj = row["_obj"]
            if row["delete_at"] is not None:
                if now >= row["delete_at"]:
                    removed_ids.add(row["id"])
                    await db.delete(obj)
                continue
            base = row["last_reinforce_at"] or row["decay_base_at"] or row["created_at"]
            if base is None:
                continue
            if isinstance(base, datetime) and base.tzinfo is not None:
                base = base.replace(tzinfo=None)
            dt_days = (now - base).total_seconds() / 86400.0
            if dt_days <= 0:
                continue
            pct = retention_pct(dt_days, row["strength_days"])
            row["importance"] = pct
        if removed_ids:
            await db.commit()

    # 过期删除的同步清理向量（避免孤儿向量）
    if removed_ids:
        for mid in removed_ids:
            try:
                await delete_memory_vector(mid)
            except Exception:
                pass

    alive = [r for r in rows if r["id"] not in removed_ids]
    alive.sort(key=lambda r: (r["is_pinned"], star_from_pct(r["importance"]), r["created_at"] or now), reverse=True)
    total = len(alive)
    if limit:
        alive = alive[skip:skip + limit]
    else:
        alive = alive[skip:]
    _logger.debug("List memories: char=%s type=%s count=%d total=%d", character_id, memory_type, len(alive), total)
    return [
        {
            "id": r["id"], "user_id": r["user_id"], "character_id": r["character_id"],
            "memory_type": r["memory_type"], "sub_type": r["sub_type"],
            "source": r["source"], "source_id": r["source_id"],
            "title": r["title"], "content": r["content"],
            "importance": star_from_pct(r["importance"]),
            "importance_pct": round(r["importance"], 1),
            "strength_days": r["strength_days"],
            "last_reinforce_at": r["last_reinforce_at"].isoformat() if r["last_reinforce_at"] else None,
            "next_review_at": r["next_review_at"].isoformat() if r["next_review_at"] else None,
            "review_count": r["review_count"],
            "delete_at": r["delete_at"].isoformat() if r["delete_at"] else None,
            "why_it_matters": r.get("why_it_matters"),
            "is_archived": r["is_archived"],
            "is_pinned": r["is_pinned"],
            "is_locked": r["is_locked"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in alive
    ], total

async def delete_memory(memory_id: int) -> bool:
    """删除记忆（软删除 + 删除向量）"""
    from app.memory.service import async_session_factory, bm25_invalidate, delete_memory_vector
    async with async_session_factory() as db:
        result = await db.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory:
            memory.is_archived = True
            # 织库（2026-08-12）：参与记忆被删 → 关联卡片置脏，generate 时懒重建
            try:
                from app.models.memory import WeaveCard, WeaveCardMemory
                from sqlalchemy import update
                await db.execute(
                    update(WeaveCard)
                    .where(
                        WeaveCard.id.in_(
                            select(WeaveCardMemory.card_id).where(WeaveCardMemory.memory_id == memory_id)
                        )
                    )
                    .values(is_stale=True)
                )
            except Exception:
                pass
            await db.flush()
            await db.commit()
            try:
                await delete_memory_vector(memory_id)
            except Exception:
                pass
            # 检索增强（2026-08-23）：记忆已从角色软删 → 使 BM25 索引失效（下次检索懒重建）
            bm25_invalidate(memory.character_id)
            return True
        return False
