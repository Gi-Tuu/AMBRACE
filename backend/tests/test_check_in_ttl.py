# -*- coding: utf-8 -*-
"""查岗请求有效期（2026-09-22 P8，S8）：120s → 600s。

背景（X7 本体重估 §1 的 S8）：后台服务没跑时前端轮询不到查岗请求，120s 一到服务端
就把它作废，这次查岗永久错过。放宽到 600s，覆盖「后台服务稍后才被拉起」的场景。

边界口径（口径即本文件的验收）：**599s 未过期**（`has:true`，状态保持 pending）、
**601s 过期**（`has:false` 且该行被置 `expired`）。另有两处护栏：
- 端点必须真的引用模块级常量 `CHECK_IN_TTL_SECONDS`（改常量即生效，不再是散落的魔数）；
- 作废只作用于被判过期的那一行，且已过期行不再参与「最新 pending」查询。

库：tests/_dbclone.py 克隆临时库（tmp_path），绝不碰生产库；接口用 TestClient +
dependency_overrides 覆盖 get_current_user_id（参照 tests/test_phone_perception_idempotency.py）。

P9 追加（第 4 条）：TTL 放宽到 600s 后，防刷窗口只有 5 分钟 ⇒「5–10 分钟」区间可能并存两条
pending。故 `phone_service.request_check_in` 在真正登记前把该用户既有 pending 一律标 expired
（superseded），**同一用户至多一条 pending**；防刷窗口本身语义不变（近 5 分钟内有非 expired
请求就直接拒登记，且拒绝时不得把那条 pending 顺手标掉）。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import phone as phone_api
from app.application import phone_service
from app.auth.deps import get_current_user_id
from app.models.character import AICharacter
from app.models.device import CheckInRequest
from app.models.user import User


# 快测档：每例起一次临时库（clone_engine），属重量级/集成型用例（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow

U1 = 1              # 归属正常的账号
CID = 51            # 该账号下发起查岗的角色
URL = "/api/v1/phone/perception/check-in-request"


@pytest.fixture()
def phone_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库 + 种子（账号 / 其角色）+ patch phone API 的会话工厂。"""
    engine = clone_engine(tmp_path / "ttl.db")
    factory = make_session_factory(engine)

    async def _seed():
        async with factory() as db:
            db.add(User(id=U1, username="ttl_u1", nickname="ttl_u1", password_hash="x"))
            await db.flush()  # FK 图有环 ⇒ 先让 users 父行落库，再挂角色
            db.add(AICharacter(id=CID, user_id=U1, name="小爱", personality="友善",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_seed())
    # phone.py 在模块导入时通过 `from app.db.database import async_session_factory` 绑定引用
    monkeypatch.setattr(phone_api, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(phone_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _get(user_id: int = U1):
    return _make_client(user_id).get(URL)


async def _add_request(factory, age_seconds: float) -> int:
    """登记一条 pending 查岗请求，并把 created_at 往前挪 age_seconds 秒。"""
    created = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds)
    async with factory() as db:
        req = CheckInRequest(user_id=U1, character_id=CID, status="pending", created_at=created)
        db.add(req)
        await db.commit()
        return req.id


async def _status(factory, req_id: int) -> str:
    async with factory() as db:
        stmt = select(CheckInRequest.status).where(CheckInRequest.id == req_id)
        return (await db.execute(stmt)).scalar_one()


async def _statuses(factory) -> dict[int, str]:
    async with factory() as db:
        rows = (await db.execute(select(CheckInRequest))).scalars().all()
        return {r.id: r.status for r in rows}


async def _backdate(factory, req_id: int, age_seconds: float) -> None:
    """把某条请求的 created_at 再往前挪（模拟时间流逝，不 sleep）。"""
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=age_seconds)
    async with factory() as db:
        await db.execute(
            update(CheckInRequest).where(CheckInRequest.id == req_id).values(created_at=ts)
        )
        await db.commit()


# ── 用例 ──

def test_ttl_constant_default_is_ten_minutes():
    """S8 口径常量：600s（原 120s）。"""
    assert phone_api.CHECK_IN_TTL_SECONDS == 600


def test_endpoint_follows_ttl_constant(phone_db, monkeypatch):
    """端点读的是模块级常量而非写死的秒数：把 TTL 临时改成 10s，15s 前的请求立刻作废。"""
    monkeypatch.setattr(phone_api, "CHECK_IN_TTL_SECONDS", 10)
    stale = asyncio.run(_add_request(phone_db, 15))
    assert _get().json() == {"has": False}
    assert asyncio.run(_status(phone_db, stale)) == "expired"

    fresh = asyncio.run(_add_request(phone_db, 5))
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json() == {"has": True, "id": fresh, "character_id": CID}


def test_within_ttl_still_available(phone_db):
    """599s：未过期 → has:true + id/character_id，且状态保持 pending。"""
    req_id = asyncio.run(_add_request(phone_db, 599))
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json() == {"has": True, "id": req_id, "character_id": CID}
    assert asyncio.run(_status(phone_db, req_id)) == "pending"


def test_beyond_ttl_expires(phone_db):
    """601s：过期 → has:false，且该行被置 expired。"""
    req_id = asyncio.run(_add_request(phone_db, 601))
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json() == {"has": False}
    assert asyncio.run(_status(phone_db, req_id)) == "expired"


def test_old_120s_boundary_no_longer_expires(phone_db):
    """旧口径回归：300s（旧实现早已作废）现在仍在有效期内。"""
    req_id = asyncio.run(_add_request(phone_db, 300))
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json().get("has") is True, r.text
    assert asyncio.run(_status(phone_db, req_id)) == "pending"


def test_expired_row_stops_being_returned(phone_db):
    """过期行不再参与「最新 pending」查询，也不误伤后来登记的新鲜请求。"""
    stale = asyncio.run(_add_request(phone_db, 601))
    assert _get().json() == {"has": False}
    assert asyncio.run(_status(phone_db, stale)) == "expired"

    fresh = asyncio.run(_add_request(phone_db, 0))
    r = _get()
    assert r.status_code == 200, r.text
    assert r.json() == {"has": True, "id": fresh, "character_id": CID}
    assert asyncio.run(_status(phone_db, fresh)) == "pending"
    # 再走一轮：新鲜请求此刻也已贴近过期线，用 backdate 推进到 601s 后应被作废
    asyncio.run(_backdate(phone_db, fresh, 601))
    assert _get().json() == {"has": False}
    assert asyncio.run(_status(phone_db, fresh)) == "expired"


# ── P9 第 4 条：登记侧 pending 唯一化（supersede） ──

def test_new_request_supersedes_stale_pending(phone_db, monkeypatch):
    """6 分钟前的 pending（已过防刷窗口、仍在 TTL 内）→ 新登记把它标 expired，只剩一条 pending。"""
    monkeypatch.setattr(phone_service, "async_session_factory", phone_db)
    stale = asyncio.run(_add_request(phone_db, 360))
    assert asyncio.run(phone_service.request_check_in(U1, CID)) is True

    statuses = asyncio.run(_statuses(phone_db))
    assert statuses[stale] == "expired", "旧 pending 必须被标 expired（不再并存两条）"
    pending = [rid for rid, st in statuses.items() if st == "pending"]
    assert len(pending) == 1 and pending[0] != stale, f"同一用户至多一条 pending，实际 {statuses}"
    # 轮询侧取到的正是新那条
    assert _get().json() == {"has": True, "id": pending[0], "character_id": CID}


def test_anti_spam_window_semantics_unchanged(phone_db, monkeypatch):
    """防刷窗口未变：近 5 分钟内已有 pending → 拒绝登记，且**不得**把那条 pending 标掉。"""
    monkeypatch.setattr(phone_service, "async_session_factory", phone_db)
    fresh = asyncio.run(_add_request(phone_db, 60))
    assert asyncio.run(phone_service.request_check_in(U1, CID)) is False
    assert asyncio.run(_statuses(phone_db)) == {fresh: "pending"}
    assert _get().json() == {"has": True, "id": fresh, "character_id": CID}
