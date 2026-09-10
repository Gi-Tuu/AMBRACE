"""事件结构化 Schema（World & Cognition P0，2026-08-15）

在现有轻量 EventBus（publish(event_type, payload)）之上定义标准事件字段，
让每个事件可追溯来源、明确说话人/可见范围/认知状态：

  speaker:  {type, id}        说话人/行动者（user/character/npc/system/tool）
  target:   {type, id}        事件目标（对谁说/对谁做）
  audience: [id...]           可见范围（角色 id 列表；不在此列不可见）
  provenance: {origin, confidence, source_event_id, tool_call_id}  来源追溯
  epistemic_status: FACT/INFERRED/PLANNED/FICTIONAL/UNVERIFIED     认知状态

规则：所有重要事件必须带 speaker；无 speaker 的事件拒绝写入（校验函数）。
来源 → epistemic_status 自动映射（FACT 类：user_message/system_event/tool_result/life_event；
INFERRED 类：inference；PLANNED 类：goal/schedule；FICTIONAL 类：story_event）。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

# 认知状态（vnew3.0 收敛为 5 种）
EPISTEMIC_FACT = "FACT"
EPISTEMIC_INFERRED = "INFERRED"
EPISTEMIC_PLANNED = "PLANNED"
EPISTEMIC_FICTIONAL = "FICTIONAL"
EPISTEMIC_UNVERIFIED = "UNVERIFIED"
EPISTEMIC_VALUES = (EPISTEMIC_FACT, EPISTEMIC_INFERRED, EPISTEMIC_PLANNED,
                    EPISTEMIC_FICTIONAL, EPISTEMIC_UNVERIFIED)

# 来源类型 → (默认 epistemic_status, 默认 confidence)
PROVENANCE_META: dict[str, tuple[str, float]] = {
    "user_message": (EPISTEMIC_FACT, 1.0),
    "system_event": (EPISTEMIC_FACT, 1.0),
    "tool_result": (EPISTEMIC_FACT, 0.95),
    "life_event": (EPISTEMIC_FACT, 0.9),
    "social_event": (EPISTEMIC_FACT, 0.85),
    "life_loop": (EPISTEMIC_FACT, 0.9),
    "ai_message": (EPISTEMIC_INFERRED, 0.6),
    "inference": (EPISTEMIC_INFERRED, 0.4),
    "goal_schedule": (EPISTEMIC_PLANNED, 0.9),
    "story_event": (EPISTEMIC_FICTIONAL, 0.2),
}

VALID_ORIGINS = set(PROVENANCE_META)


def make_event(
    event_type: str,
    *,
    speaker: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    audience: list[Any] | None = None,
    provenance: dict[str, Any] | None = None,
    epistemic_status: str | None = None,
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造标准事件 dict（自动补齐 provenance/epistemic/时间/id）。"""
    origin = (provenance or {}).get("origin") or ""
    if origin and origin not in VALID_ORIGINS:
        raise ValueError(f"unknown provenance origin: {origin}")
    if origin:
        default_status, default_conf = PROVENANCE_META[origin]
        prov = dict(provenance or {})
        prov.setdefault("origin", origin)
        prov.setdefault("confidence", default_conf)
        status = epistemic_status or default_status
    else:
        prov = dict(provenance or {})
        status = epistemic_status or EPISTEMIC_UNVERIFIED
    if status not in EPISTEMIC_VALUES:
        raise ValueError(f"unknown epistemic_status: {status}")
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": event_type,
        "timestamp": time.time(),
        "speaker": speaker,
        "target": target,
        "audience": audience or [],
        "provenance": prov,
        "epistemic_status": status,
        "data": data or {},
        "metadata": metadata or {},
    }


def require_speaker(event: dict[str, Any]) -> None:
    """校验：重要事件必须带 speaker。缺失抛 ValueError（由发布方决定是否捕获）。"""
    sp = event.get("speaker")
    if not sp or not sp.get("type") or not sp.get("id"):
        raise ValueError(f"event missing speaker: {event.get('type')}")


def speaker_of(sender_type: str, sender_id: int | None) -> dict[str, Any] | None:
    """由消息 sender 快速构造 speaker 字段；sender_id 为空返回 None。"""
    if not sender_id:
        return None
    if sender_type == "user":
        return {"type": "user", "id": sender_id}
    if sender_type == "ai":
        return {"type": "character", "id": sender_id}
    return {"type": sender_type, "id": sender_id}


def can_see(character_id: Any, event: dict[str, Any]) -> bool:
    """可见性判定（World & Cognition P2，2026-08-15）。

    - audience 含 "public" → 所有人可见
    - audience 含该角色 id → 可见
    - 其他 → 不可见（角色 B 看不到只属于 A 的私聊事件）
    """
    audience = event.get("audience") or []
    if "public" in audience:
        return True
    return str(character_id) in {str(a) for a in audience}


def private_event(character_id: Any, user_id: Any, **kwargs: Any) -> dict[str, Any]:
    """构造私聊事件：audience = [用户, 该角色]，仅双方可见（用户私聊 A 的事 B 不可见）。"""
    kwargs.setdefault("audience", [user_id, character_id])
    kwargs.setdefault("target", {"type": "user", "id": user_id})
    return make_event(**kwargs)
