# -*- coding: utf-8 -*-
"""v3.4.6 审查 G2 · F-4/F-7（+ F-10）朋友圈删除级联单测。

覆盖：
- F-4：clear_character_moments 硬删动态前显式级联清评论/用户赞/AI 赞（三张子表的 moment_id
  都带 ON DELETE CASCADE，显式删除是为兼容 FK 关闭的环境与「不依赖 SQLite 级联」的语义）；
  事件 payload 的计数＝真正被删掉的行数（先数后删，含被级联带走的孩子行）；
- F-7：delete_comment 硬删父评论时级联删除整棵子回复树，每条各落
  moment.comment_deleted 事件（幂等键绑评论 id，payload 保留内容前 200 字）；
- F-10：_resolve_author_gender 无 character 时正常返回空串（不可达行删除后回归）。

（api 函数直接以临时 db session 调用；事件经 monkeypatch moments 模块级
 append_domain_event 记录。临时库＝会话级模板库克隆（tests/_dbclone.py），逐连接注册生产
 同款 PRAGMA，因此**外键默认 ON**——与生产同语义，级联计数断言才是有效的。）
"""
import asyncio
import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from _dbclone import clone_engine, make_session_factory

from app.api import moments as moments_api
from app.models.character import AICharacter
from app.models.life import AIMoment, MomentAILike, MomentComment, MomentLike
from app.models.user import User

# 快测档（2026-09-12）：本文件是重量级/集成型用例（api 直调 + 真实文件库），打 slow 标记。
# 全量默认照跑；日常开发用 pytest -m "not slow" 跳过本档（见 docs/engineering-protocol.md 十八）。
# 提速批次已迁克隆库（建表页级拷贝，不再每例 create_all），档位不变。
pytestmark = pytest.mark.slow

_SUBTABLES = (MomentComment, MomentLike, MomentAILike)


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    engine = clone_engine(os.path.join(str(tmp_path), 't.db'))
    factory = make_session_factory(engine)

    calls: list[dict] = []

    async def _append(event_type, aggregate_type, aggregate_id, **kw):
        calls.append({
            "event_type": event_type, "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id, **kw,
        })

    monkeypatch.setattr(moments_api, "append_domain_event", _append)

    # 克隆库逐连接 FK=ON：ai_characters.user_id / moment_likes.user_id 真引用 users.id，
    # 父行先落库（幂等：用例体若已种同一主键则跳过，不重复 INSERT）。
    async def _init():
        async with factory() as db:
            if await db.get(User, 1) is None:
                db.add(User(id=1, username="u1", nickname="我", password_hash="x"))
                await db.commit()

    asyncio.run(_init())
    yield factory, calls
    engine.sync_engine.dispose()


# ── 种子：按 FK 层级分批 commit（取值/行数与原「一条 add_all」逐字相同，只拆提交边界）──

def _add_moment(db, mid, character_id=None, user_id=None, sender="user"):
    db.add(AIMoment(
        id=mid, character_id=character_id, user_id=user_id,
        sender_type=sender, content=f"动态{mid}", likes_count=1,
    ))


def _add_comment_root(db, mid):
    db.add(MomentComment(id=mid * 100 + 1, moment_id=mid, parent_id=None,
                         sender_type="ai", sender_id=99, sender_name="AI", content="评论"))


def _add_comment_reply(db, mid):
    db.add(MomentComment(id=mid * 100 + 2, moment_id=mid, parent_id=mid * 100 + 1,
                         sender_type="user", sender_id=1, sender_name="我", content="回复"))


def _add_likes(db, mid):
    db.add(MomentLike(moment_id=mid, user_id=1))
    db.add(MomentAILike(moment_id=mid, character_id=99))


async def _seed_moments_with_children(factory, specs):
    """种 N 条动态 + 每条 2 评论（父 + 子回复）/ 1 用户赞 / 1 AI 赞。

    specs: [(moment_id, character_id, user_id, sender_type), ...]
    FK=ON 下 SQLAlchemy 不保证同表自引用（moment_comments.parent_id）的插入拓扑序，
    故父行 → 根评论/赞 → 子回复 分三批提交。
    """
    async with factory() as db:
        for mid, character_id, user_id, sender in specs:
            _add_moment(db, mid, character_id=character_id, user_id=user_id, sender=sender)
        await db.commit()
    async with factory() as db:
        for mid, *_ in specs:
            _add_comment_root(db, mid)
            _add_likes(db, mid)
        await db.commit()
    async with factory() as db:
        for mid, *_ in specs:
            _add_comment_reply(db, mid)
        await db.commit()


async def _count(db, model, moment_id):
    return (await db.execute(
        select(func.count()).select_from(model).where(model.moment_id == moment_id)
    )).scalar() or 0


async def _table_count(db, model):
    return int((await db.execute(select(func.count()).select_from(model))).scalar() or 0)


# ── F-4：清空角色当日动态 → 子表零残留 ──────────────────

def test_清空角色动态_子表零残留(tmp_db):
    factory, calls = tmp_db

    async def _run():
        async with factory() as db:
            db.add(AICharacter(id=7, user_id=1, name="角色A", personality="温柔"))
            db.add(AICharacter(id=8, user_id=1, name="角色B", personality="开朗"))
            await db.commit()
        await _seed_moments_with_children(factory, [
            (1, 7, 1, "ai"), (2, 7, 1, "ai"), (3, 8, 1, "ai"),
        ])
        async with factory() as db:
            res = await moments_api.clear_character_moments(7, db=db, user_id=1)
        assert res["deleted"] == 2
        async with factory() as db:
            for model in _SUBTABLES:
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
        # FK=ON 下删父评论会级联带走子回复：计数必须是「真正被删的行数」= 4，非 rowcount 的 2
        assert cleared[0]["payload"]["comment_count"] == 4
        assert cleared[0]["payload"]["like_count"] == 2
        assert cleared[0]["payload"]["ai_like_count"] == 2

    asyncio.run(_run())


def test_清空角色动态_事件计数等于删除前后行数差(tmp_db):
    """F-4 语义钉死：payload 三个计数 == 对应表删除前后的行数差（含被级联带走的孩子行）。

    旧实现取 DELETE.rowcount，FK=ON（生产同款）下只数到语句直接匹配的行 → 评论少报一半。
    """
    factory, calls = tmp_db

    async def _run():
        async with factory() as db:
            db.add(AICharacter(id=7, user_id=1, name="角色A", personality="温柔"))
            await db.commit()
        await _seed_moments_with_children(factory, [(1, 7, 1, "ai"), (2, 7, 1, "ai")])
        async with factory() as db:
            before = {m.__name__: await _table_count(db, m) for m in _SUBTABLES}
        async with factory() as db:
            res = await moments_api.clear_character_moments(7, db=db, user_id=1)
        assert res["deleted"] == 2
        async with factory() as db:
            after = {m.__name__: await _table_count(db, m) for m in _SUBTABLES}
        cleared = [c for c in calls if c["event_type"] == "moment.cleared"]
        assert len(cleared) == 1
        payload = cleared[0]["payload"]
        # 删除前后之差必须与事件计数一致（本用例只种角色 7 的动态 → 删完归零）
        assert payload["comment_count"] == before["MomentComment"] - after["MomentComment"] == 4
        assert payload["like_count"] == before["MomentLike"] - after["MomentLike"] == 2
        assert payload["ai_like_count"] == before["MomentAILike"] - after["MomentAILike"] == 2
        assert after == {"MomentComment": 0, "MomentLike": 0, "MomentAILike": 0}

    asyncio.run(_run())


def test_删单条动态_子表零残留_事件保留计数(tmp_db):
    """delete_moment 的级联在 HEAD 已存在（F-4 核实结论），此用例锁「删除后子表零残留」。"""
    factory, calls = tmp_db

    async def _run():
        await _seed_moments_with_children(factory, [(5, None, 1, "user")])
        async with factory() as db:
            res = await moments_api.delete_moment(5, db=db, user_id=1)
        assert res["success"] is True
        async with factory() as db:
            for model in _SUBTABLES:
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
            await db.commit()
            # 树：101(用户,根) ← 102(AI) ← 103(用户)；另有独立根评论 104 不受影响
            # FK=ON 下按层级分批提交（自引用 parent_id 不保证同事务内的插入顺序）
            db.add(MomentComment(id=101, moment_id=9, parent_id=None,
                                 sender_type="user", sender_id=1, sender_name="我", content="父评论"))
            db.add(MomentComment(id=104, moment_id=9, parent_id=None,
                                 sender_type="user", sender_id=1, sender_name="我", content="无关评论"))
            await db.commit()
            db.add(MomentComment(id=102, moment_id=9, parent_id=101,
                                 sender_type="ai", sender_id=99, sender_name="AI", content="子回复"))
            await db.commit()
            db.add(MomentComment(id=103, moment_id=9, parent_id=102,
                                 sender_type="user", sender_id=1, sender_name="我", content="孙回复"))
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


# ── 迁移验收：克隆库确实是「生产同款 FK=ON」──────────────────

def test_克隆库逐连接强制外键(tmp_db):
    """本文件全部级联断言的前提：克隆库每条连接 PRAGMA foreign_keys=1，悬空外键直接报错。

    若哪天 FK 被关掉，「计数 = 真正被删行数」这组断言会退化成假绿，故显式钉住。
    """
    factory, _calls = tmp_db

    async def _run():
        async with factory() as db:
            assert int((await db.execute(text("PRAGMA foreign_keys"))).scalar()) == 1
        async with factory() as db:
            db.add(MomentLike(moment_id=987654, user_id=987654))  # 动态/用户都不存在
            with pytest.raises(IntegrityError):
                await db.commit()

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
