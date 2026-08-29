"""主动交流系统模型"""
from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


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
