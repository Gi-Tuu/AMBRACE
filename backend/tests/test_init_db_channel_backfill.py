# -*- coding: utf-8 -*-
"""P3-1 渠道旧 config 幂等回填（create_all 路径）单测。

对照 alembic a7b8c9d0e1f2 的 _backfill_channel_bindings，验证 init_db 启动期
`_backfill_channel_bindings_from_global_config` 的等价回填：空表+旧 config → 回填正确；
表非空/无旧 config → 跳过；异常 → fail-open 启动不炸；连跑两次 → 行数不变（幂等）。

每个用例用 tmp_path 下唯一私有文件库（pytest 卫生纪律：不入系统 %TEMP%），自建最小 schema，
直接驱动被回填函数、断言行字段。不触碰生产库、不重启服务。
"""
import asyncio
import uuid

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.init_db import _backfill_channel_bindings_from_global_config

# 最小 schema（仅回填函数查询的 4 张表）
_BASE_TABLES = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, parent_id INTEGER)",
    "CREATE TABLE ai_characters (id INTEGER PRIMARY KEY, user_id INTEGER)",
    "CREATE TABLE plugins (id INTEGER PRIMARY KEY, name VARCHAR(100) UNIQUE NOT NULL, config_json TEXT DEFAULT '{}')",
]
_CHANNEL_BINDINGS_OK = (
    "CREATE TABLE channel_bindings ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " channel VARCHAR(32) NOT NULL,"
    " tenant_id BIGINT NOT NULL,"
    " owner_user_id BIGINT NOT NULL,"
    " bot_account_id VARCHAR(128) DEFAULT 'default',"
    " bot_label VARCHAR(100) DEFAULT '',"
    " character_id BIGINT NOT NULL,"
    " enabled BOOLEAN DEFAULT 1,"
    " extra_json VARCHAR(2000) DEFAULT '{}',"
    " created_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
    " updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,"
    " UNIQUE(channel, tenant_id, bot_account_id))"
)
# 故意缺 extra_json 列 → 回填 INSERT 抛 OperationalError，用于验证 fail-open
_CHANNEL_BINDINGS_MALFORMED = (
    "CREATE TABLE channel_bindings ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " channel VARCHAR(32) NOT NULL,"
    " tenant_id BIGINT NOT NULL,"
    " owner_user_id BIGINT NOT NULL,"
    " bot_account_id VARCHAR(128) DEFAULT 'default',"
    " bot_label VARCHAR(100) DEFAULT '',"
    " character_id BIGINT NOT NULL,"
    " enabled BOOLEAN DEFAULT 1)"
)


async def _scenario(tmp_path, statements, *, malformed=False, twice=False):
    """在私有临时库（每次调用唯一子目录）建最小 schema+种子，驱动回填，返回 channel_bindings 行（dict）。"""
    run_dir = tmp_path / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=True)
    dsn = f"sqlite+aiosqlite:///{run_dir}/t.db"
    engine = create_async_engine(dsn, poolclass=sa.pool.NullPool)
    cb_schema = _CHANNEL_BINDINGS_MALFORMED if malformed else _CHANNEL_BINDINGS_OK
    async with engine.begin() as conn:
        for st in _BASE_TABLES:
            await conn.execute(text(st))
        await conn.execute(text(cb_schema))
        for st in statements:
            await conn.execute(text(st))
        await _backfill_channel_bindings_from_global_config(conn)
        if twice:
            await _backfill_channel_bindings_from_global_config(conn)
        result = await conn.execute(text("SELECT * FROM channel_bindings ORDER BY id"))
        rows = [dict(r) for r in result.mappings().fetchall()]
    await engine.dispose()
    return rows


# ① 空表 + 有旧 config → 回填正确（tenant 取家庭 root=parent_id）
def test_empty_table_with_old_config_backfills(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": 100}')",
    ]))
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["channel"] == "wechat"
    assert row["tenant_id"] == 5
    assert row["owner_user_id"] == 10
    assert row["bot_account_id"] == "default"
    assert row["bot_label"] == ""
    assert row["character_id"] == 100
    assert row["enabled"] == 1
    assert row["extra_json"] == "{}"


# ② 表非空 → 跳过（仅保留既有行，不重复搬）
def test_nonempty_table_skips(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": 100}')",
        "INSERT INTO channel_bindings (channel, tenant_id, owner_user_id, bot_account_id,"
        " bot_label, character_id, enabled, extra_json)"
        " VALUES ('wechat', 999, 999, 'default', '', 100, 1, '{}')",
    ]))
    assert len(rows) == 1, rows
    # 必须是既有行（tenant=999），backfill 未插入新行
    assert rows[0]["tenant_id"] == 999


# ②b tenant 取 owner 自身（无 parent_id 时回落 owner）
def test_tenant_falls_back_to_owner_without_parent(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id) VALUES (10)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": 100}')",
    ]))
    assert len(rows) == 1, rows
    assert rows[0]["tenant_id"] == 10
    assert rows[0]["owner_user_id"] == 10


# ③ 无旧 config → 跳过（无 plugin 行 / config 无 allowed_character_ids）
def test_no_old_config_skips(tmp_path):
    # 场景 A：连 plugin 行都没有
    rows_a = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
    ]))
    assert rows_a == []
    # 场景 B：有 plugin 但 config 无对应键
    rows_b = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{}')",
    ]))
    assert rows_b == []


# ④ 抛异常 → fail-open 且启动不炸（malformed 表导致 INSERT 失败，函数须正常返回）
def test_fail_open_on_exception(tmp_path):
    # malformed channel_bindings（缺 extra_json）→ 回填 INSERT 抛错；须被捕获、不向上抛
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": 100}')",
    ], malformed=True))
    # 函数正常返回（未抛异常），且未写入任何行
    assert rows == []


# ⑤ 连跑两次 → 行数不变（幂等）
def test_idempotent_run_twice(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (10, 5)",
        "INSERT INTO ai_characters (id, user_id) VALUES (100, 10)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": 100}')",
    ], twice=True))
    assert len(rows) == 1, rows
    assert rows[0]["character_id"] == 100


# ⑤b 旧模型本就单选：allowed_character_ids 为列表时只搬第一条
def test_single_select_only_first_id(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id) VALUES (20), (21)",
        "INSERT INTO ai_characters (id, user_id) VALUES (200, 20), (201, 21)",
        "INSERT INTO plugins (name, config_json) VALUES ('wechat_ilink', '{\"allowed_character_ids\": [200, 201]}')",
    ]))
    assert len(rows) == 1, rows
    assert rows[0]["character_id"] == 200


# douyin_mcp 旧 config 同样回填（与 wechat 同款逻辑）
def test_douyin_channel_backfills(tmp_path):
    rows = asyncio.run(_scenario(tmp_path, [
        "INSERT INTO users (id, parent_id) VALUES (30, 7)",
        "INSERT INTO ai_characters (id, user_id) VALUES (300, 30)",
        "INSERT INTO plugins (name, config_json) VALUES ('douyin_mcp', '{\"allowed_character_ids\": 300}')",
    ]))
    assert len(rows) == 1, rows
    assert rows[0]["channel"] == "douyin"
    assert rows[0]["tenant_id"] == 7
    assert rows[0]["character_id"] == 300
