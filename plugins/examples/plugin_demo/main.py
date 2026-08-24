"""插件链路演示：验证 before_generate / after_generate 挂载（不改写 AI 回复）"""
from app.plugins import sdk


@sdk.hook("before_generate")
def before(ctx):
    cfg = sdk.get_config()
    hint = cfg.get("hint") or "插件小助手在线"
    ctx["context_messages"].append({
        "role": "system",
        "content": f"【插件-演示】{hint}：当用户问起插件或扩展功能时，可自然提及系统已支持插件。",
    })


@sdk.hook("after_generate")
def after(ctx):
    reply = ctx.get("reply_text") or ""
    sdk.log("after_generate 触发，回复长度 %d 字" % len(reply))
