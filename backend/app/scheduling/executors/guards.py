"""跨类型前置闸 — pre_gates（A20 批 3a）

本模块自 ``app/scheduling/arbiter.py`` 的 ``_execute`` 开头逐字节搬入两段判定（A20 批 3a，2026-10-02）：
① 免打扰静默 + 夜晚睡眠静默（所有非 timer 类型都跑）；② 用户活跃 / 每小时限额 / outreach 投放三闸
（后台类型 ai_social·group_active·pet_visit 不跑——原实现在它们各自分支里就 return 了）。
边界＝**只做拦截判定，不发消息、不做执行**。

返回值口径：``False`` = 被拦下（调用方直接 return），``None`` = 放行（交给 etype 分支执行）。
用 ``is not None`` 判定，别写成真值判断。

闸函数一律经 :class:`GateBundle` 由 arbiter 现取传入，**本模块不 import 它们**——
否则 ``tests/`` 打在 arbiter 命名空间上的 monkeypatch 桩会静默失效（方案 §1 R2/R3）。
常量（``MAX_PER_HOUR`` / ``SLEEP_SILENCED_TYPES``）无人打桩，直接 import。

logger 名故意保留旧名 ``scheduler.arbiter``（D-1 已定）：台账、告警与排障都按
``scheduler.arbiter`` 关键字检索日志，换名等于排障口径全变。
"""
from datetime import datetime, timedelta, timezone

from app.domain.proactivity.decision import MAX_PER_HOUR
from app.domain.proactivity.sleep import SLEEP_SILENCED_TYPES
from app.scheduling.executors.context import GateBundle
# A20 批 4a：后台类型键集改由 registry 单一来源持有（与 @handler 分组同源，不再各留一份副本）
from app.scheduling.executors.registry import BACKGROUND_TYPES  # noqa: F401
from app.utils.logger import get_logger

_logger = get_logger("scheduler.arbiter")


async def pre_gates(item: dict, etype: str, g: GateBundle) -> bool | None:
    """``_execute`` 开头的跨类型前置闸。命中返回 ``False``，放行返回 ``None``。"""
    # 免打扰静默：默认北京时间 0:00-6:59；dnd_enabled 开启时按配置时段（定时承诺除外）
    cn_now = datetime.now(timezone(timedelta(hours=8)))
    _c0 = item.get("candidate") or {}
    _cid = _c0.get("character_id")
    if _cid is None and item.get("event") is not None:
        _cid = item["event"].character_id
    if _cid and await g.is_dnd_now(_cid, cn_now):
        _logger.info("Proactive %s char=%s skipped: dnd", etype, _cid)
        return False

    # 夜晚（21 点后至次日 8 点）用户说过"睡觉" → 主动消息类提前关闭（定时承诺除外）
    if etype in SLEEP_SILENCED_TYPES:
        _cand = item.get("candidate")
        if _cand:
            try:
                if await g.has_user_said_sleep(_cand["character_id"], _cand["user_id"]):
                    _logger.info("Proactive %s char=%d skipped: user said sleep after 21:00",
                                 etype, _cand["character_id"])
                    return False
            except Exception as e:
                _logger.warning("Sleep flag check failed: %s", e)

    # 后台类型到此为止：原实现在它们各自分支就 return 了，不参与下面三道闸
    if etype in BACKGROUND_TYPES:
        return None

    candidate = item["candidate"]
    char_id = candidate["character_id"]

    # 用户正在活跃聊天 → 暂停所有随机行为
    if await g.is_user_active(char_id, candidate["user_id"]):
        return False

    # 每小时保护（特殊事件同样计入）
    if await g.hourly_active(char_id) >= MAX_PER_HOUR:
        return False

    # ── outreach 投放口径三闸（2026-09-13 交接 §二；三个开关默认关 = 逐字节现状）──
    # 命中即留痕 [gate=...] 并 return False（跳过审批/生成，不占额度；日志走 log_trigger_candidate）
    _pacing_hit = await g.pacing_gate(item, etype, char_id, candidate)
    if _pacing_hit:
        g.mark_gate(item, _pacing_hit)
        return False

    return None
