"""小手机桌面数据模型（2026-08-11）：桌面布局 / 日历备注 / 浏览器搜索历史"""
from datetime import datetime
from sqlalchemy import Integer, String, Text, Boolean, DateTime, ForeignKey, func, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PhoneDesktop(Base):
    """角色小手机桌面（每角色一行）：壁纸等手机级设置"""
    __tablename__ = "phone_desktops"

    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), primary_key=True)
    wallpaper: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class PhoneLayout(Base):
    """桌面图标布局：每个角色每应用一行（唯一约束 character_id + app_key）"""
    __tablename__ = "phone_layouts"
    __table_args__ = (UniqueConstraint("character_id", "app_key", name="uq_phone_layout_char_app"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    app_key: Mapped[str] = mapped_column(String(30), nullable=False)
    pos: Mapped[int] = mapped_column(Integer, default=0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CalendarNote(Base):
    """角色日历备注（AI 可查看/写备注，注入聊天上下文）"""
    __tablename__ = "calendar_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    note_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    note_text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 记录者署名（角色名/用户昵称，2026-08-14）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MemoNote(Base):
    """备忘录：AI/用户共同维护的便签（无日期绑定，AI 主动记录）"""
    __tablename__ = "memo_notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 记录者署名（角色名/用户昵称，2026-08-14）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BrowserHistory(Base):
    """角色小手机浏览器搜索历史（保留 7 天）"""
    __tablename__ = "browser_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    query: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
