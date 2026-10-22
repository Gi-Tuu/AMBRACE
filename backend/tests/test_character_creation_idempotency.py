# -*- coding: utf-8 -*-
"""角色创建幂等（M1）· 真实路由 + 沙箱库守卫（2026-10-19）。

覆盖：无键兼容 / 同键重放（含新 Session）/ 并发只 1 角色 / 同键不同 payload 409 /
不同键同 payload 均可建 / 两用户同键互不泄漏 / 失败回滚不消耗键 / 非法键与 401 无副作用 /
删除目标后重放 410 / 收据不含正文 / 唯一约束真实存在（负向对照）/ 迁移在 tmp 库可升级。

不 import app.main、不连生产；conftest 已把 DATABASE_URL 指向本会话临时沙箱。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from starlette.testclient import TestClient

from app.api.characters import router as characters_router
from app.application import character_creation_idempotency as idem
from app.auth.router import router as auth_router
from app.db.database import async_session_factory
from app.utils.errors import register_exception_handlers

A, B = "pwa_idem_a", "pwa_idem_b"
PASSWORD = "PwaIdem123"
_IDS: dict[str, int] = {}


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(auth_router)
    app.include_router(characters_router)
    return app


async def _seed() -> None:
    async with async_session_factory() as db:
        have = (await db.execute(
            text("SELECT COUNT(*) FROM users WHERE username LIKE 'pwa_idem_%'"))).scalar_one()
        if have:
            for name in (A, B):
                r = (await db.execute(text("SELECT id FROM users WHERE username=:u"), {"u": name})).first()
                _IDS[name] = int(r[0])
            return
        pwd = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
        for name in (A, B):
            r = await db.execute(text(
                "INSERT INTO users (username, nickname, password_hash, is_admin) "
                "VALUES (:u,:u,:p,1)"), {"u": name, "p": pwd})
            await db.commit()
            _IDS[name] = int(r.lastrowid)


async def _counts() -> dict[str, int]:
    async with async_session_factory() as db:
        out = {}
        for t in ("ai_characters", "character_creation_requests", "chat_sessions", "chat_messages", "llm_usage"):
            out[t] = int((await db.execute(text(f"SELECT COUNT(*) FROM {t}"))).scalar_one())
        return out


@pytest.fixture(scope="module", autouse=True)
def _seeded():
    asyncio.run(_seed())
    return _IDS


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


@pytest.fixture(scope="module")
def client():
    with TestClient(_app()) as session:
        yield session


def _auth(client: TestClient, username: str = A) -> dict:
    r = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _post(client, headers, key=None, **payload):
    h = dict(headers)
    if key is not None:
        h["Idempotency-Key"] = key
    return client.post("/api/v1/characters", json=payload, headers=h)


def test_无键保持旧语义_带键重放同id且只建一个(client, _seeded):
    h = _auth(client)
    # ① 不带键：两次是两次独立创建（手机端兼容）
    r1 = _post(client, h, name="NO_KEY_1")
    r2 = _post(client, h, name="NO_KEY_2")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]

    # ② 同键同请求：同 id、只 1 角色、只 1 收据
    before = asyncio.run(_counts())
    a1 = _post(client, h, key="idem-replay-1", name="IDEM_ONE", bio="原文哨兵")
    a2 = _post(client, h, key="idem-replay-1", name="IDEM_ONE", bio="原文哨兵")
    assert a1.status_code == 201 and a2.status_code == 201
    assert a1.json()["id"] == a2.json()["id"]
    after = asyncio.run(_counts())
    assert after["ai_characters"] == before["ai_characters"] + 1
    assert after["character_creation_requests"] == before["character_creation_requests"] + 1
    # 无会话/用量增量
    assert after["chat_messages"] == before["chat_messages"]
    assert after["chat_sessions"] == before["chat_sessions"]
    assert after["llm_usage"] == before["llm_usage"]

    # ③ 新独立 Session（全新 TestClient/连接）重放仍同 id
    with TestClient(_app()) as fresh:
        h2 = _auth(fresh, A)
        a3 = _post(fresh, h2, key="idem-replay-1", name="IDEM_ONE", bio="原文哨兵")
        assert a3.status_code == 201 and a3.json()["id"] == a1.json()["id"]


def test_同键不同payload_409且不新建(client, _seeded):
    h = _auth(client)
    first = _post(client, h, key="idem-conflict-1", name="CONFLICT_A")
    assert first.status_code == 201
    before = asyncio.run(_counts())
    bad = _post(client, h, key="idem-conflict-1", name="CONFLICT_B")
    assert bad.status_code == 409, bad.text
    assert "idempotency_key_payload_mismatch" in bad.text
    after = asyncio.run(_counts())
    assert after["ai_characters"] == before["ai_characters"]
    assert after["character_creation_requests"] == before["character_creation_requests"]


def test_不同键同名可各建_两用户同键互不泄漏(client, _seeded):
    h = _auth(client, A)
    r1 = _post(client, h, key="idem-same-name-1", name="SAME_NAME")
    r2 = _post(client, h, key="idem-same-name-2", name="SAME_NAME")
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"], "同 payload 不同键应允许各建"

    hb = _auth(client, B)
    rb = _post(client, hb, key="idem-shared-key", name="B_CHAR")
    ra = _post(client, h, key="idem-shared-key", name="A_CHAR")
    assert rb.status_code == 201 and ra.status_code == 201
    assert rb.json()["id"] != ra.json()["id"]
    assert int(rb.json()["user_id"]) == _seeded[B]
    assert int(ra.json()["user_id"]) == _seeded[A], "同键不能跨用户返回别人角色"
    # 各自重放仍返回自己的
    assert _post(client, hb, key="idem-shared-key", name="B_CHAR").json()["id"] == rb.json()["id"]
    assert _post(client, h, key="idem-shared-key", name="A_CHAR").json()["id"] == ra.json()["id"]


def test_并发同键_只建一个角色与一条收据(client, _seeded):
    """两个**独立会话**并发同键：结果只能有 1 角色/1 收据，绝不双建。"""
    _auth(client)
    asyncio.run(_ensure_probe_clean("idem-race-1"))

    async def one():
        async with async_session_factory() as db:
            try:
                ch = await idem.create_character_once(
                    db, _payload("RACE_ONE"), _seeded[A], "idem-race-1")
                await db.commit()
                return ("ok", ch.id)
            except Exception as e:  # 竞争失败应是非成功且可重试
                await db.rollback()
                return ("err", getattr(e, "status_code", None), str(getattr(e, "detail", "")))

    async def both():
        return await asyncio.gather(one(), one(), return_exceptions=False)

    before = asyncio.run(_counts())
    res = asyncio.run(both())
    after = asyncio.run(_counts())
    oks = [r for r in res if r[0] == "ok"]
    assert after["ai_characters"] == before["ai_characters"] + 1, f"并发双建：{res}"
    assert after["character_creation_requests"] == before["character_creation_requests"] + 1
    assert len(oks) >= 1
    if len(oks) == 2:
        assert oks[0][1] == oks[1][1], f"两次都成功但 id 不同：{res}"
    else:
        errors = [r for r in res if r[0] == "err"]
        assert errors and all(r[1] in (409, 503) for r in errors), res
        retry = _post(client, _auth(client), key="idem-race-1", name="RACE_ONE", bio="并发哨兵")
        assert retry.status_code == 201 and retry.json()["id"] == oks[0][1], retry.text


def test_唯一约束真实存在_重复插入被数据库拒绝():
    """负向对照：没有这条约束，上面的并发测试就抓不到重复——直接验约束本身。"""
    async def probe():
        async with async_session_factory() as db:
            await db.execute(text(
                "INSERT INTO character_creation_requests "
                "(user_id, idempotency_key, payload_digest, state) VALUES (:u,:k,:d,'active')"),
                {"u": _IDS[A], "k": "dup-constraint-probe", "d": "x" * 64})
            await db.commit()
            try:
                await db.execute(text(
                    "INSERT INTO character_creation_requests "
                    "(user_id, idempotency_key, payload_digest, state) VALUES (:u,:k,:d,'active')"),
                    {"u": _IDS[A], "k": "dup-constraint-probe", "d": "y" * 64})
                await db.commit()
                return False
            except IntegrityError as error:
                await db.rollback()
                assert "character_creation_requests.user_id" in str(error.orig)
                assert "character_creation_requests.idempotency_key" in str(error.orig)
                return True

    assert asyncio.run(probe()) is True, "唯一约束缺失：并发去重没有最后防线"


def test_失败回滚不消耗键_同键后续可成功(client, _seeded, monkeypatch):
    h = _auth(client)
    asyncio.run(_ensure_probe_clean("idem-rollback-1"))
    before = asyncio.run(_counts())

    real = idem.create_character

    async def boom(db, data, user_id):
        await real(db, data, user_id)   # 真实 flush 之后再炸
        raise RuntimeError("SYNTH_AFTER_FLUSH")

    monkeypatch.setattr(idem, "create_character", boom)
    with pytest.raises(RuntimeError):
        _post(client, h, key="idem-rollback-1", name="ROLLBACK_ONE")
    mid = asyncio.run(_counts())
    assert mid["ai_characters"] == before["ai_characters"], "失败请求不得留下角色"
    assert mid["character_creation_requests"] == before["character_creation_requests"], "失败请求不得消耗键"

    monkeypatch.setattr(idem, "create_character", real)
    ok = _post(client, h, key="idem-rollback-1", name="ROLLBACK_ONE")
    assert ok.status_code == 201, ok.text
    after = asyncio.run(_counts())
    assert after["ai_characters"] == before["ai_characters"] + 1


def test_非法键_空白名_未认证_均无副作用(client, _seeded):
    h = _auth(client)
    before = asyncio.run(_counts())
    # 注：HTTP 头本身不支持非 ASCII（httpx 会在发送前报 UnicodeEncodeError），
    # 故键的字符合法性只对「可实际发送」的 ASCII 值断言。
    for bad in ("", "   ", "a" * 129, "with space", "tab\tkey"):
        r = _post(client, h, key=bad, name="SHOULD_NOT_CREATE")
        assert r.status_code in (400, 422), f"非法键 {bad!r} 应被拒，实际 {r.status_code}"
    blank = _post(client, h, name="   ")
    assert blank.status_code == 422
    unauth = client.post("/api/v1/characters", json={"name": "X"}, headers={"Idempotency-Key": "k-unauth"})
    assert unauth.status_code == 401
    after = asyncio.run(_counts())
    assert after["ai_characters"] == before["ai_characters"]
    assert after["character_creation_requests"] == before["character_creation_requests"]


def test_删除目标后重放_410不复活_收据不含正文(client, _seeded):
    h = _auth(client)
    created = _post(client, h, key="idem-deleted-1", name="TO_BE_DELETED", bio="SECRET_BIO_SENTINEL")
    assert created.status_code == 201
    cid = created.json()["id"]

    async def kill():
        async with async_session_factory() as db:
            await db.execute(text("DELETE FROM ai_characters WHERE id=:c"), {"c": cid})
            await db.commit()

    asyncio.run(kill())
    before = asyncio.run(_counts())
    replay = _post(client, h, key="idem-deleted-1", name="TO_BE_DELETED", bio="SECRET_BIO_SENTINEL")
    assert replay.status_code in (409, 410), replay.text
    after = asyncio.run(_counts())
    assert after["ai_characters"] == before["ai_characters"], "不得因重放复活角色"

    # 收据只存键/摘要/归属/目标/时间，不含请求或响应正文
    async def cols():
        async with async_session_factory() as db:
            rows = (await db.execute(text("PRAGMA table_info(character_creation_requests)"))).fetchall()
            return {r[1] for r in rows}

    names = asyncio.run(cols())
    assert names == {"id", "user_id", "idempotency_key", "payload_digest",
                     "character_id", "state", "created_at", "updated_at"}, names
    assert not (names & {"name", "bio", "payload", "response", "body"})


async def _ensure_probe_clean(key: str) -> None:
    async with async_session_factory() as db:
        await db.execute(text(
            "DELETE FROM character_creation_requests WHERE idempotency_key=:k"), {"k": key})
        await db.commit()


def _payload(name: str):
    from app.schemas.character import CharacterCreate
    return CharacterCreate(name=name, bio="并发哨兵")


def test_迁移_存量库升级只加收据表且唯一约束落地(tmp_path, monkeypatch):
    """真跑 alembic：把「create_all 出来的存量库」stamp 到父版本，再 upgrade head。

    断言：只新增收据表（旧数据不动）、唯一约束/外键形态与 ORM 一致、head 收敛到本迁移。
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine as sa_create_engine, inspect as sa_inspect

    from app.config import settings
    from app.models._all import Base

    db = tmp_path / "mig.db"
    eng = sa_create_engine(f"sqlite:///{db}")
    old_tables = [t for n, t in Base.metadata.tables.items()
                  if n != "character_creation_requests"]
    Base.metadata.create_all(eng, tables=old_tables)
    with eng.begin() as conn:
        conn.execute(text("INSERT INTO users (id,username,nickname,password_hash,is_admin) "
                          "VALUES (901,'mig_u','mig_u','x',1)"))
    # ai_characters 有多个 NOT NULL 列（is_partner / cognitive_loop_enabled 等）：
    # 用 ORM 插哨兵，让模型默认值生效，避免裸 SQL 逐个猜列。
    from sqlalchemy.orm import Session as _S
    from app.models._all import AICharacter as _C
    with _S(eng) as s:
        s.add(_C(id=902, user_id=901, name="MIG_OLD_CHAR"))
        s.commit()
    eng.dispose()

    backend_dir = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db.as_posix()}")
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.stamp(cfg, "d1a2b3c4e5f6")   # 存量库：先记账到父版本
    command.upgrade(cfg, "head")          # 只重放本迁移

    eng2 = sa_create_engine(f"sqlite:///{db}")
    insp = sa_inspect(eng2)
    assert insp.has_table("character_creation_requests")
    uniques = {u["name"] for u in insp.get_unique_constraints("character_creation_requests")}
    assert "uq_char_creation_req_user_key" in uniques, uniques
    fks = {fk["referred_table"] for fk in insp.get_foreign_keys("character_creation_requests")}
    assert fks == {"users", "ai_characters"}, fks
    with eng2.connect() as conn:
        assert conn.execute(text("SELECT name FROM ai_characters WHERE id=902")).scalar_one() == "MIG_OLD_CHAR"
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    # B2b（2026-10-21）：候选 AUTOINCREMENT 迁移已接到 e2f4a6b8c0d2 之上，head 前移。
    assert rev == "b2a0c1d2e3f4", rev
    # 唯一约束真的生效
    with eng2.begin() as conn:
        conn.execute(text("INSERT INTO character_creation_requests "
                          "(user_id,idempotency_key,payload_digest,state) VALUES (901,'mig-k','d','active')"))
    try:
        with eng2.begin() as conn:
            conn.execute(text("INSERT INTO character_creation_requests "
                              "(user_id,idempotency_key,payload_digest,state) VALUES (901,'mig-k','d','active')"))
        raise AssertionError("迁移后的唯一约束未生效")
    except Exception as e:
        assert "UNIQUE" in str(e).upper() or "IntegrityError" in type(e).__name__, e
    eng2.dispose()


@pytest.mark.parametrize("bad", [" leading-key", "trailing-key ", "\tleft-key", "right-key\t"])
def test_键不允许静默删除首尾空白(client, _seeded, bad):
    before = asyncio.run(_counts())
    response = _post(client, _auth(client), key=bad, name="STRICT_KEY")
    assert response.status_code == 422, response.text
    assert asyncio.run(_counts()) == before


def test_禁用账号已有令牌带键也403且不占键(client, _seeded):
    from app.application import permission_service
    uid = _seeded[A]
    headers = _auth(client)

    async def change(value):
        async with async_session_factory() as db:
            previous = (await db.execute(text("SELECT disabled_at FROM users WHERE id=:u"), {"u": uid})).scalar_one()
            await db.execute(text("UPDATE users SET disabled_at=:v WHERE id=:u"), {"u": uid, "v": value})
            await db.commit()
            return previous

    before = asyncio.run(_counts())
    previous = asyncio.run(change("2026-01-01 00:00:00"))
    permission_service._invalidate_account_state_cache(uid)
    try:
        response = _post(client, headers, key="disabled-key", name="DISABLED_CREATE")
        assert response.status_code == 403, response.text
        assert asyncio.run(_counts()) == before
    finally:
        asyncio.run(change(previous))
        permission_service._invalidate_account_state_cache(uid)
    assert _post(client, headers, key="disabled-key", name="DISABLED_CREATE").status_code == 201


def test_完整请求摘要含十字段以外的height(client, _seeded):
    headers = _auth(client)
    original = _post(client, headers, key="whole-payload", name="FULL_FIELDS")
    assert original.status_code == 201
    before = asyncio.run(_counts())
    changed = client.post("/api/v1/characters", headers={**headers, "Idempotency-Key": "whole-payload"},
                          json={"name": "FULL_FIELDS", "height": 175})
    assert changed.status_code == 409, changed.text
    assert "idempotency_key_payload_mismatch" in changed.text
    assert asyncio.run(_counts()) == before


def test_物理删除角色留收据空目标_删除账号级联收据():
    from app.models.character import AICharacter
    from app.models.character.creation_request import CharacterCreationRequest
    from app.models.user import User

    async def probe():
        async with async_session_factory() as db:
            user = User(username="idem_fk_owner", nickname="SYNTH", password_hash="synthetic", is_admin=False)
            db.add(user)
            await db.flush()
            character = AICharacter(user_id=user.id, name="SYNTH_FK")
            db.add(character)
            await db.flush()
            uid, cid = user.id, character.id
            receipt = CharacterCreationRequest(user_id=uid, idempotency_key="fk-retain", payload_digest="x" * 64,
                                               character_id=cid, state="active")
            db.add(receipt)
            await db.commit()
        async with async_session_factory() as db:
            await db.execute(text("DELETE FROM ai_characters WHERE id=:c"), {"c": cid})
            await db.commit()
            row = (await db.execute(text("SELECT character_id,state FROM character_creation_requests WHERE user_id=:u"),
                                    {"u": uid})).one()
            assert row.character_id is None and row.state == "active"
            await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
            await db.commit()
            assert (await db.execute(text("SELECT COUNT(*) FROM character_creation_requests WHERE user_id=:u"),
                                     {"u": uid})).scalar_one() == 0
    asyncio.run(probe())
