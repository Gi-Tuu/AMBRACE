# -*- coding: utf-8 -*-
"""批 G（2026-09-26）：小手机日历备注 / 备忘录「已完成 / 已过期」标记。

覆盖：注入文本标记（G2）、PATCH 端点（G3）、AI 标记动作（G4）。
临时库走 _dbclone.clone_engine（当前 ORM 全量 schema，含 status 列），不跑 alembic。
参照 tests/test_phone_perception_idempotency.py 的 fixture / _make_client 写法。
"""
import asyncio
from datetime import timedelta

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

import app.db.database as dbmod
import app.api.phone_desktop as pd_api
import app.application.chat.tools as chat_tools
from app.application.phone_desktop_service import get_phone_desktop_inject_text
from app.auth.deps import get_current_user_id
from app.models.character import AICharacter
from app.models.device import CalendarNote, MemoNote
from app.models.user import User
from app.utils.timeutil import app_local_now

# 每例起一次临时库（clone_engine），属重量级/集成型用例。
pytestmark = pytest.mark.slow

_CONSTRAINT = "禁止再当成待办催办"


@pytest.fixture()
def factory(monkeypatch, tmp_path):
    """临时文件库：把注入服务 / 端点 / AI 工具三处 async_session_factory 都指过来。"""
    db_path = tmp_path / "g.db"
    engine = clone_engine(db_path)
    fac = make_session_factory(engine)
    # phone_desktop_service 在函数内惰性 import，读 app.db.database 的当前属性；
    # pd_api / chat_tools 在模块顶部已绑定引用，需各自 monkeypatch 覆盖。
    monkeypatch.setattr(dbmod, "async_session_factory", fac)
    monkeypatch.setattr(pd_api, "async_session_factory", fac)
    monkeypatch.setattr(chat_tools, "async_session_factory", fac)
    yield fac
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(pd_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _mk_char(fac, uid: int, name: str = "小艾") -> int:
    async with fac() as db:
        if (await db.get(User, uid)) is None:
            db.add(User(id=uid, username=f"u{uid}", nickname=f"n{uid}"))
            await db.flush()
        ch = AICharacter(user_id=uid, name=name)
        db.add(ch)
        await db.commit()
        return ch.id


async def _mk_cal(fac, cid: int, date: str, text: str, status: str = "active") -> int:
    async with fac() as db:
        row = CalendarNote(character_id=cid, note_date=date, note_text=text, status=status, author="我")
        db.add(row)
        await db.commit()
        return row.id


async def _mk_memo(fac, cid: int, text: str, status: str = "active") -> int:
    async with fac() as db:
        row = MemoNote(character_id=cid, text=text, status=status, author="我")
        db.add(row)
        await db.commit()
        return row.id


async def _cal_status(fac, note_id: int) -> str:
    async with fac() as db:
        return (await db.get(CalendarNote, note_id)).status


async def _memo_status(fac, memo_id: int) -> str:
    async with fac() as db:
        return (await db.get(MemoNote, memo_id)).status


# ── G2 注入标记 ──

def test_inject_calendar_markers(factory):
    """日历：昨天 active→[已过期]、今天 active→[今天]、明天 active→[未来]、done→[已完成]。"""
    today = app_local_now().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    cid = asyncio.run(_mk_char(factory, 1))
    asyncio.run(_mk_cal(factory, cid, yesterday, "昨天的事"))
    asyncio.run(_mk_cal(factory, cid, today.isoformat(), "今天的事"))
    asyncio.run(_mk_cal(factory, cid, tomorrow, "明天的事"))
    asyncio.run(_mk_cal(factory, cid, today.isoformat(), "完成的事", status="done"))
    out = asyncio.run(get_phone_desktop_inject_text(cid))
    assert "[已过期] 昨天的事" in out
    assert "[今天] 今天的事" in out
    assert "[未来] 明天的事" in out
    assert "[已完成] 完成的事" in out


def test_inject_memo_markers(factory):
    """备忘录：done→[已完成]、active→[待办]。"""
    cid = asyncio.run(_mk_char(factory, 2))
    asyncio.run(_mk_memo(factory, cid, "买牛奶"))
    asyncio.run(_mk_memo(factory, cid, "交房租", status="done"))
    out = asyncio.run(get_phone_desktop_inject_text(cid))
    assert "[待办] 买牛奶" in out
    assert "[已完成] 交房租" in out


def test_inject_constraint_on_both_chains(factory):
    """日历链、备忘录链段末都带「禁止再当成待办催办」硬约束。"""
    today = app_local_now().date()
    cal_cid = asyncio.run(_mk_char(factory, 3, name="日历娘"))
    asyncio.run(_mk_cal(factory, cal_cid, today.isoformat(), "有日历事项"))
    memo_cid = asyncio.run(_mk_char(factory, 4, name="备忘娘"))
    asyncio.run(_mk_memo(factory, memo_cid, "有备忘事项"))
    assert _CONSTRAINT in asyncio.run(get_phone_desktop_inject_text(cal_cid))
    assert _CONSTRAINT in asyncio.run(get_phone_desktop_inject_text(memo_cid))


# ── G3 PATCH 端点 ──

def test_patch_calendar_note_status(factory):
    cid = asyncio.run(_mk_char(factory, 5))
    nid = asyncio.run(_mk_cal(factory, cid, app_local_now().date().isoformat(), "开会"))
    client = _make_client(5)

    ok = client.patch(f"/api/v1/phone-desktop/calendar-notes/{nid}", json={"status": "done"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "done"
    assert asyncio.run(_cal_status(factory, nid)) == "done"

    reopen = client.patch(f"/api/v1/phone-desktop/calendar-notes/{nid}", json={"status": "active"})
    assert reopen.status_code == 200, reopen.text
    assert reopen.json()["status"] == "active"

    bad = client.patch(f"/api/v1/phone-desktop/calendar-notes/{nid}", json={"status": "bogus"})
    assert bad.status_code == 400

    missing = client.patch("/api/v1/phone-desktop/calendar-notes/999999", json={"status": "done"})
    assert missing.status_code == 404


def test_patch_memo_status_and_ownership(factory):
    # 用户 6 的角色备忘录：用户 7 越权改 → 404；本人改 → 200
    owner_cid = asyncio.run(_mk_char(factory, 6))
    mid = asyncio.run(_mk_memo(factory, owner_cid, "私事"))
    other_client = _make_client(7)
    assert other_client.patch(f"/api/v1/phone-desktop/memos/{mid}", json={"status": "done"}).status_code == 404

    owner_client = _make_client(6)
    r = owner_client.patch(f"/api/v1/phone-desktop/memos/{mid}", json={"status": "done"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done"
    assert asyncio.run(_memo_status(factory, mid)) == "done"


def test_list_endpoints_carry_status(factory):
    """两个 GET 列表端点返回体补 status 字段。"""
    cid = asyncio.run(_mk_char(factory, 8))
    asyncio.run(_mk_cal(factory, cid, app_local_now().date().isoformat(), "带状态", status="done"))
    asyncio.run(_mk_memo(factory, cid, "备忘状态", status="done"))
    client = _make_client(8)
    cal = client.get("/api/v1/phone-desktop/calendar-notes", params={"character_id": cid})
    assert cal.status_code == 200, cal.text
    assert any(n["id"] and n.get("status") == "done" for n in cal.json()["notes"])
    memos = client.get("/api/v1/phone-desktop/memos", params={"character_id": cid})
    assert memos.status_code == 200, memos.text
    assert any(m.get("status") == "done" for m in memos.json()["items"])


# ── G4 AI 标记动作 ──

def test_ai_mark_note_status_hits_and_misses(factory):
    cid = asyncio.run(_mk_char(factory, 9))
    nid = asyncio.run(_mk_cal(factory, cid, app_local_now().date().isoformat(), "和客户开会"))
    # 按文本片段命中 → 置 done
    hit = asyncio.run(chat_tools._mark_note_status(cid, "calendar", "开会", "done"))
    assert hit["ok"] is True
    assert hit["matched"] == 1
    assert asyncio.run(_cal_status(factory, nid)) == "done"
    # 命中不到 → 明确失败（不静默成功）
    miss = asyncio.run(chat_tools._mark_note_status(cid, "calendar", "根本没有这段文字", "done"))
    assert miss["ok"] is False
    # 只能改自己角色的行：另一角色的同名片段命不中
    other_cid = asyncio.run(_mk_char(factory, 10, name="别人"))
    cross = asyncio.run(chat_tools._mark_note_status(other_cid, "calendar", "开会", "done"))
    assert cross["ok"] is False
