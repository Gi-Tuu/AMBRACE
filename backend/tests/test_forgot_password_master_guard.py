# -*- coding: utf-8 -*-
"""forgot-password 的主账号守卫回归（2026-09-27 修复）。

背景：原判据是物理 id==1（auth/router.py:289），多主账号 / 老库自增跳号时，
非 id=1 的根账号可被**未登录**调用 POST /auth/forgot-password 匿名重置 = 账户接管。
本测试钉住修复后的语义判据：parent_id IS NULL 且（is_admin 或 server_admin）才算主账号。

注：沙箱库整会话共享且自增 id 连续，所以「物理 id 非 1」的场景用「先占掉 id=1」来构造，
不假设具体 id 值 —— 这样与执行顺序无关。
"""
import asyncio

import bcrypt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth.router import forgot_password
from app.auth.schemas import ForgotPasswordRequest
from app.db.database import async_session_factory
from app.models.user import User


def _req():
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/forgot-password",
        "headers": [],
        "client": ("127.0.0.1", 43210),
    })


async def _seed(username, *, parent_id=None, is_admin=False, server_admin=False):
    async with async_session_factory() as db:
        u = User(
            username=username,
            nickname=username,
            password_hash=bcrypt.hashpw(b"oldpass", bcrypt.gensalt()).decode(),
            parent_id=parent_id,
            is_admin=is_admin,
            server_admin=server_admin,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


def _call(username, new_password="newpass123"):
    return asyncio.run(forgot_password(ForgotPasswordRequest(username=username, new_password=new_password), _req()))


def test_语义主账号物理_id_非一也必须被拒():
    """回归核心：根账号 + 管理员，即便物理 id 不是 1，也不允许匿名重置。"""
    async def seed():
        await _seed("consume_id_1")           # 先占掉 id=1，保证下一个不是 1
        return await _seed("master_not_one", is_admin=True)
    uid = asyncio.run(seed())
    assert uid != 1, "构造失败：本用例需要物理 id 不是 1 的根账号"
    with pytest.raises(HTTPException) as ei:
        _call("master_not_one")
    assert ei.value.status_code == 403


def test_服务器控制台管理员同样被拒():
    async def seed():
        await _seed("srv_admin_x", server_admin=True)
    asyncio.run(seed())
    with pytest.raises(HTTPException) as ei:
        _call("srv_admin_x")
    assert ei.value.status_code == 403


def test_子账号弱口令同样被拒_与注册同口径():
    """2026-09-28 用户拍板：forgot-password 也走注册口径的强度校验。"""
    async def seed():
        parent = await _seed("parent_weak", is_admin=True)
        return await _seed("child_weak", parent_id=parent)
    asyncio.run(seed())
    with pytest.raises(HTTPException) as ei:
        _call("child_weak", new_password="123456")
    assert ei.value.status_code == 400


def test_子账号可以正常重置_不误伤():
    async def seed():
        parent = await _seed("parent_ok", is_admin=True)
        return await _seed("child_ok", parent_id=parent)
    asyncio.run(seed())
    assert _call("child_ok") == {"status": "ok"}
