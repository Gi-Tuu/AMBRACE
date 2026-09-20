# -*- coding: utf-8 -*-
"""A2 M0 插件归户修复测试（2026-09-20）。

三块：
- M0-1 browser_mcp：快照按 user_id 读写（读只返回本账号、缺 user_id 返回空/不写；
  唯一键已是 (user_id,url) 联合，同一网址各账号各存一行、互不覆盖）；
- M0-2 douyin_mcp：`_caller_tenant` 解析失败 None / `_get_account(None)=={}` / 跨租户目标行 404 /
  `_char_allowed` 异常 fail-closed / inject 无租户不注入；
- M0-3 内核「已禁用插件」路由闸：flag 关=逐字节旧行为，开=disabled 插件 bridge/chat/page 全 404。

隔离：全程私有临时库（pytest tmp_path）+ monkeypatch `app.db.database.async_session_factory`，
不连生产库、不写 backend/data（测试卫生纪律 2026-09-09/09-10）。
"""
import asyncio
import sys

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import plugin_bridge as bridge_api
from app.api import plugins as plugins_api
from app.auth.deps import get_current_user_id
from app.plugins import registry


# ---------------------------------------------------------------- 临时库 / 插件装载

@pytest.fixture()
def db_factory(monkeypatch, tmp_path):
    """私有临时库：monkeypatch app.db.database.async_session_factory（插件函数内延迟 import，可生效）"""
    db_path = (tmp_path / "t.db").as_posix()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册主 metadata
        from app.models.base import Base
        from app.plugins.plugin_base import plugin_metadata
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # 渠道插件自有表（douyin_*）在独立 plugin_metadata（已装载插件即已注册）
            await conn.run_sync(plugin_metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr("app.db.database.async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


@pytest.fixture()
def browser_mod():
    """装载 browser_mcp 取模块引用（与 test_browser_warmup 同口径）"""
    registry.load_plugin_dir(registry.EXAMPLE_DIR / "browser_mcp")
    mod = sys.modules.get("ai_plugin_browser_mcp")
    assert mod is not None, "browser_mcp 应可加载"
    mod._ensure_done = True  # 表已由临时库 create_all 建好，跳过插件内联 DDL
    yield mod


@pytest.fixture()
def douyin_mod():
    """装载 douyin_mcp 取模块引用（与 test_douyin_mcp_split 同口径；已装载则复用）"""
    mod = sys.modules.get("ai_plugin_douyin_mcp")
    if mod is None:
        assert registry.load_plugin_dir(registry.EXAMPLE_DIR / "douyin_mcp") is not None
        mod = sys.modules["ai_plugin_douyin_mcp"]
    yield mod


@pytest.fixture()
def dy_db(db_factory, douyin_mod):
    """抖音用例专用库（依赖 douyin_mod 先把 douyin_* 表注册进 plugin_metadata）"""
    yield db_factory


def _seed_browser_rows(factory, rows):
    from app.models.user import BrowserSnapshot

    async def _go():
        async with factory() as db:
            for uid, url, title in rows:
                db.add(BrowserSnapshot(user_id=uid, url=url, domain="x.com", title=title, text="正文"))
            await db.commit()

    asyncio.run(_go())


def _browser_rows(factory):
    from sqlalchemy import select
    from app.models.user import BrowserSnapshot

    async def _go():
        async with factory() as db:
            rs = (await db.execute(select(BrowserSnapshot).order_by(BrowserSnapshot.id.asc()))).scalars().all()
            return [(r.user_id, r.url, r.title) for r in rs]

    return asyncio.run(_go())


# ================================================================ M0-1 browser_mcp

def test_browser_最近快照只返回本账号(db_factory, browser_mod):
    _seed_browser_rows(db_factory, [(1, "https://a/1", "A1"), (2, "https://b/1", "B1"), (1, "https://a/2", "A2")])
    mine = asyncio.run(browser_mod._recent_snapshots(5, 1))
    assert [s["url"] for s in mine] == ["https://a/2", "https://a/1"]  # 本账号，id desc
    other = asyncio.run(browser_mod._recent_snapshots(5, 2))
    assert [s["url"] for s in other] == ["https://b/1"]
    assert "B1" not in "".join(s["title"] for s in mine)


def test_browser_缺user_id返回空_fail_closed(db_factory, browser_mod):
    _seed_browser_rows(db_factory, [(1, "https://a/1", "A1")])
    assert asyncio.run(browser_mod._recent_snapshots(5, None)) == []
    assert asyncio.run(browser_mod._recent_snapshots(5, 0)) == []
    assert asyncio.run(browser_mod._recent_snapshots(5, "")) == []


def test_browser_同一网址各账号各存一行互不覆盖(db_factory, browser_mod):
    asyncio.run(browser_mod._save_snapshot("https://x/same", "x.com", "T1", "正文1", [], 1))
    # 2 号账号浏览同一 url（(user_id,url) 联合唯一）→ 各存一行，绝不覆盖 1 号的行
    asyncio.run(browser_mod._save_snapshot("https://x/same", "x.com", "T2", "正文2", [], 2))
    assert _browser_rows(db_factory) == [(1, "https://x/same", "T1"), (2, "https://x/same", "T2")]
    # 本人再浏览同 url → 更新自己的行，不新增、也不碰对方那行
    asyncio.run(browser_mod._save_snapshot("https://x/same", "x.com", "T3", "正文3", [], 1))
    assert _browser_rows(db_factory) == [(1, "https://x/same", "T3"), (2, "https://x/same", "T2")]
    # 读侧各看各的
    assert [s["title"] for s in asyncio.run(browser_mod._recent_snapshots(5, 1))] == ["T3"]
    assert [s["title"] for s in asyncio.run(browser_mod._recent_snapshots(5, 2))] == ["T2"]


def test_browser_缺user_id不写快照(db_factory, browser_mod):
    asyncio.run(browser_mod._save_snapshot("https://x/none", "x.com", "T", "正文", [], None))
    assert _browser_rows(db_factory) == []


def test_browser_inject_按账号且无user_id不注入(db_factory, browser_mod, monkeypatch):
    _seed_browser_rows(db_factory, [(1, "https://a/1", "A1"), (2, "https://b/1", "B1")])

    class _FakeSDK:
        def __init__(self, cfg):
            self._cfg = cfg

        def get_config(self):
            return self._cfg

        def log(self, *a, **k):
            return None

    monkeypatch.setattr(browser_mod, "sdk", _FakeSDK({"enabled": True, "inject_minutes": 0}))
    # 本账号：注入且只含自己的快照
    browser_mod._last_inject_ts = 0.0
    ctx = {"context_messages": [], "character_id": 1, "user_id": 1}
    asyncio.run(browser_mod.inject(ctx))
    assert len(ctx["context_messages"]) == 1
    assert "A1" in ctx["context_messages"][0]["content"]
    assert "B1" not in ctx["context_messages"][0]["content"]
    # 无 user_id → 不注入
    browser_mod._last_inject_ts = 0.0
    ctx2 = {"context_messages": [], "character_id": 1}
    asyncio.run(browser_mod.inject(ctx2))
    assert ctx2["context_messages"] == []


# ================================================================ M0-2 douyin_mcp

def _seed_douyin(factory, accounts=(), pendings=(), notes=()):
    import douyin_models

    async def _go():
        async with factory() as db:
            for tid, name in accounts:
                db.add(douyin_models.DouyinAccount(
                    tenant_id=tid, bot_account_id="default", account_name=name, bound=True, logged_in=True))
            for tid, kind, status in pendings:
                db.add(douyin_models.DouyinPending(tenant_id=tid, kind=kind, status=status, title="t"))
            for tid, aweme, desc in notes:
                db.add(douyin_models.DouyinViewedNote(
                    tenant_id=tid, aweme_id=aweme, author="u", desc=desc,
                    images_urls_json="[]", image_descs_json="[]"))
            await db.commit()

    asyncio.run(_go())


def test_caller_tenant_解析失败返回None(dy_db, douyin_mod, monkeypatch):
    async def _boom(db, uid):
        raise RuntimeError("family root 不可用")

    monkeypatch.setattr("app.application.family_service.get_family_root_id", _boom)
    assert asyncio.run(douyin_mod._caller_tenant(1)) is None
    assert asyncio.run(douyin_mod._caller_tenant(None)) is None


def test_get_account_按租户且None返回空(dy_db, douyin_mod):
    _seed_douyin(dy_db, accounts=[(1, "账号A"), (2, "账号B")])
    assert asyncio.run(douyin_mod._get_account(None)) == {}  # 不再取全表第一行
    a1 = asyncio.run(douyin_mod._get_account(1))
    assert a1["account_name"] == "账号A" and a1["bound"] is True
    a2 = asyncio.run(douyin_mod._get_account(2))
    assert a2["account_name"] == "账号B"
    assert asyncio.run(douyin_mod._get_account(999)) == {"bound": False, "logged_in": False, "account_name": ""}


def test_pending_upcoming_只列本租户_无租户返回空(dy_db, douyin_mod, monkeypatch):
    _seed_douyin(dy_db, pendings=[(1, "image_post", "pending"), (2, "image_post", "pending"),
                                  (1, "image_post", "confirmed")])
    items = asyncio.run(douyin_mod.pending_list(user_id=1))["items"]
    assert len(items) == 1  # 租户 1 只有 1 条 pending（租户 2 的那条不可见）
    up = asyncio.run(douyin_mod.upcoming_list(user_id=1))["items"]
    assert len(up) == 1
    up2 = asyncio.run(douyin_mod.upcoming_list(user_id=2))["items"]
    assert up2 == []

    async def _boom(db, uid):
        raise RuntimeError("no root")

    monkeypatch.setattr("app.application.family_service.get_family_root_id", _boom)
    assert asyncio.run(douyin_mod.pending_list(user_id=1))["items"] == []
    assert asyncio.run(douyin_mod.upcoming_list(user_id=1))["items"] == []


def test_confirm_reject_跨租户404(dy_db, douyin_mod, monkeypatch):
    # 注：_random_execute_at 的 randint(30, 60) 非法分钟缺陷已修复（上界收 59，回归测试
    # test_douyin_quiet_hours.py）；此处固定执行时间只为消除随机性、隔离被测租户口径。
    from datetime import datetime as _dt, timedelta as _td
    monkeypatch.setattr(douyin_mod, "_random_execute_at",
                        lambda: _dt(2030, 1, 1, 12, 0, 0) + _td(minutes=30))
    _seed_douyin(dy_db, pendings=[(1, "image_post", "pending")])
    # 跨租户（user 2 → 租户 2）→ 404，不泄漏存在性
    with pytest.raises(HTTPException) as ei:
        asyncio.run(douyin_mod.confirm_task(1, user_id=2))
    assert ei.value.status_code == 404
    with pytest.raises(HTTPException) as ej:
        asyncio.run(douyin_mod.reject_task(1, user_id=2))
    assert ej.value.status_code == 404
    # 本租户可正常确认
    r = asyncio.run(douyin_mod.confirm_task(1, user_id=1))
    assert r["ok"] is True


def test_notes_latest_只返回本租户(dy_db, douyin_mod):
    _seed_douyin(dy_db, notes=[(1, "111", "A的图文"), (2, "222", "B的图文")])
    r1 = asyncio.run(douyin_mod.latest_notes(user_id=1))
    assert [n["aweme_id"] for n in r1["notes"]] == ["111"]
    r2 = asyncio.run(douyin_mod.latest_notes(user_id=2))
    assert [n["aweme_id"] for n in r2["notes"]] == ["222"]


def test_char_allowed_异常fail_closed(dy_db, douyin_mod, monkeypatch):
    def _boom_factory():
        raise RuntimeError("db 不可用")

    monkeypatch.setattr("app.db.database.async_session_factory", _boom_factory)
    assert asyncio.run(douyin_mod._char_allowed(1)) is False
    assert asyncio.run(douyin_mod._char_allowed_async(1)) is False


def test_inject_无租户不注入(dy_db, douyin_mod):
    _seed_douyin(dy_db, accounts=[(1, "账号A")])
    ctx = {"context_messages": [], "character_id": 1, "user_id": None}
    asyncio.run(douyin_mod.inject(ctx))
    assert ctx["context_messages"] == []


# ================================================================ M0-3 内核 disabled 路由闸

@pytest.fixture()
def gate_client(monkeypatch):
    """装载 ai_diary（有真实 page 资源）并装配 plugins/bridge 路由；默认 enabled=False"""
    assert registry.load_plugin_dir(registry.EXAMPLE_DIR / "ai_diary") is not None
    app = FastAPI()
    app.include_router(plugins_api.router)
    app.include_router(bridge_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: 1
    monkeypatch.setattr(bridge_api, "dispatch", lambda *a, **k: _aret({"ok": True}))
    yield TestClient(app)
    registry._loaded.pop("ai_diary", None)
    registry._enabled.pop("ai_diary", None)


def _aret(v):
    async def _f(*a, **k):
        return v
    return _f()


def _set_gate(monkeypatch, on: bool):
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "plugin_disabled_route_gate", on)


def test_gate_关时_bridge_page_chat行为不变(gate_client):
    """flag 默认关：disabled 插件（load_plugin_dir 未置 enabled）仍按旧逻辑走通"""
    assert gate_client.get("/api/v1/plugins/ai_diary/page/index.html").status_code == 200
    r = gate_client.post("/api/v1/plugins/ai_diary/bridge", json={"api": "store.get", "params": {"key": "k"}})
    assert r.status_code == 200
    r2 = gate_client.post("/api/v1/plugins/ai_diary/chat", json={"input": "hi"})
    assert r2.status_code == 400  # 旧行为：ai_diary 非 chat 型（400），不是 404


def test_gate_开时_disabled插件全404(gate_client, monkeypatch):
    _set_gate(monkeypatch, True)
    assert gate_client.get("/api/v1/plugins/ai_diary/page/index.html").status_code == 404
    r = gate_client.post("/api/v1/plugins/ai_diary/bridge", json={"api": "store.get", "params": {"key": "k"}})
    assert r.status_code == 404
    r2 = gate_client.post("/api/v1/plugins/ai_diary/chat", json={"input": "hi"})
    assert r2.status_code == 404


def test_gate_开时_enabled插件不受影响(gate_client, monkeypatch):
    _set_gate(monkeypatch, True)
    registry._enabled["ai_diary"] = True
    assert gate_client.get("/api/v1/plugins/ai_diary/page/index.html").status_code == 200
    r = gate_client.post("/api/v1/plugins/ai_diary/bridge", json={"api": "store.get", "params": {"key": "k"}})
    assert r.status_code == 200
