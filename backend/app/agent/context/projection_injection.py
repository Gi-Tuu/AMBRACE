"""Refresh the authorized projection after identity resolution and before final message assembly."""
from app.agent.workspace import create_workspace
from app.agent.workspace_projection import inject_flag_on, project_into_workspace, render_projection_block


async def projection_blocks(state: dict, previous: list[str]) -> list[str]:
    if not inject_flag_on():
        return previous
    scope = tuple(state.get(k) for k in ("user_id", "character_id", "session_id"))
    if any(value is None for value in scope):
        return []
    ws = state.get("workspace")
    if ws is None or tuple(getattr(ws, k, None) for k in ("user_id", "character_id", "session_id")) != scope:
        ws = create_workspace(user_id=scope[0], character_id=scope[1], session_id=scope[2])
        state["workspace"] = ws
    # Refresh projected values each build, so a failed read cannot reuse last build's facts.
    ws.identity = {}
    ws.current_state = {}
    ws.world = {}
    ws.working_state = {}
    ws.active_topics = []
    try:
        report = await project_into_workspace(state, for_context=True)
        if report is None:
            return []
        text = render_projection_block(ws)
    except Exception:
        return []
    state["_projection_context_scope"] = scope
    return [text] if text else []
