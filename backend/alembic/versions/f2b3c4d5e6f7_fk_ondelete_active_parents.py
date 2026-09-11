# -*- coding: utf-8 -*-
"""P3-1：活跃业务父表（会话/群/朋友圈/对局）子外键 ondelete 纵深落库（2026-09-11）。

背景：c9d0e1f2a3b4 只落了 ai_characters 父表 FK 的 ondelete；引用 chat_sessions /
chat_groups / ai_moments / game_sessions 的子外键在模型层就没写 ondelete，物理层全是
NO ACTION（=RESTRICT）。开启 PRAGMA foreign_keys 后，删会话/群/动态/对局会因子行存在被
阻塞（即「FK 开启后删除/测试失败」同类隐患）。本迁移：
  1) 模型层 15 处 ForeignKey 补 ondelete（配套模型改动，单一事实源）；
  2) SQLite 不能 ALTER FK，对 14 张物理子表 batch 重建（copy_from=改后 ORM Table）；
  3) 重建后调 ensure_indexes 兜底（第四轮教训：batch recreate 会丢 mapped_column
     (index=True) 隐式单列索引）。
users 父表约 37 条子 FK 本轮不动（删账号属高危不可逆、SaaS 多租户才需要，见方案 §3.5）。
"""
import importlib.util as _ilu
import os as _os

from alembic import op

revision = "f2b3c4d5e6f7"
down_revision = "e1b2c3d4e5f6"
branch_labels = None
depends_on = None

# alembic/_helpers.py 的绝对路径（version 模块由 alembic 按文件加载，没有包上下文）。
_HELPERS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "_helpers.py"
)

# 14 张需重建的物理子表（moment_comments 一表含 moment_id + parent_id 两处 FK）
REBUILD_TABLES = [
    # —— chat_sessions 父表（6 子表）——
    "chat_messages", "daily_summaries", "pending_permission_actions",
    "proactive_message_logs", "proactive_storyline_items", "scheduled_events",
    # —— chat_groups 父表（2 子表）——
    "chat_group_members", "chat_group_messages",
    # —— ai_moments 父表（3 子表，moment_comments 含自引用）——
    "moment_likes", "moment_ai_likes", "moment_comments",
    # —— game_sessions 父表（3 子表）——
    "game_players", "game_events", "game_memories",
]


def _ensure_indexes_fn():
    """按绝对路径加载 alembic/_helpers.py（version 模块无包上下文，不能 from alembic import
    _helpers——会撞已安装的 alembic 库；照 e1b2c3d4e5f6 先例）。"""
    spec = _ilu.spec_from_file_location("ambrace_alembic_helpers", _HELPERS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ensure_indexes


def upgrade() -> None:
    import app.models  # noqa: F401  确保全部模型注册
    from app.models._all import Base

    meta = Base.metadata
    for tname in REBUILD_TABLES:
        if tname not in meta.tables:
            raise RuntimeError(f"ORM metadata 缺表 {tname}——中止，防止重建丢列")
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        for tname in REBUILD_TABLES:
            table = meta.tables[tname]
            with op.batch_alter_table(tname, recreate="always", copy_from=table):
                pass
    finally:
        op.execute("PRAGMA foreign_keys=ON")
    # 第四轮沉淀约定：凡 batch recreate(copy_from=...)，upgrade 末尾必须 ensure_indexes 兜底
    created = _ensure_indexes_fn()(op, meta)
    if created:
        print(f"[fk_ondelete_active_parents] backfill indexes after rebuild: {created}")


def downgrade() -> None:
    # FK ondelete 收紧不可逆（先例 c9d0e1f2a3b4 / d0a1b2c3d4e5 / e1b2c3d4e5f6 均 no-op）。
    # 如需回到迁移前状态，请从迁移前备份恢复数据库文件。
    pass
