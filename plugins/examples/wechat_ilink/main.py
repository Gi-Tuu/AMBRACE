# -*- coding: utf-8 -*-
"""wechat_ilink 插件入口（PR2 骨架/绑定 + PR3 入站收发闭环接线）。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。

- 加载期：import models 注册渠道自有表进 Base.metadata（X5 惯例，须在 init_db 前——
  main.py lifespan 已做渠道预加载）；`sdk.register_channel` 注册渠道（ChannelPort + meta 上报
  binding unique_per_family，使内核绑定裁决自动生效）；`routes.mount(sdk.router())` 挂载 http_router。
- **schedule_tick（PR3）**：manifest.hooks 声明 schedule_tick；``cfg.enabled`` 才轮询；插件自行
  节流 + 重入锁；ILinkClient 出错/超时只记日志（P0-5，绝不拖垮主链路）。
- 启用开关：插件本地配置 ``cfg.enabled``（用户拍板约束 1——不往内核 agent/loop.py 加 AGENT_FLAGS）。
"""
import os
import sys
import time

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from app.plugins import sdk  # noqa: E402

import models  # noqa: F401, E402  # X5：渠道自有 ORM（加载期注册进 Base.metadata）
from port import WeChatILinkPort, build_meta, make_client  # noqa: E402
import routes  # noqa: E402
import inbound  # noqa: E402


# 注册渠道：meta 上报 binding unique_per_family，使内核绑定裁决自动生效（与 douyin_mcp 同构）。
sdk.register_channel("wechat", WeChatILinkPort(), meta=build_meta())

# 挂载 http_router（前缀 /api/v1/plugins/wechat_ilink，强制登录态）。
routes.mount(sdk.router())


@sdk.hook("schedule_tick")
async def _on_tick(ctx) -> None:
    """30s schedule_tick 驱动：cfg.enabled 才轮询；插件自行节流 + 异常隔离（对齐 scheduler）。"""
    try:
        cfg = sdk.get_config()
        if not cfg.get("enabled", False):
            return
        interval = int(cfg.get("poll_interval_seconds", 30) or 30)
        now = time.monotonic()
        if now - inbound.LAST_TICK_AT["t"] < interval:
            return
        inbound.LAST_TICK_AT["t"] = now
        await inbound.poll_once(
            client_factory=make_client,
            interval=interval,
            long_poll=int(cfg.get("long_poll_timeout_seconds", 25) or 25),
            quota=int(cfg.get("quota_per_24h", 10) or 10),
        )
        # PR4：主动消息 outbound 同步（本批不做；此处留注释指明挂点）
    except Exception as e:  # noqa: BLE001 - P0-5 异常隔离，绝不拖垮主链路
        sdk.log("wechat_ilink tick error: %s", e)
