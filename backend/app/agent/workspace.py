"""Cognitive Workspace（A28-S3，2026-10-05）：本轮认知工作台——**纯运行时对象**。

要解决的问题（外部分析 10-05 §六／§七）：Chat / 主动 / Life / 群聊 各有各的"当前状态"
（`open_loops`、`working_state`、`current_state`、`prospective_intent`、`LifeState`、`CharacterState`、`Emotion`、
`Relationship`、`Thought`、`Context`……），没有任何一处统一表示「这一刻这个角色知道什么、在关注什么、想做什么」。
于是"认知"散在十个地方，长期跑会漂。

本模块是**第一阶段的最小承载**：只装已经得到的认知材料，不做决策、不取数。

三条硬边界（守卫 `test_cognitive_workspace_phase1.py` 逐条钉住，违反即红）：

1. **不落库、不查库、不调 LLM**——本文件的 import 只允许 `dataclasses`/`typing`；
2. **不控制 LangGraph**——节点自己写它，它不反过来调度图（第一阶段它甚至不被任何人读取）；
3. **只写不读**——第一阶段没有任何 prompt 段消费它，因此**输出逐字节不变**；等第二／三阶段再接投影。

字段按原文建议的 11 项起步，宁缺勿滥：不要往这里塞"大而全的 Cognition 对象"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 单轮工作台的容量上限：长链（工具再决策、多轮召回）不能把一轮的观测撑到无界。
# 溢出丢**最旧**，保留最近上下文——工作台是"此刻"的窗口，不是日志。
MAX_OBSERVATIONS = 40
MAX_CANDIDATE_ACTIONS = 20
MAX_CONSTRAINTS = 20


@dataclass
class CognitiveWorkspace:
    """本轮认知工作台。"""

    character_id: int | None = None
    user_id: int | None = None
    session_id: int | None = None
    identity: dict = field(default_factory=dict)            # 角色名/人设摘要等"我是谁"的快照
    focus: str | None = None                                # 当前关注（感知给出的话题/情绪）
    goal: str | None = None                                 # 当前目标（进行中目标，非计划系统）
    active_need: str | None = None                          # 当前需求（情绪照护/信息缺口等）
    observations: list = field(default_factory=list)        # Observation.to_dict() 的有序集合
    open_loops: list = field(default_factory=list)          # 未完成事项（已有 open_loop 体系的引用）
    active_topics: list = field(default_factory=list)       # 进行中话题（conversation_topics 引用）
    candidate_actions: list = field(default_factory=list)   # 候选行为（第一阶段只登记，不选型）
    constraints: list = field(default_factory=list)         # 约束（DND/时空纪律/权限等，字符串口径）
    last_decision: dict | None = None                       # 上一步决定（含理由，供审计）
    confidence: float | None = None                         # 当前置信度（0-1；没量出来就是 None）
    pending_commitments: list = field(default_factory=list)  # 答应过但还没做的事（timer/约定）

    # ── 写入（全部原地生效；节点通过 state["workspace"] 持有同一对象）──

    def add_observation(self, obs: dict) -> "CognitiveWorkspace":
        """挂一条观察记录（完整语义见 `app.agent.observation.Observation.to_dict()`）。"""
        if obs:
            self.observations.append(dict(obs))
            if len(self.observations) > MAX_OBSERVATIONS:
                del self.observations[: len(self.observations) - MAX_OBSERVATIONS]
        return self

    def set_focus(self, focus: str | None) -> "CognitiveWorkspace":
        self.focus = focus or None
        return self

    def set_goal(self, goal: str | None) -> "CognitiveWorkspace":
        self.goal = goal or None
        return self

    def add_candidate_action(self, action: dict) -> "CognitiveWorkspace":
        if action:
            self.candidate_actions.append(dict(action))
            if len(self.candidate_actions) > MAX_CANDIDATE_ACTIONS:
                del self.candidate_actions[: len(self.candidate_actions) - MAX_CANDIDATE_ACTIONS]
        return self

    def record_decision(self, decision: dict, confidence: float | None = None) -> "CognitiveWorkspace":
        """记下"上一步决定"。缺 `reason` 时补一个 None 占位键，让消费方不用猜形状。"""
        d = dict(decision or {})
        d.setdefault("reason", None)
        self.last_decision = d
        if confidence is not None:
            self.confidence = confidence
        return self

    def to_dict(self) -> dict:
        """快照（渲染/审计用）。**第一阶段不拼进 prompt**——真要投影是第二阶段的事。"""
        return {
            "character_id": self.character_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "identity": dict(self.identity),
            "focus": self.focus,
            "goal": self.goal,
            "active_need": self.active_need,
            "observations": [dict(o) for o in self.observations],
            "open_loops": list(self.open_loops),
            "active_topics": list(self.active_topics),
            "candidate_actions": [dict(a) for a in self.candidate_actions],
            "constraints": list(self.constraints),
            "last_decision": dict(self.last_decision) if self.last_decision else None,
            "confidence": self.confidence,
            "pending_commitments": list(self.pending_commitments),
        }


def create_workspace(*, character_id: int | None = None, user_id: int | None = None,
                     session_id: int | None = None, identity: dict | None = None) -> CognitiveWorkspace:
    """建一轮工作台。缺 caller 一律 None（与派单 F 的 fail-closed 口径一致，**不臆造 1 号账号**）。"""
    return CognitiveWorkspace(
        character_id=character_id,
        user_id=user_id,
        session_id=session_id,
        identity=dict(identity or {}),
    )


def add_observation(ws: CognitiveWorkspace | None, obs: dict) -> None:
    """模块级写入助手：`ws` 为 None（未开工作台的旧路径）时静默跳过，绝不抛。"""
    if ws is None:
        return
    try:
        ws.add_observation(obs)
    except Exception:  # 承载层出错不许影响主链路（与全仓 fire-and-forget 同口径）
        pass


def add_decision(ws: CognitiveWorkspace | None, decision: dict, confidence: float | None = None) -> None:
    """记「上一步决定」的 None 安全入口（口径同上）。"""
    if ws is None:
        return
    try:
        ws.record_decision(decision, confidence=confidence)
    except Exception:
        pass


def set_focus(ws: CognitiveWorkspace | None, focus: str | None) -> None:
    """记「当前关注」的 None 安全入口（口径同上）。"""
    if ws is None:
        return
    try:
        ws.set_focus(focus)
    except Exception:
        pass


__all__ = [
    "CognitiveWorkspace",
    "MAX_OBSERVATIONS",
    "add_decision",
    "add_observation",
    "create_workspace",
    "set_focus",
]
