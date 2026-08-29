"""fix is_admin / parent_id consistency (#68 修订)

Revision ID: f1a2b3c4d5e6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-28 01:00:00.000000

#68 修订：
- 所有独立主账号（parent_id IS NULL）统一设为 is_admin=1；
- 所有子账号（parent_id IS NOT NULL）统一设为 is_admin=0。
保证「独立主账号即管理员，子账号非管理员」的不变量。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    # 独立主账号 → is_admin=1
    bind.execute(sa.text("UPDATE users SET is_admin = 1 WHERE parent_id IS NULL"))
    # 子账号 → is_admin=0
    bind.execute(sa.text("UPDATE users SET is_admin = 0 WHERE parent_id IS NOT NULL"))


def downgrade() -> None:
    # 不可逆：不恢复旧状态（旧状态本身就是不一致的）
    pass
