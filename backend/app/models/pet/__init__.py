# -*- coding: utf-8 -*-
"""宠物域：宠物/活动记录（F6 聚合，2026-08-31）。

原 pet/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.pet.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── pet.py ──
# 宠物模型（用户养的小动物；owner_type/owner_id 预留未来 AI 角色养宠）
class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    species: Mapped[str] = mapped_column(String(30), nullable=False)  # cat/dog/parrot/rabbit/hamster/snake/gecko
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    exp: Mapped[int] = mapped_column(Integer, default=0)
    hunger: Mapped[int] = mapped_column(Integer, default=80)      # 0-100
    mood: Mapped[int] = mapped_column(Integer, default=80)
    energy: Mapped[int] = mapped_column(Integer, default=80)
    cleanliness: Mapped[int] = mapped_column(Integer, default=80)
    last_feed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_play_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_clean_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_remind_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次 AI 提醒照顾（Phase 2）
    status_text: Mapped[str] = mapped_column(String(50), default="精神满满")
    owner_type: Mapped[str | None] = mapped_column(String(10), nullable=True)  # user/ai（预留）
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 预留
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── pet_activity.py ──
# 宠物互动活动日志：互动展示区数据源（用户/角色对宠物做的事，短时去重）
class PetActivity(Base):
    """宠物活动记录：feed/play/clean/adopt/abandon/remind；同宠物同动作 30 分钟内视为同一件事（更新时间不新增）"""
    __tablename__ = "pet_activities"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    pet_id: Mapped[int] = mapped_column(Integer, ForeignKey("pets.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # feed/play/clean/adopt/abandon/remind
    actor: Mapped[str] = mapped_column(String(10), default="user")  # user=用户（含拜访）/ ai=角色自己照顾
    content: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
__all__ = [
    "Pet",
    "PetActivity",
]
