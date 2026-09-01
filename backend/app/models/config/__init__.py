# -*- coding: utf-8 -*-
"""配置域：BYOK 与服务器级 API/识图/语音/多模态/市场/运行时 Flag/用户级 LLM 配置（F6 聚合，2026-08-31）。

原 config/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.config.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── api_config.py ──
# API 配置：user_id=用户级 BYOK（聊天主链路优先）；user_id=0=服务器级全局（开源部署填一次，代码/.env 零密钥）
class ApiConfig(Base):
    __tablename__ = "api_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)  # 0 = 服务器级全局配置哨兵
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 供应商标识（深度思考开关适配用，2026-08-10）
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── vlm_config.py ──
# 识图（图片理解）服务器级全局配置（user_id=0 哨兵，单行：开源部署填一次，key 不进 .env）
class VlmConfig(Base):
    __tablename__ = "vlm_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)  # 0 = 服务器级全局配置哨兵
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── speech_config.py ──
# 语音大模型服务器级全局配置（user_id=0 哨兵，单行；当前转写仍走本地 faster-whisper，云端配置先占位落库）
class SpeechConfig(Base):
    __tablename__ = "speech_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)  # 0 = 服务器级全局配置哨兵
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── multimodal_config.py ──
# 全模态大模型服务器级全局配置（user_id=0 哨兵，单行：开源部署填一次，key 不进 .env）
class MultimodalConfig(Base):
    __tablename__ = "multimodal_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, unique=True)  # 0 = 服务器级全局配置哨兵
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── marketplace_config.py ──
# 插件市场远程配置模型（单行：启用/URL 列表/刷新间隔/域名白名单/大小上限）
class MarketplaceConfig(Base):
    __tablename__ = "marketplace_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)          # 远程市场总开关
    urls: Mapped[str] = mapped_column(Text, default="[]")                  # JSON 数组：远程 index URL 列表
    refresh_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    allowed_hosts: Mapped[str] = mapped_column(Text, default="[]")         # JSON 数组：域名白名单（[]=不限 https）
    max_zip_mb: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── runtime_flag.py ──
class RuntimeFlag(Base):
    __tablename__ = 'runtime_flags'

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── user_llm_config.py ──
# 用户多 LLM 配置（#68 账号体系 × API 配置整合 P0）。
#
# - user_id：配置归属用户；每个用户可有多个 LLM 配置（我的 LLM）。
# - is_default：同一用户至多一个 default（设置新默认自动清其他），解析链命中用户默认。
# - shared_with_subs：标记该配置可共享给子账号（主账号配置；子账号只读不可改）。
# - UNIQUE(user_id, name)：同一用户配置名唯一。
#
# 解析链（_resolve_llm_config）：任务专用 → 角色绑定（ai_characters.user_llm_config_id）
# → 用户默认 → 主账号共享默认（仅子账号）→ 服务器级 → .env。
# llm_usage.config_id/group_owner_id（P6 用量归因）本次不加。
class UserLlmConfig(Base):
    __tablename__ = "user_llm_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_llm_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 深思考开关厂商适配用
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    shared_with_subs: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
__all__ = [
    "ApiConfig",
    "VlmConfig",
    "SpeechConfig",
    "MultimodalConfig",
    "MarketplaceConfig",
    "RuntimeFlag",
    "UserLlmConfig",
]
