"""MCP 分类示例：在「扩展」页归入 MCP 分类，并向 AI 注入 MCP 通道说明

Phase 2 将在此接入真实 mcp_client（stdio/SSE/http），把外部 MCP 工具注入 AI 上下文；
当前为演示占位，验证分类展示与 context_inject 链路。
"""
from app.plugins import sdk


@sdk.hook("context_inject")
def inject_mcp(ctx):
    cfg = sdk.get_config()
    channel = cfg.get("channel") or "demo"
    ctx["context_messages"].append({
        "role": "system",
        "content": f"【插件-MCP示例】MCP 通道（{channel}）已挂载（演示）。若用户问起 MCP/工具接入，可说明当前为演示状态，真实工具接入在规划中。",
    })
    sdk.log("mcp_demo context_inject 触发")
