# -*- coding: utf-8 -*-
"""主动复习「回忆化」+ 过期计划记忆治理 测试（2026-09-09，L0-L3）。

- L0 tense.py：时态分类 / 计划有效期 / 过期判定（纯函数，零 LLM，含语境守卫与完成信号边界）；
- L1：collect_review_events / _pick_contextual_memory 排除过期计划与瞬时状态（flag 关=旧选片）；
- L2：_apply_reinforce 按 tense 分流收口（channel=review；过期计划收死、往事适度、enduring 不设限）；
- L3：_review_phrase 时态引导语 / _is_wrong_tense_directive 输出闸门 / 回忆框架 hint 组装 /
  怀旧日额度（extra_meta.tense 计数）；
- 回归：flag 关闭后 hint/选片/强化回到旧行为；F1/F4/F5（Sam 修复）语义保留。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；临时 SQLite 文件库 + monkeypatch
（与 test_memory_supersede_c.py 同法，不触碰 backend/data）。
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.memory import Memory
from app.memory.tense import (
    classify_tense, is_plan_expired, plan_valid_until, days_since,
    PLAN_DEFAULT_HORIZON_DAYS, TRIP_BUFFER_DAYS,
)

NOW = datetime(2026, 9, 9, 12, 0, 0)


def _mem(**kw):
    """构造 Memory ORM 对象（不入库；L0/L2 纯函数用）。"""
    base = dict(
        user_id=3, character_id=13, memory_type="event", content="",
        importance=60.0, review_count=0,
        created_at=datetime(2026, 8, 15, 10, 0, 0),
    )
    base.update(kw)
    return Memory(**base)


def _set_flag(monkeypatch, key, value):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, key, value)


# ────────────────────────── L0：tense 分类 ──────────────────────────

def test_classify_enduring():
    assert classify_tense(_mem(memory_type="preference", content="用户不吃香菜")) == "enduring"
    assert classify_tense(_mem(memory_type="user_info", content="用户叫小轩")) == "enduring"
    assert classify_tense(_mem(content="关系很好", sub_type="relationship")) == "enduring"
    assert classify_tense(_mem(content="有点想你", sub_type="emotion")) == "enduring"
    assert classify_tense(_mem(content="用户核心身份", is_core=True)) == "enduring"
    assert classify_tense(_mem(content="用户核心身份", core_category="identity")) == "enduring"
    # 抽取出的 user_info（sub=extracted）含将来时 → 仍按文本判 plan
    assert classify_tense(_mem(memory_type="user_info", sub_type="extracted",
                               content="用户近期将去长沙")) == "plan"


def test_classify_plan_changsha_corpus():
    # 线上真实语料（7018/7021）：必须命中 plan
    assert classify_tense(_mem(content="用户近期将去长沙，期间可能因不便携带电脑而断联")) == "plan"
    assert classify_tense(_mem(content="用户计划出行并尽量携带电脑，AI 表示电脑能带就带")) == "plan"
    assert classify_tense(_mem(content="用户8月18日出发去长沙")) == "plan"
    assert classify_tense(_mem(content="明天要去医院复诊")) == "plan"
    assert classify_tense(_mem(sub_type="plan", content="下周去长沙")) == "plan"  # L4 显式标注


def test_classify_episodic_and_transient():
    assert classify_tense(_mem(content="今天在橘子洲游玩，拍了好多照片")) == "episodic"
    assert classify_tense(_mem(memory_type="insight", content="用户喜欢被叫宝宝")) == "episodic"  # 洞察按往事口径（方案 §3）
    assert classify_tense(_mem(content="状态更新：正在商场逛街")) == "transient"
    assert classify_tense(_mem(sub_type="status", content="逛街中")) == "transient"


def test_classify_done_markers_beat_plan():
    """_DONE_MARKERS 优先级最高：已完成行程不判为未过期计划（落 episodic 往事）。"""
    assert classify_tense(_mem(content="用户计划去长沙，已经回来了")) == "episodic"
    assert classify_tense(_mem(content="那趟出行结束了，玩得很开心")) == "episodic"
    # 但 L4 显式标注 plan 且文本有完成信号 → 仍 plan（由 is_plan_expired 判过期）
    m = _mem(sub_type="plan", content="计划去长沙，已经回来了")
    assert classify_tense(m) == "plan"
    assert is_plan_expired(m, NOW) is True


def test_classify_meta_dialogue_guards():
    """语境守卫：转述/反问/否定里的将来时词不判 plan（元对话误判风险，交接 L0 要求）。"""
    assert classify_tense(_mem(content="你说要去长沙看烟花，后来生成了一个事件")) == "episodic"
    assert classify_tense(_mem(content="用户问明天要不要去长沙？")) == "episodic"
    assert classify_tense(_mem(content="用户没打算去长沙，只是提了一嘴")) == "episodic"
    assert classify_tense(_mem(content="提过要修一个 bug，准备下版本处理")) == "episodic"
    # 守卫不误伤真实计划（无引用/疑问/否定词）
    assert classify_tense(_mem(content="用户下周要去上海出差三天")) == "plan"


# ────────────────────────── L0：plan_valid_until / is_plan_expired ──────────────────────────

def test_plan_valid_until_explicit_date_with_trip_buffer():
    m = _mem(content="用户8月18日出发去长沙", created_at=datetime(2026, 8, 15))
    vu = plan_valid_until(m, NOW)
    assert vu == datetime(2026, 8, 18) + timedelta(days=TRIP_BUFFER_DAYS)
    assert is_plan_expired(m, NOW) is True  # 9 月判过期（验收 §11.1）


def test_plan_valid_until_relative_days_anchor_created_at():
    m = _mem(content="明天要去医院复诊", created_at=datetime(2026, 8, 15))
    assert plan_valid_until(m, NOW) == datetime(2026, 8, 16)
    assert is_plan_expired(m, NOW) is True


def test_plan_valid_until_default_horizon_anchor_created_at():
    """无明确日期：默认水平窗以记忆创建时间为锚（而非 now）——旧"近期"计划才能过期。"""
    m = _mem(content="用户近期将去长沙", created_at=datetime(2026, 8, 15))
    assert plan_valid_until(m, NOW) == datetime(2026, 8, 15) + timedelta(days=PLAN_DEFAULT_HORIZON_DAYS)
    assert is_plan_expired(m, NOW) is True


def test_plan_valid_until_valid_to_priority():
    m = _mem(sub_type="plan", content="下周去长沙", valid_to=datetime(2026, 9, 1))
    assert plan_valid_until(m, NOW) == datetime(2026, 9, 1)
    m2 = _mem(sub_type="plan", content="下周去长沙",
              valid_to=datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert plan_valid_until(m2, NOW) == datetime(2026, 9, 1)  # tz-aware 归一为 naive


def test_plan_not_expired_future():
    m = _mem(content="用户计划下周去长沙", created_at=NOW)
    assert is_plan_expired(m, NOW) is False
    assert is_plan_expired(m, NOW + timedelta(days=20)) is True


def test_plan_valid_until_non_plan_none():
    assert plan_valid_until(_mem(content="今天在橘子洲游玩"), NOW) is None
    assert is_plan_expired(_mem(content="今天在橘子洲游玩"), NOW) is False
    assert is_plan_expired(_mem(memory_type="preference", content="用户不吃香菜"), NOW) is False


def test_days_since():
    assert days_since(_mem(created_at=datetime(2026, 8, 15)), NOW) == 25
    assert days_since(_mem(created_at=NOW), NOW) == 0
    assert days_since(_mem(created_at=NOW), NOW) is not None


# ────────────────────────── L2：强化分流收口（纯对象） ──────────────────────────

def _reinforce(m, factor=1.3 * 4 / 3, channel="review", times=1):
    from app.memory.service import _apply_reinforce
    for _ in range(times):
        _apply_reinforce(m, factor, NOW, channel=channel)
    return m


def test_reinforce_expired_plan_capped_hard():
    m = _mem(content="用户近期将去长沙", strength_days=60.0, importance=119.0, review_count=14)
    _reinforce(m)  # 已达次数上限（14>=3）→ 不再强化，退出复习轮转
    assert m.strength_days == 60.0  # S 不变（不再翻倍）
    assert m.next_review_at is None


def test_reinforce_plan_s_cap_and_count_cap():
    m = _mem(content="用户计划下周去长沙", created_at=NOW, strength_days=3.0, review_count=0)
    for i in range(3):
        _reinforce(m)
        assert m.strength_days <= 30.0  # 有效期内计划：适度上限
    # 从 0 次开始强化 3 次（episodic 档：次数上限 6 未到），S 有界
    assert m.review_count == 3
    assert m.next_review_at is not None


def test_reinforce_episodic_moderate_cap():
    from app.memory.constants import EPISODIC_REVIEW_S_CAP, EPISODIC_REVIEW_COUNT_CAP
    m = _mem(content="今天在橘子洲游玩，拍了好多照片", strength_days=3.0, review_count=0)
    _reinforce(m, times=EPISODIC_REVIEW_COUNT_CAP)
    assert m.strength_days <= EPISODIC_REVIEW_S_CAP  # 适度空间：可超 10 但封顶 30
    assert m.strength_days > 10.0
    assert m.next_review_at is None  # 达次数上限 → 退出复习轮转
    s_before = m.strength_days
    _reinforce(m)  # 超上限后再强化：S 冻结
    assert m.strength_days == s_before


def test_reinforce_enduring_no_cap():
    m = _mem(memory_type="preference", content="用户不吃香菜", strength_days=40.0, review_count=10)
    _reinforce(m, times=5)
    assert m.strength_days == 60.0  # 恒久记忆可走到 S_MAX
    assert m.next_review_at == NOW + timedelta(days=60.0)


def test_reinforce_retrieve_channel_unaffected():
    m = _mem(content="今天在橘子洲游玩", strength_days=3.0, review_count=0)
    _reinforce(m, channel="retrieve", times=10)
    assert m.strength_days == 60.0  # 检索命中不受 event 复习上限影响
    assert m.next_review_at is not None


def test_reinforce_flag_off_old_behavior():
    def _run(monkeypatch):
        _set_flag(monkeypatch, "review_reinforce_event_cap", False)
        m = _mem(content="用户近期将去长沙", strength_days=40.0, review_count=2)
        _reinforce(m)
        return m

    m = _run(pytest.MonkeyPatch())
    assert m.strength_days == 60.0  # 旧行为：40×1.73 顶到 S_MAX（无类型上限）
    assert m.next_review_at is not None


def test_reinforce_locked_untouched():
    m = _mem(content="用户近期将去长沙", strength_days=30.0, is_locked=True)
    _reinforce(m)
    assert m.strength_days == 30.0
    assert m.review_count == 0


# ────────────────────────── DB 夹具（L1 选片 / L3 组装 / 限频） ──────────────────────────


@pytest.fixture()
def r_db(monkeypatch, tmp_path):
    """临时 SQLite 文件库：monkeypatch memory_review 的 async_session_factory（不触碰 backend/data）。"""
    import app.scheduling.memory_review as mr

    tmp = str(tmp_path)
    db_path = os.path.join(tmp, "t.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    monkeypatch.setattr(mr, "async_session_factory", factory)
    # C3（2026-09-10）：现状锚点已下沉到 app.memory.current_state（其内部从 app.db.database
    # 取会话工厂），这里一并 patch，保证复习用例仍走同一临时库。
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory, engine
    asyncio.run(engine.dispose())


async def _seed_memory(factory, **kw):
    async with factory() as db:
        m = Memory(**kw)
        db.add(m)
        await db.commit()
        await db.refresh(m)
        return m


def _plan_kw(cid, **kw):
    base = dict(
        user_id=3, character_id=cid, memory_type="event",
        content="用户近期将去长沙，期间可能因不便携带电脑而断联",
        importance=119.0, strength_days=60.0, review_count=14,
        next_review_at=datetime(2026, 9, 8, 0, 0, 0),
        created_at=datetime(2026, 8, 15, 10, 0, 0),
    )
    base.update(kw)
    return base


def _seed_session(factory, cid, uid=3, active=True):
    from app.models.chat import ChatSession

    async def _go():
        async with factory() as db:
            s = ChatSession(user_id=uid, character_id=cid, is_active=active)
            db.add(s)
            await db.commit()
            await db.refresh(s)
            return s.id

    return asyncio.run(_go())


def test_collect_review_events_excludes_expired_plan(r_db, monkeypatch):
    factory, _ = r_db
    asyncio.run(_seed_memory(factory, **_plan_kw(101)))                       # 过期计划
    asyncio.run(_seed_memory(factory, **_plan_kw(102, content="状态更新：正在逛街",
                                                  sub_type="status", importance=80.0)))  # 瞬时状态
    asyncio.run(_seed_memory(factory, user_id=3, character_id=103, memory_type="preference",
                             content="用户不吃香菜", importance=60.0,
                             next_review_at=datetime(2026, 9, 8),
                             created_at=datetime(2026, 8, 1)))               # enduring
    asyncio.run(_seed_memory(factory, user_id=3, character_id=104, memory_type="event",
                             content="用户计划下周去长沙", importance=70.0,
                             next_review_at=datetime(2026, 9, 8),
                             created_at=datetime.now(timezone.utc).replace(tzinfo=None)))  # 未过期计划
    for cid in (101, 102, 103, 104):
        _seed_session(factory, cid)

    from app.scheduling.memory_review import collect_review_events
    events = asyncio.run(collect_review_events())
    chars = {e["candidate"]["character_id"] for e in events}
    assert chars == {103, 104}  # 过期计划/瞬时状态不进主动复习；enduring 与未过期计划正常进

    # flag 关 = 回到旧选片（全部到期记忆都进候选）
    _set_flag(monkeypatch, "review_exclude_expired_plan", False)
    events = asyncio.run(collect_review_events())
    chars = {e["candidate"]["character_id"] for e in events}
    assert chars == {101, 102, 103, 104}


def test_collect_review_events_stale_plan_also_excluded(r_db):
    """置 stale 的过期计划同样不进（时态过滤与状态双保险，不依赖 memory_supersede flag）。"""
    factory, _ = r_db
    asyncio.run(_seed_memory(factory, **_plan_kw(101, status="stale")))
    _seed_session(factory, 101)
    from app.scheduling.memory_review import collect_review_events
    events = asyncio.run(collect_review_events())
    assert events == []


def test_pick_contextual_memory_excludes_expired_plan(r_db, monkeypatch):
    factory, _ = r_db
    plan_id = asyncio.run(_seed_memory(factory, **_plan_kw(201))).id
    pref_id = asyncio.run(_seed_memory(factory, user_id=3, character_id=201,
                                       memory_type="preference", content="用户不吃香菜",
                                       importance=60.0,
                                       created_at=datetime(2026, 8, 1))).id
    asyncio.run(_seed_memory(factory, user_id=3, character_id=201, memory_type="event",
                             content="今天在橘子洲游玩", importance=50.0,
                             created_at=datetime(2026, 8, 10)))
    from app.scheduling.memory_review import _pick_contextual_memory
    got = asyncio.run(_pick_contextual_memory(201, 3, "最近怎么样"))
    assert got == pref_id  # importance 最高的过期计划被跳过

    _set_flag(monkeypatch, "review_exclude_expired_plan", False)
    got = asyncio.run(_pick_contextual_memory(201, 3, "最近怎么样"))
    assert got == plan_id  # flag 关 = 旧行为（按 importance 选中过期计划）


# ────────────────────────── L3：回忆框架 / 闸门 / 限频 ──────────────────────────

def test_review_phrase_tenses():
    from app.scheduling.memory_review import _review_phrase
    # 过期计划 → 已经过去的旧安排（怀旧）
    phrase, nostalgia, kind = _review_phrase(_mem(content="用户近期将去长沙"), NOW)
    assert nostalgia is True and kind == "nostalgia"
    assert "已经过去的旧安排" in phrase and "距今约 25 天" in phrase
    # 未过期计划 → 还没到的安排
    phrase, nostalgia, kind = _review_phrase(_mem(content="用户计划下周去长沙", created_at=NOW), NOW)
    assert nostalgia is False and kind == "plan"
    assert "还没到的安排" in phrase
    # 往事 → 回忆
    phrase, nostalgia, kind = _review_phrase(_mem(content="今天在橘子洲游玩"), NOW)
    assert nostalgia is True and kind == "nostalgia"
    assert "往事" in phrase
    # 恒久
    phrase, nostalgia, kind = _review_phrase(_mem(memory_type="preference", content="用户不吃香菜"), NOW)
    assert nostalgia is False and kind == "enduring"
    # 距今 <3 天不附日期
    phrase, _, _ = _review_phrase(_mem(content="今天在橘子洲游玩", created_at=NOW), NOW)
    assert "距今" not in phrase


def test_wrong_tense_directive_gate():
    from app.scheduling.memory_review import _is_wrong_tense_directive
    # Sam 回归："电脑记得带"这类当下叮嘱被拦
    assert _is_wrong_tense_directive("电脑记得带，背不动就放家，别逞强。", True) is True
    assert _is_wrong_tense_directive("明天记得报平安。", True) is True
    # 正常回忆放行
    assert _is_wrong_tense_directive("我记得你去长沙那回还嫌电脑沉。", True) is False
    assert _is_wrong_tense_directive("上次那趟长沙玩得开心吧？", True) is False
    # 非怀旧不拦
    assert _is_wrong_tense_directive("电脑记得带，背不动就放家。", False) is False
    assert _is_wrong_tense_directive("", True) is False


@pytest.fixture()
def run_review_env(r_db, monkeypatch):
    """run_memory_review 执行环境：LLM/发送/依赖全部 mock，捕获 hint 与 extra_meta。"""
    import app.scheduling.memory_review as mr

    factory, _ = r_db
    captured = {"hint": "", "extra_meta": None, "sent": False, "text": ""}

    async def _fake_chat_completion(messages, **kw):
        captured["hint"] = messages[1]["content"]
        return captured.get("reply") or "我还记得那回事呢。"

    async def _fake_send(session_id, character_id, user_id, content, message_type, extra_meta):
        captured["sent"] = True
        captured["text"] = content
        captured["extra_meta"] = extra_meta
        return True

    async def _noop_true(*a, **k):
        return True

    async def _noop_none(*a, **k):
        return None

    async def _noop_false(*a, **k):
        return False

    async def _session_id(*a, **k):
        return 1
    import app.scheduling.scheduler as scheduler_mod
    import app.application.chat_service as chat_service_mod
    import app.scheduling.triggers as triggers_mod
    import app.agent.llm_client as llm_mod
    import app.agent.user_profile as profile_mod
    import app.agent.persona as persona_mod
    monkeypatch.setattr(scheduler_mod, "send_to_session", _fake_send)
    monkeypatch.setattr(chat_service_mod, "get_latest_session_id", _session_id)
    monkeypatch.setattr(triggers_mod, "memory_review_enabled", _noop_true)
    monkeypatch.setattr(triggers_mod, "get_last_messages", _noop_none)
    monkeypatch.setattr(llm_mod, "chat_completion", _fake_chat_completion)
    monkeypatch.setattr(llm_mod, "load_character_reasoning_level", _noop_none)
    monkeypatch.setattr(profile_mod, "build_role_prompt_block", _noop_none)
    monkeypatch.setattr(persona_mod, "build_active_channel_persona", _noop_none)
    monkeypatch.setattr(mr, "_user_in_dnd_period", _noop_false)
    monkeypatch.setattr(mr, "_last_review_at", _noop_none)
    return factory, captured, monkeypatch


def test_run_review_hint_reminisce_framework(run_review_env):
    """L3 开：hint 为回忆框架（时态引导语 + 口吻规则 + F1 时间锚点保留）；extra_meta 记 tense。"""
    factory, captured, _ = run_review_env
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="今天在橘子洲游玩，拍了好多照片")))
    from app.scheduling.memory_review import run_memory_review
    ok = asyncio.run(run_memory_review(13, 3, m.id))
    assert ok is True and captured["sent"] is True
    hint = captured["hint"]
    assert "你回忆起一段**往事**" in hint
    assert "已经发生过 / 已经过期的往事" in hint          # 时态口吻规则
    assert "禁止" in hint and "记得带" in hint            # 禁当下叮嘱
    assert "现在是北京时间" in hint                        # F1 时间锚点保留
    import json
    meta = json.loads(captured["extra_meta"])
    assert meta == {"memory_id": m.id, "tense": "nostalgia"}


def test_run_review_hint_flag_off_old_hint(run_review_env, monkeypatch):
    """L3/L1 全关：逐字节回到旧 hint（含「你想起了{label}」文案），extra_meta 不含 tense。"""
    factory, captured, _ = run_review_env
    _set_flag(monkeypatch, "review_reminisce_framework", False)
    _set_flag(monkeypatch, "review_exclude_expired_plan", False)
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="今天在橘子洲游玩")))
    from app.scheduling.memory_review import run_memory_review
    ok = asyncio.run(run_memory_review(13, 3, m.id))
    assert ok is True
    hint = captured["hint"]
    assert "你想起了发生过的事：" in hint
    assert "已经发生过 / 已经过期的往事" not in hint
    assert "现在是北京时间" in hint  # F1 仍在
    import json
    meta = json.loads(captured["extra_meta"])
    assert "tense" not in meta


def test_run_review_wrong_tense_directive_blocked(run_review_env):
    """输出闸门：怀旧记忆生成"电脑记得带"叮嘱 → 拦截不发送（Sam 09-08 回归）。"""
    factory, captured, _ = run_review_env
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="用户近期将去长沙")))
    captured["reply"] = "电脑记得带，背不动就放家，别逞强。"
    from app.scheduling.memory_review import run_memory_review
    ok = asyncio.run(run_memory_review(13, 3, m.id))
    assert ok is False
    assert captured["sent"] is False


def test_run_review_status_anchor_injected(run_review_env):
    """现状锚点：有 active 用户状态事实时注入"TA 当前已知现状"。"""
    from app.models.memory import WorldFact
    factory, captured, _ = run_review_env

    async def _seed_fact():
        async with factory() as db:
            db.add(WorldFact(user_id=3, character_id=13, subject_type="user", subject_id=3,
                             predicate="status", object_value="开学住校中，周末回家",
                             status="active"))
            await db.commit()

    asyncio.run(_seed_fact())
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="用户近期将去长沙")))
    from app.scheduling.memory_review import run_memory_review
    ok = asyncio.run(run_memory_review(13, 3, m.id))
    assert ok is True
    assert "TA 当前已知现状" in captured["hint"]
    assert "开学住校中" in captured["hint"]


def test_run_review_nostalgia_daily_quota(run_review_env, monkeypatch):
    """怀旧限频：同角色今天已发 1 条 nostalgia → 第 2 条被额度拦截。"""
    from app.models.character import ProactiveMessageLog
    factory, captured, _ = run_review_env

    async def _seed_log():
        async with factory() as db:
            db.add(ProactiveMessageLog(
                character_id=13, message_type="memory_review", content="x",
                extra_meta='{"memory_id": 999, "tense": "nostalgia"}',
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            await db.commit()

    asyncio.run(_seed_log())
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="今天在橘子洲游玩")))
    from app.scheduling.memory_review import run_memory_review
    ok = asyncio.run(run_memory_review(13, 3, m.id))
    assert ok is False
    assert captured["sent"] is False


def test_daily_count_by_tense(r_db):
    from app.models.character import ProactiveMessageLog
    from app.scheduling.memory_review import _daily_count_by_tense
    factory, _ = r_db
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _seed():
        async with factory() as db:
            db.add(ProactiveMessageLog(character_id=13, message_type="memory_review", content="a",
                                       extra_meta='{"memory_id": 1, "tense": "nostalgia"}',
                                       created_at=now))
            db.add(ProactiveMessageLog(character_id=13, message_type="memory_review", content="b",
                                       extra_meta='{"memory_id": 2, "tense": "enduring"}',
                                       created_at=now))
            db.add(ProactiveMessageLog(character_id=13, message_type="memory_review", content="c",
                                       extra_meta='{"memory_id": 3, "tense": "nostalgia"}',
                                       created_at=now - timedelta(days=2)))
            db.add(ProactiveMessageLog(character_id=14, message_type="memory_review", content="d",
                                       extra_meta='{"memory_id": 4, "tense": "nostalgia"}',
                                       created_at=now))
            await db.commit()

    asyncio.run(_seed())

    async def _count():
        async with factory() as db:
            return await _daily_count_by_tense(db, 13, "nostalgia")

    assert asyncio.run(_count()) == 1  # 只计"今天 + 本角色 + nostalgia"


def test_maybe_review_success_passes_review_channel(r_db, monkeypatch):
    """maybe_review_success → reinforce_memories(channel='review')。"""
    from app.models.character import ProactiveMessageLog
    factory, _ = r_db
    m = asyncio.run(_seed_memory(factory, **_plan_kw(13, content="用户近期将去长沙",
                                                     review_count=0, strength_days=3.0)))

    async def _seed_log():
        async with factory() as db:
            db.add(ProactiveMessageLog(
                character_id=13, message_type="memory_review", content="x",
                extra_meta=json.dumps({"memory_id": m.id}, ensure_ascii=False),
                created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            await db.commit()

    import json
    asyncio.run(_seed_log())

    captured = {}

    async def _fake_reinforce(ids, factor, debounce_hours=0.0, *, channel="retrieve"):
        captured["channel"] = channel

    import app.memory.service as service_mod
    monkeypatch.setattr(service_mod, "reinforce_memories", _fake_reinforce)
    from app.scheduling.memory_review import maybe_review_success
    n = asyncio.run(maybe_review_success(3, 13, "嗯嗯我知道了，那天我会少带点东西"))
    assert n == 1
    assert captured["channel"] == "review"
