# -*- coding: utf-8 -*-
# #46 主账号管理（选择型）测试（2026-08-24，#68 修订 2026-08-28 按家庭范围隔离）：
# - database 一次性种子：仅当无 is_admin=1 时从 env 写入 admin_user_ids（幂等）。
#   #68 修订后，一致性修正把所有 parent_id IS NULL 的独立主账号统一置为 is_admin=1。
# - permission_service.is_admin_user：DB 权威 + env 兜底（用户不存在/读取失败）
# - admin API：按家庭范围隔离（列表只看自己家庭；目标必须在家庭内；禁操作自己；
#   最后一个主账号受「不能操作自己」规则保护）
import asyncio
import os
import tempfile

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import admin as admin_api
from app.auth.deps import get_current_user_id
from app.services import permission_service as perm

ADMIN = 1
OTHER = 99


@pytest.fixture()
def db_factory(monkeypatch):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）"""
    tmp = tempfile.mkdtemp(prefix='admin_test_')
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
    monkeypatch.setattr(admin_api, 'async_session_factory', factory)
    monkeypatch.setattr(perm, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    """清理 permission_service._admin_cache，避免跨用例污染/缓存命中干扰"""
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


def _seed(db_factory):
    """写入家庭结构：1=主账号(admin)、2=其子账号(非 admin)、3=另一独立主账号(admin)。"""
    async def _s():
        import app.models.user as um
        async with db_factory() as db:
            db.add_all([
                um.User(id=1, username='admin', nickname='管理员', is_admin=True),
                um.User(id=2, username='sub', nickname='子号', is_admin=False, parent_id=1),
                um.User(id=3, username='admin3', nickname='第三号', is_admin=True),
            ])
            await db.commit()
    asyncio.run(_s())


def _read_admin(db_factory, user_id: int) -> bool:
    import app.models.user as um
    async def _r():
        async with db_factory() as db:
            row = (await db.execute(select(um.User).where(um.User.id == user_id))).scalar_one_or_none()
            return bool(row.is_admin) if row else None
    return asyncio.run(_r())


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(admin_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


# ---------------- 一次性种子 ----------------

def _run_seed(admin_ids, pre_admins: list[int], monkeypatch):
    """在临时库建表+塞用户后跑 init_db，返回 {id: is_admin}"""
    import app.db.database as db_mod
    from app.config import settings
    tmp = tempfile.mkdtemp(prefix='admin_seed_')
    db_path = os.path.join(tmp, 't.db')
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}', poolclass=NullPool)
    monkeypatch.setattr(db_mod, 'engine', engine)
    import app.db.init_db as _initdb_mod  # F1 拆分：init_db 用实现模块的 engine 绑定
    monkeypatch.setattr(_initdb_mod, 'engine', engine)
    monkeypatch.setattr(settings, 'admin_user_ids', admin_ids)

    async def _run():
        import app.models  # noqa: F401
        import app.models.user as um
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            db.add_all([
                um.User(id=1, username='a', nickname='A'),
                um.User(id=3, username='b', nickname='B'),
                um.User(id=5, username='c', nickname='C'),
            ])
            await db.commit()
        if pre_admins:
            async with factory() as db:
                for uid in pre_admins:
                    row = (await db.execute(select(um.User).where(um.User.id == uid))).scalar_one_or_none()
                    row.is_admin = True
                await db.commit()
        await db_mod.init_db()
        async with factory() as db:
            rows = (await db.execute(select(um.User).order_by(um.User.id))).scalars().all()
            return {r.id: bool(r.is_admin) for r in rows}
    result = asyncio.run(_run())
    engine.sync_engine.dispose()
    return result


def test_seed_writes_env_admins_when_none_exist(monkeypatch):
    # #68 修订：表中用户均为独立主账号（parent_id IS NULL）→ 一致性修正统一置为 admin
    result = _run_seed([1, 3], [], monkeypatch)
    assert result == {1: True, 3: True, 5: True}


def test_seed_skips_when_ui_admin_exists(monkeypatch):
    # #68 修订：一致性修正把所有独立主账号置为 admin（不受 UI/env 是否已设影响）
    result = _run_seed([1, 3], [5], monkeypatch)
    assert result == {1: True, 3: True, 5: True}


# ---------------- is_admin_user 判定 ----------------

def test_is_admin_user_reads_db(db_factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'admin_user_ids', [1])
    _seed(db_factory)
    assert asyncio.run(perm.is_admin_user(1)) is True
    assert asyncio.run(perm.is_admin_user(2)) is False
    assert asyncio.run(perm.is_admin_user(3)) is True


def test_is_admin_user_fallback_env_when_user_missing(db_factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'admin_user_ids', [7])
    # 用户 7 不在库中 → 回退 env：7 在 env → True；999 不在 → False
    assert asyncio.run(perm.is_admin_user(7)) is True
    assert asyncio.run(perm.is_admin_user(999)) is False


def test_is_admin_user_sync_uses_cache(db_factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'admin_user_ids', [1])
    _seed(db_factory)
    # 先异步读（填充缓存），同步版应直接命中缓存
    assert asyncio.run(perm.is_admin_user(1)) is True
    assert perm.is_admin_user_sync(1) is True


# ---------------- admin API ----------------

def test_list_accounts_admin(db_factory):
    _seed(db_factory)
    r = _make_client(ADMIN).get('/api/v1/admin/accounts')
    assert r.status_code == 200
    accs = {a['id']: a for a in r.json()['accounts']}
    # 只看自己家庭（自己 + 直属子账号），看不到另一独立主账号 3
    assert set(accs) == {1, 2}
    assert accs[1]['is_admin'] is True
    assert accs[1]['is_self'] is True
    assert accs[2]['is_admin'] is False
    # 不含敏感字段
    assert 'password_hash' not in accs[1]
    assert 'password' not in accs[1]


def test_list_accounts_forbidden_non_admin(db_factory):
    _seed(db_factory)
    r = _make_client(OTHER).get('/api/v1/admin/accounts')
    assert r.status_code == 403


def test_set_admin_enable(db_factory):
    _seed(db_factory)
    # 主账号可给子账号授予 admin
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/2/admin', json={'enabled': True})
    assert r.status_code == 200
    assert r.json()['is_admin'] is True
    assert _read_admin(db_factory, 2) is True


def test_set_admin_disable_when_other_admin_remains(db_factory):
    _seed(db_factory)  # 主账号 1 admin；先给子账号 2 授予 admin
    assert _make_client(ADMIN).put('/api/v1/admin/accounts/2/admin', json={'enabled': True}).status_code == 200
    # 取消子账号 admin：家庭内仍保留主账号 1 → 200
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/2/admin', json={'enabled': False})
    assert r.status_code == 200
    assert _read_admin(db_factory, 2) is False


def test_last_admin_protected(db_factory):
    # 主账号是家庭内唯一 admin → 通过「不能操作自己」规则保护（拒绝取消自己）
    _seed(db_factory)
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/1/admin', json={'enabled': False})
    assert r.status_code == 400
    assert _read_admin(db_factory, 1) is True


def test_set_admin_cannot_toggle_other_family(db_factory):
    # 3 是另一独立家庭的主账号 → 跨家庭 403
    _seed(db_factory)
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/3/admin', json={'enabled': True})
    assert r.status_code == 403


def test_self_disable_only_admin_protected(db_factory, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'admin_user_ids', [1])
    async def _s():
        import app.models.user as um
        async with db_factory() as db:
            db.add_all([
                um.User(id=1, username='admin', nickname='管理员', is_admin=True),
                um.User(id=2, username='normal', nickname='普通用户', is_admin=False),
            ])
            await db.commit()
    asyncio.run(_s())
    # 操作者取消自己且是唯一主账号 → 400（不能操作自己）
    r = _make_client(1).put('/api/v1/admin/accounts/1/admin', json={'enabled': False})
    assert r.status_code == 400


def test_set_admin_requires_enabled(db_factory):
    _seed(db_factory)
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/2/admin', json={})
    assert r.status_code == 400


def test_set_admin_user_not_found(db_factory):
    _seed(db_factory)
    # 999 不在本家庭 → 先被家庭范围隔离拦截为 403
    r = _make_client(ADMIN).put('/api/v1/admin/accounts/999/admin', json={'enabled': True})
    assert r.status_code == 403


def test_set_admin_forbidden_non_admin(db_factory):
    _seed(db_factory)
    r = _make_client(OTHER).put('/api/v1/admin/accounts/2/admin', json={'enabled': True})
    assert r.status_code == 403
