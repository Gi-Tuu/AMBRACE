# -*- coding: utf-8 -*-
"""用户域：用户/状态/免打扰/隐私申请/浏览器快照/家庭邀请（F6 聚合，2026-08-31）。

原 user/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.user.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# ── user.py ──
# 用户模型
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    birthday: Mapped[str | None] = mapped_column(String(10), nullable=True)  # MM-DD
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)  # male / female / other
    height: Mapped[float | None] = mapped_column(nullable=True)  # cm
    weight: Mapped[float | None] = mapped_column(nullable=True)  # kg
    bio: Mapped[str | None] = mapped_column(nullable=True)  # 个人简介
    lang: Mapped[str] = mapped_column(String(10), nullable=False, server_default="'zh'", default="zh")  # 界面语言 zh/en（i18n）
    ai_social_enabled: Mapped[bool] = mapped_column(Boolean, server_default="1", default=True)  # AI 间私聊开关（arbiter ai_social 采样时校验）
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)  # 主账号（#46：可勾选的账号集合，优先于 settings.admin_user_ids）
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None, index=True)  # 主账号关联（#68：NULL=独立主账号，非NULL=子账号；P3 受邀码关联用，P0-P2 只用于共享配置判定）
    # 位置信息（2026-08-08）：location_enabled 总开关；location_gps_enabled=获取地理位置（开启后用户位置不可自定义）；
    # location_follow=位置跟随（开启后 AI 位置与用户相同、不可自定义）；timezone_offset_minutes=用户本地时区（分钟，如 480=UTC+8）
    location_enabled: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    location_gps_enabled: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    user_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location_follow: Mapped[bool] = mapped_column(Boolean, server_default="0", default=False)
    timezone_offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location_lat: Mapped[float | None] = mapped_column(nullable=True)  # GPS 定位纬度（获取地理位置开启时上报）
    location_lng: Mapped[float | None] = mapped_column(nullable=True)  # GPS 定位经度
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 坐标反查城市名（Nominatim，失败留空）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # 关系
    characters = relationship("AICharacter", back_populates="user")
    chat_sessions = relationship("ChatSession", back_populates="user")
    memories = relationship("Memory", back_populates="user")

# ── user_state.py ──
# 用户八维可视化状态模型（用户主页蛛网图，用户可手动滑动调整，0-100）
class UserState(Base):
    __tablename__ = "user_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    mood: Mapped[int] = mapped_column(Integer, default=50)           # 心情
    body_temp: Mapped[int] = mapped_column(Integer, default=50)      # 体温（指数，50=正常体感）
    desire: Mapped[int] = mapped_column(Integer, default=50)         # 性欲
    possessiveness: Mapped[int] = mapped_column(Integer, default=50) # 占有欲
    fatigue: Mapped[int] = mapped_column(Integer, default=50)        # 疲惫感
    sensitivity: Mapped[int] = mapped_column(Integer, default=50)    # 敏感度
    comfort: Mapped[int] = mapped_column(Integer, default=50)        # 舒适感
    anger: Mapped[int] = mapped_column(Integer, default=50)          # 怒气值
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── user_dnd.py ──
# 用户免打扰设置模型（后端感知，供状态触发等主动行为联动）
class UserDndSettings(Base):
    """用户免打扰配置（与前端 SharedPreferences 同步，upsert）"""
    __tablename__ = "user_dnd_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    dnd_enabled: Mapped[bool] = mapped_column(Boolean, default=False)            # 免打扰时段总开关
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)   # 通知总开关（弹窗用，前端语义）
    start_hour: Mapped[int] = mapped_column(Integer, default=22)
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_hour: Mapped[int] = mapped_column(Integer, default=8)
    end_minute: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── privacy_request.py ──
# AI 隐私上锁：查看申请记录（日记 diary / 手机感知快照 phone）
class PrivacyRequest(Base):
    __tablename__ = "privacy_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    target_type: Mapped[str] = mapped_column(String(10), nullable=False)  # diary / phone
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="applied")  # applied / approved / rejected
    ai_reply: Mapped[str] = mapped_column(String(500), nullable=False, default="")  # AI 回复（提示框展示）
    mood_label: Mapped[str] = mapped_column(String(20), nullable=False, default="无所谓")  # 开心/无所谓/烦躁等
    unlock_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 通过后的解锁截止（UTC naive）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── browser.py ──
# 浏览器 MCP 数据模型（计划 16：短期快照，30 分钟过期，不进用户记忆库）
class BrowserSnapshot(Base):
    """网页浏览短期快照（30 分钟过期清理；严格记忆隔离：不写 memories 表）"""
    __tablename__ = "browser_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, default=1)
    url: Mapped[str] = mapped_column(String(500), unique=True, default="")
    domain: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    image_urls_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── account_invite.py ──
# 受邀码（#68 账号体系 × API 配置整合 P3 账号关联）。
#
# - code：8 位大写十六进制（唯一）；creator_id：发出码的独立主账号。
# - expires_at：过期时间（生成后 5 分钟有效）；一次性：used_by 非空即已使用。
# - used_by/used_at：记录兑换者，支持审计；同事务 used_by 检查防并发兑换。
#
# 账号关联采用「受邀码」方案：users.parent_id 关联（NULL=独立主账号），
# account_invites 独立表存放一次性码（比 users 内嵌 invite_code 更干净、可审计）。
class AccountInvite(Base):
    __tablename__ = "account_invites"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)  # 8 位大写 hex
    creator_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # 5 分钟有效
    used_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 兑换者 user_id；NULL=未使用
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── user_fact.py ──
# 用户级、跨角色共享的「可变近况」单值事实槽（§20，2026-09-04 落地）。
# 与 per-character Memory / world_facts 的区别（避免平行事实源）：
# - memories 是「某角色视角下」的历史记忆（含主观看法），按 (user_id, character_id) 隔离；
# - world_facts（P1-3 权威层）按 (user_id, character_id) 隔离、描述「角色视角的世界状态」；
# - user_facts 是「用户客观当前状态」的**用户级唯一事实源**（跨角色共享），只放可变单值槽
#   （location/job/relationship/living/goal_state/health），新值取代旧值并记录 previous_value。
#   角色对用户的主观看法/情感仍归 per-character Memory/insight（符合 Knowledge Scope，不跨角色）。
class GlobalUserFact(Base):
    __tablename__ = "user_facts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(30), nullable=False)  # location/job/relationship/living/goal_state/health/...
    value: Mapped[str] = mapped_column(String(200), nullable=False)  # 当前值（归一化短文本）
    previous_value: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 上一值（供旧记忆失效匹配）
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)  # chat/gps/manual/global_sync
    epistemic_status: Mapped[str] = mapped_column(String(12), default="FACT", server_default="FACT")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1.0")
    valid_from: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_user_slot"),
    )


__all__ = [
    "User",
    "UserState",
    "UserDndSettings",
    "PrivacyRequest",
    "BrowserSnapshot",
    "AccountInvite",
    "GlobalUserFact",
]
