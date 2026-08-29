# -*- coding: utf-8 -*-
"""主账号管理家庭范围隔离测试（#68 修订，2026-08-28）。

覆盖：
- 主账号列表只看自己家庭（自己 + 直属子账号）；独立主账号只看自己；
- 跨家庭账号 403；禁操作自己（400）；
- 主账号可给子账号授予/取消 admin；子账号（非 admin）访问主账号管理 403；
- 关联受邀码后子账号 is_admin=False；解除关联后恢复 is_admin=True。
"""
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import account as account_api
from app.api import admin as admin_api
from app.auth.deps import get_current_user_id
from app.models.user import User
from app.services import permission_service as perm


@pytest.fixture()
def family_db(monkeypatch):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix='family_admin_test_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as db:
            # 3 个独立主账号（模拟独立化后的状态：parent_id IS NULL → is_admin=1）
            db.add(User(id=1, username='u1', nickname='甲', is_admin=True))
            db.add(User(id=2, username='u2', nickname='乙', is_admin=True))
            db.add(User(id=3, username='u3', nickname='丙', is_admin=True))
            # 1 个子账号（parent_id=3 → is_admin=0）
            db.add(User(id=4, username='u4', nickname='丁', is_admin=False, parent_id=3))
            await db.commit()

    asyncio.run(_init())
    monkeypatch.setattr(admin_api, 'async_session_factory', factory)   # admin.py 绑定的引用
    monkeypatch.setattr(perm, 'async_session_factory', factory)        # permission_service 绑定的引用
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    """清理 permission_service._admin_cache，避免跨用例污染/缓存命中干扰。"""
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    app.include_router(account_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def test_admin_list_only_family_members(family_db):
    """主账号只能看到自己 + 子账号，看不到其他家庭。"""
    client = _make_client(3)
    r = client.get('/api/v1/admin/accounts')
    assert r.status_code == 200, r.text
    ids = {a['id'] for a in r.json()['accounts']}
    assert ids == {3, 4}, f'应只看到自己(3)+子账号(4)，实际 {ids}'


def test_admin_list_independent_sees_only_self(family_db):
    """无子女的独立主账号只看到自己。"""
    client = _make_client(1)
    r = client.get('/api/v1/admin/accounts')
    assert r.status_code == 200, r.text
    ids = {a['id'] for a in r.json()['accounts']}
    assert ids == {1}, f'独立主账号只应看到自己，实际 {ids}'
    assert r.json()['accounts'][0]['is_self'] is True


def test_admin_cannot_toggle_other_family(family_db):
    """主账号不能操作其他家庭的账号。"""
    client = _make_client(3)
    # 尝试给账号 1（其他家庭）授予权限 → 403
    r = client.put('/api/v1/admin/accounts/1/admin', json={'enabled': True})
    assert r.status_code == 403, r.text


def test_admin_cannot_toggle_self(family_db):
    """不能修改自己的 admin 状态。"""
    client = _make_client(3)
    r = client.put('/api/v1/admin/accounts/3/admin', json={'enabled': False})
    assert r.status_code == 400, r.text


def test_admin_can_toggle_sub_account(family_db):
    """主账号可以给子账号授予/取消 admin。"""
    client = _make_client(3)
    r = client.put('/api/v1/admin/accounts/4/admin', json={'enabled': True})
    assert r.status_code == 200, r.text
    assert r.json()['is_admin'] is True

    r2 = client.put('/api/v1/admin/accounts/4/admin', json={'enabled': False})
    assert r2.status_code == 200, r2.text
    assert r2.json()['is_admin'] is False


def test_sub_account_cannot_access_admin_list(family_db):
    """子账号（is_admin=False）不能访问主账号管理。"""
    client = _make_client(4)
    r = client.get('/api/v1/admin/accounts')
    assert r.status_code == 403, r.text


def test_linking_sets_sub_admin_false(family_db):
    """通过受邀码关联后，子账号 is_admin 被设为 False。"""
    from app.services.family_service import generate_invite_code, redeem_invite_code

    async def _link():
        async with family_db() as db:
            # 账号 1 生成受邀码
            res = await generate_invite_code(db, 1)
            code = res['code']
            # 账号 2 兑换（账号 2 当前 is_admin=True）
            await redeem_invite_code(db, 2, code)
            await db.commit()
            u2 = (await db.execute(select(User).where(User.id == 2))).scalar_one()
            assert u2.parent_id == 1
            assert u2.is_admin is False

    asyncio.run(_link())


def test_unlink_restores_admin(family_db):
    """解除关联后，子账号恢复 is_admin=True。"""
    from app.services.family_service import unlink

    async def _unlink():
        async with family_db() as db:
            await unlink(db, 3, 4)  # 主账号 3 踢出子账号 4
            await db.commit()
            u4 = (await db.execute(select(User).where(User.id == 4))).scalar_one()
            assert u4.parent_id is None
            assert u4.is_admin is True

    asyncio.run(_unlink())
