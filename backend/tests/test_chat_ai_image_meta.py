# -*- coding: utf-8 -*-
"""AI 生图消息标注（P0，2026-09-10）：io.py 两处改动的针对性回归。

覆盖：
1. `_append_ai_image_message` 落库 extra_meta 含 kind=ai_image / tools=["生图"] /
   gen_image=True / prompt 保留（前端据此始终显示「AI 生图」类型角标）；
2. 配文（IMG_TEXT）允许为空：content=None → 落库空串，不再是「给你画好啦～」；
   content 原样保留并截断 60 字；
3. `_push_ws_ai_message` 的 WS payload data 带非空 extra_meta（monkeypatch
   push_to_session 捕获），保证实时上屏与 REST 历史表现一致。

临时 SQLite 走 conftest 会话级沙箱库（不自建临时目录）。
"""
import asyncio
import json

from sqlalchemy import select

from app.application.chat.io import _append_ai_image_message, _push_ws_ai_message
from app.db.database import async_session_factory
from app.models.chat import ChatMessage, ChatSession
from app.models.character import AICharacter
from app.models.user import User

_USER_ID = 9501
_CHAR_ID = 9502


async def _seed_session() -> int:
    """造一个最小会话（用户 + 角色 + 会话），返回 session_id。"""
    async with async_session_factory() as db:
        db.add(User(id=_USER_ID, username="img_meta_u", nickname="生图标注用户"))
        db.add(AICharacter(id=_CHAR_ID, user_id=_USER_ID, name="生图标注角色"))
        await db.flush()
        sess = ChatSession(user_id=_USER_ID, character_id=_CHAR_ID)
        db.add(sess)
        await db.flush()
        await db.commit()
        return sess.id


async def _cleanup(session_id: int) -> None:
    async with async_session_factory() as db:
        for row in (await db.execute(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        )).scalars().all():
            await db.delete(row)
        sess = await db.get(ChatSession, session_id)
        if sess is not None:
            await db.delete(sess)
        char = await db.get(AICharacter, _CHAR_ID)
        if char is not None:
            await db.delete(char)
        user = await db.get(User, _USER_ID)
        if user is not None:
            await db.delete(user)
        await db.commit()


def test_append_ai_image_message_meta_and_caption():
    async def _run():
        session_id = await _seed_session()
        try:
            await _append_ai_image_message(
                session_id, "/uploads/gen_1.png", "一只橘猫在窗台晒太阳",
                content="……就这一张。",
            )
            async with async_session_factory() as db:
                msg = (await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id.desc())
                )).scalars().first()
                assert msg is not None, "生图消息未落库"
                assert msg.sender_type == "ai"
                assert msg.image_url == "/uploads/gen_1.png"
                assert msg.content == "……就这一张。"
                assert msg.extra_meta, "extra_meta 不应为空"
                meta = json.loads(msg.extra_meta)
                assert meta["kind"] == "ai_image"
                assert meta["tools"] == ["生图"]
                assert meta["gen_image"] is True
                assert meta["prompt"] == "一只橘猫在窗台晒太阳"
                return msg.id
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())


def test_append_ai_image_message_caption_empty_when_none():
    async def _run():
        session_id = await _seed_session()
        try:
            # LLM 未产出 IMG_TEXT：配文落空串，不再兜底「给你画好啦～」
            await _append_ai_image_message(
                session_id, "/uploads/gen_2.png", "赛博城市夜景", content=None,
            )
            async with async_session_factory() as db:
                msg = (await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id.desc())
                )).scalars().first()
                assert msg is not None
                assert msg.content == "", f"空配文应落空串，实际={msg.content!r}"
                assert "给你画好啦" not in (msg.content or "")
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())


def test_append_ai_image_message_caption_truncated_to_60():
    async def _run():
        session_id = await _seed_session()
        try:
            long_caption = "喵" * 80
            await _append_ai_image_message(
                session_id, "/uploads/gen_3.png", "长配文截断", content=long_caption,
            )
            async with async_session_factory() as db:
                msg = (await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id.desc())
                )).scalars().first()
                assert msg is not None
                assert len(msg.content) == 60, f"配文应截断到 60 字，实际 {len(msg.content)}"
        finally:
            await _cleanup(session_id)

    asyncio.run(_run())


def test_push_ws_ai_message_payload_carries_extra_meta(monkeypatch):
    """WS 实时上屏 data 必须带 extra_meta（否则实时路径拿不到 kind/tools/gen_image）。"""
    import app.ws.connection_manager as cm

    captured = []

    async def _fake_push(session_id, payload):
        captured.append((session_id, payload))
        return True

    monkeypatch.setattr(cm, "push_to_session", _fake_push)

    async def _run():
        session_id = await _seed_session()
        try:
            await _append_ai_image_message(
                session_id, "/uploads/gen_4.png", "水墨山水", content="给你画好了",
            )
            async with async_session_factory() as db:
                return (await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id.desc())
                )).scalars().first()
        finally:
            pass

    msg = asyncio.run(_run())
    try:
        asyncio.run(_push_ws_ai_message(msg.session_id, msg))
        assert captured, "未捕获到 WS 推送"
        _, payload = captured[-1]
        assert payload["type"] == "ai_response"
        data = payload["data"]
        assert data["image_url"] == "/uploads/gen_4.png"
        assert data["extra_meta"], "WS data 缺少 extra_meta"
        meta = json.loads(data["extra_meta"])
        assert meta["kind"] == "ai_image"
        assert meta["tools"] == ["生图"]
        assert meta["gen_image"] is True
        assert meta["prompt"] == "水墨山水"
    finally:
        asyncio.run(_cleanup(msg.session_id))
