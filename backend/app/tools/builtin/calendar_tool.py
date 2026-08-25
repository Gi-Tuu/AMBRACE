"""note_calendar 内置工具（AMBRACE 重构步骤 8）。

把「小手机日历备注」的执行入口登记到 ToolRegistry：
- execute 内惰性 import services（避免 agent↔services 循环）；
- 本地能力 scope=None 无权限门禁；idempotent=True 落库去重。
"""
from app.agent.tools import RISK_LOW, ToolSpec, register_tool


async def _execute_calendar(payload: dict, *, user_id=None, character_id=None, session_id=None) -> dict:
    """执行 note_calendar：按日期落库、去重、角色署名。返回 {ok, summary}。"""
    from app.services.chat.tools import _save_calendar_note

    ok = await _save_calendar_note(
        int(payload.get("character_id") or character_id or 0),
        str(payload.get("date") or ""),
        str(payload.get("text") or ""),
        str(payload.get("author") or ""),
    )
    return {"ok": bool(ok), "summary": "已记到小手机日历" if ok else "未记录"}


def register() -> None:
    register_tool(ToolSpec(
        name="note_calendar",
        description="小手机日历备注（[CAL_NOTE]日期 内容[/CAL_NOTE]）：按日期落库、去重、角色署名",
        action_type="CAL_NOTE",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=True,
        scope=None,  # 本地能力：无权限门禁
        execute=_execute_calendar,
        provenance="note",
    ))
