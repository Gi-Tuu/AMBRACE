# -*- coding: utf-8 -*-
"""渠道域模型（一机多主 / per-账号化，2026-09-05）。

渠道绑定是一等公民：一个 (渠道, 租户=独立主账号, bot账号) 三元组绑定一个角色。
与 SaaS S0 多租户同源：tenant_id 永远是家庭 root 的 user_id（get_family_root_id 解析）；
bot_account_id 为渠道下外部 bot/账号的稳定标识（家庭单账号模式恒 "default"）。

为什么新建结构化表而不是用 plugin_stores KV：
- KV 无法在 DB 层保证 (channel,tenant,bot) 唯一，并发双绑只能靠应用层 catch；
- 面板要列出家内全部绑定并 join 角色，关系表 + 索引直接支持；
- 绑定是跨渠道统一概念，由内核拥有（plugin_stores 继续服务插件零散 KV）。

binding mode：一律按 bot_single 建模（唯一约束 UQ(channel,tenant,bot)），
family_single 只是应用层 bot_account_id 恒 "default" 的特例，不做两套约束。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChannelBinding(Base):
    """渠道绑定：(channel, tenant_id, bot_account_id) → character_id，DB 唯一约束兜底并发。"""
    __tablename__ = "channel_bindings"
    __table_args__ = (
        # 两种绑定模式共用这一条唯一约束：family_single 时 bot_account_id 恒 'default'
        UniqueConstraint("channel", "tenant_id", "bot_account_id", name="uq_channel_tenant_bot"),
        Index("ix_channel_binding_tenant", "channel", "tenant_id"),
        Index("ix_channel_binding_char", "character_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)          # wechat / douyin（注册表 key，非插件名）
    tenant_id: Mapped[int] = mapped_column(BigInteger, nullable=False)        # 家庭 root user_id
    owner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)    # 实际操作者（家庭内恒=root）
    bot_account_id: Mapped[str] = mapped_column(String(128), default="default")  # 渠道下 bot 稳定键
    bot_label: Mapped[str] = mapped_column(String(100), default="")           # 面板展示名（可改）
    character_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    extra_json: Mapped[str] = mapped_column(String(2000), default="{}")       # 渠道侧轻属性（不存密文）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


__all__ = [
    "ChannelBinding",
]
