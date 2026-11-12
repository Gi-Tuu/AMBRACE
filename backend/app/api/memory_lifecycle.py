# -*- coding: utf-8 -*-
"""RC2 M3/M4：「记忆生命周期」只读投影端点（2026-11-13）。

M3 读投影：把「这条记忆现在处于哪个生命周期位置」投影到读侧——
version／superseded_by／valid_to／epistemic／来源事件数／写入回执（create/update/supersede）
／派生关系计数；M4 生命周期读侧：取代与失效状态一眼可见（不做任何写操作）。

口径（与 G5 `/why` 同族）：
- 只读、零新表、零写；只读 memories 现有列 ＋ memory_source_events ＋ memory_write_receipt；
- 租户隔离走 tenant_scope_ids，404 同形；
- flag `memory_lifecycle_view_v1` 默认关 ⇒ 路由返回 404（未开放的读面不暴露）。
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.application.tenant_service import tenant_scope_ids
from app.auth.deps import get_current_user_id
from app.db import database as db_mod
from app.i18n import tr_lang
from app.models.memory import Memory, MemorySourceEvent, MemoryWriteReceipt

from app.api.memories import router as memory_router

router = memory_router  # 与 G5 同法：挂到既有 /api/v1/memories 路由


class LifecycleReceipt(BaseModel):
    action: str
    reason: str | None = None
    created_at: str | None = None


class MemoryLifecycleResponse(BaseModel):
    status: str
    memory_id: int
    version: int = 0
    superseded_by: int | None = None
    valid_to: str | None = None
    epistemic_status: str | None = None
    source: str | None = None
    is_archived: bool = False
    source_event_uid: str | None = None
    source_event_count: int = 0
    receipts: list[LifecycleReceipt] = []
    receipt_count: int = 0
    revision_count: int = 0
    derived_count: int = 0


def _flag_on() -> bool:
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("memory_lifecycle_view_v1", False))
    except Exception:  # noqa: BLE001 —— 读旗标失败按关处理
        return False


@router.get("/{memory_id}/lifecycle", response_model=MemoryLifecycleResponse)
async def get_memory_lifecycle(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """M3/M4：记忆生命周期只读投影（flag 关不暴露）。"""
    if not _flag_on():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))
    async with db_mod.async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)
        mem = (await db.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id.in_(scope_ids),
            )
        )).scalar_one_or_none()
        if mem is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))

        receipt_rows = (await db.execute(
            select(MemoryWriteReceipt).where(MemoryWriteReceipt.memory_id == memory_id)
            .order_by(MemoryWriteReceipt.id.desc()).limit(20)
        )).scalars().all()
        receipts = [
            LifecycleReceipt(
                action=r.action,
                reason=r.reason,
                created_at=r.created_at.isoformat() if getattr(r, "created_at", None) else None,
            )
            for r in receipt_rows
        ]
        revision_count = sum(1 for r in receipt_rows
                             if r.action == "update" and (r.reason or "") == "content edited")
        event_count = 0
        if mem.source_event_uid:
            event_count = int((await db.execute(
                select(func.count()).select_from(MemorySourceEvent).where(
                    MemorySourceEvent.event_uid == mem.source_event_uid)
            )).scalar() or 0)
        derived_count = int((await db.execute(
            select(func.count()).select_from(Memory).where(
                Memory.related_memory_id == memory_id,
                Memory.user_id.in_(scope_ids),
            )
        )).scalar() or 0)
        return MemoryLifecycleResponse(
            status="ok",
            memory_id=memory_id,
            version=int(mem.version or 0),
            superseded_by=mem.superseded_by,
            valid_to=mem.valid_to.isoformat() if mem.valid_to else None,
            epistemic_status=mem.epistemic_status,
            source=mem.source,
            is_archived=bool(mem.is_archived),
            source_event_uid=mem.source_event_uid,
            source_event_count=event_count,
            receipts=receipts,
            receipt_count=len(receipts),
            revision_count=revision_count,
            derived_count=derived_count,
        )
