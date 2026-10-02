"""后台社交执行器 — ai_social / group_active / pet_visit（A20 批 4a）

自 ``arbiter._execute`` 逐字节搬入的三条分支（2026-10-02）。三类都是**后台行为**：只过
Stage 1 免打扰，不跑用户活跃/每小时限额/outreach 三闸（豁免键集＝``registry.BACKGROUND_TYPES``，
判定在 ``guards.pre_gates``）。

依赖纪律沿用前几批（方案 §1 R2/R3）：本模块**不 import** 节流闸与会话工厂——解析点一旦落到
这里，tests/ 打在 arbiter 命名空间上的 monkeypatch 桩会静默失效；分支体内的局部 import
原样留在函数内（tests/ 打的是各自模块属性）。logger 名故意保留 ``scheduler.arbiter``（D-1）。
"""
from app.scheduling.executors.context import GateBundle
from app.scheduling.executors.registry import handler


@handler("ai_social")
async def run_ai_social_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # AI 间私聊：后台行为（不推送），不受用户活跃/主动消息限额影响（自身限额在 ai_social 内部）
    from app.scheduling.ai_social import run_ai_social
    return await run_ai_social(
        candidate["character_id"], candidate["character_b_id"], candidate["user_id"],
    )


@handler("group_active")
async def run_group_active_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # 家庭群聊·角色主动冒泡：后台行为（落库群消息，群页轮询拉到即显示）
    from app.scheduling.group_active import run_group_active
    return await run_group_active(
        char_id, candidate["group_id"], candidate["user_id"],
        with_id=candidate.get("with_id"),
    )


@handler("pet_visit")
async def run_pet_visit_exec(item: dict, candidate: dict, char_id: int, g: GateBundle) -> bool:
    # AI 宠物来访：后台行为（只写互动记录+记忆，不推送消息）
    from app.scheduling.pet_care import run_pet_visit
    return await run_pet_visit(
        char_id, candidate["user_id"], candidate["ai_pet_id"],
    )
