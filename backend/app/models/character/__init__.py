# -*- coding: utf-8 -*-
"""角色域：AI 角色/八维状态与历史/状态触发日志/关系事件/剧情线/主动设置（F6 聚合，2026-08-31）。

原 character/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.character.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# ── character.py ──
# AI 角色模型
class AICharacter(Base):
    __tablename__ = "ai_characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    weight: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)  # YYYY-MM-DD
    voice: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)  # 自定义声色：音色 key（NULL=按性别默认）
    voice_rate: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)  # 语速倍率（1.0=正常；仅 edge-tts 兜底生效）
    voice_pitch: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)  # 语调 Hz 偏移（0=正常；仅 edge-tts 兜底生效）
    timezone_offset: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)  # 所在时区（UTC 偏移小时，NULL=北京时间 UTC+8；朋友圈时间按作者地区显示）
    appearance: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 背景信息（用户提供，AI 不覆盖）
    self_statement: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 自述（AI 对话中形成的自我认知）
    relationship_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default="普通朋友")
    # 关系网：该角色与用户的关系类型（对象/闺蜜/兄弟/朋友…）与是否用户对象
    relation_type: Mapped[str | None] = mapped_column(String(30), nullable=True, default="朋友")
    is_partner: Mapped[bool] = mapped_column(Boolean, default=False)
    current_status: Mapped[str | None] = mapped_column(Text, nullable=True, default="你们正在聊天")
    greeting_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 话痨度（群聊调度 L1，2026-08-25）：0-100；NULL=未设置按性格推断；talkativeness_locked=1 时 AI 不可自主调整
    talkativeness: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    talkativeness_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 认知循环 / 记忆架构 v2.1 开关（2026-08-27 用户拍板全量开启）
    cognitive_loop_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    memory_v2_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 记忆架构 v2.1（意义/目标/情境复习）
    user_llm_config_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("user_llm_configs.id"), nullable=True, default=None)  # 角色绑定 LLM 配置（#68 P0-P2：默认/我的配置/主账号共享配置）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    user = relationship("User", back_populates="characters")
    chat_sessions = relationship("ChatSession", back_populates="character")
    memories = relationship("Memory", back_populates="character")

# ── state.py ──
# 角色八维可视化状态模型（心情/体温/性欲/占有欲/疲惫感/敏感度/舒适感/怒气值，0-100）
class CharacterState(Base):
    __tablename__ = "character_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), unique=True, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, default=50)           # 心情
    body_temp: Mapped[int] = mapped_column(Integer, default=50)      # 体温（指数，50=正常体感）
    desire: Mapped[int] = mapped_column(Integer, default=50)         # 性欲
    possessiveness: Mapped[int] = mapped_column(Integer, default=50) # 占有欲
    fatigue: Mapped[int] = mapped_column(Integer, default=50)        # 疲惫感
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)    # 敏感度
    comfort: Mapped[int] = mapped_column(Integer, default=50)        # 舒适感
    anger: Mapped[int] = mapped_column(Integer, default=50)         # 怒气值（高=生气，低=平静）
    trust: Mapped[int] = mapped_column(Integer, default=50)         # 信任（v2.1 关系标量，长期不互动衰减）
    attachment: Mapped[int] = mapped_column(Integer, default=50)    # 依恋（v2.1 关系标量，长期不互动衰减）
    curiosity: Mapped[int] = mapped_column(Integer, default=50)     # 好奇（v2.1 关系标量）
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近互动（评估）时间；drift 结算不刷新，疲劳休息判定用
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── state_history.py ──
# 角色八维状态历史快照：每次聊天评估后存 1 行（Phase 2：情绪曲线/蛛网对比数据源）
class CharacterStateHistory(Base):
    __tablename__ = "character_state_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    mood: Mapped[int] = mapped_column(Integer, default=50)
    body_temp: Mapped[int] = mapped_column(Integer, default=50)
    desire: Mapped[int] = mapped_column(Integer, default=50)
    possessiveness: Mapped[int] = mapped_column(Integer, default=50)
    fatigue: Mapped[int] = mapped_column(Integer, default=50)
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)
    comfort: Mapped[int] = mapped_column(Integer, default=50)
    anger: Mapped[int] = mapped_column(Integer, default=50)
    source: Mapped[str] = mapped_column(String(20), default="eval")  # eval=聊天评估 / drift=漂移结算
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── relationship_event.py ──
# 关系事件表（记忆架构 v2.1 Phase 3b）：AI 与用户关系的里程碑互动。
#
# 承载关系记忆的"事件侧"（标量侧在 character_states.trust/attachment/curiosity，
# AI 视角摘要侧在 ai_characters.relationship_summary）；本表记录可追溯的关系变化事件，
# 供关系温度曲线、情境复习与未来关系分析使用。不双写记忆（可关联 memory_id 溯源）。
class RelationshipEvent(Base):
    __tablename__ = "relationship_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    event: Mapped[str] = mapped_column(String(300), nullable=False)      # 事件摘要（≤300）
    content: Mapped[str | None] = mapped_column(Text, nullable=True)     # 原始上下文（截断）
    change_type: Mapped[str] = mapped_column(String(20), default="other")  # trust_up/trust_down/closer/distant/care/apology/cold_war/other
    trust_delta: Mapped[int] = mapped_column(Integer, default=0)         # 信任变化量 -10..+10
    memory_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 关联记忆 id（溯源，不双写）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = {"extend_existing": True}

# ── state_trigger_log.py ──
# 状态触发日志：维度触发事件防抖与恢复检测（v1 状态触发机制）
class StateTriggerLog(Base):
    __tablename__ = "state_trigger_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    trigger_key: Mapped[str] = mapped_column(String(40), nullable=False)  # 如 anger_high / anger_mood_low
    value: Mapped[str] = mapped_column(String(200), default="")           # 触发时八维快照
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)       # 是否已回落（回落后才可再触发）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # 冷战细化（2026-08-15）：触发时怒气 / 最近哄好分级(0无 1轻哄或找台阶 2真诚 3敷衍) / 软化次数 / 别扭期标志
    anger_at_trigger: Mapped[int] = mapped_column(Integer, default=0)
    soothe_level: Mapped[int] = mapped_column(Integer, default=0)
    soothe_count: Mapped[int] = mapped_column(Integer, default=0)
    stubborn: Mapped[int] = mapped_column(Integer, default=0)

# ── storyline_event.py ──
# 剧情线事件模型：状态联动剧情线的节点进度档案（v5 剧情线）
class StorylineEvent(Base):
    """剧情线节点记录：每角色每剧情一条记录，节点执行/推进的档案

    - storyline_key: 剧情类型（cold_war / jealousy / fatigue…）
    - node_index: 节点序号（0=爆发, 1=冷战, 2=加时, 3=深夜emo, 4=破冰, 5=和好后遗症）
    - status: active(进行中) / done(已完成) / skipped(跳过) / aborted(中止)
    - trigger_source: 触发出处（如 anger_mood_low）
    - user_context: 用户当时行为摘要（剧情分支决策依据）
    - output_text: 节点产出的文本（消息/朋友圈内容）
    """
    __tablename__ = "storyline_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    storyline_key: Mapped[str] = mapped_column(String(30), nullable=False)  # cold_war / jealousy / fatigue
    node_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")  # active/done/skipped/aborted
    trigger_source: Mapped[str] = mapped_column(String(40), default="")
    user_context: Mapped[str] = mapped_column(String(500), default="")
    output_text: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("character_id", "storyline_key", "node_index", name="uq_char_story_node"),
    )

# ── proactive_storyline.py ──
# 主动事件切片模型 — 一次事件生成一条连贯消息，切成多段按顺序逐条发送
class ProactiveStorylineItem(Base):
    """事件切片：同一 group_id 属于一次事件，按 send_at 顺序逐条发送（私信专用，朋友圈不走此表）"""

    __tablename__ = "proactive_storyline_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_characters.id"), nullable=False, index=True
    )
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_sessions.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    group_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)  # 深度思考过程（气泡折叠展示，2026-08-15）
    send_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/sent/expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── proactive_settings.py ──
# 主动交流系统模型
class ProactiveSettings(Base):
    """角色级主动交流配置"""
    __tablename__ = "proactive_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ai_characters.id"), nullable=False, unique=True
    )
    enable_proactive: Mapped[bool] = mapped_column(Boolean, default=True)
    idle_threshold_minutes: Mapped[int] = mapped_column(Integer, default=120)  # 闲置多久算"离线"
    frequency: Mapped[str] = mapped_column(
        String(10), default="medium"
    )  # low / medium / high
    max_daily_proactive: Mapped[int] = mapped_column(Integer, default=5)  # 每日上限
    birthday_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    holiday_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    diary_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    moments_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    moments_comment_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 朋友圈评论/回复子开关（朋友圈开关下，2026-08-07）
    state_trigger_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 状态触发事件（v1）
    memory_review_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 记忆复习（主动交流子开关，2026-08-07）
    cold_war_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 冷战断联（v4，状态触发子开关）
    mood_badge_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 聊天页心情标识开关（状态组子开关，纯展示）
    image_gen_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 生图开关（聊天内AI发图）
    active_image_gen_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 主动生图（生图子开关：AI 自发发图，2026-08-09）
    privacy_lock_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 隐私上锁（日记/小手机查看需申请，2026-08-07）
    privacy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # 隐私组总开关（2026-08-10）
    reasoning_level: Mapped[int] = mapped_column(Integer, default=0)  # 思考过程挡位：0=关闭 / 1=简单思考 / 2=深度思考（2026-08-10）
    show_tools_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 气泡显示调用能力（识图/生图/语音回复/扩展，2026-08-10）
    weave_full_inject_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 织库全注入对话（角色设置-社交，2026-08-12）
    life_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # AI 离线生活总开关（2026-08-12，默认开）
    life_intensity: Mapped[str] = mapped_column(String(10), default="low")  # 离线生活强度 low/medium/high（3h/2h/1h tick，2026-08-12）
    life_share_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # AI 生活分享（私·织库信任概率注入，与 weave_full_inject 分开，2026-08-12）
    dnd_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 可配置免打扰开关（2026-08-12；关闭时沿用硬编码深夜 0-7 点静默）
    dnd_start: Mapped[str] = mapped_column(String(5), default="00:00")  # 免打扰开始 HH:MM（2026-08-12）
    dnd_end: Mapped[str] = mapped_column(String(5), default="07:00")  # 免打扰结束 HH:MM（2026-08-12）
    check_in_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 查岗：主动交流时可查看用户当前在用什么软件（2026-08-15）
    control_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 管制：查岗子项（占位，待设计 2026-08-15）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
class HolidayPreference(Base):
    """用户节日偏好（屏蔽不喜欢的节日）"""
    __tablename__ = "holiday_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    holiday_name: Mapped[str] = mapped_column(String(50), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # False = 屏蔽
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        __import__("sqlalchemy").UniqueConstraint("user_id", "holiday_name", name="uq_user_holiday"),
    )
class ProactiveMessageLog(Base):
    """主动消息发送日志（防重复推送）"""
    __tablename__ = "proactive_message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=True)
    message_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # proactive / birthday / holiday
    holiday_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    extra_meta: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 扩展（memory_review 关联 memory_id 等）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class ProactiveTriggerLog(Base):
    """主动消息触发候选日志（可观测：触发/决策/拒绝原因）"""
    __tablename__ = "proactive_trigger_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(16), default="pending")  # approved / rejected
    reject_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
__all__ = [
    "AICharacter",
    "CharacterState",
    "CharacterStateHistory",
    "RelationshipEvent",
    "StateTriggerLog",
    "StorylineEvent",
    "ProactiveStorylineItem",
    "ProactiveSettings",
    "HolidayPreference",
    "ProactiveMessageLog",
    "ProactiveTriggerLog",
]
