"""Tool Registry（Phase A，2026-08-16）：统一工具注册表（ToolSpec）。

内置标记工具（search/image_gen/note_calendar/note_memo/timer/status_update）先登记，
本阶段只读登记（行为零变化：执行仍走现有 chat_service / promise_parser / response_parser 链路）；
Phase C 接通权限三档（Operit：全局默认 ALLOW/ASK/FORBID + 单工具例外）、频率/幂等门禁与工具生命周期钩子。
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


def sync_plugin_tools() -> int:
    """把已加载插件的 action 自动登记为 ToolSpec（Phase C，2026-08-16）。

    工具名 = f"{plugin}.{action}"；scope 按插件映射（browser/douyin/extension，与 permission_service._plugin_scope 一致）；
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
                risk_level=RISK_HIGH if "douyin" in name else RISK_MEDIUM,
                idempotent=False,
                scope=scope,
                plugin=name,
                plugin_action=action_name,
            )
            count += 1
    return count


def _register_builtin_tools() -> None:
    register_tool(ToolSpec(
        name="search",
        description="自主联网搜索（[SEARCH]查询[/SEARCH]）：Bing 中文优先+DDG 兜底，结果注入二次生成",
        action_type="SEARCH",
        risk_level=RISK_LOW,
        rate_limit="1/60s per user",
        idempotent=True,
        scope="browser",
        ask_auto_allow=True,  # 只读低风险：ask 不挂起，直接执行（forbid 仍拦截）
        epistemic_status="UNVERIFIED",  # 网络搜索结果未证实（Phase G Observation）
        provenance="web_search",
    ))
    register_tool(ToolSpec(
        name="image_gen",
        description="聊天内生图（[GEN_IMAGE]画面描述[/GEN_IMAGE] + [IMG_TEXT]图片消息文案[/IMG_TEXT]）",
        action_type="GEN_IMAGE",
        risk_level=RISK_MEDIUM,
        rate_limit="daily limit",
        idempotent=False,
        scope="image_gen",
    ))
    register_tool(ToolSpec(
        name="note_calendar",
        description="小手机日历备注（[CAL_NOTE]日期 内容[/CAL_NOTE]）：按日期落库、去重、角色署名",
        action_type="CAL_NOTE",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=True,
    ))
    register_tool(ToolSpec(
        name="note_memo",
        description="小手机备忘录（[MEMO]内容[/MEMO]）：落库、去重、角色署名",
        action_type="MEMO",
        risk_level=RISK_LOW,
        rate_limit="",
        idempotent=True,
    ))
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
