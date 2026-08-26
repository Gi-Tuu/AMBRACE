# -*- coding: utf-8 -*-
"""小家大地图 v1.1 后端测试（life_home.py world 载荷，2026-08-26）。

- flag life_home_worldmap_enabled=False（默认）：world=null（向后兼容旧独立房间视图）
- flag 开：world 载荷含 room_origins/adjacency/exit/room_size/character，character.wx/wy 为
  origin + 房间中心；current_room 读 st.current_room；新增 location
- 角色房间变更（如卧室）→ wx/wy 随 origin 变化；location="world" 时 character.location 透传
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent.loop import AGENT_FLAGS
from app.api.life_home import WORLD_LAYOUT, ROOM_W, ROOM_H, router as life_home_router
from app.auth.deps import get_current_user_id
from app.models.character import AICharacter
from app.models.life import LifeState

OWNER = 100
ADMIN = 1


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(life_home_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


@pytest.fixture()
def home_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch life_home.async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="life_loop_world_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(AICharacter(id=1, user_id=OWNER, name="小爱", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.life_home.async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _seed_state(factory, **fields):
    async def _f():
        async with factory() as db:
            st = LifeState(character_id=1, **fields)
            db.add(st)
            await db.commit()
    asyncio.run(_f())


# ---------------- world=null（默认，向后兼容） ----------------

def test_world_default_null(home_db):
    client = _make_client(OWNER)
    data = client.get("/api/v1/life-home/state").json()
    assert data["world"] is None
    assert data["current_room"] == "living"
    assert data["location"] == "home"


def test_world_off_flag_null_even_with_room(home_db):
    """flag 关时即使角色在卧室，world 仍为 null（旧视图）；current_room 透传真实值。"""
    _seed_state(home_db, current_room="bedroom", location="home")
    client = _make_client(OWNER)
    data = client.get("/api/v1/life-home/state").json()
    assert data["world"] is None
    assert data["current_room"] == "bedroom"


# ---------------- world=载荷（flag 开） ----------------

def test_world_flag_on_payload(home_db, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "life_home_worldmap_enabled", True)
    client = _make_client(OWNER)
    data = client.get("/api/v1/life-home/state").json()
    world = data["world"]
    assert world is not None
    assert world["room_origins"] == WORLD_LAYOUT["room_origins"]
    assert world["adjacency"] == WORLD_LAYOUT["adjacency"]
    assert world["exit"] == WORLD_LAYOUT["exit"]
    assert world["room_size"] == {"w": ROOM_W, "h": ROOM_H}
    ch = world["character"]
    assert ch["room"] == "living"
    assert ch["location"] == "home"
    # 默认客厅 origin(0,0) + 中心
    assert ch["wx"] == WORLD_LAYOUT["room_origins"]["living"]["wx"] + ROOM_W / 2
    assert ch["wy"] == WORLD_LAYOUT["room_origins"]["living"]["wy"] + ROOM_H / 2


def test_world_flag_on_character_room_location(home_db, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "life_home_worldmap_enabled", True)
    _seed_state(home_db, current_room="bedroom", location="world")
    client = _make_client(OWNER)
    data = client.get("/api/v1/life-home/state").json()
    world = data["world"]
    ch = world["character"]
    assert ch["room"] == "bedroom"
    assert ch["location"] == "world"
    o = WORLD_LAYOUT["room_origins"]["bedroom"]
    assert ch["wx"] == o["wx"] + ROOM_W / 2
    assert ch["wy"] == o["wy"] + ROOM_H / 2


def test_world_flag_on_unknown_room_fallback_living(home_db, monkeypatch):
    monkeypatch.setitem(AGENT_FLAGS, "life_home_worldmap_enabled", True)
    _seed_state(home_db, current_room="garage", location="home")
    client = _make_client(OWNER)
    data = client.get("/api/v1/life-home/state").json()
    ch = data["world"]["character"]
    assert ch["room"] == "garage"          # 透传
    assert ch["wx"] == WORLD_LAYOUT["room_origins"]["living"]["wx"] + ROOM_W / 2  # origin 兜底客厅
