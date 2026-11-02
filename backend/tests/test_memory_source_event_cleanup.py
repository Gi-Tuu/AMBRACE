# -*- coding: utf-8 -*-
"""M2 2a cleanup: character cascade deletes the new proof tables (no orphan)."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.character_cascade import cascade_delete_character
from app.models.base import Base
from sqlalchemy import event, text

from app.models.memory import (
    Memory,
    MemoryWriteReceipt,
    MemoryReallowGrant,
    MemoryRestoreQuarantine,
    MemorySourceEvent,
    MemoryTombstone,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_cascade_removes_source_tombstone_grant_quarantine(tmp_path):
    async def run():
        eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
        @event.listens_for(eng.sync_engine, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(eng, expire_on_commit=False)
        async with factory() as db:
            await db.execute(text("INSERT INTO users(id,username,nickname,password_hash,created_at) VALUES (7,'u','n','x','2026-01-01 00:00:00')"))
            await db.execute(text("INSERT INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled,created_at) VALUES (11,7,'c',0,0,1,0,0,'2026-01-01 00:00:00')"))
            db.add(MemorySourceEvent(
                event_uid="evt-1", kind="chat_message", source_row_id=2001,
                generation=1, seq=1, owner_user_id=7, character_id=11,
                event_at=_now(), content_sha256="a" * 64, deleted=False,
                created_at=_now()))
            await db.flush()
            db.add(Memory(id=9001, user_id=7, character_id=11, memory_type="event",
                          content="synthetic", source_event_uid="evt-1"))
            await db.flush()
            db.add(MemoryWriteReceipt(
                character_id=11, memory_id=9001, action="create", source_event_uid="evt-1",
                owner_user_id=7, actor_kind="authenticated_user", actor_user_id=7,
                envelope_version=1, authoritative=True, idempotency_key="cleanup",
                created_at=_now()))
            db.add(MemoryTombstone(
                memory_id=9001, owner_user_id=7, character_id=11, epoch=1,
                revoked_seq=1, confirm_seq=0, state="closed", created_at=_now()))
            db.add(MemoryReallowGrant(
                token_hash="h" * 64, memory_id=9001, owner_user_id=7,
                character_id=11, epoch=1, confirm_seq=1, used=False,
                invalidated=False, created_at=_now()))
            db.add(MemoryRestoreQuarantine(
                event_uid="evt-1", owner_user_id=7, character_id=11,
                reason="backup", restored_at=_now()))
            await db.commit()
        async with factory() as db:
            stats = await cascade_delete_character(db, 11)
            await db.commit()
        async with factory() as db:
            for table in ("memory_source_events", "memories", "memory_tombstones",
                          "memory_reallow_grants", "memory_restore_quarantine"):
                n = (await db.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
                assert n == 0, (table, n)
        await eng.dispose()
        return stats

    stats = asyncio.run(run())
    assert stats.get("MemorySourceEvent", 0) >= 1
    assert stats.get("MemoryTombstone", 0) >= 1
    assert stats.get("MemoryReallowGrant", 0) >= 1
