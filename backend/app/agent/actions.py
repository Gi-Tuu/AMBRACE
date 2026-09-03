"""AgentAction 统一解析层（Phase A，2026-08-16）

把散落在 chat_service 等处的「标记驱动工具调用」解析收敛为统一 AgentAction 表达：
[SEARCH] / [GEN_IMAGE] / [IMG_TEXT] / [CAL_NOTE] / [MEMO] / [timer] / 【状态更新】
仍是 LLM 输出标记（AgentAction 的序列化形式）。本层统一 parse_actions / strip_actions，
并保留旧版各提取函数（行为与文案完全一致，供 chat_service 等调用点无缝切换）。
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# ── 动作类型（与方案 5.1 AgentAction.action_type 对齐）──
SEARCH = "SEARCH"
RECALL = "RECALL"
GEN_IMAGE = "GEN_IMAGE"
IMG_TEXT = "IMG_TEXT"
CAL_NOTE = "CAL_NOTE"
MEMO = "MEMO"
TIMER = "TIMER"
STATUS_UPDATE = "STATUS_UPDATE"

ACTION_TYPES = (SEARCH, RECALL, GEN_IMAGE, IMG_TEXT, CAL_NOTE, MEMO, TIMER, STATUS_UPDATE)


@dataclass
class AgentAction:
    """统一动作表达：LLM 输出的标记文本 → 结构化动作（保留 raw 用于幂等/审计）"""

    action_type: str
    payload: dict
    raw: str
    idempotency_key: str | None = None

    def to_step(self) -> dict:
        """转 trace 步骤（截断长字段，避免 steps_json 过大）"""
        out: dict[str, Any] = {"action": self.action_type}
        for k, v in (self.payload or {}).items():
            if isinstance(v, str) and len(v) > 80:
                out[k] = v[:80] + "…"
            else:
                out[k] = v
        return out


# ── 标记正则（与旧 chat_service 完全一致，兼容中英文括号；注释标注出处）──
_SEARCH_RE = re.compile(r"[\[【]\s*SEARCH\s*[\]】]\s*(.*?)(?:[\[【]\s*/SEARCH\s*[\]】]|$)", re.M | re.S)
# [RECALL]查询词[/RECALL]（Ariadne 模块 B，2026-09-04）：按需二跳联想检索标记；与 SEARCH 同构
# （兼容中英文/全半角括号、闭合标签可省略）；标记内允许「时间=YYYY-MM；查询」轻量前缀语法，
# 由 loop.run_recall_loop 解析，本层只做提取与剥离。
_RECALL_RE = re.compile(r"[\[【]\s*RECALL\s*[\]】]\s*(.*?)(?:[\[【]\s*/RECALL\s*[\]】]|$)", re.M | re.S)
_GEN_IMAGE_RE = re.compile(r"\[GEN_IMAGE\](.*?)\[/GEN_IMAGE\]", re.S)
_IMG_TEXT_RE = re.compile(r"\[IMG_TEXT\](.*?)\[/IMG_TEXT\]", re.S)
# 兼容英文/中文括号、闭合标签可省略（无闭合时取到行尾）；2026-08-14 修复 AI 输出【CAL_NOTE】无闭合导致不落库
_CAL_NOTE_RE = re.compile(r"[\[【]\s*CAL_NOTE\s*[\]】]\s*(.*?)(?:[\[【]\s*/CAL_NOTE\s*[\]】]|$)", re.M)
_MEMO_RE = re.compile(r"[\[【]\s*MEMO\s*[\]】]\s*(.*?)(?:[\[【]\s*/MEMO\s*[\]】]|$)", re.M)
# [timer:20m] / 【计时器:30分钟】（与 promise_parser 同源，仅识别不执行）
_TIMER_RE = re.compile(
    r"[\[【]\s*(?:timer|计时器)\s*[:：]\s*\d+\s*(?:h|小时|m|min|分钟|s|秒)?\s*[\]】]",
    re.IGNORECASE,
)
# 【状态更新：…】（与 response_parser 同源，仅识别；正文剥离仍走 parse_response）
_STATUS_UPDATE_RE = re.compile(r"[「\[【]\s*状态更新\s*[:：]\s*(.*?)[」\]】]")

# MCP 工具标记（Phase 2，2026-08-26）：[mcp.<server>.<tool>]{JSON 参数}[/mcp.<server>.<tool>]
# 兼容全/半角方括号；args 建议 JSON 对象（非 JSON 时按文本兜底）。
# P4-B（2026-08-29）：闭合标签用反向引用 \1 强制与开始标签的 server.tool 一致，
# 避免 [mcp.a.tool1]{...}[/mcp.a.tool2] 这种不匹配标签也被当作同一工具调用。
_MCP_TOOL_RE = re.compile(
    r"[\[【]\s*mcp\.([A-Za-z0-9_.-]+)\s*[\]】]\s*(.*?)[\[【]\s*/mcp\.\1\s*[\]】]",
    re.S,
)


def extract_search(text: str) -> tuple[str, str | None]:
    """提取自主搜索标记，返回 (清理后文本, 查询词或None)；与旧 chat_service._extract_search 一致"""
    if not text:
        return text, None
    m = _SEARCH_RE.search(text)
    if not m:
        return text, None
    query = m.group(1).strip()
    clean = _SEARCH_RE.sub("", text).rstrip()
    return clean, query or None


def extract_recall(text: str) -> tuple[str, str | None]:
    """提取记忆联想检索标记，返回 (清理后文本, 检索词或 None)；与 extract_search 同构（模块 B）"""
    if not text:
        return text, None
    m = _RECALL_RE.search(text)
    if not m:
        return text, None
    query = (m.group(1) or "").strip()
    clean = _RECALL_RE.sub("", text).rstrip()
    return clean, query or None


def extract_gen_image(text: str) -> tuple[str, str | None, str | None]:
    """提取生图标记，返回 (清理后的文本, 画面描述或None, 图片消息文案或None)；与旧 _extract_gen_image 一致"""
    if not text:
        return text, None, None
    img_text = None
    t = _IMG_TEXT_RE.search(text)
    if t:
        img_text = t.group(1).strip() or None
        text = _IMG_TEXT_RE.sub("", text)
    m = _GEN_IMAGE_RE.search(text)
    if not m:
        return text, None, img_text
    prompt = m.group(1).strip()
    clean = _GEN_IMAGE_RE.sub("", text).rstrip()
    return clean, prompt or None, img_text


def extract_cal_note(text: str) -> tuple[str, str] | None:
    """提取日历备注标记，返回 (YYYY-MM-DD, 内容)；无标记返回 None。日期省略=今天（北京时间）；与旧一致"""
    if not text:
        return None
    m = _CAL_NOTE_RE.search(text)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    today = datetime.now(timezone(timedelta(hours=8))).date()
    head = raw[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        note_date = head
        content = raw[11:].strip()
    elif raw[:2] in ("今天", "明天", "后天"):
        offset = {"今天": 0, "明天": 1, "后天": 2}[raw[:2]]
        note_date = (today + timedelta(days=offset)).isoformat()
        content = raw[2:].strip()
    else:
        note_date = today.isoformat()
        content = raw
    if not content:
        return None
    return note_date, content[:100]


def extract_memo(text: str) -> str | None:
    """提取备忘录标记，返回内容（≤80 字）；无标记返回 None；与旧 _extract_memo 一致"""
    if not text:
        return None
    m = _MEMO_RE.search(text)
    if not m:
        return None
    content = m.group(1).strip()
    if not content:
        return None
    return content[:80]


def extract_timer_tag(text: str) -> str | None:
    """识别 [timer:xx] / 【计时器:xx】标记文本（仅识别，事件创建仍走 promise_parser）"""
    if not text:
        return None
    m = _TIMER_RE.search(text)
    return m.group(0) if m else None


def extract_status_update(text: str) -> str | None:
    """识别【状态更新：…】标记（仅识别，正文剥离仍走 response_parser）"""
    if not text:
        return None
    m = _STATUS_UPDATE_RE.search(text)
    if not m:
        return None
    v = m.group(1).strip()
    return v or None


def strip_status_update(text: str) -> str:
    """剥离【状态更新：…】标记（P2-1：response_parser 正文清理用，与 _STATUS_UPDATE_RE 同源）"""
    if not text:
        return text
    return _STATUS_UPDATE_RE.sub("", text).strip()


def parse_mcp_actions(text: str) -> list[AgentAction]:
    """解析 MCP 工具标记（[mcp.<server>.<tool>]{json}[/mcp.<server>.<tool>]）。

    返回 AgentAction 列表（action_type=完整工具名 mcp.{server}.{tool}，payload=参数对象）；
    参数非合法 JSON 时按文本兜底（{"text": ...}）。不剥离正文（剥离请走 strip_actions）。
    """
    if not text:
        return []
    out: list[AgentAction] = []
    for m in _MCP_TOOL_RE.finditer(text):
        tool = "mcp." + m.group(1)
        arg_str = (m.group(2) or "").strip()
        args: dict = {}
        if arg_str:
            try:
                parsed = json.loads(arg_str)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
            else:
                args = {"text": arg_str}
        out.append(AgentAction(tool, args, m.group(0)))
    return out


def parse_actions(text: str) -> list[AgentAction]:
    """统一解析：把文本里所有已知动作标记解析为 AgentAction 列表（LLM 输出标记=声明动作）。

    - 同一标记出现多次时逐条记录；执行仍走旧提取函数（取首条），本函数用于 trace / 后续 Agent Loop；
    - 不剥离正文，剥离请用 strip_actions。
    """
    if not text:
        return []
    actions: list[AgentAction] = []
    for m in _SEARCH_RE.finditer(text):
        q = m.group(1).strip()
        if q:
            actions.append(AgentAction(SEARCH, {"query": q}, m.group(0)))
    for m in _RECALL_RE.finditer(text):
        q = (m.group(1) or "").strip()
        if q:
            actions.append(AgentAction(RECALL, {"query": q[:80]}, m.group(0)))
    for m in _IMG_TEXT_RE.finditer(text):
        t = m.group(1).strip()
        if t:
            actions.append(AgentAction(IMG_TEXT, {"text": t}, m.group(0)))
    for m in _GEN_IMAGE_RE.finditer(text):
        p = m.group(1).strip()
        if p:
            actions.append(AgentAction(GEN_IMAGE, {"prompt": p}, m.group(0)))
    for m in _CAL_NOTE_RE.finditer(text):
        cal = extract_cal_note(m.group(0))
        if cal:
            actions.append(AgentAction(CAL_NOTE, {"date": cal[0], "text": cal[1]}, m.group(0)))
    for m in _MEMO_RE.finditer(text):
        content = m.group(1).strip()
        if content:
            actions.append(AgentAction(MEMO, {"text": content[:80]}, m.group(0)))
    for m in _TIMER_RE.finditer(text):
        actions.append(AgentAction(TIMER, {"tag": m.group(0)}, m.group(0)))
    su = extract_status_update(text)
    if su:
        actions.append(AgentAction(STATUS_UPDATE, {"text": su}, text))
    # MCP 工具标记（Phase 2）：action_type=完整工具名，供按名路由到 ToolRunner
    actions.extend(parse_mcp_actions(text))
    return actions


# 剥离顺序：SEARCH 允许无闭合到行尾，需先剥离避免吞掉后续标记
_STRIP_PATTERNS = [_SEARCH_RE, _RECALL_RE, _IMG_TEXT_RE, _GEN_IMAGE_RE, _CAL_NOTE_RE, _MEMO_RE, _TIMER_RE, _MCP_TOOL_RE]


def strip_actions(text: str) -> str:
    """统一剥离动作标记（SEARCH/GEN_IMAGE/IMG_TEXT/CAL_NOTE/MEMO/timer）。

    状态更新/自述/记忆等 response_parser 链路标记不在此剥离（仍由 parse_response 处理）。
    """
    if not text:
        return text
    cleaned = text
    for pat in _STRIP_PATTERNS:
        cleaned = pat.sub("", cleaned)
    return cleaned.rstrip()


def actions_to_steps(actions: list[AgentAction]) -> list[dict]:
    """AgentAction 列表 → trace steps_json（截断长字段）"""
    return [a.to_step() for a in actions]
