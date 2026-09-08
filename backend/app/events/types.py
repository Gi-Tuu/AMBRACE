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
    # ── 3.10 聊天域（持久流水 domain_events）──
    CHAT_SESSION_CREATED = "chat.session_created"
    CHAT_MESSAGE_SENT = "chat.message_sent"          # data.sender_type=user/ai；data.route=http/ws_chunk/sse_batch/sse_live/continue/...
    CHAT_MESSAGE_DELETED = "chat.message_deleted"
    CHAT_SESSION_READ = "chat.session_read"
    CHAT_TURN_COMPLETED = "chat.turn_completed"      # 一轮 user→ai 清算点（幂等键绑 user_msg_id，三路径只落一条）
    # ── 3.10 朋友圈域（持久流水 domain_events，P1 埋点）──
    MOMENT_PUBLISHED = "moment.published"            # actor_type 区分 ai/user；旧名 life.moment_published 保留兼容窗口
    MOMENT_COMMENT_ADDED = "moment.comment_added"    # actor_type=ai/user；data.round 标评论轮次
    MOMENT_COMMENT_DELETED = "moment.comment_deleted"
    MOMENT_LIKED = "moment.liked"                    # 用户点赞（toggle 之「赞」）
    MOMENT_UNLIKED = "moment.unliked"                # 用户取消赞
    MOMENT_AI_LIKED = "moment.ai_liked"              # AI 批量点赞（一条事件带 char_ids 列表）
    MOMENT_DELETED = "moment.deleted"
    MOMENT_CLEARED = "moment.cleared"
    MOMENT_READ = "moment.read"
