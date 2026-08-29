# -*- coding: utf-8 -*-
"""#68 P3 账号关联（family）测试：
- 生成/复用受邀码（仅独立主账号；子账号 403；复用未过期码）
- 兑换成功/过期/一次性；并发 used_by 防重
- 主账号子账号 ≤6
- 解除关联（主账号踢人 / 子账号自我解除）
- 环状/自我/重复拒绝
- profile 返回 parent_id/is_sub；admin 账号列表返回 parent_id
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import account as account_api
from app.api import admin as admin_api
from app.auth import router as auth_router
from app.auth.deps import get_current_user_id
from app.services import permission_service as perm


@pytest.fixture()
def family_db(monkeypatch):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix='family_test_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, 'async_session_factory', factory)
    monkeypatch.setattr(auth_router, 'async_session_factory', factory)  # auth/router.py 绑定的引用
    monkeypatch.setattr(admin_api, 'async_session_factory', factory)   # admin.py 绑定的引用
    monkeypatch.setattr(perm, 'async_session_factory', factory)        # permission_service 绑定的引用
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
    app.include_router(account_api.router)
    app.include_router(auth_router.router)
    app.include_router(admin_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


async def _add_user(factory, uid: int, username: str, parent_id: int | None = None, is_admin: bool = False):
    async with factory() as db:
        from app.models.user import User
        db.add(User(id=uid, username=username, nickname=f'n{uid}', parent_id=parent_id, is_admin=is_admin))
        await db.commit()


async def _seed_invite(factory, code: str, creator_id: int, expires_at: datetime, used_by: int | None = None):
    async with factory() as db:
        from app.models.user.account_invite import AccountInvite
        db.add(AccountInvite(code=code, creator_id=creator_id, expires_at=expires_at, used_by=used_by))
        await db.commit()


async def _get_user(factory, uid: int):
    from app.db.database import async_session_factory
    async with async_session_factory() as db:
        from app.models.user import User
        u = await db.get(User, uid)
        return u.parent_id


# ── 生成 / 复用 ──

def test_generate_and_reuse_invite(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    client = _make_client(1)
    r = client.post('/api/v1/account/invite-code')
    assert r.status_code == 200, r.text
    code = r.json()['code']
    assert len(code) == 8 and code.isalnum()

    # 复用未过期码
    r2 = client.post('/api/v1/account/invite-code')
    assert r2.status_code == 200
    assert r2.json()['code'] == code


def test_sub_account_cannot_generate(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    client = _make_client(2)
    r = client.post('/api/v1/account/invite-code')
    assert r.status_code == 403, r.text


# ── 兑换 ──

def test_redeem_success(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub'))
    code = asyncio.run(_generate_code(family_db, 1))
    client = _make_client(2)
    r = client.post('/api/v1/account/link', json={'code': code})
    assert r.status_code == 200, r.text
    assert r.json()['root_id'] == 1
    assert asyncio.run(_get_user(family_db, 2)) == 1


async def _generate_code(factory, creator_id: int) -> str:
    async with factory() as db:
        from app.models.user.account_invite import AccountInvite
        db.add(AccountInvite(code='AAAA0001', creator_id=creator_id,
                             expires_at=datetime.utcnow() + timedelta(minutes=5)))
        await db.commit()
        return 'AAAA0001'


def test_redeem_expired(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub'))
    asyncio.run(_seed_invite(family_db, 'AAAA0002', 1, datetime.utcnow() - timedelta(minutes=1)))
    client = _make_client(2)
    r = client.post('/api/v1/account/link', json={'code': 'AAAA0002'})
    assert r.status_code == 400, r.text


def test_redeem_single_use(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'subA'))
    asyncio.run(_add_user(family_db, 3, 'subB'))
    asyncio.run(_seed_invite(family_db, 'AAAA0003', 1, datetime.utcnow() + timedelta(minutes=5)))
    client = _make_client(2)
    r = client.post('/api/v1/account/link', json={'code': 'AAAA0003'})
    assert r.status_code == 200, r.text
    # 第二人兑换同一码 → 一次性拒绝
    client2 = _make_client(3)
    r2 = client2.post('/api/v1/account/link', json={'code': 'AAAA0003'})
    assert r2.status_code in (400, 409), r2.text


def test_redeem_self_rejected(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_seed_invite(family_db, 'AAAA0004', 1, datetime.utcnow() + timedelta(minutes=5)))
    client = _make_client(1)
    r = client.post('/api/v1/account/link', json={'code': 'AAAA0004'})
    assert r.status_code == 400, r.text


def test_redeem_already_linked_rejected(family_db):
    # 已是子账号再兑换 → 400（重复）
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    asyncio.run(_seed_invite(family_db, 'AAAA0005', 1, datetime.utcnow() + timedelta(minutes=5)))
    client = _make_client(2)
    r = client.post('/api/v1/account/link', json={'code': 'AAAA0005'})
    assert r.status_code == 400, r.text


def test_redeem_creator_is_sub_rejected(family_db):
    # 发码者不是独立主账号 → 400
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    asyncio.run(_add_user(family_db, 3, 'candidate'))
    asyncio.run(_seed_invite(family_db, 'AAAA0006', 2, datetime.utcnow() + timedelta(minutes=5)))
    client = _make_client(3)
    r = client.post('/api/v1/account/link', json={'code': 'AAAA0006'})
    assert r.status_code == 400, r.text


# ── 并发 used_by 防重：直接调用服务层的条件更新 ──

def test_redeem_concurrent_used_by_guard(family_db):
    from app.db.database import async_session_factory
    from app.services.family_service import redeem_invite_code

    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 's1'))
    asyncio.run(_add_user(family_db, 3, 's2'))
    asyncio.run(_seed_invite(family_db, 'AAAA0007', 1, datetime.utcnow() + timedelta(minutes=5)))

    async def _redeem(uid):
        async with async_session_factory() as db:
            try:
                await redeem_invite_code(db, uid, 'AAAA0007')
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                return False

    # 模拟两个用户并发争抢同一个码：同事务 used_by IS NULL 条件更新保证只有一人成功
    results = [asyncio.run(_redeem(2)), asyncio.run(_redeem(3))]
    assert sum(1 for x in results if x) == 1, results


# ── ≤6 限制 ──

def test_redeem_sub_account_limit(family_db):
    from app.db.database import async_session_factory
    from app.services.family_service import redeem_invite_code

    asyncio.run(_add_user(family_db, 1, 'main'))
    # 主账号已有 6 个子账号
    for i in range(6):
        asyncio.run(_add_user(family_db, 100 + i, f's{i}', parent_id=1))
    asyncio.run(_add_user(family_db, 2, 'candidate'))
    code = asyncio.run(_generate_code(family_db, 1))

    async def _redeem():
        async with async_session_factory() as db:
            try:
                await redeem_invite_code(db, 2, code)
                await db.commit()
                return True
            except Exception:
                await db.rollback()
                return False

    assert asyncio.run(_redeem()) is False


# ── 解除关联 ──

def test_unlink_main_kicks_sub(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    client = _make_client(1)
    r = client.delete('/api/v1/account/link', params={'target_user_id': 2})
    assert r.status_code == 200, r.text
    assert asyncio.run(_get_user(family_db, 2)) is None


def test_unlink_sub_self(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    client = _make_client(2)
    r = client.delete('/api/v1/account/link')  # 省略 target -> 自己
    assert r.status_code == 200, r.text
    assert asyncio.run(_get_user(family_db, 2)) is None


def test_unlink_forbidden_non_parent(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    asyncio.run(_add_user(family_db, 3, 'other'))
    client = _make_client(3)
    r = client.delete('/api/v1/account/link', params={'target_user_id': 2})
    assert r.status_code == 403, r.text


# ── family info ──

def test_family_info_views(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    # 主账号视图
    main_client = _make_client(1)
    r = main_client.get('/api/v1/account/family')
    assert r.status_code == 200
    data = r.json()
    assert data['is_sub'] is False
    assert data['root_id'] == 1
    assert data['member_count'] == 2
    assert len(data['sub_accounts']) == 1
    assert data['sub_accounts'][0]['id'] == 2
    # 子账号视图
    sub_client = _make_client(2)
    r2 = sub_client.get('/api/v1/account/family')
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2['is_sub'] is True
    assert data2['parent_id'] == 1
    assert data2['main_account']['id'] == 1


# ── profile / admin 字段 ──

def test_profile_returns_parent_fields(family_db):
    asyncio.run(_add_user(family_db, 1, 'main'))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    client = _make_client(1)
    r = client.get('/api/v1/auth/profile')
    assert r.status_code == 200
    assert 'parent_id' in r.json()
    assert 'is_sub' in r.json()
    assert r.json()['is_sub'] is False

    client2 = _make_client(2)
    r2 = client2.get('/api/v1/auth/profile')
    assert r2.status_code == 200
    assert r2.json()['parent_id'] == 1
    assert r2.json()['is_sub'] is True


def test_admin_accounts_returns_parent_id(family_db):
    asyncio.run(_add_user(family_db, 1, 'main', is_admin=True))
    asyncio.run(_add_user(family_db, 2, 'sub', parent_id=1))
    client = _make_client(1)
    r = client.get('/api/v1/admin/accounts')
    assert r.status_code == 200, r.text
    accs = {a['id']: a for a in r.json()['accounts']}
    assert 'parent_id' in accs[1]
    assert accs[2]['parent_id'] == 1
    assert 'password_hash' not in accs[1]
