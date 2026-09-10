# -*- coding: utf-8 -*-
"""删角色统一级联回归夹具（v3.4.6 第二批，2026-09-09）。

造一个「什么域都玩过」的角色 → 走 DELETE /api/v1/characters/{id} → 断言：
1) 开启 FK 的独立连接跑 PRAGMA foreign_key_check 为空（不留孤儿）；
2) 被删角色的私有数据各表归零；
3) 共享数据不误删（他角色记忆/动态/群共享记忆、多角色共享织卡、仍有玩家的游戏局）。

临时 SQLite：与 tests/test_device_api.py 同范式（临时文件库 + create_all + TestClient
override get_db/get_current_user_id）；向量库与「离开记忆」走全局会话，用 monkeypatch 隔离。
"""
import asyncio
import sqlite3

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient

from app.api import characters as characters_api
from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.base import Base

USER_ID = 9001
CHAR_A = 9101  # 被删角色
CHAR_B = 9102  # 存活角色（共享织卡 / 同群 / 同局游戏 / AI 私聊对方）


async def _noop(*_args, **_kwargs):
    return None


@pytest.fixture()
def cascade_env(tmp_path, monkeypatch):
    """临时 SQLite 文件库（create_all 建表）+ 隔离全局副作用。"""
    db_path = tmp_path / "cascade.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models._all  # noqa: F401  保证全部模型注册进 Base.metadata
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    # 向量库与「离开记忆」用的是全局会话/全局向量目录，不参与本用例断言
    monkeypatch.setattr("app.db.vector_store.delete_memory_vectors_by_character", _noop)
    monkeypatch.setattr("app.memory.save_memory", _noop)
    yield factory, str(db_path)
    engine.sync_engine.dispose()


def _client(factory, user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(characters_api.router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id

    async def _db_dep():
        async with factory() as s:
            try:
                yield s
                await s.commit()  # 与 get_db 一致：提交后才能在文件库上做 foreign_key_check
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_db] = _db_dep
    return TestClient(app)


async def _seed(factory) -> dict:
    """为 CHAR_A 在每个域造最小数据；返回断言要用的 id。"""
    from datetime import datetime

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
    from app.models.chat import AIChat, ChatGroup, ChatGroupMember, ChatGroupMessage, ChatMessage, ChatSession, GroupMemory
    from app.models.device import BrowserHistory, CalendarNote, CheckInRequest, MemoNote, PhoneDesktop, PhoneLayout
    from app.models.game import GameEvent, GameMemory, GamePlayer, GameSession
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
    from app.models.user import PrivacyRequest, User

    from app.application.characters import AICharacter

    now = datetime(2026, 9, 9, 12, 0, 0)
    ids: dict = {}
    async with factory() as db:
        db.add(User(id=USER_ID, username="cascade_u", nickname="级联用户"))
        db.add(AICharacter(id=CHAR_A, user_id=USER_ID, name="级联待删A"))
        db.add(AICharacter(id=CHAR_B, user_id=USER_ID, name="级联存活B"))
        await db.flush()

        # ── 会话 / 消息 / 日摘要 / 提取记录 ──
        sess = ChatSession(user_id=USER_ID, character_id=CHAR_A)
        db.add(sess)
        await db.flush()
        msg = ChatMessage(session_id=sess.id, sender_type="user", content="你好")
        db.add(msg)
        await db.flush()
        db.add(ProcessedExtraction(user_message_id=msg.id))
        db.add(DailySummary(session_id=sess.id, summary_date="2026-09-09", summary_text="摘要"))
        ids["session_id"] = sess.id

        # ── 八维状态 / 历史 / 关系 / 触发 / 剧情 / 主动 / 时光轴 / 日记 / 定时承诺 ──
        db.add(CharacterState(character_id=CHAR_A))
        db.add(CharacterStateHistory(character_id=CHAR_A, source="eval"))
        db.add(RelationshipEvent(character_id=CHAR_A, user_id=USER_ID, event="一起看电影"))
        db.add(StateTriggerLog(character_id=CHAR_A, trigger_key="anger_high"))
        db.add(StorylineEvent(character_id=CHAR_A, storyline_key="cold_war"))
        db.add(ProactiveSettings(character_id=CHAR_A))
        db.add(ProactiveStorylineItem(
            character_id=CHAR_A, session_id=sess.id, user_id=USER_ID,
            group_id="grp-1", content="在吗", send_at=now))
        db.add(ProactiveMessageLog(character_id=CHAR_A, message_type="proactive"))
        db.add(ProactiveTriggerLog(character_id=CHAR_A, trigger_type="idle"))
        db.add(TimelineEvent(character_id=CHAR_A, event_date="2026-09-09", title="第一次旅行"))
        db.add(AIDiary(character_id=CHAR_A, diary_date="2026-09-09", content="今天很开心"))
        db.add(ScheduledEvent(user_id=USER_ID, character_id=CHAR_A, session_id=sess.id, trigger_at=now))

        # ── 记忆 / 归档 / 话题 / 舞台 / 复盘 ──
        mem_a = Memory(user_id=USER_ID, character_id=CHAR_A, memory_type="event", content="A 的记忆")
        db.add(mem_a)
        mem_b = Memory(user_id=USER_ID, character_id=CHAR_B, memory_type="event", content="B 的记忆")
        db.add(mem_b)
        await db.flush()
        db.add(MemoryArchive(memory_id=mem_a.id, user_id=USER_ID, character_id=CHAR_A, payload="{}"))
        db.add(ConversationTopic(character_id=CHAR_A, user_id=USER_ID, topic="旅行计划"))
        db.add(StageMemory(user_id=USER_ID, character_id=CHAR_A, content="角色扮演"))
        db.add(ReflectionLog(character_id=CHAR_A, user_id=USER_ID))

        # ── AI Life ──
        db.add(LifeState(character_id=CHAR_A))
        db.add(LifeActivityLog(character_id=CHAR_A, activity_type="rest"))
        db.add(LifeArtifact(user_id=USER_ID, character_id=CHAR_A, type="text"))
        db.add(LifeInterest(character_id=CHAR_A, name="摄影"))
        db.add(LifeGoal(character_id=CHAR_A, title="学会烘焙"))
        db.add(LifeSchedule(user_id=USER_ID, character_id=CHAR_A, title="看书", start_time=now))
        db.add(LifeFollowup(character_id=CHAR_A, user_id=USER_ID, summary="刚烤了饼干"))
        db.add(LifeChatIntent(character_id=CHAR_A, user_id=USER_ID, action_type="rest"))

        # ── 织库：A 独占卡 / B 归属且与 A 共享 / A 归属且与 B 共享 ──
        own_card = WeaveCard(user_id=USER_ID, character_id=CHAR_A, title="A独占卡",
                             summary="s", detail="{}", content_hash="h1")
        db.add(own_card)
        await db.flush()
        db.add(WeaveCardMemory(card_id=own_card.id, memory_id=mem_a.id))
        db.add(WeaveCardCharacter(card_id=own_card.id, character_id=CHAR_A))
        ids["own_card_id"] = own_card.id

        shared_card = WeaveCard(user_id=USER_ID, character_id=CHAR_B, title="B归属共享卡",
                                summary="s", detail="{}", content_hash="h2")
        db.add(shared_card)
        await db.flush()
        db.add(WeaveCardMemory(card_id=shared_card.id, memory_id=mem_a.id))
        db.add(WeaveCardMemory(card_id=shared_card.id, memory_id=mem_b.id))
        db.add(WeaveCardCharacter(card_id=shared_card.id, character_id=CHAR_A))
        db.add(WeaveCardCharacter(card_id=shared_card.id, character_id=CHAR_B))
        ids["shared_card_id"] = shared_card.id

        both_card = WeaveCard(user_id=USER_ID, character_id=CHAR_A, title="A归属共享卡",
                              summary="s", detail="{}", content_hash="h3")
        db.add(both_card)
        await db.flush()
        db.add(WeaveCardMemory(card_id=both_card.id, memory_id=mem_b.id))
        db.add(WeaveCardCharacter(card_id=both_card.id, character_id=CHAR_A))
        db.add(WeaveCardCharacter(card_id=both_card.id, character_id=CHAR_B))
        ids["both_card_id"] = both_card.id

        # ── 虚拟手机 ──
        db.add(PhoneDesktop(character_id=CHAR_A, wallpaper="w"))
        db.add(PhoneLayout(character_id=CHAR_A, app_key="calendar"))
        db.add(CalendarNote(character_id=CHAR_A, note_date="2026-09-09", note_text="记得买花"))
        db.add(MemoNote(character_id=CHAR_A, text="备忘"))
        db.add(BrowserHistory(character_id=CHAR_A, query="附近花店"))
        db.add(CheckInRequest(user_id=USER_ID, character_id=CHAR_A))

        # ── 群聊 ──
        grp = ChatGroup(user_id=USER_ID, name="家庭群")
        db.add(grp)
        await db.flush()
        db.add(ChatGroupMember(group_id=grp.id, character_id=CHAR_A))
        db.add(ChatGroupMember(group_id=grp.id, character_id=CHAR_B))
        db.add(ChatGroupMessage(group_id=grp.id, sender_type="ai", character_id=CHAR_A, content="A 发言"))
        db.add(ChatGroupMessage(group_id=grp.id, sender_type="ai", character_id=CHAR_B, content="B 发言"))
        db.add(GroupMemory(group_id=grp.id, user_id=USER_ID,
                           speaker_type="character", speaker_id=CHAR_A, content="A 的群记忆"))
        db.add(GroupMemory(group_id=grp.id, user_id=USER_ID,
                           speaker_type="system", speaker_id=None, content="群共享记忆"))
        ids["group_id"] = grp.id

        # ── 游戏：多玩家局（B 仍在）+ A 独占的空局 ──
        gs = GameSession(user_id=USER_ID, group_id=grp.id, game_type="undercover", player_mode="multi")
        db.add(gs)
        await db.flush()
        db.add(GamePlayer(session_id=gs.id, player_type="ai", character_id=CHAR_A, seat=0))
        db.add(GamePlayer(session_id=gs.id, player_type="ai", character_id=CHAR_B, seat=1))
        db.add(GameEvent(session_id=gs.id, event_type="deal"))
        db.add(GameMemory(session_id=gs.id, character_id=CHAR_A))
        db.add(GameMemory(session_id=gs.id, character_id=CHAR_B))
        ids["game_session_id"] = gs.id

        gs2 = GameSession(user_id=USER_ID, game_type="twenty_q", player_mode="single")
        db.add(gs2)
        await db.flush()
        db.add(GamePlayer(session_id=gs2.id, player_type="ai", character_id=CHAR_A, seat=0))
        db.add(GameEvent(session_id=gs2.id, event_type="ask"))
        db.add(GameMemory(session_id=gs2.id, character_id=CHAR_A))
        ids["game_session_empty_id"] = gs2.id

        # ── 朋友圈：A 的动态（含赞/评论）；A 在 B 动态下的点赞与评论（评论带子回复）──
        m_a = AIMoment(character_id=CHAR_A, user_id=USER_ID, sender_type="ai", content="A 的动态")
        db.add(m_a)
        m_b = AIMoment(character_id=CHAR_B, user_id=USER_ID, sender_type="ai", content="B 的动态")
        db.add(m_b)
        await db.flush()
        db.add(MomentLike(moment_id=m_a.id, user_id=USER_ID))
        db.add(MomentAILike(moment_id=m_a.id, character_id=CHAR_B))
        db.add(MomentAILike(moment_id=m_b.id, character_id=CHAR_A))
        db.add(MomentComment(moment_id=m_a.id, sender_type="user", sender_id=USER_ID,
                             sender_name="用户", content="好看"))
        c_a = MomentComment(moment_id=m_b.id, sender_type="ai", sender_id=CHAR_A,
                            sender_name="A", content="沙发")
        db.add(c_a)
        await db.flush()
        db.add(MomentComment(moment_id=m_b.id, sender_type="user", sender_id=USER_ID,
                             sender_name="用户", content="回复A", parent_id=c_a.id))
        ids["moment_b_id"] = m_b.id

        # ── AI 间私聊 / 情绪关怀 / 待审权限 / 隐私申请 ──
        db.add(AIChat(user_id=USER_ID, character_a_id=CHAR_A, character_b_id=CHAR_B,
                      speaker_id=CHAR_A, content="悄悄话"))
        db.add(EmotionCareTask(user_id=USER_ID, character_id=CHAR_A, due_at=now))
        db.add(PendingPermissionAction(user_id=USER_ID, session_id=sess.id, character_id=CHAR_A,
                                       scope="image_gen", action="{}"))
        db.add(PrivacyRequest(character_id=CHAR_A, user_id=USER_ID, target_type="diary"))
        await db.commit()
    return ids


async def _count(db, model, **filters) -> int:
    stmt = select(func.count()).select_from(model)
    for col, val in filters.items():
        stmt = stmt.where(getattr(model, col) == val)
    return int((await db.execute(stmt)).scalar() or 0)


def test_delete_character_leaves_no_orphan(cascade_env):
    factory, db_path = cascade_env
    ids = asyncio.run(_seed(factory))

    # 删角色：走真实接口（application/characters.delete_character → 统一级联）
    with _client(factory, USER_ID) as cli:
        r = cli.delete(f"/api/v1/characters/{CHAR_A}")
    assert r.status_code == 204, r.text

    # 1) 开启 FK 的独立连接体检：不得残留任何孤儿
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys=ON")
    leftovers = raw.execute("PRAGMA foreign_key_check").fetchall()
    raw.close()
    assert leftovers == [], f"删角色后仍有孤儿: {leftovers}"

    async def _check():
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
        from app.models.chat import AIChat, ChatGroupMember, ChatGroupMessage, ChatMessage, ChatSession
        from app.models.device import BrowserHistory, CalendarNote, CheckInRequest, MemoNote, PhoneDesktop, PhoneLayout
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

        from app.application.characters import AICharacter

        async with factory() as db:
            # 2) 被删角色私有数据必须归零（含本次补漏的全部表）
            for model in (
                CharacterState, CharacterStateHistory, RelationshipEvent, StateTriggerLog,
                StorylineEvent, ProactiveSettings, ProactiveStorylineItem, ProactiveMessageLog,
                ProactiveTriggerLog, TimelineEvent, AIDiary, ScheduledEvent,
                LifeState, LifeActivityLog, LifeArtifact, LifeInterest, LifeGoal,
                LifeSchedule, LifeFollowup, LifeChatIntent,
                ConversationTopic, StageMemory, ReflectionLog, Memory, MemoryArchive,
                WeaveCardCharacter, PhoneDesktop, PhoneLayout, CalendarNote, MemoNote,
                BrowserHistory, CheckInRequest, ChatGroupMessage, ChatGroupMember,
                GameMemory, GamePlayer, ChatSession, EmotionCareTask,
                PendingPermissionAction, PrivacyRequest, AIMoment,
            ):
                assert await _count(db, model, character_id=CHAR_A) == 0, f"{model.__name__} 未清干净"
            assert await _count(db, AICharacter, id=CHAR_A) == 0
            assert await _count(db, ChatMessage, session_id=ids["session_id"]) == 0
            assert await _count(db, DailySummary, session_id=ids["session_id"]) == 0
            assert await _count(db, ProcessedExtraction, user_message_id=1) == 0
            assert await _count(db, AIChat, character_a_id=CHAR_A) == 0
            assert await _count(db, AIChat, character_b_id=CHAR_A) == 0
            assert await _count(db, MomentLike, moment_id=1) == 0
            assert await _count(db, MomentAILike, character_id=CHAR_A) == 0
            assert await _count(db, MomentComment, sender_id=CHAR_A) == 0

            # 3) 共享数据不误删
            assert await _count(db, AICharacter, id=CHAR_B) == 1
            assert await _count(db, Memory, character_id=CHAR_B) == 1
            assert await _count(db, AIMoment, character_id=CHAR_B) == 1
            assert await _count(db, MomentComment, moment_id=ids["moment_b_id"]) == 0  # A 评论连子树
            assert await _count(db, ChatGroupMember, character_id=CHAR_B) == 1
            assert await _count(db, ChatGroupMessage, character_id=CHAR_B) == 1
            from app.models.chat import GroupMemory
            assert await _count(db, GroupMemory, speaker_type="system") == 1  # 群共享记忆
            assert await _count(db, GroupMemory, speaker_id=CHAR_A) == 0      # 角色署名条目
            # 多玩家局：B 仍在 → 局/事件/B 的游戏记忆保留
            assert await _count(db, GamePlayer, session_id=ids["game_session_id"]) == 1
            assert await _count(db, GameEvent, session_id=ids["game_session_id"]) == 1
            assert await _count(db, GameMemory, session_id=ids["game_session_id"]) == 1
            # A 独占的空局：事件与记忆收摊
            assert await _count(db, GamePlayer, session_id=ids["game_session_empty_id"]) == 0
            assert await _count(db, GameEvent, session_id=ids["game_session_empty_id"]) == 0
            assert await _count(db, GameMemory, session_id=ids["game_session_empty_id"]) == 0
            # 织库：A 独占卡连带关联删除；两张共享卡本体保留（归属 B），A 的关联摘除
            assert await _count(db, WeaveCard, id=ids["own_card_id"]) == 0
            assert await _count(db, WeaveCard, id=ids["shared_card_id"]) == 1
            assert await _count(db, WeaveCard, id=ids["both_card_id"]) == 1
            shared = await db.get(WeaveCard, ids["shared_card_id"])
            both = await db.get(WeaveCard, ids["both_card_id"])
            assert shared.character_id == CHAR_B
            assert both.character_id == CHAR_B  # A 归属的共享卡转给仍关联的 B（实库 NOT NULL 不置空）
            assert await _count(db, WeaveCardCharacter, character_id=CHAR_A) == 0
            assert await _count(db, WeaveCardMemory, card_id=ids["shared_card_id"]) == 1  # 只掉 A 的记忆关联
            assert await _count(db, WeaveCardMemory, card_id=ids["both_card_id"]) == 1

    asyncio.run(_check())
