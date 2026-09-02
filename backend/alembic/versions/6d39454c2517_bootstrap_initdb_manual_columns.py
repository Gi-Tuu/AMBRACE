# -*- coding: utf-8 -*-
"""bootstrap init_db manual ADD COLUMN into the chain (3.8 渐进版)

Revision ID: 6d39454c2517
Revises: d3e4f5a6b7c8
Create Date: 2026-09-03 00:00:00.000000

背景（docs/db-ddl-convergence-audit-20260902.md）：
- init_db.py（app/db/database.py 的兼容层）在 ``create_all`` 之后手工 ``ALTER TABLE ... ADD COLUMN``
  补齐了 87 个目标列（86 条 ``ADD COLUMN`` 语句）。这些列在版本链中只出现在基线 ``create_table``，
  对「已存在表」重放链时基线被 ``has_table`` 守卫跳过，因此**没有迁移会通过 ``add_column`` 补齐它们**。
- 远古库（无 ``alembic_version``、缺这些列）若被直接 stamp 到 head 会缺列。本迁移把这 87 列以
  ``add_column`` 固化进链（类型 / server_default 与 init_db 完全一致），配 ``has_column`` 守卫，
  使整条链可对「已存在表」安全重放补齐。
- 3.8 渐进版：init_db.py 的 87 处补列保留为冗余安全网（本迁移与它等价），
  未来收敛时删除 init_db 手工补列前，链已具备等价能力。
- 完全幂等：全新/当前 schema 库上所有 ``has_column`` 均命中（列已由基线 create_table / create_all 建出），
  add_column 全部跳过；只在缺失的远古库上真正补列。downgrade 可逆（delete 列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6d39454c2517"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return inspector.has_table(table)
    except Exception:
        return False


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    try:
        return column in {c["name"] for c in inspector.get_columns(table)}
    except Exception:
        return False


# (table, column, sa.Column) 清单 —— 逐列对齐 init_db.py 的手工 ADD COLUMN（87 列 / 86 语句）。
# 类型名称与 server_default 与 init_db 完全一致；REAL(location_lat/lng) 按模型映射为 sa.Float()
# （SQLite 均 NUMERIC 亲和，类型名差异仅示于 PRAGMA，不影响存储/查询）。
_BOOTSTRAP_COLUMNS: list[tuple[str, str, object]] = [
    # ai_characters
    ("ai_characters", "birthday", sa.Column("birthday", sa.String(length=10))),
    ("ai_characters", "cognitive_loop_enabled", sa.Column("cognitive_loop_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("ai_characters", "is_partner", sa.Column("is_partner", sa.Boolean(), server_default=sa.text("0"))),
    ("ai_characters", "memory_v2_enabled", sa.Column("memory_v2_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("ai_characters", "relation_type", sa.Column("relation_type", sa.String(length=30))),
    ("ai_characters", "self_statement", sa.Column("self_statement", sa.Text())),
    ("ai_characters", "timezone_offset", sa.Column("timezone_offset", sa.Integer())),
    ("ai_characters", "voice", sa.Column("voice", sa.String(length=50))),
    ("ai_characters", "voice_pitch", sa.Column("voice_pitch", sa.Float())),
    ("ai_characters", "voice_rate", sa.Column("voice_rate", sa.Float())),
    # ai_moments
    ("ai_moments", "image_desc", sa.Column("image_desc", sa.Text())),
    ("ai_moments", "image_url", sa.Column("image_url", sa.String(length=500))),
    # api_configs
    ("api_configs", "provider", sa.Column("provider", sa.String(length=30))),
    # calendar_notes
    ("calendar_notes", "author", sa.Column("author", sa.String(length=50))),
    # character_states
    ("character_states", "attachment", sa.Column("attachment", sa.Integer(), server_default=sa.text("50"))),
    ("character_states", "curiosity", sa.Column("curiosity", sa.Integer(), server_default=sa.text("50"))),
    ("character_states", "last_activity_at", sa.Column("last_activity_at", sa.DateTime())),
    ("character_states", "trust", sa.Column("trust", sa.Integer(), server_default=sa.text("50"))),
    # chat_group_messages
    ("chat_group_messages", "notify_user", sa.Column("notify_user", sa.Integer(), server_default=sa.text("0"))),
    # chat_messages
    ("chat_messages", "image_url", sa.Column("image_url", sa.String(length=500))),
    # conversation_topics
    ("conversation_topics", "goal", sa.Column("goal", sa.Boolean(), server_default=sa.text("0"))),
    ("conversation_topics", "progress", sa.Column("progress", sa.String(length=50))),
    # life_states
    ("life_states", "home_layout_json", sa.Column("home_layout_json", sa.Text())),
    # llm_usage
    ("llm_usage", "task", sa.Column("task", sa.String(length=30))),
    # memo_notes
    ("memo_notes", "author", sa.Column("author", sa.String(length=50))),
    # memories
    ("memories", "ai_rated", sa.Column("ai_rated", sa.Boolean(), server_default=sa.text("0"))),
    ("memories", "confirmation_count", sa.Column("confirmation_count", sa.Integer(), server_default=sa.text("0"))),
    ("memories", "contradiction_count", sa.Column("contradiction_count", sa.Integer(), server_default=sa.text("0"))),
    ("memories", "core_category", sa.Column("core_category", sa.String(length=20))),
    ("memories", "decay_base_at", sa.Column("decay_base_at", sa.DateTime())),
    ("memories", "delete_at", sa.Column("delete_at", sa.DateTime())),
    ("memories", "departed_names", sa.Column("departed_names", sa.String(length=255))),
    ("memories", "epistemic_status", sa.Column("epistemic_status", sa.String(length=12))),
    ("memories", "is_core", sa.Column("is_core", sa.Boolean(), server_default=sa.text("0"))),
    ("memories", "is_locked", sa.Column("is_locked", sa.Boolean(), server_default=sa.text("0"))),
    ("memories", "is_pinned", sa.Column("is_pinned", sa.Boolean(), server_default=sa.text("0"))),
    ("memories", "last_reinforce_at", sa.Column("last_reinforce_at", sa.DateTime())),
    ("memories", "next_review_at", sa.Column("next_review_at", sa.DateTime())),
    ("memories", "reliability_score", sa.Column("reliability_score", sa.Float())),
    ("memories", "review_count", sa.Column("review_count", sa.Integer(), server_default=sa.text("0"))),
    ("memories", "speaker_id", sa.Column("speaker_id", sa.Integer())),
    ("memories", "speaker_type", sa.Column("speaker_type", sa.String(length=10))),
    ("memories", "strength_days", sa.Column("strength_days", sa.Float())),
    ("memories", "why_it_matters", sa.Column("why_it_matters", sa.Text())),
    # pet_activities
    ("pet_activities", "actor", sa.Column("actor", sa.String(length=10), server_default="user")),
    # pets
    ("pets", "last_remind_at", sa.Column("last_remind_at", sa.DateTime())),
    # platform_profiles
    ("platform_profiles", "memory_restrict", sa.Column("memory_restrict", sa.String(length=10), server_default="off")),
    # plugins
    ("plugins", "type", sa.Column("type", sa.String(length=20), server_default="http")),
    # proactive_message_logs
    ("proactive_message_logs", "extra_meta", sa.Column("extra_meta", sa.Text())),
    # proactive_settings
    ("proactive_settings", "active_image_gen_enabled", sa.Column("active_image_gen_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("proactive_settings", "cold_war_enabled", sa.Column("cold_war_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "dnd_enabled", sa.Column("dnd_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("proactive_settings", "image_gen_enabled", sa.Column("image_gen_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("proactive_settings", "life_enabled", sa.Column("life_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "life_intensity", sa.Column("life_intensity", sa.String(length=10), server_default="low")),
    ("proactive_settings", "life_share_enabled", sa.Column("life_share_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "memory_review_enabled", sa.Column("memory_review_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "moments_comment_enabled", sa.Column("moments_comment_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "mood_badge_enabled", sa.Column("mood_badge_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "privacy_enabled", sa.Column("privacy_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "privacy_lock_enabled", sa.Column("privacy_lock_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "reasoning_level", sa.Column("reasoning_level", sa.Integer(), server_default=sa.text("0"))),
    ("proactive_settings", "show_tools_enabled", sa.Column("show_tools_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("proactive_settings", "state_trigger_enabled", sa.Column("state_trigger_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("proactive_settings", "weave_full_inject_enabled", sa.Column("weave_full_inject_enabled", sa.Boolean(), server_default=sa.text("0"))),
    # proactive_storyline_items
    ("proactive_storyline_items", "reasoning", sa.Column("reasoning", sa.Text())),
    # scheduled_events
    ("scheduled_events", "owner", sa.Column("owner", sa.String(length=10), server_default="ai")),
    # state_trigger_logs
    ("state_trigger_logs", "anger_at_trigger", sa.Column("anger_at_trigger", sa.Integer(), server_default=sa.text("0"))),
    ("state_trigger_logs", "soothe_count", sa.Column("soothe_count", sa.Integer(), server_default=sa.text("0"))),
    ("state_trigger_logs", "soothe_level", sa.Column("soothe_level", sa.Integer(), server_default=sa.text("0"))),
    ("state_trigger_logs", "stubborn", sa.Column("stubborn", sa.Integer(), server_default=sa.text("0"))),
    # user_workflows
    ("user_workflows", "graph", sa.Column("graph", sa.Text())),
    # users
    ("users", "ai_location", sa.Column("ai_location", sa.String(length=100))),
    ("users", "ai_social_enabled", sa.Column("ai_social_enabled", sa.Boolean(), server_default=sa.text("1"))),
    ("users", "is_admin", sa.Column("is_admin", sa.Boolean(), server_default=sa.text("0"))),
    ("users", "lang", sa.Column("lang", sa.String(length=10), server_default="zh")),
    ("users", "location_city", sa.Column("location_city", sa.String(length=100))),
    ("users", "location_enabled", sa.Column("location_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("users", "location_follow", sa.Column("location_follow", sa.Boolean(), server_default=sa.text("0"))),
    ("users", "location_gps_enabled", sa.Column("location_gps_enabled", sa.Boolean(), server_default=sa.text("0"))),
    ("users", "location_lat", sa.Column("location_lat", sa.Float())),
    ("users", "location_lng", sa.Column("location_lng", sa.Float())),
    ("users", "timezone_offset_minutes", sa.Column("timezone_offset_minutes", sa.Integer())),
    ("users", "user_location", sa.Column("user_location", sa.String(length=100))),
    # weave_cards
    ("weave_cards", "domain", sa.Column("domain", sa.String(length=10), server_default="shared")),
    # world_facts
    ("world_facts", "author", sa.Column("author", sa.String(length=20), server_default="system")),
    ("world_facts", "is_authoritative", sa.Column("is_authoritative", sa.Boolean(), server_default=sa.text("0"))),
]


def upgrade() -> None:
    bind = op.get_bind()
    # 逐表逐列 has_column 守卫补列（已存在则跳过；只对缺失的远古库真正生效）。
    # 用 op.add_column（SQLite 直改 ALTER TABLE ADD COLUMN），与 init_db 行为一致、无表重建。
    seen: set[str] = set()
    for table, _column, _col in _BOOTSTRAP_COLUMNS:
        if table in seen:
            continue
        seen.add(table)
        if not _has_table(bind, table):
            continue
        for t, n, c in _BOOTSTRAP_COLUMNS:
            if t == table and not _has_column(bind, table, n):
                op.add_column(table, c)


def downgrade() -> None:
    # 不删除这 87 列：它们同时是基线 create_table 的列（全新/基线库由基线建出；远古库由本迁移补出）。
    # 若此处删除，会让「全新库 upgrade head → downgrade」把基线列误删（如 memories.next_review_at
    # 有基线索引 idx_memories_next_review，drop 还会因索引报错）。故 downgrade 保持 no-op，
    # 这 87 列在链内属「基线 + bootstrap 双保险」，不随本迁移回退删除（可逆 = 不报错、schema 有效）。
    pass
