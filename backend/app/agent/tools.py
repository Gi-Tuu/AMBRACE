"""Tool Registry（Phase A，2026-08-16）：统一工具注册表（ToolSpec）。

AMBRACE 重构步骤 8：5 个内置工具（search/image_gen/note_calendar/note_memo/note_done）的**执行入口**
改由 app/tools/builtin/*.py 注册（execute 内惰性 import services），本文件只保留内部 AI 行为工具
（timer/status_update/memory_extract/memory_fact_check/emotion_care/weave_card/memory_summary）。
权限三档（Operit：全局默认 ALLOW/ASK/FORBID + 单工具例外）、频率/幂等门禁与工具生命周期钩子
由 tool_runner 统一执行。
"""
from dataclasses import dataclass
import re
from typing import Any, Callable

from app.actors import (
    EPISTEMIC_UNVERIFIED,
    OBS_PROVENANCE_EMOTION_CARE,
    OBS_PROVENANCE_MEMORY_EXTRACT,
    OBS_PROVENANCE_MEMORY_FACT_CHECK,
    OBS_PROVENANCE_MEMORY_SUMMARY,
    OBS_PROVENANCE_TOOL,
    OBS_PROVENANCE_WEAVE_CARD,
)
from app.utils.logger import get_logger

_logger = get_logger("agent.tools")

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
    provenance: str = OBS_PROVENANCE_TOOL  # Observation 来源标识（取值见 app/actors.py OBS_PROVENANCE_*）
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
    """按工具名稳定排序返回（A4 批 5 / T6 成本与缓存护栏 M0 项 1，2026-09-27）。

    依据：工具声明文本的顺序由本函数直接决定（context/section_mcp._build_mcp_tool_declarations
    遍历 list_tools() 拼 JSON 声明进 system 前缀），而 _REGISTRY 是 dict，顺序＝注册顺序——
    插件/MCP 工具的登记时机随启动流程变化，重启后同一批工具可能变序，使 system 前缀字节序
    不稳定、上游 prompt 前缀缓存失配。排序只改变返回顺序，零语义变化：工具定义、行为、参数
    都不动，既有调用点要么取集合（tests/test_agent_actions.py:84）要么逐项过滤，不依赖注册顺序。
    """
    return sorted(_REGISTRY.values(), key=lambda spec: spec.name)


# ═══════════════ Observation 认知标注贯通（P0 语义统一 · 第 3 步）═══════════════
# 背景（S2 地图 §1.2 丢失点 5 / 差距表 G6）：``tool_runner._make_observation`` 早就产出了
# ``{epistemic_status, provenance, summary}`` 三元组，但注入上下文那一跳**只取 summary**，
# 「这条观察是什么身份、来自哪里」整体蒸发。本段提供两件事：
#   ① :func:`observation_tag` —— 注入行的「·认知态·来源」片段，受 flag
#      ``observation_label_v1`` 门控，**关＝返回空串＝拼接结果逐字节等于旧文本**；
#   ② :func:`note_observation_injection` —— 进程内计数（标注丢弃比例），只加不改文本、
#      不写库，读端在 ``api/scheduler.py`` 的影子段。
OBSERVATION_LABEL_FLAG = "observation_label_v1"

# 标注片段的安全上限与清洗（禁掉会破坏 ``【…】`` 框架 / 分隔符的字符，防外部 MCP 服务器名注入控制符）
_LABEL_MAX_CHARS = 40
_LABEL_UNSAFE = re.compile(r"[\[\]【】·\s]+")

_OBS_COUNTER_KEYS = (
    "injection_total",             # 走到「工具结果」注入这一行的次数
    "injection_labeled",           # 其中带上了认知标注的（flag 开）
    "injection_label_dropped",     # 其中标注被丢弃的（flag 关＝现状丢失点）
    "injection_label_unavailable", # 其中 observation 压根没带 epistemic_status 的
)
_OBS_COUNTERS: dict[str, int] = dict.fromkeys(_OBS_COUNTER_KEYS, 0)


def observation_label_enabled() -> bool:
    """flag 读法与 ``events/store.domain_events_enabled`` 同款：任何异常一律按「关」（旧文本）。"""
    try:
        from app.flags.agent_flags import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(OBSERVATION_LABEL_FLAG, False))
    except Exception:
        return False


def _label_piece(value: Any, fallback: str) -> tuple[str, bool]:
    """单个标注片段：非字符串/空白 → 兜底值；清洗控制字符并截断。返回 (片段, 原值是否可用)。"""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return fallback, False
    return _LABEL_UNSAFE.sub("_", raw)[:_LABEL_MAX_CHARS], True


def observation_label(observation: Any) -> tuple[str, str]:
    """取观察里的 (认知态, 来源)；缺失/脏值落兜底（``UNVERIFIED`` / ``tool``）。纯函数、不读 flag。"""
    obs = observation if isinstance(observation, dict) else {}
    epi, _ = _label_piece(obs.get("epistemic_status"), EPISTEMIC_UNVERIFIED)
    prov, _ = _label_piece(obs.get("provenance"), OBS_PROVENANCE_TOOL)
    return epi, prov


def observation_tag(observation: Any) -> str:
    """注入行前缀里「·认知态·来源」这一段；flag 关 → 空串（拼接后逐字节等于旧文本）。"""
    if not observation_label_enabled():
        return ""
    epi, prov = observation_label(observation)
    return f"·{epi}·{prov}"


def note_observation_injection(observation: Any, tag: str) -> None:
    """登记一次「工具结果进上下文」的标注去向（丢弃 or 生效）。永不抛，异常只打日志。"""
    try:
        _OBS_COUNTERS["injection_total"] += 1
        obs = observation if isinstance(observation, dict) else {}
        if not isinstance(obs.get("epistemic_status"), str) or not obs["epistemic_status"].strip():
            _OBS_COUNTERS["injection_label_unavailable"] += 1
        if tag:
            _OBS_COUNTERS["injection_labeled"] += 1
        else:
            _OBS_COUNTERS["injection_label_dropped"] += 1
    except Exception as e:  # noqa: BLE001 - 计数不得影响注入
        _logger.warning("observation injection counter failed (fail-open): %s", e)


def observation_semantics_counters() -> dict[str, int]:
    """只读快照（副本）：进程内累计，重启归零；不落库、不查库。"""
    return dict(_OBS_COUNTERS)


def reset_observation_semantics_counters() -> None:
    """计数清零（测试/观测窗口对账用）。"""
    for k in _OBS_COUNTER_KEYS:
        _OBS_COUNTERS[k] = 0


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


def sync_plugin_tools(viewer_user_id: int | None = None, *,
                      viewer_tenant_id: int | None = None) -> int:
    """把已加载插件的 action 自动登记为 ToolSpec（Phase C，2026-08-16）。

    工具名 = f"{plugin}.{action}"；scope 按插件映射（browser/渠道注册/extension，与 permission_service._plugin_scope 一致）；
    执行入口由 ToolRunner 按 plugin+plugin_action 调 registry.run_plugin_action（与现有行为一致）。
    返回登记数。

    A2 M4（2026-09-20）flag ``plugin_runtime_scope`` 开时只登记「可见 + enabled」插件的工具：
    - enabled 判定沿用 ``registry.get_plugin(name)["enabled"]`` 口径（list_plugins 合并的
      DB plugins.enabled 内存缓存，与 ``plugin_disabled_route_gate`` 一致）；
    - 可见性 = M3 纯谓词 ``plugin_visible_to_tenant(viewer_tenant_id)``。本函数是同步函数，
      无法 await 家庭根解析（与 ``registry.list_plugins`` 同限制）：启动期调用点（main.py）
      拿不到调用者 → ``viewer_tenant_id=None``，按 M3 的 **fail-closed** 口径收敛到
      「内置 ∪ 服务级（owner 为空）」，绝不因拿不到租户而全放；异步入口若已解析出家庭根，
      可显式传 ``viewer_tenant_id`` 得到按账号的精确集合（``viewer_user_id`` 仅作调用意图标记）。
    - flag 关 → 逐字节旧行为（不判 enabled、不判可见）。
    """
    try:
        from app.plugins import registry as _registry
    except Exception:
        return 0
    _scope_on = False
    try:
        _scope_on = _registry.plugin_runtime_scope_enabled()
    except Exception:
        _scope_on = False
    count = 0
    for name, entry in list(_registry._loaded.items()):
        actions_map = (entry or {}).get("actions") or {}
        if not actions_map:
            continue
        if _scope_on:
            plugin = _registry.get_plugin(name) or {}
            if not plugin.get("enabled"):
                continue  # M4：停用插件的工具不登记（口径同 plugin_disabled_route_gate）
            prov = _registry._db_prov.get(name, {}) or {}
            if not _registry.plugin_visible_to_tenant(
                source=prov.get("source", plugin.get("source", "builtin")),
                owner_user_id=prov.get("owner_user_id"),
                owner_tenant_id=prov.get("owner_tenant_id"),
                viewer_tenant_id=viewer_tenant_id,
            ):
                continue  # M4：对本租户不可见 → 不登记
        try:
            from app.application import permission_service
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
        provenance=OBS_PROVENANCE_MEMORY_EXTRACT,
    ))
    register_tool(ToolSpec(
        name="memory_fact_check",
        description="记忆一致性核查：AI 回复与已知记忆矛盾检测并降级",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance=OBS_PROVENANCE_MEMORY_FACT_CHECK,
    ))
    register_tool(ToolSpec(
        name="emotion_care",
        description="情绪关怀：检测用户低落后生成延迟关心消息",
        risk_level=RISK_LOW,
        idempotent=False,
        provenance=OBS_PROVENANCE_EMOTION_CARE,
    ))
    register_tool(ToolSpec(
        name="weave_card",
        description="织库卡片生成：记忆聚类生成全景卡片（content_hash 幂等）",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance=OBS_PROVENANCE_WEAVE_CARD,
    ))
    register_tool(ToolSpec(
        name="memory_summary",
        description="记忆总结/复习摘要生成",
        risk_level=RISK_LOW,
        idempotent=True,
        provenance=OBS_PROVENANCE_MEMORY_SUMMARY,
    ))


_register_builtin_tools()
