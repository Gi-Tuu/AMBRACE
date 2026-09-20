# -*- coding: utf-8 -*-
"""插件域：插件/插件市场（F6 聚合，2026-08-31）。

原 plugin/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.plugin.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
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
    # ---- A2 M1 插件归户（2026-09-20）：安装者归属（可空，本批零行为变更）----
    # NULL = 内置/存量/服务级插件（全局，所有账号同等可见/可用）；
    # 非 NULL = 该「安装者账号」及其「家庭根」（family_service.get_family_root_id）。
    # 本批只落库记录，不做任何可见性过滤（可见性过滤属 M3，另批实施）。
    owner_user_id: Mapped[int | None] = mapped_column(nullable=True)
    owner_tenant_id: Mapped[int | None] = mapped_column(nullable=True)
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


# ── plugin_consent.py ──
# 插件权限「按租户」同意（A2 M6，2026-09-20）。
#
# 背景：M6 前 ``plugins.consented_permissions`` 是**服务级一次性**（任一人同意 → 之后所有人
# 安装/升级都不再弹确认）。本表把同意拆到「家庭根租户」维度：同一插件在不同家庭各自同意一次；
# 升级新增权限时同理。
#
# 口径（与 ``plugins.owner_tenant_id`` / ``channel_bindings.tenant_id`` 同口径，由
# ``family_service.get_family_root_id`` 解析）：
# - 联合主键 ``(plugin_name, tenant_id)``：一个插件一个租户至多一行；
# - ``permissions_json``：该租户已同意权限集（JSON 数组，∪ 历次同意，保序去重）；
# - ``consented_by``：最近一次同意者账号 id（NULL = 早期/服务级回填缺失）；
# - 本表为读点权威；``plugins.consented_permissions`` 保留为兼容旧读点的服务级回落
#   （仅对 ``owner_tenant_id IS NULL`` 的内置/存量插件生效）。
class PluginConsent(Base):
    __tablename__ = "plugin_consents"

    plugin_name: Mapped[str] = mapped_column(String(100), primary_key=True, nullable=False)
    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    permissions_json: Mapped[str] = mapped_column(Text, default="[]")
    consented_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consented_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


__all__ = [
    "Plugin",
    "PluginStore",
    "PluginConsent",
]
