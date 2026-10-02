"""节日类执行器 — birthday / holiday / anniversary（A20 批 4a）

自 ``arbiter._execute`` 逐字节搬入的一条分支（三键共用，2026-10-02）。边界＝**生成节日问候并
发送；任何异常都要落一条 ``[send_failed]`` 留痕**——2026-08-20 七夕死循环修复的命门：失败也标记
当日已处理，否则每 30 秒无限重试。

机械改写只有两处（与任务书一致）：分支体缩进归零、取库由 ``async_session_factory`` 改为
``g.session_factory()``（会话工厂必须经 ``GateBundle`` 现取，tests/ 有 21 处按
``arbiter`` 上的该名打桩，本模块自 import 会让桩静默失效）。``engine`` 沿用 ``_execute`` 入口的
函数内 import（scheduler ↔ arbiter 互为依赖，上提顶层会成环）。
"""
from app.models.character import ProactiveMessageLog
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler
from app.utils.logger import get_logger

_logger = get_logger("scheduler.arbiter")


@handler("birthday", "holiday", "anniversary")
async def run_festival_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 生日 / 节日 / 认识纪念日
    from app.scheduling import scheduler as engine
    etype = item["type"]
    try:
        from app.scheduling.message_generator import (
            generate_birthday_message, generate_holiday_message, generate_anniversary_message,
        )
        if etype == "birthday":
            content = await generate_birthday_message(
                character_name=candidate["character_name"],
                character_personality=candidate["character_personality"],
                user_name=candidate["nickname"] or candidate["username"],
                character_id=char_id,
                user_id=candidate["user_id"],
            )
            msg_type = "birthday"
        elif etype == "anniversary":
            content = await generate_anniversary_message(
                character_name=candidate["character_name"],
                character_personality=candidate["character_personality"],
                user_name=candidate["nickname"] or candidate["username"],
                days=int(candidate.get("anniversary_days") or 0),
                character_id=char_id,
                user_id=candidate["user_id"],
            )
            msg_type = "anniversary"
        else:
            content = await generate_holiday_message(
                character_name=candidate["character_name"],
                character_personality=candidate["character_personality"],
                user_name=candidate["nickname"] or candidate["username"],
                holiday_name=candidate.get("holiday_name", ""),
                character_id=char_id,
                user_id=candidate["user_id"],
            )
            msg_type = "holiday"
        await engine.send_to_session(
            candidate["session_id"], char_id, candidate["user_id"],
            content, message_type=msg_type,
            holiday_name=candidate.get("holiday_name"),
        )
        return True

    except Exception as e:
        # 2026-08-20 七夕死循环修复：生成/发送失败也标记当日已处理，防每 30 秒无限重试
        _logger.warning('Festival msg failed char=%d type=%s: %s', char_id, etype, e)
        try:
            async with g.session_factory() as db:
                db.add(ProactiveMessageLog(
                    character_id=char_id,
                    session_id=candidate.get('session_id'),
                    message_type=('holiday' if etype == 'holiday' else etype),
                    holiday_name=candidate.get('holiday_name'),
                    content='[send_failed] ' + str(e)[:200],
                ))
                await db.commit()
        except Exception as _le:
            _logger.warning('Festival fail-log failed: %s', _le)
        return False
