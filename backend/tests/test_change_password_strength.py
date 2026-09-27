# -*- coding: utf-8 -*-
"""P2-8 回归（2026-09-28 修复）：改密码与注册同口径做强度校验。

修复前 change_password 不校验强度（docstring 写「本地部署不设长度/字符限制」），
等于注册期策略（8-64 + 字母数字组合 + 非弱口令 + 不含用户名）可被改密码路径绕过。
"""
import asyncio

import bcrypt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.router import change_password
from app.auth.schemas import ChangePasswordRequest
from app.db.database import async_session_factory
from app.models.user import User

UID = 881
OLD = "oldpass123"


def _req():
    return Request({
        "type": "http",
        "method": "PUT",
        "path": "/api/v1/auth/password",
        "headers": [],
        "client": ("127.0.0.1", 43311),
    })


async def _seed():
    """幂等夹具：沙箱库整会话共享，重复调用只把旧密码重置回 OLD。"""
    from sqlalchemy import select
    async with async_session_factory() as db:
        row = (await db.execute(select(User).where(User.id == UID))).scalar_one_or_none()
        h = bcrypt.hashpw(OLD.encode(), bcrypt.gensalt()).decode()
        if row is None:
            db.add(User(id=UID, username="p28_user", nickname="p28_user", password_hash=h))
        else:
            row.password_hash = h
        await db.commit()


def _call(new_password, old_password=OLD):
    return asyncio.run(change_password(
        ChangePasswordRequest(old_password=old_password, new_password=new_password),
        _req(), user_id=UID,
    ))


@pytest.mark.parametrize("weak", ["12345678", "abcdefgh", "p28_user123", "123456"])
def test_弱口令被拒(weak):
    asyncio.run(_seed())
    with pytest.raises(HTTPException) as ei:
        _call(weak)
    assert ei.value.status_code == 400


def test_强口令通过且真的改了():
    asyncio.run(_seed())
    assert _call("Str0ngPass9") == {"status": "ok"}

    async def _hash():
        async with async_session_factory() as db:
            from sqlalchemy import select
            u = (await db.execute(select(User).where(User.id == UID))).scalar_one()
            return u.password_hash
    h = asyncio.run(_hash())
    assert bcrypt.checkpw(b"Str0ngPass9", h.encode())


def test_旧密码错误仍先报错():
    asyncio.run(_seed())
    with pytest.raises(HTTPException) as ei:
        _call("Str0ngPass9", old_password="wrongold1")
    assert ei.value.status_code == 400
