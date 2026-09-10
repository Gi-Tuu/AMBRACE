# -*- coding: utf-8 -*-
"""T1/T2 DB 兜底（2026-09-10 v3.4.6 第三轮止血批）：

- pets 加 abandoned_at（遗弃软删，非空=已遗弃；存量行默认 NULL=未遗弃）；
- pet_activities.pet_id 外键补 ON DELETE CASCADE 兜底（软删后行保留；防未来硬删路径悬空）；
- group_memories.group_id 外键补 ON DELETE CASCADE（删群兜底，应用层显式先删群记忆）；
- game_sessions.group_id 外键补 ON DELETE SET NULL（删群后对局历史保留，脱离群）。

外键改写与既有先例 c9d0e1f2a3b4 同口径：用 batch recreate + copy_from（以 ORM metadata
重建整表，FK/索引/约束与模型单一事实源对齐）。SQLite 不能 ALTER FK，必须整表重建。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# 需要重建外键的表（以当前 ORM metadata 为单一事实源整表重建）
REBUILD_FK_TABLES = [
    "pet_activities",
    "group_memories",
    "game_sessions",
]

# Alembic batch `copy_from` 重建会丢弃「mapped_column(index=True)」产生的隐式列索引
# （__table_args__ 显式 Index 保留）；此处对受影响表补回模型声明的列索引（幂等 IF NOT EXISTS）。
# 注：pet_activities 无列索引；group_memories/game_sessions 的索引名与 ORM 一致。
_COLUMN_INDEXES: dict[str, list[str]] = {
    "group_memories": [
        "CREATE INDEX IF NOT EXISTS ix_group_memories_group_id ON group_memories (group_id)",
        "CREATE INDEX IF NOT EXISTS ix_group_memories_user_id ON group_memories (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_group_memories_round_id ON group_memories (round_id)",
    ],
    "game_sessions": [
        "CREATE INDEX IF NOT EXISTS ix_game_sessions_game_type ON game_sessions (game_type)",
        "CREATE INDEX IF NOT EXISTS ix_game_sessions_user_id ON game_sessions (user_id)",
    ],
}

revision = "d0a1b2c3d4e5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base

    meta = Base.metadata
    conn = op.get_bind()

    # pets：加遗弃软删列（存量行 NULL=未遗弃）。has_column 守卫：init_db/create_all 已按当前
    # 模型建表的库（含该列）在版本链重放时直接跳过，避免「duplicate column」。
    _pets_cols = {c["name"] for c in sa_inspect(conn).get_columns("pets")}
    if "abandoned_at" not in _pets_cols:
        with op.batch_alter_table("pets") as b:
            b.add_column(sa.Column("abandoned_at", sa.DateTime(), nullable=True))

    # 外键兜底：整表重建（copy_from=当前 ORM metadata，含模型上的新 ondelete）
    for tname in REBUILD_FK_TABLES:
        if tname not in meta.tables:
            raise RuntimeError(f"ORM metadata 缺表 {tname}——中止，防止重建丢列")
        table = meta.tables[tname]
        op.execute("PRAGMA foreign_keys=OFF")
        with op.batch_alter_table(tname, recreate="always", copy_from=table):
            pass
        op.execute("PRAGMA foreign_keys=ON")
        # 补回 batch 重建丢弃的隐式列索引
        for _sql in _COLUMN_INDEXES.get(tname, []):
            op.execute(_sql)


def downgrade() -> None:
    # pets：回撤遗弃软删列（外键收紧与先例 c9d0e1f2a3b4 同口径不可逆，回滚请从迁移前备份恢复）
    conn = op.get_bind()
    _pets_cols = {c["name"] for c in sa_inspect(conn).get_columns("pets")}
    if "abandoned_at" in _pets_cols:
        with op.batch_alter_table("pets") as b:
            b.drop_column("abandoned_at")
