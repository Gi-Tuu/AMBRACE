"""事件类型常量（演进规划 v2 Phase A：先 3 个发布点，后续扩展）"""
from enum import Enum


class EventType(str, Enum):
    # Life Engine
    LIFE_ACTIVITY_COMPLETED = "life.activity_completed"
    LIFE_MOMENT_PUBLISHED = "life.moment_published"
    # Memory
    MEMORY_WRITTEN = "memory.written"
    # Agent Runtime（Phase G：工具执行 / 任务完成，2026-08-16）
    TOOL_EXECUTED = "tool.executed"
    TASK_COMPLETED = "task.completed"  # Phase H 任务态落地后发布
    # MCP（Phase 2，2026-08-26）：Server 连接状态变化（connected/disconnected/error）
    MCP_SERVER_STATUS = "mcp.server_status"
