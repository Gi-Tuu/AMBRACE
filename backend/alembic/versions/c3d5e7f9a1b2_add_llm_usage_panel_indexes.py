# -*- coding: utf-8 -*-
"""A4 批 8 / 块 D「费用面板」M1（2026-09-30）：llm_usage 补两条复合索引。

Revision ID: c3d5e7f9a1b2
Revises: b2d4f6a8c0e1
Create Date: 2026-09-30

为什么只加索引（本批唯一 schema 动作）：费用面板按「账号 × 窗口」过滤后在内存里分桶
（读端 app/application/system.py 的 usage_panel / _read_usage_window，复用 usage_report 的窗口
读法内核），而 llm_usage 原本只有 created_at 单列索引（models/agent/__init__.py LlmUsage）。
设计依据 AMBRACE_批8_接口面与形态护栏_详细设计_v1_20260929.md §2.4（3）。

**不动列、不动数据、不动写路径**：
- ix_llm_usage_user_created (user_id, created_at)：面板/用量的账号范围过滤（user_id IN(...) 或
  IS NULL）+ 窗口边界，是本轮读法真正走到的路径（tests/test_usage_panel_m0m1.py 用
  EXPLAIN QUERY PLAN 钉住命中）。user_id 可为 NULL（服务器级行），SQLite 索引照收。
- ix_llm_usage_task_created (task, created_at)：**如实记录**——本批读端是「一次窗口 SELECT +
  内存分桶」，不按 task 过滤，故当前**没有查询消费这条**（按派单建议登记，等把「按用途」
  下推成 SQL 条件的读法出现才生效；§2.4(3)「无证据不动 schema」对这条只做到一半）。
  task 可为 NULL（未归因历史行，审计 P1-07 起才写），读端归 (untagged) 桶。

幂等：has_table + has_index 双守卫（老库整链重放安全、重复 upgrade 不报错；表不存在则跳过）。
可逆：downgrade 带同样守卫后 drop_index——删索引不动表与数据。
收尾回验：表在位却仍缺索引 → 抛错中止，防静默漂移（同 b2d4f6a8c0e1 / a6b7c8d9e0f1 口径）。
形态照抄同表的 b6c7d8e9f0a1（只建索引，SQLite 无需 batch_alter_table）。

配套硬要求（漏了会永久缺索引）：app/db/migrate.py 的 _CURRENT_SCHEMA_SENTINELS 必须以
**idx: 前缀**登记这两条 (表, 索引)——本索引只由版本链引入、init_db 幂等层不补；老库（有表
无版本号）缺索引时必须判「落后」走 upgrade head，否则会被 stamp 到 head 却永久缺索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d5e7f9a1b2"
down_revision: Union[str, None] = "b2d4f6a8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "llm_usage"
# (索引名, 列序列) —— 与 models/agent/__init__.py LlmUsage.__table_args__ 一一对应
_INDEXES = (
    ("ix_llm_usage_user_created", ("user_id", "created_at")),
    ("ix_llm_usage_task_created", ("task", "created_at")),
)


def _has_table(bind, table: str) -> bool:
    try:
        return sa.inspect(bind).has_table(table)
    except Exception:
        return False


def _index_names(bind, table: str) -> set:
    try:
        return {i["name"] for i in sa.inspect(bind).get_indexes(table)}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    have = _index_names(bind, _TABLE)
    for name, columns in _INDEXES:
        if name not in have:
            op.create_index(name, _TABLE, list(columns), unique=False)
    # 收尾回验：表存在则索引必须在位（幂等重跑时本就在，不会误报）
    after = _index_names(op.get_bind(), _TABLE)
    missing = [name for name, _c in _INDEXES if name not in after]
    if missing:
        raise RuntimeError(f"升级后 {_TABLE} 仍缺索引 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    have = _index_names(bind, _TABLE)
    for name, _columns in _INDEXES:
        if name in have:
            op.drop_index(name, table_name=_TABLE)
