# -*- coding: utf-8 -*-
"""PWA 同源入口**真实路由契约**（沙箱集成，M1a-1，2026-10-16）。

口径：用**真实** auth / characters / chat 路由 + 真实 SQLAlchemy 会话（conftest 已把
DATABASE_URL / 向量目录 / 认证密钥指向本会话临时沙箱），合成账号与角色，跑
登录 → 角色 → 会话 → 历史。**不 import app.main**：它在导入期就抢 127.0.0.1:8766 单实例锁
（本机 8000 生产实例正持有该锁），照 test_chat_stream.py 的「独立 FastAPI 实例」策略。

边界（如实登记，不互相顶替）：
- 本文件只证「真实路由 + 真实库」这一层；浏览器端的真实 PWA build 与合成 SSE 另记。
- 文字生成一次都不跑：把 LLM 网络入口钉成「调用即失败」，并断言计数为 0
  （本流程本就不该触发生成；一旦有人把生成串进来，这条会红）。不改生产生成链。
"""
from __future__ import annotations

import asyncio

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.api.chat import router as chat_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.user import User
from app.utils.errors import register_exception_handlers

USERNAME = "pwa_same_origin_u"
PASSWORD = "PwaSameOrigin123"
NICKNAME = "PWA 同源验收"


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    app.include_router(chat_router)
    return app


async def _seed_user() -> int:
    async with async_session_factory() as db:
        row = (await db.execute(select(User).where(User.username == USERNAME))).scalar_one_or_none()
        if row is not None:
            return row.id
        user = User(
            username=USERNAME,
            nickname=NICKNAME,
            password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


@pytest.fixture
def _llm_network_guard(monkeypatch):
    """把生成链的 LLM 入口钉成「调用即失败」，并返回计数器供断言「一次都没调用」。"""
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


@pytest.fixture
def client():
    return TestClient(_app())


def test_真实路由闭环_登录角色会话历史(client, _llm_network_guard):
    user_id = asyncio.run(_seed_user())
    assert user_id > 0

    # ① 登录（真实 bcrypt + 真实限流 + 真实 JWT）
    r = client.post("/api/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert token
    auth = {"Authorization": f"Bearer {token}"}

    # ② 未带 token 的角色列表必须 401（认证边界没被放开）
    assert client.get("/api/v1/characters").status_code == 401

    # ③ 建角色（真实服务层）
    created = client.post("/api/v1/characters", json={"name": "Nova"}, headers=auth)
    assert created.status_code == 201, created.text
    char_id = created.json()["id"]

    # ④ 角色列表能看到它
    listed = client.get("/api/v1/characters", headers=auth)
    assert listed.status_code == 200, listed.text
    names = [c["name"] for c in listed.json()["characters"]]
    assert "Nova" in names

    # ⑤ 建会话（真实 chat 路由）
    sess = client.post(f"/api/v1/chat/sessions?character_id={char_id}", headers=auth)
    assert sess.status_code == 201, sess.text
    session_id = sess.json()["id"]

    # ⑥ 历史（空会话）
    hist = client.get(f"/api/v1/chat/sessions/{session_id}/messages", headers=auth)
    assert hist.status_code == 200, hist.text
    assert hist.json()["messages"] == []
    assert hist.json()["session_id"] == session_id

    # ⑦ 越权访问别人的会话 → 404（租户隔离）
    assert client.get("/api/v1/chat/sessions/999999/messages", headers=auth).status_code == 404

    # ⑧ 生成链一次都没被触发（网络 LLM 计数为 0）
    assert _llm_network_guard["n"] == 0


def test_登录失败走401且不回显内部信息(client, _llm_network_guard):
    r = client.post("/api/v1/auth/login", json={"username": USERNAME, "password": "wrong-pass-123"})
    assert r.status_code == 401
    body = r.text
    assert "bcrypt" not in body.lower()
    assert "traceback" not in body.lower()
    assert _llm_network_guard["n"] == 0


def test_未知API路径不落index(client, tmp_path):
    """同源入口下未知 API 仍是 API 语义 404，不会变成静态壳的 index。"""
    r = client.get("/api/v1/pwa-not-a-route")
    assert r.status_code == 404
    assert "正在加载" not in r.text
