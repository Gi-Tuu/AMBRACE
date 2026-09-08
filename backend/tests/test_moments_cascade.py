# -*- coding: utf-8 -*-
"""v3.4.6 审查 G2 · F-4/F-7（+ F-10）朋友圈删除级联单测。

覆盖：
- F-4：clear_character_moments 硬删动态前显式级联清评论/用户赞/AI 赞（子表 FK 无
  ondelete，原只 db.delete(moment) → 孤儿行累积）；事件 payload 保留被删计数；
- F-7：delete_comment 硬删父评论时级联删除整棵子回复树，每条各落
  moment.comment_deleted 事件（幂等键绑评论 id，payload 保留内容前 200 字）；
- F-10：_resolve_author_gender 无 character 时正常返回空串（不可达行删除后回归）。

（临时 SQLite 建全模型；api 函数直接以临时 db session 调用；事件经 monkeypatch
 moments 模块级 append_domain_event 记录。）
"""
import asyncio
import os
import tempfile

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import moments as moments_api
from app.models.life import AIMoment, MomentAILike, MomentComment, MomentLike


@pytest.fixture()
def tmp_db(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="moments_cascade_")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{os.path.join(tmp, 't.db')}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    calls: list[dict] = []

    async def _append(event_type, aggregate_type, aggregate_id, **kw):
        calls.append({
            "event_type": event_type, "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id, **kw,
        })

    monkeypatch.setattr(moments_api, "append_domain_event", _append)
    return factory, calls


def _mk_moment(db, mid, character_id=None, user_id=None, sender="user"):
    db.add(AIMoment(
        id=mid, character_id=character_id, user_id=user_id,
        sender_type=sender, content=f"动态{mid}", likes_count=1,
    ))
    db.add(MomentComment(id=mid * 100 + 1, moment_id=mid, parent_id=None,
                         sender_type="ai", sender_id=99, sender_name="AI", content="评论"))
    db.add(MomentComment(id=mid * 100 + 2, moment_id=mid, parent_id=mid * 100 + 1,
                         sender_type="user", sender_id=1, sender_name="我", content="回复"))
    db.add(MomentLike(moment_id=mid, user_id=1))
    db.add(MomentAILike(moment_id=mid, character_id=99))


async def _count(db, model, moment_id):
    return (await db.execute(
        select(func.count()).select_from(model).where(model.moment_id == moment_id)
    )).scalar() or 0


# ── F-4：清空角色当日动态 → 子表零残留 ──────────────────

def test_清空角色动态_子表零残留(tmp_db):
    factory, calls = tmp_db

    async def _run():
        async with factory() as db:
            from app.models.character import AICharacter

            db.add(AICharacter(id=7, user_id=1, name="角色A", personality="温柔"))
            db.add(AICharacter(id=8, user_id=1, name="角色B", personality="开朗"))
            _mk_moment(db, 1, character_id=7, user_id=1, sender="ai")
            _mk_moment(db, 2, character_id=7, user_id=1, sender="ai")
            _mk_moment(db, 3, character_id=8, user_id=1, sender="ai")
            await db.commit()
        async with factory() as db:
            res = await moments_api.clear_character_moments(7, db=db, user_id=1)
        assert res["deleted"] == 2
        async with factory() as db:
            for model in (MomentComment, MomentLike, MomentAILike):
                assert await _count(db, model, 1) == 0
                assert await _count(db, model, 2) == 0
            # 其它角色动态的子行不受影响（每条动态 2 评论 / 1 用户赞 / 1 AI 赞）
            assert await _count(db, MomentComment, 3) == 2
            assert await _count(db, MomentLike, 3) == 1
            assert await _count(db, MomentAILike, 3) == 1
            left = (await db.execute(select(AIMoment))).scalars().all()
            assert sorted(m.id for m in left) == [3]
        cleared = [c for c in calls if c["event_type"] == "moment.cleared"]
        assert len(cleared) == 1
        assert cleared[0]["payload"]["moment_count"] == 2
        assert cleared[0]["payload"]["comment_count"] == 4
        assert cleared[0]["payload"]["like_count"] == 2

    asyncio.run(_run())


def test_删单条动态_子表零残留_事件保留计数(tmp_db):
    """delete_moment 的级联在 HEAD 已存在（F-4 核实结论），此用例锁「删除后子表零残留」。"""
    factory, calls = tmp_db

    async def _run():
        async with factory() as db:
            _mk_moment(db, 5, user_id=1, sender="user")
            await db.commit()
        async with factory() as db:
            res = await moments_api.delete_moment(5, db=db, user_id=1)
        assert res["success"] is True
        async with factory() as db:
            for model in (MomentComment, MomentLike, MomentAILike):
                assert await _count(db, model, 5) == 0
            m = await db.get(AIMoment, 5)
            assert m.is_active is False
        deleted = [c for c in calls if c["event_type"] == "moment.deleted"]
        assert len(deleted) == 1
        assert deleted[0]["payload"]["likes_count"] == 1

    asyncio.run(_run())


# ── F-7：删父评论级联子回复树 + 事件齐全 ──────────────────

def test_删父评论_子回复级联_事件齐全(tmp_db):
    factory, calls = tmp_db

    async def _run():
        async with factory() as db:
            db.add(AIMoment(id=9, character_id=None, user_id=1, sender_type="user", content="动态"))
            # 树：101(用户,根) ← 102(AI) ← 103(用户)；另有独立根评论 104 不受影响
            db.add(MomentComment(id=101, moment_id=9, parent_id=None,
                                 sender_type="user", sender_id=1, sender_name="我", content="父评论"))
            db.add(MomentComment(id=102, moment_id=9, parent_id=101,
                                 sender_type="ai", sender_id=99, sender_name="AI", content="子回复"))
            db.add(MomentComment(id=103, moment_id=9, parent_id=102,
                                 sender_type="user", sender_id=1, sender_name="我", content="孙回复"))
            db.add(MomentComment(id=104, moment_id=9, parent_id=None,
                                 sender_type="user", sender_id=1, sender_name="我", content="无关评论"))
            await db.commit()
        async with factory() as db:
            res = await moments_api.delete_comment(9, 101, db=db, user_id=1)
        assert res == {"status": "ok"}
        async with factory() as db:
            left = (await db.execute(select(MomentComment.id))).scalars().all()
            assert left == [104]  # 整棵子树 101/102/103 均不可见且无残留
        # 每条被删评论各落一事件，幂等键绑评论 id，payload 保留内容
        deleted = [c for c in calls if c["event_type"] == "moment.comment_deleted"]
        assert sorted(c["entity_id"] for c in deleted) == [101, 102, 103]
        keys = {c["idempotency_key"] for c in deleted}
        assert keys == {f"moment.comment_deleted:moment_comment:{i}" for i in (101, 102, 103)}
        by_id = {c["entity_id"]: c["payload"] for c in deleted}
        assert by_id[101]["content"] == "父评论"
        assert by_id[102]["content"] == "子回复"
        assert by_id[103]["content"] == "孙回复"
        assert by_id[103]["parent_id"] == 102

    asyncio.run(_run())


# ── F-10：_resolve_author_gender 无 character 返回空串 ──────────────────

def test_用户动态作者性别为空串():
    from types import SimpleNamespace

    from app.application.moment_service import _resolve_author_gender

    class _FakeMoment:
        character_id = 0
        sender_type = "user"

    assert asyncio.run(_resolve_author_gender(_FakeMoment())) == ""
    assert asyncio.run(_resolve_author_gender(SimpleNamespace(character_id=None, sender_type="user"))) == ""
