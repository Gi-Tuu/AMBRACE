# -*- coding: utf-8 -*-
"""legacy 接缝自同步回归测试（2026-08-31 真机暴露）。

事故：F3 迁移时 legacy.py 的 _sync_seams() 定义了但从未被调用——裸名
async_session_factory/AICharacter/select 等在真实运行（非打桩单测）时 NameError，
聊天流式与 chunked 双路径全灭（气泡生成完即消失）。
断言：build_context_legacy 在真实内存库上执行不再抛 NameError（业务异常可接受）。
"""
import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import context_builder
from app.db import database as db_mod


@pytest.fixture
def memory_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=NullPool)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def test_build_context_legacy_no_name_error(memory_db):
    """真实内存库 + 未打桩 context_builder：legacy 完整执行不得抛 NameError（接缝自同步生效）。"""
    state = {
        "user_message": "在吗",
        "character_id": 1,
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

    async def _run():
        return await context_builder.build_context_legacy(state)

    try:
        asyncio.run(_run())
    except NameError as e:
        pytest.fail(f"legacy 裸名未同步（_sync_seams 未生效）: {e}")
    except Exception:
        # 业务异常（如角色不存在的早退）可接受；接缝 NameError 不可接受
        pass


def test_legacy_globals_contain_seam_names():
    """调用一次后，legacy globals 里必须存在接缝名字（自同步生效的直接证据）。"""
    from app.agent.context import legacy as legacy_mod

    state = {"user_message": "x", "character_id": 999999, "user_id": 1,
             "session_id": 1, "lang": "zh", "context_messages": [],
             "retrieved_memories": [], "character_info": {}, "intent": "",
             "ai_response": "", "should_update_memory": False, "new_memories": [],
             "emotional_state": "", "bio_update": None, "status_update": None}
    try:
        asyncio.run(legacy_mod.build_context_legacy(state))
    except Exception:
        pass
    for name in ("async_session_factory", "AICharacter", "select"):
        assert hasattr(legacy_mod, name), f"legacy globals 缺 {name}（_sync_seams 未生效）"
