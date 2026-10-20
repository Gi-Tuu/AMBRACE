# -*- coding: utf-8 -*-
"""PWA M1a-2 六模块只读工作台 · **隔离与零副作用守卫**（沙箱集成，2026-10-16）。

与 test_pwa_reader_contract.py 分工：那边验「封套/分页/过滤长什么样」，这边验
「只读面有没有越界」——

1. **零业务写**：对 A 账号做六域全量 GET 扫描（记忆列表走只读面**真实消费的**
   ``read_only=true`` 模式），前后对比八张业务表行数，一行都不能变；扫描前特意种一条
   **已到期** Memory，用它钉死「只读模式不物理删除到期行」；
2. **零 LLM**：扫描全程生成/LLM 网络入口计数为 0；
3. **跨家庭哨兵双向不可见**：A 的响应里不得出现 B 的 ZXQB 哨兵串，B 的响应里不得
   出现 A 的 ZXQA 哨兵串；互查对方资源 id 一律 404；
4. **旧默认维护政策另有正对照**：不带 ``read_only`` 的默认读仍按既有政策物理删除
   到期行并清向量（不许把维护政策改成默认关掉）。

注：``read_only`` 是 2026-10-16 PWA M1a-2 返工新增的**显式无维护读取**选项（见
``backend/app/api/memories.py`` / ``backend/app/memory/maintain.py``）；默认路径逐字节不变。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.api.diary import router as diary_router
from app.api.memories import router as memories_router
from app.api.moments import router as router_moments
from app.api.user_content import router as user_content_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.life import (
    AIDiary, AIMoment, MomentAILike, MomentComment, MomentLike, UserDiary, UserMemo,
)
from app.models.memory import Memory
from app.models.user import User
from app.utils.errors import register_exception_handlers

USER_A = "pwa_rdi_a"
USER_B = "pwa_rdi_b"
PASSWORD = "PwaReaderIso123"
FAR_FUTURE = datetime(2099, 1, 1)

_IDS: dict[str, int] = {}

_WATCHED = {
    "memories": Memory,
    "ai_diaries": AIDiary,
    "user_diaries": UserDiary,
    "user_memos": UserMemo,
    "ai_moments": AIMoment,
    "moment_comments": MomentComment,
    "moment_likes": MomentLike,
    "moment_ai_likes": MomentAILike,
}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    app.include_router(memories_router)
    app.include_router(diary_router)
    app.include_router(router_moments)
    app.include_router(user_content_router)
    return app


async def _seed() -> None:
    async with async_session_factory() as db:
        if (await db.execute(select(User).where(User.username == USER_A))).scalar_one_or_none():
            return
        ua = User(username=USER_A, nickname="隔离A",
                  password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode())
        ub = User(username=USER_B, nickname="隔离B",
                  password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode())
        db.add_all([ua, ub])
        await db.flush()
        ca = AICharacter(user_id=ua.id, name="IsoAlpha")
        cb = AICharacter(user_id=ub.id, name="IsoBeta")
        db.add_all([ca, cb])
        await db.flush()
        _IDS.update(ua=ua.id, ub=ub.id, ca=ca.id, cb=cb.id)

        mem_a = Memory(user_id=ua.id, character_id=ca.id, memory_type="user_info",
                       source="chat", content="ZXQA甲的记忆", importance=50,
                       last_reinforce_at=FAR_FUTURE)
        mem_b = Memory(user_id=ub.id, character_id=cb.id, memory_type="user_info",
                       source="chat", content="ZXQB乙的记忆", importance=50,
                       last_reinforce_at=FAR_FUTURE)
        db.add_all([mem_a, mem_b])
        await db.flush()
        _IDS.update(mem_a=mem_a.id, mem_b=mem_b.id)

        db.add_all([
            AIDiary(character_id=ca.id, diary_date="2026-03-01", content="ZXQA甲的角色日记"),
            AIDiary(character_id=cb.id, diary_date="2026-03-01", content="ZXQB乙的角色日记"),
            UserDiary(user_id=ua.id, diary_date="2026-03-02", content="ZXQA甲的个人日记"),
            UserDiary(user_id=ub.id, diary_date="2026-03-02", content="ZXQB乙的个人日记"),
            UserMemo(user_id=ua.id, title="ZXQA甲的备忘", content="ZXQA甲的备忘正文"),
            UserMemo(user_id=ub.id, title="ZXQB乙的备忘", content="ZXQB乙的备忘正文"),
        ])
        mo_a = AIMoment(sender_type="ai", character_id=ca.id, content="ZXQA甲的动态",
                        created_at=datetime(2026, 3, 3, 10))
        mo_b = AIMoment(sender_type="ai", character_id=cb.id, content="ZXQB乙的动态",
                        created_at=datetime(2026, 3, 3, 11))
        db.add_all([mo_a, mo_b])
        await db.flush()
        _IDS.update(mo_a=mo_a.id, mo_b=mo_b.id)
        db.add(MomentComment(moment_id=mo_a.id, parent_id=None, sender_type="user",
                             sender_id=ua.id, sender_name="隔离A", user_id=ua.id,
                             content="ZXQA甲的评论"))
        db.add(MomentComment(moment_id=mo_b.id, parent_id=None, sender_type="user",
                             sender_id=ub.id, sender_name="隔离B", user_id=ub.id,
                             content="ZXQB乙的评论"))
        await db.commit()


async def _table_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    async with async_session_factory() as db:
        for name, model in _WATCHED.items():
            counts[name] = int((await db.execute(
                select(func.count()).select_from(model))).scalar_one())
    return counts


async def _seed_expired() -> int:
    """临时种一条**已到期**（delete_at 已过）的 Memory：只读模式必须保留它、默认读会删它。"""
    async with async_session_factory() as db:
        row = Memory(user_id=_IDS["ua"], character_id=_IDS["ca"], memory_type="user_info",
                     source="chat", content="ZXQA到期行-只读面不得物理删除",
                     importance=50, status="active", last_reinforce_at=FAR_FUTURE,
                     delete_at=datetime(2000, 1, 1))
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row.id


async def _row_exists(mid: int) -> bool:
    async with async_session_factory() as db:
        return await db.get(Memory, mid) is not None


async def _row_snapshot(mid: int) -> dict:
    """业务行逐字段快照：用于断言只读模式「不改动业务行」（行消失时返回空 dict）。"""
    async with async_session_factory() as db:
        m = await db.get(Memory, mid)
        if m is None:
            return {}
        return {
            "id": m.id, "content": m.content, "memory_type": m.memory_type,
            "importance": float(m.importance or 0), "is_pinned": bool(m.is_pinned),
            "is_locked": bool(m.is_locked), "is_archived": bool(m.is_archived),
            "status": m.status, "delete_at": str(m.delete_at),
            "last_reinforce_at": str(m.last_reinforce_at), "updated_at": str(m.updated_at),
        }


@pytest.fixture
def _vector_recorder(monkeypatch):
    """向量删除回调换成记录器：不真跑向量 IO/模型，只记录「有没有被调用、调了谁」。

    ``maintain.list_memories`` 在函数顶 ``from app.memory.service import delete_memory_vector``
    （调用时解析属性），故 patch ``app.memory.service.delete_memory_vector`` 即生效。
    """
    import app.memory.service as memory_service

    calls: list[int] = []

    async def _probe(mid):
        calls.append(mid)

    monkeypatch.setattr(memory_service, "delete_memory_vector", _probe)
    return calls


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    asyncio.run(_seed())
    return _IDS


@pytest.fixture(scope="module")
def client():
    return TestClient(_app())


@pytest.fixture
def _llm_network_guard(monkeypatch):
    calls = {"n": 0}

    def _boom(*_a, **_kw):
        calls["n"] += 1
        raise AssertionError("本流程不得触发 LLM 网络调用")

    import app.agent.llm_client as llm_client
    import app.agent.nodes as nodes

    for mod, name in ((llm_client, "chat_completion"),
                      (llm_client, "chat_completion_stream"),
                      (nodes, "chat_completion"),
                      (nodes, "chat_completion_stream")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom, raising=False)
    return calls


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _sweep(client: TestClient, auth: dict, char_id: int, mem_id: int, moment_id: int) -> list[str]:
    """对六域全部 GET 端点扫一遍，返回各响应文本（供哨兵串断言）。"""
    urls = [
        f"/api/v1/characters/{char_id}",
        f"/api/v1/memories?character_id={char_id}&read_only=true",
        f"/api/v1/memories?character_id={char_id}&read_only=true&skip=1&limit=1",
        f"/api/v1/memories/{mem_id}",
        f"/api/v1/memories/{mem_id}/chain",
        f"/api/v1/memories/{mem_id}/history",
        f"/api/v1/diary/{char_id}",
        f"/api/v1/diary/{char_id}/date/2026-03-01",
        f"/api/v1/diary/{char_id}/dates?month=2026-03",
        "/api/v1/moments",
        "/api/v1/moments?skip=0&limit=1",
        f"/api/v1/moments/{moment_id}/comments",
        "/api/v1/user/memos",
        "/api/v1/user/diaries",
        "/api/v1/user/diaries/2026-03-02",
    ]
    texts = []
    for u in urls:
        r = client.get(u, headers=auth)
        assert r.status_code == 200, f"{u} → {r.status_code}: {r.text}"
        texts.append(r.text)
    return texts


def test_只读GET全程零业务写且零LLM(client, _seeded, _llm_network_guard, _vector_recorder):
    auth_a = _login(client, USER_A)
    expired_id = asyncio.run(_seed_expired())  # 到期行在场：只读面也不得动它
    before = asyncio.run(_table_counts())
    _sweep(client, auth_a, _seeded["ca"], _seeded["mem_a"], _seeded["mo_a"])
    after = asyncio.run(_table_counts())
    assert before == after, f"只读 GET 触发了业务写：{before} → {after}"
    assert asyncio.run(_row_exists(expired_id)), "只读模式物理删除了到期行"
    assert _vector_recorder == [], f"只读模式清理了向量：{_vector_recorder}"
    assert _llm_network_guard["n"] == 0


def test_跨家庭哨兵双向不可见(client, _seeded, _llm_network_guard):
    auth_a = _login(client, USER_A)
    auth_b = _login(client, USER_B)

    # A 视角：全量扫描看不到任何 ZXQB 哨兵串
    for text in _sweep(client, auth_a, _seeded["ca"], _seeded["mem_a"], _seeded["mo_a"]):
        assert "ZXQB" not in text
    # B 视角：全量扫描看得到自己的 ZXQB，看不到 ZXQA
    b_texts = _sweep(client, auth_b, _seeded["cb"], _seeded["mem_b"], _seeded["mo_b"])
    assert any("ZXQB" in t for t in b_texts)
    for text in b_texts:
        assert "ZXQA" not in text

    # 互查对方资源 id：六域一律 404（与「不存在」同观感）
    cross_404_for_a = [
        f"/api/v1/characters/{_seeded['cb']}",
        f"/api/v1/memories/{_seeded['mem_b']}",
        f"/api/v1/memories/{_seeded['mem_b']}/chain",
        f"/api/v1/memories/{_seeded['mem_b']}/history",
        f"/api/v1/diary/{_seeded['cb']}",
        f"/api/v1/diary/{_seeded['cb']}/date/2026-03-01",
        f"/api/v1/moments/{_seeded['mo_b']}/comments",
    ]
    for u in cross_404_for_a:
        assert client.get(u, headers=auth_a).status_code == 404, u
    assert client.get(f"/api/v1/characters/{_seeded['ca']}", headers=auth_b).status_code == 404
    assert client.get(f"/api/v1/memories/{_seeded['mem_a']}", headers=auth_b).status_code == 404
    assert client.get(f"/api/v1/diary/{_seeded['ca']}", headers=auth_b).status_code == 404
    assert client.get(f"/api/v1/moments/{_seeded['mo_a']}/comments", headers=auth_b).status_code == 404
    assert _llm_network_guard["n"] == 0


def test_只读模式不删到期行也不动向量(client, _seeded, _llm_network_guard, _vector_recorder):
    """返工 §2E：只读面显式 ``read_only=true`` —— 到期行**不落库删除、不清向量、业务行零改动**；
    展示口径（到期行不进列表）、认证/租户可见性/越权口径与默认路径完全一致。

    与 ``test_pwa_reader_review_side_effect.py``（首审反例，不带 read_only）的差别：那条
    证明**默认路径确实会写**，本条证明**只读面真实消费的模式确实不会写**，两条互为对照。
    """
    mid = asyncio.run(_seed_expired())
    snap_before = asyncio.run(_row_snapshot(mid))
    auth_a = _login(client, USER_A)

    r = client.get(f"/api/v1/memories?character_id={_seeded['ca']}&read_only=true", headers=auth_a)
    assert r.status_code == 200, r.text
    body = r.json()
    assert all(m["id"] != mid for m in body["memories"]), "到期行不得出现在展示列表里"
    assert body["total"] == 1, "到期行被剔除后 total 只应保留 ZXQA 甲的记忆"
    assert asyncio.run(_row_exists(mid)), "read_only 物理删除了到期行"
    assert asyncio.run(_row_snapshot(mid)) == snap_before, "read_only 改动了业务行"
    assert _vector_recorder == [], f"read_only 清理了向量：{_vector_recorder}"

    # 权限/可见性不放宽：跨家庭角色只得到空列表（不是 404、不是哨兵）；未认证仍 401
    cross = client.get(f"/api/v1/memories?character_id={_seeded['cb']}&read_only=true", headers=auth_a)
    assert cross.status_code == 200 and cross.json()["memories"] == []
    assert client.get("/api/v1/memories?read_only=true").status_code == 401
    # 参数类型错误按框架正常校验（422），不是被吞掉当 false
    assert client.get("/api/v1/memories?read_only=maybe", headers=auth_a).status_code == 422
    assert _llm_network_guard["n"] == 0


def test_默认读取仍保留既有到期物理删除政策(client, _seeded, _llm_network_guard, _vector_recorder):
    """正对照：**不带** ``read_only`` 的默认读仍是既有政策 —— 到期行物理删除 + 清向量。

    本用例保证本次返工没有把维护政策改成「默认关掉」来图省事；移动端等既有调用
    （不传该参数）行为必须逐字节不变。
    """
    mid = asyncio.run(_seed_expired())
    auth_a = _login(client, USER_A)

    r = client.get(f"/api/v1/memories?character_id={_seeded['ca']}", headers=auth_a)
    assert r.status_code == 200, r.text
    assert all(m["id"] != mid for m in r.json()["memories"])
    assert not asyncio.run(_row_exists(mid)), "默认路径必须保持既有的到期物理删除"
    assert mid in _vector_recorder, f"默认路径必须清理被删行的向量，实际 {_vector_recorder}"
    assert _llm_network_guard["n"] == 0
