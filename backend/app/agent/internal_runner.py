"""内部 AI 行为统一执行器（P0-1b，2026-08-16）

高频内部行为（记忆提炼/事实核查/情绪关怀/卡片生成等）经统一工具入口 execute_tool 执行：
- scope=None 无权限门禁（系统行为，非用户可见）；
- 统一工具生命周期钩子（requested→permission_checked→started→result/error→finished，插件可挂载）；
- tool.executed 事件（可观测内部行为次数/耗时/结果）+ Observation 标注；
- 异常隔离：内部行为失败不影响主链路（execute_tool 语义）。
"""
from app.agent.tools import ToolSpec, get_tool
from app.utils.logger import get_logger

_logger = get_logger("agent.internal_runner")


async def run_internal(
    tool_name: str,
    payload: dict,
    *,
    character_id: int | None = None,
    user_id: int | None = None,
) -> dict:
    """执行一个内部 AI 行为（经统一工具入口）。返回 execute_tool 结果 dict。"""
    try:
        from app.agent.tool_runner import execute_tool
        spec = get_tool(tool_name)
        if spec is None:
            _logger.warning("internal tool not registered: %s", tool_name)
            return {"status": "error", "tool": tool_name, "error": "not registered"}
        exec_spec = ToolSpec(
            name=spec.name,
            description=spec.description,
            action_type=spec.action_type,
            risk_level=spec.risk_level,
            rate_limit=spec.rate_limit,
            idempotent=spec.idempotent,
            scope=None,  # 内部行为无权限门禁
            ask_auto_allow=False,
            epistemic_status=getattr(spec, "epistemic_status", "FACT"),
            provenance=spec.provenance,
            execute=_dispatch(tool_name, payload),
        )
        return await execute_tool(
            exec_spec, payload, user_id=None, character_id=character_id, session_id=None,
        )
    except Exception as e:
        _logger.warning("internal tool %s failed: %s", tool_name, e)
        return {"status": "error", "tool": tool_name, "error": str(e)}


def _dispatch(tool_name: str, payload: dict):
    """按工具名返回 execute 包装函数（惰性 import 原实现，payload 解包）"""
    if tool_name == "memory_extract":
        async def _run(p: dict):
            from app.memory.extractor import extract_single
            return await extract_single(
                session_id=int(p.get("session_id") or 0),
                character_id=int(p.get("character_id") or 0),
                user_id=int(p.get("user_id") or 0),
                user_msg=p.get("user_msg") or "",
                ai_msg=p.get("ai_msg") or "",
                source_id=p.get("source_id"),
            )
        return _run
    if tool_name == "memory_fact_check":
        async def _run(p: dict):
            from app.memory.fact_check import async_fact_check
            await async_fact_check(
                character_id=int(p.get("character_id") or 0),
                user_id=int(p.get("user_id") or 0),
                user_msg=p.get("user_msg") or "",
                ai_response=p.get("ai_response") or "",
            )
            return {"ok": True}
        return _run
    if tool_name == "emotion_care":
        async def _run(p: dict):
            from app.scheduler.emotion_care import run_emotion_care
            ok = await run_emotion_care(
                char_id=int(p.get("character_id") or 0),
                user_id=int(p.get("user_id") or 0),
                task_id=int(p.get("task_id") or 0),
            )
            return {"ok": bool(ok)}
        return _run
    if tool_name == "weave_card":
        async def _run(p: dict):
            from app.weave.card_generator import generate_cards
            result = await generate_cards(
                user_id=int(p.get("user_id") or 0),
                character_id=int(p.get("character_id") or 0) or None,
                force=bool(p.get("force")),
                max_cards=p.get("max_cards"),
                domain=p.get("domain") or "shared",
            )
            return {"ok": True, "result": result}
        return _run
    if tool_name == "memory_summary":
        async def _run(p: dict):
            from app.memory.summary import summarize_identity
            result = await summarize_identity(
                character_id=int(p.get("character_id") or 0),
                user_id=int(p.get("user_id") or 0),
                force=bool(p.get("force")),
            )
            return {"ok": True, "result": result}
        return _run
    # 未接入执行（占位登记：后续新内部工具在此扩展）
    async def _unwired(p: dict):
        return {"ok": False, "message": f"内部工具 {tool_name} 未接执行入口（占位登记）"}
    return _unwired
