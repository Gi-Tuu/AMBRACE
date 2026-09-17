# -*- coding: utf-8 -*-
"""群认知表单列索引补齐 + user_facts 易变槽 TTL 列（记忆时态缺陷族·第二批任务3 + 任务2.5）。

Revision ID: b2c3d4e5f6a7
Revises: a3b4c5d6e7f8
Create Date: 2026-09-17

1) #72 群认知表 ``group_char_cognitions``（迁移 f3c4d5e6f7a1 建表）只建了复合索引
   ``idx_gcc_group_char`` / ``idx_gcc_round``；ORM 声明的单列索引（``user_id`` /
   ``character_id`` / ``created_at`` / ``group_id`` / ``round_id``）在 **alembic 整链重放出来的库**
   上缺失（``create_all`` 建的新库本就齐全，生产本机库实测 7 条索引全在）。
   复用 ``e1b2c3d4e5f6`` 引入的 ``alembic/_helpers.ensure_indexes``：对比 ORM metadata 与实际库
   差集补建，新库 0 操作、老库一次补齐、可重复执行（本迁移亦兜住未来同类遗漏）。

2) ``user_facts`` 增 ``valid_to``（易变槽 location/living/job/health 的 TTL 截止；NULL=不过期），
   对齐 world_facts 的时效链，避免「权威值本身永不失效」（生产实证：09-17 无关聊天行
   覆盖 location 权威值，真实权威被挤进 previous_value）。

幂等：``has_table`` / ``has_column`` 守卫 + ``ensure_indexes`` 差集，重放/重跑零副作用。
downgrade 有意 no-op（沿用 ``e1b2c3d4e5f6`` 已被复核认可的口径）：补索引是纯性能增益、幂等、
不改 schema 语义，且动态 DROP 会误删先例迁移建的索引使整链回退报 no such index；
``valid_to`` 是 nullable 增量列，旧代码忽略即可（向后兼容），整链 ``downgrade base`` 时
``user_facts`` 表由上游 ``e5f6a7b8c9d0`` 的 downgrade 连带删除。
"""
import importlib.util as _ilu
import os as _os

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)


def _ensure_indexes_fn():
    """取 ``alembic/_helpers.py::ensure_indexes``（同 e1b2c3d4e5f6 的加载方式）。"""
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _base_metadata():
    """当前完整 ORM metadata（导入全部模型后取 Base.metadata）。"""
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base

    return Base.metadata


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    try:
        return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    # ── 1) user_facts.valid_to（幂等；nullable 增量列，SQLite 原生 ADD COLUMN，不重建表）──
    if _has_table(bind, "user_facts") and not _has_column(bind, "user_facts", "valid_to"):
        with op.batch_alter_table("user_facts", schema=None) as batch_op:
            batch_op.add_column(sa.Column("valid_to", sa.DateTime(), nullable=True))
    # ── 2) 补齐 ORM 声明但实际库缺失的索引（含 group_char_cognitions 单列索引）──
    created = _ensure_indexes_fn()(op, _base_metadata())
    print(f"[gcc_indexes_user_fact_validity] created {len(created)} indexes: {created}")


def downgrade() -> None:
    # 有意 no-op（同 e1b2c3d4e5f6）：见模块 docstring 的「downgrade 有意 no-op」说明。
    pass
