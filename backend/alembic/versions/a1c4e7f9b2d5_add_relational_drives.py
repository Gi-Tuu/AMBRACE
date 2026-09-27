# -*- coding: utf-8 -*-
"""A4 批 3 / T1 M1a（2026-09-27）：新增关系驱力水位表 relational_drives。

Revision ID: a1c4e7f9b2d5
Revises: c6d7e8f9a0b1
Create Date: 2026-09-27

- relational_drives：行粒度 =（角色 × 用户 × 驱力类型），存「指向某个用户、按类型持续
  累积、只能被该用户的真实互动释放」的水位（level 0–100）与懒结算游标（last_settled_at）。
  设计依据 AMBRACE_批3_T1关系驱力层_详细设计_v1_20260927.md §3.1：不挂 character_states
  （该表每角色唯一一行，家庭共享口径下会把「想跟谁说话」糊成一团）、不用 JSON 单列
  （不能按 key 做 SQL 排序，且两处写点整列读改写易互相覆盖）、不复用 relationship_events
  （事件流水不是可涨可落的水位）。

**本单只做存储，零行为**：全仓无任何读写方（增长/封顶/夜间倍率/释放/候选的算法在
app/domain/relational/ 纯函数域，settle 与释放钩子属下一单 M1b）。跑完本迁移后产品行为
逐字节不变，唯一变化＝库里多一张空表。

形态：唯一约束 (character_id, user_id, drive_key) 保证「一格一行」；索引
(character_id, user_id, level) 服务唯一热路径「取该角色对该用户水位最高的驱力」。
外键沿用 NO ACTION（与 users 子表同口径，角色删除时的清理策略属钩子单，本单不预设）。

幂等：has_table 守卫（重复 upgrade 直接跳过；老库整链重放安全）。
可逆：downgrade 带同样守卫后 drop_table（连带索引/唯一约束）——表内只有可重算的水位，
无外部引用、无视图依赖，删表不伤其它表数据。

配套硬要求（漏了会永久缺表）：app/db/migrate.py 的 _CURRENT_SCHEMA_SENTINELS 必须登记
("relational_drives", "level")——本表只由版本链引入、init_db 幂等层不补，老库（有表无版本
号）缺表时必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c4e7f9b2d5"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "relational_drives"
_INDEX = "ix_relational_drives_char_user_level"
_UQ = "uq_relational_drives_char_user_key"


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _has_index(bind, table: str, index: str) -> bool:
    try:
        return index in {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        # 表名写【字面量】（不用 _TABLE）：db/migrate.py 的 _migration_chain_tables 按
        # 「create_table('字面量'」正则收集版本链建表清单，用变量会让自动判别漏掉本表。
        op.create_table(
            "relational_drives",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("drive_key", sa.String(length=20), nullable=False),
            sa.Column("level", sa.Float(), nullable=False),
            sa.Column("last_settled_at", sa.DateTime(), nullable=False),
            sa.Column("last_released_at", sa.DateTime(), nullable=True),
            sa.Column("last_released_ratio", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["character_id"], ["ai_characters.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("character_id", "user_id", "drive_key", name=_UQ),
        )
    if not _has_index(bind, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["character_id", "user_id", "level"], unique=False)
    # 收尾回验：表在位则索引必在位（幂等重跑时本就在，不会误报）
    if not _has_index(op.get_bind(), _TABLE, _INDEX):
        raise RuntimeError(f"升级后 {_TABLE} 仍缺索引 {_INDEX}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        if _has_index(bind, _TABLE, _INDEX):
            op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
