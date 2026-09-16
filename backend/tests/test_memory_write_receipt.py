# -*- coding: utf-8 -*-
"""#70 附录 C 可选 M3：记忆写入回执测试（2026-09-15）。

纪律：全程走 conftest 的会话沙箱库（DATABASE_URL 已指向 tmp_path），不碰真实库。
覆盖：flag 默认关；关时零写入；开时异步写入且字段正确；写失败静默；写入点接线守卫。
"""
import asyncio
import io


def test_flag_default_off():
    from app.agent import loop as _loop
    assert _loop.AGENT_FLAGS.get("memory_write_receipt", False) is False


async def _clear():
    from sqlalchemy import delete
    from app.db.database import async_session_factory
    from app.models.memory import MemoryWriteReceipt
    async with async_session_factory() as db:
        await db.execute(delete(MemoryWriteReceipt))
        await db.commit()


async def _rows():
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.models.memory import MemoryWriteReceipt
    async with async_session_factory() as db:
        return (await db.execute(select(MemoryWriteReceipt))).scalars().all()


def test_emit_noop_when_flag_off(monkeypatch):
    from app.agent import loop as _loop
    from app.memory import receipt as R

    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_write_receipt", False)

    async def _run():
        await _clear()
        for _ in range(5):
            R.emit_memory_receipt(11, 222, R.ACTION_CREATE, reason="should not be written")
            await asyncio.sleep(0.05)
        return len(await _rows())

    assert asyncio.run(_run()) == 0


def test_emit_writes_row_when_flag_on(monkeypatch):
    from app.agent import loop as _loop
    from app.memory import receipt as R

    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_write_receipt", True)

    async def _run():
        await _clear()
        R.emit_memory_receipt(11, 222, R.ACTION_MERGE, reason="unit test", detail={"kind": "merge"})
        for _ in range(60):
            rows = await _rows()
            if rows:
                return rows
            await asyncio.sleep(0.05)
        return await _rows()

    rows = asyncio.run(_run())
    assert len(rows) == 1
    r = rows[0]
    assert r.action == "merge"
    assert r.character_id == 11
    assert r.memory_id == 222
    assert r.reason == "unit test"
    assert "merge" in (r.detail_json or "")


def test_emit_swallows_db_errors(monkeypatch):
    from app.agent import loop as _loop
    from app.memory import receipt as R
    from app.db import database as _db

    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_write_receipt", True)

    class _Boom:
        def __call__(self, *a, **kw):
            raise RuntimeError("boom")

    monkeypatch.setattr(_db, "async_session_factory", _Boom())

    async def _run():
        R.emit_memory_receipt(1, 2, R.ACTION_CREATE, reason="must not raise")
        await asyncio.sleep(0.2)
        return True

    assert asyncio.run(_run()) is True


def test_write_points_wired():
    """接线守卫：create 1 + merge 3 + reject 2 落在 write.py；supersede / stale 落在 supersede.py。"""
    root = "D:/AMBRACE/backend/app/memory/"
    w = io.open(root + "write.py", encoding="utf-8").read()
    s = io.open(root + "supersede.py", encoding="utf-8").read()
    assert w.count("emit_memory_receipt(") >= 6
    assert "ACTION_REJECT" in w
    assert "ACTION_MERGE" in w
    assert "ACTION_CREATE" in w
    assert "ACTION_SUPERSEDE" in s
    assert "ACTION_STALE" in s
