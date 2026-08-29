# -*- coding: utf-8 -*-
"""群聊 /play 命令测试（群聊游戏 Phase 2，2026-08-26）。

- /play 解析创建会话（用户观战、玩家=群内活跃角色、trigger=user_initiated）；
- 人数不足返回群聊提示（不报 500）；
- 非法游戏名返回群聊提示（不报 500）；
- /play 默认随机多人游戏。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.games import router as games_router
from app.api.chat_groups import router as chat_groups_router
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.game import GameSession


async def _noop_ai(sid: int) -> None:
    return


async def _fake_llm(**kwargs) -> str:
    """测试假 LLM：返回空回应，避免正常群聊消息链路触发网络请求。"""
    return '{"replies": []}'


@pytest.fixture
def play_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="play_cmd_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        from app.models.chat_group import ChatGroup, ChatGroupMember
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            db.add(User(id=2, username="u2", nickname="用户二"))
            for i in range(101, 106):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            db.add(AICharacter(id=201, user_id=2, name="外人", personality="内向",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            # 群 1：5 个活跃成员（满足狼人杀 4-8）
            db.add(ChatGroup(id=1, user_id=1, name="快乐小家"))
            for cid in range(101, 106):
                db.add(ChatGroupMember(group_id=1, character_id=cid))
            # 群 2：3 个活跃成员（不足狼人杀 4-8）
            db.add(ChatGroup(id=2, user_id=1, name="三人小群"))
            for cid in range(101, 104):
                db.add(ChatGroupMember(group_id=2, character_id=cid))
            # 群 3：含一个非活跃成员 → 活跃数 4（满足狼人杀）
            db.add(ChatGroup(id=3, user_id=1, name="四人小队"))
            for cid in range(101, 105):
                db.add(ChatGroupMember(group_id=3, character_id=cid))
            await db.commit()

    asyncio.run(_init())

    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _fake_llm)

    async def _override_db():
        async with factory() as db:
            yield db

    yield factory, _override_db
    asyncio.run(engine.dispose())


def _make_client(play_db, user_id: int = 1) -> TestClient:
    factory, _override_db = play_db
    app = FastAPI()
    app.include_router(chat_groups_router)
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


# ---------------- /play 解析创建会话 ----------------
def test_play_creates_session(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/1/messages", json={"content": "/play werewolf"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["game"] is not None
    assert data["game"]["game_type"] == "werewolf"
    assert data["game"]["player_count"] == 5
    assert data["replies"] == []
    # 会话已落库（group_id=1, trigger=user_initiated, 玩家=群成员）
    async def _check():
        async with play_db[0]() as db:
            row = (await db.execute(
                __import__("sqlalchemy").select(GameSession).where(GameSession.group_id == 1)
            )).scalars().first()
            assert row is not None
            assert row.game_type == "werewolf"
            assert row.trigger == "user_initiated"
            return row.id
    _sid = asyncio.run(_check())
    assert _sid == data["game"]["session_id"]


# ---------------- /play 中文名 / 别名 ----------------
def test_play_chinese_name(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/1/messages", json={"content": "/play 狼人杀"})
    assert r.status_code == 200, r.text
    assert r.json()["game"]["game_type"] == "werewolf"


# ---------------- 人数不足 → 群聊提示（不 500）----------------
def test_play_insufficient(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/2/messages", json={"content": "/play werewolf"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["game"] is None
    assert "需要" in data["notice"] or "不足" in data["notice"]


# ---------------- 非法游戏名 → 群聊提示（不 500）----------------
def test_play_invalid_game_name(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/1/messages", json={"content": "/play 星际争霸"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["game"] is None
    assert "没听懂" in data["notice"]


# ---------------- /play 默认随机多人 ----------------
def test_play_default_random(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/1/messages", json={"content": "/play"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["game"] is not None
    assert data["game"]["game_type"] in ("werewolf", "undercover", "liars_bar")


# ---------------- 非 /play 消息不受影响 ----------------
def test_play_ignores_normal_message(play_db):
    client = _make_client(play_db)
    r = client.post("/api/v1/chat-groups/1/messages", json={"content": "今晚吃啥？"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("game") is None
    assert "replies" in data
