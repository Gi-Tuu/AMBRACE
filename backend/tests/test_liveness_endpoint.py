# -*- coding: utf-8 -*-
"""运行期活性端点 (/api/v1/system/liveness) 集成测试。

- channels 段必须读 wechat_ilink_bindings 的 last_inbound_at/last_outbound_at（只读绑定表；经 DB 反射）。
- 子系统隔离：任一子系统异常只落到对应段 _error，端点绝不 500。
- loops 段 stalled 正确驱动整体 stalled。
- 用临时 SQLite + DDL 新建 binding 表，不触碰全局 Base.metadata，避免污染其他用例。
"""
import asyncio
import os
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import system as system_api
from app.models.mcp import MCPServer

# 只建 liveness 端点读取所需的表结构；独立于全局 Base.metadata / 渠道插件加载
_BINDING_DDL = """
CREATE TABLE wechat_ilink_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  tenant_id INTEGER NOT NULL DEFAULT 0,
  bot_account_id VARCHAR(128) NOT NULL DEFAULT 'default',
  character_id INTEGER NOT NULL,
  ilink_user_id VARCHAR(128) DEFAULT '',
  ilink_bot_id VARCHAR(128) DEFAULT '',
  bot_token_enc TEXT DEFAULT '',
  baseurl VARCHAR(255) DEFAULT '',
  poll_buf TEXT DEFAULT '',
  window_started_at DATETIME,
  out_count_in_window INTEGER DEFAULT 0,
  enabled BOOLEAN DEFAULT 1,
  bound_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_inbound_at DATETIME,
  last_outbound_at DATETIME
)
"""


def _now_naive_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def liveness_db(monkeypatch, tmp_path):
    """临时 SQLite（空闲端口）+ patch app.db.database.async_session_factory（不触碰 backend/data）。"""
    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    import app.models  # noqa: F401  确保全部内核模型（含 mcp_servers）注册进 metadata
    from app.models.base import Base

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # 全局 metadata 可能已被渠道插件模型注册（其他用例 load wechat_ilink 后 create_all
            # 会一并建出模型版绑定表）；统一重建为测试 DDL 形态，列默认齐备且与插件模型解耦。
            await conn.execute(text("DROP TABLE IF EXISTS wechat_ilink_bindings"))
            await conn.execute(text(_BINDING_DDL))

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _add_binding(factory, character_id=1, enabled=True, last_in=None, last_out=None, bot="default"):
    """插入一行 wechat_ilink_bindings（只写临时库；不触碰全局 metadata）。"""
    async def _do():
        async with factory() as db:
            await db.execute(
                text(
                    "INSERT INTO wechat_ilink_bindings "
                    "(user_id, tenant_id, bot_account_id, character_id, ilink_user_id, enabled, "
                    " last_inbound_at, last_outbound_at) "
                    "VALUES (1, 1, :bot, :char, :wx, :enabled, :last_in, :last_out)"
                ),
                {"bot": bot, "char": character_id, "wx": f"wx_{character_id}",
                 "enabled": enabled, "last_in": last_in, "last_out": last_out},
            )
            await db.commit()

    asyncio.run(_do())


def _make_client():
    app = FastAPI()
    app.include_router(system_api.router)
    return TestClient(app)


def test_liveness_reads_bindings(liveness_db):
    """channels 段读绑定表 last_inbound_at/last_outbound_at。"""
    now = _now_naive_utc()
    _add_binding(liveness_db, character_id=7, enabled=True, last_in=now, last_out=now)
    _add_binding(liveness_db, character_id=8, enabled=False, last_in=None, last_out=None)

    r = _make_client().get("/api/v1/system/liveness")
    assert r.status_code == 200
    j = r.json()
    assert j["stalled"] is False
    ch = j["channels"]["wechat_ilink"]
    assert ch["binding_count"] == 2
    by_char = {b["character_id"]: b for b in ch["bindings"]}
    b7 = by_char[7]
    assert b7["enabled"] is True
    assert b7["seconds_since_inbound"] is not None and b7["seconds_since_inbound"] >= 0
    assert b7["last_inbound_at"] is not None and b7["last_outbound_at"] is not None
    b8 = by_char[8]
    assert b8["enabled"] is False
    assert b8["last_inbound_at"] is None and b8["seconds_since_inbound"] is None
    # mcp（无 auto_connect server）与 loops（supervisor 无登记目标）应为空/零
    assert j["mcp"]["expected"] == 0 and j["mcp"]["connected"] == 0
    assert j["loops"] == {}


def test_liveness_mcp_down_ids_reported(liveness_db):
    """mcp 段：auto_connect+enabled 但未连接 → down_ids 列出。"""
    async def _seed():
        async with liveness_db() as db:
            db.add(MCPServer(user_id=1, name="srv_down", transport="stdio", command="echo",
                             auto_connect=True, enabled=True))
            await db.commit()
    asyncio.run(_seed())

    r = _make_client().get("/api/v1/system/liveness")
    assert r.status_code == 200
    mcp = r.json()["mcp"]
    assert mcp["expected"] == 1
    assert mcp["connected"] == 0
    assert mcp["down_ids"] == [1]
    assert "auto-recovered" in mcp.get("note", "")


def test_liveness_sub_system_isolation(liveness_db, monkeypatch):
    """子系统隔离：DB 探活抛异常 → mcp/channels 落 _error，端点仍 200，loops 不受影响。"""
    def _boom_factory():
        raise RuntimeError("boom db")

    # 让端点访问的 async_session_factory 抛错（mcp / channels 两段都会命中）
    monkeypatch.setattr("app.db.database.async_session_factory", _boom_factory)
    r = _make_client().get("/api/v1/system/liveness")
    assert r.status_code == 200
    j = r.json()
    assert "_error" in j["mcp"]
    assert "_error" in j["channels"]
    assert "loops" in j  # loops 段独立于 DB，不受影响
    assert j["stalled"] in (True, False)


def test_liveness_stalled_from_loops(monkeypatch):
    """loops 段负责整体 stalled：登记一个陈旧（stalled）目标 → 整体 stalled=True。"""
    import time as _t
    import app.utils.supervisor as sv_mod

    # 让 mcp/channels 子系统快速失败（隔离），聚焦 loops/stalled
    def _boom_factory():
        raise RuntimeError("no db")
    monkeypatch.setattr("app.db.database.async_session_factory", _boom_factory)

    s = sv_mod.TaskSupervisor()

    async def _run_forever():
        await asyncio.Event().wait()

    async def scenario():
        s.register("scheduler", _run_forever, stall_sec=10)
        s.start()
        # 直接改写为陈旧心跳（monotonic），使其 stalled
        s._targets["scheduler"].last_beat = _t.monotonic() - 100
        monkeypatch.setattr(sv_mod, "supervisor", s)

        r = _make_client().get("/api/v1/system/liveness")
        assert r.status_code == 200
        j = r.json()
        assert j["loops"]["scheduler"]["stalled"] is True
        assert j["stalled"] is True

        await s.stop()

    asyncio.run(scenario())
