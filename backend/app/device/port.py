"""设备能力读取端口（X7-M0 契约骨架）。

``read_capability``：把「某能力对应 source 的最新一条原始快照」投影成 :class:`CapabilityReading`，
供内核 / 上下文段消费。本批不做字段级解析——``value`` 只有一个 ``raw_text``（该条
``phone_snapshots.content`` 原文）；结构化留 M1。租户过滤沿用现有口径：只按传入 ``user_id`` 取数，
跨租户不读。

``read_perception_context``：现有「手机感知」上下文文本的统一读取入口。M0 阶段委托
``application.phone_service.get_recent_perception_text`` 产出，**逐字保持迁移前行为**；
M1 再切换为基于 ``read_capability`` 的结构化组装（届时才允许文本形态变化，另行验收）。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.db.database import async_session_factory
from app.device.capabilities import get_capability
from app.models.device import PhoneSnapshot
from app.utils.logger import get_logger

_logger = get_logger("device.port")


@dataclass
class CapabilityReading:
    """一次能力读取的结果（M0 仅 raw_text；code ∈ ok / empty / unknown）。"""

    capability_id: str
    value: dict
    code: str
    retriable: bool
    observed_at: str | None
    source: str | None


async def read_capability(capability_id: str, user_id: int) -> CapabilityReading:
    """读取某能力对应 source 的最新一条快照（按 created_at / id 倒序），产出原始 raw_text。

    - 有数据：``code=ok``、``retriable=False``、``value['raw_text']=该条 content``；
    - 无数据（含未知能力、或该能力 source 当前无采集）：``code=empty``、``retriable=True``、raw_text 空；
    - 查询异常：``code=unknown``、``retriable=True``、raw_text 空，且**绝不抛出**（上下文注入链路静默降级）。
    """
    spec = get_capability(capability_id)
    if spec is None:
        return CapabilityReading(capability_id, {"raw_text": ""}, "empty", True, None, None)
    try:
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(PhoneSnapshot)
                    .where(
                        PhoneSnapshot.user_id == user_id,
                        PhoneSnapshot.source.in_(spec.sources),
                    )
                    .order_by(PhoneSnapshot.created_at.desc(), PhoneSnapshot.id.desc())
                    .limit(1)
                )
            ).scalars().first()
    except Exception as e:
        _logger.warning("read_capability failed: cap=%s err=%s", capability_id, e)
        return CapabilityReading(capability_id, {"raw_text": ""}, "unknown", True, None, None)
    if row is None:
        return CapabilityReading(capability_id, {"raw_text": ""}, "empty", True, None, None)
    observed_at = row.created_at.isoformat() if row.created_at is not None else None
    return CapabilityReading(
        capability_id,
        {"raw_text": row.content or ""},
        "ok",
        False,
        observed_at,
        row.source,
    )


async def read_perception_context(user_id: int) -> str:
    """手机感知上下文文本入口（M0 委托旧渲染产出，逐字不变；见模块 docstring）。"""
    from app.application.phone_service import get_recent_perception_text

    return await get_recent_perception_text(user_id)
