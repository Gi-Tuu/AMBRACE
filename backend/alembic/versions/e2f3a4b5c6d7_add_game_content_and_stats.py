# -*- coding: utf-8 -*-
"""#62 群聊游戏 Phase 3：游戏内容源 + 成就与统计

Revision ID: e2f3a4b5c6d7
Revises: b7c8d9e0f1a2
Create Date: 2026-09-16

- game_content_overrides：用户自定义词库/题库（user_id + game_type + content_key 唯一），
  运行时「用户自定义 > 插件内容包 > 内置常量」的最高优先级来源；
- game_stats：按 user（character_id IS NULL）/ character / game_type 累计战绩
  （局数/胜/负/平/无胜负终止/总回合/最近一局）。用户行与角色行分别用部分唯一索引落唯一；
- game_achievements：成就解锁记录（含定义快照），按 (user, character, game_type, key)
  部分唯一索引保证「同一成就只解锁一次」。

幂等守卫（has_table / 索引名）与可逆 downgrade；只建上述三张表，不动既有表。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONTENT_TABLE = "game_content_overrides"
STATS_TABLE = "game_stats"
ACH_TABLE = "game_achievements"

CONTENT_INDEXES = ("ux_game_content_user_key",)
STATS_INDEXES = ("ux_game_stats_user", "ux_game_stats_char", "ix_game_stats_user_id",
                 "ix_game_stats_character_id", "ix_game_stats_game_type")
ACH_INDEXES = ("ux_game_ach_user", "ux_game_ach_char", "ix_game_achievements_user_id",
               "ix_game_achievements_character_id")


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, CONTENT_TABLE):
        op.create_table(
            CONTENT_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("game_type", sa.String(length=30), nullable=False),
            sa.Column("content_key", sa.String(length=40), nullable=False),
            sa.Column("values_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
    have = _index_names(bind, CONTENT_TABLE)
    if "ix_game_content_overrides_user_id" not in have:
        op.create_index("ix_game_content_overrides_user_id", CONTENT_TABLE, ["user_id"])
    if "ix_game_content_overrides_game_type" not in have:
        op.create_index("ix_game_content_overrides_game_type", CONTENT_TABLE, ["game_type"])
    if "ux_game_content_user_key" not in have:
        op.create_index("ux_game_content_user_key", CONTENT_TABLE,
                        ["user_id", "game_type", "content_key"], unique=True)

    if not _has_table(bind, STATS_TABLE):
        op.create_table(
            STATS_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=True),
            sa.Column("game_type", sa.String(length=30), nullable=False),
            sa.Column("games_played", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("aborted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_rounds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_played_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["character_id"], ["ai_characters.id"], ondelete="CASCADE"),
        )
    have = _index_names(bind, STATS_TABLE)
    if "ix_game_stats_user_id" not in have:
        op.create_index("ix_game_stats_user_id", STATS_TABLE, ["user_id"])
    if "ix_game_stats_character_id" not in have:
        op.create_index("ix_game_stats_character_id", STATS_TABLE, ["character_id"])
    if "ix_game_stats_game_type" not in have:
        op.create_index("ix_game_stats_game_type", STATS_TABLE, ["game_type"])
    if "ux_game_stats_user" not in have:
        op.create_index("ux_game_stats_user", STATS_TABLE, ["user_id", "game_type"],
                        unique=True, sqlite_where=sa.text("character_id IS NULL"))
    if "ux_game_stats_char" not in have:
        op.create_index("ux_game_stats_char", STATS_TABLE, ["user_id", "game_type", "character_id"],
                        unique=True, sqlite_where=sa.text("character_id IS NOT NULL"))

    if not _has_table(bind, ACH_TABLE):
        op.create_table(
            ACH_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=True),
            sa.Column("game_type", sa.String(length=30), nullable=False, server_default="*"),
            sa.Column("achievement_key", sa.String(length=50), nullable=False),
            sa.Column("title", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("description", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("target", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("unlocked", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("unlocked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["character_id"], ["ai_characters.id"], ondelete="CASCADE"),
        )
    have = _index_names(bind, ACH_TABLE)
    if "ix_game_achievements_user_id" not in have:
        op.create_index("ix_game_achievements_user_id", ACH_TABLE, ["user_id"])
    if "ix_game_achievements_character_id" not in have:
        op.create_index("ix_game_achievements_character_id", ACH_TABLE, ["character_id"])
    if "ux_game_ach_user" not in have:
        op.create_index("ux_game_ach_user", ACH_TABLE, ["user_id", "game_type", "achievement_key"],
                        unique=True, sqlite_where=sa.text("character_id IS NULL"))
    if "ux_game_ach_char" not in have:
        op.create_index("ux_game_ach_char", ACH_TABLE,
                        ["user_id", "game_type", "character_id", "achievement_key"],
                        unique=True, sqlite_where=sa.text("character_id IS NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    for table, indexes in (
        (ACH_TABLE, ACH_INDEXES),
        (STATS_TABLE, STATS_INDEXES),
        (CONTENT_TABLE, CONTENT_INDEXES),
    ):
        if not _has_table(bind, table):
            continue
        have = _index_names(bind, table)
        for name in indexes:
            if name in have:
                op.drop_index(name, table_name=table)
        op.drop_table(table)
