# -*- coding: utf-8 -*-
"""RC2 G5：「为什么记住」来源链端点（只读）。

回答"这条记忆是怎么来的"：memory → source_event_uid → 来源事件（kind/时间/会话）
→ 签名消息正文（chat_message 类）→ 写入回执（若有）。与 derivations/history 同族：
只读、租户隔离走 tenant_scope_ids、404 同形、无 owner/character 参数信任。
"""
from __future__ import annotations


from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.application.tenant_service import tenant_scope_ids
from app.auth.deps import get_current_user_id
from app.db import database as db_mod
from app.i18n import tr_lang
from app.models.chat import ChatMessage
from app.models.memory import Memory, MemorySourceEvent
from app.models.memory import MemoryWriteReceipt

from app.api.memories import router as memory_router

router = memory_router  # G5：挂到既有 /api/v1/memories 路由（免改 main ROUTERS 清单）


class WhySource(BaseModel):
    kind: str
    source_row_id: int | None = None
    session_id: int | None = None
    event_at: str | None = None
    message_id: int | None = None
    message_excerpt: str | None = None
    message_created_at: str | None = None


class MemoryWhyResponse(BaseModel):
    status: str
    memory_id: int
    source_event_uid: str | None = None
    has_source: bool
    sources: list[WhySource] = []
    receipt_count: int = 0
    note: str = ""
    # P1-1（2026-11-11 架构治理，附加字段）：让前端区分「原始依据」与「当前内容已修订」
    memory_version: int = 0
    content_revised: bool = False
    content_edit_count: int = 0


@router.get("/{memory_id}/why", response_model=MemoryWhyResponse)
async def get_memory_why(
    memory_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """G5：记忆来源链（只读）。

    - 有 source_event_uid ⇒ 返回来源事件＋签名消息摘录＋回执数；
    - 无（老记忆/闸前写入）⇒ has_source=False、note 说明，不伪造来源；
    - 主体不存在或跨租户 → 404（与 derivations 同形）。
    """
    async with db_mod.async_session_factory() as db:
        scope_ids = await tenant_scope_ids(db, user_id)
        memory = (await db.execute(
            select(Memory).where(
                Memory.id == memory_id,
                Memory.user_id.in_(scope_ids),
            )
        )).scalar_one_or_none()
        if memory is None:
            raise HTTPException(status_code=404, detail=tr_lang(lang, "memory_not_found"))

        # P1-1：修订口径（version>1 或存在内容改写回执）
        _version = int(memory.version or 0)
        _edit_rows = (await db.execute(
            select(MemoryWriteReceipt).where(
                MemoryWriteReceipt.memory_id == memory_id,
                MemoryWriteReceipt.action == "update",
                MemoryWriteReceipt.reason == "content edited",
            )
        )).scalars().all()
        _edits = len(_edit_rows)
        _revised = _version > 1 or _edits > 0
        uid = memory.source_event_uid
        if not uid:
            return MemoryWhyResponse(
                status="ok", memory_id=memory_id, source_event_uid=None,
                has_source=False,
                note="no_source_event",   # 闸前写入/老记忆：无来源记录
                memory_version=_version,
                content_revised=_revised,
                content_edit_count=_edits,
            )
        events = (await db.execute(
            select(MemorySourceEvent).where(MemorySourceEvent.event_uid == uid)
        )).scalars().all()
        sources: list[WhySource] = []
        for e in events:
            excerpt = None
            created = None
            msg_id = None
            if e.kind == "chat_message" and e.source_row_id:
                msg = await db.get(ChatMessage, e.source_row_id)
                if msg is not None:
                    msg_id = msg.id
                    excerpt = (msg.content or "")[:200]
                    created = msg.created_at.isoformat() if msg.created_at else None
            sources.append(WhySource(
                kind=e.kind, source_row_id=e.source_row_id,
                session_id=e.session_id,
                event_at=e.event_at.isoformat() if e.event_at else None,
                message_id=msg_id, message_excerpt=excerpt,
                message_created_at=created,
            ))
        receipt_count = len((await db.execute(
            select(MemoryWriteReceipt).where(MemoryWriteReceipt.memory_id == memory_id)
        )).scalars().all())
        return MemoryWhyResponse(
            status="ok", memory_id=memory_id, source_event_uid=uid,
            has_source=True, sources=sources, receipt_count=receipt_count,
            memory_version=_version,
            content_revised=_revised,
            content_edit_count=_edits,
        )
