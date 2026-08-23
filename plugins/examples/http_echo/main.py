"""示例插件：HTTP 回显 + 记忆检索注入演示

- http_router：GET /api/v1/plugins/http_echo/echo?text=... 自定义 API
- memory_search：检索关键词含「插件/plugin」时，向召回结果注入一条演示记忆
"""
import time

from app.plugins import sdk

router = sdk.router()


@router.get("/echo")
async def echo(text: str = ""):
    return {"echo": text, "plugin": "http_echo", "ts": int(time.time())}


@sdk.hook("memory_search")
def inject_demo_memory(ctx):
    query = str(ctx.get("query") or "")
    if "插件" not in query and "plugin" not in query.lower():
        return None
    return [{
        "id": -1001,  # 虚拟 id（负数不与真实记忆冲突）
        "content": "【插件注入】用户对插件系统感兴趣：拥爱（AMBRACE）支持服务器端 Python 插件扩展（hooks/action/自定义 API）。",
        "type": "plugin_demo",
        "importance": 5.0,
    }]
