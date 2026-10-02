"""剧情/线索类执行器 — life_regression / state_trigger / unfinished_topic / prospective_intent（A20 批 4a）

自 ``arbiter._execute`` 逐字节搬入的四条分支（2026-10-02）。四条都是「拿 candidate 直接交给
各自模块的执行体」，频控与免打扰由执行体内部再判。

``run_life_regression`` / ``run_unfinished_topic`` / ``run_prospective_due`` 在搬前就是 arbiter
的**模块顶层 import**（``arbiter.py:15-17``），故此处同样在顶层引入；arbiter 侧那三行按名保留
（命名空间不缩），两边指向同一实现。``check_state_triggers`` 原就在分支体内局部 import，照原样留。

依赖纪律沿用前几批：不 import 节流闸与会话工厂；logger 名故意保留 ``scheduler.arbiter``（D-1）。
"""
from app.scheduling.life_regression import run_life_regression
from app.scheduling.prospective_intent import run_prospective_due  # Ariadne 模块G（2026-09-04）
from app.scheduling.unfinished_topic import run_unfinished_topic
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler


@handler("life_regression")
async def run_life_regression_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 生活回归摘要（Phase 2）：近 24h 生活记忆自然提及（每日 <=1 次在 collect 内处理；受统一限额/免打扰约束）
    return await run_life_regression(candidate)


@handler("state_trigger")
async def run_state_trigger_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 状态触发兜底（v2）：查错过的触发；防抖/冷却/概率/免打扰在 state_triggers 内部处理
    from app.scheduling.state_triggers import check_state_triggers
    return await check_state_triggers(char_id, candidate["user_id"], probability_multiplier=1.0)


@handler("unfinished_topic")
async def run_unfinished_topic_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 对话未收尾跟进：用户抛了话头（下次/改天/有空）→ 自然捡起话题（每日 1 次/角色，collect 内去重）
    return await run_unfinished_topic(candidate)


@handler("prospective_intent")
async def run_prospective_intent_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # Ariadne 模块G（2026-09-04）：到期承诺自然提起（一次性，兑现即焚；复用主动消息生成与免打扰/额度闸门）
    return await run_prospective_due(candidate)
