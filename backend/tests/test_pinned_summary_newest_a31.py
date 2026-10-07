# -*- coding: utf-8 -*-
"""A31 回归测试：记忆本「事件 / 印象」置顶摘要不更新（后端三条缺陷）。

对应派单（2026-10-07 A31）坐实的三个缺陷：
1. **选条错位**：同 (角色, 类型) 有多条置顶时，节流看「时间最新」那条、重写却写 `existing[0]`
   （查询无 order_by ⇒ SQLite 按 rowid 升序 ⇒ 最旧那条）⇒ 新内容写进前端不显示的旧条；
2. **不收口**：历史上混入的第二条置顶永远并存，永远不被降级；
3. **共桶污染**：`user_info` 的普通印象摘要与 `sub_type=identity` 身份画像共用一个桶，
   画像顶掉印象位，并且两边互相误降级。

语义约束（本批不改的东西）：节流时长（6h / 24h）、提示词、摘要生成逻辑、返回值键、
`list_memories` 排序都不动；收口只降级 `is_pinned`，**不物理删行**。

纪律：临时库走 tests/_dbclone（禁止连生产库）；LLM 全打桩零计费；项目未装 pytest-asyncio，统一 asyncio.run。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.memory import summary as _summary
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.user import User
from app.utils.timeutil import now_naive_utc

pytestmark = pytest.mark.slow

CHAR_ID = 201
USER_ID = 1
NEW_TEXT = "A31 凝练后的新置顶内容"
OLD_SUMMARY = "旧的那条印象摘要"
NEW_SUMMARY = "新的那条印象摘要"
OLD_IDENTITY = "旧的那条身份画像"
NEW_IDENTITY = "新的那条身份画像"
MIDDLE_SUMMARY = "中间时间的旧置顶"
MATERIAL = "用户说过这周要交实验报告"


def _ago(**kw):
    """相对 now_naive_utc 的 naive 时间戳（与库内存储约定一致）。"""
    return now_naive_utc() - timedelta(**kw)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + LLM 打桩：prompts 记录每次调用的提示词，responses 为待返回摘要文本队列。"""
    engine = clone_engine(tmp_path / "a31.db")
    factory = make_session_factory(engine)

    async def _seed_owner():
        async with factory() as db:
            db.add(User(id=USER_ID, username="a31_u1", nickname="A31 用户"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="A31 角色"))
            await db.commit()

    asyncio.run(_seed_owner())
    monkeypatch.setattr(_summary, "async_session_factory", factory)

    prompts: list[str] = []
    box = {"responses": []}

    async def _llm(messages=None, **_kw):
        prompts.append((messages or [{}])[0].get("content", ""))
        queue = box["responses"]
        return queue.pop(0) if queue else NEW_TEXT

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
    monkeypatch.setattr("app.memory.flags.memory_v2_enabled", _v2_true)
    # 身份画像灰度默认放行（本文件只观察选条/收口，不测开关面）

    yield {"factory": factory, "prompts": prompts, "responses": box["responses"], "box": box}
    asyncio.run(engine.dispose())


async def _v2_true(*_a, **_kw):
    return True


def _add(factory, *, content, mtype="event", sub_type=None, pinned=False, at=None,
         importance=50.0, source="chat"):
    """直接落一条记忆；`at` 同时写 created_at 与 updated_at，便于精确构造新旧顺序。"""
    async def _run():
        async with factory() as db:
            ts = at or now_naive_utc()
            m = Memory(user_id=USER_ID, character_id=CHAR_ID, memory_type=mtype,
                       sub_type=sub_type, source=source, content=content,
                       importance=importance, is_pinned=pinned, created_at=ts)
            m.updated_at = ts
            db.add(m)
            await db.commit()
            return m.id
    return asyncio.run(_run())


def _rows(factory):
    """按本角色取全部未归档记忆（含被降级的旧置顶）。"""
    async def _run():
        async with factory() as db:
            return list((await db.execute(
                select(Memory).where(Memory.character_id == CHAR_ID,
                                     Memory.is_archived == False)  # noqa: E712
            )).scalars().all())
    return asyncio.run(_run())


def _pinned(factory, mtype="event", sub_types=None):
    rows = [r for r in _rows(factory) if r.memory_type == mtype and r.is_pinned]
    if sub_types is not None:
        rows = [r for r in rows if (r.sub_type or "summary") in sub_types]
    return rows


# ───────────── 缺陷 1＋2：写最新条 + 同组旧置顶收口 ─────────────

def test_三条置顶_force重生成_只留最新那条并写在该条(env):
    """选条与写条必须是同一条（时间最新），且同组其余置顶一并降级。

    刻意让「时间最新」那条 id 居中（rowid 升序时 existing[0]＝最旧那条），
    这样旧缺陷（写 existing[0]）会直接咬断本用例。
    """
    factory = env["factory"]
    oldest = _add(factory, content=OLD_SUMMARY, sub_type="summary",
                  pinned=True, at=_ago(days=3))
    newest = _add(factory, content=NEW_SUMMARY, sub_type="summary",
                  pinned=True, at=_ago(days=1))
    middle = _add(factory, content=MIDDLE_SUMMARY, sub_type="summary",
                  pinned=True, at=_ago(days=2))
    _add(factory, content=MATERIAL)  # 摘要原料（非置顶）

    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=True))

    assert out["generated"] is True
    assert out["memory_id"] == newest                       # 写时间最新那条，不是 rowid 最小那条
    rows = {r.id: r for r in _rows(factory)}
    assert rows[newest].content == NEW_TEXT
    assert rows[newest].importance == 100.0
    assert rows[newest].is_pinned is True
    # 收口：同组其余旧置顶降级，但保留行、内容可追溯
    assert [r.id for r in _pinned(factory, "event")] == [newest]
    assert rows[oldest].is_pinned is False and rows[oldest].content == OLD_SUMMARY
    assert rows[middle].is_pinned is False and rows[middle].content == MIDDLE_SUMMARY


def test_收口后再次重生成_仍是同一行覆盖且只有一条置顶(env):
    """连续两次 force 重生成（日终任务＋手动重生成会这么走）不再产出第二条置顶。"""
    factory = env["factory"]
    first = _add(factory, content=OLD_SUMMARY, sub_type="summary", pinned=True, at=_ago(days=3))
    _add(factory, content=MATERIAL)
    env["box"]["responses"] = ["第一次重生成内容", "第二次重生成内容"]

    r1 = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=True))
    r2 = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=True))

    assert r1["memory_id"] == r2["memory_id"] == first      # 同一行覆盖式重写
    assert len(_pinned(factory, "event")) == 1
    assert {r.id: r.content for r in _rows(factory)}[first] == "第二次重生成内容"


# ───────────── 缺陷 3：user_info 普通摘要与 identity 分桶 ─────────────

def test_普通印象摘要_不降级identity桶(env):
    """普通摘要只收口自己的桶：identity 既不进 existing，也不被误降级。

    旧口径下 existing 含 2 条 identity，其中「1 小时前」那条时间最新 ⇒ 摘要会被写进身份画像。
    """
    factory = env["factory"]
    s_old = _add(factory, content=OLD_SUMMARY, mtype="user_info", sub_type="summary",
                 pinned=True, at=_ago(days=5))
    s_new = _add(factory, content=NEW_SUMMARY, mtype="user_info", sub_type="summary",
                 pinned=True, at=_ago(days=1))
    i_new = _add(factory, content=NEW_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=_ago(hours=1))
    i_old = _add(factory, content=OLD_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=_ago(days=3))
    _add(factory, content=MATERIAL, mtype="user_info")

    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "user_info", force=True))

    assert out["memory_id"] == s_new                        # 落在普通摘要桶的最新条
    rows = {r.id: r for r in _rows(factory)}
    assert rows[s_new].content == NEW_TEXT
    assert rows[s_old].is_pinned is False                   # 自己桶里的旧置顶被收口
    assert rows[s_old].content == OLD_SUMMARY               # 只降级不删行
    # identity 桶原样未动
    assert rows[i_new].is_pinned is True and rows[i_new].content == NEW_IDENTITY
    assert rows[i_old].is_pinned is True and rows[i_old].content == OLD_IDENTITY
    assert len(_pinned(factory, "user_info", {"identity"})) == 2


def test_身份画像重生成_只留最新identity且不碰普通摘要(env):
    """summarize_identity 同构：只留最新那条 identity 置顶，普通摘要桶一条都不碰。"""
    factory = env["factory"]
    s_new = _add(factory, content=NEW_SUMMARY, mtype="user_info", sub_type="summary",
                 pinned=True, at=_ago(days=1))
    i_old = _add(factory, content=OLD_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=_ago(days=3))
    i_new = _add(factory, content=NEW_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=_ago(hours=1))
    _add(factory, content=MATERIAL, mtype="user_info")

    out = asyncio.run(_summary.summarize_identity(CHAR_ID, USER_ID, force=True))

    assert out["generated"] is True and out["memory_id"] == i_new
    rows = {r.id: r for r in _rows(factory)}
    assert rows[i_new].content == NEW_TEXT
    assert rows[i_old].is_pinned is False and rows[i_old].content == OLD_IDENTITY
    assert rows[s_new].is_pinned is True and rows[s_new].content == NEW_SUMMARY
    assert len(_pinned(factory, "user_info", {"identity"})) == 1
    assert len(_pinned(factory, "user_info", {"summary"})) == 1


def test_仅有identity置顶时_普通摘要另起一条不覆盖画像(env):
    """分桶后 user_info 的 existing 为空 ⇒ 走新建分支，普通摘要不再抢占身份画像那条。"""
    factory = env["factory"]
    i_new = _add(factory, content=NEW_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=_ago(hours=1))
    _add(factory, content=MATERIAL, mtype="user_info")

    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "user_info", force=True))

    assert out["generated"] is True and out["memory_id"] != i_new
    rows = {r.id: r for r in _rows(factory)}
    assert rows[i_new].content == NEW_IDENTITY and rows[i_new].is_pinned is True
    fresh = rows[out["memory_id"]]
    assert (fresh.memory_type, fresh.sub_type) == ("user_info", "summary")
    assert fresh.is_pinned is True and fresh.user_id == USER_ID


# ───────────── 节流分支：不足 TTL 早退、零写入 ─────────────

def test_普通摘要节流_看最新那条且不改任何行(env):
    """6h 内不重生成：节流仍取「时间最新」那条；早退后零 LLM 调用、零写入（含不收口）。"""
    factory = env["factory"]
    old = _add(factory, content=OLD_SUMMARY, sub_type="summary", pinned=True, at=_ago(days=10))
    fresh_ts = _ago(hours=1)
    fresh = _add(factory, content=NEW_SUMMARY, sub_type="summary", pinned=True, at=fresh_ts)
    _add(factory, content=MATERIAL)

    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=False))

    assert out == {"generated": False, "memory_id": fresh, "reason": "throttled"}
    assert env["prompts"] == []                             # 零计费
    rows = {r.id: r for r in _rows(factory)}
    assert rows[fresh].content == NEW_SUMMARY and rows[fresh].updated_at == fresh_ts
    assert rows[old].is_pinned is True                      # 未收口（本批只在真重生成时收口）
    assert all(r.content != NEW_TEXT for r in rows.values())


def test_身份画像节流_24小时内不改任何行(env):
    factory = env["factory"]
    ts = _ago(hours=2)
    ident = _add(factory, content=NEW_IDENTITY, mtype="user_info", sub_type="identity",
                 pinned=True, at=ts)
    _add(factory, content=MATERIAL, mtype="user_info")

    out = asyncio.run(_summary.summarize_identity(CHAR_ID, USER_ID, force=False))

    assert out == {"generated": False, "memory_id": ident, "reason": "throttled"}
    assert env["prompts"] == []
    rows = {r.id: r for r in _rows(factory)}
    assert rows[ident].content == NEW_IDENTITY and rows[ident].updated_at == ts


def test_零置顶时新建一条摘要_行为不变(env):
    """原有行为保持：该类型没有置顶时新建 sub_type=summary 的置顶条。"""
    factory = env["factory"]
    _add(factory, content=MATERIAL)

    out = asyncio.run(_summary.summarize_memories(CHAR_ID, "event", force=False))

    assert out["generated"] is True
    pinned = _pinned(factory, "event")
    assert len(pinned) == 1 and pinned[0].id == out["memory_id"]
    assert pinned[0].sub_type == "summary" and pinned[0].content == NEW_TEXT
