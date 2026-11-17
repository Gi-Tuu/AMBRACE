# -*- coding: utf-8 -*-
"""上下文消息的**纯策略**（B2a 自 context_builder 迁出，2026-10-22）。

只做纯输入/输出或**明确的原地列表变换**：不访问数据库/flags/contextvars/日志/LLM，
也不 import context_builder / assembly。配额与裁剪**编排**（`_apply_system_total_quota`）
仍留在协调层，本模块只提供它用到的两个纯函数与优先级常量。
"""
from __future__ import annotations


# M1-S4（2026-08-31）：system 块裁剪优先级——数值越小越关键、越后裁；未识别块=默认 3（主模板同级）。
# key 为块内容头部可识别标记；主模板块（人设+记忆+聊天历史，尾部天然是低价值分区）无标记走默认。
_SYSTEM_BLOCK_PRIORITY: tuple[tuple[str, int], ...] = (
    ("## 本轮结构化状态", 5),  # 已验正式投影优先让出总预算。
    ("【系统指令】", 1),   # 无消息兜底，绝不能丢
    ("【本轮提醒】", 1),   # 用户当前轮的明确指令
    ("【内心活动指令】", 2),   # 思考挡位引导（第一人称化后的标记，2026-09-10）
    ("【存活项清单】", 2),   # P1（2026-09-23）：压缩后仍需成立的目标/未决计划/硬约束，超预算时最后才裁
    ("【全景记忆·织库】", 4), ("【设定·Lorebook】", 4), ("【AI 生活】", 4),
    ("【共同经历】", 4), ("【群聊动态】", 4), ("【生图指令】", 4), ("【搜索能力】", 4),
)
_DEFAULT_BLOCK_PRIORITY = 3


def _block_priority(content: str) -> int:
    head = (content or "")[:24]
    for marker, prio in _SYSTEM_BLOCK_PRIORITY:
        if head.startswith(marker):
            return prio
    return _DEFAULT_BLOCK_PRIORITY


def _clip_by_whole_lines(text: str, keep_chars: int) -> str:
    """M1-S4：按整行边界裁剪，绝不保留半行（P-D1 半句残片根治）；keep_chars 内无行边界则整块放弃。"""
    if len(text) <= keep_chars:
        return text
    head = text[:keep_chars]
    nl = head.rfind("\n")
    return head[:nl] if nl > 0 else ""


def _enforce_user_message_last(messages: list[dict], *, user_index: int | None = None) -> int:
    """红线②宿主不变式（2026-09-18，插件 hook 越位治理）：宿主写入的本轮 user 消息必须**是最后一条**。

    - 正常路径（插件块已被宿主落在 user 之前）→ 零移动、零行为变化，返回 0；
    - 纵深防御：任何落在宿主 user 之后的块（未来 hook / 第三方插件 / `before_generate` 越位）按
      **原相对顺序** splice 到宿主 user 之前；只改位置，不改 role/content、不删内容；
    - 锚点优先用宿主自己 append user 时记录的下标 `user_index`（防插件伪造 `role=user` 把锚点带偏）；
      下标非法或该位已非 user 时，退化为「最后一条 role=user」；两者都没有则不动；
    - 返回移动条数；调用方在 >0 时 warning + 埋点，便于发现回归。
    """
    if not messages:
        return 0
    anchor: int | None = None
    if (
        user_index is not None
        and 0 <= user_index < len(messages)
        and (messages[user_index] or {}).get("role") == "user"
    ):
        anchor = user_index
    else:
        for _i in range(len(messages) - 1, -1, -1):
            if (messages[_i] or {}).get("role") == "user":
                anchor = _i
                break
    if anchor is None or anchor == len(messages) - 1:
        return 0
    moved = messages[anchor + 1:]
    if not moved:
        return 0
    del messages[anchor + 1:]
    messages[anchor:anchor] = moved
    return len(moved)
