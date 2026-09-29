# -*- coding: utf-8 -*-
"""E15：group_active 群冒泡补「已发送」口径（ProactiveMessageLog 留痕）回归测试。

钉住三件事（只补口径、不改闸）：
① 单句冒泡分支：落 1 条群消息的同时补写 1 条 message_type="group_active" 的日志
   （character_id=发起角色、session_id=None、content=正文、extra_meta 含 group_id）；
② 双角色互聊分支：每条落库的群消息各补 1 条日志（发起者与搭档按各自 cid 记账），
   extra_meta 含 group_id 与 with_id；
③ 只在消息真正加入 session 后写：校验失败整批不落库时，日志也必须为 0 条
   （与群消息同一批 commit，不提前写）。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库一律 tmp_path，不连生产库。）
"""
import asyncio
import json
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy import select

from app.models.character import ProactiveMessageLog
from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage

OWNER = 1
GROUP = 1


def _factory(tmp_path):
    db_path = os.path.join(str(tmp_path), "ga.db").replace("\\", "/")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all():
        import app.models  # noqa: F401  确保全部 ORM 进入 metadata
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())
    return factory


def _seed(factory):
    """1 用户 + 2 角色同群（角色 1 为发起者，角色 2 为搭档）。"""
    from app.models.character import AICharacter
    from app.models.user import User

    async def _do():
        async with factory() as db:
            db.add_all([
                User(id=OWNER, username="u1", nickname="用户1"),
                AICharacter(id=1, user_id=OWNER, name="小爱"),
                AICharacter(id=2, user_id=OWNER, name="小和"),
                ChatGroup(id=GROUP, user_id=OWNER),
                ChatGroupMember(group_id=GROUP, character_id=1),
                ChatGroupMember(group_id=GROUP, character_id=2),
            ])
            await db.commit()

    asyncio.run(_do())


def _patch(monkeypatch, factory, reply):
    """把 group_active 的库与 LLM 都指到临时库/固定回复。"""
    from app.scheduling import group_active as ga

    async def fake_chat_completion(**kwargs):
        return reply

    monkeypatch.setattr(ga, "async_session_factory", factory)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", fake_chat_completion)
    return ga


def _dump(factory, model):
    async def _do():
        async with factory() as db:
            return (await db.execute(select(model).order_by(model.id))).scalars().all()

    return asyncio.run(_do())


def test_single_bubble_writes_one_log(tmp_path, monkeypatch):
    """① 无搭档退化单句冒泡：1 条群消息 + 1 条 group_active 日志，同批落库。"""
    factory = _factory(tmp_path)
    _seed(factory)
    ga = _patch(monkeypatch, factory, "早上好呀，大家最近怎么样")

    assert asyncio.run(ga.run_group_active(1, GROUP, OWNER, None)) is True

    msgs = _dump(factory, ChatGroupMessage)
    logs = _dump(factory, ProactiveMessageLog)
    assert len(msgs) == 1 and len(logs) == 1
    log = logs[0]
    assert log.character_id == 1
    assert log.session_id is None
    assert log.message_type == "group_active"
    assert log.content == "早上好呀，大家最近怎么样"
    meta = json.loads(log.extra_meta)
    assert meta["group_id"] == GROUP
    assert "with_id" not in meta  # 单句冒泡分支无搭档，不硬塞


def test_multi_chat_writes_log_per_message(tmp_path, monkeypatch):
    """② 双角色互聊：每条群消息各补 1 条日志，搭档的发言记在搭档名下。"""
    factory = _factory(tmp_path)
    _seed(factory)
    reply = json.dumps({"messages": [
        {"character_id": 1, "content": "最近天冷，大家记得添衣"},
        {"character_id": 2, "content": "是呀，用户今天出门多穿点了吗"},
        {"character_id": 1, "content": "穿了呢，早上还叮嘱他带伞"},
    ]}, ensure_ascii=False)
    ga = _patch(monkeypatch, factory, reply)

    assert asyncio.run(ga.run_group_active(1, GROUP, OWNER, 2)) is True

    msgs = _dump(factory, ChatGroupMessage)
    logs = _dump(factory, ProactiveMessageLog)
    assert len(msgs) == 3 and len(logs) == 3
    assert [m.character_id for m in msgs] == [1, 2, 1]
    assert [l.character_id for l in logs] == [1, 2, 1]
    for msg, log in zip(msgs, logs):
        assert log.message_type == "group_active"
        assert log.session_id is None
        assert log.content == msg.content
        meta = json.loads(log.extra_meta)
        assert meta == {"group_id": GROUP, "with_id": 2}


def test_content_truncated_to_500(tmp_path, monkeypatch):
    """①' 日志正文截到 500 字以内（列上限 String(500)）。"""
    factory = _factory(tmp_path)
    _seed(factory)
    long_text = "句" * 600  # 群消息正文另有 MAX_CHARS=200 截断，日志口径独立钉 [:500]
    ga = _patch(monkeypatch, factory, long_text)

    assert asyncio.run(ga.run_group_active(1, GROUP, OWNER, None)) is True

    logs = _dump(factory, ProactiveMessageLog)
    assert len(logs) == 1
    assert len(logs[0].content) <= 500


def test_invalid_llm_output_writes_nothing(tmp_path, monkeypatch):
    """③ LLM 输出无有效消息 → 群消息与日志都不落库（不提前写日志）。"""
    factory = _factory(tmp_path)
    _seed(factory)
    ga = _patch(monkeypatch, factory, "这不是 JSON，也不会落库")

    assert asyncio.run(ga.run_group_active(1, GROUP, OWNER, 2)) is False

    assert _dump(factory, ChatGroupMessage) == []
    assert _dump(factory, ProactiveMessageLog) == []
