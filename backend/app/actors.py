# -*- coding: utf-8 -*-
"""Actor / 认知态常量单一来源（P0 语义统一 · 第 1 步，2026-09-29；**零行为**）。

定位（S2 架构数据流地图 §4.3 ＋ 断点清单 #1/#2/#3/#4）
────────────────────────────────────────────────────────
「这条内容是谁说的」在本项目里有**多种落法**，取值空间散落在 6 个模块里各写各的字面量：

- ``ChatMessage.sender_type``：``user`` / ``ai``（``application/chat_service.py:355``、``:1126``）
- ``DomainEvent.actor_type``：``user`` / ``ai`` / ``system``（``api/chat.py``、``events/store.py:102``）
  另有一处「枚举文本 user/character/system」的口径登记（``application/user_cascade.py:122``）
- ``Memory.speaker_type``：``user`` / ``character`` / ``system``（``models/memory/__init__.py:32``）
- ``PhoneSnapshot.actor``：``user`` / ``character`` / ``system``（``models/device/__init__.py:82``）
- 事件 ``speaker.type``：``user`` / ``character`` / ``system`` / ``tool``（``events/schema.py:99-103``）
- 准入归属（不写进任何列）：额外有 ``perception``（``memory/write.py:56``）与 ``unset``（同文件 :232）

本模块把**取值**与**归一化判据**收成一处，供后续第 2 步接线时逐点替换。

第 1 步的硬约束（务必遵守，否则违背「零行为」）
────────────────────────────────────────────────────────
1. **不改写既有生产代码里的字面量**：``memory/write.py`` 等文件照旧自带字面量，本模块与它们
   由 ``tests/test_actor_constants.py`` 的「参数化对照」钉死（值或判据一旦漂移，测试直接红）；
   这与项目既有的「两处各自持有、故意不互 import」口径一致（见 ``write.py`` 的
   ``_META_NOISE_KEYWORDS`` 注释：避免 memory↔events 模块级循环依赖）。
2. **归一化判据逐字复用** ``memory/write.py::_normalize_sender``：词表、比较顺序、返回 ``None``
   的边界一律照现状，**不新增别名、不改判定**。
3. **纯模块**：零 IO、零业务 import（只 import ``__future__``），任何人可安全 import。

「多种落法」现状对照（第 2 步要收敛的差异，本步只登记不动）
────────────────────────────────────────────────────────
- ``normalize_sender``（本文件，＝``write.py::_normalize_sender``）：脏值/未知值 ⇒ ``None``；
  ``ai``/``char``/``bot`` 收进 ``character``，``mcp``/``search``/``external`` 收进 ``tool``。
- ``events/schema.py::speaker_of``：只把 ``ai`` → ``character``，**其余原样透传**且从不返回 ``None``。
- ``write.py::admit_memory``：角色档只认 ``character``/``ai``（**不认** ``char``/``bot``），
  工具档与 :func:`normalize_sender` 同表。⇒ 见 :data:`ADMISSION_CHARACTER_ALIASES`。
"""
from __future__ import annotations

# ───────────────────────── 归属（actor）规范值 ─────────────────────────
# 取值与大小写**照现状字面量**（每处附来源），不自创、不改名、不缩写。
ACTOR_USER = "user"                  # 用户亲口陈述（write.py:358 / models/memory:32）
ACTOR_CHARACTER = "character"        # 角色自己说的／模型自述（write.py:354 / speaker_of:102）
ACTOR_SYSTEM = "system"              # 系统事件（write.py:358 / chat_service.py:1207）
ACTOR_TOOL = "tool"                  # 外部工具/检索结果（write.py:356）
ACTOR_PERCEPTION = "perception"      # 断点 #5：感知派生条的**准入归属**（write.py:56 PERCEPTION_SENDER）
ACTOR_UNSET = "unset"                # 归属未知的兜底文本（write.py:232 ``(actor or "unset")``）

# 规范值全集（不含 ACTOR_UNSET——它是「没有归属」的占位，不是一种归属）
ACTORS: tuple[str, ...] = (ACTOR_USER, ACTOR_CHARACTER, ACTOR_SYSTEM, ACTOR_TOOL, ACTOR_PERCEPTION)

# ───────────────────────── 入参别名表（现状词表，逐字抄自 write.py）─────────────────────────
# :func:`normalize_sender` 用这三组（＝ write.py:353-358 的三个元组，顺序即判定优先级）。
CHARACTER_SENDER_ALIASES = ("ai", "character", "char", "bot")        # write.py:353
TOOL_SENDER_ALIASES = ("tool", "mcp", "search", "external")          # write.py:355
PRESERVED_SENDER_ALIASES = (ACTOR_USER, ACTOR_SYSTEM)                # write.py:357（原值保留）

# 准入裁决面（write.py::admit_memory）用的**更窄**一组：现状如此，本步不放大（差异登记于此）。
ADMISSION_CHARACTER_ALIASES = ("character", "ai")                    # write.py:415
ADMISSION_TOOL_ALIASES = ("tool", "mcp", "search", "external")       # write.py:417
ADMISSION_SYSTEM_ALIAS = ACTOR_SYSTEM                                # write.py:419

# ───────────────────────── 来源面（判归属时读到的 source 值）─────────────────────────
CHAT_SOURCE = "chat"                                        # write.py:383（来源消息可回溯）
PERCEPTION_SOURCE = "perception"                            # memory/perception_tier.py:29
# 「无来源消息 = 模型自述」三档（write.py:393，**顺序与元素照抄**：diary/life/bio）
SELF_NARRATIVE_SOURCES = ("diary", "life", "bio")
# 来源以这些前缀开头 ⇒ 工具产出（write.py:395，注意现状判的是**原值**、非小写）
TOOL_SOURCE_PREFIXES = ("mcp", "tool", "search")

# ───────────────────────── 认知态（epistemic_status）规范值 ─────────────────────────
# 唯一既有事实源是 ``events/schema.py:23-29``；这里按同名字、同值再声明一次（零业务 import），
# 由 tests/test_actor_constants.py 断言两组取值逐字相等、顺序一致，防两处漂移。
EPISTEMIC_FACT = "FACT"
EPISTEMIC_INFERRED = "INFERRED"
EPISTEMIC_PLANNED = "PLANNED"
EPISTEMIC_FICTIONAL = "FICTIONAL"
EPISTEMIC_UNVERIFIED = "UNVERIFIED"
EPISTEMIC_VALUES: tuple[str, ...] = (
    EPISTEMIC_FACT, EPISTEMIC_INFERRED, EPISTEMIC_PLANNED, EPISTEMIC_FICTIONAL, EPISTEMIC_UNVERIFIED,
)


def normalize_sender(value) -> str | None:
    """sender_type 归一：ai/character/bot → character；tool/mcp/search/external → tool；其余 user/system。

    **逐字复用** ``app/memory/write.py::_normalize_sender``（:348-359）——判定、词表、比较顺序、
    返回 ``None`` 的边界（空 / 纯空白 / 未登记值 / 归一后不在三组别名里）一律照现状，
    等价性由 ``tests/test_actor_constants.py`` 参数化对照旧实现锁死。

    入参故意不做类型校验（照旧写法 ``(value or "")``）：非字符串脏值可能抛异常，由调用方
    自行隔离（``write.py`` 的调用点全在 try/except 内），本函数不擅自吞异常——吞了就是改判定。
    """
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in CHARACTER_SENDER_ALIASES:
        return ACTOR_CHARACTER
    if v in TOOL_SENDER_ALIASES:
        return ACTOR_TOOL
    if v in PRESERVED_SENDER_ALIASES:
        return v
    return None


__all__ = [
    "ACTOR_USER", "ACTOR_CHARACTER", "ACTOR_SYSTEM", "ACTOR_TOOL", "ACTOR_PERCEPTION", "ACTOR_UNSET",
    "ACTORS",
    "CHARACTER_SENDER_ALIASES", "TOOL_SENDER_ALIASES", "PRESERVED_SENDER_ALIASES",
    "ADMISSION_CHARACTER_ALIASES", "ADMISSION_TOOL_ALIASES", "ADMISSION_SYSTEM_ALIAS",
    "CHAT_SOURCE", "PERCEPTION_SOURCE", "SELF_NARRATIVE_SOURCES", "TOOL_SOURCE_PREFIXES",
    "EPISTEMIC_FACT", "EPISTEMIC_INFERRED", "EPISTEMIC_PLANNED", "EPISTEMIC_FICTIONAL",
    "EPISTEMIC_UNVERIFIED", "EPISTEMIC_VALUES",
    "normalize_sender",
]
