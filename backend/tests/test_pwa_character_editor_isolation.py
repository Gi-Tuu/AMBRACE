# -*- coding: utf-8 -*-
"""PWA M1a-5 角色创建/编辑 · **真实权限隔离**（沙箱集成，2026-10-18）。

用真实 `app.api.characters.router` 与真实 `tenant_service` 口径（family / user），
不 mock 业务或 `tenant_scope_ids`：合成家庭 A（A1 根 + A2 子）与独立家庭 B1，各种角色与哨兵。

覆盖：
- family：A2 能 GET/PUT A1 的角色且 **owner 仍是 A1**（不因「非本人」拒绝）；
  A2 自建角色 owner 必须是 A2；
- user（`set_tenant_key_mode("user")`，finally 复原）：A2 不能读写 A1 的角色，自己的仍正常；
- 跨家庭：双向 GET/PUT 均 404，对方原文哨兵与 owner 不变；不存在 id 404 作对照；
- 未认证 GET/POST/PUT 401；
- LLM/嵌入/TTS/外网 external_tripwire 全程计数必须为 0。
"""
from __future__ import annotations

import asyncio

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.application import tenant_service
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.utils.errors import register_exception_handlers

A1, A2, B1 = "pwa_cei_a1", "pwa_cei_a2", "pwa_cei_b1"
PASSWORD = "PwaCei123"
_IDS: dict[str, int] = {}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    return app


async def _seed() -> None:
    async with async_session_factory() as db:
        have = (await db.execute(text("SELECT COUNT(*) FROM users WHERE username LIKE 'pwa_cei_%'"))).scalar_one()
        if have:
            for name in (A1, A2, B1):
                r = (await db.execute(text("SELECT id FROM users WHERE username=:u"), {"u": name})).first()
                _IDS[name] = int(r[0])
            for key, owner, cname in (("charA1", A1, "A1角色"), ("charA2", A2, "A2角色"), ("charB1", B1, "B1角色")):
                r = (await db.execute(
                    text("SELECT id FROM ai_characters WHERE user_id=:u AND name=:n"),
                    {"u": _IDS[owner], "n": cname})).first()
                _IDS[key] = int(r[0]) if r else 0
            return
        pwd = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
        u1 = await db.execute(text("INSERT INTO users (username, nickname, password_hash, is_admin) "
                                   "VALUES (:u,:u,:p,1)"), {"u": A1, "p": pwd})
        await db.commit()
        root = int(u1.lastrowid)
        u2 = await db.execute(text("INSERT INTO users (username, nickname, password_hash, is_admin, parent_id) "
                                   "VALUES (:u,:u,:p,0,:r)"), {"u": A2, "p": pwd, "r": root})
        ub = await db.execute(text("INSERT INTO users (username, nickname, password_hash, is_admin) "
                                   "VALUES (:u,:u,:p,1)"), {"u": B1, "p": pwd})
        await db.commit()
        # 角色用 ORM 造数：ai_characters 有 NOT NULL 列（is_partner 等），裸 SQL 会漏
        c1 = AICharacter(user_id=root, name="A1角色", bio="A1哨兵原文")
        c2 = AICharacter(user_id=int(u2.lastrowid), name="A2角色", bio="A2原文")
        cb = AICharacter(user_id=int(ub.lastrowid), name="B1角色", bio="B1哨兵原文")
        db.add_all([c1, c2, cb])
        await db.commit()
        _IDS.update({A1: root, A2: int(u2.lastrowid), B1: int(ub.lastrowid),
                     "charA1": int(c1.id), "charA2": int(c2.id), "charB1": int(cb.id)})


async def _char(cid: int) -> dict:
    async with async_session_factory() as db:
        r = (await db.execute(text("SELECT id, user_id, name, bio FROM ai_characters WHERE id=:c"),
                              {"c": cid})).mappings().first()
        return dict(r) if r else {}


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    asyncio.run(_seed())
    assert tenant_service.get_tenant_key_mode() == "family", "本组以 family 为起始口径"
    return _IDS


@pytest.fixture(scope="module")
def client():
    with TestClient(_app()) as session:
        yield session


@pytest.fixture(autouse=True)
def external_tripwire(monkeypatch):
    """真实出口及已加载的早绑定引用均拦截；吞掉异常也会在收尾失败。"""
    import sys
    import httpx
    import app.agent.llm_client as llm_client
    from app.application import tts_service
    from app.memory import embedding

    calls = []

    def boom(*args, **kwargs):
        calls.append(True)
        raise AssertionError("角色管理不得调用模型、嵌入、语音或外网")

    targets = [llm_client.chat_completion, llm_client.chat_completion_stream,
               embedding.text_embedding, embedding._embed_sync, tts_service.synthesize]
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("app.") and module is not None:
            for name, value in list(vars(module).items()):
                if any(value is target for target in targets):
                    monkeypatch.setattr(module, name, boom)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", boom)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", boom)
    yield calls
    assert not calls, "流程触发了禁止的出口（包括被业务吞掉的异常）"



def _auth(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_家庭共享_子账号可读写家庭根角色且owner不变(client, _seeded, external_tripwire):
    h = _auth(client, A2)
    cid = _seeded["charA1"]
    g = client.get(f"/api/v1/characters/{cid}", headers=h)
    assert g.status_code == 200, f"家庭内子账号应可读，实际 {g.status_code}"
    assert int(g.json()["user_id"]) == _seeded[A1], "owner 仍是家庭根 A1"

    r = client.put(f"/api/v1/characters/{cid}", json={"bio": "A2改的（家庭共享）"}, headers=h)
    assert r.status_code == 200, f"家庭内子账号应可编辑，实际 {r.status_code} {r.text[:200]}"
    row = asyncio.run(_char(cid))
    assert row["bio"] == "A2改的（家庭共享）"
    assert int(row["user_id"]) == _seeded[A1], "owner 不应转移"

    created = client.post("/api/v1/characters", json={"name": "A2自建"}, headers=h)
    assert created.status_code == 201, created.text
    assert int(created.json()["user_id"]) == _seeded[A2], "自建角色 owner 必须是本人"
    assert not external_tripwire


def test_user模式_子账号不能读写他人角色(client, _seeded, external_tripwire):
    # 保存**真实运行时覆盖**（None=跟随 settings）；set_tenant_key_mode(None) 即清除覆盖，
    # 直接用它的返回值会拿到"设置后的新值"，finally 复原就会把覆盖永久改成 user（口径泄漏）。
    prev_override = tenant_service._mode_override
    prev_effective = tenant_service.get_tenant_key_mode()
    tenant_service.set_tenant_key_mode("user")
    try:
        assert tenant_service.get_tenant_key_mode() == "user"
        h = _auth(client, A2)
        cid = _seeded["charA1"]
        # 用「尝试前快照」而不是种子原文（family 用例已合法改过该行，避免用例间串扰）
        before = asyncio.run(_char(cid))
        g = client.get(f"/api/v1/characters/{cid}", headers=h)
        assert g.status_code == 404, f"user 模式下应不可见，实际 {g.status_code}"
        p = client.put(f"/api/v1/characters/{cid}", json={"bio": "越权"}, headers=h)
        assert p.status_code == 404, f"user 模式下应不可编辑，实际 {p.status_code}"
        assert asyncio.run(_char(cid)) == before, "user 模式下不得被改动"
        own = client.post("/api/v1/characters", json={"name": "A2user自建"}, headers=h)
        assert own.status_code == 201, own.text
        assert int(own.json()["user_id"]) == _seeded[A2]
        own_get = client.get(f"/api/v1/characters/{_seeded['charA2']}", headers=h)
        assert own_get.status_code == 200, "自己的资源仍应可读"
    finally:
        tenant_service.set_tenant_key_mode(prev_override)
    assert tenant_service._mode_override == prev_override, "必须恢复原始覆盖值（None 即无覆盖）"
    assert tenant_service.get_tenant_key_mode() == prev_effective, "生效口径必须复原"
    assert not external_tripwire


def test_跨家庭双向404_哨兵与owner不变(client, _seeded, external_tripwire):
    ha, hb = _auth(client, A1), _auth(client, B1)
    ca, cb = _seeded["charA1"], _seeded["charB1"]
    before_a = asyncio.run(_char(ca))
    before_b = asyncio.run(_char(cb))  # 双方哨兵都读回
    assert before_a["bio"] and before_b["bio"]
    assert client.get(f"/api/v1/characters/{cb}", headers=ha).status_code == 404
    assert client.put(f"/api/v1/characters/{cb}", json={"bio": "越权"}, headers=ha).status_code == 404
    assert client.get(f"/api/v1/characters/{ca}", headers=hb).status_code == 404
    assert client.put(f"/api/v1/characters/{ca}", json={"bio": "越权"}, headers=hb).status_code == 404
    after_a = asyncio.run(_char(ca))
    after_b = asyncio.run(_char(cb))
    assert after_a == before_a, "跨家庭调用不得改动 A 的字段"
    assert after_b == before_b, "跨家庭调用不得改动 B 的字段"
    assert client.get("/api/v1/characters/99999999", headers=ha).status_code == 404, "不存在的 id 同样 404"
    assert not external_tripwire


def test_未认证401(client, _seeded, external_tripwire):
    cid = _seeded["charA1"]
    assert client.get(f"/api/v1/characters/{cid}").status_code == 401
    assert client.post("/api/v1/characters", json={"name": "未认证"}).status_code == 401
    assert client.put(f"/api/v1/characters/{cid}", json={"bio": "未认证"}).status_code == 401
    assert not external_tripwire


async def _disabled_state(uid: int, value):
    async with async_session_factory() as db:
        previous = (await db.execute(text("SELECT disabled_at FROM users WHERE id=:u"), {"u": uid})).scalar_one()
        await db.execute(text("UPDATE users SET disabled_at=:v WHERE id=:u"), {"v": value, "u": uid})
        await db.commit()
        return previous


async def _write_snapshot():
    async with async_session_factory() as db:
        return {table: [dict(row) for row in (await db.execute(text(f"SELECT * FROM {table} ORDER BY id"))).mappings()]
                for table in ("ai_characters", "chat_sessions", "chat_messages", "llm_usage")}


def test_禁用账号已有令牌_GET_POST_PUT均403且无写入(client, _seeded, external_tripwire):
    from app.application import permission_service

    uid, cid = _seeded[A2], _seeded["charA2"]
    headers = _auth(client, A2)
    # 正对照：先让同一个令牌成功读一次，也让账号状态缓存真实建立。
    assert client.get(f"/api/v1/characters/{cid}", headers=headers).status_code == 200
    before = asyncio.run(_write_snapshot())
    previous = asyncio.run(_disabled_state(uid, "2026-01-01 00:00:00"))
    permission_service._invalidate_account_state_cache(uid)
    try:
        assert client.get(f"/api/v1/characters/{cid}", headers=headers).status_code == 403
        assert client.post("/api/v1/characters", json={"name": "禁用不得新建"}, headers=headers).status_code == 403
        assert client.put(f"/api/v1/characters/{cid}", json={"bio": "禁用不得修改"}, headers=headers).status_code == 403
        assert asyncio.run(_write_snapshot()) == before
    finally:
        asyncio.run(_disabled_state(uid, previous))
        permission_service._invalidate_account_state_cache(uid)
    assert client.get(f"/api/v1/characters/{cid}", headers=headers).status_code == 200
    assert not external_tripwire
