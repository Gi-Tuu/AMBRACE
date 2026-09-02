"""search 内置工具（AMBRACE 重构步骤 8）。

把「自主联网搜索」的执行入口登记到 ToolRegistry：
- execute 内惰性 import services（避免 agent↔services 循环）；
- scope="browser" 权限三档；ask_auto_allow=True 只读低风险放行；epistemic_status=UNVERIFIED。
"""
from app.agent.tools import RISK_LOW, ToolSpec, register_tool


async def _execute_search(payload: dict, *, user_id=None, character_id=None, session_id=None) -> dict:
    """执行 search：节流判断 + 联网搜索。返回 {ok, summary}。"""
    from app.application.chat.tools import _run_web_search, _search_throttle

    query = str(payload.get("query") or "").strip()
    if not query:
        return {"ok": False, "summary": "搜索词为空"}
    if user_id is not None and not _search_throttle(user_id):
        return {"ok": False, "summary": "搜索过于频繁，请稍后再试"}
    text = await _run_web_search(query)
    if not text:
        return {"ok": False, "summary": "未搜索到可用结果"}
    # 完整结果随 result 返回；observation.summary 由 tool_runner._make_observation 按上限截断。
    return {"ok": True, "summary": text}


def register() -> None:
    register_tool(ToolSpec(
        name="search",
        description="自主联网搜索（[SEARCH]查询[/SEARCH]）：Bing 中文优先+DDG 兜底，结果注入二次生成",
        action_type="SEARCH",
        risk_level=RISK_LOW,
        rate_limit="1/60s per user",
        idempotent=True,
        scope="browser",
        ask_auto_allow=True,  # 只读低风险：ask 不挂起，直接执行（forbid 仍拦截）
        epistemic_status="UNVERIFIED",  # 网络搜索结果未证实（Phase G Observation）
        provenance="web_search",
        execute=_execute_search,
        input_schema={"query": {"type": "string"}},
    ))
