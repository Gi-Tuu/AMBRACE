# -*- coding: utf-8 -*-
"""wechat_ilink 渠道自有表（X5：main.py 加载期 import 注册进 Base.metadata，存量零迁移）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

归属原则（X5）：渠道的数据模型定义在渠道扩展包内，由 main.py 加载期 import 本模块注册进
Base.metadata（main.py lifespan 在 init_db create_all 之前预加载渠道插件，保证全新安装建表）。
与用户记忆库严格隔离：wechat_ilink_* 表只服务本渠道，source=plugin:wechat_ilink。
凭据安全（P0-4）：``bot_token`` 只存密文 ``bot_token_enc``（换绑后更新），绝不裸存、
不进日志、不进前端返回。
"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WeChatILinkBinding(Base):
    """微信桥绑定 + 轻状态（一机多主：tenant_id=家庭 root、bot_account_id=ClawBot 稳定键）。

    唯一键（一机多主 2026-09-05，替代旧全库 UQ(character_id)）：
    - uq_wechat_bot_wxuser：同一 bot 下一个微信用户唯一（partial：ilink_user_id != ''，
      与 messages 表 partial unique 先例一致——空串行不入约束，绑定可先落行后补扫码）；
    - uq_wechat_tenant_bot_char：同租户同 bot 一角色（bot_single 多 bot 时各 bot 独立一角色）。
    ilink_bot_id 每次扫码变，仅记录，不作 bot_account_id（稳定键取法见设计 §8.4，真机确认项）。
    """
    __tablename__ = "wechat_ilink_bindings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)          # 绑定归属主账号
    tenant_id: Mapped[int] = mapped_column(BigInteger, index=True, default=0)   # 家庭 root user_id（=user_id）
    bot_account_id: Mapped[str] = mapped_column(String(128), index=True, default="default")  # ClawBot 稳定键（单 bot 恒 default）
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)     # 家庭内唯一（内核裁决 + 下约束双保险）
    ilink_user_id: Mapped[str] = mapped_column(String(128), default="")   # 同微信号稳定（类 openid）
    ilink_bot_id: Mapped[str] = mapped_column(String(128), default="")    # 每次扫码变，仅记录
    bot_token_enc: Mapped[str] = mapped_column(Text, default="")          # 加密存储，绝不裸存/不进日志
    baseurl: Mapped[str] = mapped_column(String(255), default="")         # confirmed 返回，缓存
    poll_buf: Mapped[str] = mapped_column(Text, default="")               # 长轮询游标（也镜像到 state.json 防丢）
    window_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 配额窗口起点=最后入站
    out_count_in_window: Mapped[int] = mapped_column(Integer, default=0)  # 窗口内已下发条数
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    bound_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    __table_args__ = (
        Index(
            "uq_wechat_bot_wxuser", "bot_account_id", "ilink_user_id", unique=True,
            sqlite_where=text("ilink_user_id != ''"),
            postgresql_where=text("ilink_user_id != ''"),
        ),
        UniqueConstraint("tenant_id", "bot_account_id", "character_id", name="uq_wechat_tenant_bot_char"),
    )


class WeChatILinkMessage(Base):
    """收发消息流水：幂等去重 + 配额统计 + 排障。"""
    __tablename__ = "wechat_ilink_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    binding_id: Mapped[int] = mapped_column(BigInteger, index=True)
    character_id: Mapped[int] = mapped_column(BigInteger, index=True)
    ilink_msg_id: Mapped[str] = mapped_column(String(128), default="")    # 平台侧消息唯一 ID（去重键）
    context_token: Mapped[str] = mapped_column(String(255), default="")
    direction: Mapped[str] = mapped_column(String(8))                      # in / out
    content: Mapped[str] = mapped_column(Text, default="")
    quota_charged: Mapped[bool] = mapped_column(Boolean, default=False)    # 该 out 是否计入配额
    status: Mapped[str] = mapped_column(String(16), default="ok")         # ok / failed / deferred
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    # 幂等（P0-3）：只对「平台侧消息唯一 ID 非空」的行（即入站）唯一；出站行 ilink_msg_id 为空，
    # 若用全局 (binding_id, ilink_msg_id) 唯一约束，会让同 binding 的多个出站消息互相冲突。
    # 改为部分唯一索引（sqlite/postgres 方言），只约束 ilink_msg_id 非空的行——入站去重不重不漏，
    # 出站多行互不冲突。
    __table_args__ = (
        Index(
            "uq_wechat_ilink_msg_in", "binding_id", "ilink_msg_id", unique=True,
            sqlite_where=text("ilink_msg_id != ''"),
            postgresql_where=text("ilink_msg_id != ''"),
        ),
    )


__all__ = [
    "WeChatILinkBinding",
    "WeChatILinkMessage",
]
