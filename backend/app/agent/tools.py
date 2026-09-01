"""Tool Registry（Phase A，2026-08-16）：统一工具注册表（ToolSpec）。

AMBRACE 重构步骤 8：4 个内置工具（search/image_gen/note_calendar/note_memo）的**执行入口**
改由 app/tools/builtin/*.py 注册（execute 内惰性 import services），本文件只保留内部 AI 行为工具
（timer/status_update/memory_extract/memory_fact_check/emotion_care/weave_card/memory_summary）。
权限三档（Operit：全局默认 ALLOW/ASK/FORBID + 单工具例外）、频率/幂等门禁与工具生命周期钩子
由 tool_runner 统一执行。
"""
from dataclasses import dataclass
from typing import Any, Callable

# 风险等级 / 权限档（Phase C 使用；本期仅登记）
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

PERMISSION_ALLOW = "ALLOW"
PERMISSION_ASK = "ASK"
PERMISSION_FORBID = "FORBID"


@dataclass
class ToolSpec:
    """工具规格：名称/说明/风险/频率/幂等/权限 scope/执行入口（Phase C：与权限三档 + 插件 action 统一）"""

    name: str
    description: str
    action_type: str | None = None  # 对应 AgentAction.action_type
    risk_level: str = RISK_LOW
    rate_limit: str = ""  # 如 "1/60s per user"、"daily limit"
    idempotent: bool = False  # True=只读/可去重（失败自动重试 1 次 / 24h 幂等键去重）
    scope: str | None = None  # 对应 permission_service scope；None=本地能力无权限门禁
    plugin: str | None = None  # 插件名（插件 action 工具）
    plugin_action: str | None = None  # 插件 action 名
    execute: Callable[..., Any] | None = None  # 执行入口；插件工具由 ToolRunner 按 plugin+plugin_action 调用
    enabled: bool = True  # 独立开关（与权限配置并存；默认开）
    ask_auto_allow: bool = False  # 只读低风险工具：权限 ask 时不挂起询问，直接放行（如 AI 自主搜索）
    epistemic_status: str = "FACT"  # Observation 标注（Phase G）：FACT / INFERRED / UNVERIFIED（对齐世界认知）
    provenance: str = "tool"  # Observation 来源标识（如 web_search / image_gen / note）
    input_schema: dict | None = None  # MCP 工具入参 schema（工具声明/校验用；本地工具为 None）
    server_id: int | None = None  # MCP Server 归属（mcp.{server}.{tool} 命名空间工具的反查）
    max_observation_chars: int = 120  # P2-B（2026-08-29）：Observation summary 截断上限（MCP 工具设为 4000）

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "action_type": self.action_type,
            "risk_level": self.risk_level,
            "rate_limit": self.rate_limit,
            "idempotent": self.idempotent,
            "scope": self.scope,
            "plugin": self.plugin,
            "plugin_action": self.plugin_action,
            "enabled": self.enabled,
        }


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(spec: ToolSpec) -> ToolSpec:
    """登记工具（重复登记覆盖）"""
    _REGISTRY[spec.name] = spec
    return spec


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def unregister_tool(name: str) -> None:
    """注销工具（MCP 断开/删除时用）：移除登记表条目。不存在时静默。"""
    _REGISTRY.pop(name, None)


def get_tool_by_action(action_type: str) -> ToolSpec | None:
    """按 AgentAction.action_type 反查工具"""
    for spec in _REGISTRY.values():
        if spec.action_type == action_type:
            return spec
    return None


def list_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def _plugin_risk_level(plugin_name: str) -> str:
    """插件 action 风险档（X5）：注册渠道上报 risk_level（meta），未注册渠道默认 MEDIUM"""
    try:
        from app.providers.channel import channel_for_plugin
        hit = channel_for_plugin(plugin_name)
        if hit is not None and str((hit[1] or {}).get("risk_level") or "").lower() == "high":
            return RISK_HIGH
    except Exception:
        pass
    return RISK_MEDIUM


def sync_plugin_tools() -> int:
    """把已加载插件的 action 自动登记为 ToolSpec（Phase C，2026-08-16）。

    工具名 = f"{plugin}.{action}"；scope 按插件映射（browser/渠道注册/extension，与 permission_service._plugin_scope 一致）；
    执行入口由 ToolRunner 按 plugin+plugin_action 调 registry.run_plugin_action（与现有行为一致）。
    返回登记数。
    """
    try:
        from app.plugins import registry as _registry
    except Exception:
        return 0
    count = 0
    for name, entry in list(_registry._loaded.items()):
        actions_map = (entry or {}).get("actions") or {}
        if not actions_map:
            continue
        try:
            from app.services import permission_service
            scope = permission_service._plugin_scope(name)
        except Exception:
            scope = None
        for action_name in actions_map.keys():
            tool_name = f"{name}.{action_name}"
            _REGISTRY[tool_name] = ToolSpec(
                name=tool_name,
                description=f"插件 {name} 的 action：{action_name}",
                risk_level=_plugin_risk_level(name),
                idempotent=False,
                scope=scope,
                plugin=name,
                plugin_action=action_name,
            )
            count += 1
    return count


def _register_builtin_tools() -> None:
    # 4 个内置工具（search/image_gen/note_calendar/note_memo）的执行入口改由
    # app/tools/builtin/*.py 注册（AMBRACE 步骤 8），此处不再登记，避免重复/占位登记。
    register_tool(ToolSpec(
        name="timer",
        description="定时承诺（[timer:20m] / 口头时长承诺）：到点 AI 主动跟进",
        action_type="TIMER",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=False,
    ))
    register_tool(ToolSpec(
        name="status_update",
        description="状态更新（【状态更新：…】）：气泡下方小字展示，同时落角色状态",
        action_type="STATUS_UPDATE",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=True,
    ))
    # ── 内部 AI 行为工具（P0-1b，2026-08-16）──
    # 系统内部行为（非用户可见）：scope=None 无权限门禁；统一生命周期/tool.executed 事件/异常隔离
    register_tool(ToolSpec(
        name="memory_extract",
        description="记忆提炼：从对话提取用户信息/事件沉淀记忆（批量节流）",
        risk_level=RISK_LOW,
        rate_limit="30min/batch per char",
        idempotent=True,
        provenance="memory_extract",
    ))
    register_tool(ToolSpec(
        name="memory_fact_check",
        description="记忆一致性核查：AI 回复与已知记忆矛盾检测并降级",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance="memory_fact_check",
    ))
    register_tool(ToolSpec(
        name="emotion_care",
        description="情绪关怀：检测用户低落后生成延迟关心消息",
        risk_level=RISK_LOW,
        idempotent=False,
        provenance="emotion_care",
    ))
    register_tool(ToolSpec(
        name="weave_card",
        description="织库卡片生成：记忆聚类生成全景卡片（content_hash 幂等）",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance="weave_card",
    ))
    register_tool(ToolSpec(
        name="memory_summary",
        description="记忆总结/复习摘要生成",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance="memory_summary",
    ))


_register_builtin_tools()
