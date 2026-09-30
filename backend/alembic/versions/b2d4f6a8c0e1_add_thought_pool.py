# -*- coding: utf-8 -*-
"""A4 批 4 / T2 M1（2026-09-30）：新增念头池表 thought_pool。

Revision ID: b2d4f6a8c0e1
Revises: d7e8f9a0b1c2
Create Date: 2026-09-30

- thought_pool：行粒度 =（角色 × 用户 × 一条来源信号），存「还没说出口的那件具体的事」——
  成念文本、来源面与来源主键、双量（salt / novelty 入池快照）、状态机与释放计数。设计依据
  AMBRACE_批4_念头池T2_详细设计_v1_20260929.md §2.5 方案 C（新建窄表）：不复用 ``memories``
  （念头会进记忆召回/归档，把「谈资」污染成「事实」）、不复用 ``life_chat_intents``（那是生活
  loop 的行动计划，被消费时会吃掉谈资）、**不挂 character_states**（八维唯一维护方是
  character_state_service，设计 §2.4 第 3 条硬规则）。

形态：唯一约束 (character_id, user_id, source_type, source_ref, text_hash) ＝幂等入池键；
索引 (character_id, user_id, status, salt) 服务唯一热路径「取该角色对该用户可用的一条念头」。
``user_id`` 用 0 表示「角色级、无用户维度」（F1 活动 / F6 兴趣的生产表只有 character_id），
因此该列**不挂外键**——SQLite 唯一约束里 NULL 互不相等，用 NULL 会让重复抽取绕过幂等键。

**本单零行为**：跑完本迁移后产品行为逐字节不变，唯一变化＝库里多一张空表（影子供给
``app/application/thought_pool_service.py`` 由 flag ``thought_pool_shadow`` 总闸，默认关；
本批无发送权，不改任何发送文本）。

幂等：has_table 守卫（重复 upgrade 直接跳过；老库整链重放安全）。
可逆：downgrade 带同样守卫后 drop_table（连带索引/唯一约束）——表内只有可重算的候选，
无外部引用、无视图依赖，删表不伤其它表数据。

配套硬要求（漏了会永久缺表）：app/db/migrate.py 的 _CURRENT_SCHEMA_SENTINELS 必须登记
("thought_pool", "salt")——本表只由版本链引入、init_db 幂等层不补，老库（有表无版本号）
缺表时必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c0e1"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "thought_pool"
_INDEX = "ix_thought_pool_char_user_status_salt"
_UQ = "uq_thought_pool_char_user_src_ref_hash"


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
            "thought_pool",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("character_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("thought_kind", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("text", sa.String(length=120), nullable=False),
            sa.Column("source_type", sa.String(length=20), nullable=False),
            sa.Column("source_ref", sa.String(length=64), nullable=False),
            sa.Column("text_hash", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="spark"),
            sa.Column("salt", sa.Float(), nullable=False, server_default="0"),
            sa.Column("novelty", sa.Float(), nullable=False, server_default="1"),
            sa.Column("hit_sources", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("tell_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.Column("last_hit_at", sa.DateTime(), nullable=True),
            sa.Column("spent_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
            sa.ForeignKeyConstraint(["character_id"], ["ai_characters.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "character_id", "user_id", "source_type", "source_ref", "text_hash", name=_UQ
            ),
        )
    if not _has_index(bind, _TABLE, _INDEX):
        op.create_index(_INDEX, _TABLE, ["character_id", "user_id", "status", "salt"], unique=False)
    # 收尾回验：表在位则索引必在位（幂等重跑时本就在，不会误报）
    if not _has_index(op.get_bind(), _TABLE, _INDEX):
        raise RuntimeError(f"升级后 {_TABLE} 仍缺索引 {_INDEX}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE):
        if _has_index(bind, _TABLE, _INDEX):
            op.drop_index(_INDEX, table_name=_TABLE)
        op.drop_table(_TABLE)
