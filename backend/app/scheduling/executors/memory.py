"""记忆/关怀类执行器 — memory_review 系 + 宠物关怀 + ai_care / ai_adopt（A20 批 4a）

自 ``arbiter._execute`` 逐字节搬入的六条分支（2026-10-02）。共同口径：**限额/免打扰在各执行体
内部再判一次**（arbiter 侧只跑统一前置闸），返回值＝本轮是否真的做了事。

依赖纪律沿用前几批：本模块不 import 节流闸与会话工厂（经 ``GateBundle`` 现取）；分支体内的
局部 import 原样留在函数内。logger 名故意保留 ``scheduler.arbiter``（D-1）。
"""
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler


@handler("memory_review")
async def run_memory_review_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 主动到期复习（P1）：到期记忆自然提及；限额/免打扰在 memory_review 内部处理
    from app.scheduling.memory_review import run_memory_review
    return await run_memory_review(
        char_id, candidate["user_id"], candidate["memory_id"],
    )


@handler("memory_review_contextual")
async def run_memory_review_contextual_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 情境驱动复习（v2.1 Phase 4b）：感知 deep/emotion 或命中进行中目标 → 自然提及；限额复用 run_memory_review
    from app.scheduling.memory_review import run_memory_review
    return await run_memory_review(
        char_id, candidate["user_id"], candidate["memory_id"],
    )


@handler("emotion_care")
async def run_emotion_care_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # AI 情绪关怀：用户低落 → 延迟主动关心（限额/免打扰在 emotion_care 内部处理；P0-1b 经内部统一入口）
    from app.agent.internal_runner import run_internal
    _res = await run_internal(
        "emotion_care",
        {"character_id": char_id, "user_id": candidate["user_id"], "task_id": candidate["task_id"]},
        character_id=char_id, user_id=candidate.get("user_id"),
    )
    _ok = (_res.get("result") or {}).get("ok") if _res.get("status") == "ok" else False
    return bool(_ok)


@handler("pet_remind")
async def run_pet_remind_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 宠物关怀：宠物饿了/脏了 → 角色主动提醒（限额/免打扰/间隔在 pet_care 内部处理）
    from app.scheduling.pet_care import run_pet_remind
    return await run_pet_remind(
        char_id, candidate["user_id"], candidate["pet_id"],
    )


@handler("ai_care")
async def run_ai_care_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # AI 照顾自己的宠物：属性/活动/记忆 + 照顾消息（独立限额 <=1 在 pet_care 内部）
    from app.scheduling.pet_care import run_ai_care
    return await run_ai_care(
        char_id, candidate["user_id"], candidate["pet_id"],
    )


@handler("ai_adopt")
async def run_ai_adopt_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # AI 自主领养：创建 AI 宠物 + 告知消息（限额/概率在 pet_care 内部）
    from app.scheduling.pet_care import run_ai_adopt
    return await run_ai_adopt(char_id, candidate["user_id"])
