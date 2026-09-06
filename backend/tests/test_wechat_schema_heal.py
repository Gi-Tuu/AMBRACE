# -*- coding: utf-8 -*-
"""包 A（2026-09-06 待排期清理）：wechat_ilink_messages 表结构幂等自愈测试。

- 旧形态表（全量 UNIQUE(binding_id, ilink_msg_id) + 含空串 out 行数据）→ 自愈后
  索引/约束与 models 一致（partial unique）、数据完整、空串多行可落；
- 已新形态库幂等跳过（第二次调用 no-op）；
- 无 wechat 表/空库不报错。
"""
import asyncio
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

_PLUGIN_DIR = os.path.join(os.path.dirname(__file__).replace("\\tests", "\\tests"), "..", "..", "plugins", "examples", "wechat_ilink")
_PLUGIN_DIR = os.path.abspath(_PLUGIN_DIR)


@pytest.fixture()
def heal_db(tmp_path, monkeypatch):
    import sys

    if _PLUGIN_DIR not in sys.path:
        sys.path.insert(0, _PLUGIN_DIR)
    import schema_heal

    schema_heal.reset_heal_flag()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory, schema_heal
    schema_heal.reset_heal_flag()
    asyncio.run(engine.dispose())


async def _make_old_shape(factory, with_data=True):
    """旧形态：全量 UNIQUE(binding_id, ilink_msg_id)（约束 uq_wechat_ilink_msg），无 partial。"""
    async with factory() as db:
        await db.execute(text(
            "CREATE TABLE wechat_ilink_messages ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "binding_id BIGINT NOT NULL, character_id BIGINT NOT NULL, "
            "ilink_msg_id VARCHAR(128), context_token VARCHAR(255), direction VARCHAR(8) NOT NULL, "
            "content TEXT, quota_charged BOOLEAN, status VARCHAR(16), created_at DATETIME DEFAULT CURRENT_TIMESTAMP, "
            "CONSTRAINT uq_wechat_ilink_msg UNIQUE (binding_id, ilink_msg_id))"
        ))
        if with_data:
            await db.execute(text(
                "INSERT INTO wechat_ilink_messages (binding_id, character_id, ilink_msg_id, context_token,"
                " direction, content, quota_charged, status) VALUES"
                " (1, 101, 'm-1', '', 'in', '你好', 0, 'ok'),"
                " (1, 101, '', '', 'out', '回复一', 1, 'ok')"
            ))  # 旧全量唯一下第二条空串 out 行根本落不进（正是生产事故形态），自愈后可多条
        await db.commit()


def test_heal_rebuilds_old_shape_and_keeps_data(heal_db):
    factory, heal = heal_db
    asyncio.run(_make_old_shape(factory))

    result = asyncio.run(heal.ensure_messages_schema(factory))
    assert result == "rebuilt"

    async def _verify():
        async with factory() as db:
            # 数据完整（3 行全保留）
            n = (await db.execute(text("SELECT COUNT(*) FROM wechat_ilink_messages"))).scalar()
            # partial 唯一索引存在且带 WHERE
            sql = (await db.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_wechat_ilink_msg_in'"
            ))).scalar()
            # 空串 out 行现在可以再落（旧形态会撞 uq_wechat_ilink_msg）
            await db.execute(text(
                "INSERT INTO wechat_ilink_messages (binding_id, character_id, ilink_msg_id, direction,"
                " content, status) VALUES (1, 101, '', 'out', '回复三', 'ok')"
            ))
            await db.commit()
            n2 = (await db.execute(text("SELECT COUNT(*) FROM wechat_ilink_messages"))).scalar()
            return n, sql, n2

    n, sql, n2 = asyncio.run(_verify())
    assert n == 2  # 自愈前数据完整保留（in 1 条 + out 1 条）
    assert n2 == 3  # 自愈后空串 out 可多条落库（旧形态会撞全量唯一）
    assert sql and "WHERE" in sql.upper()
    # 幂等：再次调用跳过（once 标记 + 目标形态判定）
    assert asyncio.run(heal.ensure_messages_schema(factory)) == "ok"


def test_heal_skips_new_shape(heal_db):
    """已新形态（与 models 一致的目标 DDL）→ ok 且不动表。"""
    factory, heal = heal_db

    async def _create_all():
        import models  # noqa: F401

        async with factory() as db:
            await db.execute(text(
                "CREATE TABLE wechat_ilink_messages ("
                "id INTEGER NOT NULL PRIMARY KEY, binding_id BIGINT NOT NULL, character_id BIGINT NOT NULL, "
                "ilink_msg_id VARCHAR(128), context_token VARCHAR(255), direction VARCHAR(8) NOT NULL, "
                "content TEXT, quota_charged BOOLEAN, status VARCHAR(16), created_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
            ))
            await db.execute(text(
                "CREATE UNIQUE INDEX uq_wechat_ilink_msg_in ON wechat_ilink_messages"
                " (binding_id, ilink_msg_id) WHERE ilink_msg_id != ''"
            ))
            await db.commit()

    asyncio.run(_create_all())
    assert asyncio.run(heal.ensure_messages_schema(factory)) == "ok"


def test_heal_no_table_no_error(heal_db):
    factory, heal = heal_db
    assert asyncio.run(heal.ensure_messages_schema(factory)) == "no_table"
    assert asyncio.run(heal.ensure_messages_schema(factory)) == "no_table"  # once 后仍安全


def test_heal_old_shape_write_conflict_before_fix(heal_db):
    """根因锚定：旧形态下两条空串 out 行本来就会撞全量唯一（自愈后消失）。"""
    factory, heal = heal_db
    asyncio.run(_make_old_shape(factory, with_data=False))

    async def _insert_two_out():
        async with factory() as db:
            for content in ("回复一", "回复二"):
                await db.execute(text(
                    "INSERT INTO wechat_ilink_messages (binding_id, character_id, ilink_msg_id, direction,"
                    f" content, status) VALUES (1, 101, '', 'out', '{content}', 'ok')"
                ))
            await db.commit()

    with pytest.raises(IntegrityError):
        asyncio.run(_insert_two_out())
    # 自愈后可落
    asyncio.run(heal.ensure_messages_schema(factory))
    asyncio.run(_insert_two_out())  # 不再抛
