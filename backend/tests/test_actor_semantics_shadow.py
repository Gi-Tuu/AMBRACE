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


def test_flag开_不同内容各自留痕_精确重放同id并入语义不复活(env, caplog):
    """2026-10-23 Qwen 批：云端正确性改动**明确禁止**旧的相似自动并入与「候选即强化」。

    钉住三条新语义 + 一条旧不变量：

    1. **write_identity.replay_key 明文要求 source + source_id 都在**：
       同正文但不同 ``source_id`` ⇒ 不同证据，各留一行（"文字相同即同一事实"是旧并入口径）；
       同 ``source_id`` + 同正文 ⇒ write_key 相同 ⇒ 复用同一行、不新增。
    2. **不同内容各自留痕**：``find_similar_memory`` 恒 ``None`` 打桩，"咖啡"/"咖啡呀"
       是两行独立证据（旧版会误合并）。
    3. **flag 开只多留痕**：影子档不改行数、不改落库取值；不再有 ``actor shadow merge``
       日志（并入路径已废），此处按新语义断言。

    合成 source 标识（``source="diary"`` + 稳定 ``source_id``）——重放两次输入完全一致，
    代表"同一条源事件重跑"，与"另一条源事件"（不同 source_id）严格区分。
    """
    async def _rows_for(cid: int):
        async with env["factory"]() as db:
            return (await db.execute(
                select(_write.Memory).where(_write.Memory.character_id == cid)
            )).scalars().all()

    async def _count(cid: int) -> int:
        return len(await _rows_for(cid))

    SRC_BASE = 9101        # 合成：一条固定的"来源事件 id"，重放必须一致
    SRC_OTHER_CONTENT = 9101   # 同事件、不同正文（旧版会误并入）
    SRC_OTHER_EVENT = 9102     # 同正文、不同事件（新语义必须各留一行）

    # ── flag 关：同 source_id+同正文=重放；正文不同或 source_id 不同=独立证据 ──
    _flag(False)
    base_off = _write_once(user_id=UID, character_id=101, memory_type="event",
                           content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_BASE)
    other_off = _write_once(user_id=UID, character_id=101, memory_type="event",
                            content="用户喜欢喝美式咖啡呀", source="diary", source_id=SRC_OTHER_CONTENT)
    assert other_off.id != base_off.id, (
        "新语义下不同内容必须各自留痕；等价于旧的相似并入 ⇒ 该断言失败说明"
        "产品端相似合并又复活了"
    )
    snap_diff = _snap(other_off)
    other_event = _write_once(user_id=UID, character_id=101, memory_type="event",
                              content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_OTHER_EVENT)
    assert other_event.id != base_off.id, (
        "同正文不同 source_id 属不同事件，必须各留一行；否则 replay_key 又退化成看文字（旧并入口径）"
    )
    replay_off = _write_once(user_id=UID, character_id=101, memory_type="event",
                             content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_BASE)
    assert replay_off.id == base_off.id, (
        "字节级重放（同 source+source_id+15 字段）必须复用同一 Memory（write_key 唯一约束）"
    )
    assert asyncio.run(_count(101)) == 3, "重放不新增；三行=base + 不同正文 + 不同事件（同正文）"

    # ── flag 开：同一序列，落库取值与行数与关时逐值一致 ──
    _flag(True)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        base_on = _write_once(user_id=UID, character_id=103, memory_type="event",
                              content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_BASE)
        other_on = _write_once(user_id=UID, character_id=103, memory_type="event",
                               content="用户喜欢喝美式咖啡呀", source="diary", source_id=SRC_OTHER_CONTENT)
        other_event_on = _write_once(user_id=UID, character_id=103, memory_type="event",
                                     content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_OTHER_EVENT)
        logs_diff = _shadow_logs(caplog)
    assert other_on.id != base_on.id, "影子档不得引入或恢复相似并入"
    assert other_event_on.id != base_on.id, "影子档不得把不同事件视作同一行"
    assert _snap(other_on) == snap_diff, "影子档改变了不同内容的落库取值"
    # 三次不同证据的写各出一条 write 留痕；不再有 merge 留痕（旧语义已废弃）
    writes = [x for x in logs_diff if "actor shadow write" in x]
    merges = [x for x in logs_diff if "actor shadow merge" in x]
    assert len(writes) == 3, logs_diff
    assert merges == [], (
        f"新语义不再有并入路径；出现 {len(merges)} 条 merge 留痕说明产品端"
        "把相似合并又加回来了"
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="memory.actor_shadow"):
        replay_on = _write_once(user_id=UID, character_id=103, memory_type="event",
                                content="用户喜欢喝美式咖啡", source="diary", source_id=SRC_BASE)
        logs_replay = _shadow_logs(caplog)
    assert replay_on.id == base_on.id, "影子开时精确重放仍复用同一行"
    # 一次调用至多留一条 write（若产品端把留痕移到 dedup 判定之后可能为 0 条）；
    # 关键断言是 **没有 merge 留痕**（并入语义已废）。
    assert len([x for x in logs_replay if "actor shadow write" in x]) <= 1, logs_replay
    assert [x for x in logs_replay if "actor shadow merge" in x] == [], logs_replay
    assert asyncio.run(_count(103)) == asyncio.run(_count(101)) == 3


def test_flag开关_行数与查重结果不受影响(env):
    """开关切换不得改行数或查重结果；新语义：
    同 source+source_id+正文 ⇒ 重放 1 行；不同 source_id 或不同正文 ⇒ 各自独立行。"""
    async def _count(cid):
        async with env["factory"]() as db:
            return len((await db.execute(
                select(_write.Memory).where(_write.Memory.character_id == cid))).scalars().all())

    SRC_A, SRC_B = 9201, 9202

    # flag 关：base(1) + 同正文异事件(1) + 精确重放(仍 1) ⇒ 2 行
    _flag(False)
    _write_once(user_id=UID, character_id=101, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_A)
    _write_once(user_id=UID, character_id=101, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_B)  # 同正文不同事件⇒新行
    _write_once(user_id=UID, character_id=101, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_A)  # 精确重放，不加行
    off_rows = asyncio.run(_count(101))

    # flag 开：同样的三步，行数与关时逐值相同（影子只多留痕）
    _flag(True)
    _write_once(user_id=UID, character_id=103, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_A)
    _write_once(user_id=UID, character_id=103, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_B)
    _write_once(user_id=UID, character_id=103, memory_type="event",
                content="用户明天要出差", source="diary", source_id=SRC_A)
    on_rows = asyncio.run(_count(103))
    assert on_rows == off_rows == 2, (off_rows, on_rows)


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
