"""游戏记忆库模型（每角色可见摘要，不进主记忆向量检索）"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GameMemory(Base):
    """游戏记忆库：每角色在一局游戏中"经历了什么"的结构化记录。
    不进主记忆向量检索；只能通过 game_session_id 显式查询。
    """

    __tablename__ = "game_memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("game_sessions.id"), index=True)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), index=True)
    # 该记忆属于哪个角色（用户不写，用户视角由前端实时从 events 渲染）
    my_role: Mapped[str] = mapped_column(String(20), default="")
    my_word: Mapped[str] = mapped_column(String(40), default="")
    # 该角色自己的词/身份（从 private_json 快照）
    result: Mapped[str] = mapped_column(String(10), default="")  # won / lost
    survived_rounds: Mapped[int] = mapped_column(Integer, default=0)
    public_events_json: Mapped[str] = mapped_column(Text, default="[]")
    # 该角色可见的公开事件流水（GM 播报 + 所有人发言 + 投票结果）
    my_actions_json: Mapped[str] = mapped_column(Text, default="[]")
    # 该角色自己的行动（描述/投票/选择）
    summary: Mapped[str] = mapped_column(String(300), default="")
    # 一句话角色视角总结（模板生成，零 LLM）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())