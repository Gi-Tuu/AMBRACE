# -*- coding: utf-8 -*-
"""断点 #5（2026-09-29）：感知派生条禁止默认落成「用户陈述」。

覆盖（派单 §要做的 第 4 项逐条对齐）：
- 打标命中 ⇒ 落库 speaker_type/speaker_id 为空（既不是 user，也不是 character）；
- 调用方显式传 speaker_type=user（提取器路径的常态）⇒ 命中后一并撤掉，不留残留归属；
- 未命中 ⇒ 调用方给的归属原样落库（用户真说过的话不受影响）；
- flag 关 ⇒ 逐字节旧行为（speaker=user/user_id、source=chat、无打标回执）；
- `_resolve_admission_sender` 的 perception 分支：flag 开且来源为感知 ⇒ 返回 perception，
  排在显式 speaker_type 与来源消息 sender_type 之前（绝不回落 user）；flag 关 ⇒ 旧判定；
- 非感知来源的既有判定逐条不变（diary/life→character、mcp/tool→tool、ai 消息→character、兜底→user）；
- 脏输入（None / 非字符串 / 空白 / 大小写）不抛；
- 打标 fail-open ⇒ 归属改写与来源改写同批撤销；
- 回执留痕：打标回执记 speaker=perception，create 回执 provenance.actor=perception；
- 准入闸门裁决不变（只去掉 user 误标，不新增降级）。

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
from app.memory.perception_tier import PERCEPTION_SOURCE

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
    _reset()
    yield
    _reset()


def _flag(on: bool):
    """打标总闸（断点 #5 的归属改写挂同一个闸）。"""
    _loop.AGENT_FLAGS["perception_source_tag"] = on


# ─────────────────────────── 临时库 + 外部副作用打桩 ───────────────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 打桩（嵌入/向量查重/后台任务/晋升/回执），只观察落库与接线。"""
    engine = clone_engine(tmp_path / "t.db")
    factory = make_session_factory(engine)

    async def _seed_parents():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=1, username="spk_u1", nickname="归属用户"))
            db.add(AICharacter(id=101, user_id=1, name="归属角色101"))
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


def _save(**kw):
    kw.setdefault("user_id", 1)
    kw.setdefault("character_id", 101)
    kw.setdefault("memory_type", "event")
    kw.setdefault("importance", 3)
    kw.setdefault("source", "chat")
    from app.memory.write import save_memory
    return asyncio.run(save_memory(**kw))


# ─────────────────────── 集成：打标命中后 speaker 不再是 user ───────────────────────

def test_打标命中_speaker不落成user也不伪造character(env):
    """断点 #5 主断言：命中打标后两列为空（既非 user 亦非 character），来源仍是 perception。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT)
    assert m is not None
    assert m.source == PERCEPTION_SOURCE
    assert m.speaker_type not in ("user", "character"), m.speaker_type
    assert m.speaker_type is None
    assert m.speaker_id is None
    assert m.epistemic_status == "INFERRED"
    row = _rows(env["factory"])[-1]
    assert (row.speaker_type, row.speaker_id) == (None, None)


def test_打标命中_调用方显式user归属也被撤掉(env):
    """提取器路径（memory/extractor.py）会带着 speaker_type=user 进来：命中后必须一起撤，
    否则库里仍是「用户陈述」，本断点只修了一半。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    assert (m.speaker_type, m.speaker_id) == (None, None)
    assert m.source == PERCEPTION_SOURCE


def test_打标不命中_既有归属逐字节不变(env):
    """未命中判据 ⇒ 用户亲口陈述的归属照旧落库（不空、不改写）。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_MISS, speaker_type="user", speaker_id=1)
    assert m.source == "chat"
    assert (m.speaker_type, m.speaker_id) == ("user", 1)


def test_flag关_逐字节旧行为(env):
    """语料齐备、正文命中标签，但 flag 关 ⇒ 归属兜底成 user/user_id、来源仍是 chat、不打标回执。"""
    _add_snapshot(env["factory"], SNAP)
    m = _save(content="[屏幕 3分钟前] " + MEM_MISS)
    assert m.source == "chat"
    assert (m.speaker_type, m.speaker_id) == ("user", 1)
    assert m.epistemic_status == "FACT"
    assert not [r for r in env["receipts"] if r["action"] == "update"]


def test_failopen_归属改写与来源改写同批撤销(env, monkeypatch):
    """打标过程抛异常 ⇒ 来源/通道/归属一起回到打标前的值（绝不留下「来源旧、归属空」的半改状态）。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)

    async def _boom(*_a, **_kw):
        raise RuntimeError("snapshot query failed")

    monkeypatch.setattr(_write, "_recent_snapshots", _boom)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    assert m is not None
    assert m.source == "chat"
    assert (m.speaker_type, m.speaker_id) == ("user", 1)


def test_直接写perception来源_不兜底成user(env):
    """未走打标（调用方直接给 source=perception）时同样不兜底 user：判据看来源列，不看命中过程。"""
    _flag(True)
    m = _save(content=MEM_MISS, source=PERCEPTION_SOURCE, epistemic_status="INFERRED")
    assert (m.speaker_type, m.speaker_id) == (None, None)
    assert m.source == PERCEPTION_SOURCE


def test_flag关_直接写perception来源仍按旧兜底(env):
    """同上一条对照：flag 关 ⇒ 兜底逻辑逐字节旧行为（哪怕来源已是 perception）。"""
    m = _save(content=MEM_MISS, source=PERCEPTION_SOURCE, epistemic_status="INFERRED")
    assert (m.speaker_type, m.speaker_id) == ("user", 1)


def test_打标回执与create回执都记下perception归属(env):
    """「这条来自感知」的可区分性：打标回继承认改写后的归属，create 回执 provenance.actor 不再空。"""
    _flag(True)
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    upd = [r for r in env["receipts"] if r["action"] == "update"]
    assert len(upd) == 1
    assert upd[0]["memory_id"] == m.id
    assert upd[0]["detail"]["speaker"] == "perception"
    assert upd[0]["detail"]["speaker_type"] is None
    assert upd[0]["detail"]["from_speaker_type"] == "user"
    assert upd[0]["detail"]["to_source"] == PERCEPTION_SOURCE  # 既有键不被动
    cre = [r for r in env["receipts"] if r["action"] == "create"]
    assert cre and cre[-1]["detail"]["provenance"]["actor"] == "perception"


def test_准入闸门开_感知条裁决与降级不变(env):
    """断点 #5 只去「用户陈述」误标，不改裁决：感知条仍是 INFERRED、不因归属变化被额外降级/待核。"""
    _flag(True)
    _loop.AGENT_FLAGS["memory_admission_gate"] = True
    _add_snapshot(env["factory"], SNAP)
    m = _save(content=MEM_HIT, speaker_type="user", speaker_id=1)
    assert m.source == PERCEPTION_SOURCE
    assert m.epistemic_status == "INFERRED"
    assert (m.speaker_type, m.speaker_id) == (None, None)
    assert not [r for r in env["receipts"] if "pending review" in (r["reason"] or "")]


# ─────────────────────── 纯判定：_resolve_admission_sender ───────────────────────

def test_准入归属解析_perception分支不回落user():
    """感知来源排在显式 speaker_type / 来源消息 sender_type 之前：两个都是 user 也不许回落。"""
    _flag(True)
    got = asyncio.run(_write._resolve_admission_sender(
        PERCEPTION_SOURCE, 7, "user", "user", db=None))
    assert got == "perception"
    assert got != "user"
    # 也不伪造角色（派单约束 ①）
    assert got != "character"


def test_准入归属解析_来源归一后仍走perception分支():
    _flag(True)
    for src in (" Perception ", "PERCEPTION", "perception"):
        assert asyncio.run(_write._resolve_admission_sender(
            src, None, None, None, db=None)) == "perception"


def test_准入归属解析_flag关感知来源照旧回落user():
    """关闸 ⇒ 本分支整体不生效，逐字节旧判定（显式 user / 来源消息 user / 兜底 user）。"""
    for spk, msg in [("user", "user"), (None, "user"), (None, None)]:
        got = asyncio.run(_write._resolve_admission_sender(
            PERCEPTION_SOURCE, None, spk, msg, db=None))
        assert got == "user", (spk, msg, got)


@pytest.mark.parametrize("source,speaker_type,msg_sender,exp", [
    ("diary", None, None, "character"),        # 无来源消息的模型自述
    ("life", None, None, "character"),
    ("bio", None, None, "character"),
    ("mcp_tools", None, None, "tool"),
    ("search", None, None, "tool"),
    ("chat", None, "ai", "character"),          # 来源消息是 AI 台词
    ("chat", "system", None, "system"),         # 调用方显式覆盖优先
    ("chat", None, None, "user"),
    (None, None, None, "user"),                 # 兜底
    ("", "ai", None, "character"),
])
def test_准入归属解析_非感知来源逐条判定不变(source, speaker_type, msg_sender, exp):
    """开闸跑既有判定（对照）：新分支只拦 perception，其它来源一个字没动。"""
    _flag(True)
    assert asyncio.run(_write._resolve_admission_sender(
        source, None, speaker_type, msg_sender, db=None)) == exp
    _flag(False)
    assert asyncio.run(_write._resolve_admission_sender(
        source, None, speaker_type, msg_sender, db=None)) == exp


def test_来源谓词与归属常量_脏输入不抛():
    """_is_perception_source / PERCEPTION_SENDER 面向脏输入：非字符串、None、空白一律 False。"""
    assert _write.PERCEPTION_SENDER == "perception"
    assert len(_write.PERCEPTION_SENDER) <= 10  # memories.speaker_type 列宽（防未来取值变长越界）
    for bad in (None, "", "   ", 0, 1, [], {}, object(), b"perception", True):
        assert _write._is_perception_source(bad) is False
    assert _write._is_perception_source("perception") is True
    assert _write._is_perception_source(" PerCeption ") is True


def test_开关读取异常_按关处理不撤归属():
    """R8 同口径：开关面不可用（字典丢失 / 读取抛）⇒ 一律按「关」⇒ 打标与归属改写都不发生。"""
    _flag(True)
    saved = _loop.AGENT_FLAGS
    try:
        _loop.AGENT_FLAGS = None  # 模拟开关面不可用
        assert _write._perception_tag_on() is False
        assert _write._is_perception_source(PERCEPTION_SOURCE) is True

        class _Boom:
            def get(self, *_a, **_k):
                raise RuntimeError("flag face down")

        _loop.AGENT_FLAGS = _Boom()
        assert _write._perception_tag_on() is False
        assert _write._is_perception_source(PERCEPTION_SOURCE) is True
    finally:
        _loop.AGENT_FLAGS = saved
