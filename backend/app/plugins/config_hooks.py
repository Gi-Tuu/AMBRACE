"""配置驱动零代码钩子（48c）：prompt 型插件的触发匹配与 systemPrompt 注入

- 无需 main.py：type=prompt 插件仅靠 manifest 的 config.prompt 即可向聊天注入技能；
- match_prompt_trigger 为纯函数（子串匹配，无正则注入面），可单测；
- inject_prompt_skill 由 context_builder 生成前调用，异常隔离不阻断主链路。
"""
from app.utils.logger import get_logger

_logger = get_logger("plugins.config_hooks")


def match_prompt_trigger(user_message: str, cfg: dict) -> str | None:
    """纯函数：user_message 是否包含 cfg.trigger 中任一子串；命中返回该 trigger，否则 None。

    - cfg 为 config.prompt 块：{"trigger": [...], "systemPrompt": "..."}
    - 仅子串匹配（不含正则），空消息/非法 cfg 返回 None。
    """
    if not user_message or not isinstance(cfg, dict):
        return None
    triggers = cfg.get("trigger")
    if not isinstance(triggers, list):
        return None
    um = user_message
    for t in triggers:
        if isinstance(t, str) and t.strip() and t.strip() in um:
            return t.strip()
    return None


async def inject_prompt_skill(ctx: dict) -> None:
    """生成前注入：遍历已启用且 type=prompt 的插件，user_message 命中 trigger 时
    向 ctx["context_messages"] 追加一条 system 消息（systemPrompt）。异常隔离。"""
    try:
        from app.plugins import registry
        user_message = str(ctx.get("user_message") or "")
        if not user_message:
            return
        context_messages = ctx.get("context_messages")
        if not isinstance(context_messages, list):
            return
        for name, entry in list(registry._loaded.items()):
            if not registry._enabled.get(name, False):
                continue
            info = entry.get("info") or {}
            if info.get("type") != "prompt":
                continue
            # 合并 DB 覆盖值（前端零代码编辑器保存后生效）
            base = dict(info.get("config") or {})
            saved = dict(registry._db_config.get(name, {}))
            base.update(saved)
            pcfg = base.get("prompt")
            if not isinstance(pcfg, dict):
                continue
            hit = match_prompt_trigger(user_message, pcfg)
            if hit is None:
                continue
            system_prompt = str(pcfg.get("systemPrompt") or "").strip()
            if not system_prompt:
                continue
            desc = str(pcfg.get("description") or "").strip()
            header = f"【技能·{name}】"
            if desc:
                header += f"（{desc[:80]}）"
            context_messages.append({
                "role": "system",
                "content": f"{header}\n{system_prompt}",
            })
            _logger.info("prompt skill injected plugin=%s trigger=%s", name, hit)
    except Exception as e:
        _logger.warning("prompt skill inject failed: %s", e)
