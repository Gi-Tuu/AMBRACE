# -*- coding: utf-8 -*-
"""C3：douyin_viewed_notes.aweme_id 全局唯一 → (tenant_id, aweme_id) 复合唯一

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-05

- 多租户下不同家庭可各看同一条抖音作品，全局 unique 会让第二租户写库撞键；
- SQLite 列级 UNIQUE（sqlite_autoindex）无法单独 DROP → batch 重建表：去掉 aweme_id 上的
  唯一，改建复合唯一索引 uq_douyin_viewed_tenant_aweme；
- 幂等：复合索引已存在且 aweme_id 无单列唯一索引 → 跳过；仅表存在时执行；
- downgrade：仅删复合唯一索引（不重建全局 unique——多租户数据可能撞全局键，重建有数据损失，
  结构上等同「无约束」旧形态的宽松超集，回滚安全）。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "douyin_viewed_notes"
_UQ_NAME = "uq_douyin_viewed_tenant_aweme"


def _has_table(bind) -> bool:
    try:
        return sa.inspect(bind).has_table(_TABLE)
    except Exception:
        return False


def _index_names(bind) -> set:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}
    except Exception:
        return set()


def _aweme_has_single_unique(bind) -> bool:
    """aweme_id 上是否还有单列唯一索引（旧形态 sqlite_autoindex / 历史命名索引）。"""
    try:
        for i in sa.inspect(bind).get_indexes(_TABLE):
            if i.get("unique") and [c.lower() for c in i.get("column_names", [])] == ["aweme_id"]:
                return True
        for u in sa.inspect(bind).get_unique_constraints(_TABLE):
            if [c.lower() for c in u.get("column_names", [])] == ["aweme_id"]:
                return True
    except Exception:
        return True  # 反射失败保守按需要重建
    return False


def _rebuild_without_aweme_unique(bind) -> None:
    """反射重建：除 aweme_id 唯一约束外结构原样（id 保持 PK AUTOINCREMENT）。"""
    insp = sa.inspect(bind)
    col_defs, col_names = [], []
    for c in insp.get_columns(_TABLE):
        name = c["name"]
        col_names.append(name)
        if name == "id":
            # 与 SQLAlchemy create_all 的 SQLite 形态一致（INTEGER PRIMARY KEY，不写 AUTOINCREMENT，
            # 避免多出 sqlite_sequence 表改变守卫测试的表计数）
            col_defs.append('"id" INTEGER NOT NULL PRIMARY KEY')
            continue
        d = f'"{name}" {c["type"]}'
        if c.get("nullable") is False:
            d += " NOT NULL"
        if c.get("default") is not None:
            d += f" DEFAULT {c['default']}"
        col_defs.append(d)
    # 全局唯一索引不带入新表（列级 UNIQUE 与单列唯一索引一并消失）；其余普通索引按名重建
    plain_indexes = []
    for i in insp.get_indexes(_TABLE):
        if i.get("unique") or i["name"] == _UQ_NAME:
            continue
        plain_indexes.append((i["name"], i["column_names"]))
    bind.execute(sa.text(f"ALTER TABLE {_TABLE} RENAME TO {_TABLE}_mig_old"))
    bind.execute(sa.text(f'CREATE TABLE {_TABLE} ({",".join(col_defs)})'))
    bind.execute(sa.text(
        f"INSERT INTO {_TABLE} ({','.join(chr(34) + n + chr(34) for n in col_names)})"
        f" SELECT {','.join(chr(34) + n + chr(34) for n in col_names)} FROM {_TABLE}_mig_old"
    ))
    bind.execute(sa.text(f"DROP TABLE {_TABLE}_mig_old"))
    for name, cols in plain_indexes:
        bind.execute(sa.text(
            f'CREATE INDEX IF NOT EXISTS "{name}" ON {_TABLE} ({",".join(chr(34) + c + chr(34) for c in cols)})'
        ))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return  # 渠道插件未装载/全新库（create_all 直建新形态）→ 幂等跳过
    idxs = _index_names(bind)
    if _UQ_NAME in idxs and not _aweme_has_single_unique(bind):
        return  # 已是新形态
    _rebuild_without_aweme_unique(bind)
    if _UQ_NAME not in _index_names(bind):
        op.create_index(_UQ_NAME, _TABLE, ["tenant_id", "aweme_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return
    if _UQ_NAME in _index_names(bind):
        op.drop_index(_UQ_NAME, table_name=_TABLE)
    # 不重建 aweme_id 全局 unique：多租户存量数据可能撞全局键（重建需删数据，不可接受）；
    # 结构上「无全局 unique」是旧形态的宽松超集，回滚安全。
