# -*- coding: utf-8 -*-
"""L4（2026-09-09 主动复习回忆化）：过期计划记忆自动退场 + 提取侧 valid_to 写入 测试。

- expire_stale_plans：flag 关=零行为；开=过期计划置 stale（#70-C 语义）/ valid_to 落实际有效期 /
  next_review_at 置 None；未过期计划、已完成行程、瞬时状态不动；limit 节流；
  向量同步走 supersede._mark_vectors（对齐置 stale 路径）；
- daily_memory_maintenance 挂载：日终编排调用 expire_stale_plans、失败静默不阻塞；
- save_memory 提取侧（flag review_plan_validity_extract 灰度默认关）：关=零行为；
  开=计划类记忆标 sub_type=plan 并写 valid_to（解析不到日期给默认水平窗）；显式 sub_type 不覆盖。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库 + monkeypatch
（与 test_memory_chain.py 同法，不触碰 backend/data）。
"""
import asyncio
import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.service as memsvc
from app.models.memory import Memory

NOW = datetime(2026, 9, 9, 12, 0, 0)


async def _noop(*a, **k):
    return None


# 快测档（2026-09-12）：本文件是重量级/集成型用例（每例起一次临时库，约 3s/例），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

def _set_flag(monkeypatch, key, value):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, key, value)


@pytest.fixture()
def expiry_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：monkeypatch maintain_plan_expiry / service / supersede 的
    async_session_factory，并记录向量同步调用（不触碰 backend/data、不触真实 Chroma）。"""
    import app.memory.maintain_plan_expiry as mpe
    import app.memory.supersede as sup
    import app.memory.dedup as memdedup

    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(mpe, "async_session_factory", factory)
    monkeypatch.setattr(mpe, "now_naive_utc", lambda: NOW)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memdedup, "async_session_factory", factory)
    monkeypatch.setattr(sup, "async_session_factory", factory)

    calls = {"vectors": [], "bm25": []}

    async def _fake_mark_vectors(ids, status_map):
        calls["vectors"].append((list(ids), dict(status_map)))

    async def _fake_bm25(cid):
        calls["bm25"].append(cid)

    monkeypatch.setattr(sup, "_mark_vectors", _fake_mark_vectors)
    monkeypatch.setattr(sup, "_bm25_invalidate_safe", _fake_bm25)
    yield factory, calls
    asyncio.run(engine.dispose())


async def _seed(factory, **kw):
    base = dict(
        user_id=3, character_id=13, memory_type="event", content="",
        importance=60.0, created_at=datetime(2026, 8, 15, 10, 0, 0),
    )
    base.update(kw)
    async with factory() as db:
        m = Memory(**base)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


async def _get(factory, mid):
    async with factory() as db:
        return await db.get(Memory, mid)


def _expired_plan_kw(**kw):
    """线上真实语料形态（7018）：旧"近期将去"计划，S 被刷到 60、仍在复习轮转。"""
    base = dict(
        content="用户近期将去长沙，期间可能因不便携带电脑而断联",
        importance=119.0, strength_days=60.0, review_count=14,
        next_review_at=datetime(2026, 9, 8, 0, 0, 0),
        created_at=datetime(2026, 8, 15, 10, 0, 0),
    )
    base.update(kw)
    return base


# ────────────────────────── expire_stale_plans ──────────────────────────

def test_expire_flag_off_零行为(expiry_db, monkeypatch):
    """flag review_plan_expire_stale 默认关：不扫不改，返回 0（灰度安全）。"""
    import app.memory.maintain_plan_expiry as mpe
    factory, calls = expiry_db
    _set_flag(monkeypatch, "review_plan_expire_stale", False)

    async def _main():
        mid = (await _seed(factory, **_expired_plan_kw())).id
        n = await mpe.expire_stale_plans()
        row = await _get(factory, mid)
        return n, row

    n, row = asyncio.run(_main())
    assert n == 0
    assert row.status == "active"
    assert row.next_review_at == datetime(2026, 9, 8, 0, 0, 0)
    assert calls["vectors"] == [] and calls["bm25"] == []


def test_expire_过期计划置stale(expiry_db, monkeypatch):
    """开 flag：过期计划 → stale + valid_to（默认水平窗=created_at+7d）+ next_review_at=None；
    向量同步置 stale + bm25 失效（对齐 supersede 置 stale 路径）。"""
    import app.memory.maintain_plan_expiry as mpe
    factory, calls = expiry_db
    _set_flag(monkeypatch, "review_plan_expire_stale", True)

    async def _main():
        m = await _seed(factory, **_expired_plan_kw())
        n = await mpe.expire_stale_plans()
        row = await _get(factory, m.id)
        return m.id, n, row

    mid, n, row = asyncio.run(_main())
    assert n == 1
    assert row.status == "stale"
    assert row.valid_to == datetime(2026, 8, 15, 10, 0, 0) + timedelta(days=7)
    assert row.next_review_at is None
    assert calls["vectors"] == [([mid], {mid: "stale"})]
    assert calls["bm25"] == [13]


def test_expire_未过期计划不动(expiry_db, monkeypatch):
    import app.memory.maintain_plan_expiry as mpe
    factory, calls = expiry_db
    _set_flag(monkeypatch, "review_plan_expire_stale", True)

    async def _main():
        m = await _seed(factory, content="用户计划下周去长沙出差",
                        created_at=NOW, next_review_at=NOW + timedelta(days=2))
        n = await mpe.expire_stale_plans()
        row = await _get(factory, m.id)
        return n, row

    n, row = asyncio.run(_main())
    assert n == 0
    assert row.status == "active"
    assert row.valid_to is None


def test_expire_完成信号与瞬时状态不动(expiry_db, monkeypatch):
    """已完成行程（回来了）与瞬时状态（逛街中）都不是未过期计划，不置 stale。"""
    import app.memory.maintain_plan_expiry as mpe
    factory, calls = expiry_db
    _set_flag(monkeypatch, "review_plan_expire_stale", True)

    async def _main():
        a = await _seed(factory, content="用户计划去长沙，已经回来了")
        b = await _seed(factory, content="状态更新：正在商场逛街", sub_type="status")
        n = await mpe.expire_stale_plans()
        ra, rb = await _get(factory, a.id), await _get(factory, b.id)
        return n, ra, rb

    n, ra, rb = asyncio.run(_main())
    assert n == 0
    assert ra.status == "active" and rb.status == "active"


def test_expire_limit节流(expiry_db, monkeypatch):
    """limit=1：两条过期计划只处理一条（日终维护节流，余量次日继续）。"""
    import app.memory.maintain_plan_expiry as mpe
    factory, _ = expiry_db
    _set_flag(monkeypatch, "review_plan_expire_stale", True)

    async def _main():
        a = await _seed(factory, **_expired_plan_kw())
        b = await _seed(factory, **_expired_plan_kw(content="用户打算下个月去成都旅行"))
        n = await mpe.expire_stale_plans(limit=1)
        ra, rb = await _get(factory, a.id), await _get(factory, b.id)
        return n, ra, rb

    n, ra, rb = asyncio.run(_main())
    assert n == 1
    assert sorted([ra.status, rb.status]) == ["active", "stale"]


def test_expire_库异常静默返回0(expiry_db, monkeypatch):
    """DB 故障 → warning + 返回 0，不抛（不阻塞日终维护）。"""
    import app.memory.maintain_plan_expiry as mpe
    _set_flag(monkeypatch, "review_plan_expire_stale", True)

    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(mpe, "async_session_factory", _BoomFactory())
    assert asyncio.run(mpe.expire_stale_plans()) == 0


# ────────────────────────── 日终维护挂载 ──────────────────────────

def _patch_daily_steps(monkeypatch):
    """把日终维护里其余步骤全部置空/指离真实库（本组用例只验证计划退场挂载本身）。"""
    import app.scheduling.daily_memory_maintenance as dmm
    import app.memory.supersede as sup

    for name in ("generate_today_summaries", "run_dedup_light", "refresh_pinned_summaries"):
        async def _zero(*a, **k):
            return 0
        monkeypatch.setattr(dmm, name, _zero)

    async def _no_archive(days=60):
        return 0

    monkeypatch.setattr(sup, "archive_cold_superseded", _no_archive)
    _set_flag(monkeypatch, "preoccupation_enabled", False)
    _set_flag(monkeypatch, "cross_char_fact_sync", False)
    return dmm


def test_daily_maintenance_挂载计划退场(monkeypatch):
    """run_daily_memory_maintenance 调用 expire_stale_plans 并回报计数；其余维护步骤置空。"""
    import app.memory.maintain_plan_expiry as mpe

    async def _fake_expire(limit=200):
        return 7

    monkeypatch.setattr(mpe, "expire_stale_plans", _fake_expire)
    dmm = _patch_daily_steps(monkeypatch)
    out = asyncio.run(dmm.run_daily_memory_maintenance())
    assert out.get("stale_plans_expired") == 7


def test_daily_maintenance_计划退场失败不阻塞(monkeypatch):
    """expire_stale_plans 抛异常 → 静默吞掉，日终维护继续返回结果。"""
    import app.memory.maintain_plan_expiry as mpe

    async def _boom(limit=200):
        raise RuntimeError("boom")

    monkeypatch.setattr(mpe, "expire_stale_plans", _boom)
    dmm = _patch_daily_steps(monkeypatch)
    out = asyncio.run(dmm.run_daily_memory_maintenance())
    assert out.get("stale_plans_expired") == 0  # 异常路径不写值 → 保持初始 0


# ────────────────────────── 提取侧 valid_to 写入（save_memory 单点收口）──────────────────────────

@pytest.fixture()
def write_db(monkeypatch, tmp_path):
    """save_memory 路径：临时库 + 向量/嵌入/后台任务全 noop（与 test_memory_chain.py 同法）。"""
    import app.memory.dedup as memdedup

    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(memdedup, "async_session_factory", factory)

    async def _fake_embed(c):
        return [0.1, 0.2]

    monkeypatch.setattr(memsvc, "text_embedding", _fake_embed)
    monkeypatch.setattr(memsvc, "add_memory", _noop)
    monkeypatch.setattr(memdedup, "_schedule_dedup", _noop)
    import app.memory.meaning as meaning_mod
    monkeypatch.setattr(meaning_mod, "maybe_extract_meaning", _noop)
    yield factory
    asyncio.run(engine.dispose())


def _save_plan_memory(factory, monkeypatch, *, flag_on, content, sub_type="extracted"):
    _set_flag(monkeypatch, "review_plan_validity_extract", flag_on)

    async def _main():
        m = await memsvc.save_memory(
            user_id=3, character_id=13, memory_type="event",
            content=content, importance=3, source="chat",
            sub_type=sub_type, source_id=None, skip_dedup=True,
        )
        async with factory() as db:
            row = await db.get(Memory, m.id)
        return m, row

    return asyncio.run(_main())


def test_validity_extract_flag_off_零行为(write_db, monkeypatch):
    """flag 默认关：计划类内容不标 sub_type=plan、不写 valid_to（逐字节旧路径）。"""
    factory = write_db
    m, row = _save_plan_memory(
        factory, monkeypatch, flag_on=False,
        content="用户近期将去长沙，期间可能因不便携带电脑而断联",
    )
    assert row.sub_type == "extracted"
    assert row.valid_to is None


def test_validity_extract_flag_on_写valid_to(write_db, monkeypatch):
    """开 flag：计划类记忆标 sub_type=plan，valid_to=创建时间+默认水平窗（解析不到日期的兜底）。"""
    factory = write_db
    m, row = _save_plan_memory(
        factory, monkeypatch, flag_on=True,
        content="用户近期将去长沙，期间可能因不便携带电脑而断联",
    )
    assert row.sub_type == "plan"
    assert row.valid_to is not None
    assert row.valid_to <= (row.created_at or NOW) + timedelta(days=7) + timedelta(seconds=1)


def test_validity_extract_flag_on_解析到日期(write_db, monkeypatch):
    factory = write_db
    m, row = _save_plan_memory(
        factory, monkeypatch, flag_on=True,
        content="用户12月20日出发去长沙",
    )
    assert row.sub_type == "plan"
    assert row.valid_to == datetime(2026, 12, 20) + timedelta(days=3)  # 行程缓冲


def test_validity_extract_flag_on_非计划不动(write_db, monkeypatch):
    factory = write_db
    m, row = _save_plan_memory(
        factory, monkeypatch, flag_on=True,
        content="今天在橘子洲游玩，拍了好多照片",
    )
    assert row.sub_type == "extracted"
    assert row.valid_to is None


def test_validity_extract_显式sub_type不覆盖(write_db, monkeypatch):
    """调用方显式声明的 sub_type（slot/relationship 等）优先，提取侧不越权改标。"""
    factory = write_db
    m, row = _save_plan_memory(
        factory, monkeypatch, flag_on=True,
        content="用户近期将去长沙", sub_type="goal",
    )
    assert row.sub_type == "goal"
    assert row.valid_to is None
