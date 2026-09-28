# -*- coding: utf-8 -*-
"""批 0-2 · M1b「感知记忆认可/撤回 + 只读统计」后端测试（方案 §2.3 / §四）。

覆盖：
- 认可：PATCH body 带 epistemic_status="FACT" ⇒ 置 FACT + confirmation_count+1 + **不改 source** + 写一条 update 回执；
- 拒收：非感知来源 / 非法值 / 空值 ⇒ 400（且不落任何副作用）；
- 越权：跨账号 ⇒ 404（沿用 _get_owned_memory + tenant_scope_ids 口径，不放宽）；
- 既有语义不劣化：is_archived（撤回）/ importance 分支照旧；
- GET /stats/perception：数值正确、空库返回 0、分母为 0 不炸、路由不被 /{memory_id} 吞掉。

纪律：临时库走 tests/_clone（不连生产库）；回执打桩（不依赖 memory_write_receipt 运行时值）；
项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.api import memories as mem_api
from app.db import database as db_mod
from app.memory.perception_tier import PERCEPTION_SOURCE
from app.models.memory import Memory

pytestmark = pytest.mark.slow

CHAT_TEXT = "用户明确说过自己不吃香菜"
PERC_TEXT = "屏幕上看到用户搜了香菜做法"


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 回执打桩：只观察落库与接线，不触发真实写库/后台任务。"""
    engine = clone_engine(tmp_path / "m1b.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="m1b_u1", nickname="感知用户"))
            db.add(User(id=2, username="m1b_u2", nickname="外人用户"))
            db.add(AICharacter(id=101, user_id=1, name="感知角色"))
            db.add(AICharacter(id=202, user_id=2, name="外人角色"))
            await db.commit()

    asyncio.run(_seed_parents())
    # API 与 _get_owned_memory 经模块对象引用取会话工厂（late binding），必须 patch 这里
    monkeypatch.setattr(db_mod, "async_session_factory", factory)

    receipts = []

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"character_id": character_id, "memory_id": memory_id,
                         "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", _rec)
    yield {"factory": factory, "receipts": receipts}
    asyncio.run(engine.dispose())


def _seed(factory, *, user_id=1, character_id=101, source=PERCEPTION_SOURCE,
          epistemic_status="INFERRED", content=PERC_TEXT, **kw):
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            m = Memory(user_id=user_id, character_id=character_id, memory_type="event",
                       source=source, epistemic_status=epistemic_status,
                       content=content, importance=kw.pop("importance", 50.0),
                       created_at=now_naive_utc(), **kw)
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _row(factory, mid):
    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).where(Memory.id == mid))).scalar_one()
    return asyncio.run(_run())


def _patch(env, mid, data, *, user_id=1, lang="zh"):
    return asyncio.run(mem_api.update_memory(memory_id=mid, data=data, user_id=user_id, lang=lang))


def _status(coro_fn, *a, **kw):
    """取 HTTPException 状态码（拒收类用例统一用）。"""
    with pytest.raises(HTTPException) as ei:
        asyncio.run(coro_fn(*a, **kw))
    return ei.value


# ───────────────────────────── 认可 ─────────────────────────────

def test_认可_置FACT且确认次数加1(env):
    mid = _seed(env["factory"], confirmation_count=0)
    assert _patch(env, mid, {"epistemic_status": "FACT"}) == {"status": "ok"}
    row = _row(env["factory"], mid)
    assert row.epistemic_status == "FACT"
    assert row.confirmation_count == 1


def test_认可_来源保持perception_证据不丢(env):
    """关键取舍（方案 §2.3）：认可只改认知状态，source 永不变（否则打标白做）。"""
    mid = _seed(env["factory"])
    _patch(env, mid, {"epistemic_status": "FACT"})
    row = _row(env["factory"], mid)
    assert row.source == PERCEPTION_SOURCE


def test_认可_写一条update回执(env):
    mid = _seed(env["factory"])
    _patch(env, mid, {"epistemic_status": "FACT"})
    upd = [r for r in env["receipts"] if r["action"] == "update"]
    assert len(upd) == 1
    assert upd[0]["memory_id"] == mid
    assert upd[0]["character_id"] == 101
    assert "perception accepted" in upd[0]["reason"]


def test_认可两次_确认次数累加且状态仍FACT(env, monkeypatch):
    """晋升仍走既有「confirmed≥2 + 高重要」竞争（方案 §2.3），本端点不自己判晋升。"""
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", False)
    mid = _seed(env["factory"], confirmation_count=0, importance=100.0)
    _patch(env, mid, {"epistemic_status": "FACT"})
    _patch(env, mid, {"epistemic_status": "FACT"})
    row = _row(env["factory"], mid)
    assert row.confirmation_count == 2
    assert row.epistemic_status == "FACT"
    assert row.source == PERCEPTION_SOURCE


def test_认可_值大小写与空白归一(env):
    mid = _seed(env["factory"])
    _patch(env, mid, {"epistemic_status": " fact "})
    assert _row(env["factory"], mid).epistemic_status == "FACT"


def test_认可_来源大小写归一(env):
    mid = _seed(env["factory"], source=" Perception ")
    _patch(env, mid, {"epistemic_status": "FACT"})
    assert _row(env["factory"], mid).epistemic_status == "FACT"


def test_认可_可与评星同请求共存(env):
    """body 同时带 importance 与 epistemic_status：两条语义都要生效（App 复用同一 PATCH）。"""
    mid = _seed(env["factory"], review_count=0)
    _patch(env, mid, {"importance": 5, "epistemic_status": "FACT"})
    row = _row(env["factory"], mid)
    assert row.importance == 100.0
    assert row.epistemic_status == "FACT"
    assert row.confirmation_count == 1


# ───────────────────────────── 拒收 ─────────────────────────────

@pytest.mark.parametrize("src", ["chat", "summary", "group", None])
def test_非感知来源_400(env, src):
    """防被当成通用改状态入口：其它来源一律 400。"""
    mid = _seed(env["factory"], source=src, epistemic_status="INFERRED", content=CHAT_TEXT)
    err = _status(mem_api.update_memory, memory_id=mid, data={"epistemic_status": "FACT"}, user_id=1, lang="zh")
    assert err.status_code == 400
    assert _row(env["factory"], mid).epistemic_status == "INFERRED"
    assert not env["receipts"]


@pytest.mark.parametrize("val", ["INFERRED", "PLANNED", "FICTIONAL", "UNVERIFIED", "", None, "TRUE", 123])
def test_非法值_400(env, val):
    mid = _seed(env["factory"])
    err = _status(mem_api.update_memory, memory_id=mid, data={"epistemic_status": val}, user_id=1, lang="zh")
    assert err.status_code == 400
    assert _row(env["factory"], mid).epistemic_status == "INFERRED"
    assert _row(env["factory"], mid).confirmation_count in (0, None)
    assert not env["receipts"]


def test_拒收时英文语言头给英文文案(env):
    mid = _seed(env["factory"], source="chat", content=CHAT_TEXT)
    err = _status(mem_api.update_memory, memory_id=mid, data={"epistemic_status": "FACT"}, user_id=1, lang="en")
    assert err.status_code == 400
    assert "phone-observation" in str(err.detail)


def test_越权_404(env):
    mid = _seed(env["factory"])
    err = _status(mem_api.update_memory, memory_id=mid, data={"epistemic_status": "FACT"}, user_id=2, lang="zh")
    assert err.status_code == 404
    assert _row(env["factory"], mid).epistemic_status == "INFERRED"


def test_不存在的id_404(env):
    err = _status(mem_api.update_memory, memory_id=999999, data={"epistemic_status": "FACT"}, user_id=1, lang="zh")
    assert err.status_code == 404


# ─────────────────────── 既有语义不劣化（撤回） ───────────────────────

def test_撤回复用归档分支(env):
    """「不记住」＝ is_archived=True，不删行、可逆（方案 §2.3 撤回动作）。"""
    mid = _seed(env["factory"])
    _patch(env, mid, {"is_archived": True})
    row = _row(env["factory"], mid)
    assert row.is_archived is True
    assert row.source == PERCEPTION_SOURCE
    assert not env["receipts"]          # 归档不写认可回执
    _patch(env, mid, {"is_archived": False})
    assert _row(env["factory"], mid).is_archived is False


def test_不带epistemic_status时旧行为不变(env):
    mid = _seed(env["factory"])
    before = _row(env["factory"], mid)
    pinned, conf, epi = before.is_pinned, before.confirmation_count, before.epistemic_status
    _patch(env, mid, {"is_pinned": True})
    row = _row(env["factory"], mid)
    assert (row.is_pinned, row.confirmation_count, row.epistemic_status) == (True, conf, epi)
    assert row.delete_at is None


# ───────────────────────────── 只读统计 ─────────────────────────────

def _stats(user_id=1):
    return asyncio.run(mem_api.get_perception_stats(user_id=user_id))


def test_stats_数值正确(env):
    _seed(env["factory"])                                        # 感知，未认可
    _seed(env["factory"], epistemic_status="FACT")               # 感知，已认可
    _seed(env["factory"], epistemic_status="FACT", is_core=True)  # 感知，已认可 + 进画像
    _seed(env["factory"], source="chat", content=CHAT_TEXT)
    _seed(env["factory"], source="chat", content="再一条聊天", is_core=True)
    _seed(env["factory"], user_id=2, character_id=202, source="chat", content="外人的记忆")
    s = _stats()
    assert s["perception_total"] == 3
    assert s["perception_accepted"] == 2
    assert s["core_total"] == 2
    assert s["core_perception"] == 1
    assert s["perception_ratio"] == pytest.approx(3 / 5, abs=1e-4)   # 分母＝本账号可见总数（不含外人）
    assert s["core_perception_ratio"] == pytest.approx(0.5)


def test_stats_空库返回0不报错(env):
    s = _stats()
    assert s == {"perception_total": 0, "perception_accepted": 0, "perception_ratio": 0,
                 "core_perception": 0, "core_total": 0, "core_perception_ratio": 0}


def test_stats_无感知条时比例为0(env):
    _seed(env["factory"], source="chat", content=CHAT_TEXT)
    s = _stats()
    assert s["perception_total"] == 0 and s["perception_ratio"] == 0
    assert s["core_perception_ratio"] == 0


def test_stats_跨家庭隔离(env):
    _seed(env["factory"], user_id=2, character_id=202, content="外人的感知")
    assert _stats(2)["perception_total"] == 1
    assert _stats(1)["perception_total"] == 0


def test_stats_只读不写库(env, monkeypatch):
    """统计端点不得写库：真跑一遍（不打桩回执）也不产生任何行。"""
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "memory_write_receipt", True)
    _seed(env["factory"])
    before = len(asyncio.run(_all_memories(env["factory"])))
    _stats()
    assert len(asyncio.run(_all_memories(env["factory"]))) == before


async def _all_memories(factory):
    async with factory() as db:
        return (await db.execute(select(Memory))).scalars().all()


def test_stats路由不被memory_id吞掉():
    """/{memory_id} 的 id 是 int，路径段也不同；仍钉住注册顺序能命中本端点。"""
    paths = [(r.path, getattr(r, "methods", None)) for r in mem_api.router.routes]
    hit = [p for p in paths if p[0] == "/api/v1/memories/stats/perception"]
    assert hit and "GET" in hit[0][1]
    idx = [p[0] for p in paths].index("/api/v1/memories/stats/perception")
    assert idx < [p[0] for p in paths].index("/api/v1/memories/{memory_id}")
