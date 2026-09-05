# -*- coding: utf-8 -*-
"""一机多主：渠道绑定 per-账号化（channel_bindings 新表 + 微信/抖音表租户化）

Revision ID: a7b8c9d0e1f2
Revises: f3a4b5c6d7e8
Create Date: 2026-09-05

- 新增内核表 channel_bindings：(channel, tenant_id, bot_account_id) → character_id，
  UQ(channel,tenant,bot) 兜底并发双绑（全新库由 create_all 直建，upgrade 幂等跳过）；
- wechat_ilink_bindings：增 tenant_id（回填=user_id，绑定写入本就仅主账号）/ bot_account_id
  （回填='default'）；旧全库 UQ(character_id) 替换为两条唯一索引——
  uq_wechat_bot_wxuser（partial：ilink_user_id != ''，同 bot 下一微信用户唯一）
  + uq_wechat_tenant_bot_char（同租户同 bot 一角色）；重复 (bot,wxuser) 旧行先清 ilink_user_id 防撞；
- douyin 五表：user_id 正名 tenant_id（SQLite RENAME 经 batch 重建，连带 uq_douyin_comment
  约束列名更新）并去 default=1；douyin_accounts 增 bot_account_id/bot_label +
  uq_douyin_tenant_bot 唯一索引（存量重复 (tenant,'default') 行旧行改 legacy_{id} 防撞）；
- 数据回填：插件全局 config.allowed_character_ids（单选）→ 每 root 一行 channel_bindings
  （每渠道只在空表时搬一次）；douyin 存量 tenant_id=1 行归并到 douyin 绑定租户（可定则改，不可定保留）。
- 全程幂等可重放；回滚（downgrade）只删新表/新索引/新列，不改名不删数据。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOUYIN_TABLES = ("douyin_accounts", "douyin_posts", "douyin_comments", "douyin_pending", "douyin_viewed_notes")


def _inspector(bind):
    return sa.inspect(bind)


def _has_table(bind, table: str) -> bool:
    try:
        return _inspector(bind).has_table(table)
    except Exception:
        return False


def _columns(bind, table: str) -> set:
    try:
        return {c["name"] for c in _inspector(bind).get_columns(table)}
    except Exception:
        return set()


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in _inspector(bind).get_indexes(table)}
    except Exception:
        return set()


def _table_has_rows(bind, table: str) -> bool:
    try:
        row = bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()  # noqa: S608
        return row is not None
    except Exception:
        return False


def _upgrade_channel_bindings(bind) -> None:
    if _has_table(bind, "channel_bindings"):
        return
    op.create_table(
        "channel_bindings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("bot_account_id", sa.String(length=128), server_default="default"),
        sa.Column("bot_label", sa.String(length=100), server_default=""),
        sa.Column("character_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("1")),
        sa.Column("extra_json", sa.String(length=2000), server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("channel", "tenant_id", "bot_account_id", name="uq_channel_tenant_bot"),
    )
    op.create_index("ix_channel_binding_tenant", "channel_bindings", ["channel", "tenant_id"])
    op.create_index("ix_channel_binding_char", "channel_bindings", ["character_id"])


def _upgrade_wechat_bindings(bind) -> None:
    if not _has_table(bind, "wechat_ilink_bindings"):
        return
    cols = _columns(bind, "wechat_ilink_bindings")
    # 1) 增列（ADD COLUMN 带默认值，存量行立即生效）
    if "tenant_id" not in cols:
        op.add_column("wechat_ilink_bindings", sa.Column("tenant_id", sa.BigInteger(), server_default="0"))
    if "bot_account_id" not in cols:
        op.add_column("wechat_ilink_bindings", sa.Column("bot_account_id", sa.String(length=128), server_default="default"))
    # 2) 回填：tenant=user_id（绑定写入本就仅主账号，user_id 即家庭 root）；bot 恒 default
    op.execute("UPDATE wechat_ilink_bindings SET tenant_id = user_id WHERE tenant_id IS NULL OR tenant_id = 0")
    op.execute("UPDATE wechat_ilink_bindings SET bot_account_id = 'default' WHERE bot_account_id IS NULL OR bot_account_id = ''")
    # 3) 重复 (bot, wxuser) 旧行防撞：同组保留最新一行，旧行清 ilink_user_id（绑定可重扫恢复）
    op.execute(
        "UPDATE wechat_ilink_bindings SET ilink_user_id = '' WHERE ilink_user_id != '' AND id NOT IN ("
        " SELECT MAX(id) FROM wechat_ilink_bindings WHERE ilink_user_id != ''"
        " GROUP BY bot_account_id, ilink_user_id)"
    )
    # 4) 唯一键替换：named UQ(character_id) 走 batch 重建（SQLite 不支持 DROP CONSTRAINT）；
    #    新唯一键以唯一索引落地（partial 与 messages 表先例一致）
    idxs = _index_names(bind, "wechat_ilink_bindings")
    with op.batch_alter_table("wechat_ilink_bindings", schema=None) as batch_op:
        try:
            batch_op.drop_constraint("uq_wechat_ilink_char", type_="unique")
        except Exception:
            pass  # 旧库若已无该命名约束（或以同名列索引存在），保持幂等
    if "uq_wechat_bot_wxuser" not in idxs:
        op.create_index(
            "uq_wechat_bot_wxuser", "wechat_ilink_bindings",
            ["bot_account_id", "ilink_user_id"], unique=True,
            sqlite_where=sa.text("ilink_user_id != ''"),
            postgresql_where=sa.text("ilink_user_id != ''"),
        )
    idxs = _index_names(bind, "wechat_ilink_bindings")
    if "uq_wechat_tenant_bot_char" not in idxs:
        op.create_index(
            "uq_wechat_tenant_bot_char", "wechat_ilink_bindings",
            ["tenant_id", "bot_account_id", "character_id"], unique=True,
        )


def _upgrade_douyin(bind) -> None:
    # 1) 五表 user_id → tenant_id（batch 重建：RENAME + 去 default；uq_douyin_comment 约束列名连带更新）
    for table in _DOUYIN_TABLES:
        if not _has_table(bind, table):
            continue
        cols = _columns(bind, table)
        if "user_id" not in cols:
            continue
        col_def = next(c for c in _inspector(bind).get_columns(table) if c["name"] == "user_id")
        existing_default = col_def.get("server_default")
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                "user_id", new_column_name="tenant_id",
                existing_type=col_def["type"], existing_nullable=col_def.get("nullable", True),
                existing_server_default=existing_default, server_default=None,
            )
    if not _has_table(bind, "douyin_accounts"):
        return
    cols = _columns(bind, "douyin_accounts")
    # 2) douyin_accounts 增 bot 维度
    if "bot_account_id" not in cols:
        op.add_column("douyin_accounts", sa.Column("bot_account_id", sa.String(length=64), server_default="default"))
    if "bot_label" not in cols:
        op.add_column("douyin_accounts", sa.Column("bot_label", sa.String(length=100), server_default=""))
    op.execute("UPDATE douyin_accounts SET bot_account_id = 'default' WHERE bot_account_id IS NULL OR bot_account_id = ''")
    # 3) 存量重复 (tenant,'default') 行防撞：保留最新一行，旧行改 legacy_{id}
    op.execute(
        "UPDATE douyin_accounts SET bot_account_id = 'legacy_' || id WHERE id NOT IN ("
        " SELECT MAX(id) FROM douyin_accounts GROUP BY tenant_id, bot_account_id)"
    )
    idxs = _index_names(bind, "douyin_accounts")
    if "uq_douyin_tenant_bot" not in idxs:
        op.create_index("uq_douyin_tenant_bot", "douyin_accounts", ["tenant_id", "bot_account_id"], unique=True)


def _backfill_channel_bindings(bind) -> None:
    """插件全局 config.allowed_character_ids（单选）→ 每 root 一行 channel_bindings（空表时搬一次）。"""
    for plugin_name, channel in (("wechat_ilink", "wechat"), ("douyin_mcp", "douyin")):
        already = bind.execute(
            sa.text("SELECT 1 FROM channel_bindings WHERE channel = :ch LIMIT 1"), {"ch": channel}
        ).first()
        if already is not None or not _has_table(bind, "plugins"):
            continue
        prow = bind.execute(
            sa.text("SELECT config_json FROM plugins WHERE name = :n"), {"n": plugin_name}
        ).first()
        if prow is None:
            continue
        try:
            import json
            cfg = json.loads(prow[0] or "{}")
        except Exception:
            cfg = {}
        raw = cfg.get("allowed_character_ids", "")
        raw = ",".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        for cid in ids[:1]:  # 旧模型本就单选
            chrow = bind.execute(
                sa.text("SELECT user_id FROM ai_characters WHERE id = :cid"), {"cid": cid}
            ).first() if _has_table(bind, "ai_characters") else None
            if chrow is None:
                continue
            owner = int(chrow[0] or 0)
            if owner <= 0:
                continue
            urow = bind.execute(
                sa.text("SELECT parent_id FROM users WHERE id = :uid"), {"uid": owner}
            ).first() if _has_table(bind, "users") else None
            tenant = int(urow[0]) if (urow is not None and urow[0]) else owner
            bind.execute(
                sa.text(
                    "INSERT INTO channel_bindings (channel, tenant_id, owner_user_id, bot_account_id,"
                    " bot_label, character_id, enabled, extra_json)"
                    " VALUES (:ch, :t, :o, 'default', '', :cid, 1, '{}')"
                ),
                {"ch": channel, "t": tenant, "o": owner, "cid": cid},
            )


def _merge_douyin_legacy_tenant(bind) -> None:
    """douyin 存量 tenant_id=1 行归并到 douyin 绑定租户（可定则改，不可定保留 1）。"""
    if not _has_table(bind, "douyin_accounts") or not _table_has_rows(bind, "douyin_accounts"):
        return
    trow = bind.execute(
        sa.text("SELECT tenant_id FROM channel_bindings WHERE channel = 'douyin' LIMIT 1")
    ).first()
    if trow is None or int(trow[0]) == 1:
        return
    tenant = int(trow[0])
    for table in _DOUYIN_TABLES:
        if _has_table(bind, table):
            op.execute(f"UPDATE {table} SET tenant_id = {tenant} WHERE tenant_id = 1")  # noqa: S608 - 常量表名


def upgrade() -> None:
    bind = op.get_bind()
    _upgrade_channel_bindings(bind)
    _upgrade_wechat_bindings(bind)
    _upgrade_douyin(bind)
    _backfill_channel_bindings(bind)
    _merge_douyin_legacy_tenant(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "channel_bindings"):
        idxs = _index_names(bind, "channel_bindings")
        for name in ("ix_channel_binding_char", "ix_channel_binding_tenant"):
            if name in idxs:
                op.drop_index(name, table_name="channel_bindings")
        op.drop_table("channel_bindings")
    if _has_table(bind, "douyin_accounts"):
        idxs = _index_names(bind, "douyin_accounts")
        if "uq_douyin_tenant_bot" in idxs:
            op.drop_index("uq_douyin_tenant_bot", table_name="douyin_accounts")
    for table in _DOUYIN_TABLES:
        cols = _columns(bind, table) if _has_table(bind, table) else set()
        if "tenant_id" in cols:
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.alter_column("tenant_id", new_column_name="user_id", existing_type=sa.Integer())
    if _has_table(bind, "wechat_ilink_bindings"):
        idxs = _index_names(bind, "wechat_ilink_bindings")
        for name in ("uq_wechat_bot_wxuser", "uq_wechat_tenant_bot_char"):
            if name in idxs:
                op.drop_index(name, table_name="wechat_ilink_bindings")
        cols = _columns(bind, "wechat_ilink_bindings")
        with op.batch_alter_table("wechat_ilink_bindings", schema=None) as batch_op:
            batch_op.create_unique_constraint("uq_wechat_ilink_char", ["character_id"])
            if "bot_account_id" in cols:
                batch_op.drop_column("bot_account_id")
            if "tenant_id" in cols:
                batch_op.drop_column("tenant_id")
