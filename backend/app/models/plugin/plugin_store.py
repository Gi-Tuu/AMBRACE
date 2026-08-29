"""插件命名空间 KV 存储模型（48a 桥 API store.set/get）。

按 (plugin_name, user_id) 隔离：同一插件不同用户互不可见，不同插件同用户亦互不可见。
value 存 JSON 文本（≤100KB 由服务层校验），key ≤128 字符。
"""
from datetime import datetime

from sqlalchemy import String, Text, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PluginStore(Base):
    __tablename__ = "plugin_stores"
    __table_args__ = (
        UniqueConstraint("plugin_name", "user_id", "key", name="uq_plugin_store_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
