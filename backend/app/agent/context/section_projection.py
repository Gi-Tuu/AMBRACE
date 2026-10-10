# -*- coding: utf-8 -*-
"""workspace_projection section（A42 ②c 注入档，2026-10-10）：把认知投影的结构化结果渲染成 append 块。

派单＝交接文档 §四-2（**只做前置，不跑③量化退化、零计费**）。三条硬边界：

1. **默认关＝逐字节旧行为**：注入闸 `projection_inject_v1` 关（**含"键还没注册"**）⇒ builder 首行返回
   空列表，不查库、不调模型、不碰任何只读接口。开影子闸 `cognitive_projection_shadow` 只攒覆盖率读数，
   **不会**动 prompt——两把闸各自独立是这一档能不能安全上线的前提。
2. **只读已投影好的结果**：渲染函数 `workspace_projection.render_projection_block` 是纯函数（无 IO），
   本 section 不新取数、不改投影语义、不与 Working State／Topic Tracker 合并（唯一事实源不变）。
3. **配额独立不挤占**：本区用自己的 `INJECT_QUOTA_TOKENS`（五格值体量实测 ≈357 字符 > 既有最小
   append 段 `current_state_anchor` 的容量 ⇒ 必须另开一区，从别的段挤就会改它们的裁剪结果）。

取号（先跑 `python scripts/audit_context_order.py` 再定）：70–79 为保留空档，70 已被 thought_pool 占用，
71–79 未占用 ⇒ 取 **71**。落位不看 order，由 `assembly.py` append 链里的
`if _sv and "workspace_projection" in _sv` 决定（位置＝现状三连之后、location／素材块之前，符合
docs/context-order-convention.md §2.2「状态类在前」与红线②「诉求恒最后」）。

注册位置为什么不是 `assembly.py`（交接白名单原本写的是那里）：注册表靠 **import 时**登记，而
`context/__init__.py` 顶层只 import `section_*`，`assembly` 是**函数内惰性 import**（顶层引它会撞上
context_builder 的 import 循环，见该文件 §注）。实测证据：`import app.agent.context` 之后
`get_sections()` 里**没有** `workspace_projection`，再 import 一次 assembly 才有 ⇒ 挂在 assembly 上
＝每个进程第一轮不注入、且测试结果取决于谁先被 import。所以按项目自己的规矩放到 `section_*.py`，
配套在 `__init__.py` 补一行触发 import（两处都超出白名单，已写进汇报）。
"""
from __future__ import annotations

import logging

from app.agent.context.sections import ContextSection, TARGET_APPEND, register_section
from app.agent.workspace_projection import INJECT_QUOTA_TOKENS, inject_flag_on, render_projection_block

_logger = logging.getLogger("agent.context.section_projection")

_PROJECTION_INJECT_ORDER = 71


async def workspace_projection_section(state: dict, ctx: dict) -> list[str]:
    """投影结果 → 一条 append 块。闸关或没投出东西 ⇒ 空列表（零条，逐字节旧行为）。"""
    if not inject_flag_on():
        return []
    ws = (state or {}).get("workspace")
    if ws is None:
        return []
    try:
        text = render_projection_block(ws, quota_tokens=INJECT_QUOTA_TOKENS)
    except Exception as e:  # 本档坏掉绝不拖垮装配（与既有 section 同口径；异常只留 WARNING）
        _logger.warning("workspace_projection section failed: %s", e)
        return []
    return [text] if text else []


register_section(ContextSection(
    key="workspace_projection", builder=workspace_projection_section, target=TARGET_APPEND,
    quota_tokens=INJECT_QUOTA_TOKENS, order=_PROJECTION_INJECT_ORDER,
))
