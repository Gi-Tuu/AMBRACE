"""主动通道「现状锚 + 时空纪律」共享前置（C12a，2026-09-25）。

A7（2026-09-24）把这套护栏分别写进了 scheduling/life_regression.py 与 scheduling/pet_care.py，
两份实现同源但各自持有；本模块收敛为唯一来源，两处保留原函数名做薄封装（行为逐字不变）。
"""
from __future__ import annotations

# 时空纪律固定文案（逐字抄自 A7 两份实现；一致性由 tests/test_proactive_state_guard_a7.py 钉住）
STATE_GUARD_DISCIPLINE = (
    "【时空纪律】上面【当前现状】里的内容才是TA现在的真实情况；你想起的过往、旧地点、旧安排都属往事，"
    "提起时用「我记得…/还记得…」这类回忆口吻自然带过，不要当成现在正在发生的事；与【当前现状】冲突时一律以现状为准。"
)


async def current_state_anchor(character_id: int | None = None, user_id: int | None = None,
                               *, max_chars: int = 200) -> str:
    """当前现状锚（fail-open：拿不到或抛异常一律返回空串，绝不抛）。"""
    try:
        from app.memory.current_state import current_user_state_anchor
        anchor = await current_user_state_anchor(character_id=character_id, user_id=user_id,
                                                 include_profile_location=True, max_chars=max_chars)
        return anchor or ""
    except Exception:
        return ""


def guard_segments(anchor: str | None) -> list[str]:
    """【当前现状】+【时空纪律】两段（纯函数）；anchor 为空/空白时省略现状段。"""
    segs: list[str] = []
    if anchor and anchor.strip():
        segs.append("【当前现状】" + anchor.strip())
    segs.append(STATE_GUARD_DISCIPLINE)
    return segs


def cn_now_line() -> str:
    """北京时间一句话；life_regression._cn_now_prefix 多一个中文午别，两份未合并。"""
    from app.utils.timeutil import app_local_now
    now = app_local_now()
    week = "一二三四五六日"[now.weekday()]
    return (f"现在是北京时间 {now.year}年{now.month}月{now.day}日 "
            f"星期{week} {now.hour:02d}:{now.minute:02d}。")


def guard_block(anchor: str | None) -> str:
    """现状/纪律整块，结尾带换行；时间行由各调用方自行拼接（保持 A7 两处 prompt 排版不变）。"""
    return "\n".join(guard_segments(anchor)) + "\n"
