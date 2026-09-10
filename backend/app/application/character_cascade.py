# -*- coding: utf-8 -*-
"""删角色统一级联（v3.4.6 第二批，2026-09-09）。

背景：删角色原先是 application/characters.py 里手写的一长串 sa_delete（"打地鼠"），
51 张含 character_id 的表中漏了 character_state_history / life_* / weave_* / phone_* /
群聊 / 游戏 / 关系·情绪·剧情 / 隐私权限等域（只读库实测已产 463 条孤儿）。本模块把级联
集中成一张清单，删角色只调一次 cascade_delete_character，避免「有的表删两遍、有的没删」。

设计：
- CHARACTER_DELETE_SPECS：直接按「列 == character_id」物理删的私有/从属子表（子表在前、
  父表在后，未来开启 DB 级级联时同样安全）。
- 二级关联（朋友圈互动与评论子树、织库共享卡、游戏空局、群消息/群记忆、聊天派生表、
  AI 间私聊双列）需先取父 id 再清子表，见函数内各步骤。
- 所有删除都在调用方传入的同一个 db 事务内；**单表失败记 error 日志并向上抛**，不静默
  继续——级联缺口必须能被测试/线上报错暴露，而不是留下孤儿。
- 角色行本体（ai_characters）与向量库删除不在这里：前者由调用方删，后者非表删除。

显式**不删**（无 character_id 外键，不构成 FK 孤儿，属共享/日志类，留给数据治理批）：
agent_tasks / agent_task_logs / llm_usage / image_gen_tasks / channel_bindings /
lorebook_entries / world_facts / shared_events / prospective_intents / memory_archive 以外
的归档类。其中 moment_ai_likes 虽无外键，但属角色在他人动态下的互动，本模块一并清。
"""
from sqlalchemy import delete as sa_delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import EmotionCareTask, PendingPermissionAction
from app.models.character import (
    CharacterState,
    CharacterStateHistory,
    ProactiveMessageLog,
    ProactiveSettings,
    ProactiveStorylineItem,
    ProactiveTriggerLog,
    RelationshipEvent,
    StateTriggerLog,
    StorylineEvent,
)
from app.models.chat import (
    AIChat,
    ChatGroupMember,
    ChatGroupMessage,
    ChatMessage,
    ChatSession,
    GroupMemory,
)
from app.models.device import (
    BrowserHistory,
    CalendarNote,
    CheckInRequest,
    MemoNote,
    PhoneDesktop,
    PhoneLayout,
)
from app.models.game import GameEvent, GameMemory, GamePlayer
from app.models.life import (
    AIDiary,
    AIMoment,
    LifeActivityLog,
    LifeArtifact,
    LifeChatIntent,
    LifeFollowup,
    LifeGoal,
    LifeInterest,
    LifeSchedule,
    LifeState,
    MomentAILike,
    MomentComment,
    MomentLike,
    ScheduledEvent,
    TimelineEvent,
)
from app.models.memory import (
    ConversationTopic,
    DailySummary,
    Memory,
    MemoryArchive,
    ProcessedExtraction,
    ReflectionLog,
    StageMemory,
    WeaveCard,
    WeaveCardCharacter,
    WeaveCardMemory,
)
from app.models.user import PrivacyRequest
from app.utils.logger import get_logger

_logger = get_logger("application.character_cascade")

# (模型, 过滤列名)：DELETE FROM <table> WHERE <col> = :character_id
CHARACTER_DELETE_SPECS = [
    (CharacterStateHistory, "character_id"),  # ★ 只读库 100 条孤儿的直接来源
    (CharacterState, "character_id"),
    # ── AI Life ──
    (LifeState, "character_id"),
    (LifeActivityLog, "character_id"),
    (LifeArtifact, "character_id"),
    (LifeInterest, "character_id"),
    (LifeGoal, "character_id"),
    (LifeSchedule, "character_id"),
    (LifeFollowup, "character_id"),
    (LifeChatIntent, "character_id"),
    (AIDiary, "character_id"),
    (TimelineEvent, "character_id"),
    # ── 主动/定时 ──
    (ProactiveSettings, "character_id"),
    (ProactiveStorylineItem, "character_id"),
    (ScheduledEvent, "character_id"),
    (ProactiveMessageLog, "character_id"),
    (ProactiveTriggerLog, "character_id"),
    # ── 关系 / 情绪 / 剧情 ──
    (RelationshipEvent, "character_id"),
    (StateTriggerLog, "character_id"),
    (StorylineEvent, "character_id"),
    # ── 记忆 ──
    (ConversationTopic, "character_id"),
    (StageMemory, "character_id"),
    (ReflectionLog, "character_id"),
    (MemoryArchive, "character_id"),
    (Memory, "character_id"),
    (WeaveCardCharacter, "character_id"),  # 织卡-角色关联（卡本体按共享语义单独处理）
    # ── 虚拟手机 ──
    (PhoneLayout, "character_id"),
    (PhoneDesktop, "character_id"),
    (CalendarNote, "character_id"),
    (MemoNote, "character_id"),
    (BrowserHistory, "character_id"),
    (CheckInRequest, "character_id"),
    # ── 群聊（成员身份 + 其群消息）──
    (ChatGroupMessage, "character_id"),
    (ChatGroupMember, "character_id"),
    # ── 游戏（局内收摊见函数末尾）──
    (GameMemory, "character_id"),
    (GamePlayer, "character_id"),
    # ── 待审权限（session_id 非空 FK → chat_sessions，须先于会话删，避免未来 FK 强制下 RESTRICT）──
    (PendingPermissionAction, "character_id"),
    # ── 会话（派生表先清，见函数内步骤）──
    (ChatSession, "character_id"),
    # ── 情绪关怀 / 隐私申请 ──
    (EmotionCareTask, "character_id"),
    (PrivacyRequest, "character_id"),
]


async def cascade_delete_character(db: AsyncSession, character_id: int) -> dict:
    """在同一事务内删除角色的全部从属数据，返回各表删除计数（便于日志/测试断言）。

    注意：不删 ai_characters 本体，也不碰向量库——由调用方负责。
    """
    stats: dict[str, int] = {}

    async def _exec(stmt, label: str) -> int:
        """统一执行 + 计数；单条失败记日志并向上抛（级联完整性优先于「尽力删」）。"""
        try:
            rp = await db.execute(stmt)
        except Exception as e:
            _logger.error("cascade delete failed step=%s char=%s: %s", label, character_id, e)
            raise
        n = int(rp.rowcount or 0)
        stats[label] = stats.get(label, 0) + n
        return n

    async def _del(model, *where) -> int:
        return await _exec(sa_delete(model).where(*where), model.__name__)

    async def _ids(stmt) -> list:
        return list((await db.execute(stmt)).scalars().all())

    async def _collect_comment_ids(base_where) -> list:
        """取待删评论 id 并向下展开回复子树（moment_comments.parent_id 自引用外键，
        删中间节点会把子回复变成孤儿）。"""
        ids = set(await _ids(select(MomentComment.id).where(base_where)))
        while ids:
            children = set(await _ids(select(MomentComment.id).where(MomentComment.parent_id.in_(ids))))
            if not children - ids:
                break
            ids |= children
        return sorted(ids)

    # 1) 朋友圈：TA 发的动态 → 赞/AI 赞/评论；再清 TA 作为 AI 发出的评论与点赞
    moment_ids = await _ids(select(AIMoment.id).where(AIMoment.character_id == character_id))
    if moment_ids:
        await _del(MomentLike, MomentLike.moment_id.in_(moment_ids))
        await _del(MomentAILike, MomentAILike.moment_id.in_(moment_ids))
        comment_ids = await _collect_comment_ids(MomentComment.moment_id.in_(moment_ids))
        if comment_ids:
            await _del(MomentComment, MomentComment.id.in_(comment_ids))
        await _del(AIMoment, AIMoment.character_id == character_id)
    await _del(MomentAILike, MomentAILike.character_id == character_id)
    ai_comment_ids = await _collect_comment_ids(
        (MomentComment.sender_type == "ai") & (MomentComment.sender_id == character_id)
    )
    if ai_comment_ids:
        await _del(MomentComment, MomentComment.id.in_(ai_comment_ids))

    # 2) 织库：先摘「织卡↔记忆」关联（二级孤儿来源，必须在删 Memory 之前），再处理卡片本体
    memory_ids = await _ids(select(Memory.id).where(Memory.character_id == character_id))
    if memory_ids:
        await _del(WeaveCardMemory, WeaveCardMemory.memory_id.in_(memory_ids))
    for card_id in await _ids(select(WeaveCard.id).where(WeaveCard.character_id == character_id)):
        others = await _ids(
            select(WeaveCardCharacter.character_id).where(
                WeaveCardCharacter.card_id == card_id,
                WeaveCardCharacter.character_id != character_id,
            )
        )
        if others:
            # 共享卡：不删卡本体，把归属转给仍关联的另一角色（实库 weave_cards.character_id
            # 为 NOT NULL，置 NULL 会直接 500；模型层已声明 ondelete=SET NULL 待未来迁移可空后生效）
            await _exec(
                update(WeaveCard).where(WeaveCard.id == card_id).values(character_id=min(others)),
                "WeaveCard.reassigned",
            )
        else:
            await _del(WeaveCardMemory, WeaveCardMemory.card_id == card_id)
            await _del(WeaveCardCharacter, WeaveCardCharacter.card_id == card_id)
            await _del(WeaveCard, WeaveCard.id == card_id)

    # 3) 群聊：只清该角色署名的群记忆条目，群共享记忆（system 汇总）整群保留
    await _del(
        GroupMemory,
        GroupMemory.speaker_type.in_(("ai", "character")),
        GroupMemory.speaker_id == character_id,
    )

    # 4) AI 间私聊（双列）
    await _del(
        AIChat,
        or_(AIChat.character_a_id == character_id, AIChat.character_b_id == character_id),
    )

    # 5) 聊天派生：先按会话删提取/消息/日摘要（会话本体在第 7 步按 character_id 删）
    session_ids = await _ids(select(ChatSession.id).where(ChatSession.character_id == character_id))
    if session_ids:
        await _del(
            ProcessedExtraction,
            ProcessedExtraction.user_message_id.in_(
                select(ChatMessage.id).where(ChatMessage.session_id.in_(session_ids))
            ),
        )
        await _del(ChatMessage, ChatMessage.session_id.in_(session_ids))
        await _del(DailySummary, DailySummary.session_id.in_(session_ids))

    # 6) 游戏：先记下该角色参与过的局（第 7 步会删 GamePlayer，届时无从回溯）
    game_session_ids = await _ids(select(GamePlayer.session_id).where(GamePlayer.character_id == character_id))

    # 7) 通用：按 character_id 直删
    for model, col in CHARACTER_DELETE_SPECS:
        await _del(model, getattr(model, col) == character_id)

    # 8) 游戏局收摊：仍有玩家的局整局保留；空局清事件与记忆
    #    （GameSession 本体无 character_id，不构成孤儿，留作历史对局可追溯）
    for gid in sorted(set(game_session_ids)):
        left = (await db.execute(select(GamePlayer.id).where(GamePlayer.session_id == gid).limit(1))).first()
        if left is None:
            await _del(GameEvent, GameEvent.session_id == gid)
            await _del(GameMemory, GameMemory.session_id == gid)

    return stats
