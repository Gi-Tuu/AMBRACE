# -*- coding: utf-8 -*-
"""事实「修正历史」只读接口测试（小增量 2026-09-16）

覆盖：
- 读层 ``get_fact_history``：有历史（当前值 + 含 superseded 旧版的版本链）/ 无历史（空槽）/
  用户隔离 / 版本上限截断（``truncated``）；
- 服务层 ``get_world_fact_history``：属主校验（他人 fact_id → 404）、正常返回；
- HTTP 层 ``GET /{character_id}/world-facts/{fact_id}/history``：有历史 / 无历史（仅一版）/
  无权限（越权 / 不存在 → 404）三类。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库，不触碰 backend/data。
 内部查询走 async_session_factory，故需同时 patch application.characters 与 events.facts 的模块引用，
 与 test_world_facts_admin.py 同范式。）
"""
import asyncio
import os

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import characters as characters_api
from app.application import characters as svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.events import facts as facts_mod
from app.events.facts import get_fact_history
from app.models.character import AICharacter
from app.models.memory import WorldFact

pytestmark = pytest.mark.slow

_CHAR_ID = 7
_OTHER_CHAR_ID = 8
_USER_ID = 1
_OTHER_USER_ID = 999


@pytest.fixture()
def wf_db(monkeypatch, tmp_path):
    """临时库：建全表 + 种子两个角色（本人 / 他人）；session factory 指向临时库。"""
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add(AICharacter(id=_CHAR_ID, user_id=_USER_ID, name="测试角色"))
            db.add(AICharacter(id=_OTHER_CHAR_ID, user_id=_OTHER_USER_ID, name="别人的角色"))
            await db.commit()

    asyncio.run(_init())
    # facts.py 在模块顶层绑定 async_session_factory，需同步 patch 其模块名
    monkeypatch.setattr(svc, "async_session_factory", factory)
    monkeypatch.setattr(facts_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _make_client(factory, user_id=_USER_ID) -> TestClient:
    """最小 app（只挂 characters 路由）+ 依赖覆盖（登录态 / 临时库 session）。"""
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


async def _seed_fact(factory, *, user_id=_USER_ID, character_id=_CHAR_ID, predicate="setting",
                     object_value, status="active", author="system", superseded_at=None):
    async with factory() as db:
        f = WorldFact(
            user_id=user_id, character_id=character_id,
            subject_type="character", subject_id=character_id,
            predicate=predicate, object_value=object_value,
            status=status, author=author,
        )
        if superseded_at is not None:
            f.superseded_at = superseded_at
        db.add(f)
        await db.commit()
        await db.refresh(f)
        return f.id


def _seed(factory, **kw) -> int:
    return asyncio.run(_seed_fact(factory, **kw))


def _last_id(factory, *, status="active", user_id=_USER_ID) -> int:
    async def _q():
        async with factory() as db:
            rows = (await db.execute(
                select(WorldFact.id).where(
                    WorldFact.user_id == user_id, WorldFact.status == status
                ).order_by(WorldFact.id.desc())
            )).scalars().all()
            return rows[0]
    return asyncio.run(_q())


def _link_superseded(factory, old_id: int, new_id: int) -> None:
    """补链：把旧版标为被新版取代（复刻 assert_fact 的写入口径），供取代链断言用。"""
    from app.utils.timeutil import now_naive_utc

    async def _run():
        async with factory() as db:
            f = await db.get(WorldFact, old_id)
            f.superseded_by = new_id
            f.superseded_at = now_naive_utc()
            await db.commit()

    asyncio.run(_run())


def _history(**kw):
    return asyncio.run(get_fact_history(
        character_id=kw.pop("character_id", _CHAR_ID),
        user_id=kw.pop("user_id", _USER_ID),
        subject_type="character", subject_id=kw.pop("subject_id", _CHAR_ID),
        predicate=kw.pop("predicate", "setting"), **kw,
    ))


# ─────────────────────────── 读层：有历史 / 无历史 ───────────────────────────

def test_history_with_versions(wf_db):
    """同一槽先后两条：旧版 superseded、新版 active → 版本链含两版，current=新版。"""
    old_id = _seed(wf_db, object_value="我住在杭州", status="superseded")
    new_id = _seed(wf_db, object_value="我搬到了上海", status="active", author="user")
    _link_superseded(wf_db, old_id, new_id)

    out = _history()

    assert out["truncated"] is False
    assert out["current"] is not None
    assert out["current"]["id"] == new_id
    assert out["current"]["status"] == "active"
    assert out["current"]["object_value"] == "我搬到了上海"
    ids = [v["id"] for v in out["versions"]]
    assert set(ids) == {new_id, old_id}
    assert ids[0] == new_id                              # 倒序：当前值在首
    superseded = [v for v in out["versions"] if v["id"] == old_id][0]
    assert superseded["status"] == "superseded"
    assert superseded["object_value"] == "我住在杭州"
    assert superseded["superseded_by"] == new_id          # 取代链链接保留（只读暴露）
    assert superseded["superseded_at"]                     # 取代时间非空
    assert superseded["author"] == "system"
    assert superseded["asserted_at"]                       # 时间戳非空


def test_history_empty(wf_db):
    """该槽无任何行 → current=None、versions=[]（前端显示「还没被修改过」）。"""
    out = _history(predicate="never_set")
    assert out["current"] is None
    assert out["versions"] == []
    assert out["truncated"] is False


def test_history_user_isolation(wf_db):
    """用户隔离：同槽的行属于他人 → 本用户读不到。"""
    _seed(wf_db, user_id=_OTHER_USER_ID, character_id=_OTHER_CHAR_ID, object_value="我住在杭州")
    out = _history()
    assert out["current"] is None
    assert out["versions"] == []


def test_history_truncated_at_limit(wf_db):
    """版本数超上限：只回最近 N 版 + truncated=True，当前值仍在（最新版）。"""
    ids = [_seed(wf_db, object_value=f"第 {i} 版", status="superseded") for i in range(3)]
    top_id = _seed(wf_db, object_value="最新版", status="active")

    out = _history(limit=2)

    assert out["truncated"] is True
    assert len(out["versions"]) == 2
    assert out["versions"][0]["id"] == top_id             # 倒序首条＝当前值
    assert out["current"]["id"] == top_id
    assert ids[0] not in [v["id"] for v in out["versions"]]  # 最旧一版被截掉


# ─────────────────────────── 服务层：404 / 正常 ───────────────────────────

def test_service_history_404_for_other_user_fact(wf_db):
    """他人 fact_id → 404（与既有世界事实接口同口径，不泄露存在性）。"""
    fid = _seed(wf_db, user_id=_OTHER_USER_ID, character_id=_OTHER_CHAR_ID, object_value="我住在杭州")

    async def _run():
        async with wf_db() as session:
            return await svc.get_world_fact_history(
                db=session, character_id=_CHAR_ID, fact_id=fid, user_id=_USER_ID, lang="zh",
            )

    with pytest.raises(HTTPException) as ei:
        asyncio.run(_run())
    assert ei.value.status_code == 404


def test_service_history_ok(wf_db):
    """本角色本用户的 fact_id → 返回该槽当前值 + 全版本链。"""
    _seed(wf_db, object_value="我住在杭州", status="superseded")
    _seed(wf_db, object_value="我搬到了上海", status="active")
    fid = _last_id(wf_db)

    async def _run():
        async with wf_db() as session:
            return await svc.get_world_fact_history(
                db=session, character_id=_CHAR_ID, fact_id=fid, user_id=_USER_ID, lang="zh",
            )

    out = asyncio.run(_run())
    assert out["current"] is not None
    assert out["current"]["object_value"] == "我搬到了上海"
    assert len(out["versions"]) == 2
    assert out["truncated"] is False


# ─────────────────────────── HTTP 层：有历史 / 无历史 / 无权限 ───────────────────────────

def test_api_history_with_versions(wf_db):
    old_id = _seed(wf_db, object_value="我住在杭州", status="superseded")
    new_id = _seed(wf_db, object_value="我搬到了上海", status="active", author="user")
    r = _make_client(wf_db).get(f"/api/v1/characters/{_CHAR_ID}/world-facts/{new_id}/history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"]["id"] == new_id
    assert body["current"]["object_value"] == "我搬到了上海"
    assert {v["id"] for v in body["versions"]} == {old_id, new_id}
    assert body["truncated"] is False


def test_api_history_never_modified(wf_db):
    """无历史（从未改过）：只有当前这一版。"""
    fid = _seed(wf_db, object_value="我住在杭州", status="active")
    r = _make_client(wf_db).get(f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}/history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["versions"]) == 1
    assert body["current"]["id"] == fid
    assert body["truncated"] is False


def test_api_history_404_other_user_fact(wf_db):
    """无权限：他人的 fact_id → 404（既不返回内容也不泄露存在性）。"""
    fid = _seed(wf_db, user_id=_OTHER_USER_ID, character_id=_OTHER_CHAR_ID, object_value="我住在杭州")
    c = _make_client(wf_db)
    assert c.get(f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}/history").status_code == 404
    assert c.get(f"/api/v1/characters/{_OTHER_CHAR_ID}/world-facts/{fid}/history").status_code == 404


def test_api_history_404_missing(wf_db):
    assert _make_client(wf_db).get(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/424242/history").status_code == 404
