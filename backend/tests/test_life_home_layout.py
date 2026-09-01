# -*- coding: utf-8 -*-
"""小家 v3.2 家具自由摆放后端测试（2026-08-18）。

- 迁移幂等：life_states.home_layout_json 列（临时库 init_db 跑两次，不触碰 backend/data）
- GET /state：默认布局 / 自定义布局生效 / 非法 JSON 回退默认
- PUT /layout：保存成功 / 校验拒绝（未知房间、未知家具 key、坐标越界、尺寸越界、
  家具超量、JSON 超限）/ 归属 404（非 owner 非主账号）/ 主账号放行 / 部分房间保存
"""
import asyncio
import json
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api.life_home import ROOMS_LAYOUT, router as life_home_router
from app.auth.deps import get_current_user_id
from app.models.character import AICharacter
from app.models.life import LifeState
from app.models.user import User

OWNER = 100      # 角色 owner（非主账号）
ADMIN = 1        # 默认 ADMIN_USER_IDS=[1]
OTHER = 200      # 无关用户


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(life_home_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _valid_payload(**over) -> dict:
    """合法请求体：4 房间全量家具（与默认一致的位置，沙发改为小数坐标）"""
    rooms = []
    for rid, r in ROOMS_LAYOUT.items():
        furn = []
        for f in r["furniture"]:
            item = {"key": f["key"], "name": f["name"], "gx": f["gx"], "gy": f["gy"],
                    "gw": f["gw"], "gh": f["gh"], "action": f["action"]}
            if rid == "living" and f["key"] == "sofa":
                item["gx"] = 3.5
                item["gy"] = 2.25
            furn.append(item)
        rooms.append({"id": rid, "name": r["name"], "furniture": furn, "doors": r["doors"]})
    payload = {"character_id": 1, "rooms": rooms}
    payload.update(over)
    return payload


def _living_sofa(rooms: list) -> dict:
    living = next(r for r in rooms if r["id"] == "living")
    return next(f for f in living["furniture"] if f["key"] == "sofa")


@pytest.fixture()
def home_db(monkeypatch):
    """临时 SQLite 文件库：monkeypatch life_home.async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix="life_home_layout_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        from app.models.character import AICharacter
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(AICharacter(id=1, user_id=OWNER, name="小爱", is_active=True))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr("app.api.life_home.async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _read_st(factory) -> LifeState | None:
    async def _f():
        async with factory() as db:
            return (await db.execute(
                select(LifeState).where(LifeState.character_id == 1)
            )).scalar_one_or_none()
    return asyncio.run(_f())


# ---------------- 迁移幂等 ----------------

def test_init_db_布局列迁移幂等(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="life_home_migrate_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    import app.db.database as dbmod
    monkeypatch.setattr(dbmod, "engine", engine)
    import app.db.init_db as _initdb_mod  # F1 拆分：init_db 用实现模块的 engine 绑定
    monkeypatch.setattr(_initdb_mod, "engine", engine)

    async def _cols():
        async with engine.begin() as conn:
            rows = (await conn.execute(sa_text("PRAGMA table_info(life_states)"))).fetchall()
        return [c[1] for c in rows]

    async def _run():
        await dbmod.init_db()
        return await _cols()

    cols1 = asyncio.run(_run())
    assert "home_layout_json" in cols1
    assert cols1.count("home_layout_json") == 1

    cols2 = asyncio.run(_run())  # 幂等：再跑一次不报错、不重复加列
    assert cols2 == cols1
    assert cols2.count("home_layout_json") == 1

    asyncio.run(engine.dispose())


# ---------------- GET /state ----------------

def test_get_state_默认布局(home_db):
    client = _make_client(OWNER)
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    data = r.json()
    assert data["character_id"] == 1
    rooms = data["rooms"]
    assert [room["id"] for room in rooms] == list(ROOMS_LAYOUT.keys())
    for rid, default in ROOMS_LAYOUT.items():
        room = next(x for x in rooms if x["id"] == rid)
        assert room["name"] == default["name"]
        assert room["doors"] == default["doors"]
        # 无自定义 → 家具位置与默认一致
        assert [f["key"] for f in room["furniture"]] == [f["key"] for f in default["furniture"]]
        assert _living_sofa(rooms)["gx"] == 6
        assert _living_sofa(rooms)["gy"] == 1


def test_put_then_get_自定义布局生效(home_db):
    client = _make_client(OWNER)
    r = client.put("/api/v1/life-home/layout", json=_valid_payload())
    assert r.status_code == 200
    assert r.json() == {"saved": True}

    st = _read_st(home_db)
    assert st is not None and st.home_layout_json is not None
    stored = json.loads(st.home_layout_json)
    assert set(stored.keys()) == set(ROOMS_LAYOUT.keys())
    living_furn = {f["key"]: f for f in stored["living"]["furniture"]}
    assert living_furn["sofa"]["gx"] == 3.5

    r2 = client.get("/api/v1/life-home/state")
    assert r2.status_code == 200
    sofa = _living_sofa(r2.json()["rooms"])
    assert sofa["gx"] == 3.5
    assert sofa["gy"] == 2.25
    # 未拖动家具保持默认（合并语义）；name/action 来自默认
    assert sofa["name"] == "沙发"


def test_put_部分房间_其余保持默认(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"] = [r for r in payload["rooms"] if r["id"] == "living"]
    assert client.put("/api/v1/life-home/layout", json=payload).status_code == 200
    r = client.get("/api/v1/life-home/state")
    rooms = r.json()["rooms"]
    assert _living_sofa(rooms)["gx"] == 3.5       # living 自定义生效
    bedroom = next(x for x in rooms if x["id"] == "bedroom")
    bed = next(f for f in bedroom["furniture"] if f["key"] == "bed")
    assert bed["gx"] == 1 and bed["gy"] == 1       # bedroom 未保存 → 默认


def test_get_state_非法JSON回退默认(home_db):
    async def _seed_bad():
        async with home_db() as db:
            db.add(LifeState(character_id=1, home_layout_json='{"living": not-json'))
            await db.commit()
    asyncio.run(_seed_bad())

    client = _make_client(OWNER)
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    assert _living_sofa(r.json()["rooms"])["gx"] == 6  # 回退默认


def test_get_state_结构非法回退默认(home_db):
    # JSON 合法但结构不对（房间不是 dict / 家具越界）→ 相应项丢弃回退默认
    async def _seed_bad():
        async with home_db() as db:
            db.add(LifeState(character_id=1, home_layout_json=json.dumps({
                "living": {"furniture": [{"key": "sofa", "gx": 99, "gy": 1, "gw": 1, "gh": 1}]},
                "bedroom": "not-a-room",
            })))
            await db.commit()
    asyncio.run(_seed_bad())

    client = _make_client(OWNER)
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    rooms = r.json()["rooms"]
    assert _living_sofa(rooms)["gx"] == 6   # sofa 越界被丢弃 → 默认
    bedroom = next(x for x in rooms if x["id"] == "bedroom")
    assert next(f for f in bedroom["furniture"] if f["key"] == "bed")["gx"] == 1


# ---------------- PUT /layout 校验 ----------------

def test_put_未知房间400(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["id"] = "garage"
    r = client.put("/api/v1/life-home/layout", json=payload)
    assert r.status_code == 400


def test_put_未知家具key400(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["furniture"].append(
        {"key": "hot_tub", "name": "按摩浴缸", "gx": 1, "gy": 1, "gw": 1, "gh": 1, "action": None})
    r = client.put("/api/v1/life-home/layout", json=payload)
    assert r.status_code == 400
    assert client.get("/api/v1/life-home/state").json()["rooms"]  # 未污染


def test_put_坐标越界400(home_db):
    client = _make_client(OWNER)
    for bad in (17, -1):
        payload = _valid_payload()
        payload["rooms"][0]["furniture"][0]["gx"] = bad
        assert client.put("/api/v1/life-home/layout", json=payload).status_code == 400, f"gx={bad}"


def test_put_尺寸越界400(home_db):
    client = _make_client(OWNER)
    for kw in ({"gw": 0.1}, {"gw": 5.0}, {"gh": 0.4}, {"gh": 4.5}):
        payload = _valid_payload()
        payload["rooms"][0]["furniture"][0].update(kw)
        assert client.put("/api/v1/life-home/layout", json=payload).status_code == 400, kw


def test_put_坐标非数字400(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["furniture"][0]["gx"] = "3.5"
    assert client.put("/api/v1/life-home/layout", json=payload).status_code == 400


def test_put_小数坐标合法(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["furniture"][0]["gx"] = 3.75  # 自由摆放：允许小数
    r = client.put("/api/v1/life-home/layout", json=payload)
    assert r.status_code == 200
    st = _read_st(home_db)
    stored = json.loads(st.home_layout_json)
    assert stored["living"]["furniture"][0]["gx"] == 3.75


def test_put_家具超量400(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["furniture"] = [
        dict(payload["rooms"][0]["furniture"][0], key="sofa", gx=i * 0.5, gy=1)
        for i in range(31)
    ]
    r = client.put("/api/v1/life-home/layout", json=payload)
    assert r.status_code == 400


def test_put_json超限400(home_db):
    client = _make_client(OWNER)
    payload = _valid_payload()
    payload["rooms"][0]["furniture"][0]["name"] = "x" * (60 * 1024)
    r = client.put("/api/v1/life-home/layout", json=payload)
    assert r.status_code == 400


def test_put_rooms缺失400(home_db):
    client = _make_client(OWNER)
    assert client.put("/api/v1/life-home/layout", json={"character_id": 1}).status_code == 400
    assert client.put("/api/v1/life-home/layout", json={"character_id": 1, "rooms": []}).status_code == 400


# ---------------- PUT /layout rotation（v3.3 家具朝向） ----------------

def test_put_rotation默认0(home_db):
    """未传 rotation → 保存后 GET 透传默认 0"""
    client = _make_client(OWNER)
    assert client.put("/api/v1/life-home/layout", json=_valid_payload()).status_code == 200
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    for room in r.json()["rooms"]:
        for f in room["furniture"]:
            assert f["rotation"] == 0


def test_put_rotation合法0_7(home_db):
    """rotation 0-7 合法：保存并 GET 透传；未保存房间保持默认 0"""
    client = _make_client(OWNER)
    for rot in (0, 1, 3, 7):
        payload = _valid_payload()
        payload["rooms"][0]["furniture"][0]["rotation"] = rot
        assert client.put("/api/v1/life-home/layout", json=payload).status_code == 200, f"rot={rot}"
        r = client.get("/api/v1/life-home/state")
        assert _living_sofa(r.json()["rooms"])["rotation"] == rot, f"rot={rot}"
    # 只保存 living → bedroom 未保存，家具 rotation 默认 0
    payload = _valid_payload()
    payload["rooms"] = [r for r in payload["rooms"] if r["id"] == "living"]
    assert client.put("/api/v1/life-home/layout", json=payload).status_code == 200
    r = client.get("/api/v1/life-home/state")
    bedroom = next(x for x in r.json()["rooms"] if x["id"] == "bedroom")
    assert all(f["rotation"] == 0 for f in bedroom["furniture"])


def test_put_rotation越界400(home_db):
    client = _make_client(OWNER)
    for bad in (-1, 8):
        payload = _valid_payload()
        payload["rooms"][0]["furniture"][0]["rotation"] = bad
        r = client.put("/api/v1/life-home/layout", json=payload)
        assert r.status_code == 400, f"rot={bad}"
        assert client.get("/api/v1/life-home/state").json()["rooms"]  # 未污染


def test_put_rotation非整数400(home_db):
    """字符串 / bool / 浮点均拒绝（bool 是 int 子类需排除）"""
    client = _make_client(OWNER)
    for bad in ("3", True, 3.0):
        payload = _valid_payload()
        payload["rooms"][0]["furniture"][0]["rotation"] = bad
        r = client.put("/api/v1/life-home/layout", json=payload)
        assert r.status_code == 400, f"rot={bad!r}"
        assert client.get("/api/v1/life-home/state").json()["rooms"]  # 未污染


def test_get_state_rotation字段存在默认0(home_db):
    """GET 默认布局也带 rotation 字段（前端 fromMap 兜底 0）"""
    client = _make_client(OWNER)
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    for room in r.json()["rooms"]:
        for f in room["furniture"]:
            assert "rotation" in f
            assert f["rotation"] == 0


# ---------------- GET /state 标题数据（v3.3 ① 用户昵称 + 恋人） ----------------

def test_get_state_用户昵称与恋人(home_db):
    """state 返回用户昵称；is_partner 角色 → lover_name=角色名（标题「昵称与恋人的小家」）"""
    async def _seed():
        async with home_db() as db:
            db.add(User(id=OWNER, username="owner", nickname="小明"))
            char = await db.get(AICharacter, 1)
            char.is_partner = True
            char.relation_type = "对象/伴侣"
            await db.commit()
    asyncio.run(_seed())

    client = _make_client(OWNER)
    r = client.get("/api/v1/life-home/state")
    assert r.status_code == 200
    data = r.json()
    assert data["user"] == {"id": OWNER, "nickname": "小明"}
    assert data["lover_name"] == "小爱"


def test_get_state_恋人判定关键词兜底(home_db):
    """is_partner=False 但 relation_type 含「恋人/对象」关键词 → lover_name 兜底；无关关系 → None"""
    async def _seed(rt: str, partner: bool):
        async with home_db() as db:
            char = await db.get(AICharacter, 1)
            char.relation_type = rt
            char.is_partner = partner
            await db.commit()
    asyncio.run(_seed("恋人", False))
    client = _make_client(OWNER)
    assert client.get("/api/v1/life-home/state").json()["lover_name"] == "小爱"

    asyncio.run(_seed("朋友", False))
    assert client.get("/api/v1/life-home/state").json()["lover_name"] is None


# ---------------- PUT /layout 归属 ----------------

def test_put_他人角色404(home_db):
    client = _make_client(OTHER)  # 非 owner、非主账号
    r = client.put("/api/v1/life-home/layout", json=_valid_payload())
    assert r.status_code == 404


def test_put_主账号放行(home_db):
    client = _make_client(ADMIN)  # 主账号（ADMIN_USER_IDS 默认 [1]）
    r = client.put("/api/v1/life-home/layout", json=_valid_payload())
    assert r.status_code == 200
    assert r.json() == {"saved": True}


def test_get_他人角色404(home_db):
    client = _make_client(OTHER)
    assert client.get("/api/v1/life-home/state").status_code == 404
