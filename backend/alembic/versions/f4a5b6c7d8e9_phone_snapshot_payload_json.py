# -*- coding: utf-8 -*-
"""X7-M1 结构化承载：``phone_snapshots`` 新增 nullable 列 ``payload_json TEXT``。

Revision ID: f4a5b6c7d8e9
Revises: d1e2f3a4b5c6
Create Date: 2026-09-22

背景（派单 P7/P8 A 段）：手机端感知此前只有一条 ``content`` 文本（屏幕 OCR / 剪贴板拼好的整串），
消费方只能整段注入上下文。M1 起客户端可另传一份**字段级 JSON 对象**（如
``{"app":"微信","title":"张三","text":"你好"}``）存进本列：``api.phone.create_perception``
只接受「合法 JSON 对象且长度 ≤ 4000」，非法/超长一律丢弃该字段（快照本身照常写库）；
``device.port`` 解析成 ``value['structured']``，上下文段据此按字段渲染，无载荷时回落旧文本行。

口径（沿用先例 a3b4c5d6e7f8「加可空列」）：
  * **只加 nullable 列**：老行无需回填，SQLite 原生支持 ``ALTER TABLE ... ADD COLUMN``，
    ``batch_alter_table`` 在 ``recreate="auto"`` 下直接发原生 ALTER，不整表重建、不动数据；
  * ``_has_column`` 守卫 → 幂等：全新库由 init_db 的 ``create_all`` 直建（模型已含该列），
    本迁移命中守卫即 0 操作；「非空老库整链重放」（scripts/migrate_drill.py --base fresh 的
    02 形态）同样安全；
  * 库里连 ``phone_snapshots`` 都没有时 0 操作，交由建表路径按当前模型建（先例 d1e2f3a4b5c6 同口径）；
  * downgrade 可逆（带同样守卫后删列）：列可空且无索引/外键/视图依赖，删列不丢其它列的数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

TABLE = "phone_snapshots"
COLUMN = "payload_json"


def _has_column(bind, table: str, column: str) -> bool:
    try:
        insp = sa.inspect(bind)
        return column in {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        print(f"[{revision}] 库中无 {TABLE}（交由建表路径按当前模型建），0 操作")
        return
    if _has_column(bind, TABLE, COLUMN):
        print(f"[{revision}] {TABLE}.{COLUMN} 已存在（新库 create_all 已建），0 操作")
        return
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.Text(), nullable=True))
    if not _has_column(bind, TABLE, COLUMN):
        raise RuntimeError(f"升级后 {TABLE}.{COLUMN} 仍不存在——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE) or not _has_column(bind, TABLE, COLUMN):
        return  # 幂等：列已不存在则跳过
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_column(COLUMN)
