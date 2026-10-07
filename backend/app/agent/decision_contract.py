"""Decision Contract（A28-②b，2026-10-08）：**只建结构 ＋ 影子读数，不接决策链**。

派单：`AMBRACE_paidan_A28b2b_decision_contract_20261007.md`。台账 A28 行对投影那一步的原话是
「做完就停，不许顺手往 Life/Scheduler 扩」，而 GPT 原文 §10/§11 的 Decision Contract ＋ Candidate Actions
越过了这条线 ⇒ 本模块**只登记"这一轮实际做了什么、为什么、受哪些约束"**，
不参与任何一次真实决策：不调 LLM、不选行为、不改执行、不写库（影子留痕除外，且默认关）。

三条硬边界（守卫 `test_decision_contract_a28b2b.py` 逐条钉）：

1. **纯函数**：`build_contract` / `enumerate_candidates` 只吃传进来的证据，不查库、不调模型、不 import 任何执行链；
2. **不接决策链**：全仓只有 `runtime` 一处影子接线点，且它在 `generate_response`/工具执行**之后**——
   契约是"复盘这一轮做了什么"，不是"决定下一轮做什么"；任何模块都不许读 `DecisionContract` 来决定行为；
3. **不留死结构**：每个字段都必须被影子读数消费。`confidence` 本期**恒 None**——
   现有系统里没有任何可复用的置信量（`perception`/`reflection`/`naturalness` 都不是这轮选行为的置信度），
   所以读数里专门统计它的覆盖率，而不是编一个数字进来。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

_logger = logging.getLogger("agent.decision_contract")

SHADOW_FLAG = "decision_contract_shadow"
SHADOW_ROUTE = "decision_contract"

# 派单指定的五套来源系统 → 行为名（**闭集词表**，本期不扩）。
# 它是"候选面"的定义，同时也是读数的分母：影子留痕会记 `total_actions`（词表全量）
# 与 `available_actions`（本轮真能选的数量），两者的差就是"这一轮的可选项有多窄"。
# 词表里某些族在 Runtime 回复链上永远不出现（如 Life/Proactivity）——那是事实，
# 由批 3 的通道带着自己的证据调用 `enumerate_candidates` 时才会出现，不是死代码。
SOURCE_ACTIONS: dict[str, tuple[str, ...]] = {
    "chat": ("reply",),
    "life": ("rest", "activity"),
    "proactivity": ("send_message",),
    "topic": ("continue_goal",),
    "tool": ("search", "recall", "execute_tool"),
}
TOTAL_ACTIONS = sum(len(v) for v in SOURCE_ACTIONS.values())


@dataclass
class CandidateAction:
    """一个可选行为：来自哪套系统、叫什么、这一轮凭什么可以选它。"""

    action: str
    source: str
    available: bool
    evidence: str = ""       # 判"可用"的依据（state 里真实存在的键）；不可用则留空


@dataclass
class DecisionContract:
    """这一轮的决定契约（派单 §1 的五个字段，一个不多）。"""

    intent: str | None = None            # 为什么要采取行为（复用既有感知/分类结论，不自造判断）
    action: str | None = None            # 决定采取什么行为（词表内的取值；没有任何可证行为时 None）
    reason: str | None = None            # 为什么选它（只由 state 里已有的痕迹拼，缺就 None）
    confidence: float | None = None      # 置信程度：本期恒 None（没有可复用来源，见模块头第 3 条）
    constraints: list = field(default_factory=list)   # 当前不能违反的条件（逐条带来源）
    candidates: list = field(default_factory=list)    # 候选面（枚举结果，只读）

    def to_dict(self) -> dict:
        return asdict(self)

    def coverage(self) -> dict:
        """字段读数：填了几项、哪几项恒空、confidence 是否有来源。"""
        filled = [k for k in ("intent", "action", "reason", "confidence") if getattr(self, k) is not None]
        if self.constraints:
            filled.append("constraints")
        if self.candidates:
            filled.append("candidates")
        return {
            "filled": filled,
            "missing": [k for k in ("intent", "action", "reason", "confidence",
                                    "constraints", "candidates") if k not in filled],
            "confidence_covered": self.confidence is not None,
            "candidate_count": len(self.candidates),
            "available_actions": sum(1 for c in self.candidates if c.get("available")),
            "total_actions": TOTAL_ACTIONS,
        }


def _evidence_from_state(state: dict, allow_tools: bool | None = None) -> dict[str, Any]:
    """把 state 里**真实存在**的痕迹翻成"某族可用的证据"，一条都不猜。

    注意 `active_topics`：现状是渲染后的模板槽文本（见 A28-②a 报告第三节），
    所以这里的证据只说明"有进行中话题"，不代表拿到了结构化话题列表。
    """
    tools_used = [str(t) for t in (state.get("tools_used") or []) if t]
    retrieved = state.get("retrieved_memories") or []
    ev: dict[str, Any] = {
        "chat": bool((state.get("ai_response") or "").strip()),
        "topic": bool(state.get("active_topics")),
        "recall": bool(retrieved),
        "search": any("search" in t.lower() for t in tools_used),
        "execute_tool": bool(tools_used) and (allow_tools is not False),
        # Life / Proactivity 两族在"回复链"上没有证据来源：由批 3 的通道自己带进来
        "life": False,
        "send_message": False,
    }
    return ev


def enumerate_candidates(evidence: dict[str, Any]) -> list[CandidateAction]:
    """按词表逐个行为判定"这一轮能不能选"——**只枚举，不排序、不选型、不执行**。"""
    out: list[CandidateAction] = []
    for source, names in SOURCE_ACTIONS.items():
        for name in names:
            if name == "recall":
                ok, why = bool(evidence.get("recall")), "state['retrieved_memories'] 非空"
            elif name == "search":
                ok, why = bool(evidence.get("search")), "tools_used 内含搜索类能力"
            elif name == "execute_tool":
                ok, why = bool(evidence.get("execute_tool")), "tools_used 非空且本轮允许工具"
            elif name == "continue_goal":
                ok, why = bool(evidence.get("topic")), "state['active_topics'] 非空（有进行中话题）"
            elif name == "reply":
                ok, why = bool(evidence.get("chat")), "生成了回复正文"
            elif name == "send_message":
                ok, why = bool(evidence.get("send_message")), "主动投放通道给出的证据"
            else:  # rest / activity
                ok, why = bool(evidence.get("life")), "Life 链给出的证据"
            out.append(CandidateAction(action=name, source=source, available=ok,
                                       evidence=why if ok else ""))
    return out


def resolve_action(state: dict, allow_tools: bool | None = None) -> tuple[str | None, str]:
    """本轮**实际**做了什么（复盘口径，不是选型）：调了工具就是 execute_tool，否则有正文就是 reply。"""
    tools_used = [str(t) for t in (state.get("tools_used") or []) if t]
    if tools_used and allow_tools is not False:
        if any("search" in t.lower() for t in tools_used):
            return "search", f"tools_used={tools_used}"
        return "execute_tool", f"tools_used={tools_used}"
    if (state.get("ai_response") or "").strip():
        return "reply", "state['ai_response'] 非空"
    return None, "本轮没有任何可证行为"


def collect_constraints(state: dict, allow_tools: bool | None = None) -> list[dict]:
    """当前生效的约束——**只登记 state 里能证实的**，每条带来源键。"""
    out: list[dict] = []
    if state.get("group_id") is not None:
        out.append({"key": "group_chat", "why": "state['group_id'] 非空：群聊场景，私有认知不外泄"})
    if state.get("skip_memory_save"):
        out.append({"key": "no_memory_save", "why": "state['skip_memory_save']：机器生成内容不落记忆"})
    if state.get("cognitive_loop_enabled") is False:
        out.append({"key": "cognitive_loop_off", "why": "state['cognitive_loop_enabled'] is False"})
    if allow_tools is False:
        out.append({"key": "tools_disabled", "why": "调用方给出 allow_tools=False（社交短回复不执行动作标记）"})
    if state.get("channel_hint"):
        out.append({"key": "external_channel", "why": "state['channel_hint'] 非空：外部渠道，格式受渠道约束"})
    return out


def build_contract(state: dict, *, allow_tools: bool | None = None) -> DecisionContract:
    """纯装配：把 state 里已有的痕迹写成契约结构（不查库、不调模型、不做任何决定）。"""
    state = state or {}
    intent = state.get("intent")
    perception = state.get("perception")
    if not intent and isinstance(perception, dict):
        intent = perception.get("intent")

    action, action_why = resolve_action(state, allow_tools)
    bits = []
    if state.get("plan_strategy"):
        bits.append(f"策略：{str(state['plan_strategy'])[:80]}")
    if state.get("emotional_state"):
        bits.append(f"情绪态：{str(state['emotional_state'])[:40]}")
    if isinstance(perception, dict) and perception.get("emotion"):
        bits.append(f"感知情绪：{str(perception['emotion'])[:40]}")
    reason = f"{action_why}；" + "；".join(bits) if (action and bits) else (action_why if action else None)

    candidates = enumerate_candidates(_evidence_from_state(state, allow_tools))
    return DecisionContract(
        intent=str(intent) if intent else None,
        action=action,
        reason=reason,
        confidence=None,                       # 见模块头第 3 条：没有可复用来源就不编
        constraints=collect_constraints(state, allow_tools),
        candidates=[asdict(c) for c in candidates],
    )


def shadow_payload(contract: DecisionContract) -> dict:
    """影子读数的一条记录：候选数、选中项、confidence 分布、constraints 命中。"""
    cov = contract.coverage()
    return {
        "kind": "decision_contract_shadow",
        "intent": contract.intent,
        "action": contract.action,
        "reason": contract.reason,
        "confidence": contract.confidence,          # 本期恒 None，分布读数就是 {None: n}
        "confidence_histogram": {str(contract.confidence): 1},
        "constraints": [c["key"] for c in contract.constraints],
        "candidate_actions_available": [c["action"] for c in contract.candidates if c["available"]],
        **cov,
    }


def shadow_record(state: dict, contract: DecisionContract) -> bool:
    """唯一消费方：默认关的影子留痕（复用既有 `agent_task_logs` 通道）。

    返回是否真的落了痕（关着时返回 False，测试据此断言"零行为"）。
    """
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        if not AGENT_FLAGS.get(SHADOW_FLAG, False):
            return False
    except Exception:
        return False
    try:
        from app.agent.trace import enqueue_task_log
        enqueue_task_log(
            character_id=state.get("character_id"),
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            trigger="agent",
            route=SHADOW_ROUTE,
            steps_json=json.dumps([shadow_payload(contract)], ensure_ascii=False),
            status="ok",
        )
        return True
    except Exception as e:  # 影子出错绝不影响主链路
        _logger.debug("Decision contract shadow failed: %s", e)
        return False


def shadow_enabled() -> bool:
    """影子闸（默认关）。读不到旗子一律按关处理。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(SHADOW_FLAG, False))
    except Exception:
        return False


def record_decision_contract(state: dict, *, allow_tools: bool | None = None) -> dict | None:
    """runtime 的唯一接线入口：**关着就直接退出**（不建结构、不写工作台、不落痕＝逐字节旧行为）。

    开时装配契约 → 挂进工作台 → 落一条影子留痕。整体 fail-open；
    返回值只给测试与影子用，**没有任何决策路径读它**。
    """
    if not shadow_enabled():
        return None
    try:
        contract = build_contract(state, allow_tools=allow_tools)
        ws = (state or {}).get("workspace")
        if ws is not None:
            # ②a 留的空字段 candidate_actions 终于有了真来源（仍只写不读）；
            # 契约本体另挂一个新字段，**不动 last_decision**（那是反思的落点，语义不同）。
            ws.decision_contract = contract.to_dict()
            if not getattr(ws, "candidate_actions", None):
                ws.candidate_actions = [c for c in contract.to_dict()["candidates"]]
        shadow_record(state, contract)
        return contract.to_dict()
    except Exception as e:
        _logger.debug("Decision contract record failed: %s", e)
        return None


__all__ = [
    "CandidateAction",
    "DecisionContract",
    "SHADOW_FLAG",
    "SHADOW_ROUTE",
    "SOURCE_ACTIONS",
    "TOTAL_ACTIONS",
    "build_contract",
    "collect_constraints",
    "enumerate_candidates",
    "record_decision_contract",
    "resolve_action",
    "shadow_enabled",
    "shadow_payload",
    "shadow_record",
]
