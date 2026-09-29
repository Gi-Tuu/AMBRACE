# -*- coding: utf-8 -*-
"""断点 #4（2026-09-29，U4）：``phone_snapshots`` 补归属三列 actor / character_id / confidence。

Revision ID: d7e8f9a0b1c2
Revises: a1c4e7f9b2d5
Create Date: 2026-09-29

架构地图断点 #4：手机感知快照此前只有 ``user_id`` 一个归属维度，导致
「同账号所有角色看同一批快照」与「用户行为 / 角色行为无法区分」。本迁移**只补数据面**：

- ``actor``        VARCHAR(16) NULL：来源主体，取值约定 user / character / system；
- ``character_id`` INTEGER     NULL：角色归属（FK ``ai_characters.id`` ON DELETE SET NULL）；
- ``confidence``   REAL        NULL：采集置信度（预留，写入侧暂不填）。

零行为：可空、无 server_default、不建索引、**不回填历史行**（老行 actor 仍为 NULL——
「上线前的历史」与「将来某处漏填」保留区分能力；且迁移跑在启动路径上，全表 UPDATE
等于给每次冷启动加时长，同 b4c5d6e7f8a9 口径）。可见性收窄（按角色过滤）属后续批次，
本批读侧一律不动。

幂等：``has_table`` + ``has_column`` 双守卫——create_all 路径（新库已含三列）与远古库整链
重放都 0 操作，可重复执行；库里连 ``phone_snapshots`` 都没有时 0 操作，交由建表路径按当前模型建。
只加列不重建表：SQLite 原生 ``ADD COLUMN`` 支持带 FK 的可空列（默认必须为 NULL），无需
``batch_alter_table``。
可逆：downgrade 带同样守卫后按列反序 ``drop_column``（回退即丢失归属记录，数据语义不可逆）；
删列不伤其余列与行。
收尾回验：表在位却仍缺任一列 → 抛错中止，防静默漂移。

配套硬要求（漏了会永久缺列）：``app/db/migrate.py`` 的 ``_CURRENT_SCHEMA_SENTINELS`` 必须登记
(``phone_snapshots``, ``actor``/``character_id``/``confidence``) 三条——本三列只由版本链引入、
init_db 幂等层不补，老库（有表无版本号）缺列时必须判「落后」走 upgrade head。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "a1c4e7f9b2d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "phone_snapshots"

_COLUMN_NAMES = ("actor", "character_id", "confidence")


def _has_table(bind) -> bool:
    try:
        return sa.inspect(bind).has_table(TABLE)
    except Exception:
        return False


def _has_column(bind, name: str) -> bool:
    try:
        inspector = sa.inspect(bind)
        if not inspector.has_table(TABLE):
            return False
        return name in {c["name"] for c in inspector.get_columns(TABLE)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        print(f"[{revision}] 库中无 {TABLE}（交由建表路径按当前模型建），0 操作")
        return

    def _add(name: str, col) -> None:
        if not _has_column(bind, name):
            op.add_column(TABLE, col)

    _add("actor", sa.Column("actor", sa.String(length=16), nullable=True))
    # character_id 用原生 DDL 而不是 op.add_column：后者会把内联 FK 拆成独立的
    # ``ALTER TABLE ... ADD CONSTRAINT``，SQLite 直接不支持；改走 batch_alter_table 又能建 FK
    # 但会**重建整表**，违背本批「只加列不重建表」。SQLite/Postgres 都允许在 ADD COLUMN 上直接挂
    # REFERENCES（要求该列默认 NULL，本列正是可空），故一条原生 DDL 两头都对。
    if not _has_column(bind, "character_id"):
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN character_id INTEGER "
            "REFERENCES ai_characters (id) ON DELETE SET NULL"
        )
    _add("confidence", sa.Column("confidence", sa.Float(), nullable=True))

    # 收尾回验：三列必须全部在位，否则中止（幂等重跑时列本就在，不会误报）
    missing = [name for name in _COLUMN_NAMES if not _has_column(bind, name)]
    if missing:
        raise RuntimeError(f"升级后 {TABLE} 仍缺列 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    for name in reversed(_COLUMN_NAMES):
        if _has_column(bind, name):
            op.drop_column(TABLE, name)
