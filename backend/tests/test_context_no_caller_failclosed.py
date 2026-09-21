# -*- coding: utf-8 -*-
"""contextB1 + contextB2 + contextB3 + contextB4 + contextB5 + contextB6：43 处 ``state.get("user_id", 1)`` 改 fail-closed 的回归测试（2026-09-21）。

背景（docs/plans.md §六 B 家族）：context 装配 section 里纯查询型 ``state.get("user_id", 1)``
已改为 ``state.get("user_id")``。主聊天路径 ``state["user_id"]`` 恒为真实账号 → 零行为变化；
只有「宿主拿不到 caller（键缺失）」时，旧写法会把这一轮**冒充 1 号账号**（跨账号注入），
改后 ``state.get("user_id")`` → ``None`` → SQL ``== NULL``（IS NULL）→ 查不到 → 不注入该账号数据
（fail-closed，语义是「静默不注入」而非「报错」）。

覆盖第一批（B1，直接调用 6 处所在的真实函数，不 mock 业务逻辑）：
1. ``section_world._compute_current_time_str``（:66 最近会话查询）
2. ``section_world._load_user``（:111 取用户行）
3. ``section_summaries._load_char_and_user``（:37 取用户行）
4. ``section_persona.user_info_section``（:113 取用户行，强制走 except 兜底分支）
5. ``section_overlay._ensure_reasoning_names``（:437 取用户行）
6. ``section_moments.moments_section``（:38 用户动态查询）

覆盖第二批（B2，用例编号 7–16，对应派单 10 处）：
7. ``section_persona:65`` 手动八维 → 8. ``:106`` 用户画像 → 9. ``:120`` 备忘录/日记
10. ``section_world:123`` 世界事实 → 11. ``:142`` 定时承诺 → 12. ``section_phone:25`` 手机感知
13. ``section_mcp:213`` 工具声明（按派单授权退化为 ``owned_server_ids``）→ 14. ``:225`` 资源摘要
15. ``section_current_state:18`` 现状锚点 → 16. ``section_working_state:109`` 工作记忆。

覆盖第三批（B3，用例编号 17–22，``context/legacy.py`` 内联装配 12 处）：
17/18 是**装配级**对照（直跑 ``build_context_legacy`` 的注册表内联分支），一次覆盖 :120 用户行 /
:268 世界事实 / :307 用户朋友圈 / :389 手动八维 / :407 手机感知 / :433 时间承诺 / :474 距上次互动 /
:550 用户画像 / :556 备忘录+日记 / :867 AI 生活记忆（信任概率门打桩强开）。:664 工具声明与 :673
资源摘要只在 ``_section_values=None`` 的纯 legacy 分支执行，该分支会连带走排除项 :143 → 19/20 用
装配级对照覆盖这两行（:143 打桩成九槽全空，只中和未改动的它），21/22 再直调 ``_build_mcp_*_text``
钉死下游 None 语义。排除项 :105/:143/:284/:333 本批未改。

覆盖第四批（B4，用例编号 23–26，B 家族纯查询型收尾 4 处）：23. ``section_overlay:133``「AI 生活」
注入（信任概率门打桩强开，复用 B3 的 CharacterState(trust=70) + Memory(source="life") 种子）
→ 24. ``section_user_now:25``（敏感槽 ``user_fact_relationship`` 显式打开；缺 caller 时早退判据仍
回全局值，真正的 fail-closed 落在 ``get_active_user_facts`` 的 ``user_id IS NULL``）→
25. ``nodes:106``（唯一下游 ``load_active_goal_queries``，直调钉死）→ 26. ``runtime:190``
（直调 ``build_light_social_context``，正/反双证：关系锚点 + 「距上次互动」会话过滤）。
23–26 的新增读取方（``topic_tracker`` / ``memory.core`` 是模块级 ``from … import``）走
``_patch_extra_readers``——只在本批用例生效，不动 ``_patch_session``，以免改变 17/18 装配用例的读库范围。

覆盖第五批（B5，用例编号 27–33，剩余 7 处「无下游风险」调用点）：本批验收口径与前四批**不同**——
27/28（persona）、29（curated）经逐下游核实是 **no-op**（user_id 形参根本没进 SQL），故断言的是
「带 caller 与缺 caller **逐字符相等**」（等值 + 非空双证），不是「缺 caller 必消失」；
30（align）断言 None → 空 report；31（memory 任务 LLM 配置）断言缺 caller 时**不借用 1 号的 BYOK**；
32/33（热度裁剪）断言 None → 判低频 → 落到具体低频档数值（保守方向、无跨账号泄漏）。

覆盖第六批（B6，用例编号 36–40，B 家族收尾 4 处「混合语义」）：36 直调 ``_inject_core_anchors_loops``
钉行为（同一函数里「账号那半边」锚点/计时 fail-closed、「角色那半边」核心记忆/生活目标**必须仍在**）；
37/38/40 是**调用点连线**（:284 / :172 / :333——B5 教训：只测下游变异不红）；39 走 ``pets_section``
section 入口，40 走 legacy 内联真实查询，两侧口径必须一致。本批做完，B 家族累计 43 处，
``app/agent/**`` 活代码里的 ``state.get("user_id", 1)`` 归零。

防假绿：每例「先正跑（带 caller → 必见哨兵）再反跑（缺 caller → 必不见哨兵且**不抛异常**）」。
哨兵：B1 User(1).nickname=SENTINEL_USER、AIMoment=SENTINEL_MOMENT；
B2 见 ``fc_db`` 文档字符串（每处一个独立 SENTINEL_* 行）；
B3 追加 CharacterState(trust=70) + Memory(source="life")=SENTINEL_LIFE（随机概率门在用例内打桩强开）。
B5 追加种子一律**不带 SENTINEL_ 前缀**（CURATEDB5_/HOTMSG_B5/…），以免撞 18 例的
``"SENTINEL_" not in text`` 全局判据。

（项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时库走 tests/_dbclone.py，绝不碰生产库。）
"""
import asyncio

import pytest

from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py 注册（与同族测试一致）

_NO_CALLER = object()  # 传给 _state(user_id=...) 表示「宿主 state 里根本没有 user_id 键」


@pytest.fixture(scope="session")
def fc_db(tmp_path_factory):
    """会话级临时库（模板库克隆）：User(1)=SENTINEL_USER + AIMoment/ChatSession 等哨兵行。

    哨兵 user 动态故意置 ``character_id=None``：``moments_section`` 的 own 查询按 character_id
    过滤（不看 sender_type），若不置 None 会被 own 查询捞到、正/反跑都含哨兵 → 测不到 fail-closed。

    contextB2 追加种子（每处一行、各自独立哨兵，互不覆盖 B1 依赖的上面几条）：
    UserState(mood=80) / UserMemo+UserDiary / WorldFact(character,SENTINEL_FACT)
    / ScheduledEvent(pending) / PhoneSnapshot / WorldFact(user 现状) / Memory(working_state)
    / MCPServer（#7 归属正证）。

    contextB3 追加种子：CharacterState(character 13, trust=70) 抬开「AI 生活」注入的信任门槛；
    Memory(source="life")=SENTINEL_LIFE 供 legacy:867（概率门在用例内打桩强开）。

    contextB4 追加种子（三条，均为**新增行**，不动 B1–B3 依赖的哨兵）：
    GlobalUserFact(slot="relationship")=SENTINEL_USERNOW —— 刻意选敏感槽（relationship/health
    不吃总闸旁路、默认关），故只有本批用例显式打开 flag 时才可见，不会漏进 15/17/18 的现状锚点；
    ConversationTopic=GOALTGT_B4、Memory(importance=88, event)=ANCHORTGT_B4 —— 这两处**故意不用
    SENTINEL_ 前缀**：``load_active_topics_text``（persona active_topics）与 ``get_relationship_anchors``
    （section_memories:172 仍是未迁移的 ``state.get("user_id", 1)``）都按 character 过滤或仍冒充 1 号，
    用 SENTINEL_ 命名会被 18 例的 ``"SENTINEL_" not in text`` 全局断言判为串号。

    contextB5 追加种子（四组，**一律只带 B5 标记、不带 SENTINEL_ 前缀**，不动前四批哨兵）：
    WorldFact(kind="constraint")=CURATEDB5_ —— 用例 29 编纂知识层的唯一数据源（该查询只按 character 过滤）；
    GlobalUserFact(slot="job", previous_value)=JOBB5_ —— 用例 30 对齐报表（job 槽默认关，用例内显式开）；
    TaskLlmConfig(user 1, task="memory")=USERB5_MODEL —— 用例 31「借谁的 BYOK」（base_url 指向本机
    discard 端口 9，绝不会被真调用；即便被调用也是立即失败并落进静默兜底）；
    ChatSession(1,13) + 30 条 HOTMSG_B5 消息 —— 用例 32/33 高频判据（**独立会话**，避免把消息灌进
    17/18 用例所读的 session 1 而改变装配文本）。

    contextB6 追加种子（core/anchors/loops 三槽 + pets 四处调用点的数据源，标记一律含 B6、
    **不含 SENTINEL_**，理由同 B4/B5：18 例有 ``"SENTINEL_" not in text`` 全局判据）：
    User(2)=U2B6 —— 另一账号（既是外键宿主，也作「不得串号」的反向哨兵归属）；
    Memory(is_core, cid=13)=COREB6 —— 核心记忆只按 character 过滤，缺 caller 时**必须仍在**；
    Memory(importance=85/84, event)=ANCHORB6_一号/二号 —— 锚点按 user_id 过滤，正证只见 1 号、反证全空；
    LifeGoal(cid=13, active)=GOALB6 —— 开放循环里的「角色自身」部分，缺 caller 时仍应注入；
    ScheduledEvent(user 1 / user 2, pending, 未到期)=TIMERB6 —— 计时承诺按 user_id 过滤；
    Pet 四条 —— 1 号用户宠物 / 1 号无归属旧数据 / 2 号用户宠物 / 角色自养 AI 宠物（owner_id=13，
    其 user_id 也非空，故「缺 caller 只剩它」正好证明第 3 分支没被顺带掐掉）。
    """
    db_file = (tmp_path_factory.mktemp("failclosed") / "fc.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from datetime import timedelta

        from app.models.agent import TaskLlmConfig
        from app.models.chat import ChatMessage, ChatSession
        from app.models.character import AICharacter, CharacterState
        from app.models.device import PhoneSnapshot
        from app.models.life import AIMoment, LifeGoal, ScheduledEvent, UserDiary, UserMemo
        from app.models.mcp import MCPServer
        from app.models.memory import ConversationTopic, Memory, WorldFact
        from app.models.pet import Pet
        from app.models.user import GlobalUserFact, User, UserState
        from app.utils.timeutil import now_naive_utc

        now = now_naive_utc()
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="SENTINEL_USER"))
            # B6 的第二账号（跨账号反向哨兵）：与 User(1) 一起放进第一次 flush，让所有
            # user_id=2 的子行不依赖同一次 flush 内的父/子插入顺序（曾触发 FK 约束失败）。
            db.add(User(id=2, username="u2", nickname="U2B6_二号账号"))
            db.add(AICharacter(
                id=13, user_id=1, name="酱", personality="温柔",
                chat_style="口语化", relation_type="朋友", is_active=True,
            ))
            db.add(AIMoment(
                user_id=1, character_id=None, sender_type="user",
                content="SENTINEL_MOMENT", is_active=True,
            ))
            sess = ChatSession(user_id=1, character_id=13)
            db.add(sess)
            await db.flush()  # 取 sess.id 供 ScheduledEvent 的非空 session_id 外键使用

            # ── contextB2 追加（下面每一项只服务一个用例的哨兵）──
            db.add(UserState(user_id=1, mood=80))  # 八维里一项 ≠ 50
            db.add(UserMemo(user_id=1, title="备忘", content="SENTINEL_MEMO"))
            db.add(UserDiary(
                user_id=1, diary_date=now.strftime("%Y-%m-%d"), content="SENTINEL_DIARY",
            ))
            db.add(WorldFact(  # world_facts 用（character 主语 + 非瞬时谓词，免新鲜窗干扰）
                user_id=1, character_id=13, subject_type="character", subject_id=13,
                predicate="setting", object_value="SENTINEL_FACT", status="active",
                audience='["public"]', asserted_at=now,
            ))
            db.add(WorldFact(  # current_state 锚点用（subject=user；audience 不含 char → 不漏进 world_facts）
                user_id=1, character_id=13, subject_type="user", subject_id=1,
                predicate="status", object_value="SENTINEL_NOW", status="active",
                audience='["user:1"]', asserted_at=now,
            ))
            db.add(ScheduledEvent(
                user_id=1, character_id=13, session_id=sess.id,
                trigger_at=now + timedelta(hours=2), status="pending",
                content_hint="SENTINEL_PROMISE", owner="ai",
            ))
            db.add(PhoneSnapshot(
                user_id=1, source="clipboard", content="SENTINEL_SNAP", created_at=now,
            ))
            db.add(Memory(
                user_id=1, character_id=13, memory_type="working_state", title="ws",
                content='{"ongoing": [{"topic": "SENTINEL_WS", "detail": "赶派单"}]}',
            ))
            db.add(MCPServer(user_id=1, name="sentinel", transport="stdio", command="echo"))

            # ── contextB3 追加 ──
            db.add(CharacterState(character_id=13, trust=70))  # AI 生活注入：trust≥70 → 概率 0.6
            db.add(Memory(
                user_id=1, character_id=13, memory_type="event", source="life",
                title="life", content="SENTINEL_LIFE", status="active",
            ))

            # ── contextB4 追加 ──
            db.add(GlobalUserFact(  # 敏感槽：默认关 → 只有本批用例显式开 flag 才读得到
                user_id=1, slot="relationship", value="SENTINEL_USERNOW",
                source="chat", confidence=1.0, valid_from=now,
            ))
            db.add(ConversationTopic(  # nodes:106 目标/未完成路
                character_id=13, user_id=1, topic="GOALTGT_B4", status="进行中",
            ))
            db.add(Memory(  # runtime:190 关系锚点：importance≥80 + memory_type∈(event, insight)
                user_id=1, character_id=13, memory_type="event", title="anchor",
                content="ANCHORTGT_B4", importance=88.0, status="active", is_archived=False,
            ))

            # ── contextB5 追加（本批 7 处调用点的数据源；标记一律含 B5、不含 SENTINEL_）──
            from app.agent.context_builder import HOT_THRESHOLD_7D_MSGS

            db.add(WorldFact(  # 用例 29：curated 层按 kind 取，查询里根本没有 user_id
                user_id=1, character_id=13, subject_type="character", subject_id=13,
                predicate="setting", object_value="CURATEDB5_不许夸大", status="active",
                kind="constraint", audience='["public"]', asserted_at=now,
            ))
            db.add(GlobalUserFact(  # 用例 30：previous_value 非空是进对齐报表的唯一判据
                user_id=1, slot="job", value="JOBB5_新工作", previous_value="JOBB5_旧工作",
                source="chat", confidence=1.0, valid_from=now,
            ))
            db.add(TaskLlmConfig(  # 用例 31：1 号为 memory 任务配的 BYOK（缺 caller 时不得借用）
                user_id=1, task="memory", base_url="http://127.0.0.1:9/v1",
                api_key="sk-user-b5", model="USERB5_MODEL", enabled=True,
            ))
            hot_sess = ChatSession(user_id=1, character_id=13)  # 用例 32/33：高频证据放独立会话
            db.add(hot_sess)
            await db.flush()
            for i in range(HOT_THRESHOLD_7D_MSGS):
                db.add(ChatMessage(
                    session_id=hot_sess.id, sender_type="user",
                    content=f"HOTMSG_B5_{i}", created_at=now,
                ))

            # ── contextB6 追加（core/anchors/loops + pets 四处调用点；标记含 B6、不含 SENTINEL_）──
            db.add(Memory(  # 核心记忆：get_core_memories 只按 character_id → 缺 caller 也必须保住
                user_id=1, character_id=13, memory_type="user_info", sub_type="hobby",
                title="core", content="COREB6_核心记忆", importance=100.0,
                is_core=True, core_category="preference", status="active", is_archived=False,
            ))
            db.add(Memory(  # 关系锚点（1 号）：importance≥80 且 memory_type∈(event, insight)
                user_id=1, character_id=13, memory_type="event", title="anchor_u1",
                content="ANCHORB6_一号锚点", importance=85.0, status="active", is_archived=False,
            ))
            db.add(Memory(  # 关系锚点（2 号、同角色）：任何一侧都不得注入 → 证明过滤本身没坏
                user_id=2, character_id=13, memory_type="event", title="anchor_u2",
                content="ANCHORB6_二号锚点", importance=84.0, status="active", is_archived=False,
            ))
            db.add(LifeGoal(  # 开放循环「角色自身」半边：只按 character_id + status
                character_id=13, type="growth", title="GOALB6_目标", status="active", priority=3,
            ))
            db.add(ScheduledEvent(  # 开放循环「账号」半边：未到期计时承诺（1 号）
                user_id=1, character_id=13, session_id=sess.id,
                trigger_at=now + timedelta(hours=3), status="pending",
                content_hint="TIMERB6_一号计时", owner="ai",
            ))
            db.add(ScheduledEvent(  # 同上（2 号）
                user_id=2, character_id=13, session_id=sess.id,
                trigger_at=now + timedelta(hours=4), status="pending",
                content_hint="TIMERB6_二号计时", owner="ai",
            ))
            db.add(Pet(user_id=1, name="PETB6_咪咪", species="cat", owner_type="user"))
            db.add(Pet(user_id=1, name="PETB6_无归属", species="rabbit", owner_type=None))  # 分支1 旧数据
            db.add(Pet(user_id=2, name="PET2B6_旺财", species="dog", owner_type="user"))  # 跨账号反向哨兵
            db.add(Pet(user_id=1, name="AIPETB6_团子", species="gecko", owner_type="ai", owner_id=13))
            await db.commit()

    asyncio.run(_seed())
    yield factory
    asyncio.run(engine.dispose())


def _patch_session(monkeypatch, factory) -> None:
    """把临时库会话工厂指向所有读取方（含「模块级 from … import」已绑死名字的模块）。

    只 patch ``app.db.database`` 会漏掉 user_profile/facts/promise_service/phone_service/
    ownership/user_facts 这 6 处模块级绑定，以及 legacy/context_builder 两处（B3 装配层直跑要用）
    ——漏 patch 时它们读到 conftest 的空沙箱库，正跑必然失败（不会假绿），故一次性全部指向 fc_db。
    """
    for path in (
        "app.db.database.async_session_factory",
        "app.agent.context_builder.async_session_factory",
        "app.agent.context.legacy.async_session_factory",
        "app.agent.user_profile.async_session_factory",
        "app.events.facts.async_session_factory",
        "app.scheduling.promise_service.async_session_factory",
        "app.application.phone_service.async_session_factory",
        "app.mcp.ownership.async_session_factory",
        "app.memory.user_facts.async_session_factory",
    ):
        monkeypatch.setattr(path, factory)


def _patch_extra_readers(monkeypatch, factory) -> None:
    """B4 追加的两个读取方（同为模块级 ``from … import``，绑死了旧名字）。

    刻意**不并进** ``_patch_session``：那会让 17/18 的装配用例把 core/anchors/话题也读到 fc_db，
    无谓扩大既有 22 例的行为面；本批用例只多 patch 自己真正用到的模块。
    """
    for path in (
        "app.agent.topic_tracker.async_session_factory",
        "app.memory.core.async_session_factory",
    ):
        monkeypatch.setattr(path, factory)


def _patch_b5_readers(monkeypatch, factory) -> None:
    """B5 追加的三个读取方（persona / topic_tracker / cross_char_sync 同为模块级 ``from … import``）。

    与 ``_patch_extra_readers`` 同理刻意不并进 ``_patch_session``：只在本批用例生效，
    不改既有 26 例的读库范围（storyline_engine / facts / llm_client 是函数内 import，``_patch_session`` 已覆盖）。
    """
    for path in (
        "app.agent.persona.async_session_factory",
        "app.agent.topic_tracker.async_session_factory",
        "app.memory.cross_char_sync.async_session_factory",
    ):
        monkeypatch.setattr(path, factory)


def _state(user_id=1) -> dict:
    """装配 state：始终带 character_id/session_id（section 直接下标取值）；user_id 三态。

    ``_NO_CALLER`` → 不写 user_id 键（模拟宿主拿不到 caller，而非置 None——置 None 会让
    其它 ``state.get("user_id", 1)`` 分支读到 None，混进无关差异）。
    """
    st = {"character_id": 13, "session_id": 1}
    if user_id is not _NO_CALLER:
        st["user_id"] = user_id
    return st


# ───────────────────────────────────────────── 1. world :66 最近会话查询


def test_world_current_time_last_interaction_failclosed(fc_db, monkeypatch):
    """带 caller → 「距上次互动」注入；缺 caller → 会话查不到（不冒充 1 号）且该串消失。"""
    from app.agent.context import section_world as w
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)
    pos = asyncio.run(w._compute_current_time_str(_state(1), {}))
    assert "距上次互动" in pos, "带 caller 时未注入「距上次互动」→ 下面的反证会是空断言"
    neg = asyncio.run(w._compute_current_time_str(_state(_NO_CALLER), {}))  # 不抛异常
    assert "距上次互动" not in neg, "缺 caller 时把这一轮冒充成了 1 号账号的最近会话"


# ───────────────────────────────────────────── 2. world :111 取用户行


def test_world_load_user_failclosed(fc_db, monkeypatch):
    """_load_user：带 caller → 拿到 SENTINEL_USER；缺 caller → None（下游按无用户兜底）。"""
    from app.agent.context import section_world as w
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)
    user = asyncio.run(w._load_user(_state(1), {}))
    assert user is not None and user.nickname == "SENTINEL_USER"
    user2 = asyncio.run(w._load_user(_state(_NO_CALLER), {}))  # 不抛异常
    assert user2 is None, "缺 caller 时查到了 1 号账号的行（应 fail-closed 返回 None）"


# ───────────────────────────────────────────── 3. summaries :37 取用户行


def test_summaries_load_char_and_user_failclosed(fc_db, monkeypatch):
    """_load_char_and_user：带 caller → 用户行是 SENTINEL_USER；缺 caller → 用户行 None（角色行仍在）。"""
    from app.agent.context import section_summaries as s
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)
    char, user = asyncio.run(s._load_char_and_user(_state(1)))
    assert char is not None and user is not None and user.nickname == "SENTINEL_USER"
    char2, user2 = asyncio.run(s._load_char_and_user(_state(_NO_CALLER)))  # 不抛异常
    assert user2 is None, "缺 caller 时 summaries 侧把用户行冒充成了 1 号账号"


# ───────────────────────────────────────────── 4. persona :113 取用户行（except 兜底分支）


def test_persona_user_info_failclosed(fc_db, monkeypatch):
    """user_info_section：强制 build_user_profile_text 抛错 → 落到 :113 的兜底查询。

    带 caller → 产出含 SENTINEL_USER；缺 caller → 兜底为「用户」，不含 SENTINEL_USER。
    同时把 build_user_notes_text 打桩为 "" 以隔离，避免其越界读库。
    """
    import app.agent.user_profile as up
    from app.agent.context import section_persona as p
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)

    async def _boom(*_a, **_k):
        raise RuntimeError("force :113 fallback")

    async def _notes(*_a, **_k):
        return ""

    monkeypatch.setattr(up, "build_user_profile_text", _boom)
    monkeypatch.setattr(up, "build_user_notes_text", _notes)
    pos = asyncio.run(p.user_info_section(_state(1), {}))
    assert "SENTINEL_USER" in pos, "带 caller 时兜底查询未取到 1 号昵称 → 反证会是空断言"
    neg = asyncio.run(p.user_info_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_USER" not in neg, "缺 caller 时 persona 侧把用户昵称冒充成了 1 号账号"


# ───────────────────────────────────────────── 5. overlay :437 取用户行


def test_overlay_reasoning_names_failclosed(fc_db, monkeypatch):
    """_ensure_reasoning_names：带 caller → 写回 user_name=SENTINEL_USER；缺 caller → 不写 user_name。"""
    from app.agent.context import section_overlay as o
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)
    pos = _state(1)
    asyncio.run(o._ensure_reasoning_names(pos))
    assert pos.get("user_name") == "SENTINEL_USER", "带 caller 时未解析出 1 号昵称 → 反证会是空断言"
    neg = _state(_NO_CALLER)
    asyncio.run(o._ensure_reasoning_names(neg))  # 不抛异常
    assert neg.get("user_name") != "SENTINEL_USER", "缺 caller 时 overlay 侧把 user_name 冒充成了 1 号账号"


# ───────────────────────────────────────────── 6. moments :38 用户动态查询


def test_moments_user_feed_failclosed(fc_db, monkeypatch):
    """moments_section：带 caller → 输出含 SENTINEL_MOMENT；缺 caller → 用户动态查不到（输出「暂无」）。"""
    from app.agent.context import section_moments as m
    from app.db import database as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", fc_db)
    pos = asyncio.run(m.moments_section(_state(1), {}))
    assert "SENTINEL_MOMENT" in pos, "带 caller 时未注入用户动态 → 反证会是空断言"
    neg = asyncio.run(m.moments_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_MOMENT" not in neg, "缺 caller 时把这一轮的用户动态冒充成了 1 号账号"


# ══════════════════════════ contextB2 第二批（10 处，2026-09-21）══════════════════════════


# ───────────────────────────────────── 7. persona :65 手动八维状态查询


def test_persona_user_manual_state_failclosed(fc_db, monkeypatch):
    """user_manual_state_section：带 caller → 注入「心情80」；缺 caller → 查不到行 → 空串。"""
    from app.agent.context import section_persona as p

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(p.user_manual_state_section(_state(1), {}))
    assert "心情80" in pos, "带 caller 时未注入 1 号的手动八维 → 反证会是空断言"
    neg = asyncio.run(p.user_manual_state_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == "", "缺 caller 时把 1 号账号的手动状态冒充进来了"


# ───────────────────────────────────── 8. persona :106 用户画像（build_user_profile_text）


def test_persona_user_profile_failclosed(fc_db, monkeypatch):
    """user_info_section（画像段）：带 caller → 昵称 SENTINEL_USER；缺 caller → 退化为「用户」。"""
    from app.agent.context import section_persona as p

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(p.user_info_section(_state(1), {}))
    assert "SENTINEL_USER" in pos, "带 caller 时画像未取到 1 号昵称 → 反证会是空断言"
    neg = asyncio.run(p.user_info_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_USER" not in neg, "缺 caller 时把这一轮冒充成了 1 号账号的画像"


# ───────────────────────────────────── 9. persona :120 备忘录/日记（build_user_notes_text）


def test_persona_user_notes_failclosed(fc_db, monkeypatch):
    """user_info_section（笔记段）：带 caller → 含备忘录+日记哨兵；缺 caller → 两栏都是「无」。"""
    from app.agent.context import section_persona as p

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(p.user_info_section(_state(1), {}))
    assert "SENTINEL_MEMO" in pos and "SENTINEL_DIARY" in pos, "带 caller 时未注入备忘录/日记 → 反证会是空断言"
    neg = asyncio.run(p.user_info_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_MEMO" not in neg, "缺 caller 时把 1 号账号的备忘录冒充进来了"
    assert "SENTINEL_DIARY" not in neg, "缺 caller 时把 1 号账号的日记冒充进来了"


# ───────────────────────────────────── 10. world :123 世界事实（get_character_view）


def test_world_facts_failclosed(fc_db, monkeypatch):
    """world_facts_section：带 caller → 含 SENTINEL_FACT；缺 caller → 无事实 → 缺省「无」。"""
    from app.agent.context import section_world as w

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(w.world_facts_section(_state(1), {}))
    assert "SENTINEL_FACT" in pos, "带 caller 时未注入世界事实 → 反证会是空断言"
    neg = asyncio.run(w.world_facts_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_FACT" not in neg, "缺 caller 时把 1 号账号的世界事实冒充进来了"


# ───────────────────────────────────── 11. world :142 进行中时间承诺


def test_world_pending_timer_failclosed(fc_db, monkeypatch):
    """pending_timer_section：带 caller → 含 SENTINEL_PROMISE；缺 caller → 无承诺 → 缺省「无」。"""
    from app.agent.context import section_world as w

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(w.pending_timer_section(_state(1), {}))
    assert "SENTINEL_PROMISE" in pos, "带 caller 时未注入定时承诺 → 反证会是空断言"
    neg = asyncio.run(w.pending_timer_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_PROMISE" not in neg, "缺 caller 时把 1 号账号的定时承诺冒充进来了"


# ───────────────────────────────────── 12. phone :25 手机感知快照


def test_phone_perception_failclosed(fc_db, monkeypatch):
    """phone_perception_section：带 caller → 含 SENTINEL_SNAP；缺 caller → 无快照 → 缺省「无」。"""
    from app.agent.context import section_phone as ph

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(ph.phone_perception_section(_state(1), {}))
    assert "SENTINEL_SNAP" in pos, "带 caller 时未注入手机感知 → 反证会是空断言"
    neg = asyncio.run(ph.phone_perception_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "SENTINEL_SNAP" not in neg, "缺 caller 时把 1 号账号的手机采集内容冒充进来了"


# ───────────────────────────────────── 13. mcp :213 工具声明（按授权退化为归属集合）


def test_mcp_tools_owned_servers_failclosed(fc_db, monkeypatch):
    """:213 需要全局 mcp_manager 已连接态才能在单测里跑通声明，故按派单授权退化断言。

    正证：1 号名下确有 1 个 server（owned 非空 → 反证不是「恒空」的空断言）；
    反证：owned_server_ids(None) == set()（fail-closed，绝不退化成「不过滤」）。
    另跑一次真实 section 确认缺 caller 时既不抛异常也不产出块。
    """
    from app.agent.context import section_mcp as mc
    from app.mcp.ownership import owned_server_ids

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(owned_server_ids(1))
    assert pos and None not in pos, "种子 MCP server 未归属到 1 号 → 反证会是空断言"
    neg = asyncio.run(owned_server_ids(None))  # 不抛异常
    assert neg == set(), "user_id=None 时归属集合非空 → 等于放弃了多用户隔离（应为 fail-closed 空集）"
    assert asyncio.run(mc.mcp_tools_section(_state(_NO_CALLER), {})) == []


# ───────────────────────────────────── 14. mcp :225 资源摘要（真实 section 路径）


def test_mcp_resources_failclosed(fc_db, monkeypatch):
    """mcp_resources_section：注入一条属于 1 号的已连接 server 资源（替身只造连接态，不 mock 业务）。

    带 caller → 输出含 SENTINEL_RES；缺 caller → resources_for_user(None) 逐条 user_id != None 跳过
    → 无分组 → section 返回空列表（不注入）。
    """
    from types import SimpleNamespace

    from app.agent.context import section_mcp as mc
    from app.mcp.manager import mcp_manager

    _patch_session(monkeypatch, fc_db)
    conn = SimpleNamespace(
        user_id=1, server_id=9, server_name="sentinel", is_connected=True,
        resources=[{"uri": "file:///s", "name": "SENTINEL_RES", "mime_type": "text/plain", "description": ""}],
    )
    monkeypatch.setattr(mcp_manager, "_conns", {9: conn})
    pos = asyncio.run(mc.mcp_resources_section(_state(1), {}))
    assert len(pos) == 1 and "SENTINEL_RES" in pos[0], "带 caller 时未注入 MCP 资源 → 反证会是空断言"
    neg = asyncio.run(mc.mcp_resources_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == [], "缺 caller 时把 1 号账号的 MCP 资源冒充进来了"


# ───────────────────────────────────── 15. current_state :18 用户现状锚点


def test_current_state_anchor_failclosed(fc_db, monkeypatch):
    """current_state_section：带 caller → 锚点含 SENTINEL_NOW；缺 caller → 三源全空 → 不注入。"""
    from app.agent.context import section_current_state as cs

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(cs.current_state_section(_state(1), {}))
    assert len(pos) == 1 and "SENTINEL_NOW" in pos[0], "带 caller 时未注入用户现状 → 反证会是空断言"
    neg = asyncio.run(cs.current_state_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == [], "缺 caller 时把 1 号账号的现状锚点冒充进来了"


# ───────────────────────────────────── 16. working_state :109 工作记忆注入


def test_working_state_failclosed(fc_db, monkeypatch):
    """working_state_section（char13 在灰度白名单且比例=1.0，门必开）：带 caller → 含 SENTINEL_WS。

    缺 caller → get_latest(db, None, 13) 查不到行 → 返回空列表；同时直断该下游函数以钉死语义。
    """
    from app.agent.context import section_working_state as ws
    from app.application.working_state_service import get_latest

    _patch_session(monkeypatch, fc_db)
    assert ws.inject_allowed(13, 1), "char13 灰度门未开 → 下面的反证会是空断言"
    pos = asyncio.run(ws.working_state_section(_state(1), {}))
    assert len(pos) == 1 and "SENTINEL_WS" in pos[0], "带 caller 时未注入工作记忆 → 反证会是空断言"
    neg = asyncio.run(ws.working_state_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == [], "缺 caller 时把 1 号账号的工作记忆冒充进来了"

    async def _run():
        async with fc_db() as db:
            # 下游函数直断（与 section 路径互为印证）：None 取不到行、1 号取得到行
            assert await get_latest(db, None, 13) is None
            assert await get_latest(db, 1, 13) is not None

    asyncio.run(_run())


# ══════════════════════════ contextB3 第三批（legacy.py 12 处，2026-09-21）══════════════════════════



_PERSONA_KEYS = (
    "relationship", "current_status", "relationship_state", "character_feelings",
    "storyline_recall", "storyline_status", "recent_emotion", "active_topics", "identity_profile",
)


def _all_system_text(state: dict) -> str:
    """把装配产出的全部 system 消息拼成一段文本（哨兵是否泄漏只需在此判定）。"""
    return chr(10).join(m.get("content", "") for m in state["context_messages"] if m.get("role") == "system")


def _assembly_state(user_id) -> dict:
    st = _state(user_id)
    st["user_message"] = "在吗"
    return st


def _guard_assembled(out: dict) -> str:
    """装配必须跑到尾部：角色名进模板 + user 消息恒为最后一条。

    否则「文本里没有哨兵」可能只是提前 return / 中途炸掉造成的假绿。
    """
    text = _all_system_text(out)
    assert "酱" in text, "装配未产出角色 system 文本 → 正/反断言都会失真"
    assert out["context_messages"][-1]["role"] == "user", "装配未走到尾部 user 消息 → 断言范围不完整"
    return text


def _run_legacy_assembly(monkeypatch, factory, user_id) -> dict:
    """直跑 build_context_legacy 的内联装配路径，返回装配后的 state。

    ``_section_values={"relationship": ""}``：只把 persona 组（含本批排除项 :143）标记为
    「注册表已执行」→ 其余分区全部走 legacy 内联分支，本批 :120/:268/:307/:389/:407/:433/:474/
    :550/:556/:867 十处正是这些内联分支。``_trim`` 显式注入 → 不走排除项 :105。
    ``random.random`` 打桩 0.0 → :867 生活记忆的 trust 概率门（≥70 → 0.6）必开，否则正向对照 flaky。
    """
    import random

    from app.agent.context import legacy as lg

    _patch_session(monkeypatch, factory)
    monkeypatch.setattr(random, "random", lambda: 0.0)
    out = asyncio.run(lg.build_context_legacy(
        _assembly_state(user_id), _section_values={"relationship": ""}, _trim=lg._trim_limits(True),
    ))
    _guard_assembled(out)
    return out


# ───────────────────────── 17/18. 装配层：:120/:268/:307/:389/:407/:433/:474/:550/:556/:867 正/反对照


def test_legacy_assembly_with_caller_injects(fc_db, monkeypatch):
    """正向（带 caller=1）：1 号的十份数据全部进 system 文本（否则 18 的反证会是空断言）。

    「上次互动」用注入串特有的 ``｜距上次互动``（固定模板文案里也有「距上次互动的时长」，裸串恒定命中）；
    「八维」用 ``心情80``；:120 的用户行另由 ``state["user_name"]`` 钉住（昵称不进本轮 system 文本）。
    """
    out = _run_legacy_assembly(monkeypatch, fc_db, 1)
    text = _all_system_text(out)
    for marker in (
        "SENTINEL_USER", "SENTINEL_FACT", "SENTINEL_MOMENT", "心情80", "SENTINEL_SNAP",
        "SENTINEL_PROMISE", "｜距上次互动", "SENTINEL_MEMO", "SENTINEL_DIARY", "SENTINEL_LIFE",
    ):
        assert marker in text, f"正向未命中 {marker} → 该行的反向断言会是空断言"
    assert out["user_name"] == "SENTINEL_USER", "正向 :120 未取到 1 号用户行"


def test_legacy_assembly_without_caller_failclosed(fc_db, monkeypatch):
    """反向（state 无 user_id 键）：不抛异常，且任何 1 号哨兵/账号特征都不进 system 文本。"""
    out = _run_legacy_assembly(monkeypatch, fc_db, _NO_CALLER)
    text = _all_system_text(out)
    for marker in (
        "SENTINEL_USER", "SENTINEL_FACT", "SENTINEL_MOMENT", "心情80", "SENTINEL_SNAP",
        "SENTINEL_PROMISE", "｜距上次互动", "SENTINEL_MEMO", "SENTINEL_DIARY", "SENTINEL_LIFE",
    ):
        assert marker not in text, f"缺 caller 时把 1 号账号的 {marker} 冒充进来了"
    assert "SENTINEL_" not in text, "缺 caller 时 system 文本出现任何 1 号哨兵 → 未 fail-closed"
    assert out["user_name"] == "用户", "缺 caller 时 :120 仍把用户名冒充成了 1 号账号"


# ───────────────────────── 19/20. 纯 legacy 分支（_section_values=None）：:664/:673 调用点
# ───────────────────────── 这两行只在注册表未接管 MCP 时执行；该分支会连带走排除项 :143，
# ───────────────────────── 故把 :143 打桩成「九槽全空」——只中和未改动的它，两行照常真实执行。


def _seed_mcp_declarations(monkeypatch, factory) -> int:
    """造「1 号名下有一台已连接 server + 一个 mcp.* 工具」的事实；归属查询走真实 owned_server_ids。"""
    from types import SimpleNamespace

    from app.agent import tools as tl
    from app.mcp.manager import mcp_manager
    from app.mcp.ownership import owned_server_ids

    _patch_session(monkeypatch, factory)
    owned = asyncio.run(owned_server_ids(1))
    assert owned, "种子 MCP server 未归属到 1 号 → 反证会是空断言"
    sid = min(owned)
    monkeypatch.setattr(mcp_manager, "_conns", {sid: SimpleNamespace(
        user_id=1, server_id=sid, server_name="sentinel", is_connected=True,
        resources=[{"uri": "file:///s", "name": "SENTINEL_RES", "mime_type": "text/plain", "description": ""}],
    )})
    monkeypatch.setattr(tl, "list_tools", lambda: [
        tl.ToolSpec(name="mcp.sentinel_tool", description="SENTINEL_TOOL", server_id=sid),
    ])
    return sid


def _run_legacy_pure(monkeypatch, factory, user_id) -> str:
    """``_section_values=None`` 的纯 legacy 装配（:664 工具声明 / :673 资源摘要仅此分支执行）。"""
    import random

    import app.agent.persona as persona
    from app.agent.context import legacy as lg

    async def _persona_stub(*_a, **_k):
        return dict.fromkeys(_PERSONA_KEYS, "")

    _seed_mcp_declarations(monkeypatch, factory)
    monkeypatch.setattr(random, "random", lambda: 0.0)
    monkeypatch.setattr(persona, "assemble_persona_context", _persona_stub)
    out = asyncio.run(lg.build_context_legacy(
        _assembly_state(user_id), _section_values=None, _trim=lg._trim_limits(True),
    ))
    return _guard_assembled(out)


def test_legacy_pure_assembly_with_caller_injects_mcp(fc_db, monkeypatch):
    """正向：纯 legacy 分支下 1 号的 MCP 工具声明 + 资源摘要都进了 system 文本。"""
    text = _run_legacy_pure(monkeypatch, fc_db, 1)
    assert "mcp.sentinel_tool" in text, "正向未注入 MCP 工具声明 → :664 的反证会是空断言"
    assert "SENTINEL_RES" in text, "正向未注入 MCP 资源摘要 → :673 的反证会是空断言"


def test_legacy_pure_assembly_without_caller_failclosed(fc_db, monkeypatch):
    """反向：缺 caller → :664/:673 传给下游的是 None → 声明与资源都不进 system 文本（不抛异常）。"""
    text = _run_legacy_pure(monkeypatch, fc_db, _NO_CALLER)
    assert "mcp.sentinel_tool" not in text, "缺 caller 时把 1 号账号的 MCP 工具声明冒充进来了"
    assert "SENTINEL_RES" not in text, "缺 caller 时把 1 号账号的 MCP 资源摘要冒充进来了"


# ───────────────────────── 21/22. 直调 :664/:673 所在函数（钉死下游 None 语义，与 19/20 互为印证）


def test_legacy_mcp_tools_text_failclosed(fc_db, monkeypatch):
    """legacy 绑定的 ``_build_mcp_tools_text``：带 caller → 声明含哨兵工具；缺 caller → 归属空集 → 空串。"""
    from app.agent.context import legacy as lg

    sid = _seed_mcp_declarations(monkeypatch, fc_db)
    pos = asyncio.run(lg._build_mcp_tools_text(1))
    assert "mcp.sentinel_tool" in pos, "带 caller 时未产出工具声明 → 反证会是空断言"
    neg = asyncio.run(lg._build_mcp_tools_text(None))  # 不抛异常
    assert neg == "", f"user_id=None 时仍产出 1 号 server（sid={sid}）的 MCP 工具声明 → 未 fail-closed"


def test_legacy_mcp_resources_text_failclosed(fc_db, monkeypatch):
    """legacy 绑定的 ``_build_mcp_resources_text``：带 caller → 含 1 号资源；缺 caller → 空串（不注入）。"""
    from app.agent.context import legacy as lg

    _seed_mcp_declarations(monkeypatch, fc_db)
    pos = asyncio.run(lg._build_mcp_resources_text(1, stream=False))
    assert "SENTINEL_RES" in pos, "带 caller 时未产出资源摘要 → 反证会是空断言"
    neg = asyncio.run(lg._build_mcp_resources_text(None, stream=False))  # 不抛异常
    assert neg == "", "user_id=None 时把 1 号账号的 MCP 资源摘要冒充进来了 → 未 fail-closed"


# ══════════════════════════ contextB4 第四批（剩余 4 处纯查询型，2026-09-21）══════════════════════════


# ───────────────────────────────────── 23. overlay :133「AI 生活」注入（随机概率门之后）


def test_overlay_life_share_failclosed(fc_db, monkeypatch):
    """life_share_section：:133 在 ``if _share and _trust >= 60: if _rnd.random() < _prob`` 之后。

    正/反两跑用**同一个**打桩（random→0.0 ⇒ 门必开），两侧唯一差异只剩 user_id → 概率门不会把
    反向跑变成空断言。ProactiveSettings 无行 ⇒ life_share_enabled 取默认 True；trust=70 ⇒ 门槛 0.6。
    """
    import random

    from app.agent.context import section_overlay as o

    _patch_session(monkeypatch, fc_db)
    monkeypatch.setattr(random, "random", lambda: 0.0)  # 模块内是 import random as _rnd → 同一模块对象
    pos = asyncio.run(o.life_share_section(_state(1), {}))
    assert pos and "SENTINEL_LIFE" in pos[0], "带 caller 时未注入 AI 生活 → 反证会是空断言"
    neg = asyncio.run(o.life_share_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == [], "缺 caller 时把 1 号账号的 AI 生活记忆冒充进来了"


# ───────────────────────────────────── 24. user_now :25 用户最新状态（跨角色权威分区）


def test_user_now_failclosed(fc_db, monkeypatch):
    """user_now_section：敏感槽 relationship 默认关 → 用例内显式打开 AGENT_FLAGS 才有内容可测。

    缺 caller 时 ``enabled_user_fact_slots_for(None)`` 仍回全局值（flag 已开）→ 不早退，真正的
    fail-closed 落在下游 ``get_active_user_facts`` 的 ``user_facts.user_id IS NULL`` → 空 → 「无」→ 不注入。
    """
    from app.agent.context import section_user_now as un
    from app.agent.loop import AGENT_FLAGS

    _patch_session(monkeypatch, fc_db)
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_relationship", True)
    pos = asyncio.run(un.user_now_section(_state(1), {}))
    assert pos and "SENTINEL_USERNOW" in pos[0], "带 caller 时未注入用户最新状态 → 反证会是空断言"
    neg = asyncio.run(un.user_now_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert neg == [], "缺 caller 时把 1 号账号的用户级事实冒充进来了"


# ───────────────────────────────────── 25. nodes :106 目标/未完成路（唯一下游直调）


def test_active_goal_queries_failclosed(fc_db, monkeypatch):
    """nodes:106 的唯一下游 ``load_active_goal_queries``：带 caller → 命中进行中话题；缺 caller → []。

    门（AICharacter.memory_v2_enabled）默认 True（模型侧 default）→ 正向取不到行会由断言直接暴露。
    另加一条**调用点**源码守卫：本例直调下游函数，把 nodes.py:106 改回 ``…, 1)`` 时本例的行为断言
    不会红（变异反证已实测），故用 ``inspect.getsource`` 钉住那一行确实不再传默认值。
    """
    import inspect

    from app.agent import nodes as nd
    from app.agent.topic_tracker import load_active_goal_queries

    assert 'load_active_goal_queries(state["character_id"], state.get("user_id"))' in inspect.getsource(
        nd.retrieve_memories), "nodes.retrieve_memories 的目标路又把「拿不到 caller」兜底成 1 号账号"

    _patch_session(monkeypatch, fc_db)
    _patch_extra_readers(monkeypatch, fc_db)
    pos = asyncio.run(load_active_goal_queries(13, 1))
    assert "GOALTGT_B4" in pos, "带 caller 时未取到进行中目标 → 反证会是空断言"
    neg = asyncio.run(load_active_goal_queries(13, None))  # 不抛异常
    assert neg == [], "user_id=None 时把 1 号账号的进行中目标话题冒充进来了"


# ───────────────────────────────────── 26. runtime :190 轻量社交上下文（锚点 + 会话时间）


def test_light_social_context_failclosed(fc_db, monkeypatch):
    """build_light_social_context：:190 的 uid 只喂两处 —— get_relationship_anchors 与 ChatSession 过滤。

    正证两件事都到位（锚点哨兵 + 「｜距上次互动」）；反证两者皆无且不抛异常。角色基础人设/时间行
    等与 user_id 无关的块仍应产出（证明反向跑不是中途 return 造成的假绿）。
    """
    from app.agent.runtime import build_light_social_context

    _patch_session(monkeypatch, fc_db)
    _patch_extra_readers(monkeypatch, fc_db)

    pos = asyncio.run(build_light_social_context({**_state(1), "user_message": "在吗"}))
    pos_text = _all_system_text(pos)
    assert "ANCHORTGT_B4" in pos_text, "带 caller 时未注入关系锚点 → 反证会是空断言"
    assert "｜距上次互动" in pos_text, "带 caller 时未注入「距上次互动」→ 会话过滤的反证会是空断言"
    assert "你是：" in pos_text, "轻量上下文未产出角色人设块 → 反向跑可能压根没走到尾部"
    assert pos["context_messages"][-1]["role"] == "user", "轻量上下文未走到尾部 user 消息"

    neg = asyncio.run(build_light_social_context({**_state(_NO_CALLER), "user_message": "在吗"}))
    neg_text = _all_system_text(neg)
    assert "ANCHORTGT_B4" not in neg_text, "缺 caller 时把 1 号账号的关系锚点冒充进来了"
    assert "｜距上次互动" not in neg_text, "缺 caller 时把 1 号账号的会话更新时间冒充进来了"
    assert "你是：" in neg_text, "缺 caller 时连角色人设块都没产出 → 前面的 not in 会是空断言"


# ══════════════════════════ contextB5 第五批（剩余 7 处「无下游风险」调用点，2026-09-21）══════════════════════════


# ───────────────────────────────────── 27. legacy :143 assemble_persona_context（no-op 等值）

def test_persona_assemble_noop_for_missing_caller(fc_db, monkeypatch):
    """#1：``assemble_persona_context`` 的 user_id 只喂三处下游，而三处的 SQL 只按 character_id 过滤。

    故带 caller(1) 与缺 caller(None) 的返回**必须逐字符相等**（等值腿），并用 active_topics
    里的 GOALTGT_B4 钉住「确实渲染出了内容」（非空腿）——否则两跑同为空 dict 也会「相等」。
    """
    from app.agent.persona import assemble_persona_context

    _patch_session(monkeypatch, fc_db)
    _patch_b5_readers(monkeypatch, fc_db)
    pos = asyncio.run(assemble_persona_context(13, 1))
    neg = asyncio.run(assemble_persona_context(13, None))  # 不抛异常
    assert "GOALTGT_B4" in pos["active_topics"], "进行中话题没渲染出来 → 等值断言会是空断言"
    assert set(neg) == set(pos)
    for key, val in pos.items():
        assert neg[key] == val, f"persona 块 {key} 随 caller 变化（该批核实为 no-op）"


# ───────────────────────────────────── 28. section_persona :28 同一调用的 section 入口

def test_persona_section_entry_noop_for_missing_caller(fc_db, monkeypatch):
    """#2：经 ``_persona(state, ctx)`` 入口（ctx 同轮缓存）跑一遍，两跑同样逐块相等。

    ``_state(_NO_CALLER)`` 走到改后的 ``state.get("user_id")`` → None；再取一个真实槽构造器
    （``_mk_persona_slot``）确认槽文本也一致。
    """
    from app.agent.context import section_persona as p

    _patch_session(monkeypatch, fc_db)
    _patch_b5_readers(monkeypatch, fc_db)
    pos = asyncio.run(p._persona(_state(1), {}))
    neg = asyncio.run(p._persona(_state(_NO_CALLER), {}))  # 不抛异常
    assert "GOALTGT_B4" in pos["active_topics"], "带 caller 时槽里没有话题 → 等值断言会是空断言"
    assert neg == pos
    slot = p._mk_persona_slot("active_topics")
    pos_slot = asyncio.run(slot(_state(1), {}))
    neg_slot = asyncio.run(slot(_state(_NO_CALLER), {}))
    assert pos_slot == neg_slot == pos["active_topics"]


# ───────────────────────────────────── 29. section_curated :32 编纂知识（no-op 等值）

def test_curated_facts_noop_for_missing_caller(fc_db, monkeypatch):
    """#3：``get_curated_facts`` 的 SQL 只用 character_id/status/kind（user_id 未进查询）→ 两跑等值。

    比的是 ``curated_line`` 渲染后的**文本**（不比 ORM 实例）；section 入口那条腿要显式打开
    ``curated_knowledge`` flag（默认关 → 两跑都返回空列表，等值会退化成空断言）。
    """
    from app.agent.context import section_curated as cu
    from app.agent.loop import AGENT_FLAGS
    from app.events.facts import curated_line, get_curated_facts

    _patch_session(monkeypatch, fc_db)
    monkeypatch.setitem(AGENT_FLAGS, "curated_knowledge", True)  # monkeypatch 收尾还原，不外泄

    async def _render(uid):
        grouped = await get_curated_facts(
            character_id=13, user_id=uid, viewer_type="character", viewer_id=13,
        )
        return {kind: [curated_line(r) for r in rows] for kind, rows in grouped.items()}

    pos = asyncio.run(_render(1))
    neg = asyncio.run(_render(None))  # 不抛异常
    flat = [ln for lines in pos.values() for ln in lines]
    assert "CURATEDB5_不许夸大" in "".join(flat), "种子 curated 行没被取到 → 等值断言会是空断言"
    assert neg == pos, "编纂知识层的可见行随 caller 变化（该批核实为 no-op）"

    pos_sec = asyncio.run(cu.curated_knowledge_section(_state(1), {}))
    neg_sec = asyncio.run(cu.curated_knowledge_section(_state(_NO_CALLER), {}))
    assert pos_sec and "CURATEDB5_不许夸大" in pos_sec[0], "带 caller 时 section 未注入 → 等值断言会是空断言"
    assert neg_sec == pos_sec


# ───────────────────────────────────── 30. context_builder :577 跨角色事实对齐

def test_align_character_to_user_facts_failclosed(fc_db, monkeypatch):
    """#4：``get_active_user_facts(None)`` → ``user_facts.user_id IS NULL`` → 空集 → 报表 {}。

    正向用 slot="job"（细槽 flag 默认关 → 用例内显式打开，不外泄），且该行带 previous_value
    （对齐报表只登记「发生过更替」的槽）。命中数 n=0 也算登记（fc_db 里没有内容匹配旧值的
    user_info 记忆），故断言「键在」而非「值 > 0」。
    """
    from app.agent.loop import AGENT_FLAGS
    from app.memory.cross_char_sync import align_character_to_user_facts

    _patch_session(monkeypatch, fc_db)
    _patch_b5_readers(monkeypatch, fc_db)
    monkeypatch.setitem(AGENT_FLAGS, "user_fact_job", True)
    pos = asyncio.run(align_character_to_user_facts(13, 1))
    assert "job" in pos, "带 caller 时 JOBB5 槽的旧值没进对齐报表 → 反证会是空断言"
    neg = asyncio.run(align_character_to_user_facts(13, None))  # 不抛异常
    assert neg == {}, f"缺 caller 时拿 1 号账号的用户级事实去 stale 角色记忆：{neg}"


# ───────────────────────────────────── 31. context_builder :385 日摘要补生成的 LLM 归属

def test_memory_task_llm_config_not_borrowed(fc_db, monkeypatch):
    """#5（派单口径 ①：直调 ``_resolve_llm_config``，全程不触网、不调 LLM）。

    口径 ① 的两处 stub 这里改用**真实种子行**做同等观测：1 号在 memory 任务上配了 BYOK
    （USERB5_MODEL/sk-user-b5），服务器级不配 →
    - user_id=1：用户级被查到并生效（反证「用户级本来会被查询」）；
    - user_id=None：``get_task_llm_config`` 的 ``if user_id:`` 跳过用户级 → 回落服务器级/.env，
      三个字段都不等于 1 号那份（改前风险＝盗用 1 号 BYOK 与额度）。
    """
    from app.agent.llm_client import _resolve_llm_config

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(_resolve_llm_config(user_id=1, task="memory"))
    assert pos["model"] == "USERB5_MODEL", "1 号的 memory 任务配置没生效 → 反证会是空断言"
    assert pos["api_key"] == "sk-user-b5" and pos["base_url"] == "http://127.0.0.1:9/v1"
    neg = asyncio.run(_resolve_llm_config(user_id=None, task="memory"))  # 不抛异常
    assert set(neg) == set(pos), "配置结构被改动（应只是解析来源不同）"
    assert neg["model"] != "USERB5_MODEL", "缺 caller 时用了 1 号 memory 任务的模型"
    assert neg["api_key"] != "sk-user-b5", "缺 caller 时借用了 1 号账号的 BYOK key"
    assert neg["base_url"] != "http://127.0.0.1:9/v1", "缺 caller 时借用了 1 号账号的 BYOK 端点"


# ───────────────────────────────────── 32. legacy :105 热度裁剪（None → 低频保守档）


def test_is_hot_character_failclosed_uses_lowfreq_trim(fc_db, monkeypatch):
    """#6：``_is_hot_character`` 按 ``ChatSession.user_id`` 计数 → None 计数 0 → 判非高频。

    方向是**保守**（更小的摘要/织库配额），不是泄漏；断言落到低频档具体数值防口径漂移
    （低频=3000 字摘要 / 3 卡织库 / 核心 3 / 锚点 2；高频=8000 / 10 / 10 / 5）。
    """
    from app.agent import context_builder as cb

    _patch_session(monkeypatch, fc_db)
    assert asyncio.run(cb._is_hot_character(13, 1)) is True, "种子 30 条近 7 天消息未命中 → 反证会是空断言"
    assert asyncio.run(cb._is_hot_character(13, None)) is False, "缺 caller 时把 1 号账号的活跃度冒充成高频"
    assert cb._trim_limits(False) == {
        "summary_chars": 3000, "weave_limit": 3, "core_limit": 3, "anchor_limit": 2,
    }
    assert cb._trim_limits(True) == {
        "summary_chars": 8000, "weave_limit": 10, "core_limit": 10, "anchor_limit": 5,
    }


# ───────────────────────────────────── 33. context/__init__ :54 注册表侧同一判据


def test_registry_resolve_trim_failclosed(fc_db, monkeypatch):
    """#7：注册表侧 ``_resolve_trim`` 与 legacy 内联共用 ``_is_hot_character``，两侧口径必须一致。

    ``_is_hot_character`` 自身吞异常回 True ⇒ 若取库不完整（patch 漏了）两跑都会得到高频档，
    反向断言会直接红，不会静默假绿。裁剪总闸关闭时两跑也都会是高频档 → 先钉住闸门是开的。
    """
    import app.agent.context as ctx_mod
    from app.agent import context_builder as cb
    from app.agent.loop import AGENT_FLAGS

    assert AGENT_FLAGS.get("agent_context_trim", True), "裁剪总闸关 → 本例两条断言都会失真"
    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(ctx_mod._resolve_trim(_state(1)))
    assert pos == cb._trim_limits(True), "带 caller 且种子够高频 → 应取全量档"
    neg = asyncio.run(ctx_mod._resolve_trim(_state(_NO_CALLER)))  # 不抛异常
    assert neg == cb._trim_limits(False), "缺 caller 时仍按 1 号账号的活跃度发放全量配额"


# ─────────────────────── 34/35. B5 补强：调用点连线（变异必红）


def test_older_summaries_llm_user_id_call_site(fc_db, monkeypatch):
    """#5 调用点连线：``_build_older_summaries`` 补生成日摘要时把缺 caller 的 None 交给 LLM。

    本批 31 号用例只测了下游 ``_resolve_llm_config``，**变异反证显示它抓不住调用点**
    （把 `context_builder.py:385` 改回 ``state.get("user_id", 1)`` 后本文件仍 33 passed），
    故补这条只钉连线的用例：spy 掉 ``context_builder.chat_completion``，断言它收到的
    ``user_id`` 正是宿主 state 里的值（缺键 → None；带 caller → 1）。spy 直接返回字符串，不触网。
    """
    from datetime import datetime, timedelta

    from app.agent import context_builder as cb

    calls: list = []

    async def _spy(*args, **kwargs):
        calls.append(kwargs)
        return "B5 摘要"

    class _Msg:
        sender_type = "user"
        content = "B5 旧消息"

        def __init__(self, born) -> None:
            self.created_at = born

    monkeypatch.setattr(cb, "chat_completion", _spy)
    _patch_session(monkeypatch, fc_db)

    def _run(user_id, days_ago):
        calls.clear()
        born = (datetime.now() - timedelta(days=days_ago)).replace(hour=12, minute=0, second=0, microsecond=0)
        out = asyncio.run(cb._build_older_summaries(_state(user_id), [_Msg(born)], "酱", cb._trim_limits(True)))
        assert out, "补生成分支未走到（返回空串）→ 断言会失真"
        assert calls, "该天缺失日摘要时应触发一次生成"
        return calls[0]

    assert _run(1, 9).get("user_id") == 1, "带 caller 时必须用该账号 id 解析「记忆」任务模型配置"
    assert _run(_NO_CALLER, 8).get("user_id") is None, "缺 caller 时不得回落成 1 号账号（会借用他人 BYOK/额度）"


def test_legacy_trim_call_site_user_id(fc_db, monkeypatch):
    """#6 调用点连线：纯 legacy 装配自算 trim 时（``_trim=None``）把缺 caller 传成 None。

    本批 32 号用例只测了下游 ``_is_hot_character``，变异反证抓不住 `legacy.py:105` 调用点
    （改回 ``, 1`` 后本文件仍 33 passed），故补这条：spy 掉 ``legacy._is_hot_character``，
    直跑内联装配（``_trim=None`` 才会走 :105），断言收到的 user_id 就是宿主 state 里的值。
    """
    import random

    from app.agent.context import legacy as lg

    seen: list = []

    async def _spy(character_id, user_id):
        seen.append((character_id, user_id))
        return False

    monkeypatch.setattr(lg, "_is_hot_character", _spy)
    _patch_session(monkeypatch, fc_db)
    monkeypatch.setattr(random, "random", lambda: 0.0)

    def _run(user_id):
        seen.clear()
        out = asyncio.run(lg.build_context_legacy(
            _assembly_state(user_id), _section_values={"relationship": ""}, _trim=None,
        ))
        _guard_assembled(out)
        assert seen, "trim 分支未走到 → 断言会失真"
        return seen[0]

    assert _run(1) == (13, 1), "带 caller 时热度检查必须用该账号 id"
    assert _run(_NO_CALLER)[1] is None, "缺 caller 时热度检查不得回落成 1 号账号"


# ══════════════════════ contextB6 第六批（混合语义收尾 4 处，2026-09-21）══════════════════════


def _run_legacy_pure_bare(monkeypatch, factory, user_id) -> str:
    """``_section_values=None`` 的纯 legacy 装配（:284 仅此分支执行），本批只用于连线用例。

    与 ``_run_legacy_pure`` 的差别只有「不造 MCP 事实」：本批断言的是传给下游的 uid，不关心 MCP。
    persona 仍打桩（该分支会连带走 :143），``random`` 仍钉死 :867 概率门，保持与既有跑法一致。
    """
    import random

    import app.agent.persona as persona
    from app.agent.context import legacy as lg

    async def _persona_stub(*_a, **_k):
        return dict.fromkeys(_PERSONA_KEYS, "")

    _patch_session(monkeypatch, factory)
    monkeypatch.setattr(random, "random", lambda: 0.0)
    monkeypatch.setattr(persona, "assemble_persona_context", _persona_stub)
    out = asyncio.run(lg.build_context_legacy(
        _assembly_state(user_id), _section_values=None, _trim=lg._trim_limits(True),
    ))
    return _guard_assembled(out)


# ───────────────────────────────────── 36. ①/② 行为：直调 _inject_core_anchors_loops


def test_core_anchors_loops_failclosed(fc_db, monkeypatch):
    """③槽混合语义的**行为**侧：账号那半边（锚点 / 计时）fail-closed，角色那半边（核心记忆 / 目标）保住。

    正证还钉住「2 号账号的同类数据任何时候都不进来」——否则「反证看不见」可能只是因为
    下游整段坏了（把过滤写成恒假也会绿）。
    """
    from app.agent import context_builder as cb
    from app.agent.context import section_memories as sm

    _patch_session(monkeypatch, fc_db)
    _patch_extra_readers(monkeypatch, fc_db)  # memory.core 是模块级 from … import
    trim = cb._trim_limits(True)

    pos_core, pos_anchors, pos_loops = asyncio.run(sm._inject_core_anchors_loops(13, 1, trim))
    assert "COREB6_核心记忆" in pos_core, "带 caller 时核心记忆没注入 → 反证的「仍在」会是空断言"
    assert "ANCHORB6_一号锚点" in pos_anchors, "带 caller 时 1 号锚点没注入 → 反证会是空断言"
    assert "ANCHORB6_二号锚点" not in pos_anchors, "1 号那轮把 2 号账号的关系锚点也捞进来了"
    assert "GOALB6_目标" in pos_loops, "带 caller 时生活目标没注入 → 反证的「仍在」会是空断言"
    assert "TIMERB6_一号计时" in pos_loops, "带 caller 时计时承诺没注入 → 反证会是空断言"
    assert "TIMERB6_二号计时" not in pos_loops, "1 号那轮把 2 号账号的计时承诺也捞进来了"

    neg_core, neg_anchors, neg_loops = asyncio.run(sm._inject_core_anchors_loops(13, None, trim))
    assert "COREB6_核心记忆" in neg_core, "缺 caller 时把角色自身核心记忆也掐了（超出 fail-closed 射程）"
    assert "GOALB6_目标" in neg_loops, "缺 caller 时把角色自身生活目标也掐了（只按 character 归属）"
    assert neg_anchors == "无", f"缺 caller 时仍注入账号级关系锚点：{neg_anchors}"
    assert "TIMERB6" not in neg_loops, f"缺 caller 时仍注入账号级计时承诺：{neg_loops}"
    assert "ANCHORB6" not in neg_core + neg_anchors + neg_loops, "缺 caller 时任何账号锚点漏进了三槽"


# ───────────────────────────────────── 37. ① 调用点连线：legacy.py:284


def test_legacy_core_anchors_loops_call_site(fc_db, monkeypatch):
    """①连线：spy ``legacy`` 模块里绑定的 ``_inject_core_anchors_loops``，钉住 :284 传下去的 uid。

    36 号直调下游函数**抓不住调用点**（B5 的 :385/legacy:105 就是这么漏的），故本例只验连线：
    纯 legacy 装配（``_section_values=None``，否则该分支被注册表值取代）里收到的 (cid, uid)。
    """
    from app.agent.context import legacy as lg

    seen: list = []

    async def _spy(character_id, user_id, _trim):
        seen.append((character_id, user_id))
        return "无", "无", "无"

    monkeypatch.setattr(lg, "_inject_core_anchors_loops", _spy)

    def _run(user_id):
        seen.clear()
        _run_legacy_pure_bare(monkeypatch, fc_db, user_id)
        assert seen, "纯 legacy 分支未走到 :284 → 断言会失真"
        return seen[0]

    assert _run(1) == (13, 1), "带 caller 时 :284 没把该账号 id 传给三槽下游"
    assert _run(_NO_CALLER)[1] is None, "缺 caller 时 :284 仍把这一轮冒充成 1 号账号"


# ───────────────────────────────────── 38. ② 调用点连线：section_memories.py:172


def test_section_memories_core_anchors_call_site(monkeypatch):
    """②连线：注册表侧缓存入口 ``_get_core_anchors_loops`` 传给下游的 uid（不触库，纯连线）。

    ``ctx`` 只带 ``trim``（``_core_anchors_loops`` 缺省 None → 必走 :172）；spy 掉同模块的
    ``_inject_core_anchors_loops``，顺带确认返回值原样落进 ctx 缓存（三槽共享一次调用没被改坏）。
    """
    from app.agent import context_builder as cb
    from app.agent.context import section_memories as sm

    seen: list = []

    async def _spy(character_id, user_id, _trim):
        seen.append((character_id, user_id))
        return "无", "无", "无"

    monkeypatch.setattr(sm, "_inject_core_anchors_loops", _spy)

    def _run(user_id):
        seen.clear()
        ctx = {"trim": cb._trim_limits(True)}
        out = asyncio.run(sm._get_core_anchors_loops(_state(user_id), ctx))
        assert seen, ":172 未走到（缓存判据变了？）→ 断言会失真"
        assert out == ctx["_core_anchors_loops"] == ("无", "无", "无")
        return seen[0]

    assert _run(1) == (13, 1), "带 caller 时 :172 没把该账号 id 传给三槽下游"
    assert _run(_NO_CALLER)[1] is None, "缺 caller 时 :172 仍把这一轮冒充成 1 号账号"


# ───────────────────────────────────── 39. ③ 行为：section_pet.pets_section


def test_pets_section_failclosed(fc_db, monkeypatch):
    """③行为：OR 三分支——两条用户分支 fail-closed，第三条（角色自养 AI 宠物）必须保住。

    ``pets.user_id`` 是 NOT NULL ⇒ 缺 caller 时 ``user_id IS NULL`` 恒不匹配，故「无主宠物」也捞不到。
    正证钉住 2 号宠物任何时候不注入（防「恒假过滤」造成的假绿）。
    """
    from app.agent.context import section_pet as sp

    _patch_session(monkeypatch, fc_db)
    pos = asyncio.run(sp.pets_section(_state(1), {}))
    assert "PETB6_咪咪" in pos and "PETB6_无归属" in pos, "带 caller 时 1 号的宠物没注入 → 反证会是空断言"
    assert "AIPETB6_团子" in pos, "带 caller 时角色自养的 AI 宠物没注入 → 下面的正向断言不完整"
    assert "PET2B6_旺财" not in pos, "1 号那轮把 2 号账号的宠物也捞进来了"

    neg = asyncio.run(sp.pets_section(_state(_NO_CALLER), {}))  # 不抛异常
    assert "AIPETB6_团子" in neg, "缺 caller 时把角色自养的 AI 宠物也掐了（超出 fail-closed 射程）"
    assert "PETB6_咪咪" not in neg, "缺 caller 时把 1 号账号的宠物冒充进来了"
    assert "PETB6_无归属" not in neg, "缺 caller 时把 1 号账号的无归属旧数据宠物冒充进来了"
    assert "PET2B6_旺财" not in neg, "缺 caller 时把 2 号账号的宠物冒充进来了"
    assert "旺财" not in neg and "咪咪" not in neg, "用户宠物行整条漏出（含名字）→ 分支未 fail-closed"


# ───────────────────────────────────── 40. ④ 调用点：legacy.py:333 内联 pets 分支


def test_legacy_inline_pets_call_site(fc_db, monkeypatch):
    """④连线（真实执行内联分支）：``_section_values={"relationship": ""}`` 时 ``pets`` 不在注册表已执行
    集合 ⇒ 走 legacy 自己的 :333 查询，口径必须与 39 号（注册表 section 入口）一致。

    这里不打桩下游——变异 :333 回到 ``…, 1)`` 时缺 caller 那跑会重新注入 1 号宠物，本例即红。
    """
    pos_text = _all_system_text(_run_legacy_assembly(monkeypatch, fc_db, 1))
    assert "PETB6_咪咪" in pos_text, "带 caller 时内联分支没注入 1 号宠物 → 反证会是空断言"
    assert "AIPETB6_团子" in pos_text, "带 caller 时内联分支没注入角色自养 AI 宠物 → 正证不完整"
    assert "PET2B6_旺财" not in pos_text, "1 号那轮把 2 号账号的宠物也捞进来了"

    neg_text = _all_system_text(_run_legacy_assembly(monkeypatch, fc_db, _NO_CALLER))  # 不抛异常
    assert "AIPETB6_团子" in neg_text, "缺 caller 时把角色自养的 AI 宠物也掐了（超出 fail-closed 射程）"
    assert "PETB6_咪咪" not in neg_text, "缺 caller 时 :333 仍把这一轮的宠物冒充成 1 号账号"
    assert "PETB6_无归属" not in neg_text, "缺 caller 时 :333 把 1 号的无归属旧数据宠物冒充进来了"
    assert "PET2B6_旺财" not in neg_text, "缺 caller 时 :333 把 2 号账号的宠物冒充进来了"
