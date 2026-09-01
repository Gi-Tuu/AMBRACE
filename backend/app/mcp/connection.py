"""MCP 连接状态（F1 拆分，2026-08-31）：单个 Server 的活动连接数据类。

自 manager.py 原样搬移；app.mcp.manager 保留同名重导出（兼容面不变）。
"""
from dataclasses import dataclass, field
from typing import Any

from app.mcp.transport import STATUS_CONNECTED, STATUS_DISCONNECTED


@dataclass
class _Connection:
    """单个 MCP Server 的活动连接状态（由 _worker_main 任务驱动）。"""

    server_id: int
    server_name: str = ""
    enabled: bool = True
    # 归属用户（连接建立时从 DB 行带入；日志/资源注入按用户隔离用）
    user_id: int | None = None
    # 发现到的工具（list of dict：name/description/input_schema；由 worker 任务刷新）
    tools: list[dict] = field(default_factory=list)
    # 发现的资源（Phase 4：list of dict：uri/name/description/mime_type；连接时缓存）
    resources: list[dict] = field(default_factory=list)
    # 发现的提示词（Phase 4：list of dict：name/description/arguments；连接时缓存）
    prompts: list[dict] = field(default_factory=list)
    status: str = STATUS_DISCONNECTED
    last_error: str | None = None
    # worker 任务交互
    _queue: Any = None  # asyncio.Queue（put 命令，worker 消费）
    _worker: Any = None  # asyncio.Task（_worker_main）
    _ready: Any = None  # asyncio.Future（初次连接完成/失败）
    # P3-A（2026-08-29）：per-server 连接锁，串行化并发 connect，避免孤儿 worker/子进程
    connect_lock: Any = None  # asyncio.Lock（懒创建；须在运行事件循环内创建）

    @property
    def is_connected(self) -> bool:
        return self._worker is not None and not self._worker.done() and self.status == STATUS_CONNECTED
