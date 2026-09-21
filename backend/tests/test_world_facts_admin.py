# -*- coding: utf-8 -*-
"""世界设定（world facts）可删改 + 去重 测试（2026-09-15）。

覆盖：
- update 成功：改内容 + author 置 user / is_authoritative=1 / epistemic_status=FACT；
  同 subject-predicate 的其它活跃旧值被 supersede；
- update 404：目标不存在 / 非本人 / 非活跃；
- update 越权：别人的角色（user_id 不同）→ 404（关联不到就当不存在）；
- delete 放宽：author=system 的策展层事实也能删（软删 status=expired，不物理删）；
- create 去重：同 (character_id, predicate) 高度相似的活跃事实写入前置 superseded，只留新值。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库 + monkeypatch，
与 test_memory_trace.py 同法。内部写库走 async_session_factory，故需同时 patch
application.characters 与 events.facts 两个模块的引用。）
"""
import asyncio
import os

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import characters as characters_api
from app.application import characters as characters_svc
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.events import facts as facts_mod
from app.models.character import AICharacter
from app.models.memory import WorldFact

pytestmark = pytest.mark.slow

_CHAR_ID = 7
_OTHER_CHAR_ID = 8
_USER_ID = 1
_OTHER_USER_ID = 999


@pytest.fixture()
def wf_db(monkeypatch, tmp_path):
    """临时库（模板库克隆，见 tests/_dbclone.py）：种子角色；把 characters/facts 的
    session factory 指向临时库。"""
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        from app.models.user import User
        async with factory() as db:
            # _dbclone 默认开 FK（生产同款 PRAGMA）：ai_characters.user_id 需 users 父行先存在
            db.add(User(id=_USER_ID, username="wf_u1", nickname="本人"))
            db.add(User(id=_OTHER_USER_ID, username="wf_other", nickname="他人"))
            db.add(AICharacter(id=_CHAR_ID, user_id=_USER_ID, name="测试角色"))
            db.add(AICharacter(id=_OTHER_CHAR_ID, user_id=_OTHER_USER_ID, name="别人的角色"))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(characters_svc, "async_session_factory", factory)
    monkeypatch.setattr(facts_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _make_client(factory, user_id=_USER_ID) -> TestClient:
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


def _seed_fact(factory, **over) -> int:
    """种一条事实，返回 id；默认本角色本用户的 active system 事实（策展层口径）。"""
    kw = dict(
        user_id=_USER_ID, character_id=_CHAR_ID, subject_type="character",
        subject_id=_CHAR_ID, predicate="setting", object_value="用户腰不好",
        status="active", confidence=1.0, epistemic_status="FACT",
        audience='["user:1", "char:7"]', author="system", is_authoritative=False,
        source="curated",
    )
    kw.update(over)

    async def _run():
        async with factory() as db:
            r = WorldFact(**kw)
            db.add(r)
            await db.commit()
            await db.refresh(r)
            return r.id

    return asyncio.run(_run())


def _get_fact(factory, fact_id: int) -> WorldFact | None:
    async def _run():
        async with factory() as db:
            return await db.get(WorldFact, fact_id)

    return asyncio.run(_run())


# ---------------- PUT：编辑 ----------------

def test_update_world_fact_success(wf_db):
    """编辑成功：内容更新 + 升为用户权威（author=user/is_authoritative=1/FACT）。"""
    fid = _seed_fact(wf_db, object_value="用户腰不好", author="system", is_authoritative=False,
                     epistemic_status="INFERRED")
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}",
        json={"content": "用户腰有旧伤，不可逞能"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "id": fid}
    f = _get_fact(wf_db, fid)
    assert f.object_value == "用户腰有旧伤，不可逞能"
    assert f.author == "user"
    assert f.is_authoritative is True
    assert f.epistemic_status == "FACT"
    assert f.status == "active"                      # 编辑不是删除，仍活跃
    assert f.predicate == "setting"                  # 未传 predicate 时保持默认 setting


def test_update_world_fact_supersedes_same_subject_predicate(wf_db):
    """改前把同 subject-predicate 的其它活跃旧值置 superseded（留痕不删）。"""
    keep_id = _seed_fact(wf_db, object_value="用户腰不好")
    old_id = _seed_fact(wf_db, object_value="用户的对象是 sam", predicate="setting")
    old2_id = _seed_fact(wf_db, object_value="用户住在杭州", predicate="setting")
    other_pred = _seed_fact(wf_db, object_value="用户住在上海", predicate="curated")
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/{keep_id}",
        json={"content": "用户腰有旧伤"},
    )
    assert r.status_code == 200
    for oid in (old_id, old2_id):
        o = _get_fact(wf_db, oid)
        assert o.status == "superseded"              # 同 subject+predicate 旧值留痕停用
        assert o.superseded_by == keep_id
    assert _get_fact(wf_db, other_pred).status == "active"   # 不同 predicate 不受影响


def test_update_world_fact_404_not_found(wf_db):
    """目标不存在 → 404。"""
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/999999",
        json={"content": "x"},
    )
    assert r.status_code == 404


def test_update_world_fact_404_other_users_character(wf_db):
    """越权：请求别人的角色（非本人）→ 404（归属校验，不泄露是否存在）。"""
    fid = _seed_fact(wf_db, user_id=_OTHER_USER_ID, character_id=_OTHER_CHAR_ID,
                     subject_id=_OTHER_CHAR_ID)
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_OTHER_CHAR_ID}/world-facts/{fid}",
        json={"content": "x"},
    )
    assert r.status_code == 404
    assert _get_fact(wf_db, fid).object_value == "用户腰不好"   # 未被改动


def test_update_world_fact_404_inactive(wf_db):
    """已停用的事实不可编辑 → 404（只能改活跃事实）。"""
    fid = _seed_fact(wf_db, status="superseded")
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}",
        json={"content": "x"},
    )
    assert r.status_code == 404


def test_update_world_fact_400_empty_content(wf_db):
    """空内容 → 400（content 必填）。"""
    fid = _seed_fact(wf_db)
    r = _make_client(wf_db).put(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}",
        json={"content": "   "},
    )
    assert r.status_code == 400


# ---------------- DELETE：放宽 ----------------

def test_delete_system_world_fact_allowed(wf_db):
    """放宽后：author=system 的策展层事实也能删（软删 status=expired，留痕不物理删）。"""
    fid = _seed_fact(wf_db, author="system", is_authoritative=False)
    r = _make_client(wf_db).delete(f"/api/v1/characters/{_CHAR_ID}/world-facts/{fid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    f = _get_fact(wf_db, fid)
    assert f is not None                            # 软删：行仍在
    assert f.status == "expired"
    # 列表接口只返回 active，删后不再出现
    items = _make_client(wf_db).get(f"/api/v1/characters/{_CHAR_ID}/world-facts").json()["items"]
    assert all(i["id"] != fid for i in items)


def test_delete_world_fact_404_other_user(wf_db):
    """别人的事实 / 不存在的 id → 404。"""
    fid = _seed_fact(wf_db, user_id=_OTHER_USER_ID, character_id=_OTHER_CHAR_ID,
                     subject_id=_OTHER_CHAR_ID)
    assert _make_client(wf_db).delete(
        f"/api/v1/characters/{_OTHER_CHAR_ID}/world-facts/{fid}").status_code == 404
    assert _make_client(wf_db).delete(
        f"/api/v1/characters/{_CHAR_ID}/world-facts/424242").status_code == 404


# ---------------- POST：写入去重 ----------------

def test_create_world_fact_supersedes_similar(wf_db):
    """创建时同 (character_id, predicate) 高度相似的活跃事实被 superseded，只留新值。

    三颗种子都用 subject_type='user'（与新建行的 subject 不同）：assert_fact 只在
    「同 subject+predicate」上 supersede，够不到它们，因此这里的停用只可能来自
    本波新增的相似度去重。
    """
    _diff_subject = dict(subject_type="user", subject_id=_USER_ID)
    old_id = _seed_fact(wf_db, object_value="用户腰不好，需注意别逞能加量", predicate="setting",
                        author="system", **_diff_subject)
    far_id = _seed_fact(wf_db, object_value="用户喜欢喝美式咖啡", predicate="setting",
                        author="system", **_diff_subject)
    other_pred = _seed_fact(wf_db, object_value="用户腰不好，需注意别逞能加量", predicate="curated",
                            author="system", **_diff_subject)
    r = _make_client(wf_db).post(
        f"/api/v1/characters/{_CHAR_ID}/world-facts",
        json={"content": "用户腰不好，需要注意别逞能加量", "predicate": "setting"},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert _get_fact(wf_db, old_id).status == "superseded"   # 语义重复 → 旧的停用留痕
    assert _get_fact(wf_db, far_id).status == "active"       # 不相似 → 保留
    assert _get_fact(wf_db, other_pred).status == "active"   # 不同 predicate → 不受影响
    new_f = _get_fact(wf_db, new_id)
    assert new_f.status == "active"
    assert new_f.author == "user"
    assert new_f.object_value == "用户腰不好，需要注意别逞能加量"
