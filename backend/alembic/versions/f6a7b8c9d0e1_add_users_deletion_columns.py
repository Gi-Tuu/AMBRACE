# -*- coding: utf-8 -*-
"""控制台删号·第一期地基：``users`` 新增 nullable 列 ``deleted_at`` / ``purge_after``。

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0
Create Date: 2026-09-24

背景（方案 v2 §3.2 阶段一，2026-09-24 拍板「回收站 + 7 天宽限期」）：全仓此前**没有任何删号能力**
——``users`` 只有 ``disabled_at``（禁用）与 ``llm_mode``，连软删字段都没有。本迁移把两阶段删除的
状态位列建出来（第一期一次建好地基，第二期只补后台清除器，不返工）：

- ``deleted_at``：非空 = 已标记删除（进回收站）；写该列的同时强制写 ``disabled_at``，
  restore 清空两列；
- ``purge_after``：宽限期到点时间（UTC naive），后台清除器只扫
  ``deleted_at IS NOT NULL AND purge_after <= now``。

口径（沿用先例 f4a5b6c7d8e9「加可空列」）：
  * **只加 nullable 列**：老行无需回填（NULL = 未进回收站，现有账号行为逐字节不变），
    SQLite 原生支持 ``ALTER TABLE ... ADD COLUMN``，``batch_alter_table`` 在 ``recreate="auto"``
    下直接发原生 ALTER，不整表重建、不动数据；
  * ``_has_column`` 守卫（**直查 ``PRAGMA table_info``**）→ 幂等：全新库由 init_db 的
    ``create_all`` 直建（模型已含两列），本迁移命中守卫即 0 操作；「非空老库整链重放」
    （scripts/migrate_drill.py --base fresh 的 02 形态）同样安全，可重复执行不报错；
  * 库里连 ``users`` 都没有时 0 操作，交由建表路径按当前模型建（先例 f4a5b6c7d8e9 同口径）；
  * **升级后回验**：断言两列都在位，缺任一即抛错中止，防止静默漂移（缺列会让
    ``GET /server/accounts`` 与回收站标记直接报错，而不是悄悄少一个字段）；
  * downgrade 可逆（带同样守卫后用 ``batch_alter_table`` 删列）：两列均可空、无索引/外键/视图
    依赖，删列不丢其它列的数据；回退即丢失回收站状态（哪些号在等清除），语义不可逆。

本迁移同时把两列登记进 ``app/db/migrate.py`` 的 ``_CURRENT_SCHEMA_SENTINELS``：老库（有表但无
版本号）缺此列必须判「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "users"
COLUMNS = ("deleted_at", "purge_after")


def _has_column(bind, table: str, column: str) -> bool:
    """列在位守卫：直查 ``PRAGMA table_info``（表不存在/异常一律 False → 跳过，不阻塞链）。

    表名/列名走标识符白名单再拼接——PRAGMA 不支持绑定参数，而本迁移的标识符都是上面的常量，
    白名单只是把「别把外部输入喂进来」这件事写死。
    """
    if not (table.replace("_", "").isalnum() and column.replace("_", "").isalnum()):
        return False
    try:
        rows = bind.execute(sa.text(f'PRAGMA table_info("{table}")')).fetchall()
    except Exception:
        return False
    return column in {r[1] for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        print(f"[{revision}] 库中无 {TABLE}（交由建表路径按当前模型建），0 操作")
        return
    for col in COLUMNS:
        if _has_column(bind, TABLE, col):
            print(f"[{revision}] {TABLE}.{col} 已存在（新库 create_all 已建），跳过")
            continue
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column(col, sa.DateTime(), nullable=True))
    missing = [c for c in COLUMNS if not _has_column(bind, TABLE, c)]
    if missing:
        raise RuntimeError(f"升级后 {TABLE} 仍缺列 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return
    for col in COLUMNS:
        if not _has_column(bind, TABLE, col):
            continue  # 幂等：列已不存在则跳过
        with op.batch_alter_table(TABLE, schema=None) as batch_op:
            batch_op.drop_column(col)
    # 回收站状态随列一并消失（语义不可逆）：回退后所有账号回到「无删除态」。
