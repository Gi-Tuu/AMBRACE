# -*- coding: utf-8 -*-
"""补回 batch recreate / has_table 守卫静默丢失的隐式列索引（v3.4.6 第四轮，2026-09-10）。

背景（第四轮 I1+I2+I3，共 32 个索引）：
- ``c9d0e1f2a3b4`` 对 48 张表执行 ``batch_alter_table(recreate="always", copy_from=...)`` 重建时，
  ``mapped_column(index=True)`` 的隐式单列索引未被重建（``__table_args__`` 显式 Index 保留）；
- ``d0a1b2c3d4e5`` 重建 ``group_memories`` 后手列补索引，漏了 ``created_at``（I2）；
- ``e7f8a9b0c1d2`` 的 ``has_table`` 守卫使 ``account_invites`` 的 ``create_index`` 被一并跳过（I3）。

本迁移不重建表、不碰数据，只按 ORM metadata 对比实际 schema，缺啥补啥（先对比再建 → 幂等）。
`init_db()`/`create_all` 建的全新库本就齐全 → 0 操作；整链 `upgrade head` 重放的临时库会被
`c9d0e1f2a3b4` 再丢一次同样的 31 个 → 本迁移一并补齐；生产老库一次性补齐 32 个；
未来再有同类遗漏，本迁移仍可兜住。

实测（生产库一致性副本）：补前缺失 32 → 创建 32 → 补后缺失 0 → 再跑一次创建 0，
关键表行数前后零变动（方案 §7.1）。
"""
import importlib.util as _ilu
import os as _os

from alembic import op

revision = "e1b2c3d4e5f6"
down_revision = "d0a1b2c3d4e5"
branch_labels = None
depends_on = None

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)


def _ensure_indexes_fn():
    """取 ``alembic/_helpers.py::ensure_indexes``。

    不能写 ``from alembic import _helpers``：version 模块由 ``util.load_python_file`` 以
    ``spec_from_file_location`` 载入（无包上下文），且 ``backend/alembic`` 与已安装的
    ``alembic`` 库同名。这里按绝对路径加载，不改 sys.path、不受 cwd 影响。
    """
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def _base_metadata():
    """当前完整 ORM metadata（导入全部模型后取 Base.metadata）。"""
    import app.models  # noqa: F401  # 确保所有模型注册到 Base.metadata
    from app.models.base import Base

    return Base.metadata


def upgrade() -> None:
    created = _ensure_indexes_fn()(op, _base_metadata())
    # 迁移日志核对用：全新安装（create_all 库）应打印 created 0；整链重放的新库 31；生产老库 32
    print(f"[backfill_indexes] created {len(created)} indexes: {created}")


def downgrade() -> None:
    # 有意「不回滚索引」，只回退版本记账（对数据与正确性零影响）。
    #
    # 方案 §2.4 的动态 downgrade（按同一对比逻辑 DROP 所有 metadata 声明索引）经实测会误删
    # **先例迁移建的**索引，使版本链回退报 "no such index"：
    #   · 回退到中间 revision：d4e5f6a7b8c9 的 downgrade 会再 drop idx_pis_char（已被本迁移误删）
    #     → OperationalError（tests/test_memory_supersede_migration.py 已复现）；
    #   · 回退到 base：1d19fa0a34c9 的 downgrade 对其中 32 个索引是无守卫
    #     `batch_op.drop_index(...)`，同样 no such index。
    # 而「只 DROP 这 32 个」的静态白名单同样会让 baseline 的无守卫 DROP 失败。故唯一能保证
    # 整链全方向可回退的写法就是不动索引——补索引是纯性能增益、幂等、不改 schema，保留无害。
    # 项目先例：d0a1b2c3d4e5 的 FK 收紧同样在 downgrade 里声明不可逆。
    #
    # 如需强制回到「补索引前」状态，等价操作是 DROP 掉本迁移 upgrade 打印的那份索引清单。
    pass
