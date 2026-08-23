"""织库增量补卡：save_memory 后异步调度（每角色 30 分钟防抖，单次最多 1 张卡）"""
import asyncio
from app.utils.logger import get_logger
import time

_logger = get_logger("weave.incremental")

INCREMENTAL_COOLDOWN = 30 * 60  # 每角色两次增量整理的最小间隔（秒）
_last_ts: dict[tuple[int, int], float] = {}


def schedule_incremental_weave(user_id: int, character_id: int, domain: str = "shared") -> None:
    """save_memory 后调用：30 分钟防抖 + 异步执行（失败仅告警，不影响记忆写入）

    domain：shared=全·织库（共同记忆）/ private=私·织库（AI 生活 source=life）
    """
    key = (user_id, character_id, domain)
    now = time.time()
    if now - _last_ts.get(key, 0.0) < INCREMENTAL_COOLDOWN:
        return
    _last_ts[key] = now
    try:
        asyncio.ensure_future(_run(user_id, character_id, domain))
    except Exception as e:
        _logger.warning("weave incremental schedule failed: %s", e)


async def _run(user_id: int, character_id: int, domain: str = "shared") -> None:
    try:
        # P0-1b：织库增量整理经统一内部工具入口执行（生命周期/tool.executed 事件/异常隔离）
        from app.agent.internal_runner import run_internal

        res = await run_internal(
            "weave_card",
            {"user_id": user_id, "character_id": character_id, "max_cards": 1, "domain": domain},
            character_id=character_id, user_id=user_id,
        )
        result = (res.get("result") or {}) if res.get("status") == "ok" else {"error": res.get("error")}
        _logger.info("weave incremental done: %s", result)
    except Exception as e:
        _logger.warning("weave incremental run failed: %s", e)
