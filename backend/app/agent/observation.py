"""统一 Observation 语义（A28 第一阶段，2026-10-05）。

背景：`tool_runner._make_observation` 早就产出了 `{epistemic_status, provenance, summary}` 三键，
但「一次工具执行到底观察到了什么、以什么认知状态、来自哪儿、是谁的哪一轮」这些字段散在调用参数里，
没有同一个承载对象——于是 Workspace（A28-S3）想接Observation 时只能各调用点自己拼。

本模块只做**语义承载**，不做替换：
- `to_core_dict()`＝**与历史三键 dict 逐字节同形**（键集合与取值口径不变），主链路继续用它；
- `to_dict()`＝完整记录（含 source/status/tool 与 user/character/session 归属），只供 Workspace 承载。

**第一阶段刻意没有把完整记录塞进 `execute_tool` 的返回 dict**——那会给所有消费者加一个新键，
属于行为变化；完整语义由调用方（`runtime._run_tool_stage`）就地组装后交给 Workspace。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# 与历史实现一致的默认标注（getattr 兜底值，勿改：ToolSpec 未声明时的既有口径）
_DEFAULT_EPISTEMIC_STATUS = "FACT"
_DEFAULT_PROVENANCE = "tool"
_DEFAULT_MAX_OBSERVATION_CHARS = 120

# ── 观测来源类别（source）──
# 注意：这是「这条观测由哪一类认知动作产生」，与 `provenance`（＝哪个**工具面**产出，
# 闭集词表在 `app/actors.py` 的 OBS_PROVENANCE_*，受棘轮 `test_棘轮_全仓散落来源字面量只减不增` 管）
# **不是同一套词表**，也与 `app/memory/sources.py` 的记忆来源元数据无关，三处别混用。
# 非工具类观测（记忆召回、反思）的 provenance 一律留 None：它没有工具面，不替它编一个取值。
SOURCE_TOOL = "tool"
SOURCE_MEMORY = "memory"
SOURCE_REFLECTION = "reflection"


def summarize_result(result) -> str:
    """从工具返回值取摘要（**取法与历史实现逐行一致**：dict 按 summary→message→result→text 兜底，str 直接用，其余空）。

    `text` 兜底是给 MCP 工具的：它们返回 `{ok, text, raw}`，砍掉 text 就等于什么都没观察到。
    """
    if isinstance(result, dict):
        return str(result.get("summary") or result.get("message") or result.get("result") or result.get("text") or "")
    if isinstance(result, str):
        return result
    return ""


def clamp_summary(summary: str, max_chars: int | None) -> str:
    """按工具配置截断（`max_observation_chars` 缺省/为 0 都回落到 120，与历史 `or 120` 一致）。"""
    limit = int(max_chars or _DEFAULT_MAX_OBSERVATION_CHARS)
    return str(summary)[:limit]


@dataclass
class Observation:
    """一次观察的最小语义承载。字段全部为**已知道的事实**，不虚构（缺归属就是 None）。"""

    source: str                      # 观察来源类别：tool / memory / reflection …
    status: str                      # 执行结果：ok / blocked / error …
    summary: str                     # 已截断的摘要文本
    epistemic_status: str = _DEFAULT_EPISTEMIC_STATUS
    provenance: str = _DEFAULT_PROVENANCE
    tool_name: str | None = None
    user_id: int | None = None
    character_id: int | None = None
    session_id: int | None = None
    extra: dict = field(default_factory=dict)

    def to_core_dict(self) -> dict:
        """历史形态的三键 dict（`_make_observation` 的既有产物，主链路继续用它）。"""
        return {
            "epistemic_status": self.epistemic_status,
            "provenance": self.provenance,
            "summary": self.summary,
        }

    def to_dict(self) -> dict:
        """完整记录：只进 Workspace，不进 prompt（第一阶段无消费者）。"""
        out = {
            "source": self.source,
            "status": self.status,
            "summary": self.summary,
            "epistemic_status": self.epistemic_status,
            "provenance": self.provenance,
            "tool_name": self.tool_name,
            "user_id": self.user_id,
            "character_id": self.character_id,
            "session_id": self.session_id,
        }
        if self.extra:
            out["extra"] = dict(self.extra)
        return out


def from_spec(spec, result, status: str, *, source: str = SOURCE_TOOL,
              user_id: int | None = None, character_id: int | None = None,
              session_id: int | None = None) -> Observation:
    """按历史口径从 ToolSpec ＋ 返回值构造 Observation（默认值/截断上限都取自 spec 的同名属性）。"""
    return Observation(
        source=source,
        status=status,
        summary=clamp_summary(summarize_result(result), getattr(spec, "max_observation_chars", None)),
        epistemic_status=getattr(spec, "epistemic_status", _DEFAULT_EPISTEMIC_STATUS),
        provenance=getattr(spec, "provenance", _DEFAULT_PROVENANCE),
        tool_name=getattr(spec, "name", None),
        user_id=user_id,
        character_id=character_id,
        session_id=session_id,
    )


__all__ = [
    "Observation",
    "SOURCE_MEMORY",
    "SOURCE_REFLECTION",
    "SOURCE_TOOL",
    "clamp_summary",
    "from_spec",
    "summarize_result",
]
