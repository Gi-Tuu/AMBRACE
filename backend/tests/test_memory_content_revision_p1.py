# -*- coding: utf-8 -*-
"""P1-1 合同：手动改内容的证据键闭环 + why 视图的修订口径（flag `memory_content_revision_v1` 默认关）。

判据：
- flag 关 ⇒ 逐字旧行为（write_key 不变、不发改写回执、version 仍 +1）；
- flag 开 ⇒ write_key 随内容重算（撞车则保留旧键）、发一条 `update/content edited` 回执；
- `/why` 始终返回 memory_version / content_revised / content_edit_count（无来源事件时也返回）。

注意：回执走 fire-and-forget（`spawn_background`），测试里 loop 一关就丢 ⇒
接线用 spy 验、why 计数用直接 await `_persist_receipt` 落一条真回执验。
"""
import asyncio
import types

from sqlalchemy import text

UID, CID = 9761, 9762
FLAG = "memory_content_revision_v1"


def _patch_side_effects(monkeypatch):
    """把向量重算/BM25 失效换成 no-op（本用例只验证据键与回执）。"""
    from app.memory import embedding as emb_mod
    from app.memory import bm25_index
    from app.db import vector_store as vs

    async def _fake_embed(_text):
        return [0.0] * 8

    async def _fake_upsert(**_kw):
        return None

    monkeypatch.setattr(emb_mod, "text_embedding", _fake_embed)
    monkeypatch.setattr(vs, "upsert_memory_vector", _fake_upsert)
    monkeypatch.setattr(bm25_index, "invalidate", lambda *_a, **_k: None)


def _spy_receipt(monkeypatch):
    """记录 emit_memory_receipt 的调用（fire-and-forget 在测试里跑不完，只验接线）。"""
    from app.memory import receipt as receipt_mod

    calls: list = []

    def _spy(character_id, memory_id, action, *, reason="", detail=None):
        calls.append({"character_id": character_id, "memory_id": memory_id,
                      "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr(receipt_mod, "emit_memory_receipt", _spy)
    return calls


async def _seed():
    from app.db.database import async_session_factory

    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
            "VALUES (:u,:n,'n','x')"), {"u": UID, "n": f"p11_{UID}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,"
            "talkativeness_locked,is_active,cognitive_loop_enabled,memory_v2_enabled) "
            "VALUES (:c,:u,'c',0,0,1,0,0)"), {"c": CID, "u": UID})
        await db.commit()


async def _make(content: str, write_key: str | None = None) -> int:
    from app.db.database import async_session_factory
    from app.models.memory import Memory

    async with async_session_factory() as db:
        m = Memory(user_id=UID, character_id=CID, memory_type="event", source="chat",
                   source_id=1, content=content, version=1, write_key=write_key,
                   epistemic_status="FACT", scope="private", importance=60.0)
        db.add(m)
        await db.commit()
        return int(m.id)


async def _row(mid: int):
    from app.db.database import async_session_factory
    from app.models.memory import Memory

    async with async_session_factory() as db:
        return await db.get(Memory, mid)


async def _edit(mid: int, content: str):
    from app.api.memories import update_memory_content

    return await update_memory_content(memory_id=mid, data={"content": content},
                                       user_id=UID, lang="zh")


async def _write_receipt_now(mid: int) -> None:
    """直接 await 回执落库（模拟 spawn 真正执行），供 why 计数用例使用。"""
    from app.memory.receipt import _persist_receipt

    await _persist_receipt(CID, mid, "update", "content edited", {"version": 2})


def test_flag_off_keeps_legacy_behaviour(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import content_revision

    monkeypatch.setitem(AGENT_FLAGS, FLAG, False)
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)
    _patch_side_effects(monkeypatch)
    calls = _spy_receipt(monkeypatch)
    asyncio.run(_seed())
    mid = asyncio.run(_make("旧内容甲", write_key="k-legacy-1"))

    out = asyncio.run(_edit(mid, "新内容甲"))
    row = asyncio.run(_row(mid))
    assert out["version"] == 2
    assert "write_key_revised" not in out, "flag 关＝响应形状逐字不变（不加新键）"
    assert row.write_key == "k-legacy-1", "flag 关时证据键必须逐字保持"
    assert calls == [], "flag 关时不得发内容改写回执"
    assert content_revision.enabled() is False


def test_flag_on_recomputes_key_and_emits_receipt(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory.write_identity import replay_key

    monkeypatch.setitem(AGENT_FLAGS, FLAG, True)
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", True)
    _patch_side_effects(monkeypatch)
    calls = _spy_receipt(monkeypatch)
    asyncio.run(_seed())
    mid = asyncio.run(_make("旧内容乙", write_key="k-legacy-2"))

    out = asyncio.run(_edit(mid, "新内容乙"))
    row = asyncio.run(_row(mid))
    assert out["version"] == 2 and out["write_key_revised"] is True
    assert row.write_key and row.write_key != "k-legacy-2"
    assert row.write_key == replay_key(row), "证据键必须与改后的内容一致"
    assert len(calls) == 1 and calls[0]["action"] == "update"
    assert calls[0]["reason"] == "content edited" and calls[0]["memory_id"] == mid


def test_collision_keeps_old_key(monkeypatch):
    from app.db.database import async_session_factory
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import content_revision

    monkeypatch.setitem(AGENT_FLAGS, FLAG, True)
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", True)
    _patch_side_effects(monkeypatch)
    asyncio.run(_seed())
    fake = types.SimpleNamespace(
        id=9991, user_id=UID, character_id=CID, source="chat", source_id=1,
        group_id=None, scope="private", speaker_type=None, speaker_id=None,
        epistemic_status="FACT", memory_type="event", sub_type=None, content="撞车内容",
        node_type=None, parent_id=None, chain_id=None, related_memory_id=None,
        derived_from_ids="[]", source_event_uid=None, write_key="k-old")
    from app.memory.write_identity import replay_key
    target_key = replay_key(fake)
    assert target_key
    asyncio.run(_make("占位行", write_key=target_key))

    async def run():
        async with async_session_factory() as db:
            return await content_revision.recompute_write_key(db, fake)

    res = asyncio.run(run())
    assert res["collision"] is True and res["changed"] is False
    assert fake.write_key == "k-old"


def test_why_reports_version_and_revised(monkeypatch):
    from app.api.memory_why import get_memory_why
    from app.flags.agent_flags import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, FLAG, True)
    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", True)
    _patch_side_effects(monkeypatch)
    asyncio.run(_seed())
    mid = asyncio.run(_make("旧内容丙", write_key="k-legacy-3"))
    asyncio.run(_edit(mid, "新内容丙"))
    asyncio.run(_write_receipt_now(mid))

    why = asyncio.run(get_memory_why(memory_id=mid, user_id=UID, lang="zh"))
    assert why.memory_version == 2
    assert why.content_revised is True
    assert why.content_edit_count == 1


def test_why_reports_version_without_source():
    from app.api.memory_why import get_memory_why

    asyncio.run(_seed())
    mid = asyncio.run(_make("无来源内容", write_key="k-nosrc"))
    why = asyncio.run(get_memory_why(memory_id=mid, user_id=UID, lang="zh"))
    assert why.has_source is False
    assert why.memory_version == 1 and why.content_revised is False
    assert why.content_edit_count == 0


def test_flag_registered_default_off():
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import content_revision

    assert AGENT_FLAGS.get(FLAG) is False, "默认关＝现网零行为变化"
    assert content_revision.FLAG == FLAG and content_revision.REASON == "content edited"
