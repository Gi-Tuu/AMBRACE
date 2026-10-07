"""情绪关怀 IO 端口（架构地图断点 #1 样板，2026-09-29）。

约定：domain 侧（app/domain/emotion/care.py）只保留判定逻辑与 prompt 组装，一切 IO（DB / LLM /
发送 / 人设素材）经本文件定义的协议由上层注入；生产实现见 app/application/emotion_care_ports.py。

本文件是纯类型声明：零 IO、零业务模块 import（只有 dataclasses / datetime / typing）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class CarePortsNotInjected(RuntimeError):
    """未注入 EmotionCarePorts 且没有可用的兼容钩子时抛出（不静默降级，问题当场可见）。"""


@dataclass(frozen=True)
class CareTaskView:
    """emotion_care_tasks 行的只读快照（domain 只用到这几个字段，不跨层传 ORM 实体）。"""

    id: int
    status: str = ""
    trigger_msg: str = ""
    user_id: int = 0
    character_id: int = 0
    due_at: datetime | None = None


@dataclass(frozen=True)
class CareCharacterView:
    """角色快照：name / personality 供人设兜底文案，其余字段留在上层不进 domain。"""

    id: int
    name: str = ""
    personality: str | None = None


class EmotionCarePorts(Protocol):
    """care.py 需要的最小端口集合（计数查询 / 任务表 / 角色与会话 / 生成素材 / LLM / 写日志发送）。"""

    # ── 开关与任务表 ──
    async def proactive_enabled(self, character_id: int) -> bool: ...

    async def has_pending_care_task(self, user_id: int, character_id: int) -> bool: ...

    async def create_care_task(self, *, user_id: int, character_id: int,
                               trigger_msg: str, due_at: datetime) -> None: ...

    async def load_care_task(self, task_id: int) -> CareTaskView | None: ...

    async def finish_care_task(self, task_id: int, status: str) -> None: ...

    async def cancel_stale_care_tasks(self, now: datetime, stale_before: datetime) -> None: ...

    async def fetch_due_care_tasks(self, now: datetime,
                                   stale_before: datetime) -> list[CareTaskView]: ...

    # ── 每日限额 / 最小间隔 / 免打扰（proactive_message_logs + dnd 设置）──
    async def daily_care_count(self, character_id: int) -> int: ...

    async def last_care_at(self, character_id: int) -> datetime | None: ...

    async def user_in_dnd(self, user_id: int) -> bool: ...

    # ── 角色与会话 ──
    async def load_character(self, character_id: int) -> CareCharacterView | None: ...

    async def latest_session_id(self, user_id: int, character_id: int) -> int | None: ...

    # A32（2026-10-07）：到期执行前重取现状用——该会话最近 limit 条**用户**正文（按时间正序）。
    # 只看用户侧，避免角色自己上一句关怀里的「到家了吗」被当成剧情已推进。
    async def recent_messages(self, session_id: int, limit: int = 6) -> list[str]: ...

    # ── 生成素材（人设块 / 主动通道 persona / 天气 / 现状锚护栏块）──
    async def build_identity_prompt(self, character_id: int, user_id: int) -> str: ...

    async def build_active_persona(self, character_id: int, user_id: int) -> str: ...

    async def weather_line(self, user_id: int) -> str: ...

    async def state_guard_block(self, character_id: int, user_id: int) -> str: ...

    # ── LLM 与主动消息出口 ──
    async def reasoning_level(self, character_id: int) -> int: ...

    async def chat_completion(self, *, messages: list[dict[str, str]], temperature: float,
                              max_tokens: int, task: str, user_id: int) -> str: ...

    async def send_care_message(self, *, session_id: int, character_id: int, user_id: int,
                                content: str, message_type: str,
                                extra_meta: str | None = None) -> object | None:
        """A37 批 1：返回发送结局（生产实现转发 `scheduler.SendResult`）。

        返回 None ＝"替身/旧实现没表态"，调用侧按已发处理（逐字节保持改前行为）；
        返回 `SendResult(ok=False, ...)` ＝被闸拦下，调用侧**不得**写 done。
        """
        ...
