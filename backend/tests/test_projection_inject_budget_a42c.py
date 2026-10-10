# -*- coding: utf-8 -*-
"""A42 ②c 注入档（前置，2026-10-10）守卫：默认关＝逐字节不变 · 独立配额 · 纯函数不查库。

派单＝交接文档 §四-2：**只做前置**（渲染器＋分区＋落位），③「投影进 prompt 的退化量化」要计费授权，本文件不碰。

钉的五件事（对应派单的三条守卫＋我自己在做的过程中撞到的两个坑）：
① **注入闸关 ⇒ 装配产出逐字节相同**：跑真 `assemble_context()` 比 `context_messages` 本身，
   并配「投影确实填了几格」的反向钉，否则"相等"是空对空；
② **builder 与落位分开钉**：闸关 ⇒ builder 返回空列表；`_sv` 里没这个键 ⇒ append 链一条都不追加
   （钉的是"注册表没产出时不会有人替它渲染一遍"——项目已经删掉过 13 段这种内联兜底）；
③ **两把闸各自独立**：开注入不开影子 ⇒ 只渲染 ws 上现成的格，**一次额外查询都不发**
   （用计数桩，不用异常桩：整条路径 fail-open，异常桩证不了"没调用"）；
④ **配额是新区，不挤占既有段**：既有各段 (quota_tokens, order) 与基线表逐键相同，新区 200/71 独占；
⑤ **注册时机**：`import app.agent.context` 之后注册表里就得有这个键——挂在 `assembly.py` 里注册
   会因该文件是**函数内惰性 import** 而"每进程第一轮不注入"（本单实测过这个形状，故挪进 section_*.py）。

纪律：临时库（`_dbclone`），绝不连生产库；不调模型、零计费。
"""
import asyncio
import json

import pytest
from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py（含 section_projection）注册
import app.agent.context.assembly as assembly_mod
import app.agent.workspace_projection as proj_mod
from app.agent.context.section_projection import workspace_projection_section
from app.agent.context.sections import get_sections
from app.agent.workspace import CognitiveWorkspace
from app.agent.workspace_projection import (
    INJECT_FLAG, INJECT_HEADER, INJECT_QUOTA_TOKENS, project_workspace, render_projection_block,
)
from app.flags import agent_flags

CHAR_ID, USER_ID = 13, 1

# 既有分区的配额/取号基线：②c 只许**新增**一区，从这些段里挤就会改它们的裁剪结果
_BASELINE_SECTIONS = {
    "current_state_anchor": (220, 42),
    "user_now": (300, 44),
    "working_state": (300, 20),
    "location": (300, 81),
    "thought_pool": (0, 70),
    "continue_payload": (0, 60),
    "mcp_tools": (800, 30),
    "world_facts": (600, 45),
}


@pytest.fixture(scope="module")
def ctx_db(tmp_path_factory):
    """临时库 + User(1) + AICharacter(13)：与 `test_projection_output_equivalence_a42.py` 同一形状。"""
    db_file = (tmp_path_factory.mktemp("a42c") / "ctx.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=USER_ID, username="a42c_user", nickname="本人"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True,
                               self_statement="我是酱，说话短。", bio="住在花园隔壁。"))
            await db.commit()

    asyncio.run(_seed())
    old = assembly_mod.async_session_factory
    assembly_mod.async_session_factory = factory
    yield factory
    assembly_mod.async_session_factory = old
    asyncio.run(engine.dispose())


def _base_state(**over):
    state = {
        "user_message": "在吗", "character_id": CHAR_ID, "user_id": USER_ID, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {"self_statement": "我是酱，说话短。", "bio": "住在花园隔壁。"},
        "character_name": "酱", "user_name": "本人", "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None, "status_update": None,
        "lang": "zh", "perception": {"topic": "刚下班到家", "mood": "疲惫"},
        "workspace": CognitiveWorkspace(),
    }
    state.update(over)
    return state


def _assemble(state, **sv):
    """跑真装配函数。`sv` 用来模拟注册表产出（键不存在＝该段本轮没产出）。"""
    from app.agent.context_builder import _trim_limits

    section_values = {"relationship": ""}
    section_values.update(sv)
    out = asyncio.run(assembly_mod.assemble_context(
        state, _section_values=section_values, _trim=_trim_limits(True),
    ))
    return out["context_messages"]


def _fingerprint(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _flag(key, on):
    old = agent_flags.AGENT_FLAGS.get(key)
    agent_flags.AGENT_FLAGS[key] = on
    return old


def _restore(key, old):
    if old is None:
        agent_flags.AGENT_FLAGS.pop(key, None)
    else:
        agent_flags.AGENT_FLAGS[key] = old


def _run_projection(state, *, shadow: bool):
    old = _flag(proj_mod.SHADOW_FLAG, shadow)
    try:
        return asyncio.run(proj_mod.project_into_workspace(state))
    finally:
        _restore(proj_mod.SHADOW_FLAG, old)


def _five_field_workspace():
    """用**真投影入口**填五格（自己拼字段形状＝只测到渲染器对假数据的适应）。"""
    ws = CognitiveWorkspace()
    project_workspace(
        ws,
        identity={"character_name": "酱", "user_name": "本人", "bio": "住在花园隔壁。"},
        perception={"topic": "刚下班到家"},
        current_state={"entries": [
            {"key": "location", "label": "位置", "value": "在家", "source": "s"},
            {"key": "activity", "label": "在做什么", "value": "吃饭", "source": "s"},
        ], "empty": False},
        world={"as_of": "2026-10-10T05:00:00", "version": 2, "routes": {
            "user_facts": {"kind": "user_fact", "items": [{"predicate": "job", "value": "程序员"}]},
            "world_facts": {"kind": "world_fact", "items": [{"value": "今天降温"}]},
            "working_state": {"kind": "working_state", "items": [{"value": "在聊周末安排"}]},
        }},
        topics=[{"topic": "周末去哪散步", "importance": 0.8}],
    )
    return ws


# ───────────────────────── ① 默认关＝逐字节不变 ─────────────────────────

def test_影子闸开注入闸关时装配产出逐字节相同(ctx_db):
    msgs_off = _assemble(_base_state())
    state = _base_state()
    report = _run_projection(state, shadow=True)
    msgs_on = _assemble(state)
    assert report is not None, "投影没跑起来（state 里没 workspace？）⇒ 本例前提失效"
    assert _fingerprint(msgs_on) == _fingerprint(msgs_off), (
        "只开影子闸就改了 prompt ⇒ 两把闸并不独立，②c 的前置门禁不成立"
    )


def test_投影确实填了几格否则上一条是空对空(ctx_db):
    state = _base_state()
    report = _run_projection(state, shadow=True)
    filled = report.get("filled") or {}
    assert len(filled) >= 2, f"'相等'可能只是因为啥都没投：{list(filled)}"


def test_逐字节比较对prompt通道真的敏感(ctx_db):
    """没有这一条，上面的"相等"可能是在比两份没人读的东西（同义反复）。"""
    a = _fingerprint(_assemble(_base_state()))
    b = _fingerprint(_assemble(_base_state(user_message="换个说法在吗")))
    assert a != b, "换了用户消息两次装配还逐字节相同 ⇒ 本文件的比较没看到 prompt，所有'相等'作废"


# ───────────────────────── ② builder 与落位分开钉 ─────────────────────────

def test_闸关时builder首行就返回空列表(ctx_db):
    state = _base_state()
    _run_projection(state, shadow=True)          # 把 ws 填上，证明"有内容也不渲染"
    old = _flag(INJECT_FLAG, False)
    try:
        assert asyncio.run(workspace_projection_section(state, {})) == []
    finally:
        _restore(INJECT_FLAG, old)


def test_闸开时builder产出一块且渲染的是投影结果(ctx_db):
    state = _base_state()
    _run_projection(state, shadow=True)
    old = _flag(INJECT_FLAG, True)
    try:
        out = asyncio.run(workspace_projection_section(state, {}))
    finally:
        _restore(INJECT_FLAG, old)
    assert len(out) == 1, out
    assert out[0].startswith(INJECT_HEADER), out[0][:60]


def test_注册表没产出时append链不替它渲染(ctx_db):
    """钉的是"只有一处渲染点"：`_sv` 里没这个键 ⇒ 一条都不追加（项目已删过 13 段这种内联兜底）。"""
    base = _fingerprint(_assemble(_base_state()))
    with_key = _fingerprint(_assemble(_base_state(), workspace_projection=["假块内容"]))
    assert base != with_key, "append 链没消费这个键 ⇒ 落位是假的"
    msgs = _assemble(_base_state(), workspace_projection=["假块内容"])
    assert [m for m in msgs if m.get("content") == "假块内容"], msgs[-3:]
    assert msgs[-1]["role"] == "user", "红线②：用户消息必须仍在最后（注入不许越位到诉求之后）"


# ───────────────────────── ③ 两把闸独立：开注入不额外查库 ─────────────────────────

def test_开注入不开影子时一次额外查询都不发(monkeypatch):
    """注入档只渲染 ws 上已有的东西；取数永远在影子闸后面（这是它能被单独打开的前提）。

    计数桩而非异常桩：builder 整体 fail-open，抛出去的异常会被自己的 except 吞掉，证不了"没调用"。
    """
    import app.agent.topic_tracker as tt
    import app.events.world_state as wsv
    import app.memory.current_state as cs

    calls = []

    async def _cnt_cs(**kw):
        calls.append("current_state")
        return None

    async def _cnt_world(**kw):
        calls.append("world_state")
        return None

    async def _cnt_topics(cid, uid):
        calls.append("topics")
        return []

    monkeypatch.setattr(cs, "get_current_user_state", _cnt_cs, raising=False)
    monkeypatch.setattr(wsv, "world_state_snapshot", _cnt_world, raising=False)
    monkeypatch.setattr(tt, "load_active_topics_rows", _cnt_topics)

    state = _base_state()
    old = _flag(INJECT_FLAG, True)
    try:
        asyncio.run(workspace_projection_section(state, {}))
    finally:
        _restore(INJECT_FLAG, old)
    assert calls == [], f"注入档自己就去查库了：{calls}"


def test_空workspace时闸开着也不塞空壳(ctx_db):
    """没跑过投影 ⇒ 五格全空 ⇒ 返回 []，绝不能只塞一个标题进 prompt。"""
    old = _flag(INJECT_FLAG, True)
    try:
        assert asyncio.run(workspace_projection_section(_base_state(), {})) == []
    finally:
        _restore(INJECT_FLAG, old)


# ───────────────────────── ④ 配额是新区，不挤占既有段 ─────────────────────────

def test_既有分区的配额与取号没被动过():
    got = {s.key: (s.quota_tokens, s.order) for s in get_sections()}
    for key, want in _BASELINE_SECTIONS.items():
        assert got.get(key) == want, f"{key} 的配额/取号被挪了：{got.get(key)} ≠ {want}"


def test_新区独立且取号独占():
    got = [s for s in get_sections() if s.key == "workspace_projection"]
    assert len(got) == 1, f"注册了 {len(got)} 份＝import 时机或注册表被改坏"
    assert got[0].quota_tokens == INJECT_QUOTA_TOKENS == 200, got[0].quota_tokens
    assert got[0].order == 71, got[0].order
    assert [s.key for s in get_sections() if s.order == 71] == ["workspace_projection"], "71 号被两个键占了"


# ───────────────────────── ⑤ 渲染器：纯函数 · 整行取舍 · 预算内 ─────────────────────────

def test_五格齐全时体量不超新区预算():
    text = render_projection_block(_five_field_workspace())
    assert text.startswith(INJECT_HEADER), text[:60]
    assert len(text) <= INJECT_QUOTA_TOKENS * 2, (len(text), text)
    assert len(text.splitlines()) <= 1 + 5, text


def test_超长值整行不留也不截半句():
    """预算不够时**丢掉整行**：半句话进 prompt＝模型读到一条被腰斩的事实，比没有更糟。"""
    ws = _five_field_workspace()
    ws.current_state = {"entries": [{"key": "note", "label": "备注", "value": "长" * 5000}],
                        "empty": False}
    text = render_projection_block(ws, quota_tokens=60)
    assert len(text) <= 60 * 2, len(text)
    assert "备注" not in text, "超预算那一行该整行丢掉，不该留下半句"
    for ln in text.splitlines()[1:]:
        assert ln.startswith("- "), f"出现不是整行的碎片：{ln!r}"
    # 反向钉：预算给足时这一格必须出现（否则上面的"丢掉"只是因为压根没渲染出东西）
    assert "备注" in render_projection_block(ws, quota_tokens=400)


def test_渲染是纯函数源码里没有IO():
    import inspect

    src = inspect.getsource(render_projection_block)
    for banned in ("async_session_factory", "chat_completion", "await ", "select("):
        assert banned not in src, f"渲染函数出现 IO 入口 {banned}"
    assert render_projection_block(None) == ""
    assert render_projection_block(CognitiveWorkspace()) == ""


def test_极小预算时返回空串而不是只剩标题():
    assert render_projection_block(_five_field_workspace(), quota_tokens=4) == ""
