# -*- coding: utf-8 -*-
"""A4 批 6 / T5「注入视图分离」M0 三项落点测试（2026-09-27）。

覆盖派单三个小项：
1. 项 1（行为变化、默认关）：注册表版 life_share 补「现状面子句」，用新开关
   ``current_view_filter`` 包住。断言口径：**断言构造出来的 SQL 字符串**（把 section 里真
   执行的 select 语句捕获下来，比对 ``whereclause`` 编译文本）——关=where 里没有
   ``memories.status``，且与「四个原始条件」手写的基线 SQL 逐字节一致；开=追加
   ``memories.status``，且关态文本是开态文本的前缀（＝只做了「追加」这一件事）。
   （选择 SQL 断言而非行为差异：行为差异需要种 active/superseded 两条 life 记忆再比注入
   文本，注入与否还受 trust/随机概率门影响，判据更绕；口径漂移本身就落在 where 上。）
2. 项 2（只留痕）：装配尾部 ``system_total_chars`` 事件被写**一次**，detail 含
   system_chars / budget_tokens / reserve_on；并验证埋点通道抛异常时装配照常完成（异常吞掉）。
3. 项 3（只留痕）：``_run_sections`` 循环结束只写**一条**聚合 ``section_budget`` 事件，
   sections 元素含 key / chars / empty，且返回值与改动前逐字一致（不改任何段的返回值）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库走 tests/_dbclone.py，绝不碰生产库。）
"""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册
from app.agent.context import legacy as legacy_mod
from app.agent.context.sections import ContextSection, TARGET_APPEND, TARGET_TEMPLATE
from app.agent.context import section_overlay as _overlay
from app.agent.loop import AGENT_FLAGS
from app.application.flag_catalog import FLAG_CATALOG, meta_for

_KEY = "current_view_filter"


# ────────────────────────────────────────────── 项 1：开关默认关 + 目录条目


def test_开关默认关且目录有条目():
    """AGENT_FLAGS 默认 False；flag_catalog 有 memory 组 / order 722 / 不可见条目，zh·en 文案非空。"""
    assert _KEY in AGENT_FLAGS, "新键必须登记进 AGENT_FLAGS（否则 runtime_flags 开了也不生效）"
    assert AGENT_FLAGS[_KEY] is False, "默认必须关（关=逐字节旧行为）"
    assert _KEY in FLAG_CATALOG, "新键必须在开关目录里登记"

    m = FLAG_CATALOG[_KEY]
    assert m["group"] == "memory" and m["order"] == 722 and m["visible"] is False
    zh, en = meta_for(_KEY, "zh"), meta_for(_KEY, "en")
    for lang, it in (("zh", zh), ("en", en)):
        assert it["title"].strip() and it["desc"].strip(), f"{lang} 文案为空"
        text = f"{it['title']} {it['desc']}".lower()
        for term in ("flag", "agent_flags", "db", "prompt"):
            assert term not in text, f"{lang} 文案含实现术语 {term!r}"


def test_取子句纯函数_关时空开时非空(monkeypatch):
    """``_current_view_clauses()``：关 → 空列表（不附加任何子句）；开 → 一条现状面子句。"""
    monkeypatch.setitem(AGENT_FLAGS, _KEY, False)
    assert _overlay._current_view_clauses() == []
    monkeypatch.setitem(AGENT_FLAGS, _KEY, True)
    assert len(_overlay._current_view_clauses()) == 1


def test_读开关异常按关处理(monkeypatch):
    """取开关失败（AGENT_FLAGS 读取抛异常）按「关」处理，不抛异常打断注入。"""
    class _Boom:
        def get(self, *a, **k):
            raise RuntimeError("flag 读取炸了")

    import app.agent.loop as _loop_mod
    monkeypatch.setattr(_loop_mod, "AGENT_FLAGS", _Boom())
    assert _overlay._current_view_clauses() == []


# ────────────────────────────────────── 项 1：life_share 真执行的 SQL 断言


class _FakeResult:
    """最小结果替身：section 里用到 scalar_one_or_none / scalars().all()。"""

    def __init__(self, one=None):
        self._one = one

    def scalar_one_or_none(self):
        return self._one

    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    """假会话：按调用次序返回 CharacterState(trust=80) / ProactiveSettings(分享开) / 空记忆集。

    第 3 次 execute 才是 AI 生活记忆查询（前两次是信任度与角色开关），语句原样收进 sink。
    """

    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, *a, **kw):
        self._sink.append(stmt)
        n = len(self._sink)
        if n == 1:
            return _FakeResult(SimpleNamespace(trust=80))
        if n == 2:
            return _FakeResult(SimpleNamespace(life_share_enabled=True))
        return _FakeResult(None)


def _life_share_whereclause(monkeypatch) -> str:
    """跑真的 ``life_share_section``，捕获它执行的那条 AI 生活查询，返回 where 的编译文本。"""
    sink: list = []
    monkeypatch.setattr("app.db.database.async_session_factory", lambda: _FakeSession(sink))
    monkeypatch.setattr("random.random", lambda: 0.0)  # 概率门必然放行
    state = {"user_id": 1, "character_id": 13}
    out = asyncio.run(_overlay.life_share_section(state, {}))
    assert out == [] and len(sink) == 3, "夹具没走到 AI 生活记忆查询（假会话次序变了）"
    return str(sink[-1].whereclause.compile())


def test_关时where不含现状面子句_开时含(monkeypatch):
    """关=where 里没有 memories.status；开=追加。断言的是构造出来的 SQL 字符串。"""
    from app.models.memory import Memory

    monkeypatch.setitem(AGENT_FLAGS, _KEY, False)
    off = _life_share_whereclause(monkeypatch)
    monkeypatch.setitem(AGENT_FLAGS, _KEY, True)
    on = _life_share_whereclause(monkeypatch)

    assert "memories.status" not in off, f"开关关却带上了现状面子句：{off}"
    assert "memories.status" in on, f"开关开却没带上现状面子句：{on}"
    # 关态 SQL 必须与改动前逐字节一致：与手写「四个原始条件」基线完全相等
    baseline = str(select(Memory).where(
        Memory.user_id == 1,
        Memory.character_id == 13,
        Memory.source == "life",
        Memory.delete_at.is_(None),
    ).whereclause.compile())
    assert off == baseline, f"关态与旧 SQL 不一致：\n关={off}\n基线={baseline}"
    # 开态只做「追加」这一件事：关态文本是开态文本的前缀
    assert on.startswith(off), f"开态不是在尾部追加：\n关={off}\n开={on}"


# ────────────────────────────────────── 项 2：装配尾部留痕（真装配函数）


@pytest.fixture(scope="module")
def asm_db(tmp_path_factory):
    """会话级临时库（模板库克隆）：User(1) + AICharacter(13) 两行足够走到装配尾部。"""
    db_file = (tmp_path_factory.mktemp("t5m0") / "t5.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(
                id=13, user_id=1, name="酱", personality="温柔",
                chat_style="口语化", relation_type="朋友", is_active=True,
            ))
            await db.commit()

    asyncio.run(_seed())
    old = legacy_mod.async_session_factory
    legacy_mod.async_session_factory = factory
    yield factory
    legacy_mod.async_session_factory = old
    asyncio.run(engine.dispose())


@pytest.fixture
def obs_capture(monkeypatch):
    """捕获 obs_event(cid, metric, detail, kind)（patch 源头，装配里是动态 import）。"""
    events: list = []
    monkeypatch.setattr(
        "app.memory.observability.obs_event",
        lambda cid, metric, detail, kind=None: events.append((cid, metric, detail)),
    )
    return events


def _assemble() -> dict:
    from app.agent.context_builder import _trim_limits

    state = {
        "user_message": "在吗",
        "character_id": 13,
        "user_id": 1,
        "session_id": 1,
        "intent": "",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {},
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "bio_update": None,
        "status_update": None,
        "lang": "zh",
    }
    return asyncio.run(legacy_mod.build_context_legacy(
        state, _section_values={"relationship": ""}, _trim=_trim_limits(True),
    ))


def test_装配尾部写一次system_total_chars且字段齐全(asm_db, obs_capture):
    """项 2：真装配一次 ⇒ system_total_chars 事件恰好一条，detail 含三个字段。"""
    from app.agent import context_builder as _cb

    out = _assemble()
    hits = [e for e in obs_capture if e[1] == "system_total_chars"]
    assert len(hits) == 1, f"留痕应每轮装配一条，实际 {len(hits)} 条"
    cid, _metric, detail = hits[0]
    assert cid == 13
    assert set(detail) == {"system_chars", "budget_tokens", "reserve_on"}, f"字段不符：{detail}"
    sys_msgs = [m for m in out["context_messages"] if m.get("role") == "system"]
    assert detail["system_chars"] == sum(len(m.get("content") or "") for m in sys_msgs) > 0
    assert detail["budget_tokens"] == _cb._effective_system_budget_tokens() > 0
    assert detail["reserve_on"] is _cb.context_budget_reserve_enabled()


def test_留痕通道抛异常不拖垮装配(asm_db, monkeypatch):
    """项 2 约束：埋点异常必须吞掉，装配照常完成（system 块 + 尾部宿主 user 消息仍在）。"""
    def _boom(*a, **k):
        raise RuntimeError("观测通道炸了")

    monkeypatch.setattr("app.memory.observability.obs_event", _boom)
    out = _assemble()
    msgs = out["context_messages"]
    assert [m for m in msgs if m.get("role") == "system"], "装配结果里 system 块没了"
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "在吗"


# ────────────────────────────────────── 项 3：每段注入体量聚合留痕


async def _tpl_text(state, ctx):
    return "hello"          # 5 字符


async def _append_list(state, ctx):
    return ["a", "bb"]      # 3 字符


async def _tpl_empty(state, ctx):
    return ""               # 空串


async def _raise(state, ctx):
    raise ValueError("section 崩了")


def test_每段体量只写一条聚合事件且不改返回值(monkeypatch):
    """项 3：_run_sections 循环结束只写**一条** section_budget，sections 元素含 key/chars/empty。"""
    events: list = []
    monkeypatch.setattr(
        "app.memory.observability.obs_event",
        lambda cid, metric, detail, kind=None: events.append((cid, metric, detail)),
    )
    fake = [
        ContextSection(key="t1", builder=_tpl_text, target=TARGET_TEMPLATE, slot="t1"),
        ContextSection(key="a1", builder=_append_list, target=TARGET_APPEND),
        ContextSection(key="e1", builder=_tpl_empty, target=TARGET_TEMPLATE, slot="e1"),
        ContextSection(key="boom", builder=_raise, target=TARGET_APPEND),
    ]
    monkeypatch.setattr(_ctx, "get_sections", lambda: list(fake))

    values = asyncio.run(_ctx._run_sections({"character_id": 13, "user_id": 1}, {}))
    # ① 返回值逐字不变（崩掉那段照旧不写入，legacy 内联兜底语义不动）
    assert values == {"t1": "hello", "a1": ["a", "bb"], "e1": ""}, values
    # ② 只有一条聚合事件（不每段一条）
    budget = [e for e in events if e[1] == "section_budget"]
    assert len(budget) == 1, f"聚合事件应恰好一条，实际 {len(budget)} 条"
    detail = budget[0][2]
    assert detail["total"] == 3 and detail["n_empty"] == 1 and detail["chars_total"] == 8, detail
    assert {i["key"] for i in detail["sections"]} == {"t1", "a1", "e1"}, detail["sections"]
    for item in detail["sections"]:
        assert set(item) == {"key", "chars", "empty"}, f"元素字段不符：{item}"
        assert isinstance(item["chars"], int) and isinstance(item["empty"], bool)
    # ③ 排序：体量大的排前面（读端直接看 top 消耗）
    assert detail["sections"][0]["key"] == "t1", detail["sections"]


def test_聚合留痕通道抛异常不影响sections结果(monkeypatch):
    """项 3 约束：留痕异常吞掉，_run_sections 仍返回完整 values。"""
    def _boom(*a, **k):
        raise RuntimeError("观测通道炸了")

    monkeypatch.setattr("app.memory.observability.obs_event", _boom)
    fake = [ContextSection(key="t1", builder=_tpl_text, target=TARGET_TEMPLATE, slot="t1")]
    monkeypatch.setattr(_ctx, "get_sections", lambda: list(fake))
    assert asyncio.run(_ctx._run_sections({"character_id": 13, "user_id": 1}, {})) == {"t1": "hello"}
