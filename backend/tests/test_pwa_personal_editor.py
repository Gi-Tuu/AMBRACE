# -*- coding: utf-8 -*-
"""PWA M1a-3 个人内容编辑器 · **真实路由契约**（沙箱集成，2026-10-17）。

口径：用**真实** auth / user_content 路由 + 真实 SQLAlchemy 会话（conftest 已把
DATABASE_URL / 认证密钥指向本会话临时沙箱），跑个人备忘 / 个人日记的 CRUD、边界与
家庭共享可见性。**不 import app.main**（单实例锁），照 test_pwa_reader_contract.py 的
「独立 FastAPI 实例」策略。

本单覆盖的后端改动（读代码确认）：``user_content._memo_json`` 与 ``_diary_json`` **只追加**
``user_id`` 字段，其余响应字段不变。本文件同时钉死该新增字段的准确性与既有行为不变。

关键口径（读实现确认，逐字节对齐）：
- 读列表走 ``tenant_scope_ids``（家庭模式=家庭成员）→ 家庭内共享可见、跨家庭隔离；
- 写/改/删的归属校验用同一份租户白名单 → 跨家庭 404；**写行归属恒为 actor**
  （主编 upsert 只命中 ``user_id == actor`` 的那一天那一行）；
- 截断：备忘 title ``[:100]`` / content ``[:2000]``；日记 content ``[:5000]``；
- 空正文（strip 后为空）→ 400；坏日期 → 400。

造数约定（防踩坑）：
- 每个用例各自种一套「甲家（主 + 子，同家庭两账号）、乙家（主账号）」（函数级沙箱，
  账号名带自增序号确保唯一）；备忘/日记预置固定 ``updated_at=2020``，让「行是否被改动」
  可被逐字段检出；
- 对账不用「行数没变」代替：取**前后全字段快照 + 预期写集合**，逐字节比对。
"""
from __future__ import annotations

import asyncio
import itertools
from datetime import datetime

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.user_content import router as user_content_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.life import UserDiary, UserMemo
from app.models.user import User
from app.utils.errors import register_exception_handlers

PASSWORD = "PwaEditor123"
_seq = itertools.count(1)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(user_content_router)
    return app


async def _mk_user(db, nickname: str, *, is_admin: bool, parent_id: int | None = None) -> User:
    user = User(
        username=f"pwa_pe_{next(_seq):03d}",
        nickname=nickname,
        password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
        is_admin=is_admin,
        parent_id=parent_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_scene() -> dict:
    """甲家（a1 主 + a2 子）、乙家（b1 主）；各预置备忘/日记（含同日不同账号两条）。"""
    s: dict = {}
    async with async_session_factory() as db:
        a1 = await _mk_user(db, "编辑甲主", is_admin=True)
        a2 = await _mk_user(db, "编辑甲子", is_admin=False, parent_id=a1.id)
        b1 = await _mk_user(db, "编辑乙主", is_admin=True)
        await db.flush()
        s.update(ua1=a1.id, ua2=a2.id, ub1=b1.id,
                 a1_username=a1.username, a2_username=a2.username, b1_username=b1.username)

        ma1 = UserMemo(user_id=a1.id, title="A1-标题", content="A1-备忘正文",
                       updated_at=datetime(2020, 1, 1, 10))
        ma2 = UserMemo(user_id=a2.id, title="A2-标题", content="A2-备忘正文",
                       updated_at=datetime(2020, 1, 2, 10))
        mb = UserMemo(user_id=b1.id, title="B-标题", content="B-备忘正文",
                      updated_at=datetime(2020, 1, 3, 10))
        da1 = UserDiary(user_id=a1.id, diary_date="2026-05-01", content="A1-日记",
                        updated_at=datetime(2020, 2, 1, 10))
        da2 = UserDiary(user_id=a2.id, diary_date="2026-05-01", content="A2-日记",
                        updated_at=datetime(2020, 2, 2, 10))
        db1 = UserDiary(user_id=b1.id, diary_date="2026-06-01", content="B-日记",
                        updated_at=datetime(2020, 2, 3, 10))
        db.add_all([ma1, ma2, mb, da1, da2, db1])
        await db.flush()
        s.update(ma1=ma1.id, ma2=ma2.id, mb=mb.id, da1=da1.id, da2=da2.id, db1=db1.id)
        await db.commit()
    return s


# ── DB 探测器 ────────────────────────────────────────────────────────────────
def _row_json(m, kind: str) -> dict:
    base = {
        "user_id": m.user_id, "content": m.content,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }
    if kind == "memo":
        base["title"] = m.title
    else:
        base["diary_date"] = m.diary_date
    return base


async def _memo_row(memo_id: int) -> dict | None:
    async with async_session_factory() as db:
        m = await db.get(UserMemo, memo_id)
        return None if m is None else _row_json(m, "memo")


async def _diary_row(diary_id: int) -> dict | None:
    async with async_session_factory() as db:
        d = await db.get(UserDiary, diary_id)
        return None if d is None else _row_json(d, "diary")


async def _memo_snapshot(user_ids: list[int]) -> dict[int, dict]:
    async with async_session_factory() as db:
        rows = (await db.execute(select(UserMemo).where(UserMemo.user_id.in_(user_ids)))).scalars().all()
        return {m.id: _row_json(m, "memo") for m in rows}


async def _diary_snapshot(user_ids: list[int]) -> dict[int, dict]:
    async with async_session_factory() as db:
        rows = (await db.execute(select(UserDiary).where(UserDiary.user_id.in_(user_ids)))).scalars().all()
        return {d.id: _row_json(d, "diary") for d in rows}


async def _diary_ids(user_id: int, diary_date: str) -> list[int]:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(UserDiary.id).where(
                UserDiary.user_id == user_id, UserDiary.diary_date == diary_date
            )
        )).scalars().all()
        return [int(i) for i in rows]


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def scene():
    return asyncio.run(_seed_scene())


@pytest.fixture(scope="module")
def client():
    return TestClient(_app())


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── 1. 备忘 CRUD 真实落库 ────────────────────────────────────────────────────
def test_备忘CRUD真实落库(client, scene):
    auth = _login(client, scene["a1_username"])

    r = client.post("/api/v1/user/memos", json={"title": "采购单", "content": "牛奶鸡蛋"}, headers=auth)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["user_id"] == scene["ua1"] and b["title"] == "采购单" and b["content"] == "牛奶鸡蛋"
    mid = b["id"]
    assert asyncio.run(_memo_row(mid))["content"] == "牛奶鸡蛋"

    lst = client.get("/api/v1/user/memos", headers=auth).json()
    assert set(lst.keys()) == {"memos", "total"}
    assert mid in {m["id"] for m in lst["memos"]}

    up = client.put(f"/api/v1/user/memos/{mid}", json={"title": "采购单2", "content": "牛奶鸡蛋面包"}, headers=auth)
    assert up.status_code == 200, up.text
    assert up.json()["title"] == "采购单2" and up.json()["content"] == "牛奶鸡蛋面包"
    assert asyncio.run(_memo_row(mid))["content"] == "牛奶鸡蛋面包"

    de = client.delete(f"/api/v1/user/memos/{mid}", headers=auth)
    assert de.status_code == 200, de.text
    assert de.json() == {"status": "ok"}
    assert asyncio.run(_memo_row(mid)) is None
    assert mid not in {m["id"] for m in client.get("/api/v1/user/memos", headers=auth).json()["memos"]}


# ── 2. 日记 CRUD 与同账号同日 upsert 真实落库 ────────────────────────────────
def test_日记CRUD与upsert真实落库(client, scene):
    auth = _login(client, scene["a1_username"])
    date = "2026-07-15"

    r = client.post("/api/v1/user/diaries", json={"diary_date": date, "content": "日记-1"}, headers=auth)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["user_id"] == scene["ua1"] and b["diary_date"] == date and b["content"] == "日记-1"
    did = b["id"]

    # 同 (账号, 日期) 再写 → upsert 命中同一行（不新建第二行）
    again = client.post("/api/v1/user/diaries", json={"diary_date": date, "content": "日记-2"}, headers=auth)
    assert again.status_code == 201, again.text
    assert again.json()["id"] == did, "upsert 未命中本人同日行"
    assert again.json()["content"] == "日记-2"
    assert asyncio.run(_diary_row(did))["content"] == "日记-2"
    assert asyncio.run(_diary_ids(scene["ua1"], date)) == [did]

    one = client.get(f"/api/v1/user/diaries/{date}", headers=auth)
    assert one.status_code == 200, one.text
    assert one.json()["content"] == "日记-2" and one.json()["user_id"] == scene["ua1"]

    de = client.delete(f"/api/v1/user/diaries/{did}", headers=auth)
    assert de.status_code == 200, de.text
    assert asyncio.run(_diary_row(did)) is None


# ── 3. 边界：空正文 / 坏日期 / 超长截断（如实断言既有实现口径）──────────────
def test_空正文坏日期与超长截断(client, scene):
    auth = _login(client, scene["a1_username"])

    assert client.post("/api/v1/user/memos", json={"content": ""}, headers=auth).status_code == 400
    assert client.post("/api/v1/user/memos", json={"content": "   "}, headers=auth).status_code == 400

    long = client.post("/api/v1/user/memos", json={"title": "T" * 150, "content": "C" * 2500}, headers=auth)
    assert long.status_code == 201, long.text
    row = asyncio.run(_memo_row(long.json()["id"]))
    assert len(row["title"]) == 100 and len(row["content"]) == 2000  # 如实截断，非报错

    assert client.put(f"/api/v1/user/memos/{long.json()['id']}", json={"content": "  "},
                      headers=auth).status_code == 400

    assert client.post("/api/v1/user/diaries", json={"diary_date": "2026-13-99", "content": "x"},
                       headers=auth).status_code == 400
    assert client.post("/api/v1/user/diaries", json={"diary_date": "not-a-date", "content": "x"},
                       headers=auth).status_code == 400
    assert client.post("/api/v1/user/diaries", json={"diary_date": "2026-07-20", "content": ""},
                       headers=auth).status_code == 400

    big = client.post("/api/v1/user/diaries", json={"diary_date": "2026-07-21", "content": "Y" * 6000},
                      headers=auth)
    assert big.status_code == 201, big.text
    assert len(asyncio.run(_diary_row(big.json()["id"]))["content"]) == 5000  # 如实截断

    assert client.get("/api/v1/user/diaries/2026-13-99", headers=auth).status_code == 400


# ── 4. 未认证：全部端点一律 401 ──────────────────────────────────────────────
def test_未认证一律401(client, scene):
    cases = [
        ("get", "/api/v1/user/memos", None),
        ("post", "/api/v1/user/memos", {"content": "x"}),
        ("get", "/api/v1/user/diaries", None),
        ("get", "/api/v1/user/diaries/2026-05-01", None),
        ("post", "/api/v1/user/diaries", {"diary_date": "2026-05-01", "content": "x"}),
        ("put", f"/api/v1/user/memos/{scene['ma1']}", {"content": "x"}),
        ("delete", f"/api/v1/user/memos/{scene['ma1']}", None),
        ("delete", f"/api/v1/user/diaries/{scene['da1']}", None),
    ]
    for method, path, body in cases:
        kwargs = {} if body is None else {"json": body}
        r = getattr(client, method)(path, **kwargs)
        assert r.status_code == 401, (method, path, r.status_code, r.text)


# ── 5. 家庭共享只读展示 + 跨家庭 404 ─────────────────────────────────────────
def test_家庭共享只读展示与跨家庭404(client, scene):
    auth_a1 = _login(client, scene["a1_username"])
    auth_a2 = _login(client, scene["a2_username"])
    auth_b = _login(client, scene["b1_username"])

    a1_memos = client.get("/api/v1/user/memos", headers=auth_a1).json()
    assert set(a1_memos.keys()) == {"memos", "total"}
    assert {m["id"] for m in a1_memos["memos"]} == {scene["ma1"], scene["ma2"]}  # 家庭共享可见
    assert scene["mb"] not in {m["id"] for m in a1_memos["memos"]}               # 跨家庭不可见
    assert {m["id"]: m["user_id"] for m in a1_memos["memos"]} == {
        scene["ma1"]: scene["ua1"], scene["ma2"]: scene["ua2"],
    }

    a2_memos = client.get("/api/v1/user/memos", headers=auth_a2).json()
    assert {m["id"] for m in a2_memos["memos"]} == {scene["ma1"], scene["ma2"]}

    b_memos = client.get("/api/v1/user/memos", headers=auth_b).json()
    assert {m["id"] for m in b_memos["memos"]} == {scene["mb"]}

    a1_diaries = client.get("/api/v1/user/diaries", headers=auth_a1).json()
    assert {d["id"] for d in a1_diaries["diaries"]} == {scene["da1"], scene["da2"]}
    assert scene["db1"] not in {d["id"] for d in a1_diaries["diaries"]}

    # 单日详情：只查乙家有的日期 → 404；查家庭共享日期 → 返家庭内某行（不越家庭）
    assert client.get("/api/v1/user/diaries/2026-06-01", headers=auth_a1).status_code == 404
    fam = client.get("/api/v1/user/diaries/2026-05-01", headers=auth_a1)
    assert fam.status_code == 200, fam.text
    assert fam.json()["user_id"] in {scene["ua1"], scene["ua2"]}

    # 跨家庭写/删 → 404（与「不存在」同观感）
    assert client.put(f"/api/v1/user/memos/{scene['mb']}", json={"content": "x"},
                      headers=auth_a1).status_code == 404
    assert client.delete(f"/api/v1/user/memos/{scene['mb']}", headers=auth_a1).status_code == 404
    assert client.delete(f"/api/v1/user/diaries/{scene['db1']}", headers=auth_a1).status_code == 404


# ── 6. 新增字段 user_id 准确（列表 + 单条 + 写入回包）───────────────────────
def test_新增user_id字段准确(client, scene):
    auth = _login(client, scene["a1_username"])

    memos = client.get("/api/v1/user/memos", headers=auth).json()["memos"]
    for m in memos:
        assert "user_id" in m
    assert {m["id"]: m["user_id"] for m in memos} == {
        scene["ma1"]: scene["ua1"], scene["ma2"]: scene["ua2"],
    }

    diaries = client.get("/api/v1/user/diaries", headers=auth).json()["diaries"]
    for d in diaries:
        assert "user_id" in d
    assert {d["id"]: d["user_id"] for d in diaries} == {
        scene["da1"]: scene["ua1"], scene["da2"]: scene["ua2"],
    }

    single = client.get("/api/v1/user/diaries/2026-05-01", headers=auth)
    assert single.status_code == 200 and single.json()["user_id"] in {scene["ua1"], scene["ua2"]}

    pm = client.post("/api/v1/user/memos", json={"content": "写一条备忘"}, headers=auth)
    assert pm.json()["user_id"] == scene["ua1"]
    pd = client.post("/api/v1/user/diaries", json={"diary_date": "2026-08-08", "content": "写一条日记"},
                     headers=auth)
    assert pd.json()["user_id"] == scene["ua1"]


# ── 7. 本人编辑只作用本人行 / 不改家人同日行（前后快照 + 写集合对账）─────────
def test_本人编辑只作用本人行且不改家人同日行(client, scene):
    auth = _login(client, scene["a1_username"])
    before_m = asyncio.run(_memo_snapshot([scene["ua1"], scene["ua2"]]))
    before_d = asyncio.run(_diary_snapshot([scene["ua1"], scene["ua2"]]))

    up = client.put(f"/api/v1/user/memos/{scene['ma1']}", json={"content": "A1-新正文"}, headers=auth)
    assert up.status_code == 200, up.text
    dr = client.post("/api/v1/user/diaries", json={"diary_date": "2026-05-01", "content": "A1-新日记"},
                     headers=auth)
    assert dr.status_code == 201, dr.text
    assert dr.json()["id"] == scene["da1"], "主编 upsert 未命中本人同日行"

    after_m = asyncio.run(_memo_snapshot([scene["ua1"], scene["ua2"]]))
    after_d = asyncio.run(_diary_snapshot([scene["ua1"], scene["ua2"]]))

    # 家人行逐字段不变（content / updated_at / created_at 全等）
    assert after_m[scene["ma2"]] == before_m[scene["ma2"]], "本人改备忘影响到了家人行"
    assert after_d[scene["da2"]] == before_d[scene["da2"]], "本人写日记影响到了家人同日行"

    # 本人行确已改
    assert after_m[scene["ma1"]]["content"] == "A1-新正文"
    assert after_d[scene["da1"]]["content"] == "A1-新日记"

    # 写集合对账（逐字段差异，非「行数没变」）：只有本人行发生变化
    assert {k for k in before_m if after_m[k] != before_m[k]} == {scene["ma1"]}
    assert {k for k in before_d if after_d[k] != before_d[k]} == {scene["da1"]}
