"""AI 伙伴生活 API（Life Engine v2，2026-08-12）

- GET /api/v1/life/timeline：AI 生活时间线（source=life 记忆，角色详情页-角色生活只读）
- GET /api/v1/life/state：Life State（energy/focus/needs/phase）
- GET /api/v1/life/artifacts：AI 生活产物库（Phase 2：创作/浏览/学习产物）
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import async_session_factory
from app.models.life import LifeArtifact, LifeGoal, LifeInterest
from app.models.memory import Memory

router = APIRouter(prefix="/api/v1/life", tags=["Life"])


@router.get("/timeline")
async def life_timeline(
    character_id: int | None = None,
    limit: int = Query(50, le=200),
    user_id: int = Depends(get_current_user_id),
):
    """AI 生活时间线（source=life 记忆，时间倒序；指定角色则只返回该角色）"""
    async with async_session_factory() as db:
        cond = [Memory.user_id == user_id, Memory.source == "life"]
        if character_id is not None:
            cond.append(Memory.character_id == character_id)
        rows = (
            await db.execute(
                select(Memory)
                .where(*cond)
                .order_by(Memory.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        items = [
            {
                "id": m.id,
                "character_id": m.character_id,
                "sub_type": m.sub_type or "life_event",
                "content": m.content,
                "importance": round(float(m.importance or 0), 1),
                "created_at": m.created_at,
            }
            for m in rows
        ]
    return {"items": items, "total": len(items)}


@router.get("/state")
async def life_state(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """Life State 查看（energy/focus/needs/phase）"""
    from app.life.life_state import get_life_state
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        c = await db.get(AICharacter, character_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        st = await get_life_state(db, character_id)
        import json

        needs = json.loads(st.needs_json or "{}")
    return {
        "character_id": character_id,
        "energy": st.energy,
        "focus": st.focus,
        "needs": needs,
        "phase": st.phase,
        "last_tick_at": st.last_tick_at,
    }
@router.get("/artifacts")
async def life_artifacts(
    character_id: int | None = None,
    type: str | None = None,
    limit: int = Query(50, le=200),
    user_id: int = Depends(get_current_user_id),
):
    """AI 生活产物库（Phase 2）：创作/浏览/学习产物列表（时间倒序，可按角色/类型过滤）"""
    async with async_session_factory() as db:
        cond = [LifeArtifact.user_id == user_id]
        if character_id is not None:
            cond.append(LifeArtifact.character_id == character_id)
        if type:
            cond.append(LifeArtifact.type == type)
        rows = (
            await db.execute(
                select(LifeArtifact)
                .where(*cond)
                .order_by(LifeArtifact.created_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        items = [
            {
                "id": a.id,
                "character_id": a.character_id,
                "type": a.type,
                "title": a.title or "",
                "content_text": a.content_text,
                "content_url": a.content_url,
                "metadata": __import__("json").loads(a.metadata_json or "{}"),
                "source_activity": a.source_activity or "",
                "created_at": a.created_at,
            }
            for a in rows
        ]
    return {"items": items, "total": len(items)}
@router.get("/interests")
async def life_interests(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """AI 生活兴趣（Phase 3）：角色兴趣列表（按等级降序）"""
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        c = await db.get(AICharacter, character_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        rows = (
            await db.execute(
                select(LifeInterest)
                .where(LifeInterest.character_id == character_id)
                .order_by(LifeInterest.level.desc())
            )
        ).scalars().all()
        items = [
            {
                "id": it.id,
                "name": it.name,
                "level": it.level,
                "status": it.status or "active",
                "source": it.source or "",
                "last_engaged_at": it.last_engaged_at,
            }
            for it in rows
        ]
    return {"items": items, "total": len(items)}


@router.get("/goals")
async def life_goals(
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """AI 生活目标（Phase 3）：角色目标列表（active 优先，按优先级降序）"""
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        c = await db.get(AICharacter, character_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        rows = (
            await db.execute(
                select(LifeGoal)
                .where(LifeGoal.character_id == character_id)
                .order_by(LifeGoal.status.asc(), LifeGoal.priority.desc(), LifeGoal.created_at.desc())
            )
        ).scalars().all()
        items = [
            {
                "id": g.id,
                "type": g.type,
                "title": g.title,
                "description": g.description or "",
                "priority": g.priority,
                "progress": g.progress,
                "progress_total": g.progress_total,
                "status": g.status or "active",
                "related_user": bool(g.related_user),
                "deadline": g.deadline,
                "created_at": g.created_at,
                "completed_at": g.completed_at,
            }
            for g in rows
        ]
    return {"items": items, "total": len(items)}


@router.get("/browsing")
async def life_browsing(
    character_id: int | None = None,
    limit: int = Query(50, le=200),
    user_id: int = Depends(get_current_user_id),
):
    """AI 真实浏览记录（Phase B，2026-08-14）：browse/learn 活动的 trace（真实 URL）按时间倒序；无 URL 的旧记录不展示"""
    import json as _json
    from app.models.life import LifeActivityLog

    async with async_session_factory() as db:
        # P0-7 安全加固（2026-08-16）：强制按用户归属过滤（LifeActivityLog 无 user_id 列，join AICharacter）
        from app.models.character import AICharacter
        cond = [
            LifeActivityLog.status == "completed",
            LifeActivityLog.activity_type.in_(["browse", "learn"]),
            AICharacter.id == LifeActivityLog.character_id,
            AICharacter.user_id == user_id,
        ]
        if character_id is not None:
            cond.append(LifeActivityLog.character_id == character_id)
        rows = (
            await db.execute(
                select(LifeActivityLog)
                .join(AICharacter, AICharacter.id == LifeActivityLog.character_id)
                .where(*cond)
                .order_by(LifeActivityLog.completed_at.desc())
                .limit(max(1, min(limit, 200)))
            )
        ).scalars().all()
        items = []
        for r in rows:
            try:
                out = _json.loads(r.output_json or "{}")
            except Exception:
                out = {}
            tr = out.get("trace") or {}
            if not tr.get("url"):
                continue
            items.append({
                "id": r.id,
                "character_id": r.character_id,
                "activity_type": r.activity_type,
                "title": str(tr.get("title") or "")[:120],
                "url": str(tr.get("url") or ""),
                "duration_sec": int(tr.get("duration_sec") or 0),
                "summary": str(out.get("summary") or "")[:200],
                "created_at": r.completed_at or r.started_at,
            })
    return {"items": items, "total": len(items)}


@router.get("/schedules")
async def life_schedules(
    character_id: int,
    date: str | None = None,
    limit: int = Query(30, le=100),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """AI 日程（Phase B-2，2026-08-14）：三来源（固定作息/Goal 推导/AI 自生成）按开始时间倒序；date=YYYY-MM-DD 按北京时间过滤"""
    from app.life.schedule import list_schedules
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        c = await db.get(AICharacter, character_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "character_not_found"))
        rows = await list_schedules(db, character_id, date_str=date, limit=limit)
        items = [
            {
                "id": s.id,
                "character_id": s.character_id,
                "title": s.title,
                "description": s.description or "",
                "start_time": s.start_time,
                "end_time": s.end_time,
                "status": s.status,
                "priority": s.priority,
                "source": s.source,
                "recurrence": s.recurrence,
                "created_at": s.created_at,
                "completed_at": s.completed_at,
            }
            for s in rows
        ]
    return {"items": items, "total": len(items)}


@router.get("/shared")
async def shared_events(
    character_id: int | None = None,
    limit: int = Query(20, le=50),
    user_id: int = Depends(get_current_user_id),
):
    """共同经历（Phase C，2026-08-14）：用户与 AI 共同经历（触发：记住/第一次/纪念日等）"""
    from app.memory.shared_events import list_shared_events

    async with async_session_factory() as db:
        rows = await list_shared_events(db, user_id, character_id=character_id, limit=limit)
        items = [
            {
                "id": e.id,
                "character_id": e.character_id,
                "event_type": e.event_type,
                "category": e.category,
                "title": e.title,
                "description": e.description,
                "importance": round(float(e.importance or 0), 2),
                "is_anniversary": bool(e.is_anniversary),
                "event_time": e.event_time,
            }
            for e in rows
        ]
    return {"items": items, "total": len(items)}


@router.post("/schedules/{schedule_id}/complete")
async def complete_schedule(
    schedule_id: int,
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """标记日程完成（AI 已完成对应事项）"""
    from datetime import datetime, timezone as _tz
    from app.models.life import LifeSchedule
    from app.models.character import AICharacter as _AIC

    async with async_session_factory() as db:
        s = await db.get(LifeSchedule, schedule_id)
        _owned = (await db.execute(
            select(_AIC).where(_AIC.id == character_id, _AIC.user_id == user_id)
        )).scalar_one_or_none()
        if _owned is None:
            raise HTTPException(status_code=404, detail="character not found")
        if s is None or s.character_id != character_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "schedule_not_found"))
        s.status = "completed"
        s.completed_at = datetime.now(_tz.utc).replace(tzinfo=None)
        await db.commit()
    return {"ok": True, "id": schedule_id, "status": "completed"}


@router.post("/schedules/{schedule_id}/cancel")
async def cancel_schedule(
    schedule_id: int,
    character_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """取消日程（被更高优先级取代/用户忽略）"""
    from app.models.life import LifeSchedule
    from app.models.character import AICharacter as _AIC

    async with async_session_factory() as db:
        _owned = (await db.execute(
            select(_AIC).where(_AIC.id == character_id, _AIC.user_id == user_id)
        )).scalar_one_or_none()
        if _owned is None:
            raise HTTPException(status_code=404, detail="character not found")
        s = await db.get(LifeSchedule, schedule_id)
        if s is None or s.character_id != character_id:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "schedule_not_found"))
        s.status = "cancelled"
        await db.commit()
    return {"ok": True, "id": schedule_id, "status": "cancelled"}
