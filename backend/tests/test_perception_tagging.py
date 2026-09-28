# -*- coding: utf-8 -*-
"""批 0-2 · M1a「感知打标接线 + 跨来源禁合并 + 召回补 source」测试。

覆盖（派单 §三 第 5 项逐条对齐）：
- flag 关 ⇒ 打标不发生、合并行为逐字节旧；
- flag 开 + 感知语料为空 ⇒ 不启用判据、不报错；
- flag 开 + 正文命中标签残留 / 长词重合 ⇒ source=perception、epistemic_status=INFERRED、回执写出；
- flag 开 + 不命中 ⇒ 保持原 source；
- 跨来源不合并（感知条 vs 既有 chat 条不被合并；两个 chat 条照旧合并；既有感知条也不被 chat 条并入）；
- curated 侧近似合并同口径（events/facts.py）；
- 打标路径抛异常 ⇒ 记忆照旧落库（fail-open，绝不写丢）；
- retrieve 输出含 source/sub_type，且条数与顺序不受影响。

纪律：临时库走 tests/_dbclone（禁止连生产库）；不新增 LLM/向量调用（嵌入与向量查重全部打桩）；
项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.memory import write as _write
from app.memory.perception_tier import (
    PERCEPTION_SOURCE,
    SNAPSHOT_WINDOW_MINUTES,
)

pytestmark = pytest.mark.slow

SNAP = "开会迟到五分钟被主管说了一顿"          # 感知快照正文
MEM_HIT = "他今天开会迟到五分钟被主管说了一顿"    # 与 SNAP 连续重合 ≥6 字 ⇒ 主判据命中
MEM_MISS = "用户喜欢在阳台养几盆多肉"            # 与 SNAP 无重合


@pytest.fixture(autouse=True)
def clean_flags():
    """相关 flag 前后复位（autouse：本文件任何用例都不依赖本机 runtime_flags 现值）。"""
    def _reset():
        _loop.AGENT_FLAGS["perception_source_tag"] = False
        _loop.AGENT_FLAGS["memory_admission_gate"] = False
        _loop.AGENT_FLAGS["memory_write_receipt"] = False
        _loop.AGENT_FLAGS["memory_trace_debug"] = False
    _reset()
    yield
    _reset()


def _flag(on: bool):
    """把打标总闸设成指定值。"""
    _loop.AGENT_FLAGS["perception_source_tag"] = on


# ───────────────────────── 纯函数：flag 登记 + 跨来源谓词 ─────────────────────────

def test_新flag已登记且默认关():
    """flag 的唯一事实源是 AGENT_FLAGS；默认必须 False（关＝逐字节旧行为）。"""
    assert "perception_source_tag" in _loop.AGENT_FLAGS
    assert _loop.AGENT_FLAGS["perception_source_tag"] is False


def test_新flag已登记展示元数据():
    from app.application.flag_catalog import FLAG_CATALOG

    meta = FLAG_CATALOG["perception_source_tag"]
    assert meta["group"] == "memory"
    assert meta["visible"] is False
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]
    # 既有口径：catalog 只承载展示元数据，不承载默认值（默认值唯一事实源＝AGENT_FLAGS）
    assert "default" not in meta and "enabled" not in meta


def test_谓词_flag关恒不阻断():
    assert _write._cross_source_merge_blocked(PERCEPTION_SOURCE, "chat") is False
    assert _write._cross_source_merge_blocked("chat", PERCEPTION_SOURCE) is False


@pytest.mark.parametrize("incoming,candidate,exp", [
    (PERCEPTION_SOURCE, "chat", True),      # 感知 → 非感知：禁
    ("chat", PERCEPTION_SOURCE, True),      # 非感知 → 感知：禁（双向）
    (PERCEPTION_SOURCE, "moment", True),
    (PERCEPTION_SOURCE, None, True),        # 候选行无来源＝非感知，同样禁
    ("chat", "chat", False),                # 同来源照旧
    (PERCEPTION_SOURCE, PERCEPTION_SOURCE, False),
    ("chat", None, False),                  # 两边都不是感知：照旧
    (None, None, False),
    ("moment", "diary", False),
    (" Perception ", "CHAT", True),         # 大小写 / 首尾空白归一
])
def test_谓词_flag开按收窄口径(incoming, candidate, exp):
    _flag(True)
    assert _write._cross_source_merge_blocked(incoming, candidate) is exp


def test_谓词_write与facts两处口径一致():
    """memory 侧与 curated 侧必须同判（否则禁令在一个口子上失效，方案风险 R2/R4）。"""
    import app.events.facts as _facts

    pairs = [(PERCEPTION_SOURCE, "chat"), ("chat", PERCEPTION_SOURCE),
             ("chat", "chat"), (PERCEPTION_SOURCE, PERCEPTION_SOURCE),
             ("chat", None), (None, None), (" Perception ", "CHAT"), (PERCEPTION_SOURCE, None)]
    for on in (False, True):
        _flag(on)
        for a, b in pairs:
            assert _write._cross_source_merge_blocked(a, b) == _facts._cross_source_merge_blocked(a, b), (a, b, on)


def test_谓词_开关读取异常回落不阻断():
    """读不到 flag 一律按「关」处理（R8：绝不因为读开关失败而改变写入结果）。"""
    _flag(True)
    saved = _loop.AGENT_FLAGS
    try:
        _loop.AGENT_FLAGS = None  # 模拟开关面不可用（导入失败 / 字典丢失）
        assert _write._perception_tag_on() is False
        assert _write._cross_source_merge_blocked(PERCEPTION_SOURCE, "chat") is False
    finally:
        _loop.AGENT_FLAGS = saved


# ─────────────────────── 集成：save_memory 打标 / 禁合并 ───────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 打桩外部副作用（嵌入/向量查重/后台任务/晋升/回执），只观察落库与接线。"""
    engine = clone_engine(tmp_path / "t.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="tag_u1", nickname="打标用户"))
            db.add(AICharacter(id=101, user_id=1, name="打标角色101"))
            await db.commit()

    asyncio.run(_seed_parents())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_similar(*_a, **_kw):
        return None

    async def _no_add(**_kw):
        return None

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _no_similar)
    monkeypatch.setattr(svc, "add_memory", _no_add)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    import app.memory.dedup as dd
    monkeypatch.setattr(dd, "_schedule_dedup", _noop)

    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())

    receipts = []

    def _rec(character_id, memory_id, action, *, reason="", detail=None):
        receipts.append({"memory_id": memory_id, "action": action, "reason": reason, "detail": detail})

    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", _rec)

    async def _promote(*_a, **_kw):
        return None

    monkeypatch.setattr("app.memory.core.maybe_promote_core", _promote)
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook", _noop)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _noop())

    yield {"factory": factory, "receipts": receipts}
    asyncio.run(engine.dispose())


def _add_snapshot(factory, content, *, user_id=1, minutes_ago=0, source="accessibility"):
    """落一条手机快照；minutes_ago 控制它是否落在 30 分钟窗内。"""
    from app.models.device import PhoneSnapshot
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            db.add(PhoneSnapshot(
                user_id=user_id, source=source, content=content,
                created_at=now_naive_utc() - timedelta(minutes=minutes_ago),
            ))
            await db.commit()

    asyncio.run(_run())


def _rows(factory):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            return (await db.execute(select(Memory).order_by(Memory.id))).scalars().all()

    return asyncio.run(_run())


def _seed_memory(factory, content, *, source="chat", memory_type="event"):
    from app.models.memory import Memory

    async def _run():
        async with factory() as db:
            db.add(Memory(user_id=1, character_id=101, memory_type=memory_type,
                          content=content, source=source, importance=50.0))
            await db.commit()

    asyncio.run(_run())


def _save(**kw):
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 3)
    kw.setdefault("source", "chat")
    from app.memory.write import save_memory
    return asyncio.run(save_memory(**kw))


def test_flag关_不打标_逐字节旧行为(env):
    """语料齐备但 flag 关：来源/认知状态/回执全部维持旧结果（不打标、不写 update 回执）。"""
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == "chat"
    assert m.epistemic_status == "FACT"
    assert [r.source for r in _rows(env["factory"])] == ["chat"]
    assert not [r for r in env["receipts"] if r["action"] == "update"]


def test_flag开_语料为空_不启用不报错(env):
    """本轮没有任何快照 ⇒ 判据不启用（方案 §2.4：语料为空一律不启用），来源照旧。"""
    _flag(True)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == "chat"
    assert m.epistemic_status == "FACT"


def test_flag开_命中长词重合_改写来源并写回执(env):
    """主判据（长词重合）命中：落库前改写 source=perception + INFERRED，并写一条回执。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == PERCEPTION_SOURCE
    assert m.epistemic_status == "INFERRED"
    upd = [r for r in env["receipts"] if r["action"] == "update"]
    assert len(upd) == 1
    assert upd[0]["memory_id"] == m.id
    assert upd[0]["detail"]["from_source"] == "chat"
    assert upd[0]["detail"]["to_source"] == PERCEPTION_SOURCE
    assert upd[0]["detail"]["epistemic_status"] == "INFERRED"


def test_flag开_命中标签残留_改写来源(env):
    """辅助判据（快照时间标签残留）命中：即使正文与快照无长词重合也应标出。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content="[屏幕 3分钟前] " + MEM_MISS)
    assert m is not None
    assert m.source == PERCEPTION_SOURCE
    assert m.epistemic_status == "INFERRED"


def test_flag开_不命中_保持原来源(env):
    """既无标签残留、又无长词重合 ⇒ 不动来源（用户真说过的话不受影响）。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_MISS)
    assert m is not None
    assert m.source == "chat"
    assert m.epistemic_status == "FACT"
    assert not [r for r in env["receipts"] if r["action"] == "update"]


def test_flag开_窗口外语料不参与打标(env):
    """快照超出 30 分钟窗（与注入侧同口径）⇒ 本轮感知语料为空 ⇒ 不启用判据。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP, minutes_ago=SNAPSHOT_WINDOW_MINUTES + 30)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == "chat"


def test_flag开_感知条不被并入既有chat条(env):
    """禁令 3 正向：命中打标的正文与既有 chat 记忆逐字相同也不并（否则打标白做）。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    _seed_memory(env["factory"], SNAP)
    m = _save(content=SNAP)
    assert m is not None
    rows = _rows(env["factory"])
    assert len(rows) == 2
    assert [r.source for r in rows] == ["chat", PERCEPTION_SOURCE]
    assert not [r for r in env["receipts"] if r["action"] == "merge"]


def test_flag开_既有感知条不被chat新条并入(env):
    """禁令 3 反向：新条按旧行为落库（本轮无感知），也不得并进既有感知条。"""
    _flag(True)
    _seed_memory(env["factory"], SNAP, source=PERCEPTION_SOURCE)
    m = _save(content=SNAP)
    assert m is not None
    assert m.source == "chat"
    assert [r.source for r in _rows(env["factory"])] == [PERCEPTION_SOURCE, "chat"]


def test_flag开_两个chat条照旧合并(env):
    """收窄口径校验：两边都不是感知 ⇒ 合并行为不得改变（同来源照旧）。"""
    _flag(True)
    _seed_memory(env["factory"], SNAP)
    m = _save(content=SNAP)
    assert m is not None
    assert len(_rows(env["factory"])) == 1
    assert [r["action"] for r in env["receipts"]] == ["merge"]


def test_flag关_跨来源照旧合并_逐字节旧行为(env):
    """flag 关 ⇒ 禁令撤除：拨关一个 bool 即恢复旧行为（方案 §六 回退口径）。"""
    _seed_memory(env["factory"], SNAP, source=PERCEPTION_SOURCE)
    m = _save(content=SNAP)
    assert m is not None
    assert len(_rows(env["factory"])) == 1
    assert [r["action"] for r in env["receipts"]] == ["merge"]


def test_打标路径抛异常_fail_open不丢记忆(env, monkeypatch):
    """快照查询炸掉：记忆必须照旧落库（来源不改）且不向上抛——绝不把记忆写丢。"""
    _flag(True)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query blew up")

    monkeypatch.setattr(_write, "_recent_snapshots", _boom)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == "chat"
    assert m.epistemic_status == "FACT"
    assert len(_rows(env["factory"])) == 1


def test_打标只标注不拒收(env):
    """判据命中后记忆仍然落库（只标注、不拒收）：用户真说过的话绝不允许被丢弃。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert len(_rows(env["factory"])) == 1
    assert [r["action"] for r in env["receipts"]] == ["update", "create"]


# ─────────────────────────── 集成：curated 近似合并 ───────────────────────────

def _curated_rows(factory):
    from app.models.memory import WorldFact

    async def _run():
        async with factory() as db:
            return (await db.execute(select(WorldFact).order_by(WorldFact.id))).scalars().all()

    return asyncio.run(_run())


def _assert_curated(factory, source):
    from app.events.facts import assert_curated

    async def _run():
        async with factory() as db:
            row = await assert_curated(
                db, character_id=101, user_id=1, kind="fact",
                object_value=SNAP, source=source,
            )
            await db.commit()
            return row
    return asyncio.run(_run())


def test_curated_跨来源不合并_flag开新增行(env):
    """curated 近似合并同受禁令 3：感知来的同义表述不得并掉既有 chat 来源的权威行。"""
    _flag(True)
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    first = _assert_curated(env["factory"], "chat")
    second = _assert_curated(env["factory"], PERCEPTION_SOURCE)
    rows = _curated_rows(env["factory"])
    assert len(rows) == 2
    assert [r.source for r in rows] == ["chat", PERCEPTION_SOURCE]
    assert second.id != first.id


def test_curated_同来源照旧合并_flag开(env):
    """两边都是 chat：近似合并行为不变（同来源照旧）。"""
    _flag(True)
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    first = _assert_curated(env["factory"], "chat")
    second = _assert_curated(env["factory"], "chat")
    rows = _curated_rows(env["factory"])
    assert len(rows) == 1
    assert first.id == second.id == rows[0].id


def test_curated_flag关_跨来源照旧合并(env):
    """flag 关 ⇒ 禁令撤除，回到逐字节旧行为（并给既有权威行）。"""
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    first = _assert_curated(env["factory"], "chat")
    second = _assert_curated(env["factory"], PERCEPTION_SOURCE)
    rows = _curated_rows(env["factory"])
    assert len(rows) == 1
    assert first.id == second.id


# ─────────────────────────── 集成：召回输出补 source ───────────────────────────

# flag 关时的既有输出键集合（用于钉「只加字段、不减不改」）
_OLD_KEYS = {"id", "content", "type", "importance", "created_at", "epistemic_status",
             "speaker_id", "speaker_type", "reliability_score", "contradiction_count",
             "why_it_matters", "status"}


@pytest.fixture()
def renv(monkeypatch, tmp_path):
    """召回用临时库：双路（向量 / BM25）打桩为空 → 走 LIKE 兜底 → 统一 _rerank → _final。"""
    engine = clone_engine(tmp_path / "r.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.memory import Memory
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="rec_u1", nickname="召回用户"))
            db.add(AICharacter(id=101, user_id=1, name="召回角色101"))
            db.add(Memory(user_id=1, character_id=101, memory_type="event",
                          content=SNAP, source=PERCEPTION_SOURCE,
                          sub_type="extracted", importance=50.0))
            db.add(Memory(user_id=1, character_id=101, memory_type="event",
                          content="迟到这件事以后再聊", source="chat",
                          sub_type="slot", importance=40.0))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_text):
        return [0.0] * 8

    async def _no_hits(**_kw):
        return []

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "vector_search", _no_hits)
    monkeypatch.setattr(svc, "bm25_search", _no_hits)
    # 召回末尾的检索 trace 是 fire-and-forget 真写库（用的是生产会话），测试里必须掐掉
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook_collect", _noop)

    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _search(query="迟到", limit=5):
    from app.memory.retrieve import search_memories
    return asyncio.run(search_memories(101, query, limit=limit))


def test_retrieve_flag关_输出键逐字节旧(renv):
    res = _search()
    assert len(res) == 2
    assert set(res[0]) == _OLD_KEYS


def test_retrieve_flag开_补source且条数顺序不变(renv):
    off_ids = [r["id"] for r in _search()]
    _flag(True)
    res = _search()
    assert [r["id"] for r in res] == off_ids      # 条数与顺序都不变
    assert set(res[0]) == _OLD_KEYS | {"source", "sub_type"}
    by_src = {r["source"]: r for r in res}
    assert by_src[PERCEPTION_SOURCE]["sub_type"] == "extracted"
    assert by_src["chat"]["sub_type"] == "slot"
