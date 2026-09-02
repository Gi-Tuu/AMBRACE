# -*- coding: utf-8 -*-
"""插件域：插件/插件市场（F6 聚合，2026-08-31）。

原 plugin/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.plugin.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── plugin.py ──
# 插件模型（扩展系统：插件开关/配置持久化，全局不绑定用户）
class Plugin(Base):
    __tablename__ = "plugins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    author: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(20), default="plugin")  # plugin / mcp
    type: Mapped[str] = mapped_column(String(20), default="http")  # 48c：插件类型 http/prompt/chat/workflow/hybrid
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    # ---- 3.9 插件安全闸（2026-09-02）：来源校验与同意记录 ----
    source: Mapped[str] = mapped_column(String(16), default="builtin")  # builtin / remote / local
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 远程来源 download_url（local/builtin 为 NULL）
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 实际计算值（索引未提供也记录）
    consented_permissions: Mapped[str] = mapped_column(Text, default="[]")  # 已同意权限集 JSON 数组（∪ 历次同意）
    consented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次同意时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── plugin_store.py ──
# 插件命名空间 KV 存储模型（48a 桥 API store.set/get）。
#
# 按 (plugin_name, user_id) 隔离：同一插件不同用户互不可见，不同插件同用户亦互不可见。
# value 存 JSON 文本（≤100KB 由服务层校验），key ≤128 字符。
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
__all__ = [
    "Plugin",
    "PluginStore",
]
