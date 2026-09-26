# -*- coding: utf-8 -*-
"""删角色清理缺口回归（2026-09-26 全量审查批 A：P2-1 / P2-2）。

钉住三件事：
1. **P2-1 根治**：``cascade_delete_character`` 把该角色未触发（pending/matched）的前瞻意图置
   ``cancelled``，已兑现/作废的留痕不动；
2. **P2-1 防御**：``run_prospective_due`` 在角色或私聊会话已不存在时**不认领、不调 LLM**，
   直接终态取消（防「认领 → 白烧一次 LLM → 外键失败 → 回滚重试」）；
3. **P2-2**：``delete_character`` 对仍绑定外部渠道的角色显式拦截 409，解绑后可正常删除。

夹具口径抄 tests/test_prospective_intent.py::pi_db 与 tests/test_character_cascade.py::cascade_env
（临时库一律 pytest ``tmp_path`` + tests/_dbclone.py 模板克隆；收尾 ``asyncio.run(engine.dispose())``）。
（项目未装 pytest-asyncio，统一 asyncio.run 同步执行。）
"""
import asyncio
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from _dbclone import clone_engine, make_session_factory

# 快测档口径同 test_prospective_intent.py：每例起一次临时库，打 slow 标记
pytestmark = pytest.mark.slow

USER_ID = 1
CHAR_ID = 11
SESSION_ID = 7
DANGLING_ID = 999  # 故意不存在的角色/会话 id


async def _noop(*_a, **_k):
    return None


@pytest.fixture()
def cleanup_env(tmp_path, monkeypatch):
    """临时文件库 + 种子用户；把全局会话工厂（含 prospective_intent 模块内的名字）指向它。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=USER_ID, username="cleanup_u", nickname="清理用户"))
            await db.commit()

    asyncio.run(_seed())
    import app.db.database as db_mod
    import app.scheduling.prospective_intent as pi
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    monkeypatch.setattr(pi, "async_session_factory", factory)
    # 删除链路上的全局副作用（向量库、「离开记忆」）不参与本文件断言
    monkeypatch.setattr("app.db.vector_store.delete_memory_vectors_by_character", _noop)
    monkeypatch.setattr("app.memory.save_memory", _noop)
    yield factory
    asyncio.run(engine.dispose())


async def _seed_character(factory, *, with_session: bool = True) -> None:
    """种一个角色（可选带私聊会话），用于「删除前」的正常态。"""
    from app.models.chat import ChatSession
    from app.models.character import AICharacter
    async with factory() as db:
        db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="清理角色", personality="温柔"))
        if with_session:
            db.add(ChatSession(id=SESSION_ID, user_id=USER_ID, character_id=CHAR_ID))
        await db.commit()


def _add_intent(factory, *, character_id: int, session_id: int, status: str = "pending") -> int:
    """直接落一行前瞻意图（不走 upsert_intent，避免 side 判定干扰本文件断言）。"""
    from app.models.memory import ProspectiveIntent

    async def _run():
        async with factory() as db:
            row = ProspectiveIntent(
                user_id=USER_ID, character_id=character_id, kind="promise",
                content="下周带你去吃火锅", status=status, chat_session_id=session_id,
            )
            db.add(row)
            await db.commit()
            return row.id
    return asyncio.run(_run())


def _pis_status(factory, pis_id: int) -> str | None:
    from app.models.memory import ProspectiveIntent

    async def _run():
        async with factory() as db:
            row = await db.get(ProspectiveIntent, pis_id)
            return row.status if row else None
    return asyncio.run(_run())


def _char_exists(factory, char_id: int) -> bool:
    from app.models.character import AICharacter

    async def _run():
        async with factory() as db:
            return await db.get(AICharacter, char_id) is not None
    return asyncio.run(_run())


def _cascade_delete(factory) -> None:
    from app.application.character_cascade import cascade_delete_character

    async def _run():
        async with factory() as db:
            await cascade_delete_character(db, CHAR_ID)
            await db.commit()
    asyncio.run(_run())


def _add_binding(factory) -> None:
    from app.models.channel import ChannelBinding

    async def _run():
        async with factory() as db:
            db.add(ChannelBinding(
                channel="douyin", tenant_id=USER_ID, owner_user_id=USER_ID,
                bot_account_id="bot-1", character_id=CHAR_ID,
            ))
            await db.commit()
    asyncio.run(_run())


def _drop_binding(factory) -> None:
    from app.models.channel import ChannelBinding

    async def _run():
        async with factory() as db:
            rows = (await db.execute(
                select(ChannelBinding).where(ChannelBinding.character_id == CHAR_ID)
            )).scalars().all()
            for r in rows:
                await db.delete(r)
            await db.commit()
    asyncio.run(_run())


def _delete_character(factory) -> None:
    from app.application.characters import delete_character

    async def _run():
        async with factory() as db:
            await delete_character(db, CHAR_ID, USER_ID, "zh")
            await db.commit()
    asyncio.run(_run())


def test_cascade_cancel_untouched_prospective_intents(cleanup_env):
    """P2-1 根治：删角色级联把 pending/matched 置 cancelled，discharged 留痕不动。"""
    factory = cleanup_env
    asyncio.run(_seed_character(factory))
    pending_id = _add_intent(factory, character_id=CHAR_ID, session_id=SESSION_ID)
    matched_id = _add_intent(factory, character_id=CHAR_ID, session_id=SESSION_ID, status="matched")
    discharged_id = _add_intent(factory, character_id=CHAR_ID, session_id=SESSION_ID,
                                status="discharged")
    other_id = _add_intent(factory, character_id=CHAR_ID + 1, session_id=SESSION_ID)

    _cascade_delete(factory)

    assert _pis_status(factory, pending_id) == "cancelled"
    assert _pis_status(factory, matched_id) == "cancelled"
    # 已兑现的不改写（留痕语义不变），他角色的更不受影响
    assert _pis_status(factory, discharged_id) == "discharged"
    assert _pis_status(factory, other_id) == "pending"


def test_run_prospective_due_cancels_when_owner_or_session_missing(cleanup_env, monkeypatch):
    """P2-1 防御：角色不存在、以及角色在但会话已被删，都不认领、不调 LLM，直接 cancelled。"""
    from app.scheduling.prospective_intent import run_prospective_due

    calls: list[dict] = []

    async def _fake_llm(**kw):
        calls.append(kw)
        return "不该被调用的一句话"

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)

    # ① 角色与会话都不存在（删角色后的典型形态）
    pis_missing = _add_intent(factory=cleanup_env, character_id=DANGLING_ID, session_id=DANGLING_ID)
    ok = asyncio.run(run_prospective_due({
        "pis_id": pis_missing, "character_id": DANGLING_ID, "user_id": USER_ID,
        "content": "下周带你去吃火锅", "session_id": DANGLING_ID,
    }))
    assert ok is False
    assert calls == [], "角色已不存在却仍调了 LLM（白烧）"
    assert _pis_status(cleanup_env, pis_missing) == "cancelled"

    # ② 角色在、私聊会话已被删
    asyncio.run(_seed_character(cleanup_env, with_session=False))
    pis_no_session = _add_intent(cleanup_env, character_id=CHAR_ID, session_id=DANGLING_ID)
    ok2 = asyncio.run(run_prospective_due({
        "pis_id": pis_no_session, "character_id": CHAR_ID, "user_id": USER_ID,
        "content": "下周带你去吃火锅", "session_id": DANGLING_ID,
    }))
    assert ok2 is False
    assert calls == [], "会话已不存在却仍调了 LLM（白烧）"
    assert _pis_status(cleanup_env, pis_no_session) == "cancelled"


def test_run_prospective_due_fires_when_owner_and_session_exist(cleanup_env, monkeypatch):
    """反向对照：角色与会话都在时防御闸不得误拦（仍走认领 + 发送）。"""
    from app.scheduling.prospective_intent import run_prospective_due

    asyncio.run(_seed_character(cleanup_env))
    pis_id = _add_intent(cleanup_env, character_id=CHAR_ID, session_id=SESSION_ID)

    async def _fake_llm(**kw):
        return "到点了，那家火锅安排上？"

    sent: dict = {}

    async def _fake_send(session_id, character_id, user_id, content,
                         message_type="prospective_intent", **kw):
        sent["session_id"] = session_id
        return None

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)
    monkeypatch.setattr("app.scheduling.scheduler.send_to_session", _fake_send)

    ok = asyncio.run(run_prospective_due({
        "pis_id": pis_id, "character_id": CHAR_ID, "user_id": USER_ID,
        "content": "下周带你去吃火锅", "session_id": SESSION_ID,
    }))
    assert ok is True, "角色与会话都在却被防御闸拦掉（误杀）"
    assert sent.get("session_id") == SESSION_ID
    assert _pis_status(cleanup_env, pis_id) == "discharged"


def test_channel_binding_blocks_character_delete(cleanup_env):
    """P2-2：仍绑外部渠道 ⇒ 删除被 409 拦截且角色不动；解绑后可正常删除。"""
    factory = cleanup_env
    asyncio.run(_seed_character(factory))
    _add_binding(factory)

    with pytest.raises(HTTPException) as ei:
        _delete_character(factory)
    assert ei.value.status_code == 409
    assert "解绑" in ei.value.detail, f"409 文案未走新 i18n key：{ei.value.detail}"
    assert _char_exists(factory, CHAR_ID) is True, "被拦截却已经把角色删掉"

    _drop_binding(factory)
    _delete_character(factory)
    assert _char_exists(factory, CHAR_ID) is False
