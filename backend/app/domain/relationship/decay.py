"""关系标量衰减（认知架构 v2.1）：长期不互动 → trust/attachment 缓慢下降。

进程内每日最多执行一次；由 arbiter.run_tick 调用（失败静默）。
互动加分（bump）随 Phase 3「主动/被动统一状态机」一起做。

架构地图断点 #1 · domain 去 IO 铺开（2026-09-29）：本模块只做「闲置天数 → 衰减步长 → 下限钳制」
的判定，不再直接 import DB / ORM 实体；读（character_states 全量）与写（trust/attachment 回写）
经 RelationshipDecayPorts（app/domain/relationship/ports.py）由上层注入，生产实现 =
app/application/relationship_decay_ports.production_relationship_decay_ports。
接线：scheduling/arbiter.py（run_tick 每日衰减）。未注入时抛 DecayPortsNotInjected，
既不 fail-open 也不 fail-closed，问题当场暴露（run_tick 原有 try/except 静默口径不变）。
"""
from datetime import datetime, timezone

from app.domain.relationship.ports import (
    DecayPortsNotInjected,
    RelationshipDecayPorts,
    StateDecayUpdate,
)
from app.utils.logger import get_logger

_logger = get_logger("scheduler.relationship_decay")

# 距最后一次互动超过 N 天才开始衰减；每多一天下降步长；下限
IDLE_DAYS_THRESHOLD = 1
DAILY_DECAY_STEP = 0.5
RELATION_MIN = 20

_last_run_date: str | None = None


def _resolve_ports(ports: RelationshipDecayPorts | None) -> RelationshipDecayPorts:
    """取端口实现：显式注入优先，否则清晰报错（本模块没有遗留钩子，调用方必须注入）。"""
    if ports is not None:
        return ports
    raise DecayPortsNotInjected(
        "relationship decay IO 端口未注入：请显式传入 RelationshipDecayPorts（生产实现 "
        "app.application.relationship_decay_ports.production_relationship_decay_ports）"
        "——架构地图断点 #1"
    )


async def run_relationship_decay(ports: RelationshipDecayPorts | None = None) -> None:
    """每日关系衰减（进程内节流，每天一次）"""
    global _last_run_date
    ports = _resolve_ports(ports)
    today = datetime.now(timezone.utc).date().isoformat()
    if _last_run_date == today:
        return
    _last_run_date = today
    try:
        states = await ports.fetch_character_states()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        changed = 0
        updates = []
        for st in states:
            last = st.last_activity_at
            if last is None:
                continue
            last = last.replace(tzinfo=None) if last.tzinfo else last
            idle_days = (now - last).total_seconds() / 86400.0
            if idle_days <= IDLE_DAYS_THRESHOLD:
                continue
            drop = int((idle_days - IDLE_DAYS_THRESHOLD) * DAILY_DECAY_STEP)
            if drop <= 0:
                continue
            new_trust = None
            new_attachment = None
            if (st.trust or 50) > RELATION_MIN:
                new_trust = max(RELATION_MIN, int((st.trust or 50) - drop))
                changed += 1
            if (st.attachment or 50) > RELATION_MIN:
                new_attachment = max(RELATION_MIN, int((st.attachment or 50) - drop))
                changed += 1
            if new_trust is not None or new_attachment is not None:
                updates.append(StateDecayUpdate(state_id=st.id, trust=new_trust,
                                                attachment=new_attachment))
        if changed:
            await ports.apply_decay(updates)
            _logger.info("Relationship decay applied: %d dims changed", changed)
    except Exception as e:
        _logger.warning("Relationship decay failed: %s", e)


# ── 本文件已去 IO（架构地图断点 #1 · domain 去 IO 铺开，2026-09-29）────────────
# 1. 新增纯类型层 app/domain/relationship/ports.py：RelationshipDecayPorts 协议
#    （typing.Protocol，无框架）+ CharacterStateView / StateDecayUpdate 快照 +
#    DecayPortsNotInjected；只依赖 dataclasses / datetime / typing。
# 2. 本模块去掉全部顶层 IO import：原 app.db.database.async_session_factory、
#    sqlalchemy.select、app.models.character.CharacterState —— 全部搬到
#    app/application/relationship_decay_ports.py（select(CharacterState) 全量读、
#    按主键取行后只赋值变化的列、最后一次性 commit，与会话形态逐字对齐旧实现）。
# 3. 阈值 IDLE_DAYS_THRESHOLD / DAILY_DECAY_STEP / RELATION_MIN、进程内每日节流
#    _last_run_date、判定顺序（last_activity_at 为空跳过 → naive 归一 → idle_days 阈值 →
#    drop<=0 跳过 → trust/attachment 分别按 or 50 兜底并钳到 RELATION_MIN）、changed 计数口径、
#    两条日志文案，全部逐字未动。
# 4. 与 care 样板的差异：本模块**没有**保留迁移期兼容钩子——既有测试
#    （test_tool_trace_governance / test_sources_registry / test_proactive_outreach）monkeypatch
#    的是公共函数 run_relationship_decay 本身（不是模块内部名），唯一生产调用方
#    scheduling/arbiter.py 已在白名单内完成注入接线，因此 _resolve_ports 只有
#    「显式注入 / 抛错」两态，直接达到 domain 零 IO import 终态。
#
# 同类待做 domain 文件清单（只列文件名，本批未改）：
# - app/domain/decision/layer.py（函数内 import app.db.database，1 处开会话）
# - app/domain/proactivity/pacing.py（函数内 import app.agent.loop.AGENT_FLAGS，跨层取开关）
