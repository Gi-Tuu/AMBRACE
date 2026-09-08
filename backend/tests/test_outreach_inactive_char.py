# -*- coding: utf-8 -*-
"""B1-③ 非活跃角色停发 + 配额让位（2026-09-08，用户拍板）。

覆盖要点：
- 纯函数 ``outreach.skip_inactive_char`` 边界：0 消息（None）停发 / 窗口内不停发 /
  超窗口停发 / 窗口 <=0 恒不停发（回退）；
- ``arbiter.inactive_char_skip``：flag 开 + 0 消息角色 → 停发；flag 开 + 活跃角色 → 照常；
  flag 关 → 恒不停发（一键回退）；
- ``arbiter.get_hours_since_last_user_message``（临时库）：无任何消息 → None；
  只有 AI 消息 → None；有用户消息 → 约 N 小时。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；DB 用例走临时 SQLite，不碰真实库。）
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.domain.proactivity import outreach as oc
from app.scheduling import arbiter


# ── 纯函数边界 ──

def test_零消息角色_停发():
    assert oc.skip_inactive_char(None) is True


def test_窗口内有用户消息_不停发():
    assert oc.skip_inactive_char(0.0) is False
    assert oc.skip_inactive_char(1.5) is False
    assert oc.skip_inactive_char(23.9) is False
    assert oc.skip_inactive_char(24.0) is False  # 边界：等于窗口算活跃（> 窗口才停发）


def test_超出窗口_停发():
    assert oc.skip_inactive_char(24.1) is True
    assert oc.skip_inactive_char(72.0) is True


def test_窗口置零_恒不停发_可回退():
    assert oc.skip_inactive_char(None, window_hours=0) is False
    assert oc.skip_inactive_char(999.0, window_hours=0) is False
    assert oc.skip_inactive_char(None, window_hours=-1) is False


def test_默认窗口为24小时():
    assert oc.INACTIVE_CHAR_WINDOW_HOURS == 24.0


# ── arbiter 门控（flag + 判据接线）──

@pytest.fixture()
def _flag():
    from app.agent.loop import AGENT_FLAGS

    old = AGENT_FLAGS.get("proactive_inactive_char_skip")
    yield AGENT_FLAGS
    if old is None:
        AGENT_FLAGS.pop("proactive_inactive_char_skip", None)
    else:
        AGENT_FLAGS["proactive_inactive_char_skip"] = old


def test_门控_非活跃角色停发(_flag, monkeypatch):
    _flag["proactive_inactive_char_skip"] = True

    async def _no_msg(_cid):
        return None

    monkeypatch.setattr(arbiter, "get_hours_since_last_user_message", _no_msg)
    assert asyncio.run(arbiter.inactive_char_skip(18)) is True


def test_门控_活跃角色照常(_flag, monkeypatch):
    _flag["proactive_inactive_char_skip"] = True

    async def _recent(_cid):
        return 1.0

    monkeypatch.setattr(arbiter, "get_hours_since_last_user_message", _recent)
    assert asyncio.run(arbiter.inactive_char_skip(13)) is False


def test_门控_开关可回退(_flag, monkeypatch):
    _flag["proactive_inactive_char_skip"] = False

    async def _no_msg(_cid):
        return None

    monkeypatch.setattr(arbiter, "get_hours_since_last_user_message", _no_msg)
    assert asyncio.run(arbiter.inactive_char_skip(18)) is False


def test_门控_查询异常_fail_open不停发(_flag, monkeypatch):
    _flag["proactive_inactive_char_skip"] = True

    async def _boom(_cid):
        raise RuntimeError("db down")

    monkeypatch.setattr(arbiter, "get_hours_since_last_user_message", _boom)
    assert asyncio.run(arbiter.inactive_char_skip(13)) is False


# ── 最近用户消息查询（临时库）──

@pytest.fixture()
def tmp_db(monkeypatch):
    """临时 SQLite：create_all 全模型 + 把 arbiter 的 session factory 指向临时工厂。"""
    tmp = tempfile.mkdtemp(prefix="inactive_char_")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(arbiter, "async_session_factory", factory)
    return factory


def test_查最近用户消息_无任何消息返回None(tmp_db):
    from app.models.chat import ChatSession

    async def _run():
        async with tmp_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=18))
            await db.commit()
        return await arbiter.get_hours_since_last_user_message(18)

    assert asyncio.run(_run()) is None


def test_查最近用户消息_只有AI消息返回None(tmp_db):
    from app.models.chat import ChatMessage, ChatSession

    async def _run():
        async with tmp_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=18))
            db.add(ChatMessage(session_id=1, sender_type="ai", content="在干嘛"))
            await db.commit()
        return await arbiter.get_hours_since_last_user_message(18)

    assert asyncio.run(_run()) is None


def test_查最近用户消息_跨会话统计(tmp_db):
    from app.models.chat import ChatMessage, ChatSession

    async def _run():
        async with tmp_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=13))
            db.add(ChatSession(id=2, user_id=2, character_id=13))
            _old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=30)
            db.add(ChatMessage(session_id=1, sender_type="user", content="早", created_at=_old))
            _now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
            db.add(ChatMessage(session_id=2, sender_type="user", content="在吗", created_at=_now))
            await db.commit()
        return await arbiter.get_hours_since_last_user_message(13)

    got = asyncio.run(_run())
    assert got is not None and 1.9 <= got <= 2.1


def test_查最近用户消息_不串角色(tmp_db):
    from app.models.chat import ChatMessage, ChatSession

    async def _run():
        async with tmp_db() as db:
            db.add(ChatSession(id=1, user_id=1, character_id=13))
            db.add(ChatSession(id=2, user_id=1, character_id=18))
            _now = datetime.now(timezone.utc).replace(tzinfo=None)
            db.add(ChatMessage(session_id=1, sender_type="user", content="在吗", created_at=_now))
            await db.commit()
        return await arbiter.get_hours_since_last_user_message(18)

    assert asyncio.run(_run()) is None
