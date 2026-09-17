# -*- coding: utf-8 -*-
"""#72 PR-C P5 群记忆日终合并收敛测试（compact_group_memories / 注入过滤 / 迁移可逆）。

- flag 关 → 零行为变化；
- N 条 8 天前 + 3 条昨天 → compact 后：旧行全 is_archived=1、生成 1 条摘要行、近 3 条未被归档；
- recall_group_longterm 只返回未归档内容（归档行不再注入）；
- 二次执行幂等（不新增摘要行）；
- 摘要 content 长度 ≤ 上限（600）；
- 迁移链单头 + downgrade 可逆（tmp_path 临时库跑真实 alembic 链）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。）
"""
import asyncio
import os

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.memory.group_memory as gm
from app.utils.timeutil import beijing_day_start_utc
from datetime import timedelta

# 重量级/集成型用例（每例起一次临时库），打 slow 标记（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

_MY_REV = "a3b4c5d6e7f8"          # 本迁移修订号（group_memories.is_archived）
_PARENT_REV = "e2f3a4b5c6d7"       # 父节点（当前链头）


@pytest.fixture()
def gmem_db(monkeypatch, tmp_path):
    """临时库 + 把 group_memory 的 async_session_factory 指向临时工厂。"""
    import app.models  # noqa: F401
    from app.models.base import Base
    import app.db.database as db_mod
    import app.memory.service as memsvc

    tmp = str(tmp_path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}",
                                 poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_init())
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(memsvc, "async_session_factory", factory)
    monkeypatch.setattr(gm, "async_session_factory", factory)

    async def _seed_group():
        from app.models.chat import ChatGroup
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="tester", nickname="测试"))
            g = ChatGroup(id=1, user_id=1, name="家庭群聊")
            db.add(g)
            await db.commit()
    asyncio.run(_seed_group())
    yield factory
    asyncio.run(engine.dispose())


def _cutoff():
    """与 compact 同口径：北京时间今日 00:00 - 7 天（更旧者被合并）。"""
    return beijing_day_start_utc() - timedelta(days=gm._COMPACT_KEEP_DAYS)


def _seed_rows(factory, specs):
    """specs: [(content, created_at, user_id), ...]；逐条写 group_memories（is_archived 默认 0）。"""

    async def _run():
        async with factory() as db:
            for content, created_at, user_id in specs:
                db.add(gm.GroupMemory(
                    group_id=1, user_id=user_id, round_id=None,
                    speaker_type="system", speaker_id=None,
                    content=content, epistemic_status="FACT", importance=40.0,
                    created_at=created_at,
                ))
            await db.commit()
    asyncio.run(_run())


def _query(factory):
    """返回本群全部 group_memories（按 id 升序）。"""

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(gm.GroupMemory).where(gm.GroupMemory.group_id == 1)
                .order_by(gm.GroupMemory.id.asc())
            )).scalars().all()
            return [(r.id, r.content, bool(r.is_archived), str(r.created_at)[:10]) for r in rows]
    return asyncio.run(_run())


def test_flag_off_noop(gmem_db, monkeypatch):
    """flag 关：compact 直接返回 skipped，不写不读、库无变化。"""
    factory = gmem_db
    monkeypatch.setattr(gm, "group_memory_compact_on", lambda: False)
    cutoff = _cutoff()
    old = cutoff - timedelta(days=1)
    _seed_rows(factory, [
        ("八天前的老事件", old, 1),
        ("昨天的新鲜事", cutoff + timedelta(days=6, hours=1), 1),  # 在保留窗内
    ])
    before = _query(factory)
    stats = asyncio.run(gm.compact_group_memories())
    after = _query(factory)
    assert stats == {"skipped": True, "groups": 0, "archived": 0, "summaries": 0}
    assert before == after


def test_compact_archives_old_keeps_recent(gmem_db, monkeypatch):
    """N 条 8 天前 + 3 条昨天 → 旧行全归档、生成 1 摘要、近 3 条未归档。"""
    factory = gmem_db
    monkeypatch.setattr(gm, "group_memory_compact_on", lambda: True)
    cutoff = _cutoff()
    old = cutoff - timedelta(days=1)              # 8 天前，超出保留窗
    recent = cutoff + timedelta(days=6, hours=2)  # 昨天（保留窗内）
    specs = [(f"老事件{i}", old, 1) for i in range(10)] + \
            [(f"新鲜事{i}", recent, 1) for i in range(3)]
    _seed_rows(factory, specs)

    stats = asyncio.run(gm.compact_group_memories())
    rows = _query(factory)
    archived = [r for r in rows if r[2]]
    active = [r for r in rows if not r[2]]

    # 旧 10 条全归档、生成 1 摘要、近 3 条原样保留
    assert stats["archived"] == 10
    assert stats["summaries"] == 1
    assert len(archived) == 10
    assert len(active) == 4                       # 3 最近 + 1 摘要
    # 摘要行特征：speaker=system / FACT / round_id=None（content 含日期范围前缀）
    summary = [r for r in active if "老事件" in r[1]][0]
    assert summary[1].startswith("[") and "~" in summary[1]
    # 近 3 条未被归档
    recent_contents = {f"新鲜事{i}" for i in range(3)}
    assert recent_contents.issubset({r[1] for r in active})


def test_recall_excludes_archived(gmem_db, monkeypatch):
    """compact 后 recall_group_longterm 只返回未归档内容（归档旧行不再注入）。"""
    factory = gmem_db
    monkeypatch.setattr(gm, "group_memory_compact_on", lambda: True)
    cutoff = _cutoff()
    old = cutoff - timedelta(days=1)
    recent = cutoff + timedelta(days=6, hours=2)
    specs = [(f"老事件{i}", old, 1) for i in range(5)] + \
            [(f"新鲜事{i}", recent, 1) for i in range(3)]
    _seed_rows(factory, specs)
    asyncio.run(gm.compact_group_memories())

    rows = _query(factory)
    active = [r for r in rows if not r[2]]   # 未归档：3 最近 + 1 摘要
    # recall 返回的应为未归档行的精确集合（归档的 5 条旧事件作为独立条目不出现）
    expected = {f"[{r[3]}] {r[1]}" for r in active}
    recalled = set(asyncio.run(gm.recall_group_longterm(group_id=1)))
    assert recalled == expected
    assert len(recalled) == 4                       # 3 最近 + 1 摘要
    assert any("~" in s for s in recalled)          # 摘要行（带日期范围前缀）在召回里
    # 归档的旧事件作为独立条目不在召回中（其文本可能嵌在摘要里，但不会被当作独立 [日期] 行返回）
    archived_old = {f"[{r[3]}] {r[1]}" for r in rows if r[2]}
    assert not (recalled & archived_old)


def test_idempotent_second_run(gmem_db, monkeypatch):
    """二次执行幂等：旧行已归档，不再产生新摘要行。"""
    factory = gmem_db
    monkeypatch.setattr(gm, "group_memory_compact_on", lambda: True)
    cutoff = _cutoff()
    old = cutoff - timedelta(days=1)
    recent = cutoff + timedelta(days=6, hours=2)
    specs = [(f"老事件{i}", old, 1) for i in range(8)] + \
            [(f"新鲜事{i}", recent, 1) for i in range(3)]
    _seed_rows(factory, specs)

    s1 = asyncio.run(gm.compact_group_memories())
    rows_after_1 = _query(factory)
    s2 = asyncio.run(gm.compact_group_memories())
    rows_after_2 = _query(factory)

    assert s1["summaries"] == 1 and s2["summaries"] == 0
    # 两次执行后行集合完全一致（无新增摘要）
    assert rows_after_1 == rows_after_2
    # 仍只有 1 条摘要（带 ~ 前缀）
    assert sum(1 for r in rows_after_2 if "~" in r[1]) == 1


def test_summary_length_within_limit(gmem_db, monkeypatch):
    """合并超长内容时摘要 content 截断到上限（600）以内。"""
    factory = gmem_db
    monkeypatch.setattr(gm, "group_memory_compact_on", lambda: True)
    cutoff = _cutoff()
    old = cutoff - timedelta(days=1)
    # 50 条各 50 字的老事件 → 拼接后远超 600
    specs = [(f"老事件内容填充{i:040d}", old, 1) for i in range(50)]
    _seed_rows(factory, specs)

    asyncio.run(gm.compact_group_memories())
    rows = _query(factory)
    summaries = [r for r in rows if "~" in r[1]]
    assert len(summaries) == 1
    assert len(summaries[0][1]) <= gm._COMPACT_SUMMARY_MAX
    # 旧 50 条全部归档
    assert sum(1 for r in rows if r[2]) == 50


# ── 迁移链单头 + downgrade 可逆（tmp_path 临时库跑真实 alembic 链）──
@pytest.fixture()
def mig_db(monkeypatch, tmp_path):
    import app.config as cfg
    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "mig.db")
    monkeypatch.setattr(cfg.settings, "database_url", "sqlite+aiosqlite:///" + db_path)
    yield db_path


def _alembic_cfg():
    from alembic.config import Config
    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    return cfg


def _heads():
    from alembic.script import ScriptDirectory
    return set(ScriptDirectory.from_config(_alembic_cfg()).get_heads())


def test_migration_head_and_downgrade(mig_db):
    """迁移链单头 + downgrade 可逆。

    说明：本迁移只做「group_memories 加 is_archived」一步，父节点 e2f3a4b5c6d7 已是链头。
    - 单头：脚本目录只看 _heads()（不重放链，贴合生产 init_db + alembic stamp 路径）。
    - 可逆：用 create_all 建当前模型 schema（含 is_archived）后 stamp 到 head，
      再 downgrade 到父修订验证列被移除、upgrade 回 head 验证列真实重建（非仅幂等跳过）。
    """
    from alembic import command
    from app.models._all import Base
    from sqlalchemy import create_engine as _sync_create_engine
    db_path = mig_db
    cfg = _alembic_cfg()

    # 单头：链无分叉（不硬编码具体 revision——新增迁移后自动跟随；原硬编码 _MY_REV 在后续批次
    # 新增迁移后会误报，b2c3d4e5f6a7 起已 stale）
    heads = _heads()
    assert len(heads) == 1, (
        f"迁移链应单头无分叉（本批 _MY_REV={_MY_REV} 已不是链头），实际 {heads}"
    )

    # 建当前模型 schema（含 is_archived），再 stamp 到 head（生产即 init_db + stamp）
    eng0 = _sync_create_engine("sqlite:///" + db_path)
    Base.metadata.create_all(eng0)
    eng0.dispose()
    command.stamp(cfg, _MY_REV)

    # 回退父修订：is_archived 干净移除（验证 downgrade 可逆）
    command.downgrade(cfg, _PARENT_REV)
    eng = _sync_create_engine("sqlite:///" + db_path)
    cols = {c["name"] for c in inspect(eng).get_columns("group_memories")}
    assert "is_archived" not in cols
    eng.dispose()

    # 再升级到 head：is_archived 重新加回（验证 upgrade 真正 ADD，非仅幂等跳过）
    command.upgrade(cfg, "head")
    eng2 = _sync_create_engine("sqlite:///" + db_path)
    cols2 = {c["name"] for c in inspect(eng2).get_columns("group_memories")}
    assert "is_archived" in cols2
    eng2.dispose()
