# -*- coding: utf-8 -*-
"""群文件：chat_group_messages 增加 file_url / file_name / file_size（2026-11-22）。

## 背景
PWA / App 的群聊此前只有文字。群文件消息需要持久化原始文件名、字节数和服务端
``/uploads/...`` URL；文件二进制仍由既有 upload_service 保存，不进入消息正文。

## 契约
* ``content`` 保留为展示用文件名（兼容旧客户端把它当文字消息读）；
* 三个新列全部 nullable：历史文字消息保持 NULL，不需要回填；
* 文件 URL 是服务端相对路径，客户端按 API base 拼接，不保存绝对主机名。

## 形式
* 表名写字面量，符合 ``app/db/migrate.py`` 的版本链扫描；
* 幂等：列已存在则跳过（测试库/重放安全）；
* downgrade 用 batch 重建删除三列。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f8d7c6b5a4"
down_revision: Union[str, None] = "c6f8b0d2e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None

_TABLE = "chat_group_messages"
_COLUMNS = (
    sa.Column("file_url", sa.String(length=500), nullable=True),
    sa.Column("file_name", sa.String(length=255), nullable=True),
    sa.Column("file_size", sa.Integer(), nullable=True),
)


def _existing_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(_TABLE) as batch:
        for col in _COLUMNS:
            if col.name not in existing:
                batch.add_column(col)


def downgrade() -> None:
    existing = _existing_columns()
    with op.batch_alter_table(_TABLE) as batch:
        for col in reversed(_COLUMNS):
            if col.name in existing:
                batch.drop_column(col.name)
