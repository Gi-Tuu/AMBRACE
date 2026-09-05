# -*- coding: utf-8 -*-
"""任务 A 渠道来源标记（wechat_ilink）测试：

- send_and_receive(..., channel="wechat_ilink")：monkeypatch _run_agent_core 为 async stub，
  断言传给 stub 的 kwargs 含 channel_hint="wechat_ilink"；落库用户消息 extra_meta JSON 含
  channel=wechat_ilink，AI 消息 extra_meta 亦含 channel=wechat_ilink。
- App 默认调用（不传 channel）：channel_hint=None，用户消息 extra_meta 不含 channel（兼容性回归）。
全部走临时 SQLite 文件库 + 空闲端口（不触碰 backend/data 真实库），与 test_wechat_ilink_bridge 同构。
"""
import asyncio
import json
import os
import tempfile

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.application.chat_service as cs


@pytest.fixture()
def chat_db(monkeypatch):
    """临时 SQLite 文件库：patch chat_service 绑定各模块的 async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix="wechat_origin_test_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    # chat_service 在模块顶层 `from app.db.database import async_session_factory`（导入期绑定），
    # 单 patch db_mod 不会作用到它，故对 cs 模块再 patch 一次，_persist_user_message/落库才走临时库。
    monkeypatch.setattr(cs, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _patch_send_deps(monkeypatch, core_result):
    """monkeypatch send_and_receive 的轻量依赖，聚焦「channel 透传 + extra_meta 落库」断言。"""
    calls = {}

    async def _fake_core(*_a, **_k):
        calls["core_kwargs"] = _k
        return core_result

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(cs, "_run_agent_core", _fake_core)
    monkeypatch.setattr(cs, "_push_user_notify", _noop)
    monkeypatch.setattr(cs, "_run_post_processing", _noop)
    return calls


def _fake_core_value():
    return {
        "final_state": {
            "reasoning": "", "tools_used": [], "status_update": "",
            "should_update_memory": False,
        },
        "final_text": "回复文本",
        "gen_prompt": None,
        "img_text": None,
    }


def _messages(factory, sender_type):
    from app.models.chat import ChatMessage

    async def _q():
        async with factory() as db:
            return (await db.execute(
                select(ChatMessage).where(ChatMessage.sender_type == sender_type)
            )).scalars().all()

    return asyncio.run(_q())


def _channel_from_meta(extra_meta):
    if extra_meta is None:
        return None
    try:
        data = json.loads(extra_meta)
    except Exception:
        return None
    return data.get("channel") if isinstance(data, dict) else None


def test_wechat_channel_propagates_hint_and_meta(chat_db, monkeypatch):
    """channel="wechat_ilink" → stub 收到 channel_hint；用户消息与 AI 消息 extra_meta 均含 channel。"""
    calls = _patch_send_deps(monkeypatch, _fake_core_value())
    asyncio.run(cs.send_and_receive(
        999, 13, 101, "你好呀", lang="zh", reply_delay=False, channel="wechat_ilink",
    ))

    # stub 收到的 kwargs 含 channel_hint
    assert calls["core_kwargs"]["channel_hint"] == "wechat_ilink"

    user_msgs = _messages(chat_db, "user")
    ai_msgs = _messages(chat_db, "ai")
    assert len(user_msgs) == 1 and len(ai_msgs) == 1
    # 用户消息 extra_meta JSON 含 channel=wechat_ilink（保留 quote 语义：无 quote 仅 channel）
    assert _channel_from_meta(user_msgs[0].extra_meta) == "wechat_ilink"
    # AI 消息 extra_meta 同样含 channel
    assert _channel_from_meta(ai_msgs[0].extra_meta) == "wechat_ilink"


def test_app_default_no_channel_stays_compatible(chat_db, monkeypatch):
    """App 默认调用（不传 channel）→ channel_hint=None；用户消息 extra_meta 不含 channel（兼容性回归）。"""
    calls = _patch_send_deps(monkeypatch, _fake_core_value())
    asyncio.run(cs.send_and_receive(998, 13, 101, "你好呀", lang="zh", reply_delay=False))

    assert calls["core_kwargs"].get("channel_hint") is None

    user_msgs = _messages(chat_db, "user")
    ai_msgs = _messages(chat_db, "ai")
    assert len(user_msgs) == 1 and len(ai_msgs) == 1
    # 无 quote / 无 channel：用户消息 extra_meta 保持 None（零行为变化）
    assert user_msgs[0].extra_meta is None
    assert _channel_from_meta(user_msgs[0].extra_meta) is None
    # AI 消息 _meta 无 reasoning/tools/status/channel → extra_meta 为 None
    assert ai_msgs[0].extra_meta is None
