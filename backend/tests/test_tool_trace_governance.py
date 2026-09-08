# -*- coding: utf-8 -*-
"""工具轨迹失真治理 R1 / R2 / R4 专项回归测试（2026-09-09，方案 §4.1 / §4.2 / §4.4）。

- R1：调度器「本轮未触发」不再写 agent_task_logs（止血写放大）；真执行失败记 error 而非 blocked；
      flag 关可一键回退旧行为。
- R2：status.classify 拆出中性 skipped（blocked 收紧为真正拦截）；get_agent_mind 过滤
      调度未触发噪音、窗口取大后分区返回 tool_logs + scheduler_trace.skipped_recent，
      并并入账号级真实 MCP 调用（mcp_calls，只读侧分区不物理合并）。
- R4：MCP 远端 isError=True 时适配层上抛 ToolExecutionError → ToolRunner 记 error（消灭假成功）；
      只读（幂等）工具失败重试 1 次、写工具不重试。

均为纯函数 / 临时库测试（不触碰 backend/data）；项目未装 pytest-asyncio，统一 asyncio.run。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent import loop
from app.agent import status as agent_status
from app.agent.tool_runner import execute_tool
from app.api import characters as characters_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent import AgentTaskLog
from app.models.character import AICharacter
from app.models.mcp import McpCallLog
from app.scheduling import arbiter

USER = 1
CHAR = 88101


# ────────────────────────── R1：arbiter trace 口径 ──────────────────────────

def _trace_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: calls.append(kw))
    return calls


def _item(char_id=7, **kw):
    item = {"type": "greeting", "priority": 1,
            "candidate": {"character_id": char_id, "user_id": 2, "session_id": 3}}
    item.update(kw)
    return item


def test_R1_未触发不写trace(monkeypatch):
    """本轮未触发（_execute 正常 return False、未抛错）→ 不写 agent_task_logs（默认 flag 开）。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    loop.AGENT_FLAGS["agent_trace_scheduler_only_executed"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(_item(), False, 10))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert calls == []


def test_R1_未触发flag关回退旧行为(monkeypatch):
    """flag 关 → 回到旧「每候选一条 blocked」（一键回退）。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    loop.AGENT_FLAGS["agent_trace_scheduler_only_executed"] = False
    try:
        asyncio.run(arbiter._trace_scheduler_task(_item(), False, 10))
    finally:
        loop.AGENT_FLAGS["agent_trace_scheduler_only_executed"] = True
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert len(calls) == 1
    assert calls[0]["status"] == "blocked"
    assert calls[0]["error"] == "限额/条件拦截（本轮未触发）"


def test_R1_真执行失败记error(monkeypatch):
    """真进入执行却失败（_execute 抛错，exec_error=True）→ 记 status=error（不是 blocked）。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    loop.AGENT_FLAGS["agent_trace_scheduler_mark_exec_error"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(_item(char_id=7), False, 33, exec_error=True))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert len(calls) == 1
    assert calls[0]["status"] == "error"
    assert calls[0]["route"] == "scheduler"
    assert "主动任务执行失败" in (calls[0]["error"] or "")
    assert "exec_error" in calls[0]["steps_json"]


def test_R1_真执行失败_mark_flag关回blocked(monkeypatch):
    """mark flag 关 → 真失败仍记 blocked（保留回退路径）。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    loop.AGENT_FLAGS["agent_trace_scheduler_mark_exec_error"] = False
    try:
        asyncio.run(arbiter._trace_scheduler_task(_item(), False, 5, exec_error=True))
    finally:
        loop.AGENT_FLAGS["agent_trace_scheduler_mark_exec_error"] = True
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert len(calls) == 1
    assert calls[0]["status"] == "blocked"


def test_R1_成功仍记ok(monkeypatch):
    """真正执行成功 → 仍记 ok（llm_calls=1），不受本次改动影响。"""
    calls = _trace_calls(monkeypatch)
    loop.AGENT_FLAGS["agent_loop_scheduler"] = True
    try:
        asyncio.run(arbiter._trace_scheduler_task(_item(), True, 12))
    finally:
        loop.AGENT_FLAGS["agent_loop_scheduler"] = False
    assert len(calls) == 1
    assert calls[0]["status"] == "ok"
    assert calls[0]["llm_calls"] == 1
    assert calls[0]["error"] is None


def test_R1_run_tick_执行抛错传exec_error(monkeypatch):
    """run_tick：_execute 抛异常 → exec_error=True 传给 _trace_scheduler_task（真失败可观测）。"""
    seen = {}

    class _Src:
        name = "fake"

        async def collect(self, ctx):
            return [{"type": "greeting", "priority": 1,
                     "candidate": {"character_id": 7, "user_id": 1, "session_id": 3}}]

    async def _boom(item):
        raise RuntimeError("llm down")

    async def _noop_decay():
        return None

    async def _motivation(char_id):
        return 0.0

    async def _trace(item, ok, latency, *, exec_error=False):
        seen["kwargs"] = {"ok": ok, "exec_error": exec_error}

    async def _log_candidate(item, ok):
        return None

    import app.domain.relationship.decay as _decay_mod
    monkeypatch.setattr(arbiter, "all_sources", lambda: [_Src()])
    monkeypatch.setattr(arbiter, "_execute", _boom)
    monkeypatch.setattr(arbiter, "_compute_motivation", _motivation)
    monkeypatch.setattr(arbiter, "_outreach_enabled", lambda: False)
    monkeypatch.setattr(arbiter, "_trace_scheduler_task", _trace)
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _log_candidate)
    monkeypatch.setattr(_decay_mod, "run_relationship_decay", _noop_decay)

    out = asyncio.run(arbiter.run_tick())
    assert out == []                      # 执行失败不计入 executed
    assert seen["kwargs"]["ok"] is False
    assert seen["kwargs"]["exec_error"] is True


def test_R1_run_tick_未触发不传exec_error(monkeypatch):
    """run_tick：_execute 正常 return False（未触发）→ exec_error=False。"""
    seen = {}

    class _Src:
        name = "fake"

        async def collect(self, ctx):
            return [{"type": "greeting", "priority": 1,
                     "candidate": {"character_id": 7, "user_id": 1, "session_id": 3}}]

    async def _noop(item):
        return False

    async def _noop_decay():
        return None

    async def _motivation(char_id):
        return 0.0

    async def _trace(item, ok, latency, *, exec_error=False):
        seen["kwargs"] = {"ok": ok, "exec_error": exec_error}

    async def _log_candidate(item, ok):
        return None

    import app.domain.relationship.decay as _decay_mod
    monkeypatch.setattr(arbiter, "all_sources", lambda: [_Src()])
    monkeypatch.setattr(arbiter, "_execute", _noop)
    monkeypatch.setattr(arbiter, "_compute_motivation", _motivation)
    monkeypatch.setattr(arbiter, "_outreach_enabled", lambda: False)
    monkeypatch.setattr(arbiter, "_trace_scheduler_task", _trace)
    monkeypatch.setattr(arbiter, "log_trigger_candidate", _log_candidate)
    monkeypatch.setattr(_decay_mod, "run_relationship_decay", _noop_decay)

    asyncio.run(arbiter.run_tick())
    assert seen["kwargs"] == {"ok": False, "exec_error": False}


# ────────────────────────── R2：状态口径 + 轨迹分区 ──────────────────────────

def test_R2_classify_skipped独立中性桶():
    """skipped / not_attempted → skipped（中性，非失败、非拦截）；blocked/intercepted 才归 blocked。"""
    assert agent_status.classify("skipped") == "skipped"
    assert agent_status.classify("not_attempted") == "skipped"
    assert agent_status.classify("blocked") == "blocked"
    assert agent_status.classify("intercepted") == "blocked"
    assert agent_status.classify("ok") == "success"
    assert agent_status.classify("error") == "failed"
    # 历史库旧值不受影响（不回填、不删数据）
    assert agent_status.classify("BLOCKED") == "blocked"


@pytest.fixture()
def mind_db():
    """临时 SQLite 文件库（不触碰 backend/data），种子一个角色并返回 (factory, character_id)。"""
    tmp = tempfile.mkdtemp(prefix="ambrace_trace_")
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


def test_R2_get_agent_mind_过滤未触发噪音并计数(mind_db):
    """调度「未触发」（scheduler + blocked/skipped + 无真实调用）不进 tool_logs，只计 skipped_recent。"""
    factory, char_id = mind_db
    # 3 条未触发噪音 + 2 条真实记录
    for _ in range(3):
        asyncio.run(_seed_log(factory, trigger="scheduler", status="blocked",
                              steps_json="[]", latency_ms=1, tool_calls=0, llm_calls=0))
    asyncio.run(_seed_log(factory, trigger="scheduler", status="ok",
                          steps_json="[]", latency_ms=2, llm_calls=1, tool_calls=0))
    asyncio.run(_seed_log(factory, trigger="chat", status="ok",
                          steps_json="[]", latency_ms=3, llm_calls=1, tool_calls=1))

    body = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind").json()
    logs = body["tool_logs"]
    assert len(logs) == 2
    assert all(l["trigger"] != "scheduler" or l["status"] == "success" for l in logs)
    assert body["scheduler_trace"]["skipped_recent"] == 3
    assert body["scheduler_trace"]["window"] == 200


def test_R2_get_agent_mind_真执行失败的调度任务保留(mind_db):
    """调度器真执行失败（status=error）不是噪音，必须保留在 tool_logs 里（可观测性不丢）。"""
    factory, char_id = mind_db
    asyncio.run(_seed_log(factory, trigger="scheduler", status="error",
                          steps_json="[]", latency_ms=9, tool_calls=0, llm_calls=0))
    body = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind").json()
    assert len(body["tool_logs"]) == 1
    assert body["tool_logs"][0]["status"] == "failed"
    assert body["scheduler_trace"]["skipped_recent"] == 0


def test_R2_get_agent_mind_有工具调用的blocked不误过滤(mind_db):
    """scheduler + blocked 但 tool_calls/llm_calls>0（真被拦在执行中）→ 保留。"""
    factory, char_id = mind_db
    asyncio.run(_seed_log(factory, trigger="scheduler", status="blocked",
                          steps_json="[]", latency_ms=4, tool_calls=1, llm_calls=0))
    body = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind").json()
    assert len(body["tool_logs"]) == 1
    assert body["scheduler_trace"]["skipped_recent"] == 0


def test_R2_get_agent_mind_mcp_calls账号级分区(mind_db):
    """mcp_calls 按 user_id 返回账号级真实 MCP 调用（不写 agent_task_logs，读侧分区）。"""
    factory, char_id = mind_db

    async def _seed_mcp():
        async with factory() as db:
            db.add(McpCallLog(user_id=USER, server_id=1, server_name="srv", tool="mcp.srv.echo",
                              status="ok", latency_ms=7))
            db.add(McpCallLog(user_id=USER, server_id=1, server_name="srv", tool="mcp.srv.echo",
                              status="error", error="server not connected", latency_ms=3))
            db.add(McpCallLog(user_id=999, server_id=2, server_name="other", tool="mcp.other.x",
                              status="ok", latency_ms=1))
            await db.commit()

    asyncio.run(_seed_mcp())
    body = _make_client(factory).get(f"/api/v1/characters/{char_id}/agent-mind").json()
    mcp = body["mcp_calls"]
    assert len(mcp) == 2                       # 只含本账号（user_id=1）
    assert {m["server_name"] for m in mcp} == {"srv"}
    by_status = {m["status"]: m for m in mcp}
    assert by_status["success"]["status_raw"] == "ok"
    assert by_status["failed"]["error"] == "server not connected"
    # agent_task_logs 未被写入 MCP 记录（不物理合并）
    assert body["tool_logs"] == []


# ────────────────────────── R4：MCP 假成功治理 ──────────────────────────

def _mcp_spec(tool_name: str, server_name: str = "srv", server_id: int = 1):
    from app.mcp.tool_adapter import mcp_tool_to_spec
    return mcp_tool_to_spec(server_name, {"name": tool_name, "description": "d"}, server_id)


async def _allow(spec, uid):
    """权限放行（MCP scope 无 tool_permissions 行时默认行为不稳定，测试内显式固定）。"""
    return "allow"


def test_R4_isError上抛ToolExecutionError(monkeypatch):
    """远端 isError=True → 适配层抛 ToolExecutionError（错误文本透传），不再返回 ok=False。"""
    from app.mcp import manager as mgr
    from app.mcp.tool_adapter import ToolExecutionError

    async def _call(server_id, tool_name, payload):
        return {"content": [], "isError": True, "error": "server not connected"}

    monkeypatch.setattr(mgr.mcp_manager, "call_tool", _call)
    spec = _mcp_spec("read_thing")
    with pytest.raises(ToolExecutionError) as ei:
        asyncio.run(spec.execute({}))
    assert "server not connected" in str(ei.value)
    assert ei.value.tool == "mcp.srv.read_thing"


def test_R4_成功仍返回ok():
    """isError 缺失 / False → 正常返回 ok=True + text（零回归）。"""
    import app.mcp.manager as _m

    async def _call(server_id, tool_name, payload):
        return {"content": [{"type": "text", "text": "echoed"}], "isError": False}

    orig = _m.mcp_manager.call_tool
    _m.mcp_manager.call_tool = _call
    try:
        spec = _mcp_spec("read_thing")
        out = asyncio.run(spec.execute({}))
    finally:
        _m.mcp_manager.call_tool = orig
    assert out["ok"] is True
    assert out["text"] == "echoed"


def test_R4_execute_tool_未连接记error(monkeypatch):
    """isError → execute_tool 记 status=error 且错误文本透传（不再是假成功 ok）。"""
    from app.mcp import manager as mgr
    from app.agent import tool_runner

    async def _call(server_id, tool_name, payload):
        return {"content": [], "isError": True, "error": "call timeout"}

    monkeypatch.setattr(mgr.mcp_manager, "call_tool", _call)
    monkeypatch.setattr(tool_runner, "check_tool_permission", _allow)
    spec = _mcp_spec("write_thing")          # 写工具：idempotent=False
    out = asyncio.run(execute_tool(spec, {}, user_id=1))
    assert out["status"] == "error"
    assert "call timeout" in out["error"]


def test_R4_只读工具失败重试一次_写工具不重试(monkeypatch):
    """幂等（只读 low risk）MCP 工具失败自动重试 1 次；写工具（high risk）不重试。"""
    from app.mcp import manager as mgr
    from app.agent import tool_runner

    counter = {"n": 0}

    async def _call(server_id, tool_name, payload):
        counter["n"] += 1
        return {"content": [], "isError": True, "error": "boom"}

    monkeypatch.setattr(mgr.mcp_manager, "call_tool", _call)
    monkeypatch.setattr(tool_runner, "check_tool_permission", _allow)

    # 只读（read_* → RISK_LOW → idempotent=True）→ attempts=2
    counter["n"] = 0
    out = asyncio.run(execute_tool(_mcp_spec("read_thing"), {}, user_id=1))
    assert out["status"] == "error"
    assert counter["n"] == 2

    # 写（write_* → RISK_HIGH → idempotent=False）→ attempts=1
    counter["n"] = 0
    out = asyncio.run(execute_tool(_mcp_spec("write_thing"), {}, user_id=1))
    assert out["status"] == "error"
    assert counter["n"] == 1
