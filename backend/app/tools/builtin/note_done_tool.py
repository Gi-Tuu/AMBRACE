"""note_done 内置工具（批 G4，2026-09-26）。

把「小手机备注标记完成/重开」的执行入口登记到 ToolRegistry：
- 触发形态：模型读小手机清单后，对**已经结束**（办完/过期）的条目输出
  [CAL_DONE]关键词[/CAL_DONE] / [MEMO_DONE]关键词[/MEMO_DONE]（解析层 app/agent/actions.py
  统一成 action_type=NOTE_DONE，payload 里的 type 区分日历 / 备忘）；
- execute 内惰性 import services（避免 agent↔services 循环）；
- 本地能力 scope=None 无权限门禁；idempotent=True（同一目标重复标记结果一致，失败自动重试 1 次）；
- 执行侧命中不到行一律 ok=False（绝不静默成功），执行结果进 observation 供模型据实续话。
"""
from app.agent.tools import RISK_LOW, ToolSpec, register_tool


async def _execute_note_done(payload: dict, *, user_id=None, character_id=None, session_id=None) -> dict:
    """执行 note_done：按关键词把本角色的小手机日历/备忘行标记为 done（或 active 重开）。"""
    from app.application.chat.tools import _mark_note_status

    res = await _mark_note_status(
        int(payload.get("character_id") or character_id or 0),
        str(payload.get("type") or ""),
        str(payload.get("match") or ""),
        str(payload.get("status") or "done"),
    )
    return {"ok": bool(res.get("ok")), "summary": str(res.get("message") or "")}


def register() -> None:
    register_tool(ToolSpec(
        name="note_done",
        description="小手机备注标记（[CAL_DONE]/[MEMO_DONE]）：把已结束的日历/备忘条目标记完成或重开",
        action_type="NOTE_DONE",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=True,
        scope=None,  # 本地能力：无权限门禁
        execute=_execute_note_done,
        provenance="note",
    ))
