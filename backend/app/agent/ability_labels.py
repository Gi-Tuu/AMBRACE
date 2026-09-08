"""能力标签归一（R6，2026-09-09，工具轨迹治理 §4.6）。

把「本轮真实发生的能力/工具」归一为给用户看的中文能力标签，替代旧实现里
「把全部启用中的插件英文 id 拼成一坨 `扩展：a、b、c`」的做法（多报 + 无中文名 + 漏报真实工具）。

设计原则：
- **只在工具/能力真正执行成功后记录**（单一事实源在后端，群聊/流式/非流式都受益）；
- 内置工具 id → 中文名；插件 → 友好名（manifest.display_name 优先）；MCP → `MCP·服务器名`；
- 隐藏内部工具（底层检索/记忆/状态维护不算「TA 调用的能力」）；
- 去重保序 + 上限（单条回复最多 6 项）；
- **任何异常都不影响主链路**（展示标签是纯附加信息）。
"""
from __future__ import annotations

# 内置工具 / 能力 id → 中文展示名（键用 tool spec name 或 action_type，按需补全）
_BUILTIN_LABELS = {
    "web_search": "联网搜索",
    "search": "联网搜索",
    "SEARCH": "联网搜索",
    "gen_image": "生图",
    "image_gen": "生图",
    "GEN_IMAGE": "生图",
    "tts": "语音回复",
    "vision": "识图",
    "image_understanding": "识图",
    "doc_qa": "文档问答",
    "note_calendar": "记日历",
    "CAL_NOTE": "记日历",
    "note_memo": "记备忘",
    "MEMO": "记备忘",
    "timer": "定时提醒",
    "TIMER": "定时提醒",
    "create_timer": "定时提醒",
}

# 内部工具：对用户不可见（底层检索 / 记忆 / 状态维护 / 内部 AI 行为，不算「TA 调用的能力」）
_HIDDEN = {
    "memory_search", "memory_obs", "state_trigger", "save_memory",
    "context_refresh", "internal_reflect",
    # 内部 AI 行为工具（tools._register_builtin_tools，P0-1b）
    "memory_extract", "memory_fact_check", "emotion_care", "weave_card",
    "memory_summary", "status_update",
}

_MAX_ITEMS = 6  # 单条回复最多展示的能力项数


def _humanize(name: str) -> str:
    """英文 id 兜底美化：去下划线/连字符、分段首字母大写（仅用于无映射时）。"""
    parts = [p for p in (name or "").replace("-", "_").split("_") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or (name or "")


def plugin_label(info: dict) -> str:
    """插件友好名：优先 manifest.display_name（registry.info 已透传），否则美化 name。"""
    dn = str((info or {}).get("display_name") or "").strip()
    if dn:
        return dn
    return _humanize(str((info or {}).get("name") or "plugin"))


def mcp_label(server_name: str, tool_name: str = "") -> str:
    """MCP 能力名：只展示到服务器粒度（`MCP·服务器名`），避免把远端工具名堆给用户。"""
    srv = (server_name or "").strip() or "MCP"
    return f"MCP·{srv}"


def is_hidden(name: str) -> bool:
    n = (name or "").strip().lower()
    return n in _HIDDEN


def normalize_tool_list(items: list[str]) -> list[str]:
    """去重（保序）+ 上限，供最终写入 extra_meta.tools 前统一过一遍。"""
    seen: set[str] = set()
    out: list[str] = []
    for it in items or []:
        t = str(it or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= _MAX_ITEMS:
            break
    return out


def _mcp_server_name(spec, explicit: str | None = None) -> str:
    """从 MCP 工具 spec 解析服务器名（mcp.{server}.{tool}）。"""
    if explicit:
        return explicit
    name = str(getattr(spec, "name", "") or "")
    if name.startswith("mcp."):
        parts = name.split(".")
        if len(parts) > 2:
            return parts[1]
    return ""


def _plugin_info(plugin_name: str) -> dict:
    """查插件 info（含 display_name）；查不到返回空 dict 走 _humanize 兜底。"""
    if not plugin_name:
        return {}
    try:
        from app.plugins.registry import get_plugin
        return get_plugin(plugin_name) or {}
    except Exception:
        return {}


def record_ability_used(
    state: dict,
    *,
    spec=None,
    label: str | None = None,
    plugin_info: dict | None = None,
    mcp_server_name: str | None = None,
) -> None:
    """工具/能力真正执行成功后调用，把中文能力标签累计进 ``state['tools_used']``。

    - ``spec`` 非空时按 MCP / 插件 / 内置三类推导中文名；``label`` 显式给定则直接用；
    - 内部工具（``is_hidden``）不记录；
    - 去重保序、上限 6 项；任何异常静默吞掉（绝不因展示标签打断主链路）。
    """
    try:
        if label is None:
            if spec is None:
                return
            _name = str(getattr(spec, "name", "") or "")
            if getattr(spec, "server_id", None) is not None or _name.startswith("mcp."):
                label = mcp_label(_mcp_server_name(spec, mcp_server_name), _name)
            elif getattr(spec, "plugin", None):
                label = plugin_label(plugin_info or _plugin_info(str(spec.plugin)))
            else:
                label = (
                    _BUILTIN_LABELS.get(_name)
                    or _BUILTIN_LABELS.get(str(getattr(spec, "action_type", "") or ""))
                    or _humanize(_name)
                )
        if not label or is_hidden(label) or (spec is not None and is_hidden(str(getattr(spec, "name", "") or ""))):
            return
        bucket = state.setdefault("tools_used", [])
        if not isinstance(bucket, list):
            return
        if label not in bucket:
            bucket.append(label)
        if len(bucket) > _MAX_ITEMS:
            del bucket[_MAX_ITEMS:]
    except Exception:
        # 展示标签永远不得影响主链路
        pass
