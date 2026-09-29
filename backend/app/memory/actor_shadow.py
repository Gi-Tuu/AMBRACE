# -*- coding: utf-8 -*-
"""Actor 语义影子埋点（P0 语义统一 · 第 1 步，2026-09-29；**只判定 + 只留痕，零写入**）。

为什么要影子（S2 地图 §4.3 丢失点 1/2/3）
────────────────────────────────────────────────────────
「按统一语义该记在谁名下」这件事，现状与「本轮实际落库成什么」是分岔的：

1. ``write.py:722-723``——调用方没给 speaker 时**缺省 ``user``**（``source=diary``/``bio`` 这类
   模型自述也会被记成「用户说的」）；
2. ``write.py::_resolve_admission_sender`` 的 perception 分支受 ``perception_source_tag`` 门控，
   关闸时感知派生条一路掉到底部 ``return "user"``；
3. 并入（merge）路径上，进来的那句话的归属**整体消失**（只更新旧行的强度，不记「谁并进来的」）。

第 1 步**只观测不生效**：挂点（``write.py`` 两处）在 flag 开时多算一次纯判定、多打一条 INFO，
取值、返回值、判据、异常路径一律不动。攒够「unified vs actual 差异占比」再谈第 2 步接线。

口径来源（**不新增判定**）
────────────────────────────────────────────────────────
:func:`judge_actor` 的分支顺序与判据＝``memory/write.py::_resolve_admission_sender``（:362-397）
的现有优先级链：感知来源 > 调用方显式 speaker_type > 来源消息 sender_type > 来源类型默认
（diary/life/bio→character、mcp/tool/search 前缀→tool）> 兜底 user。
唯一有意差异：**本函数纯、不查库**——现状第 4 档会 ``db.get(ChatMessage)`` 取来源消息的
``sender_type``，这里如实返回 ``actor=None`` ＋ 依据 ``need_source_message``（拿不准就说拿不准，
不臆造成 user）。

约束：零 DB、零写文件、不改任何入参、不抛异常（内部全量 fail-open）；留痕**只有 INFO 日志一条**，
不新增表/列、不调 ``obs_event``（那条会写 ``agent_task_logs``，属新增 DB 调用，本步禁止）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.actors import (
    ACTOR_CHARACTER,
    ACTOR_PERCEPTION,
    ACTOR_TOOL,
    ACTOR_USER,
    CHAT_SOURCE,
    PERCEPTION_SOURCE,
    SELF_NARRATIVE_SOURCES,
    TOOL_SOURCE_PREFIXES,
    normalize_sender,
)
from app.utils.logger import get_logger

_logger = get_logger("memory.actor_shadow")

# 依据标签（留痕里说明「按哪一档判的」；与 judge_actor 的分支一一对应，不引入新判定）
BASIS_PERCEPTION = "perception_source"            # 第 1 档：感知来源
BASIS_SPEAKER_TYPE = "explicit_speaker_type"      # 第 2 档：调用方显式 speaker_type
BASIS_SOURCE_MESSAGE = "source_message_sender"    # 第 3 档：来源消息的 sender_type（已取到）
BASIS_NEED_SOURCE_MESSAGE = "need_source_message"  # 第 4 档：现状要查库才能定 ⇒ 如实「未知」
BASIS_SELF_NARRATIVE = "self_narrative_source"    # 第 5 档：diary/life/bio → 角色自述
BASIS_TOOL_SOURCE = "tool_source_prefix"          # 第 6 档：mcp/tool/search 前缀 → 工具
BASIS_DEFAULT_USER = "default_user_fallback"      # 第 7 档：兜底（丢失点 1/2 的落点）

BASIS_VALUES = (BASIS_PERCEPTION, BASIS_SPEAKER_TYPE, BASIS_SOURCE_MESSAGE, BASIS_NEED_SOURCE_MESSAGE,
                BASIS_SELF_NARRATIVE, BASIS_TOOL_SOURCE, BASIS_DEFAULT_USER)

# 挂点标识（日志里区分两处埋点：新行 vs 并入）
SITE_WRITE = "write"
SITE_MERGE = "merge"


@dataclass(frozen=True)
class ActorJudgment:
    """统一语义下这条内容应归属的 actor ＋ 依据（纯数据，不可变）。

    ``actor`` 为 ``None`` 表示「纯判定拿不准」——现状这一步要读 ``ChatMessage``，影子档不查库，
    绝不臆造成 ``user``（臆造就等于把丢失点伪装成结论）。
    """

    actor: str | None
    basis: str

    def differs_from(self, actual: str | None) -> bool:
        """与实际落库/准入归属比对（``None``＝拿不准，不计入「判错」，只计「待观察」）。"""
        return self.actor is not None and self.actor != actual


def _is_perception_source(source) -> bool:
    """来源是否感知派生（照 ``write.py:59-61`` 的判据：归一比较、脏输入恒 False）。"""
    return isinstance(source, str) and source.strip().lower() == PERCEPTION_SOURCE


def _as_sender(value):
    """说话人字段安全取值：非字符串脏值按「未识别」处理（照 ``_is_perception_source`` 的守卫风格）。

    现状 ``write.py::_normalize_sender`` 对 ``123`` 这类入参会抛 ``AttributeError``（调用方各自吞掉），
    影子档不允许把异常传到主链路上，所以这里先守卫再交给**同一个**归一化函数——字符串入参的结果
    与现状逐字相同，非字符串入参只是「从抛异常」变成「判不出＝None」，不会凭空多出一种归属。
    """
    return value if value is None or isinstance(value, str) else None


def judge_actor(*, source=None, speaker_type=None, source_message_sender=None,
                source_id=None) -> ActorJudgment:
    """按统一语义判定这条内容应归属哪个 actor（纯函数，零 IO）。

    入参全部关键字传递、都可缺省；判据顺序与 ``_resolve_admission_sender`` 现状一致：

    ==== ============================== =========================================
    档   命中条件                       结果
    ==== ============================== =========================================
    1    ``source`` 是感知来源           ``perception``
    2    ``speaker_type`` 可归一          归一结果（``character`` / ``tool`` / ``user`` / ``system``）
    3    ``source_message_sender`` 可归一  归一结果
    4    ``source=="chat"`` 且有 ``source_id`` 且上两档都空 ⇒ 现状查库；本函数 ``None``
    5    ``source`` ∈ diary/life/bio     ``character``（模型自述）
    6    ``source`` 以 mcp/tool/search 开头 ``tool``
    7    其余                            ``user``（＝现状兜底，也是丢失点所在）
    ==== ============================== =========================================

    与现状唯一的语义差异：第 1 档**不受** ``perception_source_tag`` 门控（统一语义里感知就是感知），
    该差异正是要观测的丢失点 2，故第 1 档关闸时会与现状不同——这不是 bug，是本轮要量出来的东西。
    """
    if _is_perception_source(source):
        return ActorJudgment(ACTOR_PERCEPTION, BASIS_PERCEPTION)
    st = normalize_sender(_as_sender(speaker_type))
    if st:
        return ActorJudgment(st, BASIS_SPEAKER_TYPE)
    ms = normalize_sender(_as_sender(source_message_sender))
    if ms:
        return ActorJudgment(ms, BASIS_SOURCE_MESSAGE)
    if source == CHAT_SOURCE and source_id is not None:
        return ActorJudgment(None, BASIS_NEED_SOURCE_MESSAGE)
    if source in SELF_NARRATIVE_SOURCES:
        return ActorJudgment(ACTOR_CHARACTER, BASIS_SELF_NARRATIVE)
    if source and str(source).startswith(TOOL_SOURCE_PREFIXES):
        return ActorJudgment(ACTOR_TOOL, BASIS_TOOL_SOURCE)
    return ActorJudgment(ACTOR_USER, BASIS_DEFAULT_USER)


def trace_actor_write(*, character_id=None, user_id=None, source=None, speaker_type=None,
                      source_message_sender=None, source_id=None, actual_speaker_type=None,
                      actual_speaker_id=None, admission_actor=None) -> None:
    """埋点①（新行落库前）：留痕比对「统一语义应归为谁」vs「本轮实际会落成什么」。

    只打一条 INFO，不写库、不改任何取值。调用方（``write.py``）负责 flag 门控与本函数异常隔离；
    本函数内部再兜一层，保证留痕本身永远不能改变写入结果。
    """
    try:
        j = judge_actor(source=source, speaker_type=speaker_type,
                        source_message_sender=source_message_sender, source_id=source_id)
        actual = admission_actor or actual_speaker_type
        _logger.info(
            "actor shadow %s: char=%s user=%s src=%s unified=%s basis=%s actual=%s "
            "actual_speaker_id=%s diff=%s",
            SITE_WRITE, character_id, user_id, source, j.actor, j.basis, actual,
            actual_speaker_id, j.differs_from(actual),
        )
    except Exception as e:  # pragma: no cover - 留痕不得影响主链路
        _logger.warning("actor shadow trace failed (fail-open): %s", e)


def trace_actor_merge(*, target=None, source=None, speaker_type=None, source_id=None,
                      source_message_sender=None, content=None) -> None:
    """埋点②（并入现场）：进来的那句话按统一语义该归为谁 vs 被并进去那行现在记的是谁。

    并入是新内容**唯一**的消失现场（不产生新行，落库后那句话就没了），所以这里单独留一条痕。
    同样只打 INFO；只读 ``target`` 上已有的列值，不查库、不刷新、不改动 ORM 对象。
    """
    try:
        j = judge_actor(source=source, speaker_type=speaker_type,
                        source_message_sender=source_message_sender, source_id=source_id)
        actual = getattr(target, "speaker_type", None)
        _logger.info(
            "actor shadow %s: into=%s src=%s incoming=%s unified=%s basis=%s target_speaker=%s "
            "diff=%s text=%.20s",
            SITE_MERGE, getattr(target, "id", None), source, speaker_type,
            j.actor, j.basis, actual, j.differs_from(actual), (content or ""),
        )
    except Exception as e:  # pragma: no cover - 留痕不得影响主链路
        _logger.warning("actor shadow merge trace failed (fail-open): %s", e)


__all__ = [
    "ActorJudgment", "judge_actor", "trace_actor_write", "trace_actor_merge",
    "BASIS_PERCEPTION", "BASIS_SPEAKER_TYPE", "BASIS_SOURCE_MESSAGE", "BASIS_NEED_SOURCE_MESSAGE",
    "BASIS_SELF_NARRATIVE", "BASIS_TOOL_SOURCE", "BASIS_DEFAULT_USER", "BASIS_VALUES",
    "SITE_WRITE", "SITE_MERGE",
]
