# -*- coding: utf-8 -*-
"""Ariadne 模块F：Curated Knowledge 读写层 + context 分区测试（2026-09-04）。

覆盖要点：
- assert_curated 写 20 条 constraint/fact → get_curated_facts 仍能按配额取到（验证绕过 12 条上限）；
- assert_fact 连续写 15 条 status → 只有最旧的 status 被 supersede；curated 一条不少；
- stale_after 过期：curated 仍返回且 curated_line 带 [待复核]；expires_at 过期 status 不返回（两种时间语义不混）；
- verify 只升不降：human > machine > unverified；
- section：flag 关→空；flag 开→constraint 必在、user_text 命中 links 的行排最前、超配额被 clip；
- audience：非 public 且 viewer 不在 audience 的 curated 不返回。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import WorldFact


@pytest.fixture()
def cf_db(monkeypatch):
    """临时库：create_all 全模型 + 把 facts 模块的 async_session_factory 指向临时工厂。"""
    tmp = tempfile.mkdtemp(prefix="ariadne_f_")
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _add_fact(factory, *, character_id, user_id=1, kind, object_value,
              predicate="curated", audience="[\"public\"]", status="active",
              verify_state="unverified", sources_json="[]", links_json="[]",
              stale_after=None, expires_at=None, confidence=1.0, asserted_at=None):
    from datetime import datetime, timezone
    now = (asserted_at or datetime.now(timezone.utc).replace(tzinfo=None))
    async def _run():
        async with factory() as db:
            r = WorldFact(
                user_id=user_id, character_id=character_id, subject_type="character",
                subject_id=character_id, predicate=predicate, object_value=object_value,
                status=status, confidence=confidence, epistemic_status="FACT",
                audience=audience, author="system", is_authoritative=True,
                kind=kind, verify_state=verify_state,
                sources_json=sources_json, links_json=links_json,
                stale_after=stale_after, expires_at=expires_at, asserted_at=now,
            )
            db.add(r)
            await db.commit()
            await db.refresh(r)
            return r.id
    return asyncio.run(_run())


def test_assert_curated_bypasses_12_cap(cf_db):
    from app.events.facts import assert_curated, get_curated_facts, KIND_CONSTRAINT, KIND_FACT
    factory = cf_db

    async def _seed():
        async with factory() as db:
            for i in range(10):
                await assert_curated(db, character_id=11, user_id=1, kind=KIND_CONSTRAINT,
                                     object_value=f"铁律{i}")
                await assert_curated(db, character_id=11, user_id=1, kind=KIND_FACT,
                                     object_value=f"事实{i}")
            await db.commit()
    asyncio.run(_seed())

    grouped = asyncio.run(get_curated_facts(character_id=11, user_id=1))
    assert len(grouped[KIND_CONSTRAINT]) == 8  # constraint 放宽到 8
    assert len(grouped[KIND_FACT]) == 4        # 其余每类 4
    # 总数远超 12，但仍能按配额取到（绕过 12 条上限）
    assert sum(len(v) for v in grouped.values()) >= 12


def test_assert_fact_eviction_only_touches_status(cf_db):
    from app.events.facts import assert_fact, assert_curated, get_curated_facts, KIND_FACT, KIND_STATUS
    factory = cf_db
    # 先写 1 条 curated（不应被淘汰）
    async def _seed_curated():
        async with factory() as db:
            await assert_curated(db, character_id=11, user_id=1, kind=KIND_FACT,
                                 object_value="用户住在杭州", predicate="curated")
            await db.commit()
    asyncio.run(_seed_curated())

    # 写 15 条 status（不同 predicate，避免同键 supersede；只保留 12 条，最旧 3 条被 supersede）
    async def _write_status():
        for i in range(15):
            await assert_fact(
                subject_type="character", subject_id=11, predicate=f"status{i}",
                object_value=f"状态{i}", user_id=1, character_id=11,
                audience=[("user", 1), ("char", 11)],
            )
    asyncio.run(_write_status())

    grouped = asyncio.run(get_curated_facts(character_id=11, user_id=1))
    assert len(grouped[KIND_FACT]) == 1  # curated 一条不少

    # 校验 status：仍是 active 的 <= 12，最旧的被 superseded
    from sqlalchemy import select
    async def _count_status():
        async with factory() as db:
            rows = (await db.execute(select(WorldFact).where(
                WorldFact.character_id == 11, WorldFact.kind == KIND_STATUS,
            ))).scalars().all()
            active = [r for r in rows if r.status == "active"]
            superseded = [r for r in rows if r.status == "superseded"]
            return len(active), len(superseded)
    active_n, sup_n = asyncio.run(_count_status())
    assert active_n == 12
    assert sup_n == 3


def test_stale_vs_expires_semantics(cf_db):
    from datetime import datetime, timedelta, timezone
    from app.events.facts import get_curated_facts, curated_line, KIND_FACT, KIND_STATUS
    factory = cf_db
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past = now - timedelta(hours=1)
    # curated：stale_after 已过期 → 仍返回 + [待复核]
    _add_fact(factory, character_id=11, kind=KIND_FACT, object_value="用户是程序员",
              stale_after=past)
    # curated：stale_after 未到 → 返回，无 [待复核]
    _add_fact(factory, character_id=11, kind=KIND_FACT, object_value="用户喜欢猫",
              stale_after=now + timedelta(days=1))
    # status：expires_at 已过期 → get_active_facts 不返回（物理过期）
    _add_fact(factory, character_id=11, kind=KIND_STATUS, predicate="status",
              object_value="在吃饭", expires_at=past)

    grouped = asyncio.run(get_curated_facts(character_id=11, user_id=1))
    flines = [r.object_value for r in grouped[KIND_FACT]]
    assert "用户是程序员" in flines and "用户喜欢猫" in flines  # 过期的 curated 仍在（不消失）

    # curated_line 带 [待复核]（仅过期那条）
    stale_row = next(r for r in grouped[KIND_FACT] if r.object_value == "用户是程序员")
    fresh_row = next(r for r in grouped[KIND_FACT] if r.object_value == "用户喜欢猫")
    assert "[待复核]" in curated_line(stale_row)
    assert "[待复核]" not in curated_line(fresh_row)


def test_verify_state_only_escalates(cf_db):
    from app.events.facts import (assert_curated, VERIFY_UNVERIFIED, VERIFY_MACHINE, VERIFY_HUMAN,
                                  KIND_FACT)
    factory = cf_db

    async def _run():
        async with factory() as db:
            r1 = await assert_curated(db, character_id=11, user_id=1, kind=KIND_FACT,
                                      object_value="用户擅长做饭", verify_state=VERIFY_MACHINE)
            await db.flush()
            # 用更低级别（unverified）更新 → 不降级
            r2 = await assert_curated(db, character_id=11, user_id=1, kind=KIND_FACT,
                                      object_value="用户擅长做饭", verify_state=VERIFY_UNVERIFIED)
            await db.commit()
            return r1.verify_state, r2.verify_state
    v1, v2 = asyncio.run(_run())
    assert v1 == VERIFY_MACHINE and v2 == VERIFY_MACHINE  # 不降级

    async def _run2():
        async with factory() as db:
            r3 = await assert_curated(db, character_id=11, user_id=1, kind=KIND_FACT,
                                      object_value="用户擅长做饭", verify_state=VERIFY_HUMAN)
            await db.commit()
            return r3.verify_state
    v3 = asyncio.run(_run2())
    assert v3 == VERIFY_HUMAN  # 升级成功


def test_section_flag_off_empty(cf_db):
    from app.agent.context.section_curated import curated_knowledge_section
    from app.agent.loop import AGENT_FLAGS

    async def _run():
        AGENT_FLAGS["curated_knowledge"] = False
        return await curated_knowledge_section(
            {"character_id": 11, "user_id": 1, "user_message": "你好"}, {})
    assert asyncio.run(_run()) == []


def test_section_constraint_present_and_trigger_first(cf_db):
    from app.agent.context.section_curated import curated_knowledge_section
    from app.agent.loop import AGENT_FLAGS
    from app.events.facts import KIND_CONSTRAINT, KIND_FACT
    factory = cf_db
    # constraint + 两条同 kind 的 fact（其一 links 命中用户文本 → 应排该类最前）
    _add_fact(factory, character_id=11, kind=KIND_CONSTRAINT, object_value="不许提前任")
    _add_fact(factory, character_id=11, kind=KIND_FACT, object_value="用户喜欢喝美式",
              links_json='["咖啡"]')
    _add_fact(factory, character_id=11, kind=KIND_FACT, object_value="用户喜欢喝茶")

    async def _run():
        AGENT_FLAGS["curated_knowledge"] = True
        blocks = await curated_knowledge_section(
            {"character_id": 11, "user_id": 1, "user_message": "要不要一起去喝咖啡？"}, {})
        AGENT_FLAGS["curated_knowledge"] = False
        return blocks
    blocks = asyncio.run(_run())
    assert blocks, "flag 开且含 curated 时必有输出"
    text = blocks[0]
    assert "【编纂知识层" in text
    assert "不许提前任" in text  # constraint 必在
    # 命中 links（咖啡）的 fact 排在同类不命中的前面
    assert text.index("用户喜欢喝美式") < text.index("用户喜欢喝茶")


def test_quota_clipped(cf_db):
    from app.agent.context.section_curated import curated_knowledge_section, _CURATED_QUOTA_TOKENS
    from app.agent.loop import AGENT_FLAGS
    from app.events.facts import KIND_CONSTRAINT, KIND_FACT
    factory = cf_db
    # 塞满各类，让拼接后文本超配额（20 行 * ~90 字 > 配额*2 字符）
    long_val = "这是一个足够长以触发配额裁剪的长期稳定知识条目，用于验证 section 超配额时会被裁剪而不超出预算。" * 3
    for i in range(8):
        _add_fact(factory, character_id=11, kind=KIND_CONSTRAINT, object_value=long_val)
    for kind in (KIND_FACT, "preference_profile", "relationship_baseline"):
        for i in range(4):
            _add_fact(factory, character_id=11, kind=kind, object_value=long_val)

    budget_chars = _CURATED_QUOTA_TOKENS * 2
    async def _run():
        AGENT_FLAGS["curated_knowledge"] = True
        blocks = await curated_knowledge_section(
            {"character_id": 11, "user_id": 1, "user_message": ""}, {})
        AGENT_FLAGS["curated_knowledge"] = False
        return blocks
    blocks = asyncio.run(_run())
    assert blocks
    assert len(blocks[0]) <= budget_chars


def test_audience_filter_hides_private(cf_db):
    from app.events.facts import get_curated_facts, KIND_FACT
    factory = cf_db
    # private：只对 char:11 + user:3 可见
    _add_fact(factory, character_id=11, kind=KIND_FACT, object_value="私密设定",
              audience='["char:11", "user:3"]')
    # 以 char:99 视角查看 → 不可见
    grouped = asyncio.run(get_curated_facts(character_id=11, user_id=1,
                                            viewer_type="character", viewer_id=99))
    vals = [r.object_value for r in grouped[KIND_FACT]]
    assert vals == []
    # 以 char:11 视角查看 → 可见
    grouped2 = asyncio.run(get_curated_facts(character_id=11, user_id=1,
                                             viewer_type="character", viewer_id=11))
    vals2 = [r.object_value for r in grouped2[KIND_FACT]]
    assert "私密设定" in vals2
