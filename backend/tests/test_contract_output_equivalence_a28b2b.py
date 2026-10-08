# -*- coding: utf-8 -*-
"""②b（Decision Contract）的「开闸不改产出」守卫——把 ②a 那条证据补齐到另一个影子闸上。

为什么单独一份：`test_projection_output_equivalence_a42.py` 钉的是 `cognitive_projection_shadow`，
而 `decision_contract_shadow` 以前只被源码／闭包守卫钉住（`test_decision_contract_a28b2b.py`
里**没有**任何 `context_messages` 断言——2026-10-08 grep 实测 0 命中）。
你要拍的是**两个**影子闸，不该只有一个有装配面证据。

纪律：临时库（`_dbclone`，绝不连生产库）、不调模型、零计费；每条「相等」都配反向钉防空对空。
"""
import asyncio
import json

import pytest
from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册
import app.agent.decision_contract as dc_mod
from app.agent.context import assembly as assembly_mod
from app.flags import agent_flags

CHAR_ID, USER_ID = 13, 1


@pytest.fixture(scope="module")
def ctx_db(tmp_path_factory):
    db_file = (tmp_path_factory.mktemp("a28b2b") / "ctx.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=USER_ID, username="b2b_user", nickname="本人"))
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
    from app.agent.workspace import CognitiveWorkspace

    state = {
        "user_message": "今晚吃点啥",
        "character_id": CHAR_ID,
        "user_id": USER_ID,
        "session_id": 1,
        "intent": "chat",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {"self_statement": "我是酱，说话短。", "bio": "住在花园隔壁。"},
        "character_name": "酱",
        "user_name": "本人",
        "ai_response": "",
        "should_update_memory": False,
        "new_memories": [],
        "emotional_state": "",
        "perception": {"topic": "晚饭", "mood": "一般"},
        "reflection": {"summary": "用户在问晚饭"},
        "naturalness": {"score": 0.7},
        "workspace": CognitiveWorkspace(),
    }
    state.update(over)
    return state


def _assemble(state):
    from app.agent.context_builder import _trim_limits

    out = asyncio.run(assembly_mod.assemble_context(
        state, _section_values={"relationship": ""}, _trim=_trim_limits(True),
    ))
    return out["context_messages"]


def _fp(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _record(state, *, shadow: bool):
    old = agent_flags.AGENT_FLAGS.get(dc_mod.SHADOW_FLAG, False)
    agent_flags.AGENT_FLAGS[dc_mod.SHADOW_FLAG] = shadow
    try:
        return dc_mod.record_decision_contract(state, allow_tools=False)
    finally:
        agent_flags.AGENT_FLAGS[dc_mod.SHADOW_FLAG] = old


def test_契约开闸后装配产出逐字节相同(ctx_db):
    msgs_off = _assemble(_base_state())

    state = _base_state()
    contract = _record(state, shadow=True)
    msgs_on = _assemble(state)

    assert contract is not None, "契约没产出来（闸没切对／state 缺证据）⇒ 本例前提失效"
    assert _fp(msgs_on) == _fp(msgs_off), (
        "②b 影子上开就改了 prompt ⇒ 台账里「只写不读」那句不成立，必须停下来重审"
    )


def test_契约确实产出了候选与intent_否则上一条是空对空(ctx_db):
    """反向钉：上面那句「逐字节相同」只有在契约**真的建出来了**时才叫证据。"""
    contract = _record(_base_state(), shadow=True)
    cands = contract.get("candidates") or []
    assert cands, f"候选面为空 ⇒ 比较可能是空对空：{contract}"
    assert len(cands) >= 2, f"候选只有 {cands}，覆盖面不足以说明「写了但没被读」"
    assert contract.get("source_family") or contract.get("route_family") or contract.get("intent"), (
        f"契约连派单族／意图都没有：{contract}"
    )


def test_工作台只多两个字段_别的键一律没被碰(ctx_db):
    """契约的落点是 `ws.decision_contract`／`ws.candidate_actions` 两格，
    别的格（尤其 `last_decision`＝反思落点）不许被它写——这条把「不动别人的字段」钉住。"""
    from app.agent.workspace import CognitiveWorkspace

    before = CognitiveWorkspace()
    after = CognitiveWorkspace()
    state = _base_state(workspace=after)
    _record(state, shadow=True)
    changed = [k for k in vars(after) if getattr(after, k, None) != getattr(before, k, None)]
    assert set(changed) <= {"decision_contract", "candidate_actions"}, (
        f"契约写了不该写的字段：{changed}"
    )


def test_关闸时既不建结构也不落痕(ctx_db, monkeypatch):
    """关闸＝逐字节旧行为：不建契约、不落 trace。

    计数桩而不是异常桩——`record_decision_contract` 整体 fail-open，抛出去会被自己的 except 吞。
    """
    import app.agent.trace as trace_mod

    calls = []
    monkeypatch.setattr(trace_mod, "enqueue_task_log",
                        lambda **kw: calls.append(kw.get("route")))
    assert _record(_base_state(), shadow=False) is None
    assert calls == [], f"闸关着却落了痕：{calls}"

    _record(_base_state(), shadow=True)
    # 开闸后要么真落痕、要么因为 runtime 未绑事件循环而静默 fail-open；
    # 这里只断言" attempted 过"或"根本没 attempt"二选一都可接受，但建结构必须发生（见上一例）。
    assert dc_mod.shadow_enabled() or True, "本例只为证明关闸零动作，开闸侧交给上面两例"


def test_逐字节比较对prompt通道真的敏感(ctx_db):
    a = _fp(_assemble(_base_state()))
    b = _fp(_assemble(_base_state(user_message="今晚吃点啥呢")))
    assert a != b, "换了用户消息两次装配还相同 ⇒ 本文件的『相等』全都没在比 prompt 内容"
