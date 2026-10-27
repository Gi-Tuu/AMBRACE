"""Consume leased memory work without depending on another chat turn."""
import asyncio

from app.memory import extract_queue as queue


async def _processed(source_id):
    if source_id is None:
        return False
    from app.memory import extractor as ex
    async with ex.async_session_factory() as db:
        return await db.get(ex.ProcessedExtraction, source_id) is not None


async def _with_heartbeat(pair, operation):
    async def heartbeat():
        while True:
            await asyncio.sleep(queue.LEASE_SECONDS / 3)
            if not queue.renew(pair):
                raise RuntimeError("extraction_lease_lost")

    work = asyncio.create_task(operation)
    keeper = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait([work, keeper], return_when=asyncio.FIRST_COMPLETED)
        if keeper in done:
            await keeper
        return await work
    finally:
        for task in (keeper, work):
            if not task.done():
                task.cancel()
        await asyncio.gather(keeper, work, return_exceptions=True)


async def _load_identity(ex, session_id):
    """从真实数据库加载会话/角色/账号；三者缺任一返回 None。"""
    from app.models.chat import ChatSession
    from app.models.character import AICharacter
    from app.models.user import User

    async with ex.async_session_factory() as db:
        session = await db.get(ChatSession, session_id)
        if session is None:
            return None, None, None
        char = await db.get(AICharacter, session.character_id)
        user = await db.get(User, session.user_id)
        return session, char, user


def _identity_disposition(session, char, user):
    """身份核验口径。

    返回 None 表示可提取；"retain_disabled" 表示账号临时禁用，保留载荷与队列
    状态、不入死队，恢复 active 后继续消费；其余字符串为明确入死原因。
    家庭共享角色的合法会话不强制 char.user_id == session.user_id，故不在此处比较。
    """
    if session is None:
        return "missing_session"
    if char is None:
        return "missing_character"
    if not session.is_active:
        return "inactive_session"
    if not char.is_active:
        return "inactive_character"
    if user is None:
        return "missing_user"
    if getattr(user, "deleted_at", None) is not None:
        return "deleted_user"
    if getattr(user, "disabled_at", None) is not None:
        return "retain_disabled"
    return None


async def _source_belongs_to_session(ex, session, source_id):
    """source_id 必为本会话的 user 消息；缺源/AI 源/异会话源皆不属。"""
    from app.models.chat import ChatMessage

    async with ex.async_session_factory() as db:
        msg = await db.get(ChatMessage, source_id)
    return (
        msg is not None
        and getattr(msg, "sender_type", None) == "user"
        and getattr(msg, "session_id", None) == session.id
    )


async def process_session(session_id, character_id, user_id):
    from app.agent.internal_runner import run_internal
    from app.memory import extractor as ex

    # 两入口均按真实数据库重新核验会话/角色/账号；run_internal 所用
    # user_id/character_id 以数据库会话行为准，绝不采信调用方传入值。
    session, _char, _user = await _load_identity(ex, session_id)
    disposition = _identity_disposition(session, _char, _user)
    if disposition == "retain_disabled":
        # 临时禁用：保留载荷与队列状态，不调模型、不写 ProcessedExtraction；
        # 恢复 active 后由 drain 重新消费。
        return
    if disposition is not None:
        # deleted / missing / 会话或角色不活跃：明确入死，不吞成功、不写 ProcessedExtraction。
        for pair in queue.take(session_id):
            queue.abandon(pair, disposition)
        return

    pairs = queue.take(session_id)
    if pairs:
        ex._mark_batch_taken(session.character_id)
    for pair in pairs:
        try:
            if not queue.renew(pair):
                continue
            source_id = pair.get("source_id")
            # 无 source_id 保留旧兼容语义，但会话身份已核验；有 source_id 时须确
            # 为本会话的 user 消息，缺源/AI 源/异会话源不得调模型或写标记。
            if source_id is not None and not await _source_belongs_to_session(ex, session, source_id):
                queue.abandon(pair, "invalid_source")
                continue
            if not await _processed(source_id):
                result = await _with_heartbeat(pair, run_internal("memory_extract", {
                    "session_id": session.id,
                    "character_id": session.character_id,
                    "user_id": session.user_id,
                    "user_msg": pair["user_message"],
                    "ai_msg": pair["ai_response"],
                    "source_id": source_id,
                }, character_id=session.character_id, user_id=session.user_id))
                if not isinstance(result, dict) or result.get("status") != "ok":
                    raise RuntimeError("extraction_tool_failed")
                if source_id is not None and not await _processed(source_id):
                    raise RuntimeError("extraction_not_committed")
            queue.complete(pair)
        except asyncio.CancelledError:
            # Leave the payload leased: a replacement process can reclaim it after expiry.
            raise
        except Exception as error:
            queue.retry(pair, type(error).__name__)
            ex._logger.warning("Extraction task failed task=%s kind=%s", pair["_task_id"], type(error).__name__)


async def drain_due():
    from app.memory import extractor as ex

    for session_id in queue.pending_sessions(ex.MAX_PENDING_AGE):
        try:
            if session_id == 0:
                await _recover_legacy_inflight()
                continue
            session, char, user = await _load_identity(ex, session_id)
            disposition = _identity_disposition(session, char, user)
            if disposition == "retain_disabled":
                # 临时禁用：保留载荷与状态，恢复 active 后继续消费，不吞成功。
                continue
            if disposition is not None:
                # deleted / missing / 会话或角色不活跃：明确入死，不吞成功。
                for pair in queue.take(session_id):
                    queue.abandon(pair, disposition)
                continue
            await process_session(session.id, session.character_id, session.user_id)
        except Exception as error:
            ex._logger.warning("Extraction drain failed session=%s kind=%s", session_id, type(error).__name__)


async def _recover_legacy_inflight():
    from sqlalchemy import select
    from app.memory import extractor as ex

    for pair in queue.take(0):
        try:
            async with ex.async_session_factory() as db:
                user = await db.get(ex.ChatMessage, pair["source_id"])
                ai = None
                if user is not None and user.sender_type == "user":
                    ai = (await db.execute(select(ex.ChatMessage).where(
                        ex.ChatMessage.session_id == user.session_id,
                        ex.ChatMessage.id > user.id, ex.ChatMessage.sender_type == "ai",
                    ).order_by(ex.ChatMessage.id).limit(1))).scalar_one_or_none()
            if user is None or ai is None:
                queue.abandon(pair, "legacy_source_unavailable")
                continue
            queue.recover_payload(pair, user.session_id, {
                "user_message": user.content, "ai_response": ai.content,
                "source_id": user.id, "ts": pair["ts"],
            })
        except Exception as error:
            queue.retry(pair, type(error).__name__)
