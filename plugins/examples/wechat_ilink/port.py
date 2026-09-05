# -*- coding: utf-8 -*-
"""wechat_ilink ChannelPort 适配器（X5 渠道外迁契约）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

微信是「私聊型」渠道，不做抖音那套「发作品 / 拉评论 / 回评 / 媒体上传」。5 个契约方法中与
微信无关的显式返回「不支持」/空，避免内核误调；``binding_status`` 返回绑定状态（供内核/前端
通用渲染）。

收发消息（get_updates / send_text）**不在 ChannelPort 契约方法里**：iLink 长轮询与整段发送
由 inbound 消费器（schedule_tick 驱动）承接；本文件（PR3）补充 **``make_client``**：为「入站
回复路径」用绑定行的密文 token 构建 ILinkClient（解密 + baseurl + 超时），集中一处避免回路
各处重复解密/构造。主动 outbound 方法留 PR4（本批不做）。
"""
from __future__ import annotations


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def make_client(bot_token_enc: str, baseurl: str = "", timeout: float = 30.0):
    """为「入站回复/长轮询」构建 ILinkClient（send 通道补实，§8.5/PR3）。

    - ``bot_token_enc``：绑定行密文 token（经 crypto_util.decrypt 解回明文，绝不裸存/进日志）；
    - ``baseurl``：confirmed 返回、已过白名单校验的微信官方域；空则回退默认 host；
    - 解密失败（坏 key/密文损坏）抛出 RuntimeError（crypto_util 口径），调用方按 P0-5 隔离吞掉。
    """
    from ilink_client import ILinkClient  # noqa: PLC0415 - 插件目录由 main.py 加入 sys.path
    import crypto_util  # noqa: PLC0415

    return ILinkClient(crypto_util.decrypt(bot_token_enc), baseurl or "", timeout=timeout)


class WeChatILinkPort:
    """渠道端口：仅状态/绑定面；收发消息不在 ChannelPort 方法里（由 inbound 消费器 + make_client 承担）。"""

    async def publish(self, payload: dict) -> dict:
        """微信不做作品发布。"""
        return {"ok": False, "unsupported": True, "reason": "wechat_ilink 仅支持私聊，不支持发布作品"}

    async def pull_comments(self, payload: dict) -> list[dict]:
        """私聊渠道无评论概念。"""
        return []

    async def reply_comment(self, payload: dict) -> dict:
        """私聊渠道无回评。"""
        return {"ok": False, "unsupported": True, "reason": "wechat_ilink 仅支持私聊，不支持回评"}

    async def upload_media(self, payload: dict) -> dict:
        """媒体主动下发 v1 不做（P2-2 腾讯 CDN + AES 上传）。"""
        return {"ok": False, "unsupported": True, "reason": "wechat_ilink v1 不支持媒体下发（P2-2 再做 CDN 上传）"}

    async def binding_status(self, payload: dict) -> dict:
        """返回绑定状态供内核/前端通用渲染（复用 routes 的绑定视图，避免两处逻辑漂移）。"""
        import routes  # noqa: PLC0415 - 惰性导入，插件目录由 main.py 加入 sys.path

        user_id = _int(payload.get("user_id")) if isinstance(payload, dict) else None
        return await routes.get_binding_view(user_id)


def build_meta() -> dict:
    """渠道注册元数据（内核通用化消费：scope/风险/权限/绑定插件关联）。"""
    return {
        "label": "微信（ClawBot）",
        "plugin": "wechat_ilink",
        "permissions": ["wechat_bind", "wechat_send", "wechat_poll"],
        "scope": "wechat",
        "scope_label": "微信",
        "scope_desc": "微信桥：经官方 ClawBot/iLink 与你绑定的角色私聊（仅本人↔自己的 Agent）",
        "risk_level": "medium",
        # 一机多主（Q1 拍板 2026-09-05）：微信按 bot_single 建模——多微信号各自 ClawBot 并存、
        # 各绑一角色；底层唯一约束 UQ(channel,tenant,bot)，bot 稳定键多 bot 阶段真机确认后启用（§8.4）。
        "binding": {"unique_per_family": True, "mode": "bot_single"},
    }
