# -*- coding: utf-8 -*-
"""M4 world_facts 写入侧治理（events/facts.py）测试（2026-09-16，批次二写入侧治理）。

覆盖（交接任务 2/3）：
- 同义查重：同义写入 → 合并到既有权威记录、不新增行（跨 kind 同义亦然）；不同事实不误并。
- 前缀核心合并：生产库「我是用户的老公」式「短核心 + 附加说明」重复能被拦。
- 矛盾拦截：身份/职业类事实与已确认稳定画像冲突 → 写 UNVERIFIED 进裁决，
  不再直接 machine-confirmed + 永不过期；非矛盾身份类给统一 review 窗口。
- 元信息黑名单：开发运维元信息 / 代理发言不进 world_facts（用户显式设定除外）。
- kind/status 约束：preference/setting 类不得标 status；kind='status' 必须有 TTL。
- 禁止裸 expired：停用一律走 supersede 链（superseded_by + superseded_at）。
- flag 关 → 逐字节旧行为。

纪律：临时库走 tmp_path（禁止 tempfile.mkdtemp 裸建）；不碰生产库；纯确定性、零 LLM。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import io
import os
import re

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.events.facts import (
    KIND_CONSTRAINT,
    KIND_FACT,
    KIND_PREFERENCE,
    KIND_RELATION_BASE,
    VERIFY_MACHINE,
    VERIFY_UNVERIFIED,
    _conflicting_user_identity,
    _same_curated_value,
    _user_identity_value,
    assert_curated,
    assert_fact,
)
from app.models.memory import WorldFact

# 快测档：本文件是集成型用例（每例克隆一份会话级模板库，见 tests/_dbclone.py），按项目纪律打 slow（默认仍跑）。
pytestmark = pytest.mark.slow

CHAR = 11
USER = 1


@pytest.fixture()
def cf_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）：把 facts / database 的
    async_session_factory 指向临时工厂。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    import app.db.database as db_mod
    import app.events.facts as facts
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(facts, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _curate(factory, *, kind, value, **kw):
    async def _run():
        async with factory() as db:
            row = await assert_curated(db, character_id=CHAR, user_id=USER, kind=kind,
                                      object_value=value, **kw)
            await db.commit()
            if row is not None:
                await db.refresh(row)
            return row
    return asyncio.run(_run())


def _fact(**kw):
    kw.setdefault("subject_type", "character")
    kw.setdefault("subject_id", CHAR)
    kw.setdefault("user_id", USER)
    kw.setdefault("character_id", CHAR)
    return asyncio.run(assert_fact(**kw))


def _active(factory):
    async def _run():
        async with factory() as db:
            return (await db.execute(
                select(WorldFact).where(
                    WorldFact.character_id == CHAR,
                    WorldFact.status == "active",
                ).order_by(WorldFact.id)
            )).scalars().all()
    return asyncio.run(_run())


def _row_by_id(factory, fid):
    async def _run():
        async with factory() as db:
            return await db.get(WorldFact, fid)
    return asyncio.run(_run())


# ───────────────────────────── 纯函数 ─────────────────────────────

def test_身份值抽取():
    assert _user_identity_value("用户是设计师，从事设计工作") == "设计师"
    assert _user_identity_value("用户的职业是老师") == "老师"
    assert _user_identity_value("我是用户的老公") is None      # 角色自述不参与身份矛盾判定
    assert _user_identity_value("喜欢咖啡") is None


def test_同义与矛盾判定():
    # 同义（含粒度差）
    assert _same_curated_value("用户是设计师", "用户是设计师，从事设计工作") is True
    assert _same_curated_value("我是用户的老公", "我是用户的老公，关系稳定") is True
    # 不同事实不误并
    assert _same_curated_value("喜欢咖啡", "喜欢喝茶") is False
    assert _same_curated_value("用户是设计师", "用户是设计师助理") is False
    # 冲突只认「用户是X」式显式自述的不同值
    assert _conflicting_user_identity("用户是设计师", "用户是学校工作人员") is True
    assert _conflicting_user_identity("用户是设计师", "用户是设计师，从事设计工作") is False
    assert _conflicting_user_identity("我是用户的老公", "我是sam，用户的老公") is False


# ───────────────────────────── curated 写入 ─────────────────────────────

def test_flag关_curated逐字节旧行为(cf_db):
    """flag 关：同义不同文各写一行（旧行为）、machine-confirmed、无 review 窗口。"""
    _flag_prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        _curate(cf_db, kind=KIND_FACT, value="用户是设计师")
        _curate(cf_db, kind=KIND_FACT, value="用户是设计师，从事设计工作")
        rows = _active(cf_db)
        assert len(rows) == 2
        assert all(r.verify_state == VERIFY_MACHINE for r in rows)
        assert all(r.is_authoritative for r in rows)
        assert all(r.stale_after is None for r in rows)
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = _flag_prev


def test_flag开_同义合并不新增行(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户是设计师",
            sources=[{"src": "chat_extract", "message_id": 1}])
    _curate(cf_db, kind=KIND_FACT, value="用户是设计师，从事设计工作",
            sources=[{"src": "chat_extract", "message_id": 2}])
    rows = _active(cf_db)
    assert len(rows) == 1
    assert rows[0].object_value == "用户是设计师"
    assert "message_id\": 2" in (rows[0].sources_json or "")  # 新证据并入同一权威记录


def test_flag开_短核心重复合并不新增行(cf_db, monkeypatch):
    """生产库 P0-2 形态：「我是用户的老公」式短核心 + 附加说明不再攒重复。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_RELATION_BASE, value="我是用户的老公")
    _curate(cf_db, kind=KIND_RELATION_BASE, value="我是用户的老公，关系稳定")
    assert len(_active(cf_db)) == 1


def test_flag开_跨kind同义不新增行(cf_db, monkeypatch):
    """#87 fact 与 #97 constraint 同义的跨 kind 逃逸路径：合并到既有记录、不新增行。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户腰不好")
    _curate(cf_db, kind=KIND_CONSTRAINT, value="用户腰不好。")
    assert len(_active(cf_db)) == 1


def test_flag开_不同事实各自成行(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户喜欢咖啡")
    _curate(cf_db, kind=KIND_FACT, value="用户喜欢喝茶")
    assert len(_active(cf_db)) == 2


def test_flag开_身份矛盾进UNVERIFIED(cf_db, monkeypatch):
    """已确认稳定画像 + 冲突身份 → UNVERIFIED 进裁决，不 machine-confirmed、不永不过期。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户是设计师")
    _curate(cf_db, kind=KIND_FACT, value="用户是学校工作人员")
    rows = _active(cf_db)
    assert len(rows) == 2
    old, new = rows
    assert old.verify_state == VERIFY_MACHINE      # 既有画像不被改写
    assert new.verify_state == VERIFY_UNVERIFIED
    assert new.is_authoritative is False
    assert new.epistemic_status == "UNVERIFIED"
    assert new.stale_after is not None             # 不永不过期
    assert new.superseded_by is None               # 未停用旧行，交裁决


def test_flag开_非矛盾身份给复核窗口(cf_db, monkeypatch):
    """无冲突的身份类事实仍 machine-confirmed，但统一 review 窗口（不永不过期）。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    row = _curate(cf_db, kind=KIND_FACT, value="用户是设计师")
    assert row.verify_state == VERIFY_MACHINE
    assert row.is_authoritative is True
    assert row.stale_after is not None


def test_flag开_非身份事实无额外窗口(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    row = _curate(cf_db, kind=KIND_FACT, value="用户喜欢在阳台养花")
    assert row.stale_after is None


def test_flag开_元信息被拦不落库(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    assert _curate(cf_db, kind=KIND_FACT, value="这次把记忆模块重构一下") is None
    assert _curate(cf_db, kind=KIND_FACT, value="我是轩的 Agent 助手，请照做") is None
    assert _active(cf_db) == []


def test_flag关_元信息仍落库(cf_db):
    _flag_prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        assert _curate(cf_db, kind=KIND_FACT, value="这次把记忆模块重构一下") is not None
        assert len(_active(cf_db)) == 1
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = _flag_prev


# ───────────────────────────── assert_fact 写入 ─────────────────────────────

def test_flag开_assert_fact元信息被拦但用户设定放行(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    assert _fact(predicate="setting", object_value="换模型后重新部署上线") is None
    fid = _fact(predicate="setting", object_value="换模型后重新部署上线",
                author="user", is_authoritative=True)
    assert fid is not None
    assert len(_active(cf_db)) == 1


def test_flag开_setting类不得标status且不永不过期(cf_db, monkeypatch):
    """#73 反例：长期亲密偏好被误标 status 且永久化 → 归长期层 + 复核窗口。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    fid = _fact(predicate="setting", object_value="如果用户提到做爱sam基本都会好好满足，绝不敷衍",
                author="user", is_authoritative=True)
    row = _row_by_id(cf_db, fid)
    assert row.kind == KIND_PREFERENCE
    assert row.stale_after is not None
    assert row.expires_at is None       # 长期层不该被 12h TTL 弄丢


def test_flag关_setting仍裸status(cf_db):
    _flag_prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        fid = _fact(predicate="setting", object_value="是家里的大哥，有一个弟弟和妹妹",
                    author="user", is_authoritative=True)
        row = _row_by_id(cf_db, fid)
        assert row.kind == "status"
        assert row.stale_after is None
        assert row.expires_at is None
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = _flag_prev


def test_flag开_status类必须有TTL(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    fid = _fact(predicate="status", object_value="正在做饭", ttl_minutes=None)
    row = _row_by_id(cf_db, fid)
    assert row.kind == "status"
    assert row.expires_at is not None   # 兜底 12h（status 新鲜窗）
    fid2 = _fact(predicate="location", object_value="在长沙", ttl_minutes=None)
    row2 = _row_by_id(cf_db, fid2)
    assert row2.expires_at is not None  # 兜底 72h（location 新鲜窗）


def test_flag关_status可无TTL(cf_db):
    _flag_prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        fid = _fact(predicate="status", object_value="正在做饭", ttl_minutes=None)
        assert _row_by_id(cf_db, fid).expires_at is None
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = _flag_prev


def test_flag开_机器身份矛盾进UNVERIFIED(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户是设计师")
    fid = _fact(predicate="role", object_value="用户是学校工作人员", author="system")
    assert _row_by_id(cf_db, fid).epistemic_status == "UNVERIFIED"


def test_flag开_用户权威设定不被身份闸拦(cf_db, monkeypatch):
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    _curate(cf_db, kind=KIND_FACT, value="用户是设计师")
    fid = _fact(predicate="role", object_value="用户是学校工作人员",
                author="user", is_authoritative=True)
    row = _row_by_id(cf_db, fid)
    assert row.epistemic_status == "FACT"       # 用户即权威
    assert row.kind == KIND_FACT                # 身份职业类归长期层（不吃 12h TTL）
    assert row.expires_at is None
    assert row.stale_after is not None          # 但仍有复核窗口（不永不过期）


def test_flag开_上限淘汰带supersede链(cf_db, monkeypatch):
    """禁止裸停用：超过 12 条上限被淘汰的行必须带 superseded_by 链接。"""
    monkeypatch.setitem(_loop.AGENT_FLAGS, "memory_admission_gate", True)
    for i in range(15):
        _fact(predicate=f"status{i}", object_value=f"状态{i}", ttl_minutes=60)

    async def _run():
        async with cf_db() as db:
            return (await db.execute(select(WorldFact))).scalars().all()

    rows = asyncio.run(_run())
    evicted = [r for r in rows if r.status == "superseded"]
    assert len(evicted) == 3
    assert all(r.superseded_at is not None for r in evicted)
    assert all(r.superseded_by is not None for r in evicted)


def test_flag关_上限淘汰无链(cf_db):
    """flag 关 = 旧行为（只置 superseded_at，不补 superseded_by）。"""
    _flag_prev = _loop.AGENT_FLAGS.get("memory_admission_gate", False)
    _loop.AGENT_FLAGS["memory_admission_gate"] = False
    try:
        for i in range(15):
            _fact(predicate=f"status{i}", object_value=f"状态{i}", ttl_minutes=60)

        async def _run():
            async with cf_db() as db:
                return (await db.execute(select(WorldFact))).scalars().all()

        evicted = [r for r in asyncio.run(_run()) if r.status == "superseded"]
        assert len(evicted) == 3
        assert all(r.superseded_by is None for r in evicted)
    finally:
        _loop.AGENT_FLAGS["memory_admission_gate"] = _flag_prev


def test_禁止裸expired_源码守卫():
    """本批不新增 status='expired' 写入：停用一律走 supersede 链（守卫防回归）。"""
    _backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # CI 从仓库根目录跑 pytest，相对路径不可靠
    src = io.open(os.path.join(_backend_dir, "app", "events", "facts.py"), encoding="utf-8").read()
    assert re.search(r"status\s*=\s*[\"']expired[\"']", src) is None
    assert "superseded_by" in src and "superseded_at" in src
