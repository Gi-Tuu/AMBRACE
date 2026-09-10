# -*- coding: utf-8 -*-
"""T1 宠物遗弃改软删回归（v3.4.6 第三轮止血批，2026-09-10）。

背景：abandon_pet 原硬删 pets 行，pet_activities.pet_id NOT NULL + RESTRICT →
有活动记录的宠物一遗弃必现 IntegrityError 500；且删后才写活动、外键悬空。
本次改软删：置 abandoned_at、保留行（活动/记忆可追溯）、先落活动再置标记、幂等。

断言：
1) 有活动宠物的遗弃不抛 500（abandon_pet 返回 True，不因外键回滚）；
2) pets 行保留且 abandoned_at 非空（未硬删）；
3) pet_activities 活动记录保留（未级联删除）；
4) 「当前宠物」查询（pets API 列表口径）不再包含已遗弃宠物；
5) 已遗弃再遗弃幂等返回 False（不重复写）。
"""
import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.application.pet_service as pet_svc


async def _noop(*_a, **_k):
    return None


@pytest.fixture()
def pet_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库 + 隔离全局副作用（pet_service 的 async_session_factory / save_memory）。"""
    db_path = os.path.join(str(tmp_path), "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(pet_svc, "async_session_factory", factory)
    monkeypatch.setattr(pet_svc, "save_memory", _noop)
    yield factory
    asyncio.run(engine.dispose())


def _seed(factory):
    from datetime import datetime
    from app.models.character import AICharacter
    from app.models.pet import Pet, PetActivity
    from app.models.user import User

    async def _main():
        async with factory() as db:
            db.add(User(id=21001, username="u_t1", nickname="用户T1", is_admin=True))
            db.add(AICharacter(id=22001, user_id=21001, name="小柔", is_active=True))
            db.add(Pet(id=23001, user_id=21001, name="毛毛", species="cat",
                       owner_type="user", created_at=datetime(2026, 8, 1)))
            # 有活动记录：T1 根因正是「有活动宠物的硬删必撞 pet_activities.pet_id RESTRICT」
            db.add(PetActivity(pet_id=23001, user_id=21001, action="feed", actor="user",
                               content="用户喂了毛毛"))
            await db.commit()

    asyncio.run(_main())


def _pets_list_ids(factory, user_id):
    """复刻 pets API list_pets 的「当前宠物」查询口径，验证已遗弃被过滤。"""
    from sqlalchemy import or_
    from app.models.pet import Pet

    async def _main():
        async with factory() as db:
            rows = (await db.execute(
                select(Pet).where(
                    Pet.user_id == user_id,
                    Pet.abandoned_at.is_(None),
                    or_(Pet.owner_type.is_(None), Pet.owner_type == "user"),
                ).order_by(Pet.created_at.asc())
            )).scalars().all()
            return [p.id for p in rows]

    return asyncio.run(_main())


def test_abandon_soft_delete_保留行且活动保留(pet_db):
    _seed(pet_db)

    # 有活动宠物遗弃不 500（原硬删必 IntegrityError）
    result = asyncio.run(pet_svc.abandon_pet(23001, 21001))
    assert result is True

    async def _check():
        from app.models.pet import Pet, PetActivity
        async with pet_db() as db:
            pet = await db.get(Pet, 23001)
            assert pet is not None, "软删应保留 pets 行（不硬删）"
            assert pet.abandoned_at is not None, "软删应置 abandoned_at"
            acts = (await db.execute(
                select(PetActivity).where(PetActivity.pet_id == 23001)
            )).scalars().all()
            assert len(acts) == 2, "遗弃后活动记录应保留（原 feed + 新 abandon）"
            actions = {a.action for a in acts}
            assert {"feed", "abandon"} <= actions
            assert any(a.content == "用户喂了毛毛" for a in acts)
    asyncio.run(_check())

    # 列表口径：已遗弃宠物不可见
    assert 23001 not in _pets_list_ids(pet_db, 21001)

    # 幂等：已遗弃再遗弃返回 False
    assert asyncio.run(pet_svc.abandon_pet(23001, 21001)) is False


def test_abandon_未遗弃宠物仍在列表(pet_db):
    _seed(pet_db)
    # 未遗弃宠物仍在「当前宠物」列表（过滤只剔除 abandoned_at 非空，不误伤正常宠物）
    from app.models.pet import Pet
    async def _main():
        async with pet_db() as db:
            db.add(Pet(id=23002, user_id=21001, name="旺财", species="dog", owner_type="user"))
            await db.commit()
    asyncio.run(_main())
    ids = _pets_list_ids(pet_db, 21001)
    assert 23001 in ids
    assert 23002 in ids


def test_abandon_不属于用户返回False(pet_db):
    _seed(pet_db)
    # 其它 user_id 不可遗弃（服务层返回 False）
    assert asyncio.run(pet_svc.abandon_pet(23001, 99999)) is False
