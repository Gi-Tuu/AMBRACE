# -*- coding: utf-8 -*-
"""A31 兜底：memories 同桶「置顶 + 未归档」最多一条（部分唯一索引，2026-10-07）。

背景
----
A31 修了「节流看最新、重写写最旧」与「同组不收口」两个缺陷，代码层已保证同桶只留一条置顶；
本迁移补 DB 级保险（纵深防御）：同一 (character_id, memory_type, 桶) 再插第二条置顶，写入时会被拒绝。

桶口径与 A31 完全一致（不一致会让约束与代码语义打架）：
  - sub_type 为 NULL 或 summary ⇒ 归普通摘要桶（索引里统一折叠为空串）；
  - 其余（如 identity）按自身值分桶。

前置：A.3.3 一次性回填已把存量多余置顶降级（实测 4 条：7410 / 6055 / 6651 / 5559），
回填后每桶恰好 1 条置顶（36 组 / 36 条）⇒ 索引可建。

已知边界（有意为之）
----
模型层（app/models）不声明该索引：测试夹具需要构造「多条置顶」的非法中间态来验证 A31 的收口逻辑；
若模型层带唯一索引，这些用例会在构造阶段就被数据库拒绝。故约束只存在于迁移链的物理层，
与仓库既有「模型层 vs 物理层」口径一致（同 c8f1a2b3d4e5 那条补 ON DELETE CASCADE 的处理方式）。
"""
from alembic import op
from sqlalchemy import inspect as _sa_inspect

revision = "d1a2b3c4e5f6"
down_revision = "c8f1a2b3d4e5"
branch_labels = None
depends_on = None

_INDEX = "ux_memories_pinned_active"


def upgrade() -> None:
    # 插件裸 schema 库（只有插件自己的表，没有 memories）⇒ 跳过本步，沿用 c0d1e2f3a4b5 / 24d19ef3 约定。
    if not _sa_inspect(op.get_bind()).has_table("memories"):
        return
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX}
        ON memories (
            character_id,
            memory_type,
            CASE WHEN sub_type IS NULL OR sub_type = 'summary' THEN '' ELSE sub_type END
        )
        WHERE is_pinned = 1 AND is_archived = 0
        """
    )


def downgrade() -> None:
    if not _sa_inspect(op.get_bind()).has_table("memories"):
        return
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")