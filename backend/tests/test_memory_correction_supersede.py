# -*- coding: utf-8 -*-
"""D5 方案 A（2026-11-10）：无引用纠错的检索确认取代链合同测试。

被测链：process_feedback_signal（无引用分支）→ _supersede_uncited_correction
→ search_memories 检索 → 新鲜窗排除本轮新写值 → LLM 确认「被推翻旧说法」
→ supersede_memory(old, new_id=None, reason="user_correction")。

合同：
- flag `memory_correction_supersede_v1` 默认关＝行为零变化（不检索、不取代）；
- 开启后：纠错词命中＋LLM 确认才取代；无矛盾/幻觉 id/确认词（非纠错）一律不动；
- 本轮新写值（created_at 新鲜窗内）永不被取代。
（2026-08-15「无引用不检索」规则由今日方案 A 拍板有条件放开：flag 门控＋LLM 确认双闸，
见 docs/plans.md 与 flag 注释。）
"""
import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.db.database as db_mod
import app.memory.supersede as sup
import app.memory.reliability as reliability
import app.db.vector_store as vs
import app.memory.bm25_index as bm25
import app.memory.service as memsvc
from app.flags.agent_flags import AGENT_FLAGS
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User
from app.utils.timeutil import now_naive_utc


async def _noop(*a, **k):
    return None


@pytest.fixture()
def corr_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：monkeypatch 相关模块的 async_session_factory（不触碰 backend/data）。"""
    tmp = str(tmp_path)
    engine = clone_engine(os.path.join(tmp, "corr.db"))
    factory = make_session_factory(engine)

    async def _seed_parents():
        async with factory() as db:
            db.add(User(id=1, username="corr_u1", nickname="纠错链用户"))
            db.add(AICharacter(id=10, user_id=1, name="纠错链角色"))
            await db.commit()

    asyncio.run(_seed_parents())

    for m in (memsvc, sup, reliability):
        monkeypatch.setattr(m, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    class _FakeCollection:
        def get(self, ids=None, include=None, where=None, limit=None, **kw):
            return {"ids": list(ids or []), "embeddings": [], "metadatas": [], "documents": []}

        def add(self, **kw):
            return None

        def upsert(self, **kw):
            return None

        def query(self, **kw):
            return {"ids": [], "documents": [], "metadatas": [], "distances": []}

        def update(self, **kw):
            return None

        def delete(self, **kw):
            return None

    fake_col = _FakeCollection()

    async def _get_col():
        return fake_col

    monkeypatch.setattr(vs, "get_or_create_collection", _get_col)
    monkeypatch.setattr(bm25, "invalidate", lambda *a, **k: None)
    monkeypatch.setattr(memsvc, "bm25_invalidate", lambda *a, **k: None)
    monkeypatch.setattr(reliability, "_last_signal_at", {})
    return factory


async def _save_mem(factory, content, *, age_days=None, source_id=100):
    """直接落一条 source=chat/user 的 FACT 记忆；age_days 指定则把 created_at 拨回去。"""
    from app.memory.write import save_memory
    m = await save_memory(user_id=1, character_id=10, memory_type="fact",
                          content=content, source="chat", source_id=source_id,
                          speaker_type="user", epistemic_status="FACT", importance=4)
    if age_days is not None:
        async with factory() as db:
            row = await db.get(Memory, m.id)
            row.created_at = now_naive_utc() - timedelta(days=age_days)
            await db.commit()
    return m


async def _get_status(factory, mid):
    async with factory() as db:
        row = await db.get(Memory, mid)
        return row.status


def _patch_chain(monkeypatch, hits, llm_ids, calls):
    """检索/LLM 打桩：hits＝search_memories 返回；llm_ids＝LLM 确认输出的 id 列表。"""

    async def search(**kwargs):
        calls.append({"search": [h.get("id") for h in hits]})
        return hits

    monkeypatch.setattr("app.memory.search_memories", search)

    async def llm(*args, **kwargs):
        calls.append({"llm": llm_ids})
        import json as _json
        return _json.dumps({"supersede": list(llm_ids)})

    monkeypatch.setattr("app.agent.llm_client.chat_completion", llm)


def _flags_on(monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "memory_correction_supersede_v1", True)
    monkeypatch.setitem(AGENT_FLAGS, "memory_supersede", True)


def test_flag_default_off_noop(corr_db, monkeypatch):
    """默认关＝行为零变化：纠错词命中也不检索、不取代。"""
    calls = []
    _patch_chain(monkeypatch, [], [], calls)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了")
        return old

    old = asyncio.run(scenario())
    assert calls == []  # flag 关：连检索都不发生
    assert asyncio.run(_get_status(corr_db, old.id)) != "superseded"


def test_confirmed_old_superseded(corr_db, monkeypatch):
    """flag 开＋纠错词＋LLM 确认 → 旧记忆 superseded。"""
    calls = []
    _flags_on(monkeypatch)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        _patch_chain(monkeypatch,
                     [{"id": old.id, "content": "用户住在城北老小区",
                       "created_at": now_naive_utc() - timedelta(days=10), "status": "active"}],
                     [old.id], calls)
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了",
                                                  turn_source_id=777)
        return old

    old = asyncio.run(scenario())
    assert calls and any("llm" in c for c in calls)  # 检索＋确认都发生
    assert asyncio.run(_get_status(corr_db, old.id)) == "superseded"


def test_fresh_turn_writes_never_superseded(corr_db, monkeypatch):
    """本轮新写值（新鲜窗内）被检索命中也不得取代——取代只针对旧说法。"""
    _flags_on(monkeypatch)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        fresh = await _save_mem(corr_db, "用户已经搬到城西", source_id=777)
        hits = [{"id": old.id, "content": "用户住在城北老小区",
                 "created_at": now_naive_utc() - timedelta(days=10), "status": "active"},
                {"id": fresh.id, "content": "用户已经搬到城西",
                 "created_at": now_naive_utc(), "status": "active"}]
        _patch_chain(monkeypatch, hits, [old.id, fresh.id], [])
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了",
                                                  turn_source_id=777)
        return old, fresh

    old, fresh = asyncio.run(scenario())
    assert asyncio.run(_get_status(corr_db, old.id)) == "superseded"
    assert asyncio.run(_get_status(corr_db, fresh.id)) != "superseded"


def test_llm_no_contradiction_noop(corr_db, monkeypatch):
    """LLM 判无矛盾 → 什么都不动。"""
    _flags_on(monkeypatch)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        _patch_chain(monkeypatch,
                     [{"id": old.id, "content": "用户住在城北老小区",
                       "created_at": now_naive_utc() - timedelta(days=10), "status": "active"}],
                     [], [])
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了")
        return old

    old = asyncio.run(scenario())
    assert asyncio.run(_get_status(corr_db, old.id)) != "superseded"


def test_hallucinated_id_ignored(corr_db, monkeypatch):
    """LLM 幻觉 id（不在候选里）不得触发任何取代。"""
    _flags_on(monkeypatch)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        _patch_chain(monkeypatch,
                     [{"id": old.id, "content": "用户住在城北老小区",
                       "created_at": now_naive_utc() - timedelta(days=10), "status": "active"}],
                     [old.id + 9999], [])
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了")
        return old

    old = asyncio.run(scenario())
    assert asyncio.run(_get_status(corr_db, old.id)) != "superseded"


def test_confirmation_and_non_correction_still_noop(corr_db, monkeypatch):
    """确认词（「没错」）与普通闲聊（无纠错词）即便 flag 开也不进取代链。"""
    calls = []
    _flags_on(monkeypatch)
    _patch_chain(monkeypatch, [], [], calls)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        await reliability.process_feedback_signal(10, 1, "没错，就是这样")
        await reliability.process_feedback_signal(10, 1, "今天天气不错啊")
        return old

    old = asyncio.run(scenario())
    assert calls == []
    assert asyncio.run(_get_status(corr_db, old.id)) != "superseded"


def test_throttle_blocks_repeat_within_window(corr_db, monkeypatch):
    """同一（user,char,文本）600s 内重复纠错只跑一次链（节流复用现有表）。"""
    calls = []
    _flags_on(monkeypatch)

    async def scenario():
        old = await _save_mem(corr_db, "用户住在城北老小区", age_days=10)
        _patch_chain(monkeypatch,
                     [{"id": old.id, "content": "用户住在城北老小区",
                       "created_at": now_naive_utc() - timedelta(days=10), "status": "active"}],
                     [old.id], calls)
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了")
        await reliability.process_feedback_signal(10, 1, "记错了，我已经搬到城西了")
        return old

    old = asyncio.run(scenario())
    llm_calls = [c for c in calls if "llm" in c]
    assert len(llm_calls) == 1
    assert asyncio.run(_get_status(corr_db, old.id)) == "superseded"
