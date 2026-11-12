# -*- coding: utf-8 -*-
"""FK ondelete 落库 + configs 去 users FK + ai_moments/weave_cards 可空化（2026-09-09 v3.4.6 第三批）。

背景：豆包 v3.4.6 全量检查 P0——模型层已把 43 张含 ai_characters FK 的表补 ondelete
（44 CASCADE + 2 SET NULL），5 张 configs 表去掉 user_id→users FK（服务器级 0/-1 哨兵），
ai_moments/weave_cards.character_id 改可空。SQLite 不能 ALTER FK，本迁移对 48 张表
batch 重建（copy_from=当前 ORM metadata，DB schema 与模型单一事实源对齐）。
"""
from alembic import op
from sqlalchemy import inspect as sa_inspect

REBUILD_TABLES = [
    "character_states", "character_state_history", "relationship_events",
    "state_trigger_logs", "storyline_events", "proactive_storyline_items",
    "proactive_settings", "proactive_message_logs", "proactive_trigger_logs",
    "life_states", "life_activity_logs", "life_artifacts", "life_interests",
    "life_goals", "life_schedules", "life_followups", "life_chat_intents",
    "ai_diaries", "ai_moments", "scheduled_events", "timeline_events",
    "memories", "conversation_topics", "stage_memories", "reflection_logs",
    "weave_cards", "weave_card_characters", "weave_card_memories",
    "chat_sessions", "chat_group_members", "chat_group_messages", "ai_chats",
    "phone_desktops", "phone_layouts", "calendar_notes", "memo_notes",
    "browser_history", "check_in_requests",
    "game_players", "game_memories",
    "emotion_care_tasks", "pending_permission_actions", "privacy_requests",
    "api_configs", "vlm_configs", "speech_configs", "multimodal_configs",
    "image_gen_configs",
]

revision = "c9d0e1f2a3b4"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def _rebuild_copy_table(meta, conn, tname):
    """batch recreate 的 copy_from：以当前 ORM metadata 为单一事实源（to_metadata 深拷贝列/列级外键/
    表级约束/索引，含 ondelete），但只保留『DB 已存在』的列——避免『未来迁移新增的列』（如本仓后续给
    group_memories 加 is_archived）被提前 SELECT，导致从基线 alembic upgrade head 失败。

    对『已升级到本迁移之后』的存量库无行为变化（未来列尚未存在，过滤结果即全模型列），
    仅修复『从基线整链重放』路径；列上的外键（含 ondelete）随列保留 → 重建后 ondelete 生效。
    """
    from sqlalchemy import (
        ForeignKeyConstraint as _FK,
        Index as _Idx,
        MetaData as _MD,
        Table as _Tbl,
    )

    model_table = meta.tables[tname]
    existing = {c["name"] for c in sa_inspect(conn).get_columns(tname)}
    # 深拷贝，避免直接改全局 Base.metadata；逐列 append（列级外键随列保留）再补回表级约束/索引
    src = model_table.to_metadata(_MD())
    new_t = _Tbl(src.name, _MD(), **src.kwargs)
    for col in src.columns:
        if col.name in existing:
            new_t.append_column(col._copy())
    for const in src.constraints:
        if isinstance(const, _FK):
            if set(c.name for c in const.columns) <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
        elif not getattr(const, "_column_flag", False):
            if set(c.name for c in const.columns) <= existing:
                new_t.append_constraint(const._copy(target_table=new_t))
    for idx in src.indexes:
        if set(c.name for c in idx.columns) <= existing:
            _Idx(
                idx.name,
                unique=idx.unique,
                *[new_t.c[c.name] for c in idx.columns],
                **idx.kwargs,
            )
    return new_t


def upgrade() -> None:
    import app.models  # noqa: F401
    from app.models._all import Base

    meta = Base.metadata
    conn = op.get_bind()
    for tname in REBUILD_TABLES:
        if tname not in meta.tables:
            raise RuntimeError(f"ORM metadata 缺表 {tname}——中止，防止重建丢列")
        copy_t = _rebuild_copy_table(meta, conn, tname)
        op.execute("PRAGMA foreign_keys=OFF")
        with op.batch_alter_table(tname, recreate="always", copy_from=copy_t):
            pass
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    # 表结构收紧不可逆；回滚请从迁移前备份恢复
    pass
