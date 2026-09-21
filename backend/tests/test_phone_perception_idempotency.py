# -*- coding: utf-8 -*-
"""感知快照幂等（2026-09-21 P2，治盘点 S3）。

客户端补传（本地队列 flush）会把同一条快照再发一次，服务端必须按「同用户 + 同 source +
同 content + 最近 5 分钟」去重：不写库、返回 200 + deduped:true，否则重传会把有效数据
挤出 MAX_KEEP 窗口。参照 tests/test_device_api.py 的 TestClient / _make_client 写法。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from starlette.testclient import TestClient

from _dbclone import clone_engine, make_session_factory

from app.api import phone as phone_api
from app.auth.deps import get_current_user_id
from app.models.device import PhoneSnapshot


# 快测档：每例起一次临时库（clone_engine），属重量级/集成型用例（见 docs/engineering-protocol.md 十八）。
pytestmark = pytest.mark.slow


@pytest.fixture()
def phone_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：patch phone API 绑定的 async_session_factory。"""
    db_path = tmp_path / 't.db'
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)
    # phone.py 在模块导入时通过 `from app.db.database import async_session_factory` 绑定引用
    monkeypatch.setattr(phone_api, 'async_session_factory', factory)
    yield factory
    engine.sync_engine.dispose()


def _make_client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(phone_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    return TestClient(app)


def _post(client: TestClient, source: str, content: str, client_key: str = ""):
    return client.post('/api/v1/phone/perception', data={
        'source': source, 'content': content, 'client_key': client_key,
    })


async def _add_user(factory, uid: int):
    async with factory() as db:
        from app.models.user import User
        db.add(User(id=uid, username=f'u{uid}', nickname=f'n{uid}'))
        await db.commit()


async def _rows(factory, user_id: int):
    async with factory() as db:
        stmt = (
            select(PhoneSnapshot)
            .where(PhoneSnapshot.user_id == user_id)
            .order_by(PhoneSnapshot.id.asc())
        )
        return list((await db.execute(stmt)).scalars().all())


async def _backdate(factory, snap_id: int, minutes: int):
    """把某条快照的 created_at 往前挪（模拟「超出 5 分钟窗口」）"""
    ts = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
    async with factory() as db:
        await db.execute(
            update(PhoneSnapshot).where(PhoneSnapshot.id == snap_id).values(created_at=ts)
        )
        await db.commit()


# ── 用例 ──

def test_same_content_within_window_deduped(phone_db):
    """同一 source+content 连续两次 POST：第二次不写库，返回 deduped:true。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    r1 = _post(client, 'clipboard', '刚复制的一段话', 'clipboard|9|123')
    assert r1.status_code == 200, r1.text
    assert r1.json().get('deduped') is not True
    assert 'snapshot' in r1.json()

    r2 = _post(client, 'clipboard', '刚复制的一段话', 'clipboard|9|123')
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"status": "ok", "deduped": True}
    assert len(asyncio.run(_rows(phone_db, 1))) == 1


def test_same_content_outside_window_inserts(phone_db):
    """把第一条 created_at 改成 10 分钟前再 POST：超出窗口 → 正常新增（2 行）。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    first = _post(client, 'clipboard', '重复出现的剪贴板内容').json()['snapshot']
    asyncio.run(_backdate(phone_db, first['id'], 10))

    r2 = _post(client, 'clipboard', '重复出现的剪贴板内容')
    assert r2.status_code == 200, r2.text
    assert r2.json().get('deduped') is not True
    rows = asyncio.run(_rows(phone_db, 1))
    assert len(rows) == 2


def test_same_content_different_source_inserts(phone_db):
    """同内容不同 source：各自一条，不去重。"""
    asyncio.run(_add_user(phone_db, 1))
    client = _make_client(1)
    assert _post(client, 'clipboard', '同一段文字').status_code == 200
    r2 = _post(client, 'notification', '同一段文字')
    assert r2.status_code == 200, r2.text
    assert r2.json().get('deduped') is not True
    assert len(asyncio.run(_rows(phone_db, 1))) == 2
