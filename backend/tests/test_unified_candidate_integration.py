"""统一候选整合合同（2026-10-25；2026-10-25 窄返工按主审收窄口径）。

**证据边界（先说清楚，避免夸大）**：本文件测的是**内核函数合同**，不是公共链路端到端：
1. `_apply_system_total_quota`：A28/A42 投影让位与 user 末位不变式（内核）；
2. `chat_time._from_snapshot`：锚点筛选内核（严格更早、(created_at,id) 语义）；
3. `plan_completion.record_assertion`：证据重放**内核**幂等（**不是** queue ack 验证）。

公共入口/worker 侧的真实证据来自既有已覆盖这些链路的用例（由 INTEGRATION_EVIDENCE.md 逐条列出
命令与 JUnit），本文件不重复堆用例、也不把内核单测写成公共链路已验。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.agent.context import chat_time as ct
from app.agent import context_builder as cb
from app.memory.plan_completion import (
    PlanScope, bind_explicit, item_is_completed, record_assertion, register_item)
from app.models.memory import PlanCompletionEvidence

from _plan_completion_fixture_c9b import c9b_world as c9b_world  # noqa: F401  (fixture)

PROJECTION_HEADER = "## 本轮结构化状态"
TARGET = "设备C的绝缘质检"        # 必须同时是源消息与 claim 的字面子串
CLAIM = "设备C的绝缘质检已完成"
SCOPE = PlanScope(101, 201, 301)


# ---------------------------------------------------------------- A28/A42 + 预算
def test_内核_投影让位先于核心块且user末位不变(monkeypatch):
    """投影块（优先级 5）先让出总预算；人设块仍保留，user 消息逐字且仍在最后。"""
    persona = "【人设】" + "甲" * 400
    projection = PROJECTION_HEADER + "\n" + "乙" * 400
    messages = [
        {"role": "system", "content": persona},
        {"role": "system", "content": projection},
        {"role": "user", "content": "我现在在质检台"},
    ]
    monkeypatch.setattr(cb, "TOTAL_SYSTEM_QUOTA_TOKENS", 220)
    cb._apply_system_total_quota(messages)

    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    # 投影先让位：要么整块消失，要么被裁到不含原长文本
    assert len(system_text) < len(persona) + len(projection), "总预算必须真的裁掉了内容"
    assert system_text.count("乙") < 400, "投影块必须先被让位"
    # 人设块优先级更高（未识别块=3 < 5），不应先于投影被清空
    assert "【人设】" in system_text
    # 本轮 user 消息逐字保留且仍是最后一条
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "我现在在质检台"


# ------------------------------------------------------------------------ C7
def test_内核_锚点只取严格更早快照():
    """锚点取入口快照里严格早于当前轮的那条；同秒也按 (created_at,id) 判定，不回查当前 ROWID。"""
    reference = datetime(2026, 10, 25, 12, 0, 0)
    state = {
        "chat_time_reference_at": reference,
        "chat_time_anchor_snapshot": {
            "status": ct.STATUS_OK,
            "created_at": reference - timedelta(days=1),
            "message_id": 7,
        },
    }
    anchor = ct._from_snapshot(state)
    assert anchor.status == ct.STATUS_OK
    assert anchor.previous_at == reference - timedelta(days=1)
    assert ct.format_gap_text(anchor) is not None

    # 同一秒、id 更小：仍算「更早的一条」，不得把当前消息当成历史
    same_second = {
        "chat_time_reference_at": reference,
        "chat_time_anchor_snapshot": {
            "status": ct.STATUS_OK,
            "created_at": reference,
            "message_id": 6,
        },
    }
    anchor2 = ct._from_snapshot(same_second)
    assert anchor2.status == ct.STATUS_OK
    assert anchor2.previous_at == reference, "同秒仍应落成\"刚刚\"，而不是被当成本轮"
    gap2 = ct.format_gap_text(anchor2)
    assert gap2 is not None and "天" not in gap2

    # 无历史：不虚构任何间隔
    empty = {"chat_time_reference_at": reference,
             "chat_time_anchor_snapshot": {"status": ct.STATUS_NO_HISTORY}}
    anchor3 = ct._from_snapshot(empty)
    assert anchor3.status == ct.STATUS_NO_HISTORY
    assert ct.format_gap_text(anchor3) is None


# ------------------------------------------------------------------------ C9
def test_内核_同一断言重放幂等非queue_ack(c9b_world):
    """同一 (scope, source, claim) 的断言重放不得产生第二条证据，也不得重复结算。"""

    async def run():
        source = await c9b_world.message("计划：把设备C的绝缘质检做完")
        signal = await c9b_world.message("设备C的绝缘质检已完成")
        async with c9b_world.factory() as db:
            item = await register_item(db, SCOPE, source, TARGET)
            first = await record_assertion(db, SCOPE, signal, TARGET, CLAIM)
            assert await bind_explicit(db, first, item)
            again = await record_assertion(db, SCOPE, signal, TARGET, CLAIM)
            assert await bind_explicit(db, again, item)
            rows = (await db.execute(
                select(func.count()).select_from(PlanCompletionEvidence))).scalar_one()
            completed = await item_is_completed(db, item)
        assert first.id == again.id, "同一断言重放必须命中同一条证据（幂等键）"
        assert rows == 1, f"重复 ack 不得新增证据行，实际 {rows}"
        assert completed is True

    asyncio.run(run())
