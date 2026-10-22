# -*- coding: utf-8 -*-
"""C7 时间锚点接缝测试：真实落库/真实入口边界 → 真实上下文装配。

覆盖：
- 诊断原复现：5 天 1 小时旧用户消息 → 真实 _persist_user_message 落当前 → 真实
  _compute_current_time_str 仍显示 5 天前，不再是“刚刚”；
- 正常/轻量共用同一锚点判定；
- HTTP / chunked / SSE 三个真实入口把边界传到装配（不是手工拼理想 state）；
- SSE 回退 chunked 时把已落库的当前消息 id 仅作为时间边界传下去；
- 无 source 且无边界（图片/批量/语音等预落库路径）安全省略，不伪造“刚刚”；
- 轻量社交入口的并发后来消息排除；首次无历史省略；
- continue（无新用户消息）用入口捕获的最后用户发言快照；
- 模型 tripwire 零调用（真实装配需要的日摘要已预置，不触发生成）。
"""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
import app.agent.context as _ctx  # noqa: F401 —— 触发全部 section 注册（build_context 依赖）
from app.models import Base, ChatMessage
from app.utils.timeutil import now_naive_utc


@pytest.fixture()
def c7_env(tmp_path, monkeypatch):
    db_path = (tmp_path / "c7_context.db").as_posix()
    assert tmp_path.as_posix() in db_path, "C7 接缝 DB 必须落在 pytest tmp_path 沙箱"
    engine = create_async_engine("sqlite+aiosqlite:///" + db_path, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    from app.application import chat_service as cs
    from app.application.chat import streaming as st
    from app.db import database as db_mod
    from app.agent import context_builder as cb
    from app.agent.context import assembly as ctx_assembly
    from app.memory import core as memory_core

    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(cs, "async_session_factory", factory)
    monkeypatch.setattr(st, "async_session_factory", factory)
    # context_builder / assembly / memory.core 在模块顶层绑定了 factory（日摘要补生成、
    # 角色存在检查、核心记忆检索等路径）；不补会把合成库的查询落到全局沙箱。
    monkeypatch.setattr(cb, "async_session_factory", factory)
    monkeypatch.setattr(ctx_assembly, "async_session_factory", factory)
    monkeypatch.setattr(memory_core, "async_session_factory", factory)
    yield {"engine": engine, "factory": factory}
    asyncio.run(engine.dispose())


async def _add_message(factory, session_id, sender_type, content, created_at):
    from app.models import ChatMessage

    async with factory() as db:
        msg = ChatMessage(
            session_id=session_id, sender_type=sender_type,
            content=content, created_at=created_at,
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg.id


async def _seed_case(factory, *, gap=timedelta(days=5, hours=1), ai_greeting_only=False):
    """用户 1 × 角色 1 × 会话 1；旧用户消息 + 对应日摘要（避免装配补生成日摘要）。"""
    from app.models import AICharacter, ChatSession, DailySummary, User

    now = now_naive_utc()
    old = now - gap
    async with factory() as db:
        db.add(User(id=1, username="u1", nickname="U1"))
        db.add(AICharacter(id=1, user_id=1, name="C1"))
        db.add(ChatSession(
            id=1, user_id=1, character_id=1, created_at=now, updated_at=now,
        ))
        await db.commit()
    if ai_greeting_only:
        await _add_message(factory, 1, "ai", "AI_GREETING", old)
        return None, old
    old_id = await _add_message(factory, 1, "user", "OLD_USER", old)
    async with factory() as db:
        db.add(DailySummary(
            session_id=1, summary_date=old.strftime("%Y-%m-%d"),
            summary_text="SYNTH_OLD_SUMMARY",
        ))
        await db.commit()
    return old_id, old


async def _seed_ai_greeting_only(factory):
    return await _seed_case(factory, ai_greeting_only=True)


def _patch_no_llm(monkeypatch):
    calls = []

    async def _trip(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("C7 forbids LLM calls")

    from app.agent import context_builder, nodes

    monkeypatch.setattr(nodes, "chat_completion", _trip)
    monkeypatch.setattr(nodes, "chat_completion_stream", _trip)
    monkeypatch.setattr(context_builder, "chat_completion", _trip)
    return calls


def _patch_service(monkeypatch, captured):
    """真实 _run_agent_core 的边界捕获，桩掉其前后的非时间副作用。"""
    from app.application import character_state_service as css
    from app.application import chat_service as cs

    async def _noop(*args, **kwargs):
        return None

    async def _false(*args, **kwargs):
        return False

    async def _zero(*args, **kwargs):
        return 0

    async def _none(*args, **kwargs):
        return None

    async def _recall(**kw):
        return kw["final_state"], 1

    async def _search(**kw):
        return kw["final_state"], kw["full_text"], [], False, 1

    async def _mcp(**kw):
        return kw["final_state"], kw["full_text"]

    async def _notes(**kw):
        return kw["full_text"], None, None

    async def _finalize(**kw):
        return kw["full_text"], kw["final_state"]

    class _FakeAgent:
        async def ainvoke(self, state):
            captured["state"] = dict(state)
            final = dict(state)
            final.update({
                "ai_response": "SYNTH_REPLY",
                "streamed": False,
                "raw_response": "SYNTH_REPLY",
                "stream_blocks": [],
                "stream_saved": [],
                "should_update_memory": False,
            })
            return final

    monkeypatch.setattr(cs, "_cold_war_block", _false)
    monkeypatch.setattr(cs, "_run_user_turn_signals", _noop)
    monkeypatch.setattr(cs, "_load_reasoning_level", _zero)
    monkeypatch.setattr(css, "get_character_states", _none)
    monkeypatch.setattr(cs, "agent", _FakeAgent())
    monkeypatch.setattr(cs, "_run_recall_second_hop", _recall)
    monkeypatch.setattr(cs, "_run_search_stage", _search)
    monkeypatch.setattr(cs, "_run_mcp_stage", _mcp)
    monkeypatch.setattr(cs, "_emit_chat_trace", _noop)
    monkeypatch.setattr(cs, "_schedule_chat_task", _noop)
    monkeypatch.setattr(cs, "_run_note_and_timer_stage", _notes)
    monkeypatch.setattr(cs, "_finalize_visible_text", _finalize)
    monkeypatch.setattr(cs, "append_domain_event", _noop)

    def _spawn(_coro):
        close = getattr(_coro, "close", None)
        if close is not None:
            close()

    monkeypatch.setattr(cs, "spawn_background", _spawn)
    monkeypatch.setattr(cs, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    from app.application.chat import streaming as st
    monkeypatch.setattr(st, "_push_user_notify", _noop)
    from app.events import store as event_store
    monkeypatch.setattr(event_store, "append_domain_event", _noop)
    return captured


async def _assemble_system_text(state):
    from app.agent.context_builder import build_context

    out = await build_context(state)
    return "\n".join(
        str(m.get("content") or "")
        for m in out.get("context_messages", [])
        if m.get("role") == "system"
    )


def test_real_helper_repro_after_persist_not_just_now(c7_env, monkeypatch):
    """诊断原复现：真实落库刷新 updated_at 后，真实 helper 仍显示 5 天前。"""
    from app.agent.context.chat_time import STATUS_OK, capture_request_anchor, resolve_chat_time_anchor
    from app.agent.context.section_world import _compute_current_time_str
    from app.agent.runtime import _build_initial_state
    from app.application import chat_service as cs
    from app.models import ChatMessage, ChatSession
    from sqlalchemy import select

    model_calls = _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})

    async def run():
        factory = c7_env["factory"]
        old_id, _old = await _seed_case(factory)
        boundary = await capture_request_anchor(1, 1)
        before_state = _build_initial_state(
            character_id=1, user_id=1, session_id=1,
            user_message="BEFORE", lang="zh", reasoning_level=0,
            chat_time_reference_at=boundary["chat_time_reference_at"],
            chat_time_anchor_snapshot=boundary["chat_time_anchor_snapshot"],
        )
        before = await _compute_current_time_str(before_state, {})
        assert "距上次互动 5 天前" in before

        msg_id, info = await cs._persist_user_message(
            1, 1, 1, "CURRENT_AFTER_FIVE_DAYS", shared_memory=False,
        )
        assert msg_id is not None and info is not None
        async with factory() as db:
            sess = (await db.execute(select(ChatSession).where(ChatSession.id == 1))).scalar_one()
            assert sess.updated_at is not None
            src_owner = (await db.execute(
                select(ChatSession.user_id, ChatSession.character_id)
                .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
                .where(ChatMessage.id == msg_id)
            )).one()
        assert tuple(src_owner) == (1, 1)
        after_state = _build_initial_state(
            character_id=1, user_id=1, session_id=1,
            user_message="AFTER", lang="zh", reasoning_level=0,
            source_id=msg_id,
        )
        assert after_state["chat_time_anchor_snapshot"] is None
        after = await _compute_current_time_str(after_state, {})
        anchor = await resolve_chat_time_anchor(after_state)
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id
        assert "距上次互动 5 天前" in after
        assert "距上次互动 刚刚" not in after
        assert not model_calls
        assert not captured

    asyncio.run(run())


def test_normal_and_light_share_the_same_anchor(c7_env, monkeypatch):
    """同一份 3 天 2 小时历史：正常时间串与轻量时间行取到同一间隔。"""
    from app.agent.context.chat_time import capture_request_anchor
    from app.agent.context.section_world import _compute_current_time_str
    from app.agent.runtime import _build_initial_state, build_light_social_context

    _patch_no_llm(monkeypatch)

    async def run():
        factory = c7_env["factory"]
        await _seed_case(factory, gap=timedelta(days=3, hours=2))
        boundary = await capture_request_anchor(1, 1)
        state = _build_initial_state(
            character_id=1, user_id=1, session_id=1,
            user_message="LIGHT", lang="zh", reasoning_level=0,
            chat_time_reference_at=boundary["chat_time_reference_at"],
            chat_time_anchor_snapshot=boundary["chat_time_anchor_snapshot"],
        )
        normal = await _compute_current_time_str(state, {})
        light_state = await build_light_social_context(dict(state))
        light_text = "\n".join(
            str(m.get("content") or "") for m in light_state["context_messages"]
            if m.get("role") == "system"
        )
        assert "距上次互动 3 天前" in normal
        assert "距上次互动 3 天前" in light_text
        assert "距上次互动 刚刚" not in light_text

    asyncio.run(run())


def test_http_entry_boundary_reaches_assembly(c7_env, monkeypatch):
    """真实 HTTP 入口：落库 → 真实 core 构造 state → 真实 build_context。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.application import chat_service as cs

    model_calls = _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})

    async def run():
        factory = c7_env["factory"]
        old_id, _ = await _seed_case(factory)
        res = await cs.send_and_receive(
            1, 1, 1, "CURRENT_HTTP", lang="zh", reply_delay=False,
        )
        assert res.get("ai_message") is not None
        state = captured["state"]
        assert state["source_id"] is not None and state["source_id"] != old_id
        assert state["chat_time_reference_at"] is None  # source 路径以实际落库时间为准
        assert state["chat_time_anchor_snapshot"] is None
        anchor = await resolve_chat_time_anchor(state)
        async with c7_env["factory"]() as db:
            source = await db.get(ChatMessage, state["source_id"])
            assert anchor.reference_at == source.created_at
        assert anchor.status == STATUS_OK
        assert anchor.previous_message_id == old_id
        text = await _assemble_system_text(state)
        assert "距上次互动 5 天前" in text
        assert "距上次互动 刚刚" not in text
        assert not model_calls

    asyncio.run(run())


def test_chunked_entry_boundary_reaches_assembly(c7_env, monkeypatch):
    """真实 chunked 入口（save_user_message=True）边界同样到达装配。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.application import chat_service as cs

    model_calls = _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})

    async def run():
        factory = c7_env["factory"]
        old_id, _ = await _seed_case(factory)
        res = await cs.send_and_receive_chunked(
            1, 1, 1, "CURRENT_CHUNKED", lang="zh", reply_delay=False,
        )
        assert res.get("chunks")
        state = captured["state"]
        assert state["source_id"] is not None
        assert state["chat_time_reference_at"] is None  # source 路径以实际落库时间为准
        assert state["chat_time_anchor_snapshot"] is None
        anchor = await resolve_chat_time_anchor(state)
        async with c7_env["factory"]() as db:
            source = await db.get(ChatMessage, state["source_id"])
            assert anchor.reference_at == source.created_at
        assert anchor.status == STATUS_OK and anchor.previous_message_id == old_id
        text = await _assemble_system_text(state)
        assert "距上次互动 5 天前" in text
        assert not model_calls

    asyncio.run(run())


def test_chunked_no_source_without_boundary_omits_gap(c7_env, monkeypatch):
    """图片/批量/语音等预落库路径：没有 source 也没有边界 → 安全省略，不伪造刚刚。"""
    from app.application import chat_service as cs

    _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})

    async def run():
        factory = c7_env["factory"]
        await _seed_case(factory)
        await cs._persist_user_message(1, 1, 1, "PRE_PERSISTED_CURRENT", shared_memory=False)
        res = await cs.send_and_receive_chunked(
            1, 1, 1, "CURRENT_IMAGE_LIKE", lang="zh", reply_delay=False,
            save_user_message=False,
        )
        assert res.get("chunks")
        state = captured["state"]
        assert state["source_id"] is None
        assert state["chat_time_source_id"] is None
        assert state["chat_time_anchor_snapshot"] is None
        text = await _assemble_system_text(state)
        assert "｜距上次互动" not in text

    asyncio.run(run())


def test_chunked_no_source_with_time_source_uses_previous_user(c7_env, monkeypatch):
    """SSE 回退等预落库路径显式传时间 source：只用它排除当前轮，不改 source_id。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.application import chat_service as cs

    _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})

    async def run():
        factory = c7_env["factory"]
        old_id, _ = await _seed_case(factory)
        pre_id, _ = await cs._persist_user_message(
            1, 1, 1, "PRE_PERSISTED_CURRENT", shared_memory=False,
        )
        res = await cs.send_and_receive_chunked(
            1, 1, 1, "CURRENT_FALLBACK", lang="zh", reply_delay=False,
            save_user_message=False, chat_time_source_id=pre_id,
        )
        assert res.get("chunks")
        state = captured["state"]
        assert state["source_id"] is None
        assert state["chat_time_source_id"] == pre_id
        anchor = await resolve_chat_time_anchor(state)
        assert anchor.status == STATUS_OK and anchor.previous_message_id == old_id
        text = await _assemble_system_text(state)
        assert "距上次互动 5 天前" in text

    asyncio.run(run())


def test_sse_entry_boundary_reaches_assembly(c7_env, monkeypatch):
    """真实 SSE 入口：真实落用户消息 → 把 id 传给 core → 构造 state → 装配。"""
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.application.chat import streaming as st

    model_calls = _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})
    events = []

    async def sink(event, payload):
        events.append((event, payload))

    async def run():
        await _seed_case(c7_env["factory"])
        await st.send_and_receive_stream(
            1, 1, 1, "CURRENT_SSE", lang="zh", sink=sink,
        )
        user_events = [p for e, p in events if e == "user_message"]
        assert user_events, events
        persisted_id = user_events[0]["id"]
        state = captured["state"]
        assert state["source_id"] == persisted_id
        assert state["chat_time_reference_at"] is None  # source 路径以实际落库时间为准
        assert state["chat_time_anchor_snapshot"] is None
        anchor = await resolve_chat_time_anchor(state)
        async with c7_env["factory"]() as db:
            source = await db.get(ChatMessage, state["source_id"])
            assert anchor.reference_at == source.created_at
        assert anchor.status == STATUS_OK
        text = await _assemble_system_text(state)
        assert "距上次互动 5 天前" in text
        assert "距上次互动 刚刚" not in text
        assert not model_calls

    asyncio.run(run())


def test_sse_no_source_without_boundary_omits_gap(c7_env, monkeypatch):
    """语音等预落库 SSE 路径：save_user_message=False 且无时间 source → 安全省略。"""
    from app.application import chat_service as cs
    from app.application.chat import streaming as st

    _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})
    events = []

    async def sink(event, payload):
        events.append((event, payload))

    async def run():
        factory = c7_env["factory"]
        await _seed_case(factory)
        await cs._persist_user_message(1, 1, 1, "PRE_PERSISTED_VOICE", shared_memory=False)
        await st.send_and_receive_stream(
            1, 1, 1, "CURRENT_VOICE", lang="zh", sink=sink,
            save_user_message=False,
        )
        assert not [p for e, p in events if e == "user_message"]
        state = captured["state"]
        assert state["source_id"] is None
        assert state["chat_time_source_id"] is None
        assert state["chat_time_anchor_snapshot"] is None
        text = await _assemble_system_text(state)
        assert "｜距上次互动" not in text

    asyncio.run(run())


def test_sse_fallback_passes_time_source_to_chunked(c7_env, monkeypatch):
    """SSE 回退 chunked 时：user_msg_id 仍为 None（记忆语义不变），时间 source 传已落库 id。"""
    from app.application import chat_service as cs
    from app.application.chat import streaming as st

    _patch_no_llm(monkeypatch)
    _patch_service(monkeypatch, {})
    recorded = {}

    async def boom(*args, **kwargs):
        raise RuntimeError("synthetic stream failure")

    async def fake_chunked(*args, **kwargs):
        recorded.update(kwargs)
        return {
            "chunks": [{
                "id": 77, "session_id": 1, "sender_type": "ai",
                "content": "FALLBACK", "created_at": "2026-10-22T00:00:00",
                "extra_meta": None,
            }],
            "memories_updated": False,
        }

    monkeypatch.setattr(cs, "_run_agent_core", boom)
    monkeypatch.setattr(cs, "send_and_receive_chunked", fake_chunked)
    events = []

    async def sink(event, payload):
        events.append((event, payload))

    async def run():
        await _seed_case(c7_env["factory"])
        await st.send_and_receive_stream(1, 1, 1, "CURRENT_SSE_FALLBACK", lang="zh", sink=sink)
        user_events = [p for e, p in events if e == "user_message"]
        assert user_events, events
        persisted_id = user_events[0]["id"]
        assert recorded.get("save_user_message") is False
        assert recorded.get("chat_time_source_id") == persisted_id

    asyncio.run(run())


def test_light_entry_uses_boundary_and_excludes_late_message(c7_env, monkeypatch):
    """轻量社交入口：入口快照后插入的并发消息不得成为锚点；模型只调用回复那一次。"""
    from app.agent import nodes
    from app.agent.context import chat_time
    from app.agent.runtime import run_social_reply
    from app.memory import core as memory_core
    from app.memory import current_state as memory_current

    model_calls = _patch_no_llm(monkeypatch)
    calls = []

    async def fake_generate(state):
        calls.append(dict(state))
        state["ai_response"] = "SYNTH_LIGHT"
        return state

    async def _empty(*args, **kwargs):
        return []

    async def _empty_anchor(*args, **kwargs):
        return ""

    monkeypatch.setattr(nodes, "generate_response", fake_generate)
    monkeypatch.setattr(memory_core, "get_core_memories", _empty)
    monkeypatch.setattr(memory_core, "get_relationship_anchors", _empty)
    monkeypatch.setattr(memory_current, "current_user_state_anchor", _empty_anchor)
    from app.agent import llm_client
    async def _zero(*args, **kwargs):
        return 0
    monkeypatch.setattr(llm_client, "load_character_reasoning_level", _zero)

    real_capture = chat_time.capture_request_anchor
    interleave = {}

    async def capture_then_interleave(user_id, character_id):
        boundary = await real_capture(user_id, character_id)
        factory = c7_env["factory"]
        async with factory() as db:
            await db.execute(delete(ChatMessage).where(ChatMessage.id == interleave["high_id"]))
            await db.commit()
        interleave["late_id"] = await _add_message(
            factory, 1, "user", "LATE_LIGHT", now_naive_utc(),
        )
        return boundary

    monkeypatch.setattr(chat_time, "capture_request_anchor", capture_then_interleave)

    async def run():
        factory = c7_env["factory"]
        old_id, old = await _seed_case(factory, gap=timedelta(days=3, hours=2))
        interleave["high_id"] = await _add_message(
            factory, 1, "ai", "HIGH_AI", old + timedelta(seconds=1),
        )
        res = await run_social_reply(
            character_id=1, user_id=1, session_id=1,
            user_message="在吗", lang="zh", light_context=True,
        )
        assert res.get("status") == "ok", res
        assert interleave["late_id"] == interleave["high_id"], "测试前置：删除最高行后晚到消息应复用编号"
        assert len(calls) == 1
        state = calls[0]
        snapshot = state["chat_time_anchor_snapshot"]
        assert snapshot["status"] == chat_time.STATUS_OK
        assert snapshot["message_id"] == old_id
        assert snapshot["created_at"] == old
        text = "\n".join(
            str(m.get("content") or "") for m in state["context_messages"]
            if m.get("role") == "system"
        )
        assert "距上次互动 3 天前" in text
        assert "距上次互动 刚刚" not in text
        assert not model_calls

    asyncio.run(run())


def test_light_first_visit_omits_gap(c7_env, monkeypatch):
    """首次无历史（只有 AI 问候）→ 轻量时间行不注入“距上次互动”。"""
    from app.agent import nodes
    from app.agent.runtime import run_social_reply
    from app.memory import core as memory_core
    from app.memory import current_state as memory_current

    _patch_no_llm(monkeypatch)
    calls = []

    async def fake_generate(state):
        calls.append(state.get("context_messages"))
        state["ai_response"] = "SYNTH_FIRST"
        return state

    async def _empty(*args, **kwargs):
        return []

    async def _empty_anchor(*args, **kwargs):
        return ""

    monkeypatch.setattr(nodes, "generate_response", fake_generate)
    monkeypatch.setattr(memory_core, "get_core_memories", _empty)
    monkeypatch.setattr(memory_core, "get_relationship_anchors", _empty)
    monkeypatch.setattr(memory_current, "current_user_state_anchor", _empty_anchor)
    from app.agent import llm_client
    async def _zero(*args, **kwargs):
        return 0
    monkeypatch.setattr(llm_client, "load_character_reasoning_level", _zero)

    async def run():
        await _seed_ai_greeting_only(c7_env["factory"])
        res = await run_social_reply(
            character_id=1, user_id=1, session_id=1,
            user_message="第一次来", lang="zh", light_context=True,
        )
        assert res.get("status") == "ok", res
        text = "\n".join(
            str(m.get("content") or "") for m in calls[0] if m.get("role") == "system"
        )
        assert "｜距上次互动" not in text

    asyncio.run(run())


def test_continue_entry_snapshot_survives_deleted_max_row(c7_env, monkeypatch):
    """continue_chat（WS continue_to_session 入口）：捕获后删除最高行+晚到插入仍用旧快照。"""
    from app.agent.context import chat_time
    from app.agent.context.chat_time import STATUS_OK, resolve_chat_time_anchor
    from app.application import chat_service as cs

    _patch_no_llm(monkeypatch)
    captured = _patch_service(monkeypatch, {})
    interleave = {}
    real_capture = chat_time.capture_request_anchor

    async def capture_then_interleave(user_id, character_id):
        boundary = await real_capture(user_id, character_id)
        factory = c7_env["factory"]
        async with factory() as db:
            await db.execute(delete(ChatMessage).where(ChatMessage.id == interleave["high_id"]))
            await db.commit()
        interleave["late_id"] = await _add_message(
            factory, 1, "user", "LATE_CONTINUE", now_naive_utc(),
        )
        return boundary

    monkeypatch.setattr(chat_time, "capture_request_anchor", capture_then_interleave)

    async def run():
        factory = c7_env["factory"]
        old_id, old = await _seed_case(factory)
        interleave["high_id"] = await _add_message(
            factory, 1, "ai", "HIGH_AI", old + timedelta(seconds=1),
        )
        res = await cs.continue_chat(1, 1, 1, last_message_id=0, lang="zh")
        assert res.get("content")
        assert interleave["late_id"] == interleave["high_id"], "测试前置：删除最高行后晚到消息应复用编号"
        state = captured["state"]
        assert state["source_id"] is None
        snapshot = state["chat_time_anchor_snapshot"]
        assert snapshot["status"] == chat_time.STATUS_OK
        assert snapshot["message_id"] == old_id
        assert snapshot["created_at"] == old
        anchor = await resolve_chat_time_anchor(state)
        assert anchor.status == STATUS_OK and anchor.previous_message_id == old_id
        text = await _assemble_system_text(state)
        assert "距上次互动 5 天前" in text
        assert "距上次互动 刚刚" not in text

    asyncio.run(run())
