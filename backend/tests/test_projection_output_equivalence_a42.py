# -*- coding: utf-8 -*-
"""A42 ①：认知投影「开闸前后装配产出逐字节相同」——用生产装配函数本身作证。

为什么补这条：A28-②a 的投影是**只写不读**的，而它"不读"这件事以前只被一条**源码扫描**守卫钉住
（`test_cognitive_workspace_phase1.py:334` 扫 `context/` 里有没有 `workspace` 字样）。
源码扫描是"没出现这个词"，不是"产出没变"——真要把 ②c 推上 prompt，前置门禁必须是**装配面**的证据。
本文件跑的是真 `assemble_context()`，比较的是它吐出的 `context_messages` 本身。

纪律：临时库（`_dbclone`，绝不连生产库）、不调模型、零计费；
每条"相等"断言都配一发**反向钉**证明这个比较对 prompt 通道真的敏感（否则恒相等＝没牙）。
"""
import asyncio
import json

import pytest
from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册
import app.agent.workspace_projection as proj_mod
from app.agent.context import assembly as assembly_mod
from app.flags import agent_flags

CHAR_ID, USER_ID = 13, 1


@pytest.fixture(scope="module")
def ctx_db(tmp_path_factory):
    """临时库 + User(1) + AICharacter(13)：装配函数只查这两张表就能走完 append 链。"""
    db_file = (tmp_path_factory.mktemp("a42ctx") / "ctx.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=USER_ID, username="a42_user", nickname="本人"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True,
                               self_statement="我是酱，说话短。", bio="住在花园隔壁。"))
            await db.commit()
            from app.models.memory import ConversationTopic

            async with factory() as db:
                db.add(ConversationTopic(character_id=CHAR_ID, user_id=USER_ID,
                                         topic="周末去哪散步", status="进行中", importance=0.8))
                db.add(ConversationTopic(character_id=CHAR_ID, user_id=USER_ID,
                                         topic="新项目排期", status="搁置", importance=0.9))
                await db.commit()

    asyncio.run(_seed())
    old = assembly_mod.async_session_factory
    assembly_mod.async_session_factory = factory
    yield factory
    assembly_mod.async_session_factory = old
    asyncio.run(engine.dispose())


def _base_state(**over):
    """照生产装配入口吃的形状组一份 state（键名取自 `context/assembly.py` 实际写入的那些）。"""
    from app.agent.workspace import CognitiveWorkspace

    state = {
        "user_message": "在吗",
        "character_id": CHAR_ID,
        "user_id": USER_ID,
        "session_id": 1,
        "intent": "",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {"self_statement": "我是酱，说话短。", "bio": "住在花园隔壁。"},
        "character_name": "酱",
        "user_name": "本人",
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "bio_update": None,
        "status_update": None,
        "lang": "zh",
        # 生产里 perceive 节点给的 perception 是 dict（带 topic）；列表形状 project_focus 不认
        "perception": {"topic": "刚下班到家", "mood": "疲惫"},
        "workspace": CognitiveWorkspace(),
    }
    state.update(over)
    return state


def _assemble(state):
    """跑**真**装配函数，返回 context_messages（逐字节比对的单位）。"""
    from app.agent.context_builder import _trim_limits

    out = asyncio.run(assembly_mod.assemble_context(
        state, _section_values={"relationship": ""}, _trim=_trim_limits(True),
    ))
    return out["context_messages"]


def _fingerprint(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _run_projection(state, *, shadow: bool):
    """按 runtime 的真入口跑一次投影，只切 `AGENT_FLAGS` 那一个开关。"""
    old = agent_flags.AGENT_FLAGS.get(proj_mod.SHADOW_FLAG, False)
    agent_flags.AGENT_FLAGS[proj_mod.SHADOW_FLAG] = shadow
    try:
        return asyncio.run(proj_mod.project_into_workspace(state))
    finally:
        agent_flags.AGENT_FLAGS[proj_mod.SHADOW_FLAG] = old


# ───────────────────────────────────── 1. 主断言：开闸不改产出

def test_投影开闸后装配产出逐字节相同(ctx_db):
    off_state, on_state = _base_state(), _base_state()
    msgs_off = _assemble(off_state)

    report = _run_projection(on_state, shadow=True)
    msgs_on = _assemble(on_state)

    assert report is not None, "投影没跑起来（state 里没 workspace？）⇒ 本例前提失效"
    assert _fingerprint(msgs_on) == _fingerprint(msgs_off), (
        "②a 影子上开就改了 prompt ⇒ ②c 的前置门禁不成立，必须停下来重审，"
        "不要直接把它接进对话链"
    )


def test_投影确实填进了几格_否则上一条是空对空(ctx_db):
    """反向钉：上面那句"逐字节相同"只有在投影**真的产出了内容**时才叫证据。"""
    state = _base_state()
    report = _run_projection(state, shadow=True)
    filled = report.get("filled") or {}
    assert len(filled) >= 2, "投影只填了 %s ⇒ 上面那句'相等'可能只是因为啥都没投：%s" % (list(filled), report)
    ws = state["workspace"]
    assert ws.projection is report, "投影结果必须挂在 workspace.projection 上（唯一去处）"


def test_逐字节比较对prompt通道真的敏感(ctx_db):
    """决定性自证：改一份**进 prompt 的**输入（角色名），两次装配必须不同。

    没有这一条，"相等"可能是因为在比两份空列表／比一个没人读的键——那就是同义反复。
    """
    a = _assemble(_base_state())
    # 第一版这里改的是 state["character_name"]，结果**没红**——因为人格块是从库里那行角色渲染的，
    # 不是从 state 键。这条钉的必须是真上 prompt 的通道：改 DB 人设行 + 改用户消息。
    async def _rename():
        from app.models.character import AICharacter

        async with ctx_db() as db:
            c = await db.get(AICharacter, CHAR_ID)
            c.name = "酱酱"
            c.self_statement = "完全不同的一段自述。"
            await db.commit()

    asyncio.run(_rename())
    b = _assemble(_base_state(user_message="换个说法在吗"))
    assert _fingerprint(a) != _fingerprint(b), (
        "换了自述／角色名两次装配还逐字节相同 ⇒ 本文件的比较压根没看到 prompt 内容，所有『相等』作废"
    )


def test_关闸时零额外查询_默认关就是零代价(ctx_db, monkeypatch):
    """闸关 ⇒ 不许去碰那两个只读接口与话题表（否则"默认关＝零代价"这句是空话）。

    用**计数桩**而不是抛异常的桩：`project_into_workspace` 整体 fail-open，
    抛出去会被自己的 except 吞掉，异常桩证明不了"没调用"。
    """
    calls = []
    import app.memory.current_state as cs
    import app.events.world_state as wsv
    import app.agent.topic_tracker as tt

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

    _run_projection(_base_state(), shadow=False)
    assert calls == [], f"闸关着却去查了库：{calls}"

    _run_projection(_base_state(), shadow=True)
    assert "topics" in calls, f"闸开了也没取话题 ⇒ 本例的反向自证失效：{calls}"
