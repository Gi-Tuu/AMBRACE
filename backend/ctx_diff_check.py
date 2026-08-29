# -*- coding: utf-8 -*-
"""AMBRACE 步骤5 字节级差分核查（独立脚本，不被 pytest 收集）。

对比 build_context（注册表/flag-on 主路径）与 build_context_legacy（内联/flag-off 回退）
在【同一 state】上产出的 state["context_messages"] 是否逐字节一致。

用法：
    cd backend
    .venv\\Scripts\\python.exe ctx_diff_check.py

说明：flag-on 主路径（app.agent.context.build_context）跑注册表 section 后委托
build_context_legacy（携 `_section_values`）；flag-off 直接 build_context_legacy 内联计算。
两者都必须逐字节一致才算迁移成功。
"""
import asyncio
import copy
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.agent.context_builder as cb
from app.agent.context.section_memories import (
    _memory_char_rounds as _char_rounds,
    _memory_inject_rounds as _inject_rounds,
)


def _reset_memory_state():
    _char_rounds.clear()
    _inject_rounds.clear()


async def _seed(factory):
    from sqlalchemy import select

    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat_session import ChatSession
    from app.models.chat_message import ChatMessage
    from app.models.memory import Memory
    from app.models.moment import AIMoment
    from app.models.pet import Pet
    from app.models.character_state import CharacterState
    from app.models.proactive_settings import ProactiveSettings
    from app.models.weave_card import WeaveCard
    from app.models.shared_event import SharedEvent
    from app.models.chat_group import ChatGroup, ChatGroupMember, ChatGroupMessage
    from app.models.lorebook_entry import LorebookEntry

    async with factory() as db:
        db.add(User(id=1, username="alice", nickname="Alice", gender="female",
                    location_enabled=True, location_city="杭州", ai_location="杭州",
                    timezone_offset_minutes=480))
        db.add(AICharacter(
            id=100, user_id=1, name="小爱", gender="female",
            personality="温柔体贴，善解人意", chat_style="亲切自然，像闺蜜",
            bio="一个温柔的女孩", self_statement="我是小爱，你的朋友",
            relationship_summary="亲密的朋友", current_status="正在聊天",
            cognitive_loop_enabled=True, memory_v2_enabled=True, is_active=True,
        ))
        db.add(ChatSession(id=500, user_id=1, character_id=100, is_active=True,
                           updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(ChatMessage(session_id=500, sender_type="user", content="今天天气怎么样？",
                           created_at=now - timedelta(minutes=10)))
        db.add(ChatMessage(session_id=500, sender_type="ai", content="挺好的呀，想出去走走吗？",
                           created_at=now - timedelta(minutes=5)))
        db.add(ChatMessage(session_id=500, sender_type="user", content="好呀，那就散步去。",
                           created_at=now - timedelta(minutes=1)))
        # 更早的历史（日摘要）
        db.add(ChatMessage(session_id=500, sender_type="user", content="上周去爬山好开心",
                           created_at=now - timedelta(days=10)))
        db.add(ChatMessage(session_id=500, sender_type="ai", content="是呀，山顶的风景很美",
                           created_at=now - timedelta(days=10)))
        # 检索区记忆
        db.add(Memory(id=900, user_id=1, character_id=100, memory_type="fact",
                      sub_type="preference", content="用户喜欢喝美式咖啡",
                      importance=60, is_archived=False, is_pinned=False,
                      created_at=now - timedelta(days=2),
                      speaker_type="user", speaker_id=1, epistemic_status="FACT"))
        db.add(Memory(id=901, user_id=1, character_id=100, memory_type="fact",
                      sub_type="location", content="用户住在杭州",
                      importance=55, is_archived=False, is_pinned=False,
                      created_at=now - timedelta(days=1),
                      speaker_type="user", speaker_id=1, epistemic_status="FACT"))
        # 核心记忆
        db.add(Memory(id=902, user_id=1, character_id=100, memory_type="fact",
                      sub_type="identity", content="用户最珍视的家庭",
                      importance=100, is_archived=False, is_pinned=True,
                      is_core=True, core_category="identity",
                      created_at=now - timedelta(days=3), epistemic_status="FACT"))
        # 关系锚点（用 get_relationship_anchors 查询的字段）
        db.add(Memory(id=903, user_id=1, character_id=100, memory_type="event",
                      content="一起在西湖边看过日落", importance=90, is_archived=False,
                      created_at=now - timedelta(days=5), epistemic_status="FACT"))
        # 朋友圈
        db.add(AIMoment(id=700, character_id=100, user_id=1, sender_type="ai",
                        content="今天的花开得真好", is_active=True,
                        created_at=now - timedelta(hours=3)))
        db.add(AIMoment(id=701, character_id=100, user_id=1, sender_type="user",
                        content="周末要去打羽毛球", is_active=True,
                        created_at=now - timedelta(days=1)))
        # 宠物
        db.add(Pet(id=800, user_id=1, name="毛球", species="cat", hunger=70, mood=60,
                   energy=80, cleanliness=75, status_text="状态不错", level=3, exp=30,
                   owner_type="user", owner_id=None,
                   created_at=now - timedelta(days=30)))
        # 角色状态（八维 + 关系标量）
        db.add(CharacterState(id=1, character_id=100, mood=65, body_temp=50, desire=40,
                              possessiveness=55, fatigue=35, sensitivity=60, comfort=70,
                              anger=20, trust=75, attachment=80, curiosity=66))
        # overlay 分区数据（织库/群聊/生图/共同经历/lorebook 触发表）
        db.add(ProactiveSettings(character_id=100, weave_full_inject_enabled=True,
                                 image_gen_enabled=True, active_image_gen_enabled=False,
                                 life_share_enabled=True))
        db.add(WeaveCard(id=600, user_id=1, character_id=100, title="一起爬山", summary="一起爬过黄山看日出",
                         detail="{}", importance=90, content_hash="abcdef", domain="shared", is_stale=False))
        db.add(SharedEvent(id=1, user_id=1, character_id=100, event_type="milestone", category="anniversary",
                           title="恋爱纪念日", description="一起过的第一个纪念日", importance=0.9))
        db.add(ChatGroup(id=1, user_id=1, name="家庭群聊"))
        db.add(ChatGroupMember(group_id=1, character_id=100))
        db.add(ChatGroupMessage(id=1, group_id=1, sender_type="user", character_id=None, content="周末去钓鱼啊"))
        db.add(LorebookEntry(id=1, user_id=1, character_id=100, title="天气设定", content="用户怕冷，冬天出门提醒加衣",
                             keywords='["天气"]', exclude_keywords="[]", active=True))
        await db.commit()


async def _run_build(factory, state, use_registry: bool):
    from app.agent.loop import AGENT_FLAGS
    from app.agent import context_builder

    def _state_fresh():
        s = {}
        s.update({k: copy.deepcopy(v) for k, v in state.items()})
        return s

    if use_registry:
        AGENT_FLAGS["agent_context_registry"] = True
        out = await context_builder.build_context(_state_fresh())
    else:
        AGENT_FLAGS["agent_context_registry"] = False
        out = await context_builder.build_context(_state_fresh())
    return out["context_messages"]


async def _main(factory):
    await _seed(factory)
    base_state = {
        "user_id": 1,
        "character_id": 100,
        "session_id": 500,
        "user_message": "帮我画张天气图",
        "retrieved_memories": [],
        "context_messages": [],
        "character_info": {},
        "ai_response": "",
        "lang": "zh",
        "reasoning_level": 1,
        "cognitive_loop_enabled": True,
        "perception": {"emotion": "开心", "intent": "chat"},
        "continue_payload": {"last_ai_content": "我们周末一起去看海吧"},
    }

    results = []
    for label in ["flag_on(registry)", "flag_off(inline)"]:
        _reset_memory_state()
        msgs = await _run_build(factory, base_state, use_registry=(label.startswith("flag_on")))
        results.append((label, msgs))

    (l1, m1) = results[0]
    (l2, m2) = results[1]
    import os as _os
    if _os.environ.get("CTX_DIFF_DUMP"):
        print("==== flag_off message[0] content ====")
        print(m2[0]["content"])
        print("==== end ====")
    same = m1 == m2
    print(f"[{'PASS' if same else 'FAIL'}] {l1} vs {l2}: {len(m1)} msgs vs {len(m2)} msgs")
    if not same:
        for i, (a, b) in enumerate(zip(m1, m2)):
            if a != b:
                print(f"  message[{i}] DIFF:")
                print(f"    on : {a.get('content')!r}")
                print(f"    off: {b.get('content')!r}")
        if len(m1) != len(m2):
            print(f"  length mismatch: on={len(m1)} off={len(m2)}")
    else:
        # 再确认逐字节相同的总长度
        print(f"  byte identical: total_chars on={sum(len(m['content']) for m in m1)}"
              f" off={sum(len(m['content']) for m in m2)}")
    return same


def main():
    tmp = tempfile.mkdtemp(prefix="ctx_diff_")
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    # 把 temp engine 串到全项目所有捕获了 async_session_factory 的模块引用
    import app.db.database as _dbmod
    _original = _dbmod.async_session_factory
    _dbmod.async_session_factory = factory
    for _name, _m in list(sys.modules.items()):
        if getattr(_m, "async_session_factory", None) is _original:
            try:
                setattr(_m, "async_session_factory", factory)
            except Exception:
                pass
    cb.async_session_factory = factory

    same = asyncio.run(_main(factory))
    asyncio.run(engine.dispose())
    sys.exit(0 if same else 1)


if __name__ == "__main__":
    main()
