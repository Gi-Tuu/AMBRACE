"""context 注册表：build_context 主函数（步骤5：全 section 注册表驱动）。

``build_context`` 运行所有已注册 section（template 槽 + append 块），把各分区值收集进
``_section_values`` 后委托 ``context_builder.build_context_legacy`` 完成最终组装
（模板 .format + 追加块 + 系统总量裁剪）。

Feature Flag：``agent_context_registry``（默认开）——开=走本模块（注册表驱动）；关=回退旧实现
``context_builder.build_context_legacy``（见 context_builder.build_context 包装层）。

每个 section 异常仅记 ``_logger.warning`` 跳过（不拖垮整体，维持现状）。
单 section 抛异常不影响其他 section 的注入。

注：对 ``context_builder`` 的 import 一律放在函数内（惰性）——因 context_builder 顶层
import 本包的子模块（section_*）会先触发本包 ``__init__``，若在顶层再回引 context_builder
会造成 import 循环。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import get_sections, TARGET_APPEND, TARGET_TEMPLATE
from app.agent.context import section_mcp as _section_mcp  # noqa: F401  # 触发 MCP section 注册
from app.agent.context import section_memories as _section_memories  # noqa: F401  # 触发记忆 section 注册
from app.agent.context import section_persona as _section_persona  # noqa: F401
from app.agent.context import section_summaries as _section_summaries  # noqa: F401
from app.agent.context import section_moments as _section_moments  # noqa: F401
from app.agent.context import section_pet as _section_pet  # noqa: F401
from app.agent.context import section_phone as _section_phone  # noqa: F401
from app.agent.context import section_world as _section_world  # noqa: F401
from app.agent.context import section_overlay as _section_overlay  # noqa: F401
from app.agent.context.section_memories import _bump_memory_round  # P3-5：注册表路径先 bump 再跑 sections

_logger = logging.getLogger("agent.context")


def _load_ctx_builder():
    """惰性导入 context_builder（含 build_context_legacy / _EST_CHARS_PER_TOKEN 等）。"""
    from app.agent import context_builder as _cb
    return _cb


async def _resolve_trim(state: dict) -> dict:
    """热度裁剪参数（与 build_context_legacy 口径一致）：core/anchors 注入上限需要。"""
    _cb = _load_ctx_builder()
    hot = True
    try:
        from app.agent.loop import AGENT_FLAGS
        if AGENT_FLAGS.get("agent_context_trim", True):
            hot = await _cb._is_hot_character(state["character_id"], state.get("user_id", 1))
    except Exception:
        hot = True
    return _cb._trim_limits(hot)


async def _run_sections(state: dict, ctx: dict) -> dict:
    """注册表驱动：调用已注册 section 的 builder，收集各分区值。

    - template 槽：``values[key] = text``（str，未裁剪原始值，由 build_context_legacy 统一裁剪）；
      key 采用 ``sec.key``（legacy 覆盖块按 key 读取；pets 的 slot 为 ``pets_info``、key 为 ``pets``，
      以 key 记录才能被 legacy 覆盖块识别并跳过内联）；
    - append 块：``values[key] = list[str]``（每条即一条追加 system 消息内容）。
    单个 section 抛异常仅记 warning 跳过（不拖垮整体，维持现状）。

    P3-1（2026-08-31）：记录**所有已执行** section 的键——包括结果为空的键（template 空串 /
    append 空列表）——一并写入 ``values``，供 ``build_context_legacy`` 用「key in _sv」判断并
    跳过对应内联计算（消除注册表 + legacy 双重 DB 查询）。空值亦写入，使 legacy 占位符语义
    （如 moments 无内容仍「暂无」、pets 仍「无」）与内联一致；section 抛异常未执行时不写入，
    legacy 照常内联计算兜底（行为与现状一致）。
    """
    values: dict[str, object] = {}
    for sec in get_sections():
        if not sec.enabled:
            continue
        try:
            text = await sec.builder(state, ctx)
        except Exception as e:
            _logger.warning("context section %s failed: %s", sec.key, e)
            continue
        if sec.target == TARGET_TEMPLATE and sec.slot:
            values[sec.key] = text
        elif sec.target == TARGET_APPEND:
            # 统一为 list[str]（每条即一条追加 system 消息内容）
            if isinstance(text, str):
                values[sec.key] = [text]
            else:
                values[sec.key] = list(text)
    return values


async def build_context(state: dict, *, stream: bool | None = None) -> dict:
    """组装 state["context_messages"]（注册表驱动版，行为与旧版 build_context 零变化）。

    - `stream`（P2-A）：显式标记流式模式；None 时从 state 推断（state["stream_sink"] 非空 = 流式）。
    - 检索区轮次 +1 在 build_context_legacy 的角色存在检查后执行（与旧版位置一致，
      保证 N 轮去重语义不变）。
    """
    _cb = _load_ctx_builder()

    build_context_legacy = _cb.build_context_legacy
    est_chars_per_token = _cb._EST_CHARS_PER_TOKEN
    is_stream = bool(state.get("stream_sink")) if stream is None else bool(stream)

    trim = await _resolve_trim(state)

    ctx = {
        "is_stream": is_stream,
        "trim": trim,
        "est_chars_per_token": est_chars_per_token,
    }
    # P3-5（2026-08-25）：检索区轮次 +1 移到 sections 之前——注册表路径与纯 legacy 路径都先 bump
    # 再算记忆/检索区/Lorebook（sticky/cooldown），保证两条路径轮次一致（消除 off-by-one）。
    # legacy（build_context_legacy）在 _section_values 非空（注册表路径）时不再重复 bump。
    _bump_memory_round(state["character_id"])

    values = await _run_sections(state, ctx)

    # 委托旧实现完成最终组装：所有分区值来自注册表 section，其余（角色存在检查/温度/字符基础信息等）
    # 与旧版完全一致（零行为变化）。_trim 复用注册表路径算好的热度裁剪参数（避免 _is_hot_character 二次查询）。
    return await build_context_legacy(state, stream=stream, _section_values=values, _trim=trim)
