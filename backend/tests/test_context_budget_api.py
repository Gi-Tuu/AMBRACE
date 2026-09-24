# -*- coding: utf-8 -*-
"""P2b（2026-09-24）上下文预算读数端 GET /api/v1/system/context-budget。

这是 P2a（context_budget_reserve）的**读数端**测试，守的口径：
1. flag 关（默认）：effective == 总硬顶（逐字旧行为，读端不得自己减一块）；
   flag 开：effective == 总硬顶 − 回复预留 − 工具声明预留；
2. 下限保底：预留之和 ≥ 总硬顶（配置退化）时不出现 0 或负值，也不越过总硬顶；
3. 无裁剪记录 → last_clip=null、clip_count_24h=0；有记录 → 解析出埋点字段、
   **只取当前用户**、按 id desc 取最近一条，24h 窗口外的行不计数但仍是「最近一次」；
4. steps_json 是坏 JSON → 按「无记录」处理，不抛异常；
5. 查库异常 → 一律 fail-open 返回 200（预算段照出 + last_clip=null + error 文案），绝不 500；
6. 埋点 detail 里的 blocks 数组（含用户上下文块头部原文）不回传。

临时库统一走 tests/_dbclone 模板克隆（不触生产库）；本端点纯读，用例不改任何状态。
"""
import asyncio
import json
import os
from datetime import timedelta

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.agent import context_builder as cb
from app.agent import loop as agent_loop
from app.api import system as system_api
from app.application import system as system_svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.agent import AgentTaskLog
from app.utils.timeutil import now_naive_utc

# 快测档：本文件每例起一次临时库（_dbclone 克隆），属重量级/集成型用例
pytestmark = pytest.mark.slow

USER = 1
OTHER = 2
_FLAG_KEY = "context_budget_reserve"


@pytest.fixture()
def budget_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库（模板克隆）；不 patch 全局工厂，用例经 get_db 依赖注入拿会话。"""
    engine = clone_engine(os.path.join(str(tmp_path), "t.db"))
    factory = make_session_factory(engine)
    # 逐例锁定默认口径（关），防上一例把开关留在开态
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, False)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture()
def flag_on(monkeypatch):
    """把 P2a 的灰度开关拨到「开」（AGENT_FLAGS 是进程内字典，monkeypatch 自动还原）。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, True)


def _make_client(factory, user_id: int = USER) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)

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


def _clip_detail(**over) -> dict:
    """按 P2a 埋点口径造 detail（flag 开且真裁剪时才会写这六个字段 + 旧的两个字段）。"""
    detail = {
        "total_removed": 900,
        "blocks": [{"removed": 500, "head": "【全景记忆·织库】独家记忆ABC123"}],
        "budget": 7700,
        "used": 8120,
        "reserve_reply": cb.REPLY_RESERVE_TOKENS,
        "reserve_tools": cb.TOOL_DEFS_RESERVE_TOKENS,
        "clipped_blocks": 1,
        "freed_chars": 900,
    }
    detail.update(over)
    return detail


def _seed(factory, *, steps_json, user_id: int = USER, character_id: int = 13,
          route: str = "quota_clipped_sections", trigger: str = "memory_obs",
          created_at=None):
    async def _main():
        async with factory() as db:
            db.add(AgentTaskLog(
                user_id=user_id, character_id=character_id, trigger=trigger,
                route=route, steps_json=steps_json, status="ok",
                **({"created_at": created_at} if created_at else {}),
            ))
            await db.commit()
    asyncio.run(_main())


# ── 预算段 ────────────────────────────────────────────────────────────────

def test_flag_off_effective_equals_total(budget_db):
    r = _make_client(budget_db).get("/api/v1/system/context-budget")
    assert r.status_code == 200
    body = r.json()
    assert body["flag_enabled"] is False
    assert body["total_quota_tokens"] == cb.TOTAL_SYSTEM_QUOTA_TOKENS == 9000
    # 关＝读端不得自行减一块：与旧行为逐字一致
    assert body["effective_budget_tokens"] == body["total_quota_tokens"]
    assert body["reserve_reply_tokens"] == 800
    assert body["reserve_tools_tokens"] == 500
    assert body["floor_tokens"] == 256
    assert body["error"] == ""


def test_flag_on_effective_subtracts_both_reserves(budget_db, flag_on):
    body = _make_client(budget_db).get("/api/v1/system/context-budget").json()
    assert body["flag_enabled"] is True
    assert body["effective_budget_tokens"] == (
        cb.TOTAL_SYSTEM_QUOTA_TOKENS
        - cb.REPLY_RESERVE_TOKENS - cb.TOOL_DEFS_RESERVE_TOKENS
    ) == 9000 - 1300


@pytest.mark.parametrize("total", [1000, 100])
def test_degenerate_reserve_still_floors_above_zero(budget_db, flag_on, monkeypatch, total):
    """配置退化（预留之和 ≥ 总硬顶）：走下限保底，既不为 0/负也不越过总硬顶。"""
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", total)
    body = _make_client(budget_db).get("/api/v1/system/context-budget").json()
    assert body["effective_budget_tokens"] > 0
    assert body["effective_budget_tokens"] <= total
    assert body["effective_budget_tokens"] == min(cb.MIN_SYSTEM_BUDGET_TOKENS, total)


def test_service_reads_live_flag(budget_db, monkeypatch):
    """服务层直接调用也要跟着 AGENT_FLAGS 现值走（读数端不得缓存/自己另算一套）。"""
    async def _run():
        async with budget_db() as db:
            return await system_svc.get_context_budget(USER, db)

    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, False)
    assert asyncio.run(_run())["effective_budget_tokens"] == cb.TOTAL_SYSTEM_QUOTA_TOKENS
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, _FLAG_KEY, True)
    assert asyncio.run(_run())["effective_budget_tokens"] == cb.TOTAL_SYSTEM_QUOTA_TOKENS - 1300


# ── 裁剪段 ────────────────────────────────────────────────────────────────

def test_no_clip_record(budget_db):
    body = _make_client(budget_db).get("/api/v1/system/context-budget").json()
    assert body["last_clip"] is None
    assert body["clip_count_24h"] == 0


def test_clip_record_parsed_scoped_and_ordered(budget_db):
    _seed(budget_db, user_id=OTHER, steps_json=json.dumps(_clip_detail(budget=1, used=2)))
    _seed(budget_db, character_id=1, steps_json=json.dumps(_clip_detail(budget=7000)))  # 自己的旧的一条
    _seed(budget_db, character_id=13, steps_json=json.dumps(_clip_detail(budget=7700, used=8120)))
    body = _make_client(budget_db, user_id=USER).get("/api/v1/system/context-budget").json()
    last = body["last_clip"]
    assert last is not None and last["character_id"] == 13          # id desc：取自己最近一条
    assert last["detail"]["budget"] == 7700 and last["detail"]["used"] == 8120
    assert last["detail"]["reserve_reply"] == 800 and last["detail"]["reserve_tools"] == 500
    assert last["detail"]["clipped_blocks"] == 1 and last["detail"]["freed_chars"] == 900
    assert body["clip_count_24h"] == 2                              # 只数自己的，不数他人
    # 红线：埋点 blocks 数组里是用户上下文块头部原文，读数端不外传
    assert "blocks" not in last["detail"]
    assert "独家记忆ABC123" not in json.dumps(body, ensure_ascii=False)


def test_clip_outside_24h_still_shows_as_last(budget_db):
    old = now_naive_utc() - timedelta(hours=30)
    _seed(budget_db, steps_json=json.dumps(_clip_detail()), created_at=old)
    body = _make_client(budget_db).get("/api/v1/system/context-budget").json()
    assert body["clip_count_24h"] == 0
    assert body["last_clip"] is not None, "「最近一次被裁」不受 24h 窗口限制"


def test_other_route_or_trigger_ignored(budget_db):
    _seed(budget_db, route="two_pass_trace", steps_json=json.dumps(_clip_detail()))
    _seed(budget_db, trigger="chat", steps_json=json.dumps(_clip_detail()))
    body = _make_client(budget_db).get("/api/v1/system/context-budget").json()
    assert body["last_clip"] is None and body["clip_count_24h"] == 0


def test_broken_steps_json_is_treated_as_no_record(budget_db):
    _seed(budget_db, steps_json="{not json at all")
    _seed(budget_db, steps_json='["一个数组而不是对象"]')
    r = _make_client(budget_db).get("/api/v1/system/context-budget")
    assert r.status_code == 200
    body = r.json()
    assert body["last_clip"] is None
    assert body["clip_count_24h"] == 2, "计数与解析互不影响：条数照实报"
    assert body["error"] == ""


def test_db_failure_fails_open_not_500():
    """查库抛异常 → 200 + 预算段 + last_clip=null + error 文案（诊断导出不能被读日志拖死）。"""
    class _BrokenDb:
        """鸭子类型的坏会话：只要碰到 execute 就炸（本端点只读，不碰真实引擎）。"""

        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    app = FastAPI()
    app.include_router(system_api.router)

    async def _db():
        yield _BrokenDb()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user_id] = lambda: USER
    r = TestClient(app).get("/api/v1/system/context-budget")
    assert r.status_code == 200
    body = r.json()
    assert body["last_clip"] is None
    assert body["clip_count_24h"] == 0
    assert body["error"].startswith("clip_query_failed")
    assert body["effective_budget_tokens"] == cb.TOTAL_SYSTEM_QUOTA_TOKENS  # 预算段与库无关，照出
