"""AMBRACE MCP 接入模块（Phase 1-2：stdio/SSE/streamable-http 客户端 + 工具注册 + 权限 + API）。

- manager.py：MCPClientManager —— 管理多个 MCP Server 连接（stdio/sse/streamable_http；
  连接/断开/发现工具/代理调用；SSRF 防护；状态 Event Bus 广播；mcp_servers.json 部署预置）
- tool_adapter.py：mcp_tool_to_spec —— 把 MCP tool 描述适配进现有 ToolSpec / ToolRegistry
"""
