# -*- coding: utf-8 -*-
"""设备域：手机桌面布局/快照/自动状态/设备推送令牌（含日历备忘/浏览器历史等桌面卡片模型）（F6 聚合，2026-08-31）。

原 device/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.device.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── phone_desktop.py ──
# 小手机桌面数据模型（2026-08-11）：桌面布局 / 日历备注 / 浏览器搜索历史
class PhoneDesktop(Base):
    """角色小手机桌面（每角色一行）：壁纸等手机级设置"""
    __tablename__ = "phone_desktops"

    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), primary_key=True)
    wallpaper: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
class PhoneLayout(Base):
    """桌面图标布局：每个角色每应用一行（唯一约束 character_id + app_key）"""
    __tablename__ = "phone_layouts"
    __table_args__ = (UniqueConstraint("character_id", "app_key", name="uq_phone_layout_char_app"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False)
    app_key: Mapped[str] = mapped_column(String(30), nullable=False)
    pos: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class CalendarNote(Base):
    """角色日历备注（AI 可查看/写备注，注入聊天上下文）"""
    __tablename__ = "calendar_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False)
    note_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 记录者署名（角色名/用户昵称，2026-08-14）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class MemoNote(Base):
    """备忘录：AI/用户共同维护的便签（无日期绑定，AI 主动记录）"""
    __tablename__ = "memo_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 记录者署名（角色名/用户昵称，2026-08-14）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class BrowserHistory(Base):
    """角色小手机浏览器搜索历史（保留 7 天）"""
    __tablename__ = "browser_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False)
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── phone_snapshot.py ──
# 手机感知快照（AI 走出沙箱）：手机端采集的屏幕文字/剪贴板/相册元数据与图片描述
class PhoneSnapshot(Base):
    __tablename__ = "phone_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # accessibility/clipboard/media
    content: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    image_desc: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # X7-M1 结构化承载：客户端字段级 JSON 对象文本（api.phone 只收合法对象且 ≤4000，非法即存 NULL）
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
class CheckInRequest(Base):
    """查岗请求（2026-08-15）：角色想感知用户手机时登记，前端轮询发现后立即采集上报"""
    __tablename__ = "check_in_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / done / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

# ── phone_auto_state.py ──
# 手机感知·AI 主动提及状态：记录上次已提及的通知指纹与触发时间（节流防打扰）
class PhoneAutoState(Base):
    __tablename__ = "phone_auto_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    last_trigger_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fingerprints: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 列表：上次已提及的通知指纹
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── device_token.py ──
# 设备推送 token（FCM 离线推送，2026-08-28）：user_device_tokens 表。
#
# - 每行代表一个用户设备的推送通道（当前仅 FCM；push_provider 预留 apns）。
# - 同一用户多设备：每设备一行，推送时逐 token 发送。
# - 唯一约束 (user_id, device_id, push_provider)：同一设备同一通道只保留最新 token。
# - last_seen_at 由 App 前台心跳更新；90 天未活跃的 token 可定期清理。
class UserDeviceToken(Base):
    """用户设备推送 token（FCM / 预留 APNs）。"""

    __tablename__ = "user_device_tokens"
    __table_args__ = (
        UniqueConstraint("user_id", "device_id", "push_provider", name="uq_device_token_user_device_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)  # index=True（P3-4）：与 ix_user_device_tokens_user_id 对齐
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)  # App 生成的设备 UUID
    platform: Mapped[str] = mapped_column(String(16), nullable=False)  # android | ios
    push_provider: Mapped[str] = mapped_column(String(16), nullable=False)  # fcm | apns
    # push_token index=True（P3-4）：修正迁移 d9e0 的命名错位——它建的是 ix_user_device_tokens_token，
    # 列名实为 push_token；ORM 隐式索引名应为 ix_user_device_tokens_push_token（迁移 e8f9a0b1c2d3 改名）。
    push_token: Mapped[str] = mapped_column(String(512), nullable=False, index=True)  # FCM registration token
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
# ── device_action_lists.py（X7-M4c-3，2026-09-23 派单 P19）──
# 行动闸门的「名单」落库：目标白名单（闸门④）+ 插件灰度名单（闸门③a）。
#
# - 两份名单此前只在进程内存（重启即清零），本批起以库为权威；限流/熔断计数仍是运行时统计，
#   刻意不落库（重启清零属预期语义）。
# - tenant_id 不挂 FK：与 plugin_consents.tenant_id 同口径（家庭根租户，允许解析不到时不写行）。
# - 唯一约束即幂等写入的判据（重复添加不产生第二行）。
class DeviceActionTarget(Base):
    """行动目标白名单（闸门④）：一个租户一个目标应用一行，缺表/缺行＝全拒。"""
    __tablename__ = "device_action_targets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "target", name="uq_device_action_target_tenant_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 家庭根租户
    target: Mapped[str] = mapped_column(String(128), nullable=False)  # 包名（写入前已 strip）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
class DeviceActionPlugin(Base):
    """插件行动灰度名单（闸门③a 的逐插件放开集，全局不分租户）。"""
    __tablename__ = "device_action_plugins"
    __table_args__ = (
        UniqueConstraint("plugin_name", name="uq_device_action_plugins_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

__all__ = [
    "PhoneDesktop",
    "PhoneLayout",
    "CalendarNote",
    "BrowserHistory",
    "MemoNote",
    "PhoneSnapshot",
    "CheckInRequest",
    "PhoneAutoState",
    "UserDeviceToken",
    "DeviceActionTarget",
    "DeviceActionPlugin",
]
