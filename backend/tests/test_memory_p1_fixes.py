# -*- coding: utf-8 -*-
"""记忆链路 P1 修复测试（2026-08-18，审查 M-P1-1 / M-P1-3 / M-P1-2 / M-P1-4）：
- M-P1-1 意义提炼候选窗口：created_at 距今 <=24h 且 importance<60 的记忆能入选（不被 6h 衰减挤出）；
- M-P1-3 BIO/RELATIONSHIP 保守合并：新值更长时替换 / 更短时保留现值+追加（纯函数）；
- M-P1-2 纠正降权：contradiction_count>0 的记忆分数低于同 importance 无矛盾记忆；
  注入行对 contradiction_count>0 的记忆追加「以你最新说法为准」后缀；
- M-P1-4 置顶配额：3 条置顶 + 非置顶时 top3 最多含 2 条置顶且非置顶可进入。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库参照 test_memory_p2_fixes 风格）
"""
import asyncio
import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.agent.llm_client as llm_mod
import app.memory.bm25_index as bm25
import app.memory.meaning as meaning_mod
import app.memory.service as memsvc
from app.memory.format import format_memory_line  # X-1（2026-08-18）：公共格式化函数（原 context_builder._format_memory_line 迁移至此）
from app.memory.extractor import _merge_profile_text
from app.models.character import AICharacter
from app.models.memory import Memory


async def _noop(*a, **k):
    return None


@pytest.fixture()
def mem_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch 记忆模块的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="memory_p1_test_")
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
    monkeypatch.setattr(meaning_mod, "async_session_factory", factory)
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


# ---------------- M-P1-1：意义提炼候选窗口（24h 新鲜窗口防衰减挤出） ----------------

def test_meaning_候选窗口_24h内低重要度可入选(mem_db, monkeypatch):
    """created_at 距今 <=24h 且 importance<60 的记忆能被批量查询选中（写入时达标但 6h 衰减后跌破 60 的场景）"""
    captured = {}

    async def _fake_llm(messages, **kw):
        captured["prompt"] = messages[-1]["content"]
        return "[]"

    async def _main():
        async with mem_db() as db:
            db.add(AICharacter(id=1, user_id=1, name="测试", memory_v2_enabled=True))
            await db.commit()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fresh_low = await _seed(mem_db, **_base_kw(
            content="用户今天第一次说喜欢海边", importance=55.0, created_at=now))
        old_low = await _seed(mem_db, **_base_kw(
            content="用户上周说过怕打雷", importance=55.0,
            created_at=now - timedelta(days=3)))
        high = await _seed(mem_db, **_base_kw(
            content="用户为了家人换工作", importance=65.0,
            created_at=now - timedelta(days=3)))
        monkeypatch.setattr(llm_mod, "chat_completion", _fake_llm)
        updated = await meaning_mod.run_meaning_extraction(1, 1)
        return updated, fresh_low, old_low, high

    updated, fresh_low, old_low, high = asyncio.run(_main())
    assert fresh_low.content in captured["prompt"]          # 24h 内低重要度入选
    assert high.content in captured["prompt"]               # importance>=60 仍入选
    assert old_low.content not in captured["prompt"]        # 非新鲜且 importance<60 不入选
    assert updated == 0


def test_meaning_候选窗口_低重要度新鲜记忆被提炼写入(mem_db, monkeypatch):
    """24h 内低重要度记忆入选后，LLM 返回的 why 会写回 why_it_matters"""
    captured = {}

    async def _fake_llm(messages, **kw):
        captured["prompt"] = messages[-1]["content"]
        return "[{\"id\": %d, \"why\": \"海边对他有特殊意义\"}]" % captured["target_id"]

    async def _main():
        async with mem_db() as db:
            db.add(AICharacter(id=1, user_id=1, name="测试", memory_v2_enabled=True))
            await db.commit()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        fresh_low = await _seed(mem_db, **_base_kw(
            content="用户说喜欢在海边散步", importance=55.0, created_at=now))
        captured["target_id"] = fresh_low.id
        monkeypatch.setattr(llm_mod, "chat_completion", _fake_llm)
        updated = await meaning_mod.run_meaning_extraction(1, 1)
        async with mem_db() as db:
            m = await db.get(Memory, fresh_low.id)
            why = m.why_it_matters if m else None
        return updated, why

    updated, why = asyncio.run(_main())
    assert updated == 1
    assert why == "海边对他有特殊意义"


# ---------------- M-P1-3：BIO/RELATIONSHIP 保守合并（纯函数） ----------------

def test_merge_profile_现值缺失用新值():
    assert _merge_profile_text(None, "我喜欢夏天", 500) == "我喜欢夏天"
    assert _merge_profile_text("", "我喜欢夏天", 500) == "我喜欢夏天"


def test_merge_profile_新值更长时替换():
    cur = "我喜欢夏天"
    new = "我喜欢夏天、也喜欢冬天、最怕打雷"
    out = _merge_profile_text(cur, new, 500)
    assert out == new                    # LLM 输出完整最新表述 → 替换


def test_merge_profile_新值更短时保留现值并追加():
    cur = "我喜欢夏天、也喜欢冬天、最怕打雷"
    new = "我喜欢夏天"
    out = _merge_profile_text(cur, new, 500)
    assert out.startswith(cur)           # 现值保留（防单侧面覆盖多面信息）
    assert new in out                    # 新值追加
    assert "\n\n" in out                # 追加用空行分段连接（多段自述可读）


def test_merge_profile_截断上限不变():
    cur = "字" * 200
    new = "字" * 30
    out = _merge_profile_text(cur, new, 200)
    assert len(out) <= 200               # 整体截断到 max_len（与既有覆盖写 [:max_len] 一致）
    assert out.startswith("字" * 200)    # 现值完整保留在前

    out2 = _merge_profile_text(None, "x" * 600, 500)
    assert len(out2) == 500              # 新值超长截断到上限


def test_merge_profile_新值为空保留现值():
    assert _merge_profile_text("我喜欢夏天", "", 500) == "我喜欢夏天"
    assert _merge_profile_text("我喜欢夏天", "   ", 500) == "我喜欢夏天"


# ---------------- M-P1-2：纠正降权 + 注入后缀 ----------------

def test_rerank_矛盾记忆降权低于同重要度无矛盾记忆(mem_db, monkeypatch):
    """contradiction_count>0 的记忆 score 减 (count*10)，同 importance 下排在无矛盾记忆之后"""
    async def _main():
        contrad = await _seed(mem_db, **_base_kw(
            content="用户在北京长大", importance=80.0, contradiction_count=2))
        clean = await _seed(mem_db, **_base_kw(
            content="用户在北京工作", importance=80.0, contradiction_count=0))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=5)
        return hits, contrad, clean

    hits, contrad, clean = asyncio.run(_main())
    ids = [h["id"] for h in hits]
    assert contrad.id in ids and clean.id in ids
    assert hits[0]["id"] == clean.id                       # 无矛盾记忆排前（80 > 80-20）
    by_id = {h["id"]: h for h in hits}
    assert by_id[contrad.id]["contradiction_count"] == 2   # contradiction_count 透传到注入端


def test_注入行_纠正记忆带最新说法后缀():
    line = format_memory_line({
        "content": "用户住在北京朝阳区",
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "FACT",
        "reliability_score": 0.8,
        "contradiction_count": 1,
    })
    assert line.startswith("- [记录于 2026-08-01] 用户住在北京朝阳区")
    assert line.endswith("（你后来纠正过，以你最新说法为准）")
    assert "以你最新说法为准" in line


def test_注入行_无矛盾记忆不带后缀():
    line = format_memory_line({
        "content": "用户喜欢喝美式咖啡",
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "FACT",
        "reliability_score": 0.9,
        "contradiction_count": 0,
    })
    assert line == "- [记录于 2026-08-01] 用户喜欢喝美式咖啡"
    assert "以你最新说法为准" not in line


def test_注入行_截断后再追加后缀():
    content = "用户说他下周要去北京出差见客户" + "字" * 200
    line = format_memory_line({
        "content": content,
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "FACT",
        "reliability_score": 0.9,
        "contradiction_count": 3,
    })
    assert "用户说他下周要去北京出差见客户" in line
    assert len(line[:line.index("（你后来纠正过")]) <= 150 + len("- [记录于 2026-08-01] ")  # 后缀在截断之后


def test_注入行_UNVERIFIED前缀与后缀可共存():
    line = format_memory_line({
        "content": "用户养了一只猫",
        "created_at": datetime(2026, 8, 1),
        "epistemic_status": "UNVERIFIED",
        "reliability_score": 0.3,
        "contradiction_count": 1,
    })
    assert "[UNVERIFIED] " in line
    assert line.endswith("（你后来纠正过，以你最新说法为准）")


# ---------------- M-P1-4：置顶配额（top3 最多 2 条置顶，非置顶可进入） ----------------

def test_置顶配额_top3最多2条置顶且非置顶进入(mem_db, monkeypatch):
    """3 条置顶 + 非置顶时：top3 最多含 2 条置顶，最高分非置顶进入第 3 槽"""
    async def _main():
        p1 = await _seed(mem_db, **_base_kw(content="置顶摘要一：用户喜欢北京", importance=40.0, is_pinned=True))
        p2 = await _seed(mem_db, **_base_kw(content="置顶摘要二：用户去过北京", importance=40.0, is_pinned=True))
        p3 = await _seed(mem_db, **_base_kw(content="置顶摘要三：用户想再去北京", importance=40.0, is_pinned=True))
        n1 = await _seed(mem_db, **_base_kw(content="用户下周去北京出差", importance=90.0))
        n2 = await _seed(mem_db, **_base_kw(content="用户喜欢北京烤鸭", importance=30.0))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=3)
        return hits, {p1.id, p2.id, p3.id}, {n1.id, n2.id}

    hits, pinned_ids, normal_ids = asyncio.run(_main())
    assert len(hits) == 3
    n_pinned = sum(1 for h in hits if h["id"] in pinned_ids)
    n_normal = sum(1 for h in hits if h["id"] in normal_ids)
    assert n_pinned == 2                  # 最多 2 条置顶
    assert n_normal == 1                  # 非置顶可进入（第 3 槽）
    assert hits[2]["id"] in normal_ids    # 非置顶按分数排序保留（90 > 30）


def test_置顶配额_置顶不足2条时非置顶填满(mem_db, monkeypatch):
    """1 条置顶 + 3 条非置顶：top3 = 1 置顶 + 2 非置顶"""
    async def _main():
        p = await _seed(mem_db, **_base_kw(content="置顶摘要：用户在北京长大", importance=40.0, is_pinned=True))
        n1 = await _seed(mem_db, **_base_kw(content="用户在北京工作三年", importance=90.0))
        n2 = await _seed(mem_db, **_base_kw(content="用户喜欢北京秋天", importance=70.0))
        n3 = await _seed(mem_db, **_base_kw(content="用户下周去北京", importance=50.0))

        async def _boom_embed(c):
            raise RuntimeError("embed unavailable")

        monkeypatch.setattr(memsvc, "text_embedding", _boom_embed)
        monkeypatch.setattr(memsvc, "vector_search", _boom_embed)

        hits = await memsvc.search_memories(character_id=1, query="北京", limit=3)
        return hits, p.id, {n1.id, n2.id, n3.id}

    hits, p_id, normal_ids = asyncio.run(_main())
    assert len(hits) == 3
    assert hits[0]["id"] == p_id          # 置顶仍靠前（+500 加分）
    assert sum(1 for h in hits if h["id"] in normal_ids) == 2
