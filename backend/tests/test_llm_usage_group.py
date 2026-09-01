# -*- coding: utf-8 -*-
"""#68 P6 用量组聚合测试：
- 主账号聚合含自己 + 直属子账号 + 服务器级(NULL)；by_user 正确（成员 user_id/nickname/total）
- 子账号只看自己，不返回 by_user
- 服务器级（user_id NULL）行只进主账号统计
- _record_usage_async 透传 user_id/config_id/group_owner_id（monkeypatch 捕获落库参数）
"""
import asyncio
import os
import tempfile
from datetime import datetime

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.agent import llm_client
from app.api import system as system_api
from app.auth.deps import get_current_user_id
from app.services import permission_service as perm


@pytest.fixture()
def usage_db(monkeypatch):
    """临时 SQLite 文件库：patch 各模块绑定的 async_session_factory（不触碰 backend/data）。"""
    tmp = tempfile.mkdtemp(prefix='llm_usage_test_')
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
    monkeypatch.setattr(perm, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_admin_cache():
    perm._admin_cache.clear()
    yield
    perm._admin_cache.clear()


async def _add_user(factory, uid: int, username: str, parent_id: int | None = None, is_admin: bool = False):
    from app.models.user import User
    async with factory() as db:
        db.add(User(id=uid, username=username, nickname=f'n{uid}', parent_id=parent_id, is_admin=is_admin))
        await db.commit()


async def _seed_usage(factory, rows: list[dict]):
    """rows: {user_id, config_id, group_owner_id, total, model}"""
    from app.models.agent import LlmUsage
    now = datetime.now()
    async with factory() as db:
        for i, r in enumerate(rows):
            db.add(LlmUsage(
                user_id=r.get('user_id'),
                config_id=r.get('config_id'),
                group_owner_id=r.get('group_owner_id'),
                provider='p', model=r.get('model', 'm'),
                prompt_tokens=r.get('total', 0),
                completion_tokens=0,
                total_tokens=r.get('total', 0),
                reasoning_tokens=0,
                task='chat',
                created_at=now,
            ))
        await db.commit()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(system_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _seed_family(usage_db):
    """用户：1=主账号；2/3=其子账号；4=另一独立主账号。"""
    asyncio.run(_add_user(usage_db, 1, 'main', is_admin=True))
    asyncio.run(_add_user(usage_db, 2, 'subA', parent_id=1, is_admin=False))
    asyncio.run(_add_user(usage_db, 3, 'subB', parent_id=1, is_admin=False))
    asyncio.run(_add_user(usage_db, 4, 'other_main', is_admin=True))


def test_main_account_group_aggregation(usage_db):
    _seed_family(usage_db)
    asyncio.run(_seed_usage(usage_db, [
        {'user_id': 1, 'config_id': 10, 'group_owner_id': 1, 'total': 100, 'model': 'm1'},
        {'user_id': 2, 'config_id': 11, 'group_owner_id': 1, 'total': 200, 'model': 'm1'},
        {'user_id': 3, 'config_id': 12, 'group_owner_id': 1, 'total': 300, 'model': 'm2'},
        {'user_id': 4, 'config_id': 13, 'group_owner_id': 4, 'total': 999, 'model': 'm3'},  # 其他家庭
        {'user_id': None, 'config_id': None, 'group_owner_id': None, 'total': 50, 'model': 'm2'},  # 服务器级
    ]))
    client = _make_client(1)
    r = client.get('/api/v1/system/llm-usage')
    assert r.status_code == 200, r.text
    data = r.json()
    # 自己 + 子账号 + 服务器级；排除其他家庭（999）
    assert data['used_total'] == 100 + 200 + 300 + 50
    # by_model：m1=300、m2=350、m3 不在
    by_model = {m['model']: m['total'] for m in data['by_model']}
    assert by_model == {'m1': 300, 'm2': 350}
    # by_user：成员 1/2/3（不含其他家庭 4、不含服务器级）
    by_user = {u['user_id']: u for u in data['by_user']}
    assert set(by_user.keys()) == {1, 2, 3}
    assert by_user[1]['nickname'] == 'n1' and by_user[1]['total'] == 100
    assert by_user[2]['nickname'] == 'n2' and by_user[2]['total'] == 200
    assert by_user[3]['nickname'] == 'n3' and by_user[3]['total'] == 300


def test_sub_account_sees_only_self(usage_db):
    _seed_family(usage_db)
    asyncio.run(_seed_usage(usage_db, [
        {'user_id': 1, 'config_id': 10, 'group_owner_id': 1, 'total': 100, 'model': 'm1'},
        {'user_id': 2, 'config_id': 11, 'group_owner_id': 1, 'total': 200, 'model': 'm1'},
        {'user_id': 3, 'config_id': 12, 'group_owner_id': 1, 'total': 300, 'model': 'm2'},
        {'user_id': None, 'config_id': None, 'group_owner_id': None, 'total': 50, 'model': 'm2'},
    ]))
    client = _make_client(2)
    r = client.get('/api/v1/system/llm-usage')
    assert r.status_code == 200, r.text
    data = r.json()
    assert data['used_total'] == 200  # 只看自己，不含服务器级/主账号/其他子账号
    assert data['by_user'] == []
    by_model = {m['model']: m['total'] for m in data['by_model']}
    assert by_model == {'m1': 200}


def test_server_level_row_only_in_main(usage_db):
    _seed_family(usage_db)
    asyncio.run(_seed_usage(usage_db, [
        {'user_id': 2, 'config_id': 11, 'group_owner_id': 1, 'total': 200, 'model': 'm1'},
        {'user_id': None, 'config_id': None, 'group_owner_id': None, 'total': 50, 'model': 'm2'},
    ]))
    main_client = _make_client(1)
    r_main = main_client.get('/api/v1/system/llm-usage')
    assert r_main.status_code == 200, r_main.text
    assert r_main.json()['used_total'] == 200 + 50  # 主账号含服务器级
    # 服务器级行不进入 by_user（user_id NULL 不是成员）
    assert {u['user_id'] for u in r_main.json()['by_user']} == {2}

    sub_client = _make_client(2)
    r_sub = sub_client.get('/api/v1/system/llm-usage')
    assert r_sub.status_code == 200, r_sub.text
    assert r_sub.json()['used_total'] == 200  # 子账号只看自己，服务器级不进


def test_record_usage_async_passthrough(monkeypatch):
    """_record_usage_async 落库时透传 user_id/config_id/group_owner_id。"""
    captured = {}

    class _FakeDB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def add(self, obj):
            captured['obj'] = obj

        async def commit(self):
            pass

    monkeypatch.setattr(
        'app.db.database.async_session_factory',
        lambda: _FakeDB(),
    )

    async def _run():
        llm_client._record_usage_async(
            'p', 'm', 10, 20, 5, task='chat',
            user_id=2, config_id=11, group_owner_id=1,
        )
        await asyncio.sleep(0.05)  # 等待后台任务写入

    asyncio.run(_run())
    obj = captured['obj']
    assert obj.user_id == 2
    assert obj.config_id == 11
    assert obj.group_owner_id == 1
    assert obj.task == 'chat'
    assert obj.provider == 'p'
    assert obj.model == 'm'
    assert obj.total_tokens == 30
