"""AMBRACE 3.10 / X6（2026-09-16）—— 主动「内容策略包」内核侧桥接。

职责边界（改动前先读，别越界）
------------------------------
- **内核（本模块 + special/plugin 两个源 + arbiter）**：选人（roster）、频控、去重、
  关系门、免打扰、拍板与发送。
- **策略包（插件）**：只回答「今天该不该发、发哪一类、文案怎么说」，返回候选；
  **不做选人、不做频控、不去重、不判免打扰、不自己发消息**。

让位机制（防双发）
------------------
flag ``proactive_strategy_plugins``（默认 False）开 **且** 有已启用插件在 manifest
``config.strategy_category`` 声明接管某类别时：

1. 内核同名策略源整体让位（本轮仅 ``special``）→ 同一触发日同一类别只有一个生产者；
2. 内核仍按 ``(character_id, message_type, 北京日界)`` 对策略候选做去重兜底
   —— 即使让位判定失效（如用户改了 config），也只会发一次；
3. flag 关 / 无策略包接管时：hook ctx 不下发 roster → 策略包返回空 →
   内核各源行为与现状逐字节一致（零行为变化）。
"""
from __future__ import annotations

from typing import Any

from app.utils.logger import get_logger

_logger = get_logger("scheduler.sources.strategy")

# Feature Flag 名（硬编码默认在 app/agent/loop.py AGENT_FLAGS）
STRATEGY_FLAG = "proactive_strategy_plugins"
# 插件 manifest config 中声明「本包接管的策略类别」的键
STRATEGY_CATEGORY_KEY = "strategy_category"
# 策略候选声明落库口径的键（内核校验后才采信）
STRATEGY_CANDIDATE_KEY = "strategy"
# 本轮迁移的类别 → 允许的 message_type（白名单，防止插件伪造落库口径）
CATEGORY_MESSAGE_TYPES: dict[str, tuple[str, ...]] = {
    "special": ("birthday", "holiday", "anniversary"),
}


def strategy_enabled() -> bool:
    """flag 是否开启（读 AGENT_FLAGS 内存；异常→False，即回退旧行为）。"""
    try:
        from app.agent.loop import AGENT_FLAGS

        return bool(AGENT_FLAGS.get(STRATEGY_FLAG, False))
    except Exception:
        return False


def claimed_categories() -> set[str]:
    """当前【已启用】插件声明接管的策略类别集合（进程内缓存读取，零 DB）。

    读取插件 manifest 的默认 config 与 DB 覆盖值的合并结果（``list_plugins()``）；
    未启用 / 未声明的不算接管。异常→空集（=内核不让位，回退旧行为）。
    """
    out: set[str] = set()
    try:
        from app.plugins.registry import list_plugins

        for p in list_plugins():
            if not p.get("enabled"):
                continue
            cat = (p.get("config") or {}).get(STRATEGY_CATEGORY_KEY)
            if isinstance(cat, str) and cat.strip():
                out.add(cat.strip())
    except Exception as e:  # 隔离：扫描失败 = 不让位
        _logger.warning("strategy claims scan failed: %s", e)
        return set()
    return out


def category_yielded(category: str) -> bool:
    """该策略类别是否已被策略包接管（内核对应源应让位）。flag 关 → 恒 False。"""
    if not strategy_enabled():
        return False
    return category in claimed_categories()


def category_of(candidate: dict) -> str | None:
    """读出候选声明的策略类别（非空字符串才算）。"""
    v = (candidate or {}).get(STRATEGY_CANDIDATE_KEY)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def message_type_of(candidate: dict) -> str | None:
    """策略候选声明的落库口径（内核白名单校验后才采信，供去重与统计使用）。

    非策略候选 / 类别未知 / message_type 不在该类别白名单 → 返回 None（按普通插件候选处理）。
    """
    cat = category_of(candidate)
    if not cat:
        return None
    allowed = CATEGORY_MESSAGE_TYPES.get(cat)
    if not allowed:
        return None
    mt = (candidate or {}).get("message_type")
    if isinstance(mt, str) and mt in allowed:
        return mt
    return None


async def sent_today(character_id: int, message_type: str) -> bool:
    """该角色今天（北京日界）是否已发过该 message_type 的主动消息。

    内核侧去重兜底：策略包无状态、每 tick 都会投同样的候选，靠这里收口成「同一触发日只发一次」。
    查询失败 fail-open 返回 False（宁可多一次，也不阻塞发送；还有小时限额兜底）。
    """
    try:
        from sqlalchemy import select

        from app.db.database import async_session_factory
        from app.models.character import ProactiveMessageLog
        from app.utils.timeutil import beijing_day_start_utc

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ProactiveMessageLog.id)
                    .where(
                        ProactiveMessageLog.character_id == character_id,
                        ProactiveMessageLog.message_type == message_type,
                        ProactiveMessageLog.created_at >= beijing_day_start_utc(),
                    )
                    .limit(1)
                )
            ).first()
        return row is not None
    except Exception as e:
        _logger.warning(
            "strategy dedup query failed char=%s type=%s: %s", character_id, message_type, e
        )
        return False


async def build_roster() -> list[dict[str, Any]]:
    """内核「选人」：给出开启主动互动、且有会话的角色名单（供策略包映射候选，不下发策略判定）。

    只做「谁有资格被考虑」：角色激活 + 主动互动开关 + 存在会话；
    **不做**日期/节日/纪念日判定、不做节流（那是策略包与频控的事）。
    每角色 2 次小查询（最新会话 / 首个会话），仅在 flag 开且有包接管时调用。
    """
    from app.scheduling.triggers import get_active_characters, get_first_session, get_latest_session

    roster: list[dict[str, Any]] = []
    for c in await get_active_characters():
        try:
            latest = await get_latest_session(c["character_id"], c["user_id"])
            if not latest:
                continue
            first = await get_first_session(c["character_id"], c["user_id"])
            first_at = first.get("created_at") if first else None
            roster.append(
                {
                    "character_id": c["character_id"],
                    "user_id": c["user_id"],
                    "character_name": c.get("character_name") or "",
                    "nickname": c.get("nickname") or c.get("username") or "",
                    "birthday": c.get("birthday"),          # MM-DD 或 None
                    "birthday_enabled": bool(c.get("birthday_enabled")),
                    "holiday_enabled": bool(c.get("holiday_enabled")),
                    "session_id": latest.get("id"),
                    "first_session_at": first_at.isoformat() if first_at else None,
                }
            )
        except Exception as e:
            _logger.warning("roster build failed char=%s: %s", c.get("character_id"), e)
    return roster


def build_hook_ctx(categories: set[str], roster: list[dict]) -> dict:
    """拼装下发给 proactive_candidate hook 的 ctx（仅在 flag 开且有接管时调用）。"""
    return {"strategy_categories": sorted(categories), "roster": roster}
