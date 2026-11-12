# -*- coding: utf-8 -*-
"""P1-2 合同：向量内容一致性（flag `vector_content_check_v1` 默认关）。

判据：
- 写入侧 metadata 必带 `content_sha256`（add/upsert 两路）；
- 对账纯函数只报「metadata 哈希 ≠ 库内 content 哈希」的 id（无哈希/无库内行跳过）；
- `collect_stale` 真读向量库 metadata（用假 collection 注入）并与库内 content 比对；
- tick 在 flag 开时才调漂移修复，flag 关＝行为不变。
"""
import asyncio

from sqlalchemy import text

UID, CID = 9771, 9772
FLAG = "vector_content_check_v1"


def _fake_collection(ids, metadatas):
    class _C:
        def get(self, **_kw):
            return {"ids": ids, "metadatas": metadatas}

        def add(self, **_kw):
            return None

        def upsert(self, **_kw):
            return None

    return _C()


async def _seed():
    from app.db.database import async_session_factory

    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
            "VALUES (:u,:n,'n','x')"), {"u": UID, "n": f"p12_{UID}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
            "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": CID, "u": UID})
        await db.commit()


async def _make(content: str) -> int:
    from app.db.database import async_session_factory
    from app.models.memory import Memory

    async with async_session_factory() as db:
        m = Memory(user_id=UID, character_id=CID, memory_type="event", source="chat",
                   source_id=1, content=content, epistemic_status="FACT",
                   scope="private", importance=50.0)
        db.add(m)
        await db.commit()
        return int(m.id)


def test_content_sha256_deterministic():
    from app.memory.vector_consistency import content_sha256

    a = content_sha256("同一段内容")
    assert a == content_sha256("同一段内容") and a != content_sha256("换一段内容")
    assert len(a) == 64


def test_stale_among_pure():
    from app.memory.vector_consistency import content_sha256, stale_among

    meta = {1: content_sha256("甲"), 2: content_sha256("乙"), 3: "", 4: content_sha256("丁")}
    db = {1: "甲", 2: "乙已改", 3: "丙", }
    assert stale_among(meta, db) == [2], "只报哈希不一致；空哈希与缺库内行跳过"


def test_write_metadata_carries_hash(monkeypatch):
    from app.db import vector_store as vs
    from app.memory.vector_consistency import content_sha256

    captured = {}

    class _C:
        # 真实 chroma 客户端是同步方法（vector_store 用 asyncio.to_thread 包），假件必须同步
        def add(self, **kw):
            captured["add"] = kw

        def upsert(self, **kw):
            captured["upsert"] = kw

    async def _fake_get():
        return _C()

    monkeypatch.setattr(vs, "get_or_create_collection", _fake_get)
    asyncio.run(vs.add_memory(memory_id=1, character_id=2, memory_type="event",
                              content="内容甲", embedding=[0.0], user_id=UID))
    asyncio.run(vs.upsert_memory_vector(memory_id=1, character_id=2, memory_type="event",
                                        content="内容甲", embedding=[0.0], user_id=UID))
    assert captured["add"]["metadatas"][0]["content_sha256"] == content_sha256("内容甲")
    assert captured["upsert"]["metadatas"][0]["content_sha256"] == content_sha256("内容甲")


def test_collect_stale_uses_db_content(monkeypatch):
    from app.db import vector_store as vs
    from app.memory import vector_consistency as vc

    asyncio.run(_seed())
    mid_ok = asyncio.run(_make("内容一致"))
    mid_drift = asyncio.run(_make("内容已改"))
    ids = [str(mid_ok), str(mid_drift)]
    metas = [{"content_sha256": vc.content_sha256("内容一致")},
             {"content_sha256": vc.content_sha256("旧内容")}]

    async def _fake_get():
        return _fake_collection(ids, metas)

    monkeypatch.setattr(vs, "get_or_create_collection", _fake_get)
    stale = asyncio.run(vc.collect_stale(limit=10))
    assert stale == [mid_drift], stale


def test_flag_default_off_and_tick_wiring():
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import vector_consistency as vc

    assert AGENT_FLAGS.get(FLAG) is False, "默认关＝现网零行为变化"
    assert vc.FLAG == FLAG and vc.META_KEY == "content_sha256"
    from pathlib import Path
    watch = (Path(__file__).resolve().parents[1] / "app" / "application" / "vector_sync_watch.py").read_text(encoding="utf-8")
    assert "vector_consistency" in watch and "repair_content_drift" in watch
