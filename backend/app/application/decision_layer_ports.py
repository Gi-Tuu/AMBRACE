"""决策层影子留痕 IO 端口的生产实现（架构地图断点 #1 · V2b，2026-09-29）。

原先写在 app/domain/decision/layer.py 函数体里的 IO 调用原样搬到此处，**全部保持函数级惰性
import**（与旧版同一位置）：既有测试对 ``app.agent.trace.enqueue_task_log`` /
``app.db.database.async_session_factory`` 的 monkeypatch 因此照旧命中。

唯一结构变化：旧 ``_write_batch`` 里「解析归属 user_id」与「批量 insert」共用同一个 db 会话
（``resolve_owner_user_id(cid, db=db)``），现在拆成两个端口方法各开一次会话。
resolve 只读 AICharacter（trace.py 内部本就自带「无 db 时自开会话」分支，失败返回 None 不抛），
insert 仍是一个会话一次 commit，读写之间无事务依赖 ⇒ 语义不变。

接线点（domain 不反向依赖本模块，由上层注入；未注入时 domain 侧惰性绑定本单例＝迁移期兼容钩子）：
- app/memory/ai_rating.py —— 挂点 A（异步评星，SINK_DIRECT）
- app/memory/format.py —— 挂点 B（同步格式化，SINK_BUFFER）
"""
from __future__ import annotations

import time
from typing import Any

from app.domain.decision.ports import ShadowRow


class ProductionDecisionLayerPorts:
    """DecisionShadowPorts 生产实现（原 layer.py 的 IO 代码，行为不变）。"""

    # ── 开关与时钟 ──

    def flags(self) -> dict[str, Any]:
        from app.agent.loop import AGENT_FLAGS
        return AGENT_FLAGS

    def perf_counter(self) -> float:
        return time.perf_counter()

    def monotonic(self) -> float:
        return time.monotonic()

    # ── 单行 fire-and-forget 通道（direct 形态） ──

    def new_task_id(self) -> str:
        from app.agent.trace import new_task_id
        return new_task_id()

    def enqueue_task_log(self, **row: Any) -> None:
        from app.agent.trace import enqueue_task_log
        enqueue_task_log(**row)

    # ── 批量落库通道（buffer 形态） ──

    def spawn_background(self, coro, *, name: str | None = None):
        from app.utils.async_tasks import spawn_background
        return spawn_background(coro, name=name)

    async def resolve_owner_user_id(self, character_id: int) -> int | None:
        from app.agent.trace import resolve_owner_user_id
        return await resolve_owner_user_id(character_id)

    async def write_shadow_rows(self, rows: list[ShadowRow]) -> None:
        from app.db.database import async_session_factory
        from app.models.agent import AgentTaskLog
        async with async_session_factory() as db:
            for kwargs in rows:
                db.add(AgentTaskLog(**kwargs))
            await db.commit()


production_decision_layer_ports = ProductionDecisionLayerPorts()
