"""context 依赖接口（Protocol，方案 2.3）。

agent 侧只依赖这些「需要什么数据/能力」的接口，service 侧实现并注册；
避免 agent → services 的硬 import（依赖方向：api → services → agent → models/db）。
本步骤（步骤 4，试水）暂未启用，仅供后续 section 化 / 依赖修正使用。
"""
from __future__ import annotations

from typing import Protocol


class ContextDataProvider(Protocol):
    """agent 定义「需要什么数据」，service 实现并注册（方案 2.3 示例）。"""

    async def get_inject_text(self, state: dict) -> str: ...


class PermissionGateway(Protocol):
    """permission_service 在 service 侧实现；agent 侧只依赖接口，不再 import service。"""

    async def check_mode(self, user_id: int, scope: str) -> str: ...
    async def check_mcp_mode(self, user_id: int, scope: str, risk_level: str = "medium") -> str: ...
    async def create_pending_action(
        self, user_id: int, session_id: int, character_id: int, scope: str, payload: dict,
    ): ...
