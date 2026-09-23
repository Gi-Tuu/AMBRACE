"""设备能力读取端口（X7-M0 契约骨架 + M1 结构化承载）。

``read_capability``：把「某能力对应 source 的最新一条原始快照」投影成 :class:`CapabilityReading`，
供内核 / 上下文段消费。M1 起 ``value`` 有两个键：

- ``raw_text``：该条 ``phone_snapshots.content`` 原文（语义与 M0 逐字一致）；
- ``structured``：该条 ``payload_json`` 解析出的字段级对象；无载荷 / 非对象 / 坏 JSON → ``None``。

三态（``ok`` / ``empty`` / ``unknown``）与 ``retriable`` 语义不变，异常照旧不抛。

``read_perception_records``：M1 新增的手机感知「逐条记录」读取入口——窗口/去重口径与旧文本渲染
（``application.phone_service.get_recent_perception_text``）一致，但只出数据不出文案，渲染交给
上下文段（``agent/context/section_phone.py``）。之所以不复用 ``read_capability``：能力清单只有 8 条
设备能力，剪贴板 / 相册 / 操作结果在能力表里没有对应项，按能力取数会直接丢掉这三类采集。

会话工厂刻意按属性访问（``database.async_session_factory``）而不是 ``from … import`` 绑死名字：
上下文链路的测试（``tests/test_context_no_caller_failclosed._patch_session``）只改 ``app.db.database``
上的名字，绑死引用会读不到临时库。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db import database
from app.device.capabilities import get_capability
from app.models.device import PhoneSnapshot
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc

_logger = get_logger("device.port")

# 感知记录口径（与旧文本渲染同源：最近 30 分钟、每来源留最新一条、候选 8 条）
PERCEPTION_MAX_AGE_MINUTES = 30
PERCEPTION_LIMIT = 8


@dataclass
class CapabilityReading:
    """一次能力读取的结果（code ∈ ok / empty / unknown）。"""

    capability_id: str
    value: dict
    code: str
    retriable: bool
    observed_at: str | None
    source: str | None
    confidence: float | None = None  # 载荷里含 confidence 就用它，否则 None（M2 加法，不影响三态）


@dataclass
class PerceptionRecord:
    """一条手机感知记录（同一来源只会有最新一条，见 :func:`read_perception_records`）。"""

    source: str
    raw_text: str
    image_desc: str
    structured: dict | None
    observed_at: datetime | None  # naive UTC，与 phone_snapshots.created_at 同口径


def _no_data(capability_id: str, code: str) -> CapabilityReading:
    """empty / unknown 两态共用的空结果（value 形状与 ok 态一致，消费方无需分支）。"""
    return CapabilityReading(capability_id, {"raw_text": "", "structured": None}, code, True, None, None)


def _parse_payload(raw: str | None) -> dict | None:
    """``payload_json`` → 字段级对象；空 / 坏 JSON / 非对象 → ``None``（读侧绝不抛）。"""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _extract_confidence(payload: dict | None) -> float | None:
    """从结构化载荷取 ``confidence``：含则转 float，缺失 / 非数值 → ``None``（绝不抛）。"""
    if not isinstance(payload, dict) or "confidence" not in payload:
        return None
    try:
        return float(payload["confidence"])
    except (TypeError, ValueError):
        return None


async def read_capability(capability_id: str, user_id: int) -> CapabilityReading:
    """读取某能力对应 source 的最新一条快照（按 created_at / id 倒序）。

    - 有数据：``code=ok``、``retriable=False``、``value={'raw_text', 'structured'}``；
    - 无数据（含未知能力、或该能力 source 当前无采集）：``code=empty``、``retriable=True``、值全空；
    - 查询异常：``code=unknown``、``retriable=True``、值全空，且**绝不抛出**（上下文注入链路静默降级）。
    """
    spec = get_capability(capability_id)
    if spec is None:
        return _no_data(capability_id, "empty")
    try:
        async with database.async_session_factory() as db:
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
        return _no_data(capability_id, "unknown")
    if row is None:
        return _no_data(capability_id, "empty")
    observed_at = row.created_at.isoformat() if row.created_at is not None else None
    payload = _parse_payload(row.payload_json)
    value = {"raw_text": row.content or "", "structured": payload}
    return CapabilityReading(
        capability_id, value, "ok", False, observed_at, row.source, _extract_confidence(payload)
    )


async def read_perception_records(
    user_id: int | None,
    max_age_minutes: int = PERCEPTION_MAX_AGE_MINUTES,
    limit: int = PERCEPTION_LIMIT,
) -> list[PerceptionRecord]:
    """取最近 ``max_age_minutes`` 分钟内、每个来源最新一条感知快照（原始数据，不产文案）。

    排序 created_at 倒序（同刻按 id 倒序，保证确定性）→ 同来源只留最新 → 丢弃无时间戳/超时的行。
    查询异常不抛，返回空列表（消费方按「无」降级）。
    """
    if user_id is None:
        return []
    try:
        async with database.async_session_factory() as db:
            rows = (
                await db.execute(
                    select(
                        PhoneSnapshot.source,
                        PhoneSnapshot.content,
                        PhoneSnapshot.image_desc,
                        PhoneSnapshot.payload_json,
                        PhoneSnapshot.created_at,
                    )
                    .where(PhoneSnapshot.user_id == user_id)
                    .order_by(PhoneSnapshot.created_at.desc(), PhoneSnapshot.id.desc())
                    .limit(limit)
                )
            ).all()
    except Exception as e:
        _logger.warning("read_perception_records failed: user=%s err=%s", user_id, e)
        return []
    cutoff = now_naive_utc() - timedelta(minutes=max_age_minutes)
    seen: set[str] = set()
    records: list[PerceptionRecord] = []
    for source, content, image_desc, payload_json, created_at in rows:
        if created_at is not None and created_at.tzinfo is not None:  # 与旧渲染同口径：按 naive UTC 比较
            created_at = created_at.replace(tzinfo=None)
        if created_at is None or created_at < cutoff or source in seen:
            continue
        seen.add(source)
        records.append(PerceptionRecord(
            source=source,
            raw_text=content or "",
            image_desc=image_desc or "",
            structured=_parse_payload(payload_json),
            observed_at=created_at,
        ))
    return records
