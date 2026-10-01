# -*- coding: utf-8 -*-
"""A4 批 8 块 A M0 —— OpenAI 兼容「形状」的**纯函数**映射层。

为什么单列一个域模块
--------------------
§2.1(1) 要求「形状映射是纯函数 + 单测钉住」。放在 ``app/domain/`` 下（与
``domain/relational``、``domain/thought`` 同级）就是为了**结构上无法**碰 IO：
本模块零 import 业务模块，只吃基础类型、只吐基础类型（由测试用 AST 钉住）。

边界（强约束）：**零 IO、零 ORM、零 DB、零 flag、零网络**。
M0 的 DoD 是「**不注册任何路由** ⇒ 全仓行为逐字节不变」，所以本模块**不接任何调用方**。

口径来源：AMBRACE_批8_接口面与形态护栏_详细设计_v1_20260929.md
§1.1（块 A）/ §2.1（形状壳）/ §7 M0「A」行。

⚠️ 所有上限一律**沿用** ``application/character_chat_api.chat_with_character``
（:216-227）的既有硬顶——§2.1(1) 明令「不在新壳里另设一套阈值」。
"""
from __future__ import annotations

# ── 形状常量 ──────────────────────────────────────────────────────────
MODEL_PREFIX = "ambrace:"          # model 字段携带角色标识：ambrace:<ai_id>
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"             # 服务端人设独占，客户端不得传

# ── 上限：逐条对齐 character_chat_api.chat_with_character（:216-227）──
MAX_INPUT_CHARS = 4000             # :220 输入 ≤4000 字符
DEFAULT_MAX_TOKENS = 800           # :200 缺省 800
MAX_TOKENS_CAP = 2000              # :223 / config.py:108 全局上限
MIN_MAX_TOKENS = 1                 # :223 下限
DEFAULT_TEMPERATURE = 0.8          # :201 缺省 0.8
TEMPERATURE_MIN = 0.0              # :224 夹取下界
TEMPERATURE_MAX = 1.5              # :224 夹取上界

# ── history 形状保护（纯形状层；M1 接线时再与内核口径对齐）──
MAX_HISTORY_ITEMS = 50             # 只保留最近 N 条，防「声明一大串」式刷屏
MAX_HISTORY_ITEM_CHARS = 2000      # 单条截断


def _to_int(value, default: int) -> int:
    """脏值（None / 非数字）回落默认——形状层绝不因一个坏字段抛错。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_max_tokens(value) -> int:
    """``max(1, min(2000, int(value or 800)))``——与内核 :223 逐字同口径。"""
    return max(MIN_MAX_TOKENS, min(MAX_TOKENS_CAP, _to_int(value, DEFAULT_MAX_TOKENS)))


def clamp_temperature(value) -> float:
    """``max(0.0, min(1.5, float(value if not None else 0.8)))``——与内核 :224 同口径。"""
    raw = DEFAULT_TEMPERATURE if value is None else _to_float(value, DEFAULT_TEMPERATURE)
    return max(TEMPERATURE_MIN, min(TEMPERATURE_MAX, raw))


def _normalize_history(history) -> list[dict]:
    """history → OpenAI ``messages``（只留 user / assistant，逐条截断 + 条数上限）。

    - 非 dict / 无 content 的项**整条丢弃**（形状层不做内容修复）；
    - ``role`` 只认 ``assistant``，其余（含 system、脏值）一律落 ``user``——
      system 由服务端人设独占，历史里出现的 system 不当作 system 对待；
    - 超 ``MAX_HISTORY_ITEMS`` 时**只保留最近 N 条**（保近不保全，上下文价值随时间衰减）。
    """
    if not isinstance(history, (list, tuple)):
        return []
    items: list[dict] = []
    for raw in history:
        if not isinstance(raw, dict):
            continue
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        role = ROLE_ASSISTANT if raw.get("role") == ROLE_ASSISTANT else ROLE_USER
        items.append({"role": role, "content": content.strip()[:MAX_HISTORY_ITEM_CHARS]})
    if len(items) > MAX_HISTORY_ITEMS:
        items = items[-MAX_HISTORY_ITEMS:]
    return items


def _alternate(messages: list[dict]) -> list[dict]:
    """强制 user / assistant 交替：连续同角色**合并**（用换行拼接，不丢内容）。

    交替是 OpenAI 形态的硬要求；合并而非丢弃，是为了不悄悄吃掉用户的历史内容。
    """
    out: list[dict] = []
    for msg in messages:
        if out and out[-1]["role"] == msg["role"]:
            out[-1] = {"role": msg["role"],
                       "content": out[-1]["content"] + "\n" + msg["content"]}
        else:
            out.append(dict(msg))
    return out


def to_openai_payload(req) -> dict:
    """内部请求体（``api/ai_api.py:17-23`` 的 ``_ChatRequest`` 形状）→ OpenAI 请求体形状。

    入参键：``aiId / input / history / maxTokens / temperature / lang``。
    出参：``{"model", "messages", "max_tokens", "temperature"}``
    - ``model`` = ``ambrace:<aiId>``（角色标识只在 model 一处编码：key 绑账号、角色走 model）
    - ``messages`` = history 归一后的 user/assistant 交替 **+ 末条当前输入**（user）
    - ``max_tokens`` / ``temperature`` 一律夹到内核既有硬顶

    ``lang`` 不进 OpenAI 请求体（OpenAI 形状里没有对应字段），留给调用方在链路外处理。
    """
    data = req if isinstance(req, dict) else {}
    ai_id = data.get("aiId", data.get("ai_id"))
    messages = _normalize_history(data.get("history"))

    text = data.get("input")
    if isinstance(text, str) and text.strip():
        messages.append({"role": ROLE_USER, "content": text.strip()[:MAX_INPUT_CHARS]})

    return {
        "model": f"{MODEL_PREFIX}{ai_id}",
        "messages": _alternate(messages),
        "max_tokens": clamp_max_tokens(data.get("maxTokens", data.get("max_tokens"))),
        "temperature": clamp_temperature(data.get("temperature")),
    }


def validate_compat_request(req) -> str | None:
    """OpenAI 形状入参的**显式拒绝**校验：返回中文错误文案；``None`` ＝ 通过。

    只管**形状与文案**；归属判定（该角色是否属于调用账号）要查库，**留给 M1**。

    §2.1(1) 明令「必须显式拒绝而不是静默忽略」——静默忽略会让接错的人以为成功：
    ``stream=true`` / ``tools`` / ``response_format`` / ``n>1`` / 客户端传
    ``messages[role=system]`` ⇒ 一律返回文案（端点侧映射成 400）。
    """
    if not isinstance(req, dict):
        return "请求体必须是 JSON 对象。"

    if req.get("stream"):
        return "暂不支持流式输出（stream=true）：当前流式链路绑定会话，兼容端点不建会话。"
    if req.get("tools"):
        return "暂不支持函数调用（tools）：本批次未开放工具能力。"
    if req.get("response_format"):
        return "暂不支持 response_format：结构化输出不在本批次范围内。"
    if _to_int(req.get("n"), 1) > 1:
        return "暂不支持一次返回多个候选（n>1）。"

    messages = req.get("messages")
    if not isinstance(messages, list) or not messages:
        return "messages 必须是非空数组。"
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            return f"messages[{i}] 必须是对象。"
        role = msg.get("role")
        if role == ROLE_SYSTEM:
            return ("messages 不允许包含 system 角色：系统人设由服务端独占，"
                    "不接受客户端传入。")
        if role not in (ROLE_USER, ROLE_ASSISTANT):
            return f"messages[{i}].role 只允许 user / assistant，收到 {role!r}。"
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            return f"messages[{i}].content 必须是非空字符串。"

    model = req.get("model")
    if not isinstance(model, str) or not model.strip():
        return "model 必须是非空字符串。"
    if not model.startswith(MODEL_PREFIX):
        return f"model 必须以 {MODEL_PREFIX} 开头（形如 {MODEL_PREFIX}<ai_id>）。"
    if not model[len(MODEL_PREFIX):].strip().isdigit():
        return f"model 里的角色 id 必须是数字，形如 {MODEL_PREFIX}<ai_id>。"
    return None
