# -*- coding: utf-8 -*-
"""S4：租户隔离矩阵回归（一机多主 / SaaS S0 同源键，2026-09-05）。

统一约定：任何用户私有数据以 tenant_id = 家庭 root 为隔离键（app/application/tenant_scope）。
本文件与既有角色/会话隔离测试并列，覆盖渠道面（channel_bindings）：
- A/B 两独立主账号：互相不可见、互相不可影响（service 层 + reader 层）；
- 子账号 = 其 root（GET 跟随、写 403）；
- resolve_tenant 全员一致（主=自己，子=父）。
"""
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent import loop as agent_loop
from app.application import channel_binding_service as svc
from app.application.tenant_scope import resolve_tenant
from app.models.channel import ChannelBinding
from app.models.character import AICharacter
from app.models.user import User


@pytest.fixture()
def iso_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield factory
    engine.sync_engine.dispose()


async def _seed(factory):
    async with factory() as db:
        db.add(User(id=1, username="A", nickname="A", is_admin=True))
        db.add(User(id=2, username="B", nickname="B", is_admin=True))
        db.add(User(id=3, username="A_sub", nickname="s", parent_id=1, is_admin=False))
        db.add(AICharacter(id=101, user_id=1, name="甲"))
        db.add(AICharacter(id=102, user_id=2, name="乙"))
        await db.commit()


async def _rows(factory):
    async with factory() as db:
        return (await db.execute(select(ChannelBinding).order_by(ChannelBinding.id))).scalars().all()


def test_matrix_tenant_resolution(iso_db):
    """租户键：主账号=自己；子账号=其主账号；与 SaaS S0 同源。"""
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            return {
                1: await resolve_tenant(db, 1),
                2: await resolve_tenant(db, 2),
                3: await resolve_tenant(db, 3),
            }

    assert asyncio.run(_run()) == {1: 1, 2: 2, 3: 1}


def test_matrix_bindings_mutually_invisible(iso_db, monkeypatch):
    """A/B 各绑各渠道角色：list_bindings 互不可见；DB 行互不覆盖。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with iso_db() as db:
            a = await svc.list_bindings(db, 1, "wechat")
            b = await svc.list_bindings(db, 2, "wechat")
            all_rows = await svc.list_bindings(db, 1)  # 跨渠道列 A 名下全部
            return a, b, all_rows

    a, b, all_rows = asyncio.run(_run())
    assert [r.character_id for r in a] == [101]
    assert [r.character_id for r in b] == [102]
    assert len(asyncio.run(_rows(iso_db))) == 2
    assert all(r.tenant_id == 1 for r in all_rows)


def test_matrix_sub_account_get_follows_root_write_forbidden(iso_db, monkeypatch):
    """子账号读=其 root 家庭视图；写=403 语义（SubAccountForbidden）。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _seed_bind():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()

    asyncio.run(_seed_bind())

    async def _run():
        async with iso_db() as db:
            sub_view = await svc.list_bindings(db, await resolve_tenant(db, 3), "wechat")
            with pytest.raises(svc.SubAccountForbidden):
                await svc.upsert_binding(db, 3, "wechat", 101)
            with pytest.raises(svc.SubAccountForbidden):
                await svc.remove_binding(db, 3, "wechat")
            return sub_view

    sub_view = asyncio.run(_run())
    assert [r.character_id for r in sub_view] == [101]


def test_matrix_b_rebind_does_not_touch_a(iso_db, monkeypatch):
    """后绑覆盖先绑回归（服务层）：B 换绑后 A 的 character_id 保持。"""
    monkeypatch.setitem(agent_loop.AGENT_FLAGS, "channel_binding_v2", True)
    asyncio.run(_seed(iso_db))

    async def _run():
        async with iso_db() as db:
            await svc.upsert_binding(db, 1, "wechat", 101)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102)
            await db.commit()
        async with iso_db() as db:
            await svc.upsert_binding(db, 2, "wechat", 102, bot_label="乙的bot")  # B 再次 PUT
            await db.commit()

    asyncio.run(_run())
    rows = asyncio.run(_rows(iso_db))
    by_tenant = {r.tenant_id: r.character_id for r in rows}
    assert by_tenant == {1: 101, 2: 102}
