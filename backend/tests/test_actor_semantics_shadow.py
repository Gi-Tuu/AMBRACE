# -*- coding: utf-8 -*-
"""P0 语义统一 · 第 1 步「actor 影子埋点」测试（2026-09-29）。

钉住五条底线（派单 §要求 5 逐条对齐）：
1. **判据照现状**：``judge_actor`` 的分支顺序与结果，对每一枚非查库入参都必须等于
   ``write.py::_resolve_admission_sender``（打标闸开/关两种状态下都对照）；
2. **纯判定零 IO**：不查库（给一个「一查就报错」的假会话也不影响判定）、不写库、不 mutate 入参、
   脏输入不抛异常；
3. **flag 关＝零行为**：``save_memory`` 落库字段与改动前一致，且一条影子日志都不打；
4. **flag 开＝只多留痕**：落库字段逐字段等于 flag 关时，唯一区别是多一条 INFO 日志；
   埋点内部抛错 fail-open，写入照旧；
5. **目录双向锁不破**：新键同时登记进 ``AGENT_FLAGS`` 与 ``flag_catalog``（默认关、组 memory、不可见）。

纪律：临时库走 ``tests/_dbclone``（禁止连生产库）；嵌入/向量查重/后台任务/晋升/事件/插件钩子全部
打桩，本用例不产生任何 LLM 或向量调用。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import types

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

from app.agent import loop as _loop
from app.application.flag_catalog import FLAG_CATALOG
from app.flags.agent_flags import AGENT_FLAGS
from app.memory import actor_shadow as _shadow
from app.memory import write as _write

pytestmark = pytest.mark.slow

SHADOW_MARK = "actor shadow"
UID = 1
CHARS = (101, 102, 103, 104)


@pytest.fixture(autouse=True)
def clean_flags():
    """相关 flag 前后复位（本文件任何用例都不依赖本机 runtime_flags 现值）。"""
    keys = ("actor_semantics_shadow", "perception_source_tag", "memory_admission_gate",
            "memory_write_receipt", "memory_trace_debug", "review_plan_validity_extract")

    def _reset():
        for key in keys:
            AGENT_FLAGS[key] = False
    _reset()
    yield
    _reset()


def _flag(on: bool):
    AGENT_FLAGS["actor_semantics_shadow"] = on
    assert _loop.AGENT_FLAGS is AGENT_FLAGS, "两处 flag 名字必须指向同一个 dict（否则热切不生效）"


class _NoQueryDb:
    """「一查就报错」的假会话：用来证明影子判定自身不查库。"""

    async def get(self, *_a, **_kw):
        raise AssertionError("影子纯判定不得查库")


class _MsgDb:
    """返回一条带 sender_type 的来源消息（现状第 4 档的查库结果）。"""

    def __init__(self, sender_type):
        self._sender_type = sender_type

    async def get(self, *_a, **_kw):
        return types.SimpleNamespace(sender_type=self._sender_type)


# ─────────────────── ① 判据与现状准入归属逐值对照 ───────────────────

# (source, speaker_type, source_message_sender)；一律 source_id=None ⇒ 不触发现状的查库档
CORPUS = [
    ("chat", None, None),
    ("chat", None, "ai"),
    ("chat", "user", "ai"),
    ("chat", "bot", None),
    ("chat", "CHAR", None),
    ("chat", "", None),
    ("diary", None, None),
    ("life", None, None),
    ("bio", None, None),
    ("diary", "ai", None),
    ("moment", None, None),
    ("pet", None, None),
    ("mcp_weather", None, None),
    ("tool_x", None, None),
    ("search", None, None),
    ("external", "external", None),
    ("system", "system", None),
    ("summary", None, "tool"),
]


@pytest.mark.parametrize("tag_on", (False, True))
@pytest.mark.parametrize("source,speaker,src_sender", CORPUS)
def test_判定与现状准入归属逐值相同(source, speaker, src_sender, tag_on):
    AGENT_FLAGS["perception_source_tag"] = tag_on
    j = _shadow.judge_actor(source=source, speaker_type=speaker,
                            source_message_sender=src_sender, source_id=None)
    real = asyncio.run(_write._resolve_admission_sender(source, None, speaker, src_sender, _NoQueryDb()))
    assert j.actor == real, f"{source!r}/{speaker!r}/{src_sender!r} 判定漂移（tag={tag_on}）"


def test_感知条关闸时现状回落user_影子如实判perception():
    """丢失点 2 的证据（本批只留痕、不改判）：打标闸关时现状把感知条算成 user，统一语义判 perception。"""
    AGENT_FLAGS["perception_source_tag"] = False
    real = asyncio.run(_write._resolve_admission_sender("perception", None, None, None, _NoQueryDb()))
    j = _shadow.judge_actor(source="perception")
    assert real == "user" and j.actor == "perception" and j.basis == _shadow.BASIS_PERCEPTION


def test_现状第4档要查库_影子如实返回未知而非臆造user():
    """``source=chat`` 且有来源消息时现状会 ``db.get(ChatMessage)``；纯判定不查库 ⇒ actor=None。"""
    j = _shadow.judge_actor(source="chat", source_id=5)
    assert j.actor is None and j.basis == _shadow.BASIS_NEED_SOURCE_MESSAGE
    # 对照：现状拿到来源消息的 sender_type 后判 character（影子拿不到，就不猜）
    real = asyncio.run(_write._resolve_admission_sender("chat", 5, None, None, _MsgDb("ai")))
    assert real == "character"
    # 而「拿不准」不能与「用户」混同——differs_from 对未知一律 False，不把它计成判错
    assert j.differs_from("user") is False


def test_判定档位与依据一一对应():
    cases = {
        _shadow.BASIS_PERCEPTION: dict(source="perception"),
        _shadow.BASIS_SPEAKER_TYPE: dict(speaker_type="ai"),
        _shadow.BASIS_SOURCE_MESSAGE: dict(source_message_sender="mcp"),
        _shadow.BASIS_NEED_SOURCE_MESSAGE: dict(source="chat", source_id=1),
        _shadow.BASIS_SELF_NARRATIVE: dict(source="diary"),
        _shadow.BASIS_TOOL_SOURCE: dict(source="tool_call"),
        _shadow.BASIS_DEFAULT_USER: dict(source="summary"),
    }
    for basis, kw in cases.items():
        assert _shadow.judge_actor(**kw).basis == basis, basis
    assert set(cases) == set(_shadow.BASIS_VALUES), "依据标签与判据档位不同步"


def test_优先级链照现状_显式speaker压过来源消息():
    got = _shadow.judge_actor(source="chat", speaker_type="user", source_message_sender="ai")
    assert got.actor == "user" and got.basis == _shadow.BASIS_SPEAKER_TYPE
    # 感知档排在显式 speaker 之前（断点 #5 口径：屏幕内容既不是用户亲口、也不是角色自述）
    assert _shadow.judge_actor(source=" Perception ", speaker_type="user").actor == "perception"


def test_脏输入不抛且入参不被mutate():
    src, spk = "diary", "ai"
    for bad in (None, 123, 3.14, ["user"], object(), True):
        j = _shadow.judge_actor(source=bad, speaker_type=bad, source_message_sender=bad, source_id=bad)
        assert isinstance(j, _shadow.ActorJudgment) and j.basis in _shadow.BASIS_VALUES, bad
    _shadow.judge_actor(source=src, speaker_type=spk)
    assert (src, spk) == ("diary", "ai")


def test_判定模块零DB零写入依赖():
    """静态自证：影子模块不得 import 数据库/模型/回执/可观测（那些会写库）。"""
    tree = ast.parse(inspect.getsource(_shadow))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
    banned = ("app.db", "app.models", "app.memory.receipt", "app.memory.observability", "app.memory.service")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), f"影子模块越界依赖：{m}"
    assert "app.actors" in mods, "判定必须走常量单一来源"


# ─────────────────── ② flag 登记与目录双向锁 ───────────────────

def test_flag_默认关且双面登记():
    assert "actor_semantics_shadow" in AGENT_FLAGS
    assert AGENT_FLAGS["actor_semantics_shadow"] is False
    meta = FLAG_CATALOG["actor_semantics_shadow"]
    assert meta["group"] == "memory" and meta["visible"] is False
    assert meta["title_zh"] and meta["desc_zh"] and meta["title_en"] and meta["desc_en"]


def test_flag_目录双向锁不破():
    """AGENT_FLAGS ↔ flag_catalog 一一对应（与 test_flag_catalog_metadata 同一把锁，本批不破）。"""
    assert set(AGENT_FLAGS) == set(FLAG_CATALOG), set(AGENT_FLAGS) ^ set(FLAG_CATALOG)


# ─────────────────── ③④ save_memory 挂点：零行为 / 只多留痕 ───────────────────

@pytest.fixture()
def env(monkeypatch, tmp_path):
    """临时库 + 打桩外部副作用（嵌入/向量查重/后台任务/晋升/事件/插件钩子），只观察落库与日志。"""
    engine = clone_engine(tmp_path / "t.db")
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=UID, username="actor_u1", nickname="归属用户"))
            for cid in CHARS:
                db.add(AICharacter(id=cid, user_id=UID, name=f"归属角色{cid}"))
            await db.commit()

    asyncio.run(_seed())

    import app.memory.service as svc

    async def _embed(_t):
        return [0.0] * 8

    async def _none(*_a, **_kw):
        return None

    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(svc, "text_embedding", _embed)
    monkeypatch.setattr(svc, "find_similar_memory", _none)
    monkeypatch.setattr(svc, "add_memory", _noop)
    monkeypatch.setattr(svc, "bm25_invalidate", lambda *_a, **_kw: None)

    import app.memory.dedup as dd
    monkeypatch.setattr(dd, "_schedule_dedup", _noop)
    import app.utils.async_tasks as at
    monkeypatch.setattr(at, "spawn_background", lambda coro, **_kw: coro.close())
    monkeypatch.setattr("app.memory.receipt.emit_memory_receipt", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.memory.core.maybe_promote_core", _noop)
    monkeypatch.setattr("app.events.publish", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.plugins.registry.run_hook", _noop)
    monkeypatch.setattr("app.memory.meaning.maybe_extract_meaning", lambda *_a, **_kw: _noop())

    yield {"factory": factory}
    asyncio.run(engine.dispose())


def _snap(memory) -> dict:
    """语义列快照（不含自增 id / 时间戳）——开关前后必须逐字段相同。"""
    return {c: getattr(memory, c) for c in (
        "user_id", "memory_type", "title", "content", "scope", "importance", "sub_type",
        "source", "source_id", "group_id", "speaker_type", "speaker_id", "epistemic_status",
        "reliability_score", "chain_id", "parent_id", "node_type", "derived_from_ids",
    )}


def _write_once(**kw):
    return asyncio.run(_write.save_memory(**kw))


def _shadow_logs(caplog):
    return [r.getMessage() for r in caplog.records if SHADOW_MARK in r.getMessage()]


def test_flag关_落库字段与现状一致且一条影子日志都不打(env, caplog):
    _flag(False)
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        m = _write_once(user_id=UID, character_id=101, memory_type="event",
                        content="今天去了美术馆", source="diary")
    assert m is not None
    assert _shadow_logs(caplog) == [], "flag 关时不得打影子日志"
    # 现状语义（本批不许改）：diary 无 speaker ⇒ 缺省记成 user（＝要观测的丢失点 1）
    assert m.speaker_type == "user" and m.speaker_id == UID
    assert m.source == "diary" and m.epistemic_status == "FACT"


def test_flag开_只多一条INFO留痕_落库字段逐字段等于关时(env, caplog):
    _flag(False)
    off = _write_once(user_id=UID, character_id=101, memory_type="event",
                      content="今天去了美术馆", source="diary")
    snap_off = _snap(off)
    _flag(True)
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        on = _write_once(user_id=UID, character_id=102, memory_type="event",
                         content="今天去了美术馆", source="diary")
        logs = _shadow_logs(caplog)
    assert _snap(on) == snap_off, "影子档改变了落库取值"
    assert len(logs) == 1, logs
    line = logs[0]
    assert "actor shadow write" in line and "unified=character" in line and "actual=user" in line
    assert "basis=self_narrative_source" in line and "diff=True" in line


def test_flag开_归属一致时照旧留痕且diff为False(env, caplog):
    _flag(True)
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        m = _write_once(user_id=UID, character_id=101, memory_type="event",
                        content="用户说自己喜欢熬夜", source="diary", speaker_type="user")
        logs = _shadow_logs(caplog)
    assert m.speaker_type == "user"
    assert len(logs) == 1 and "unified=user" in logs[0] and "diff=False" in logs[0]


def test_flag开_并发现场单独留痕_并入结果与状态不变(env, caplog):
    """第二句与第一句字符重合 ⇒ 走并入；并入是新内容唯一的消失现场（丢失点 3）。"""
    _flag(False)
    first = _write_once(user_id=UID, character_id=101, memory_type="event",
                        content="用户喜欢喝美式咖啡", source="diary")
    merged = _write_once(user_id=UID, character_id=101, memory_type="event",
                         content="用户喜欢喝美式咖啡呀", source="diary")
    assert merged.id == first.id
    snap_off = _snap(merged)

    _flag(True)
    base = _write_once(user_id=UID, character_id=103, memory_type="event",
                       content="用户喜欢喝美式咖啡", source="diary")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        again = _write_once(user_id=UID, character_id=103, memory_type="event",
                            content="用户喜欢喝美式咖啡呀", source="diary")
        logs = _shadow_logs(caplog)
    assert again.id == base.id
    assert _snap(again) == snap_off, "影子档改变了并入结果"
    merge_logs = [x for x in logs if "actor shadow merge" in x]
    assert len(merge_logs) == 1 and "target_speaker=user" in merge_logs[0], logs


def test_flag开关_行数与查重结果不受影响(env):
    async def _count(cid):
        async with env["factory"]() as db:
            return len((await db.execute(
                select(_write.Memory).where(_write.Memory.character_id == cid))).scalars().all())

    _flag(False)
    _write_once(user_id=UID, character_id=101, memory_type="event",
                content="用户明天要出差", source="diary")
    _write_once(user_id=UID, character_id=101, memory_type="event",
                content="用户明天要出差哦", source="diary")
    off_rows = asyncio.run(_count(101))

    _flag(True)
    _write_once(user_id=UID, character_id=103, memory_type="event",
                content="用户明天要出差", source="diary")
    _write_once(user_id=UID, character_id=103, memory_type="event",
                content="用户明天要出差哦", source="diary")
    assert asyncio.run(_count(103)) == off_rows == 1


def test_flag开_判定抛错时fail_open写入照旧(env, monkeypatch):
    _flag(True)

    def _boom(*_a, **_kw):
        raise RuntimeError("判据内部炸了")

    monkeypatch.setattr(_shadow, "judge_actor", _boom)
    m = _write_once(user_id=UID, character_id=101, memory_type="event",
                    content="用户养了一只柯基", source="diary")
    assert m is not None and m.speaker_type == "user" and m.source == "diary"


def test_flag开_留痕函数抛错也不影响写入(env, monkeypatch):
    _flag(True)

    def _boom(*_a, **_kw):
        raise RuntimeError("日志发不出去")

    monkeypatch.setattr(_shadow, "trace_actor_write", _boom)
    monkeypatch.setattr(_shadow, "trace_actor_merge", _boom)
    m = _write_once(user_id=UID, character_id=101, memory_type="event",
                    content="用户养了一只柯基", source="diary")
    assert m is not None and m.speaker_type == "user"


def test_flag开关面不可用时照旧写入(env, monkeypatch):
    """读不到开关（模块被污染 / dict 不可用）一律按关处理——绝不因读开关改变写入结果或外抛异常。"""
    _flag(True)
    monkeypatch.setattr("app.flags.agent_flags.AGENT_FLAGS", None)
    m = _write_once(user_id=UID, character_id=101, memory_type="event",
                    content="用户养了一只柯基", source="diary")
    assert m is not None and m.speaker_type == "user" and m.source == "diary"
