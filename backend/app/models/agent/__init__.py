# -*- coding: utf-8 -*-
"""Agent 任务域：任务/任务日志/LLM 用量与额度/任务级 LLM 配置/情绪关怀任务/工具权限（F6 聚合，2026-08-31）。

原 agent/*.py 逐文件类定义已并入本模块（类体逐字节保留，节注释标注来源文件，
原文件 docstring 转注释保留）；__all__ 与 app/models/_all.py 导出名不变。历史路径兼容：
- app.models.agent.<file>（活跃路径的 2 行薄壳）重导出本模块名字；
- 顶层 app.models.<flat> 薄壳已重定向到本模块。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# ── task.py ──
# Agent 任务表（Phase H，2026-08-16）：任务级状态（断点续作/进度/结果），与 agent_task_logs（执行流水）分离
class AgentTask(Base):
    """单次 Agent 任务（复杂请求/主动行为）：goal/status/progress/result，可追踪可续作"""

    __tablename__ = "agent_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # 与 agent_task_logs.task_id 同源
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # chat_task / scheduler
    goal: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 目标（用户消息或行为类型）
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/running/done/failed
    progress_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 已执行步骤（AgentAction.to_step 列表）
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 结果摘要
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── task_log.py ──
# Agent Task Trace 表（Phase A，2026-08-16）：轻量任务追踪，先只写不读
class AgentTaskLog(Base):
    """单次 Agent 任务 trace（聊天/搜索/生图等调用处写入；成本归因第三批可复用）"""

    __tablename__ = "agent_task_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    character_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trigger: Mapped[str | None] = mapped_column(String(20), nullable=True)  # chat / chunked / image_gen / scheduler / plugin
    route: Mapped[str | None] = mapped_column(String(30), nullable=True)  # direct / search_loop / chunked
    steps_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 各步动作（AgentAction.to_step 列表）
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)  # 近似成本（本期留空，由 llm_usage 聚合）
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok / degraded / error
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

# ── llm_usage.py ──
# LLM token 用量统计与额度（2026-08-11：免费额度展示；总量仅主账号可设）
class LlmUsage(Base):
    """单次 LLM 调用用量（llm_client 落库，后台异步写入不阻塞主流程）"""
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None=服务器级/未知
    config_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 命中的 user_llm_configs.id（#68 P6）
    group_owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 家庭根账号（#68 P6 组聚合）
    task: Mapped[str | None] = mapped_column(String(30), nullable=True)  # 用途归因（审计 P1-07，2026-08-15）
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
class LlmUsageLimit(Base):
    """总额度设置（免费额度总量，单行 id=1；0=未设置）"""
    __tablename__ = "llm_usage_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    total_limit: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── task_llm_config.py ──
# 任务专用 LLM 配置：user_id=0=服务器级全局、>0=用户级；task 指定用途（记忆/卡片/情绪/状态/复习/主动消息/日记/时光）
# 与 api_configs（聊天主链路 chat 配置）并存：任务配置命中时优先于 chat 配置，实现"按任务指定模型"。
# api_key 支持多 Key 池（逗号/换行分隔或 JSON 数组），llm_client 按请求轮换。
class TaskLlmConfig(Base):
    __tablename__ = "task_llm_configs"
    __table_args__ = (UniqueConstraint("user_id", "task", name="uq_task_llm_user_task"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, default=0)  # 0=服务器级全局哨兵
    task: Mapped[str] = mapped_column(String(20), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    api_key: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 支持多 Key（逗号/JSON 数组）
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

# ── emotion_care_task.py ──
# AI 情绪关怀任务表（用户低落 → 角色延迟主动关心）
class EmotionCareTask(Base):
    __tablename__ = "emotion_care_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    trigger_msg: Mapped[str] = mapped_column(String(500), default="")
    due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

# ── tool_permission.py ──
# AI 能力权限模型：用户级三档权限（allow/ask/forbid）+ 待确认动作表（2026-08-12）
#
# 设计参考 Operit 工具权限模型：全局默认档位 + 每能力例外，实际权限 = 例外优先。
class ToolPermission(Base):
    """用户级 AI 能力权限（scope="__global__" 表示全局默认档位）"""
    __tablename__ = "tool_permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)  # image_gen / image_understand / tts / asr / browser / douyin / extension / __global__
    level: Mapped[str] = mapped_column(String(10), nullable=False, default="allow")  # allow / ask / forbid
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "scope", name="uq_user_scope"),
    )
class PendingPermissionAction(Base):
    """AI 主动调用能力的待确认动作（权限=ask 时挂起，用户确认后执行）"""
    __tablename__ = "pending_permission_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    character_id: Mapped[int] = mapped_column(Integer, ForeignKey("ai_characters.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 动作载荷（如生图 prompt）
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")  # pending / approved / denied / expired
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
__all__ = [
    "AgentTask",
    "AgentTaskLog",
    "LlmUsage",
    "LlmUsageLimit",
    "TaskLlmConfig",
    "EmotionCareTask",
    "ToolPermission",
    "PendingPermissionAction",
]
