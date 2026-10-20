# -*- coding: utf-8 -*-
"""PWA M1a-3 会话工作台 · **真实路由契约**（沙箱集成，2026-10-17）。

口径：用**真实** auth / chat 路由 + 真实 SQLAlchemy 会话（conftest 已把 DATABASE_URL /
认证密钥指向本会话临时沙箱），跑「建会话 / 会话列表 / 历史分页」。**不 import app.main**
（单实例锁），照 test_pwa_same_origin.py 的「独立 FastAPI 实例」策略。

本单覆盖的两处后端改动（读代码确认，逐字节对齐实现）：
1. ``chat_store.create_session(..., force_new=False)``：默认 False 复用最新活跃会话（行为与
   改动前逐字节一致）；``force_new=True`` 跳过复用直接新建，问候语/落库/事件逻辑不变。
2. ``POST /api/v1/chat/sessions`` 新增可选 query ``force_new``，其余不变（仍 201、仍按
   ``AICharacter.user_id == user_id`` 精确校验角色归属 → 不属于本人一律 404）。

造数约定（防踩坑）：
- 每个用例各自种一套「两家庭、同家庭两账号、多角色」（函数级沙箱，账号名带自增序号确保唯一，
  互不污染；本会话库为会话级临时文件库）；
- 历史角色预置 8 条消息，其中 H4/H5 **同 created_at 不同 id**，用来钉死窗口顺序；
- 全程不触发 LLM/TTS：把生成入口与 TTS 合成入口钉成「调用即失败」，断言计数为 0。
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

from app.api.chat import router as chat_router
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.models.character import AICharacter
from app.models.chat import ChatMessage, ChatSession
from app.models.domain_event import DomainEvent
from app.models.user import User
from app.utils.errors import register_exception_handlers

PASSWORD = "PwaSession123"
_seq = itertools.count(1)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app


async def _mk_user(db, nickname: str, *, is_admin: bool, parent_id: int | None = None) -> User:
    user = User(
        username=f"pwa_sw_{next(_seq):03d}",
        nickname=nickname,
        password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
        is_admin=is_admin,
        parent_id=parent_id,
    )
    db.add(user)
    await db.flush()
    return user


async def _seed_scene() -> dict:
    """种一套：甲家（主账号 a1 + 子账号 a2）、乙家（主账号 b1）；多角色 + 多会话。"""
    s: dict = {}

    def _t(h: int, m: int) -> datetime:
        return datetime(2026, 1, 2, h, m)

    async with async_session_factory() as db:
        a1 = await _mk_user(db, "甲家主账号", is_admin=True)
        a2 = await _mk_user(db, "甲家子账号", is_admin=False, parent_id=a1.id)
        b1 = await _mk_user(db, "乙家主账号", is_admin=True)
        await db.flush()

        ch_default = AICharacter(user_id=a1.id, name="默认角色", greeting_message="你好呀，我是默认角色")
        ch_force = AICharacter(user_id=a1.id, name="强制角色", greeting_message="强制问候语")
        ch_hist = AICharacter(user_id=a1.id, name="历史角色")
        ch_bool = AICharacter(user_id=a1.id, name="开关角色")
        ch_sub = AICharacter(user_id=a2.id, name="子账号角色", greeting_message="子账号问候")
        ch_b = AICharacter(user_id=b1.id, name="外家庭角色", greeting_message="外来问候")
        db.add_all([ch_default, ch_force, ch_hist, ch_bool, ch_sub, ch_b])
        await db.flush()
        s.update(
            ua1=a1.id, ua2=a2.id, ub1=b1.id,
            a1_username=a1.username, a2_username=a2.username, b1_username=b1.username,
            ch_default=ch_default.id, ch_force=ch_force.id, ch_hist=ch_hist.id,
            ch_bool=ch_bool.id, ch_sub=ch_sub.id, ch_b=ch_b.id,
        )

        # 强制角色：预置一个活跃旧会话（「同角色两个会话」里的旧会话，含 2 条消息）
        s_force_old = ChatSession(user_id=a1.id, character_id=ch_force.id,
                                  created_at=datetime(2026, 1, 1, 9))
        db.add(s_force_old)
        await db.flush()
        db.add_all([
            ChatMessage(session_id=s_force_old.id, sender_type="ai", content="FORCE-旧1",
                        created_at=datetime(2026, 1, 1, 9, 1)),
            ChatMessage(session_id=s_force_old.id, sender_type="user", content="FORCE-旧2",
                        created_at=datetime(2026, 1, 1, 9, 2)),
        ])
        s["s_force_old"] = s_force_old.id

        # 开关角色：预置一个空活跃会话（force_new=false 显式传参对照用）
        s_bool = ChatSession(user_id=a1.id, character_id=ch_bool.id,
                             created_at=datetime(2026, 1, 4, 9))
        db.add(s_bool)
        await db.flush()
        s["s_bool"] = s_bool.id

        # 历史角色：8 条消息（H4/H5 同 created_at 不同 id）
        s_hist = ChatSession(user_id=a1.id, character_id=ch_hist.id,
                             created_at=datetime(2026, 1, 2, 9))
        db.add(s_hist)
        await db.flush()
        hist = [
            ChatMessage(session_id=s_hist.id, sender_type="user", content="H1", created_at=_t(10, 0)),
            ChatMessage(session_id=s_hist.id, sender_type="ai", content="H2", created_at=_t(10, 1)),
            ChatMessage(session_id=s_hist.id, sender_type="user", content="H3", created_at=_t(10, 2)),
            ChatMessage(session_id=s_hist.id, sender_type="user", content="H4", created_at=_t(10, 3)),
            ChatMessage(session_id=s_hist.id, sender_type="user", content="H5", created_at=_t(10, 3)),
            ChatMessage(session_id=s_hist.id, sender_type="ai", content="H6", created_at=_t(10, 4)),
            ChatMessage(session_id=s_hist.id, sender_type="user", content="H7", created_at=_t(10, 5)),
            ChatMessage(session_id=s_hist.id, sender_type="ai", content="H8", created_at=_t(10, 6)),
        ]
        db.add_all(hist)
        await db.flush()
        s.update(s_hist=s_hist.id, hist_ids=[m.id for m in hist])

        # 子账号 / 外家庭各一个会话（越权不泄漏哨兵）
        s_sub = ChatSession(user_id=a2.id, character_id=ch_sub.id, created_at=datetime(2026, 1, 5, 9))
        s_b = ChatSession(user_id=b1.id, character_id=ch_b.id, created_at=datetime(2026, 1, 6, 9))
        db.add_all([s_sub, s_b])
        await db.flush()
        s.update(s_sub=s_sub.id, s_b=s_b.id)
        await db.commit()
    return s


# ── DB 探测器 ────────────────────────────────────────────────────────────────
async def _session_ids(user_id: int, character_id: int | None = None) -> list[int]:
    async with async_session_factory() as db:
        q = select(ChatSession.id).where(
            ChatSession.user_id == user_id, ChatSession.is_active == True  # noqa: E712
        )
        if character_id is not None:
            q = q.where(ChatSession.character_id == character_id)
        return [int(i) for i in (await db.execute(q)).scalars().all()]


async def _ordered_contents(session_id: int) -> list[str]:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ChatMessage.content)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())
        )).scalars().all()
        return list(rows)


async def _ai_contents(session_id: int) -> list[str]:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(ChatMessage.content).where(
                ChatMessage.session_id == session_id, ChatMessage.sender_type == "ai"
            )
        )).scalars().all()
        return list(rows)


async def _session_created_events(session_id: int) -> list[int]:
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(DomainEvent.aggregate_id).where(
                DomainEvent.event_type == "chat.session_created",
                DomainEvent.aggregate_id == session_id,
            )
        )).scalars().all()
        return [int(i) for i in rows]


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _enable_domain_events():
    """打开领域事件灰度开关，让建会话的创建事件真落库可断言。

    事件流水受 ``AGENT_FLAGS['domain_event_log_enabled']`` 灰度控制（默认关）；本文件只读代码
    路径、不改生产默认，用例内临时打开再按原值还原（conftest 另有一道全量快照护栏）。
    """
    from app.agent.loop import AGENT_FLAGS

    had = "domain_event_log_enabled" in AGENT_FLAGS
    prev = AGENT_FLAGS.get("domain_event_log_enabled")
    AGENT_FLAGS["domain_event_log_enabled"] = True
    yield
    if had:
        AGENT_FLAGS["domain_event_log_enabled"] = prev
    else:
        AGENT_FLAGS.pop("domain_event_log_enabled", None)


@pytest.fixture
def scene():
    return asyncio.run(_seed_scene())


@pytest.fixture(scope="module")
def client():
    return TestClient(_app())


@pytest.fixture
def guard(monkeypatch):
    """把生成链 LLM 入口与 TTS 合成入口钉成「调用即失败」，返回计数供断言零调用。"""
    calls = {"llm": 0, "tts": 0}

    def _boom_llm(*_a, **_kw):
        calls["llm"] += 1
        raise AssertionError("建会话流程不得触发 LLM 网络调用")

    def _boom_tts(*_a, **_kw):
        calls["tts"] += 1
        raise AssertionError("建会话流程不得触发 TTS 合成")

    import app.agent.llm_client as llm_client
    import app.agent.nodes as nodes
    import app.application.tts_service as tts_service

    for mod, name in ((llm_client, "chat_completion"), (llm_client, "chat_completion_stream"),
                      (nodes, "chat_completion"), (nodes, "chat_completion_stream")):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _boom_llm, raising=False)
    if hasattr(tts_service, "synthesize"):
        monkeypatch.setattr(tts_service, "synthesize", _boom_tts, raising=False)
    return calls


def _login(client: TestClient, username: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mids(resp) -> list[str]:
    return [m["content"] for m in resp.json()["messages"]]


# ── 1. 默认建会话：两次复用同 ID ─────────────────────────────────────────────
def test_默认建会话两次复用同ID(client, scene, guard):
    auth = _login(client, scene["a1_username"])
    cid = scene["ch_default"]

    r1 = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    assert r1.status_code == 201, r1.text
    r2 = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    assert r2.status_code == 201, r2.text

    assert r1.json()["id"] == r2.json()["id"], "默认建会话两次未复用同一 ID"
    assert r1.json()["character_id"] == cid == r2.json()["character_id"]
    assert r1.json()["greeting"] == "你好呀，我是默认角色"  # 首次为新建分支，带问候语
    assert r2.json()["greeting"] is None                      # 第二次为复用分支
    assert asyncio.run(_session_ids(scene["ua1"], cid)) == [r1.json()["id"]]
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 2. 新建分支：问候语与创建事件保留 ────────────────────────────────────────
def test_新建会话落问候语与创建事件(client, scene, guard):
    auth = _login(client, scene["a1_username"])
    cid = scene["ch_default"]

    r = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # 问候语真落库（新会话里有一条 ai 消息 == 角色问候语）
    assert asyncio.run(_ai_contents(sid)) == ["你好呀，我是默认角色"]
    # 会话创建事件真追加（aggregate_id 绑该会话）
    assert asyncio.run(_session_created_events(sid)) == [sid]
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 3. force_new=true：跳过复用、新建新 ID，旧会话消息完整 ────────────────────
def test_force_new创建新ID且旧会话消息完整(client, scene, guard):
    auth = _login(client, scene["a1_username"])
    cid = scene["ch_force"]
    old = scene["s_force_old"]
    before = asyncio.run(_ordered_contents(old))
    assert before == ["FORCE-旧1", "FORCE-旧2"]

    # 默认入口先复用既有活跃会话（证明旧会话确实存在且活跃）
    reuse = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    assert reuse.status_code == 201, reuse.text
    assert reuse.json()["id"] == old and reuse.json()["greeting"] is None

    # force_new=true：跳过复用，新建新会话
    fresh = client.post(f"/api/v1/chat/sessions?character_id={cid}&force_new=true", headers=auth)
    assert fresh.status_code == 201, fresh.text
    nid = fresh.json()["id"]
    assert nid != old, "force_new 未新建会话（仍复用旧会话）"
    assert fresh.json()["greeting"] == "强制问候语"  # 新建分支的问候语逻辑保留
    assert sorted(asyncio.run(_session_ids(scene["ua1"], cid))) == sorted([old, nid])
    assert asyncio.run(_ai_contents(nid)) == ["强制问候语"]
    assert asyncio.run(_session_created_events(nid)) == [nid]

    # 旧会话消息完整、一行未动
    assert asyncio.run(_ordered_contents(old)) == before

    # force_new 后用默认入口：按最新消息复用新会话（证明新建会话已生效）
    again = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    assert again.json()["id"] == nid
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 4. force_new=false 显式传参：行为与默认一致 ──────────────────────────────
def test_force_new_false显式传参与默认一致(client, scene, guard):
    auth = _login(client, scene["a1_username"])
    cid = scene["ch_bool"]
    existing = scene["s_bool"]

    default = client.post(f"/api/v1/chat/sessions?character_id={cid}", headers=auth)
    explicit = client.post(f"/api/v1/chat/sessions?character_id={cid}&force_new=false", headers=auth)
    assert default.status_code == 201 and explicit.status_code == 201
    assert default.json()["id"] == explicit.json()["id"] == existing, "显式 false 未与默认同口径复用"
    assert asyncio.run(_session_ids(scene["ua1"], cid)) == [existing]

    # 与真值对照：force_new=true 才会新建
    truthy = client.post(f"/api/v1/chat/sessions?character_id={cid}&force_new=true", headers=auth)
    assert truthy.json()["id"] != existing
    assert len(asyncio.run(_session_ids(scene["ua1"], cid))) == 2
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 5. 历史分页：skip/limit/total 与窗口顺序 ─────────────────────────────────
def test_历史分页窗口顺序与total(client, scene, guard):
    auth = _login(client, scene["a1_username"])
    sid = scene["s_hist"]

    p1 = client.get(f"/api/v1/chat/sessions/{sid}/messages?skip=0&limit=3", headers=auth)
    p2 = client.get(f"/api/v1/chat/sessions/{sid}/messages?skip=3&limit=3", headers=auth)
    p3 = client.get(f"/api/v1/chat/sessions/{sid}/messages?skip=6&limit=3", headers=auth)
    assert p1.status_code == p2.status_code == p3.status_code == 200
    assert p1.json()["session_id"] == sid
    assert p1.json()["total"] == p2.json()["total"] == p3.json()["total"] == 8

    # 实测口径：DB 先按 (created_at DESC, id DESC) 取窗，再对每页 reverse。
    # 故每页内部按 (created_at ASC, id ASC)，且「页 0 = 最新的一窗」。
    assert _mids(p1) == ["H6", "H7", "H8"]
    assert _mids(p2) == ["H3", "H4", "H5"]
    assert _mids(p3) == ["H1", "H2"]
    # 同 created_at 不同 id 的两行（H4/H5）在窗口内按 id 升序，顺序稳定
    ids_p2 = [m["id"] for m in p2.json()["messages"]]
    assert ids_p2 == sorted(ids_p2)
    assert {m["content"] for m in p2.json()["messages"]} == {"H3", "H4", "H5"}

    # 越界页：空窗口、total 仍为全量
    beyond = client.get(f"/api/v1/chat/sessions/{sid}/messages?skip=8&limit=3", headers=auth)
    assert beyond.json()["messages"] == [] and beyond.json()["total"] == 8

    # 越权不泄漏：非同账号（甲子）与外家庭（乙）看甲主会话一律 404；跨会话同理
    auth_a2 = _login(client, scene["a2_username"])
    auth_b = _login(client, scene["b1_username"])
    assert client.get(f"/api/v1/chat/sessions/{sid}/messages", headers=auth_a2).status_code == 404
    assert client.get(f"/api/v1/chat/sessions/{sid}/messages", headers=auth_b).status_code == 404
    assert client.get(f"/api/v1/chat/sessions/{scene['s_sub']}/messages", headers=auth).status_code == 404
    assert client.get(f"/api/v1/chat/sessions/{scene['s_b']}/messages", headers=auth).status_code == 404
    assert client.get("/api/v1/chat/sessions/999999/messages", headers=auth).status_code == 404
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 6. 会话列表：只返回当前账号 ──────────────────────────────────────────────
def test_会话列表只返回当前账号(client, scene, guard):
    auth_a1 = _login(client, scene["a1_username"])
    auth_a2 = _login(client, scene["a2_username"])
    auth_b = _login(client, scene["b1_username"])

    la1 = client.get("/api/v1/chat/sessions", headers=auth_a1)
    assert la1.status_code == 200, la1.text
    body = la1.json()
    assert set(body.keys()) == {"sessions", "total"}
    assert body["total"] == len(body["sessions"]) == 3
    assert {s["id"] for s in body["sessions"]} == {scene["s_bool"], scene["s_force_old"], scene["s_hist"]}
    assert all(s["user_id"] == scene["ua1"] for s in body["sessions"])
    assert scene["s_sub"] not in {s["id"] for s in body["sessions"]}
    assert scene["s_b"] not in {s["id"] for s in body["sessions"]}

    l2 = client.get("/api/v1/chat/sessions", headers=auth_a2).json()
    assert {s["id"] for s in l2["sessions"]} == {scene["s_sub"]}
    assert all(s["user_id"] == scene["ua2"] for s in l2["sessions"])

    lb = client.get("/api/v1/chat/sessions", headers=auth_b).json()
    assert {s["id"] for s in lb["sessions"]} == {scene["s_b"]}
    assert all(s["user_id"] == scene["ub1"] for s in lb["sessions"])
    assert guard["llm"] == 0 and guard["tts"] == 0


# ── 7. 建会话归属校验：外家庭 / 非本人角色 / 不存在 → 404 ─────────────────────
def test_建会话跨家庭与非本人角色404(client, scene, guard):
    auth_a1 = _login(client, scene["a1_username"])
    auth_a2 = _login(client, scene["a2_username"])

    # 外家庭角色 → 404（与「不存在」同观感）
    assert client.post(f"/api/v1/chat/sessions?character_id={scene['ch_b']}", headers=auth_a1).status_code == 404
    # 角色不存在 → 404
    assert client.post("/api/v1/chat/sessions?character_id=999999", headers=auth_a1).status_code == 404
    # 同家庭但非同账号的角色：归属校验按 user_id 精确匹配，故 404（如实登记，见文件头）
    assert client.post(f"/api/v1/chat/sessions?character_id={scene['ch_default']}", headers=auth_a2).status_code == 404
    assert client.post(f"/api/v1/chat/sessions?character_id={scene['ch_b']}", headers=auth_a2).status_code == 404
    assert guard["llm"] == 0 and guard["tts"] == 0
