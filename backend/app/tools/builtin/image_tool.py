"""image_gen 内置工具（AMBRACE 重构步骤 8）。

把「聊天内生图」的执行入口登记到 ToolRegistry：
- execute 内惰性 import services（避免 agent↔services 循环）；
- scope="image_gen" 权限三档；risk_level=MIDIUM；idempotent=False。
"""
from app.agent.tools import RISK_MEDIUM, ToolSpec, register_tool


async def _execute_image(payload: dict, *, user_id=None, character_id=None, session_id=None) -> dict:
    """执行 image_gen：触发异步生图并追加 AI 图片消息。返回 {ok, summary}。

    实际生成流程（_gen_image_flow）内部自处理 AI 能力权限（forbid/ask）与每日额度；
    此处先做工具级权限三档（scope="image_gen"），再把生成委托给 services 层。

    P1-4（2026-09-16）：落库前统一清洗——画面描述去标记残片（prompt 只进 extra_meta），
    配文经同一出口判定「裸 prompt → None」，绝不让整段生图 prompt 变成可见 content。
    """
    from app.agent.actions import sanitize_image_caption, sanitize_image_prompt
    from app.application.chat.tools import _gen_image_flow

    prompt = sanitize_image_prompt(str(payload.get("prompt") or ""))
    if not prompt:
        return {"ok": False, "summary": "生图指令缺少画面描述"}
    raw_caption = payload.get("img_text")
    caption = sanitize_image_caption(
        str(raw_caption).strip() if raw_caption else None, prompt)
    await _gen_image_flow(
        int(payload.get("user_id") or user_id or 0),
        int(payload.get("character_id") or character_id or 0),
        int(payload.get("session_id") or session_id or 0),
        prompt,
        caption,
    )
    return {"ok": True, "summary": "图片生成任务已提交"}


def register() -> None:
    register_tool(ToolSpec(
        name="image_gen",
        description="聊天内生图（[GEN_IMAGE]画面描述[/GEN_IMAGE] + [IMG_TEXT]图片消息文案[/IMG_TEXT]）",
        action_type="GEN_IMAGE",
        risk_level=RISK_MEDIUM,
        rate_limit="daily limit",
        idempotent=False,
        scope="image_gen",
        execute=_execute_image,
        provenance="image_gen",
    ))
