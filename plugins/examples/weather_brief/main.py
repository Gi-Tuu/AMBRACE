"""天气简报插件：在 AI 回复前注入用户当地天气（复用 Open-Meteo 天气服务）"""
from app.plugins import sdk


@sdk.hook("context_inject")
async def inject_weather(ctx):
    try:
        from app.application.weather_service import get_user_weather_line
        line = await get_user_weather_line(ctx.get("user_id") or 1)
        if not line:
            return
        cfg = sdk.get_config()
        prefix = cfg.get("prefix") or "【插件-天气简报】"
        ctx["context_messages"].append({
            "role": "system",
            "content": prefix + line,
        })
        sdk.log("已注入天气: %s", line[:50])
    except Exception as e:
        sdk.log("天气注入失败: %s", e)
