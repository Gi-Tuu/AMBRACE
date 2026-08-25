# -*- coding: utf-8 -*-
"""记忆链路 P2 修复测试（2026-08-18，审查 M-P2-5 / M-P2-2 / M-P2-3 / M-P2-4 / M-P2-1）：
- _normalize_importance 双标度归一化（1-5 星与百分比）；
- keyword 兜底路径与向量路径同样产出 reliability 与 rerank 加分（mock 向量失败）；
- catchup 配对：连续两条用户消息第一条也能与后续 AI 消息配对；
- 截断头尾采样保留尾部关键信息；
- summarize_identity 意义输入 / 情境复习分支在 why_it_matters 非空时命中（不依赖 sub_type）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照 test_memory_locked_guard 风格）
"""
import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.bm25_index as bm25
import app.memory.service as memsvc
import app.memory.summary as summary_mod
import app.scheduler.memory_review as review_mod
from app.memory.extractor import _pair_user_ai, _truncate_sample
from app.memory.service import _normalize_importance
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch 记忆模块的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="memory_p2_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(summary_mod, "async_session_factory", factory)
    monkeypatch.setattr(review_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "delete_memory_vector", _noop)  # 避免真实 ChromaDB 调用
    bm25._persist_root = Path(tmp)  # 2026-08-23 深化：索引持久化隔离到临时目录，不写生产/不跨测试泄漏
    bm25.clear_cache()  # 检索增强（2026-08-23）：BM25 索引为进程内全局缓存，避免跨测试的 character_id 复用污染
    yield factory
    bm25.clear_cache()          # persist_root 仍指向临时目录，清内存+清盘
    bm25._persist_root = None
    asyncio.run(engine.dispose())


async def _seed(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


def _base_kw(**over):
    kw = dict(
        user_id=1, character_id=1, memory_type="event",
        content="x", importance=50.0, is_locked=False,
        strength_days=5.0,
    )
    kw.update(over)
    return kw


# ---------------- M-P2-2：_normalize_importance 双标度 ----------------

def test_normalize_importance_1_5星标度():
    assert _normalize_importance(1) == 20.0
    assert _normalize_importance(3) == 60.0
    assert _normalize_importance(5) == 100.0
    assert _normalize_importance(3.5) == 70.0


def test_normalize_importance_百分比标度原样():
    assert _normalize_importance(40.0) == 40.0
    assert _normalize_importance(100.0) == 100.0
    assert _normalize_importance(120.0) == 120.0
    assert _normalize_importance(40) == 40.0


def test_normalize_importance_边界与异常输入():
    assert _normalize_importance(0) == 0.0
    assert _normalize_importance(None) == 0.0
    assert _normalize_importance(5.0) == 100.0


# ---------------- M-P2-3：keyword 兜底路径与向量路径同款 rerank ----------------

def test_keyword_兜底_产出reliability与意义加分(mem_db, monkeypatch):
    async def _main():
        high = await _seed(mem_db, **_base_kw(
            content="用户下周要去北京出差", importance=50.0, memory_type="event"))
        meaningful = await _seed(mem_db, **_base_kw(
            content="用户在北京长大", importance=40.0, memory_type="user_info",
            why_it_matters="北京对用户意义重大"))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=5)
        return hits, high, meaningful

    hits, high, meaningful = asyncio.run(_main())
    ids = [h["id"] for h in hits]
    assert meaningful.id in ids and high.id in ids
    for h in hits:
        assert "reliability_score" in h          # 与向量路径同样透传 reliability
    assert hits[0]["id"] == meaningful.id        # why_it_matters +20 压过 importance 差距（40+20 > 50）


def test_keyword_兜底_置顶记忆恒在前(mem_db, monkeypatch):
    async def _main():
        pinned = await _seed(mem_db, **_base_kw(
            content="用户喜欢北京的春天", importance=30.0, is_pinned=True))
        normal = await _seed(mem_db, **_base_kw(
            content="用户去过北京很多次", importance=90.0))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=5)
        return hits, pinned, normal

    hits, pinned, normal = asyncio.run(_main())
    assert hits[0]["id"] == pinned.id            # 置顶 +10000 恒在前（兜底路径同样生效）


# ---------------- M-P2-4：catchup 配对 ----------------

def _msg(kind, mid):
    return SimpleNamespace(sender_type=kind, id=mid, content=f"c{mid}")


def test_catchup_配对_连续用户消息第一条与后续AI配对():
    msgs = [_msg("user", 1), _msg("user", 2), _msg("ai", 3)]
    pairs = _pair_user_ai(msgs)
    assert (msgs[0], msgs[2]) in pairs  # [U1, U2, A1]：U1 不再永远漏配
    assert (msgs[1], msgs[2]) in pairs  # 第二条用户消息同样与第一条后续 AI 配对


def test_catchup_配对_常规交替序列():
    msgs = [_msg("user", 1), _msg("ai", 2), _msg("user", 3), _msg("ai", 4)]
    assert _pair_user_ai(msgs) == [(msgs[0], msgs[1]), (msgs[2], msgs[3])]


def test_catchup_配对_尾部用户消息无AI不配对():
    msgs = [_msg("user", 1), _msg("ai", 2), _msg("user", 3)]
    assert _pair_user_ai(msgs) == [(msgs[0], msgs[1])]


def test_catchup_配对_空列表():
    assert _pair_user_ai([]) == []


# ---------------- M-P2-4：截断头尾采样 ----------------

def test_截断头尾采样_保留尾部关键信息():
    long_text = "字" * 150 + "用户下周要去北京" + "字" * 60
    out = _truncate_sample(long_text)
    assert "用户下周要去北京" in out            # 尾部关键信息不再被 150 字截断丢掉
    assert len(out) == 201                      # 100 + 省略号 + 100
    assert out.count("…") == 1
    assert out.startswith("字" * 100)
    assert out.endswith("字" * 60)


def test_截断头尾采样_短文本原样():
    assert _truncate_sample("短文本") == "短文本"
    assert _truncate_sample("x" * 200) == "x" * 200  # 恰好 head+tail 不截断


def test_截断头尾采样_边界与None():
    assert len(_truncate_sample("x" * 201)) == 201
    assert _truncate_sample(None) == ""
    assert _truncate_sample("") == ""


# ---------------- M-P2-1：why_it_matters 命中（不依赖 sub_type） ----------------

def test_summarize_identity_意义输入在why_it_matters非空时命中(mem_db, monkeypatch):
    captured = {}

    async def _fake_llm(messages, **kw):
        captured["prompt"] = messages[-1]["content"]
        return "用户是重视家庭与事业的人。"

    async def _v2_enabled(cid):
        return True

    async def _main():
        await _seed(mem_db, **_base_kw(
            memory_type="user_info", content="用户喜欢安静的生活", importance=60.0,
            sub_type="extracted"))
        await _seed(mem_db, **_base_kw(
            memory_type="event", content="用户为了家人换工作", importance=70.0,
            sub_type="event", why_it_matters="家人对他最重要"))
        import app.agent.llm_client as llm_mod
        import app.memory.flags as flags_mod
        monkeypatch.setattr(llm_mod, "chat_completion", _fake_llm)
        monkeypatch.setattr(flags_mod, "memory_v2_enabled", _v2_enabled)
        return await summary_mod.summarize_identity(character_id=1, user_id=1)

    result = asyncio.run(_main())
    assert result["generated"] is True
    assert "家人对他最重要" in captured["prompt"]  # 意义记忆被纳入输入（无需 sub_type=meaning）


def test_pick_contextual_memory_why_it_matters分支命中(mem_db, monkeypatch):
    async def _main():
        meaningless_high = await _seed(mem_db, **_base_kw(
            content="完全不相关的内容", importance=90.0))
        meaningful = await _seed(mem_db, **_base_kw(
            content="用户说工作压力大", importance=60.0,
            why_it_matters="工作状态影响关系"))
        picked = await review_mod._pick_contextual_memory(1, 1, "随便聊聊")
        return picked, meaningful.id, meaningless_high.id

    picked, meaningful_id, _ = asyncio.run(_main())
    assert picked == meaningful_id  # 意义分支优先于更高 importance 的无意义记忆
