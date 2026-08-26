# -*- coding: utf-8 -*-
"""游戏 API 冒烟测试（群聊游戏 Phase 1，2026-08-26）。

- catalog / create / action / state / abort / archive / history 基本冒烟；
- 多用户隔离（他人访问 404/403）。
用临时库 + 关闭 AI 自动回合，保证确定性。
"""
import asyncio
import json
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.games import router as games_router
from app.auth.deps import get_current_user_id
from app.models.game import GameSession


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(games_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _noop_ai(sid: int) -> None:
    """测试中关闭 AI 自动回合。"""
    return


@pytest.fixture
def game_api_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="game_api_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        from app.models.character import AICharacter
        from app.models.user import User
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户一"))
            db.add(User(id=2, username="u2", nickname="用户二"))
            for i in range(101, 107):
                db.add(AICharacter(id=i, user_id=1, name=f"角色{i}", personality="外向",
                                   chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.games.async_session_factory", factory)
    monkeypatch.setattr("app.memory.service.async_session_factory", factory)
    monkeypatch.setattr("app.api.games._resume_ai_turns", _noop_ai)
    yield factory
    asyncio.run(engine.dispose())


def _create_undercover(client):
    r = client.post("/api/v1/games/sessions", json={
        "game_type": "undercover", "player_ids": [101, 102, 103],
        "user_as_player": True,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["session_id"] > 0
    assert data["state"]["my"]["seat"] == 0
    return data["session_id"], data["state"]


# ---------------- catalog ----------------
def test_catalog(game_api_db):
    client = _make_client(1)
    r = client.get("/api/v1/games/catalog")
    assert r.status_code == 200
    games = r.json()["games"]
    assert {g["game_type"] for g in games} == {"undercover", "truth_or_dare", "twenty_q"}
    assert any(g["needs_gm"] for g in games)


# ---------------- create + state ----------------
def test_create_and_state(game_api_db):
    client = _make_client(1)
    sid, state = _create_undercover(client)
    assert state["status"] == "playing"
    assert state["phase"] == "describe"
    assert len(state["players"]) == 4

    r = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0})
    assert r.status_code == 200
    st = r.json()
    assert st["my"]["seat"] == 0
    assert st["my"]["private"] != {}  # 玩家拿到自己的词
    # 观战视角不带 private
    r2 = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": -1})
    assert r2.status_code == 200
    assert r2.json()["my"] is None


# ---------------- action ----------------
def test_action(game_api_db):
    client = _make_client(1)
    sid, state = _create_undercover(client)
    seat = state["my"]["seat"]
    r = client.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": seat, "action": "describe", "payload": {"content": "它和吃喝有点关系。"}})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # 非法动作（重复描述 / 越权）→ 400/403
    bad = client.post(f"/api/v1/games/sessions/{sid}/action",
                      json={"seat": seat, "action": "describe", "payload": {"content": "再来一次"}})
    assert bad.status_code in (400, 403)
    # 观战者/他人不可行动
    other = _make_client(2)
    r2 = other.post(f"/api/v1/games/sessions/{sid}/action",
                    json={"seat": seat, "action": "describe", "payload": {"content": "别人"}})
    assert r2.status_code in (403, 404)


# ---------------- abort ----------------
def test_abort(game_api_db):
    client = _make_client(1)
    sid, _ = _create_undercover(client)
    r = client.post(f"/api/v1/games/sessions/{sid}/abort")
    assert r.status_code == 200
    assert r.json()["status"] == "aborted"
    st = client.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).json()
    assert st["status"] == "aborted"


# ---------------- archive ----------------
def test_archive(game_api_db):
    client = _make_client(1)
    sid, _ = _create_undercover(client)
    async def _finish():
        async with game_api_db() as db:
            s = await db.get(GameSession, sid)
            s.status = "finished"
            s.winner_side = "civilians"
            s.round = 3
            s.archive_json = json.dumps({
                "game_type": "undercover", "game_name": "谁是卧底", "player_count": 4,
                "rounds": 3, "winner_side": "civilians", "players": [], "timeline": [],
            }, ensure_ascii=False)
            await db.commit()
    asyncio.run(_finish())
    r = client.get(f"/api/v1/games/sessions/{sid}/archive")
    assert r.status_code == 200
    ar = r.json()["archive"]
    assert ar["game_type"] == "undercover"
    assert ar["winner_side"] == "civilians"
    # 未结束的会话不可取手札
    sid2, _ = _create_undercover(client)
    assert client.get(f"/api/v1/games/sessions/{sid2}/archive").status_code == 400


# ---------------- history ----------------
def test_history(game_api_db):
    client = _make_client(1)
    sid, _ = _create_undercover(client)
    async def _finish():
        async with game_api_db() as db:
            s = await db.get(GameSession, sid)
            s.status = "finished"
            s.winner_side = "civilians"
            s.round = 3
            s.archive_json = json.dumps({"game_type": "undercover", "player_count": 4, "rounds": 3},
                                        ensure_ascii=False)
            await db.commit()
    asyncio.run(_finish())
    r = client.get("/api/v1/games/history")
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(it["session_id"] == sid and it["status"] == "finished" for it in items)
    # game_type 过滤
    assert client.get("/api/v1/games/history", params={"game_type": "twenty_q"}).json()["items"] == []


# ---------------- 多用户隔离 ----------------
def test_multi_user_isolation(game_api_db):
    owner = _make_client(1)
    sid, _ = _create_undercover(owner)
    intruder = _make_client(2)
    assert intruder.get(f"/api/v1/games/sessions/{sid}/state", params={"seat": 0}).status_code == 404
    assert intruder.post(f"/api/v1/games/sessions/{sid}/abort").status_code == 403
    assert intruder.get(f"/api/v1/games/sessions/{sid}/archive").status_code == 404
    # 未结束不可取手札（owner 视角）
    assert owner.get(f"/api/v1/games/sessions/{sid}/archive").status_code == 400
