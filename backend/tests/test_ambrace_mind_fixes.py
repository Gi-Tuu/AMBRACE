# -*- coding: utf-8 -*-
"""2026-08-25 AMBRACE 记忆召回/任务记录/工具轨迹/主动备忘 专项回归测试。

守护四处体验修复：
- A 记忆召回：memory_search trace 的 hit_count=候选命中数 / returned=实际返回条数 语义（端到端）；
- B/C 任务记录与工具轨迹：status 经 classify 归一（blocked/partial 不再被误判为 failed），
  并保留 status_raw 供排查；
- D 主动备忘：主动消息（generate_proactive_event）允许 LLM 输出 [MEMO]，落小手机备忘录并剥离标记。

均为纯函数/临时库测试（不触碰 backend/data）；项目未装 pytest-asyncio，统一 asyncio.run 同步执行。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import characters as characters_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent import AgentTaskLog
from app.models.character import AICharacter

USER = 1
CHAR = 88001


@pytest.fixture()
def mind_db():
    """临时 SQLite 文件库（不触碰 backend/data），种子一个角色并返回 (factory, character_id)。"""
    tmp = tempfile.mkdtemp(prefix="ambrace_mind_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    async def _seed():
        async with factory() as db:
            db.add(AICharacter(id=CHAR, user_id=USER, name="测试", cognitive_loop_enabled=False))
            await db.commit()

    asyncio.run(_seed())
    yield factory, CHAR
    engine.sync_engine.dispose()


def _make_client(factory, user_id=USER) -> TestClient:
    app = FastAPI()
    app.include_router(characters_api.router)

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _seed_log(factory, **kw):
    async with factory() as db:
        db.add(AgentTaskLog(character_id=CHAR, user_id=USER, **kw))
        await db.commit()


# ---------------- A：记忆召回 trace 语义（端到端） ----------------

def test_agent_mind_memory_search_trace_语义(mind_db):
    """memory_search trace：hit_count=候选命中数（去重后），returned=实际返回条数（旧日志无 returned 回退 hit_count）。"""
    factory, char_id = mind_db
    asyncio.run(_seed_log(
        factory,
        trigger="memory_search", route="hybrid",
        steps_json='{"query":"喜欢什么","hit_count":4,"returned":3}',
        latency_ms=12, status="ok",
    ))
    r = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind")
    assert r.status_code == 200
    body = r.json()
    assert body["memory_search"]["total"] == 1
    assert body["memory_search"]["hit"] == 1
    item = body["memory_search"]["recent"][0]
    assert item["hit_count"] == 4
    assert item["returned"] == 3
    # 旧日志无 returned 字段 → 回退 hit_count
    asyncio.run(_seed_log(
        factory,
        trigger="memory_search", route="keyword",
        steps_json='{"query":"x","hit_count":2}',
        latency_ms=8, status="ok",
    ))
    r2 = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind")
    assert r2.json()["memory_search"]["recent"][0]["returned"] == 2


# ---------------- C：工具轨迹 status 口径归一 ----------------

def test_agent_mind_tool_logs_status归一(mind_db):
    """tool_logs 的 status 经 classify 归一；blocked 不再被当成 failed，且保留 status_raw。"""
    factory, char_id = mind_db
    asyncio.run(_seed_log(factory, trigger="chat", status="ok", steps_json="[]", latency_ms=5))
    asyncio.run(_seed_log(factory, trigger="scheduler", status="blocked", steps_json="[]", latency_ms=5))
    asyncio.run(_seed_log(factory, trigger="image_gen", status="error", steps_json="[]", latency_ms=5))
    r = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind")
    assert r.status_code == 200
    logs = r.json()["tool_logs"]
    by_raw = {x["status_raw"]: x for x in logs}
    assert by_raw["ok"]["status"] == "success"
    assert by_raw["blocked"]["status"] == "blocked"   # 拦截≠失败
    assert by_raw["error"]["status"] == "failed"


# ---------------- B：任务记录兜底 status 口径归一 ----------------

def test_agent_mind_tasks_fallback_status归一(mind_db):
    """无正式 agent_tasks 时兜底 _summarize_task_logs：ok/partial/error 分别归一 success/partial/failed。"""
    factory, char_id = mind_db
    # 不种 agent_tasks（确保走兜底），只种 agent_task_logs
    asyncio.run(_seed_log(factory, trigger="chat", status="ok", steps_json="[]", latency_ms=5))
    asyncio.run(_seed_log(factory, trigger="chat", status="partial", steps_json="[]", latency_ms=5))
    asyncio.run(_seed_log(factory, trigger="scheduler", status="error", steps_json="[]", latency_ms=5))
    r = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    # 兜底按 id 倒序；此处断言三种 status 至少被兜底编码为 success/partial/failed
    statuses = {t["status"] for t in tasks}
    assert "success" in statuses
    assert "partial" in statuses
    assert "failed" in statuses


def test_summarize_task_logs_纯函数(mind_db):
    """_summarize_task_logs 直接验证：blocked/partial 不再被粗暴归为 failed。"""
    factory, char_id = mind_db
    asyncio.run(_seed_log(factory, trigger="chat", status="ok", steps_json="[]", latency_ms=5))
    asyncio.run(_seed_log(factory, trigger="scheduler", status="partial", steps_json="[]", latency_ms=5))

    async def _run():
        async with factory() as db:
            from app.application.characters import _summarize_task_logs  # F8：api 门面已删
            return await _summarize_task_logs(char_id, db)

    out = asyncio.run(_run())
    mapping = {x["trigger"]: x["status"] for x in out}
    assert mapping["chat"] == "success"
    assert mapping["scheduler"] == "partial"


# ---------------- D：主动消息备忘 ----------------

def test_generate_proactive_event_备忘录落库并剥离(monkeypatch):
    """主动消息里 LLM 输出 [MEMO] → 落 memo 并剥离标记；无 [MEMO] 时零副作用。"""
    import app.memory as mem_mod
    import app.memory.bm25_index as bm25
    import app.scheduling.message_generator as mg_mod
    from app.agent import loop as _loop
    from unittest.mock import AsyncMock

    saved = {}

    async def _fake_save(tool_name, payload, character_id):
        saved["tool"] = tool_name
        saved["payload"] = payload

    async def _fake_llm(**kw):
        return "记得带伞出门！\n[MEMO]今晚要去接朋友[/MEMO]"

    monkeypatch.setattr(mg_mod, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg_mod, "load_character_reasoning_level", AsyncMock(return_value=0))
    monkeypatch.setattr(mem_mod, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(mg_mod, "_load_recent_reflection", AsyncMock(return_value=""))
    # 主动备忘经统一工具入口落库，mock 掉执行入口捕获备忘录内容
    monkeypatch.setattr("app.application.chat.tools._execute_note_tool", _fake_save)
    _loop.AGENT_FLAGS["proactive_naturalness_score"] = False
    try:
        segments = asyncio.run(mg_mod.generate_proactive_event(
            character_name="小爱", character_bio="", character_personality="友善",
            character_id=1, user_id=1, current_status="在家",
        ))
    finally:
        _loop.AGENT_FLAGS["proactive_naturalness_score"] = True
    assert saved["tool"] == "note_memo"
    assert saved["payload"]["text"] == "今晚要去接朋友"
    assert saved["payload"]["author"] == "小爱"
    joined = "\n".join(segments)
    assert "[MEMO]" not in joined          # 剥离标记
    assert "记得带伞出门！" in joined
    bm25.clear_cache()


def test_generate_proactive_event_无memo零副作用(monkeypatch):
    """主动消息未输出 [MEMO] 时不落 memo，行为保持不变。"""
    import app.memory as mem_mod
    import app.scheduling.message_generator as mg_mod
    from app.agent import loop as _loop
    from unittest.mock import AsyncMock

    saved = []

    async def _fake_save(tool_name, payload, character_id):
        saved.append((tool_name, payload))

    async def _fake_llm(**kw):
        return "今天天气不错，散步去。"

    monkeypatch.setattr(mg_mod, "chat_completion", _fake_llm)
    monkeypatch.setattr(mg_mod, "load_character_reasoning_level", AsyncMock(return_value=0))
    monkeypatch.setattr(mem_mod, "search_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(mg_mod, "_load_recent_reflection", AsyncMock(return_value=""))
    monkeypatch.setattr("app.application.chat.tools._execute_note_tool", _fake_save)
    _loop.AGENT_FLAGS["proactive_naturalness_score"] = False
    try:
        segments = asyncio.run(mg_mod.generate_proactive_event(
            character_name="小爱", character_bio="", character_personality="友善",
            character_id=1, user_id=1, current_status="在家",
        ))
    finally:
        _loop.AGENT_FLAGS["proactive_naturalness_score"] = True
    assert saved == []
    assert "\n".join(segments) == "今天天气不错，散步去。"


# ---------------- 独立：classify 语义（守护 2026-08-23 状态口径） ----------------

def test_classify_状态桶():
    from app.agent.status import classify
    assert classify("ok") == "success"
    assert classify("done") == "success"
    assert classify("error") == "failed"
    assert classify("blocked") == "blocked"
    assert classify("partial") == "partial"
    assert classify(None) == "unknown"
