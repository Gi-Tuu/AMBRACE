# -*- coding: utf-8 -*-
"""App 添加未绑定 ClawBot：可用 bot 来源（包「App 添加未绑定 bot 链路」，2026-09-06）。

选型（交接三选一）：**A2 同机读取 openclaw accounts + Provider 抽象位**。
- `/qrcode` 扫码自助（首选候选）核验结论：协议层独立于 openclaw（ILinkClient 直连 iLink），
  但**真机从未闭环**（NEEDS_RUNTIME_VERIFICATION 密集），且会给拥爱引入第二套凭据
  （与网关 openclaw 登录并存 → 双消费/互踢不确定性）——不选；
- A1 桥上报（推荐于多机/SaaS）：留 Provider 位（`AVAILABLE_BOTS_PROVIDER` 注释与
  `list_openclaw_accounts` 单点替换），本期不实现（需网关改动+重启）；
- A2 同机读取：网关与拥爱同机部署（现形态）下立即可用，零桥改动。

归属与隔离口径：
- available = openclaw 已登录账号 − 已被**任意租户**绑定的 bot_account_id（谁先绑归谁，
  upsert 后行带 tenant；root-only 可见）；
- 绑定执行（`bind_available_bot`）：bot 的 userId 作 ilink_user_id 落插件行（**无 token**：
  凭据在网关侧，relay 网关发送不依赖拥爱 token；自轮询 poll 对空 token 解密失败自动隔离），
  并 upsert channel_bindings（角色绑定）。首次该 bot 来消息即可按 (bot, wxuser) 路由。
"""
from __future__ import annotations

import json
from pathlib import Path

# Provider 位：多机/SaaS 形态换 A1（bridge 启动/登录时上报可用 bot 到拥爱轻量表）时，
# 仅替换 list_openclaw_accounts / list_available_bots 的数据源，UI 与绑定执行不变。
DEFAULT_OPENCLAW_STATE_DIR = Path.home() / ".openclaw" / "openclaw-weixin"


def list_openclaw_accounts(state_dir: Path | None = None) -> list[dict]:
    """读 openclaw accounts（纯函数，路径可注入便于单测）：[{account_id, user_id, saved_at}]。

    覆盖优先级：参数 > 环境变量 MULTIBOT_WX_DIR（演练/测试隔离） > 默认 home 路径。
    """
    import os  # noqa: PLC0415

    base = Path(state_dir) if state_dir else Path(
        os.environ.get("MULTIBOT_WX_DIR") or str(DEFAULT_OPENCLAW_STATE_DIR))
    idx = base / "accounts.json"
    try:
        ids = json.loads(idx.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(ids, list):
        return []
    out: list[dict] = []
    acc_dir = base / "accounts"
    for aid in ids:
        aid = str(aid).strip()
        if not aid:
            continue
        user_id, saved_at = "", ""
        try:
            data = json.loads((acc_dir / f"{aid}.json").read_text(encoding="utf-8"))
            user_id = str(data.get("userId") or "").strip()
            saved_at = str(data.get("savedAt") or "")
        except Exception:
            pass  # 账号文件缺失/损坏：仍列出 id（userId 空时无法落 wxuser 映射，由调用方过滤）
        out.append({"account_id": aid, "user_id": user_id, "saved_at": saved_at})
    return out


def filter_available(accounts: list[dict], bound_bot_ids: set[str]) -> list[dict]:
    """过滤出「已登录且拥爱未绑定」的 bot（纯函数）：剔除已绑定 id 与缺 userId 的账号。"""
    return [a for a in accounts
            if a.get("account_id") and a.get("account_id") not in bound_bot_ids and a.get("user_id")]


async def list_available_bots(db, tenant_id: int | None, state_dir: Path | None = None) -> list[dict]:
    """GET /available-bots 数据源（root-only 由路由层保证）。

    bound = 已被**任意租户**绑定的 bot_account_id（隔离：别的租户已绑的 bot 不再出现在我的
    可添加列表，防止误绑他人 bot；新登录未绑定的 bot 谁先绑归谁）。
    """
    from sqlalchemy import select  # noqa: PLC0415

    import models  # noqa: PLC0415

    bound = set((await db.execute(select(models.WeChatILinkBinding.bot_account_id).where(
        models.WeChatILinkBinding.enabled.is_(True),
    ))).scalars().all())
    bound.discard("default")  # 单 bot 占位行不参与排除（旧部署迁移前仍允许添加）
    accounts = list_openclaw_accounts(state_dir)
    return filter_available(accounts, bound)
