# -*- coding: utf-8 -*-
"""C1b（X7 遗留②）：行动确认策略按账号落库 —— 新增 ``device_action_policies`` 一张表。

Revision ID: f8a9b0c1d2e3
Revises: f7b8c9d0e1f2
Create Date: 2026-09-25

背景（派单 C1b）：M4c-5 落地的三档确认策略（轻 ``once_ever`` / 中 ``first_per_type`` /
重 ``every_time``）此前**只存 App 本机 shared_preferences**（``device_action_policy_<capability>``），
换机、重装、清数据后一律回到缺省档，用户配的「重」被静默放宽。本批升级为**按账号服务端持久化**
（服务端为权威、本机 prefs 降级为缓存与离线回落）。表在 ``app/models/device`` 里是
:class:`~app.models.device.DeviceActionPolicy`（DDL 与本迁移逐项对齐）。

形态口径：
- **一人一条能力一行**：联合唯一 ``(user_id, capability)``（幂等 upsert 的判据）；
  ``user_id`` 单列索引（读取按账号整取）。``user_id`` 不挂 FK——与 ``account_purge_jobs``
  同口径，删号链路由 ``app/application/user_cascade.py`` 按列名识别归属列覆盖（无需改代码）。
- **无行＝没配过**（不是「缺省档的一行」）：端点 GET 只回有行的能力，App 回落缺省档，
  因此本迁移不写入任何初始行（给全量用户预建三行会把「没配过」与「配成中档」混为一谈）。
- ``capability`` 只允许 ``kind="act"`` 的三条、``policy`` 只允许三档字面量——合法性在**端点**校验
  （非法 400），本表不加 CHECK（与两份行动名单表一致，避免约束口径两处漂移）。
- 刻意**不含**「轻档永久放行」标记（``device_action_once_ever_*``）：那属于跨端放开执行，另行拍板。

口径（沿用先例 f5a6b7c8d9e0 / f7b8c9d0e1f2「新建表」）：
  * ``has_table`` 守卫 → **幂等**：全新库由 init_db 的 ``create_all`` 按当前模型直建，本迁移命中
    守卫即 0 操作；「非空老库整链重放」（scripts/migrate_drill.py 的 02 形态）在此真正建表；
  * 表名在 ``create_table`` 里写**字面量**（不用常量）：``app/db/migrate.py`` 的
    ``_migration_chain_tables`` 靠正则收集「版本链建过的表」，变量形态会漏登记 → 自动比对失去
    覆盖（故本批无需再往 ``_CURRENT_SCHEMA_SENTINELS`` 手工补哨兵，自动比对已兜住「老库缺表」）；
  * 升级后**回验**表与全部列都在位，缺任一即抛错中止，防止静默漂移（缺表时端点写库会失败，
    App 看到的是「保存失败」而不是「没建表」，问题会被掩盖）；
  * downgrade 可逆（带同样守卫后 drop，先删索引再删表）：回退即丢失各账号已配的档位
    （语义不可逆），行为回退到 M4c-5 的「只存本机」。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "f7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "device_action_policies"
COLUMNS = ("id", "user_id", "capability", "policy", "created_at", "updated_at")
USER_INDEX = "ix_device_action_policies_user_id"


def _has_table(bind) -> bool:
    """表在位守卫（异常一律 False → 跳过，不阻塞链）。"""
    try:
        return sa.inspect(bind).has_table(TABLE)
    except Exception:
        return False


def _has_column(bind, column: str) -> bool:
    """列在位守卫：直查 ``PRAGMA table_info``（表不存在/异常一律 False）。"""
    if not (TABLE.replace("_", "").isalnum() and column.replace("_", "").isalnum()):
        return False
    try:
        rows = bind.execute(sa.text(f'PRAGMA table_info("{TABLE}")')).fetchall()
    except Exception:
        return False
    return column in {r[1] for r in rows}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        op.create_table(
            "device_action_policies",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            # 不挂 FK：删号链路由 user_cascade 按列名识别归属列（USER_FAMILY_COLUMNS 已含 user_id）
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("capability", sa.String(length=64), nullable=False),
            sa.Column("policy", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "capability",
                                name="uq_device_action_policy_user_cap"),
        )
        # 索引名与 ORM 隐式名一致（ix_<表>_<列>），否则 create_all 直建的新库与本迁移建出的库形态不同
        op.create_index(USER_INDEX, TABLE, ["user_id"])
        print(f"[{revision}] 已建表 {TABLE}")
    else:
        print(f"[{revision}] 表 {TABLE} 已存在（幂等跳过）")

    missing = [c for c in COLUMNS if not _has_column(bind, c)]
    if missing:
        raise RuntimeError(f"升级后 {TABLE} 仍缺列 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind):
        return  # 幂等：表本就不在
    try:
        op.drop_index(USER_INDEX, table_name=TABLE)
    except Exception:
        pass  # 索引可能随表消失（create_all 直建/老库形态差异），删不动不影响回退语义
    op.drop_table(TABLE)
    # 回退即丢失各账号已配的档位（本机 prefs 仍是 App 实际读取的缓存，不随之删除）。
