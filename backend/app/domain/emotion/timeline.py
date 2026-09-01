"""状态情绪记忆时间线服务：三源（情绪记忆/状态触发日志/剧情线事件）合并只读，纯查询零 LLM"""
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.database import async_session_factory
from app.models.memory import Memory
from app.models.state_trigger_log import StateTriggerLog
from app.models.storyline_event import StorylineEvent
from app.utils.logger import get_logger

_logger = get_logger("services.emotion_timeline")

_DIM_CN = {
    "mood": "心情", "body_temp": "体温", "desire": "性欲", "possessiveness": "占有欲",
    "fatigue": "疲惫感", "sensitivity": "敏感度", "comfort": "舒适感", "anger": "怒气值",
}
_CN_TO_KEY = {cn: key for key, cn in _DIM_CN.items()}

# 状态触发规则 key -> 情绪标签（与 scheduler/state_triggers.py RULES 对齐）
_TRIGGER_LABELS = {
    "anger_high": "生气", "mood_low": "低落", "fatigue_high": "疲惫",
    "desire_high": "亲密冲动", "anger_mood_low": "冷战/爆发",
    "fatigue_mood_low": "又累又难过", "desire_body_temp": "冲动亲密",
    "possessiveness_desire": "吃醋/占有",
}

# 剧情线 key -> 剧情名；cold_war 节点名与 storyline_engine 对齐，其他剧情用通用节点名
_STORYLINE_LABELS = {"cold_war": "冷战", "jealousy": "吃醋", "fatigue": "疲惫"}
_COLD_WAR_NODES = ["爆发", "冷战", "加时", "深夜emo", "破冰", "和好后遗症"]
_GENERIC_NODES = ["启动", "发展", "高潮", "缓和", "破冰", "收尾"]

# 情绪记忆 content 维度变化："心情降到45、怒气升到62"
_EMO_CHANGE_RE = re.compile(r"(心情|体温|性欲|占有欲|疲惫感|敏感度|舒适感|怒气值)(?:升到|降到|升至|降至)(\d+)")
# 状态触发快照："心情=45；怒气值=80"
_SNAPSHOT_RE = re.compile(r"(心情|体温|性欲|占有欲|疲惫感|敏感度|舒适感|怒气值)=(\d+)")


def _parse_emotion_changes(content: str) -> list[dict]:
    """从情绪记忆原文解析维度变化（记忆未存旧值，from/delta 为 None）；失败降级为空列表"""
    out = []
    for m in _EMO_CHANGE_RE.finditer(content or ""):
        cn, val = m.group(1), int(m.group(2))
        key = _CN_TO_KEY.get(cn, cn)
        out.append({"key": key, "cn": cn, "from": None, "to": val, "delta": None})
        if len(out) >= 3:
            break
    return out


def _parse_snapshot_changes(value: str) -> list[dict]:
    """从状态触发日志的八维快照解析，取偏离 50 最大的前 3 维（无历史旧值）"""
    parsed = []
    for m in _SNAPSHOT_RE.finditer(value or ""):
        cn, val = m.group(1), int(m.group(2))
        parsed.append({"key": _CN_TO_KEY.get(cn, cn), "cn": cn, "from": None, "to": val,
                       "delta": None, "_dev": abs(val - 50)})
    parsed.sort(key=lambda x: x["_dev"], reverse=True)
    for item in parsed:
        item.pop("_dev", None)
    return parsed[:3]


def _emotion_label(changes: list[dict]) -> str:
    """维度变化 → 情绪标签（优先高优先级映射）"""
    for c in changes:
        key, to = c["key"], c["to"]
        if key == "mood" and to is not None and to <= 35:
            return "低落"
        if key == "anger" and to is not None and to >= 60:
            return "生气"
        if key == "fatigue" and to is not None and to >= 60:
            return "疲惫"
        if key == "desire" and to is not None and to >= 60:
            return "亲密波动"
        if key == "possessiveness" and to is not None and to >= 60:
            return "吃醋"
        if key == "body_temp" and to is not None and to >= 60:
            return "兴奋"
        if key == "comfort" and to is not None and to <= 40:
            return "不安"
        if key == "sensitivity" and to is not None and to >= 60:
            return "多想"
    return "情绪波动"


def _storyline_node_name(key: str, node_index: int) -> str:
    names = _COLD_WAR_NODES if key == "cold_war" else _GENERIC_NODES
    if 0 <= node_index < len(names):
        return names[node_index]
    return f"节点{node_index}"


def _beijing_period(at: datetime) -> str:
    """北京时间时段标签（早/下午/晚上/深夜）"""
    bj_hour = (at + timedelta(hours=8)).hour
    if 5 <= bj_hour < 11:
        return "上午"
    if 11 <= bj_hour < 17:
        return "下午"
    if 17 <= bj_hour < 23:
        return "晚上"
    return "深夜"


async def get_emotion_timeline(character_id: int, days: int = 7, dimension: str | None = None) -> dict:
    """三来源合并时间线 + 纯程序化概览（零 LLM）。dimension=维度 key（mood/anger/...）按事件含该维度过滤"""
    days = max(1, min(int(days or 7), 90))
    start = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    events = []

    # 来源 1：情绪事件记忆（sub_type=emotion）
    try:
        async with async_session_factory() as db:
            from app.memory.service import _active_status_clause  # #70-C：仅 active（flag 关=永真）
            mems = (await db.execute(
                select(Memory).where(
                    Memory.character_id == character_id,
                    Memory.sub_type == "emotion",
                    Memory.is_archived == False,
                    Memory.created_at >= start,
                    _active_status_clause(),
                ).order_by(Memory.created_at.desc())
            )).scalars().all()
        for m in mems:
            changes = _parse_emotion_changes(m.content or "")
            events.append({
                "id": m.id, "source": "emotion", "source_id": m.id,
                "at": m.created_at, "label": _emotion_label(changes),
                "dim_changes": changes, "content": (m.content or "")[:300],
            })
    except Exception as e:
        _logger.warning("Emotion timeline memory query failed: %s", e)

    # 来源 2：状态触发日志（含恢复状态）
    try:
        async with async_session_factory() as db:
            logs = (await db.execute(
                select(StateTriggerLog).where(
                    StateTriggerLog.character_id == character_id,
                    StateTriggerLog.created_at >= start,
                ).order_by(StateTriggerLog.created_at.desc())
            )).scalars().all()
        for lg in logs:
            label = _TRIGGER_LABELS.get(lg.trigger_key, lg.trigger_key)
            if lg.recovered:
                label += "（已恢复）"
            content = lg.value or ""
            events.append({
                "id": lg.id, "source": "state_trigger", "source_id": lg.id,
                "at": lg.created_at, "label": f"状态触发 · {label}",
                "dim_changes": _parse_snapshot_changes(content), "content": content[:300],
            })
    except Exception as e:
        _logger.warning("Emotion timeline trigger query failed: %s", e)

    # 来源 3：剧情线事件
    try:
        async with async_session_factory() as db:
            st_events = (await db.execute(
                select(StorylineEvent).where(
                    StorylineEvent.character_id == character_id,
                    StorylineEvent.created_at >= start,
                ).order_by(StorylineEvent.created_at.desc())
            )).scalars().all()
        for se in st_events:
            skey = se.storyline_key or "storyline"
            label = f"剧情 · {_STORYLINE_LABELS.get(skey, skey)}（{_storyline_node_name(skey, se.node_index or 0)}）"
            content = se.output_text or se.user_context or se.trigger_source or ""
            events.append({
                "id": se.id, "source": "storyline", "source_id": se.id,
                "at": se.created_at, "label": label,
                "dim_changes": [], "content": content[:300],
            })
    except Exception as e:
        _logger.warning("Emotion timeline storyline query failed: %s", e)

    # dimension 过滤（事件含该维度变化才显示）
    if dimension:
        events = [e for e in events if any(c["key"] == dimension for c in e["dim_changes"])]

    # 按时间倒序
    events.sort(key=lambda e: e["at"], reverse=True)

    # 概览（纯程序化）
    emotion_count = sum(1 for e in events if e["source"] == "emotion")
    trigger_count = sum(1 for e in events if e["source"] == "state_trigger")
    storyline_count = sum(1 for e in events if e["source"] == "storyline")
    total = len(events)

    periods = {}
    for e in events:
        p = _beijing_period(e["at"])
        periods[p] = periods.get(p, 0) + 1
    top_period = max(periods, key=periods.get) if periods else ""

    top_dim = None
    top_dev = -1
    for e in events:
        for c in e["dim_changes"]:
            dev = abs((c.get("to") or 50) - 50)
            if dev > top_dev:
                top_dev = dev
                top_dim = c["cn"]
    summary_parts = [f"近{days}天共{total}次情绪波动"]
    if emotion_count or trigger_count or storyline_count:
        summary_parts.append(f"（对话评估{emotion_count}次、状态触发{trigger_count}次、剧情{storyline_count}次）")
    if top_period:
        summary_parts.append(f"多发生在{top_period}")
    if top_dim:
        summary_parts.append(f"{top_dim}波动最明显")
    summary = {
        "total": total, "emotion_count": emotion_count,
        "trigger_count": trigger_count, "storyline_count": storyline_count,
        "top_period": top_period, "top_dimension": top_dim,
        "text": "，".join(summary_parts) + "。" if summary_parts else "近几天还没有情绪记录，多和 TA 聊聊会自动记录在这里。",
    }

    return {
        "character_id": character_id,
        "days": days,
        "events": [{**e, "at": e["at"].isoformat()} for e in events],
        "summary": summary,
    }
