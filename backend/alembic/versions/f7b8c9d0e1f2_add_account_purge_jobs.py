# -*- coding: utf-8 -*-
"""控制台删号·第二期第一批：新增 ``account_purge_jobs``（物理清除器的进度账本）。

Revision ID: f7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-24

为什么需要这张表（方案 v2 §六「删一半进程被杀」+ 派单第 7 项）
--------------------------------------------------------------
单账号最多 40 万行，清除必然跨多次 ``await``（分批 + 让出事件循环），期间 watchdog /
重启 / 异常都可能把它打断。进度若只记在内存里，重启后无从知道「删到哪了」，
只能整个重跑（大表重扫）或干脆放弃（留下半删状态）。故清除器把账本落库：

- ``user_id``：**一个账号一行**（UNIQUE）——它就是续跑的定位键；``users.id`` 自增不回收，
  故不存在「同 id 第二个账号」撞键；
- ``status``：``running`` / ``done`` / ``failed``；
- ``started_at`` / ``finished_at``：耗时与观测；
- ``cursor_json``：第 0 步固化的三份集合（角色 id / 会话 id / 记忆 id）+ 各表已删行数 +
  已完成阶段标记。**固化集合必须落盘**：``ai_characters`` / ``chat_sessions`` 行删掉后，
  这些 id 永久不可反查（v2 §3.2 第 0 步的同一理由），续跑只能靠它；
- ``report_json``：给控制台/审计回显的摘要（各表行数、文件与 trash、向量与 BM25 计数、耗时）；
- ``error``：失败原因（含报错表名与语句原文）。

口径（**2026-09-24 Codex 复核订正：本表会进 Base.metadata**）：
  * 该表**必须有 ORM 模型**（``app/models/user/__init__.py::AccountPurgeJob``，DDL 与本迁移逐项对齐）：
    哨兵登记的表若不在 metadata，「主 ORM 建齐、只缺插件表」的库会被反复判落后
    （tests/test_migrate_schema_detect.py::test_manual_sentinel_ignores_plugin_tables），
    与 ``user_runtime_flags`` / ``user_llm_limits`` 同口径；
  * 本迁移的 ``create_table`` 负责老库补齐，并登记进 ``app/db/migrate.py`` 的 ``_CURRENT_SCHEMA_SENTINELS``；
    版本链建表全集自动比对（``_migration_chain_tables``）同样覆盖它，双保险；
  * ``has_table`` 守卫 → 幂等：非空老库整链重放时命中守卫即 0 操作，不报错；
  * **升级后回验**：断言表与全部列在位，缺任一即抛错中止（缺表会让清除器无法记账，
    表现为「删一半没法续跑」，不能静默）；
  * downgrade 可逆（drop 表）：回退即丢失清除进度账本，语义不可逆（业务数据不受影响）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "account_purge_jobs"
COLUMNS = ("id", "user_id", "status", "started_at", "finished_at",
           "cursor_json", "report_json", "error")


def _has_column(bind, table: str, column: str) -> bool:
    """列在位守卫：直查 ``PRAGMA table_info``（表不存在/异常一律 False）。"""
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
        # 表名写**字面量**（不用 TABLE 常量）：migrate.py 的 _migration_chain_tables 靠正则
        # 匹配「create_table 后紧跟的字符串字面量」收集「版本链建过的表」，变量形态会漏登记 →
        # 自动比对失去覆盖。⚠ 别在这段注释里照抄那句正则样例：正则扫的是**文件全文（含注释）**，
        # 注释里的样例会让集合凭空多出一张幽灵表，于是「当前 schema」永远判落后、演练 03 形态红
        # （2026-09-24 本批实测踩到，已修）。
        op.create_table(
            "account_purge_jobs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            # UNIQUE：一账号一账本（续跑定位键）；不设 FK——账号行正是本表要记录「怎么删掉」的对象
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="running"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("cursor_json", sa.Text(), nullable=True),
            sa.Column("report_json", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", name="uq_account_purge_jobs_user_id"),
        )
        print(f"[{revision}] 已建表 {TABLE}")
    else:
        print(f"[{revision}] 表 {TABLE} 已存在（幂等跳过）")
    missing = [c for c in COLUMNS if not _has_column(bind, TABLE, c)]
    if missing:
        raise RuntimeError(f"升级后 {TABLE} 仍缺列 {missing}——中止，防止静默漂移")


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return  # 幂等：表本就不在
    op.drop_table(TABLE)
    # 回退即丢失清除进度账本（哪些号删到哪一步）；业务数据行不受影响。
