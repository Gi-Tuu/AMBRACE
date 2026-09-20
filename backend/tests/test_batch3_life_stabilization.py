# -*- coding: utf-8 -*-
"""批次三「AI Life 止血」回归测试（2026-09-16）。

覆盖 5 个任务：
- 任务1 study 不再空转 / 无真实文本不写记忆 / 不同活动不复用 memory_id；
- 任务2 禁止凭空生成亲属（活动内容与记忆都不落凭空亲属）；
- 任务3 空间模型支持住校（dorm/campus/canteen/library；宿舍无厨房 → 不落 kitchen）；
- 任务4 作息状态机（scheduled→active→completed）+ 当天补生成 + overdue 仅异常兜底；
- 任务5 needs 曲线不再长期贴边（顶格 100 / 单科 4）。

纪律：纯逻辑优先；需要 DB 的用例一律 tmp_path 临时 SQLite，绝不碰生产库。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.life import activity, life_loop, schedule, space
from app.life.decision import ACTIONS, Decision, StateSnapshot, decide
from app.life.life_loop import LifeLoopTask
from app.life.life_state import NEEDS, default_needs, settle_needs

pytestmark = pytest.mark.slow

_NEEDS = {k: 50 for k in NEEDS}


def _snap(**over) -> StateSnapshot:
    base = dict(
        character_id=1, user_id=1, energy=70, focus=50,
        needs=dict(_NEEDS), phase="afternoon", mood=50,
        fatigue=30, anger=10, location="home", current_room="living",
    )
    base.update(over)
    return StateSnapshot(**base)


class _Char:
    def __init__(self, cid=1, name="小爱"):
        self.id = cid
        self.name = name
        self.user_id = 1
        self.relationship_summary = "普通朋友"
        self.relation_type = "朋友"


# ═══════════════════════ 任务3：空间模型 ═══════════════════════

def test_space_只有老家有厨房():
    assert space.has_kitchen("home") is True
    for loc in ("dorm", "campus", "canteen", "library", "world", None):
        assert space.has_kitchen(loc) is False


def test_space_外出与基地地点判定():
    for loc in ("world", "friend", "outside", "exit"):
        assert space.is_away(loc) is True
    for loc in ("home", "dorm", "campus", "canteen", "library", None, ""):
        assert space.is_away(loc) is False
    for loc in ("home", "dorm", "campus", "canteen", "library"):
        assert space.at_base(loc) is True
    assert space.at_base("world") is False


def test_space_room_for_宿舍不会落厨房():
    # 宿舍吃饭 → 食堂（不再凭空 kitchen 炖肉）
    assert space.room_for("dorm", "kitchen", "eat") == "canteen"
    # 宿舍学习/喝咖啡 → 宿舍卧室（宿舍真实存在的房间）
    assert space.room_for("dorm", "bedroom", "study") == "bedroom"
    assert space.room_for("dorm", "kitchen", "coffee") == "bedroom"
    # 图书馆不会出现卧室；教学楼不会出现客厅
    assert space.room_for("library", "bedroom", "study") == "library"
    assert space.room_for("campus", "living", "create") == "classroom"
    # 家里（有厨房）保持原样
    assert space.room_for("home", "kitchen", "eat") == "kitchen"
    assert space.room_for("home", "living") == "living"


def test_space_住校白天按作息分布():
    assert space.is_term_time() is True
    assert space.home_base() == ("dorm", "bedroom")
    assert space.sleep_location() == ("dorm", "bedroom")
    assert space.student_day_location("morning", 6) == ("dorm", "bedroom")
    assert space.student_day_location("morning", 9) == ("campus", "classroom")
    assert space.student_day_location("afternoon", 13) == ("canteen", "canteen")
    assert space.student_day_location("afternoon", 15) == ("campus", "classroom")
    assert space.student_day_location("evening", 20) == ("library", "library")
    assert space.student_day_location("sleep", 23) == ("dorm", "bedroom")


def test_space_normalize_location_字面home按住校归一():
    assert space.normalize_location("home") == "dorm"
    assert space.normalize_location("world") == "world"
    assert space.normalize_location(None) == "home"  # 空值兜底为字面 home


def test_space_空间话术守卫禁止做饭():
    guard = space.space_guard("dorm")
    assert "宿舍" in guard and "没有厨房" in guard
    assert "做饭" in guard and "等你回家" in guard


# ═══════════════════════ 任务3：决策器门控 ═══════════════════════

def test_decision_宿舍是基地不被强制回家():
    """住校角色在宿舍/图书馆时不再被判「人在外面」而反复 return_home。"""
    for loc in ("dorm", "campus", "canteen", "library"):
        act = decide(_snap(location=loc, phase="evening", energy=80)).action
        assert act != "return_home", loc


def test_decision_外出地点仍然门控():
    assert decide(_snap(location="world", phase="evening", energy=80)).action == "return_home"
    assert decide(_snap(location="friend", energy=30, phase="afternoon")).action == "return_home"


def test_decision_return_home仍是字面home():
    """纯函数保持字面落点（真正落点由 life_loop 经 space.normalize_location 归一）。"""
    assert ACTIONS["return_home"].location_to == "home"
    assert ACTIONS["sleep"].location_to == "home"


# ═══════════════════════ 任务2：亲属守卫 ═══════════════════════

def test_relations_无设定时任何亲属都算凭空():
    from app.life import relations

    assert relations.mentions_unspecified_relation("陪妈妈逛菜市场") is True
    assert relations.mentions_unspecified_relation("和家人一起吃晚饭") is True
    assert relations.mentions_unspecified_relation("奶奶今天打电话来") is True
    # 非亲属内容放行
    assert relations.mentions_unspecified_relation("学了一会儿新东西") is False
    assert relations.mentions_unspecified_relation("") is False


def test_relations_明确设定过的称谓放行():
    from app.life import relations

    assert relations.mentions_unspecified_relation("陪妈妈逛菜市场", ["妈妈"]) is False
    # 用户本人（伴侣关系称谓）也属明确设定
    assert relations.mentions_unspecified_relation("和老公说了会话", ["老公"]) is False
    assert relations.mentions_unspecified_relation("和老公说了会话", []) is True


def test_activity_guard_relation_content_替换并标记():
    content, blocked = activity.guard_relation_content("小爱", "陪母亲散步", [])
    assert blocked is True
    assert "母亲" not in content and "小爱" in content
    content2, blocked2 = activity.guard_relation_content("小爱", "整理了一段记忆", [])
    assert blocked2 is False and content2 == "整理了一段记忆"


def test_resolve_known_people_读用户昵称与设定称谓():
    from app.life import relations

    class _User:
        nickname = "sam"
        username = "sam_admin"

    class _CharRow:
        user_id = 7
        relationship_summary = "你和用户是对象/伴侣关系"
        relation_type = "恋人"

    class _DB:
        async def get(self, model, pk):
            return _User() if pk == 7 else _CharRow()

    people = asyncio.run(relations.resolve_known_people(_DB(), 1))
    assert "sam" in people and "sam_admin" in people
    assert "伴侣" in people or "对象" in people  # 设定里明确的伴侣称谓


def test_resolve_known_people_异常时保守返回空():
    from app.life import relations

    class _Broken:
        async def get(self, model, pk):
            raise RuntimeError("db down")

    assert asyncio.run(relations.resolve_known_people(_Broken(), 1)) == []


# ═══════════════════════ 任务5：needs 曲线 ═══════════════════════

def test_needs_顶格不再贴边100():
    needs = {k: 100 for k in NEEDS}
    for _ in range(40):
        needs = settle_needs(needs)
    for k in NEEDS:
        assert 55 <= needs[k] <= 85, (k, needs[k])   # 收敛在均衡区，不再恒 100


def test_needs_从4回升不再单科贴边():
    needs = {k: 50 for k in NEEDS}
    needs["learning"] = 4
    for _ in range(12):
        needs = settle_needs(needs)
    assert needs["learning"] > 20      # 被耗光的 learning 会真实回升
    assert needs["learning"] <= 100


def test_needs_被满足后立即下降且不弹回():
    needs = {k: 50 for k in NEEDS}
    out = settle_needs(needs, {"curiosity": 15})
    assert out["curiosity"] <= 43      # 满足量生效，回升不吞掉满足
    assert out["curiosity"] < 50
    for k in NEEDS:
        assert 0 <= out[k] <= 100


def test_needs_多轮随机满足仍在界内():
    needs = default_needs()
    for i in range(30):
        needs = settle_needs(needs, {"learning": 12, "curiosity": 5} if i % 3 == 0 else None)
        for k in NEEDS:
            assert 0 <= needs[k] <= 100


# ═══════════════════════ 任务1：记忆沉淀闸门（纯逻辑） ═══════════════════════

def test_is_meaningful_summary_恒定模板不沉淀():
    task = LifeLoopTask()
    char = _Char()
    tpl = task._template_summary(char, Decision("study"), ACTIONS["study"])
    assert task.is_meaningful_summary(tpl, tpl) is False          # 恒定模板句 → 不写记忆
    assert task.is_meaningful_summary("", tpl) is False
    assert task.is_meaningful_summary(None, tpl) is False
    assert task.is_meaningful_summary("小爱学了「学一个新技能」，整理了一遍要点。", tpl) is True
    # 凭空亲属内容 → 不沉淀
    assert task.is_meaningful_summary("小爱陪妈妈散步，很开心。", tpl, True) is False


def test_is_meaningful_summary_非study模板同样拦截():
    task = LifeLoopTask()
    char = _Char()
    tpl = task._template_summary(char, Decision("walk"), ACTIONS["walk"])
    assert task.is_meaningful_summary(tpl, tpl) is False


def test_life_writer_禁止复用memory_id走skip_dedup(monkeypatch):
    from app.life import life_writer

    captured = {}

    async def _fake_save(**kw):
        captured.update(kw)

        class _Mem:
            id = 4242

        return _Mem()

    monkeypatch.setattr("app.memory.service.save_memory", _fake_save)
    mem = asyncio.run(life_writer.save_life_memory_with_retry(
        user_id=1, character_id=1, content="真实内容", memory_type="event"))
    assert mem.id == 4242
    assert captured.get("skip_dedup") is True      # 不复用旧 memory_id（跳过写入查重合并）

    captured.clear()
    asyncio.run(life_writer.save_life_memory_with_retry(
        user_id=1, character_id=1, content="真实内容", memory_type="event", skip_dedup=False))
    assert captured.get("skip_dedup") is False     # 调用方显式覆盖仍然生效


# ═══════════════════════ DB 夹具（tmp_path 临时 SQLite） ═══════════════════════

@pytest.fixture()
def life_db(tmp_path):
    """临时 SQLite（tmp_path）：建全部模型表，绝不触碰 backend/data 生产库。"""
    db_path = str(tmp_path / "batch3_life.db")
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  # 注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    yield factory
    asyncio.run(engine.dispose())


async def _add_character(factory, cid=1, user_id=1, name="小爱"):
    from app.models.character import AICharacter
    async with factory() as db:
        db.add(AICharacter(id=cid, user_id=user_id, name=name, is_active=True))
        await db.commit()


# ═══════════════════════ 任务4：作息状态机 ═══════════════════════

def _now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_schedule_状态机正常流转(life_db):
    """到点 scheduled→active；结束 active→completed；漏太久才 overdue。"""
    from app.models.life import LifeSchedule

    now = _now_naive()

    async def _run():
        async with life_db() as db:
            db.add_all([
                # 到点未结束 → active
                LifeSchedule(user_id=1, character_id=1, title="进行中", start_time=now - timedelta(minutes=30),
                             end_time=now + timedelta(minutes=30), status="scheduled", source="ai_generated"),
                # 到点已结束 → completed
                LifeSchedule(user_id=1, character_id=1, title="刚结束", start_time=now - timedelta(hours=2),
                             end_time=now - timedelta(hours=1), status="active", source="ai_generated"),
                # 未到点 → scheduled 不动
                LifeSchedule(user_id=1, character_id=1, title="未来", start_time=now + timedelta(hours=2),
                             end_time=now + timedelta(hours=3), status="scheduled", source="ai_generated"),
                # 漏太久（>6h 宽限）→ overdue 异常兜底
                LifeSchedule(user_id=1, character_id=1, title="漏掉", start_time=now - timedelta(days=3),
                             end_time=now - timedelta(days=3) + timedelta(hours=1), status="scheduled",
                             source="fixed_routine", recurrence="daily"),
                # 当天补生成的已过作息（宽限内）→ 同一轮 scheduled→active→completed
                LifeSchedule(user_id=1, character_id=1, title="今天已过", start_time=now - timedelta(hours=4),
                             end_time=now - timedelta(hours=3), status="scheduled",
                             source="fixed_routine", recurrence="daily"),
            ])
            await db.commit()
            await schedule.advance_schedules(db, 1)
            rows = {s.title: s for s in (await db.execute(
                select(LifeSchedule).where(LifeSchedule.character_id == 1))).scalars().all()}

        assert rows["进行中"].status == "active"
        assert rows["刚结束"].status == "completed" and rows["刚结束"].completed_at is not None
        assert rows["未来"].status == "scheduled"
        assert rows["漏掉"].status == "overdue"
        # 当天补生成的过期作息正常收敛为 completed（不再一律 overdue）
        assert rows["今天已过"].status == "completed"

    asyncio.run(_run())


def test_schedule_当天固定作息可补生成且幂等(life_db):
    """9-16 漏生成「起床」的修复：当天任一时刻都补生成，且重复调用不重复建。"""
    async def _run():
        async with life_db() as db:
            first = await schedule.ensure_fixed_routines(db, 1, 1)
            second = await schedule.ensure_fixed_routines(db, 1, 1)
            titles = sorted(s.title for s in (await db.execute(
                select(schedule.LifeSchedule).where(schedule.LifeSchedule.character_id == 1))).scalars().all())
        assert first == 3                      # 起床/午休/睡觉 全部补生成
        assert second == 0                     # 幂等
        assert titles == ["午休", "睡觉", "起床"]

    asyncio.run(_run())


def test_schedule_活跃上限不阻断固定作息(life_db):
    """已有 5 条活跃日程时，固定作息仍必须能补生成（否则作息骨架丢失）。"""
    from app.models.life import LifeSchedule

    now = _now_naive()

    async def _run():
        async with life_db() as db:
            for i in range(schedule.MAX_ACTIVE):
                db.add(LifeSchedule(user_id=1, character_id=1, title=f"占位{i}",
                                    start_time=now + timedelta(hours=i + 1),
                                    end_time=now + timedelta(hours=i + 2),
                                    status="scheduled", source="ai_generated"))
            await db.commit()
            created = await schedule.ensure_fixed_routines(db, 1, 1)
        assert created == 3

    asyncio.run(_run())


def test_schedule_tick_重复调用不重复生成(life_db):
    async def _run():
        async with life_db() as db:
            out1 = await schedule.schedule_tick(db, 1, 1)
            out2 = await schedule.schedule_tick(db, 1, 1)
        assert out1["routines"] == 3
        assert out2["routines"] == 0

    asyncio.run(_run())


# ═══════════════════════ 任务1：study 具体总结 ═══════════════════════

def test_study_总结取真实主题(life_db):
    """study 产出的总结包含真实目标标题，且不是恒定模板句。"""
    from app.models.life import LifeGoal

    asyncio.run(_add_character(life_db))
    task = LifeLoopTask()
    char = _Char()

    async def _run():
        async with life_db() as db:
            db.add(LifeGoal(character_id=1, type="skill", title="学一个新技能",
                            status="active", priority=2, progress_total=3))
            await db.commit()
            summary = await task._study_summary(db, char)
        return summary

    summary = asyncio.run(_run())
    assert "学一个新技能" in summary
    tpl = task._template_summary(char, Decision("study"), ACTIONS["study"])
    assert summary != tpl
    assert task.is_meaningful_summary(summary, tpl) is True


def test_study_无真实主题回落模板被闸门拦截(life_db, monkeypatch):
    """取不到真实主题 → 回落模板 → 判定无意义 → 不沉淀（memory_id=null 路径）。"""
    from app.agent.loop import AGENT_FLAGS

    monkeypatch.setitem(AGENT_FLAGS, "life_loop_llm", False)  # 不走真实 LLM
    asyncio.run(_add_character(life_db))
    task = LifeLoopTask()
    char = _Char()

    async def _run():
        async with life_db() as db:
            summary = await task._build_summary(db, char, Decision("study"), ACTIONS["study"])
        return summary

    summary = asyncio.run(_run())
    assert summary == task._template_summary(char, Decision("study"), ACTIONS["study"])
    assert task.is_meaningful_summary(summary, summary) is False


# ═══════════════════════ 任务1/2：活动执行落库闸门 ═══════════════════════

def _patch_pick(monkeypatch, name):
    async def _pick(*a, **kw):
        return name
    monkeypatch.setattr(activity, "_pick_activity", _pick)
    monkeypatch.setattr(activity.random, "random", lambda: 0.0)  # 概率闸门必过


def test_rest纯数值活动不写记忆且summary为null(life_db, monkeypatch):
    asyncio.run(_add_character(life_db))
    _patch_pick(monkeypatch, "rest")

    async def _run():
        async with life_db() as db:
            from app.models.character import AICharacter
            char = await db.get(AICharacter, 1)
            log = await activity.run_activity(db, 1, char, "afternoon", dict(_NEEDS), 80, "high")
            meta = json.loads(log.output_json or "{}")
            return log, meta

    log, meta = asyncio.run(_run())
    assert log.status == "completed"
    assert log.memory_id is None          # 无真实文本 → 不写记忆
    assert meta["summary"] is None        # 不再用空串占位
    assert meta["placeholder"] is True


def test_亲属内容不写记忆不落产物且summary为null(life_db, monkeypatch):
    asyncio.run(_add_character(life_db))
    _patch_pick(monkeypatch, "learn")

    async def _fake_gen(db, user_id, character, name):
        return "今天陪妈妈去菜市场买了菜，顺便给家里做了顿饭。", None

    monkeypatch.setattr(activity, "_generate_content", _fake_gen)

    async def _run():
        async with life_db() as db:
            from app.models.character import AICharacter
            from app.models.life import LifeArtifact
            char = await db.get(AICharacter, 1)
            log = await activity.run_activity(db, 1, char, "afternoon", dict(_NEEDS), 80, "high")
            meta = json.loads(log.output_json or "{}")
            arts = (await db.execute(select(LifeArtifact).where(
                LifeArtifact.character_id == 1))).scalars().all()
            return log, meta, len(arts)

    log, meta, n_artifacts = asyncio.run(_run())
    assert log.memory_id is None
    assert meta["summary"] is None
    assert meta["relation_blocked"] is True
    assert n_artifacts == 0               # 凭空亲属内容不落产物


def test_真实内容才写记忆(life_db, monkeypatch):
    asyncio.run(_add_character(life_db))
    _patch_pick(monkeypatch, "learn")

    async def _fake_gen(db, user_id, character, name):
        return "读了一篇讲潮汐的文章，把涨落周期的道理弄明白了。", None

    monkeypatch.setattr(activity, "_generate_content", _fake_gen)

    async def _fake_save(**kw):
        assert kw.get("skip_dedup") is True

        class _Mem:
            id = 8888

        return _Mem()

    monkeypatch.setattr("app.memory.service.save_memory", _fake_save)

    async def _run():
        async with life_db() as db:
            from app.models.character import AICharacter
            from app.models.life import LifeArtifact
            char = await db.get(AICharacter, 1)
            log = await activity.run_activity(db, 1, char, "afternoon", dict(_NEEDS), 80, "high")
            meta = json.loads(log.output_json or "{}")
            arts = (await db.execute(select(LifeArtifact).where(
                LifeArtifact.character_id == 1))).scalars().all()
            return log, meta, len(arts)

    log, meta, n_artifacts = asyncio.run(_run())
    assert log.memory_id == 8888
    assert meta["summary"] and meta["summary"].startswith("读了一篇讲潮汐")
    assert n_artifacts == 1


# ═══════════════════════ 任务3：life_loop 空间同步 ═══════════════════════

def test_sync_day_space_宿舍按作息流动(life_db):
    from app.models.life import LifeState

    async def _run():
        task = LifeLoopTask()
        async with life_db() as db:
            st = LifeState(character_id=1, location="home", current_room="bedroom")
            db.add(st)
            await db.commit()
            await task._sync_day_space(db, st, "evening")
        return st.location, st.current_room

    loc, room = asyncio.run(_run())
    assert (loc, room) == space.student_day_location("evening", life_loop.beijing_hour())


def test_sync_day_space_外出中不干预(life_db):
    from app.models.life import LifeState

    async def _run():
        task = LifeLoopTask()
        async with life_db() as db:
            st = LifeState(character_id=1, location="world", current_room="exit")
            db.add(st)
            await db.commit()
            await task._sync_day_space(db, st, "afternoon")
        return st.location, st.current_room

    assert asyncio.run(_run()) == ("world", "exit")
