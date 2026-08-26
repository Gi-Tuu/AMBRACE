# -*- coding: utf-8 -*-
"""v3.2.8 全量审查 4 项修复的回归单测。

① P1-1：GET /api/v1/scheduler/stats 不传 character_id 不再 500，且经 AICharacter 关联过滤统计正确。
② P2-1：POST /api/v1/proactive/trigger/test 无 LLM Key（generate_proactive_event 抛 RuntimeError）→ 400 + friendly 文案；其他异常 → 502。
③ P2-2：Alembic 新迁移（04dd1d6c5544）在全新库 alembic upgrade head 后建出 user_rhythm / browser_snapshots。
（P3-1 无独立断言：异常时仅记 warning 日志，不向上抛。）
"""
import asyncio
import os
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import scheduler as scheduler_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.scheduler.message_generator import score_naturalness

ADMIN = 1


# ---------------- ① P1-1：scheduler/stats 不传 character_id ----------------

async def _seed_stats(factory):
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat_session import ChatSession
    from app.models.chat_message import ChatMessage
    from app.models.proactive_settings import ProactiveMessageLog, ProactiveTriggerLog

    now = datetime.now(timezone.utc)

    async def _seed():
        async with factory() as db:
            db.add_all([
                User(id=1, username="u1", nickname="用户1"),
                User(id=200, username="u2", nickname="用户2"),
                AICharacter(id=1, user_id=1, name="小爱"),
                AICharacter(id=2, user_id=200, name="小满"),
                ChatSession(id=1, user_id=1, character_id=1),
            ])
            await db.flush()
            # user1 的主动消息（2 条，命中）；user2 的主动消息（应被 AICharacter 关联过滤掉）
            db.add_all([
                ProactiveMessageLog(
                    character_id=1, session_id=1, message_type="proactive", content="早安",
                    created_at=(now - timedelta(minutes=30)).replace(tzinfo=None)),
                ProactiveMessageLog(
                    character_id=1, session_id=1, message_type="proactive", content="晚安",
                    created_at=(now - timedelta(minutes=20)).replace(tzinfo=None)),
                ProactiveMessageLog(
                    character_id=2, session_id=None, message_type="proactive", content="别人的",
                    created_at=(now - timedelta(minutes=10)).replace(tzinfo=None)),
                # user1 的触发日志：2 approved + 1 rejected；user2 的 1 条（应被过滤）
                ProactiveTriggerLog(
                    character_id=1, user_id=1, trigger_type="motivation", decision="approved",
                    created_at=(now - timedelta(minutes=30)).replace(tzinfo=None)),
                ProactiveTriggerLog(
                    character_id=1, user_id=1, trigger_type="greeting", decision="approved",
                    created_at=(now - timedelta(minutes=25)).replace(tzinfo=None)),
                ProactiveTriggerLog(
                    character_id=1, user_id=1, trigger_type="motivation", decision="rejected",
                    created_at=(now - timedelta(minutes=20)).replace(tzinfo=None)),
                ProactiveTriggerLog(
                    character_id=2, user_id=200, trigger_type="motivation", decision="approved",
                    created_at=(now - timedelta(minutes=10)).replace(tzinfo=None)),
                # session1 中用户回复（晚于首条主动消息，应计入回复率）
                ChatMessage(
                    session_id=1, sender_type="user", content="吃了吗",
                    created_at=(now - timedelta(minutes=15)).replace(tzinfo=None)),
            ])
            await db.commit()

    await _seed()


@pytest.fixture()
def stats_client():
    tmp = tempfile.mkdtemp(prefix="stats_")
    db_path = os.path.join(tmp, "t.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    asyncio.run(_seed_stats(factory))

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

    app = FastAPI()
    app.include_router(scheduler_api.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user_id] = lambda: 1
    client = TestClient(app)
    yield client
    engine.sync_engine.dispose()


def test_stats_without_character_id_returns_200_and_correct(stats_client):
    """P1-1：不传 character_id 时 GET /api/v1/scheduler/stats 返回 200，且统计正确（不 500）。"""
    r = stats_client.get("/api/v1/scheduler/stats")
    assert r.status_code == 200
    body = r.json()
    # 只统计 user_id=1 的数据（AICharacter 关联过滤），不含 user2 的 1 条
    assert body["total_triggered"] == 3
    assert body["total_sent"] == 2
    assert body["total_cancelled"] == 1
    assert body["reply_rate"] == 1.0
    assert body["trigger_type_stats"] == {"motivation": 2, "greeting": 1}


def test_stats_with_character_id_still_ok(stats_client):
    """P1-1 回归：传 character_id 走原分支，仍返回 200。"""
    r = stats_client.get("/api/v1/scheduler/stats", params={"character_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total_sent"] == 2
    assert body["total_triggered"] == 3


# ---------------- ② P2-1：trigger_test 无 LLM Key 400 / 其他异常 502 ----------------

class _FakeSession:
    def __init__(self, char):
        self._char = char

    async def get(self, model, pk, **kw):
        if self._char is None:
            return None
        return self._char if pk == self._char.id else None


def _char(char_id=1):
    return SimpleNamespace(
        id=char_id, user_id=1, name="小爱", bio="",
        personality="友善", current_status="在家", relationship_summary="普通朋友",
    )


def _make_app(char, user_id):
    app = FastAPI()
    app.include_router(scheduler_api.router)
    app.include_router(scheduler_api.proactive_router)

    async def _fake_db():
        yield _FakeSession(char)

    app.dependency_overrides[get_db] = _fake_db
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return app


def _patch_api_deps(monkeypatch, gen_delta):
    async def _is_admin(uid):
        return uid == ADMIN

    async def _gen(**kw):
        if isinstance(gen_delta, Exception):
            raise gen_delta
        return gen_delta

    async def _sess(char_id, user_id):
        return {"id": 1}

    async def _last(sid, limit=10):
        return "用户: hi"

    monkeypatch.setattr("app.services.permission_service.is_admin_user", _is_admin)
    monkeypatch.setattr("app.scheduler.message_generator.generate_proactive_event", _gen)
    monkeypatch.setattr("app.scheduler.triggers.get_latest_session", _sess)
    monkeypatch.setattr("app.scheduler.triggers.get_last_messages", _last)


def test_trigger_test_no_llm_key_returns_400(monkeypatch):
    """P2-1：无 LLM Key 时 generate_proactive_event 抛 RuntimeError → 400 + friendly 文案，不是 500。"""
    from app.utils.errors import friendly_llm_error
    err = RuntimeError("未配置 LLM API Key：请在管理端配置服务器级 API（PUT /api/v1/system/api-config/server）")
    _patch_api_deps(monkeypatch, err)
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 1})
    assert r.status_code == 400
    assert r.json()["detail"] == friendly_llm_error(err)
    assert r.json()["detail"].startswith("LLM API Key 无效或未配置")


def test_trigger_test_generic_error_returns_502(monkeypatch):
    """P2-1：非 Key 异常 → 记 warning + HTTPException 502，不 500。"""
    _patch_api_deps(monkeypatch, ValueError("boom"))
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 1})
    assert r.status_code == 502
    assert r.json()["detail"] == "生成失败：boom"


def test_trigger_test_ok_still_works(monkeypatch):
    """P2-1 回归：正常生成仍返回 200。"""
    _patch_api_deps(monkeypatch, (["你好呀！", "今天天气不错。"], ""))
    client = TestClient(_make_app(_char(), ADMIN))
    r = client.post("/api/v1/proactive/trigger/test", json={"character_id": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "你好呀！\n今天天气不错。"
    assert body["naturalness_score"] == score_naturalness(["你好呀！", "今天天气不错。"])


# ---------------- ③ P2-2：Alembic 新迁移建出两张表 ----------------

def test_alembic_migration_creates_new_tables(monkeypatch):
    """全新库 alembic upgrade head 后 user_rhythm / browser_snapshots / mcp_servers / mcp_call_logs + life_loop 新表存在（表数 93 + alembic_version = 94）。"""
    from alembic import command
    from app.config import settings as _settings
    from app.db.migrate import _alembic_config

    tmp = tempfile.mkdtemp(prefix="alembic_")
    db_path = os.path.join(tmp, "fresh.db").replace("\\", "/")
    monkeypatch.setattr(_settings, "database_url", "sqlite+aiosqlite:///" + db_path)

    command.upgrade(_alembic_config(), "head")

    conn = sqlite3.connect(db_path)
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    assert "user_rhythm" in names
    assert "browser_snapshots" in names
    assert "mcp_servers" in names  # AMBRACE MCP 接入（Phase 1）新增表
    assert "mcp_call_logs" in names  # AMBRACE MCP 接入（Phase 4）新增表
    assert "game_sessions" in names  # 群聊游戏 Phase 1
    assert "game_players" in names
    assert "game_events" in names
    assert "game_memories" in names
    assert "life_followups" in names  # Life Loop v1.1（2026-08-26）
    assert "life_chat_intents" in names  # Life Loop v1.1（2026-08-26）
    assert "alembic_version" in names
    assert len(names) == 98  # 93 张应用表 + 4 张游戏表 + alembic_version
