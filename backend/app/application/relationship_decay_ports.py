"""关系标量衰减 IO 端口的生产实现（架构地图断点 #1 · domain 去 IO 铺开，2026-09-29）。

原先写在 app/domain/relationship/decay.py 里的读写原样搬到此处：
- 读：`select(CharacterState)` 全量加载 + `.scalars().all()`，逐字保留（含审计标注的「全表加载」
  口径，本单不改行为）；投影为 CharacterStateView 只读快照。
- 写：与旧实现同一个会话、最后一次 `commit()`（旧形态是在同一 session 里改 ORM 对象再提交，
  这里按主键取回同一批 ORM 对象、只赋值判定为变化的那一列，再统一提交），
  `updated_at` 的 onupdate 触发行为因此与改动前完全一致。

接线点（domain 不反向依赖本模块，由上层注入）：
- app/scheduling/arbiter.py —— run_tick 每日关系衰减
"""
from sqlalchemy import select

from app.db.database import async_session_factory
from app.domain.relationship.ports import CharacterStateView, StateDecayUpdate
from app.models.character import CharacterState


class ProductionRelationshipDecayPorts:
    """RelationshipDecayPorts 生产实现（原 decay.py 的读写，行为不变）。"""

    async def fetch_character_states(self) -> list[CharacterStateView]:
        async with async_session_factory() as db:
            states = (await db.execute(select(CharacterState))).scalars().all()
        return [CharacterStateView(id=st.id, last_activity_at=st.last_activity_at,
                                   trust=st.trust, attachment=st.attachment) for st in states]

    async def apply_decay(self, updates: list[StateDecayUpdate]) -> None:
        if not updates:
            return
        async with async_session_factory() as db:
            for u in updates:
                st = await db.get(CharacterState, u.state_id)
                if st is None:
                    continue
                if u.trust is not None:
                    st.trust = u.trust
                if u.attachment is not None:
                    st.attachment = u.attachment
            await db.commit()


production_relationship_decay_ports = ProductionRelationshipDecayPorts()
