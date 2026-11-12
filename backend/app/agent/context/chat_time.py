# -*- coding: utf-8 -*-
"""C7：本轮聊天时间锚点（正常/轻量上下文共用）。

锚点口径（固定，不再读 ``ChatSession.updated_at``）：

- 同一 ``user_id × character_id`` 的**私聊会话**中，``sender_type='user'`` 的消息；
- 有当前消息 source 时：严格早于 source 的 ``(created_at, id)``（同秒按 id 稳定排序）；
- 无 source 时：使用**请求入口捕获的最后一条用户发言快照**（历史时间 + 历史身份），
  解析时不再按可复用的 ``chat_messages`` ROWID 重查；
- AI 回复、主动消息、会话排序/已读更新都不算“用户发言”。

无 source 入口先查最后一条用户消息，再取参考时刻；快照只记录当时已经存在的历史事实。
因此删除最高行后晚到消息即使复用旧 ROWID，也不会改变快照；解析只用快照时间计算间隔。
任何失败/异常（缺 caller、无历史、查询失败、未来时间）都返回非 ``ok`` 状态；调用方省略
子句，绝不把当前轮或未知值渲染成“刚刚/错误天数”。本模块只读
``chat_messages`` / ``chat_sessions``，不写库、不调模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select

from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc, to_naive_utc

_logger = get_logger("agent.context.chat_time")

STATUS_OK = "ok"
STATUS_NO_HISTORY = "no_history"
STATUS_MISSING_CALLER = "missing_caller"
STATUS_MISSING_CONTEXT = "missing_context"
STATUS_MISSING_BOUNDARY = "missing_boundary"
STATUS_INVALID_SOURCE = "invalid_source"
STATUS_QUERY_FAILED = "query_failed"
STATUS_FUTURE_DATA = "future_data"


@dataclass(frozen=True)
class ChatTimeAnchor:
    """一次锚点判定的结果；``status == ok`` 时 ``gap_seconds`` 才可用于展示。"""

    status: str
    previous_message_id: int | None = None
    previous_at: datetime | None = None
    reference_at: datetime | None = None
    gap_seconds: int | None = None

    @property
    def has_gap(self) -> bool:
        return self.status == STATUS_OK and self.gap_seconds is not None


async def capture_request_anchor(user_id, character_id) -> dict:
    """在无 source 请求入口捕获“最后一条用户发言”的稳定快照。

    返回键与 ``AgentState`` 字段同名：

    - ``chat_time_reference_at``：查询完成后拍的请求参考时刻（UTC naive）；
    - ``chat_time_anchor_snapshot``：``{status, message_id, created_at}``；无历史为
      ``status=no_history``，查询失败为 ``status=query_failed``，缺 caller/角色为 ``None``。

    先查最后一条用户消息、再取参考时刻：快照只可能包含查询时刻已存在的历史行；查询之后
    新插入的消息（包括删除最高行后复用 ROWID 的晚到消息）不会进入快照。解析阶段不再按
    ROWID 回查，因此编号复用无法穿越请求边界。
    """
    reference_at = now_naive_utc()
    if user_id is None or character_id is None:
        # 缺 caller/角色：不发起任何用户数据查询；由 resolve 返回 missing_caller/context。
        return {"chat_time_reference_at": reference_at, "chat_time_anchor_snapshot": None}

    try:
        from app.db.database import async_session_factory
        from app.models.chat import ChatMessage, ChatSession

        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(ChatMessage.id, ChatMessage.created_at)
                    .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                    .where(
                        ChatSession.user_id == user_id,
                        ChatSession.character_id == character_id,
                        ChatMessage.sender_type == "user",
                    )
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(1)
                )
            ).first()

        reference_at = now_naive_utc()
        if row is None or row[1] is None:
            snapshot = {"status": STATUS_NO_HISTORY}
        else:
            snapshot = {
                "status": STATUS_OK,
                "message_id": int(row[0]),
                "created_at": to_naive_utc(row[1]),
            }
        return {"chat_time_reference_at": reference_at, "chat_time_anchor_snapshot": snapshot}
    except Exception as e:
        _logger.warning("C7 request anchor capture failed: %s", e)
        return {
            "chat_time_reference_at": now_naive_utc(),
            "chat_time_anchor_snapshot": {"status": STATUS_QUERY_FAILED},
        }


async def resolve_chat_time_anchor(state: dict) -> ChatTimeAnchor:
    """解析“本轮之前的上次用户发言”。

    source 优先取 ``chat_time_source_id``（只服务时间锚点，不改变 ``source_id`` 的记忆语义）；
    没有时回退既有 ``source_id``。两者都没有时使用入口快照，绝不按当前 ROWID 回查。
    """
    user_id = state.get("user_id")
    character_id = state.get("character_id")
    if user_id is None:
        return ChatTimeAnchor(STATUS_MISSING_CALLER)
    if character_id is None:
        return ChatTimeAnchor(STATUS_MISSING_CONTEXT)

    source_id = state.get("chat_time_source_id")
    if source_id is None:
        source_id = state.get("source_id")

    try:
        if source_id is not None:
            return await _from_source(state, user_id, character_id, source_id)
        return _from_snapshot(state)
    except Exception as e:
        _logger.warning("C7 chat time anchor failed: %s", e)
        return ChatTimeAnchor(STATUS_QUERY_FAILED)


def _from_snapshot(state: dict) -> ChatTimeAnchor:
    """使用请求入口快照；不访问数据库，因此 ROWID 复用不影响结果。"""
    reference_at = to_naive_utc(state.get("chat_time_reference_at"))
    snapshot = state.get("chat_time_anchor_snapshot")
    if reference_at is None or not isinstance(snapshot, dict):
        return ChatTimeAnchor(STATUS_MISSING_BOUNDARY)

    status = snapshot.get("status")
    if status == STATUS_OK:
        previous_at = to_naive_utc(snapshot.get("created_at"))
        if previous_at is None:
            return ChatTimeAnchor(STATUS_NO_HISTORY, reference_at=reference_at)
        raw_id = snapshot.get("message_id")
        try:
            previous_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            previous_id = None
        return _build_ok(previous_id, previous_at, reference_at)
    if status in (STATUS_NO_HISTORY, STATUS_QUERY_FAILED):
        return ChatTimeAnchor(status, reference_at=reference_at)
    return ChatTimeAnchor(STATUS_MISSING_BOUNDARY, reference_at=reference_at)


async def _from_source(
    state: dict, user_id, character_id, source_id,
) -> ChatTimeAnchor:
    """校验 source 归属后，取严格早于它的最后一条用户消息。"""
    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession

    try:
        source_id_int = int(source_id)
    except (TypeError, ValueError):
        return ChatTimeAnchor(STATUS_INVALID_SOURCE)

    session_id = state.get("session_id")
    async with async_session_factory() as db:
        source_conds = [
            ChatMessage.id == source_id_int,
            ChatMessage.sender_type == "user",
            ChatSession.user_id == user_id,
            ChatSession.character_id == character_id,
        ]
        if session_id is not None:
            source_conds.append(ChatMessage.session_id == session_id)
        source_row = (
            await db.execute(
                select(ChatMessage.created_at)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(*source_conds)
            )
        ).first()
        if source_row is None or source_row[0] is None:
            return ChatTimeAnchor(STATUS_INVALID_SOURCE)
        reference_at = to_naive_utc(source_row[0])
        reference_jd = func.julianday(reference_at)

        previous_row = (
            await db.execute(
                select(ChatMessage.id, ChatMessage.created_at)
                .join(ChatSession, ChatMessage.session_id == ChatSession.id)
                .where(
                    ChatSession.user_id == user_id,
                    ChatSession.character_id == character_id,
                    ChatMessage.sender_type == "user",
                    # SQLite 的 server_default 时间串可能没有微秒，而绑定参数固定带 .000000；
                    # 直接字符串比较会把同秒当前消息误判为更早。julianday 归一后按真实时刻比较。
                    or_(
                        func.julianday(ChatMessage.created_at) < reference_jd,
                        and_(
                            func.julianday(ChatMessage.created_at) == reference_jd,
                            ChatMessage.id < source_id_int,
                        ),
                    ),
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(1)
            )
        ).first()

    if previous_row is None or previous_row[1] is None:
        return ChatTimeAnchor(STATUS_NO_HISTORY, reference_at=reference_at)
    return _build_ok(int(previous_row[0]), previous_row[1], reference_at)


def _build_ok(
    previous_message_id: int | None, previous_at, reference_at,
) -> ChatTimeAnchor:
    previous_at = to_naive_utc(previous_at)
    reference_at = to_naive_utc(reference_at)
    if previous_at is None or reference_at is None:
        return ChatTimeAnchor(
            STATUS_NO_HISTORY,
            previous_message_id=previous_message_id,
            previous_at=previous_at,
            reference_at=reference_at,
        )
    gap = (reference_at - previous_at).total_seconds()
    if gap < 0:
        # 未来时间/时钟异常：不钳成“刚刚”，整条子句省略，避免伪造回访。
        return ChatTimeAnchor(
            STATUS_FUTURE_DATA,
            previous_message_id=previous_message_id,
            previous_at=previous_at,
            reference_at=reference_at,
        )
    return ChatTimeAnchor(
        STATUS_OK,
        previous_message_id=previous_message_id,
        previous_at=previous_at,
        reference_at=reference_at,
        gap_seconds=int(gap),
    )


def format_gap_text(anchor: ChatTimeAnchor | None) -> str | None:
    """把锚点间隔渲染成既有“距上次互动”桶位；不可用时返回 None。"""
    if anchor is None or anchor.status != STATUS_OK or anchor.gap_seconds is None:
        return None
    secs = max(0, int(anchor.gap_seconds))
    if secs < 60:
        return "刚刚"
    if secs < 3600:
        return f"{secs // 60} 分钟前"
    if secs < 86400:
        hours, minutes = divmod(secs // 60, 60)
        return f"{hours} 小时 {minutes} 分钟前"
    if secs < 172800:
        return "昨天"
    if secs < 604800:
        return f"{secs // 86400} 天前"
    if secs < 2592000:
        return f"{secs // 604800} 周前"
    if secs < 31536000:
        return f"{secs // 2592000} 个月前"
    return "很久"


async def interaction_gap_text(state: dict, ctx: dict | None = None) -> str | None:
    """本轮“距上次用户发言”文案；同一 ctx 内只查一次。无有效锚点返回 None。"""
    anchor = None
    if isinstance(ctx, dict):
        anchor = ctx.get("_chat_time_anchor")
    if anchor is None:
        anchor = await resolve_chat_time_anchor(state)
        if isinstance(ctx, dict):
            ctx["_chat_time_anchor"] = anchor
    return format_gap_text(anchor)
