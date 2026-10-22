# -*- coding: utf-8 -*-
"""audit13 P2-1 补漏（2026-10-13）：Life Loop 内部 study 兜底主题止血测试。

契约（对应 life_loop.py::_INTERNAL_STUDY_TOPICS / _study_summary / _build_summary）：
1. 主题命中系统内部兜底集合（「整理一段时间的记忆」）时，study **不再沉淀长期记忆**，
   且**两档 LLM 都不沉淀**：
   - `_study_summary` 对内部主题返回明确哨兵 ``None``（与"根本没主题"的 "" 区分）；
   - `_build_summary` 识别哨兵 → 直接返回恒定模板句，**不进 LLM 分支**（零 LLM 调用），
     由既有 `is_meaningful_summary` 闸门拒收；
   四条固定收获句式（有点收获/记下来备查/顺手记了几笔/整理了一遍要点）逐条验证都不沉淀；
2. 活动本体照常结算：`life_activity_logs.status=completed`、`output_json.summary=null`、
   `memory_id=null`，energy/needs 按动作结算，目标推进照旧（禁早退导致活动未完成）；
3. 真实学习主题不受影响：真实主题与内部主题同为 priority=2 时（生产取值见
   app/life/life_tick.py:148，元组第 4 位是 progress_total 不是 priority）靠 `id desc` 决胜，
   真实主题 + 四条普通收获句照常沉淀（不按普通短语全局拒收），且在 llm=True 档仍优先用
   确定性具体总结、不消耗 LLM 配额；
4. **无真实主题**的旧行为不变：`_study_summary` 返回 "" → llm=True 时仍走旧 LLM fallback 并写入；
5. 判据是精确匹配：标题里含「整理」字样但不是内部主题原文的，照旧沉淀（不误伤真人主题）；
6. 其它动作（create 等）不被误拦；每日 ≤5 条的既有配额口径不变；
7. 变异守卫：动态 compile 一份"撤掉内部哨兵挂点"的 life_loop 变异体，必须复现"内部主题
   在 llm=True 档沉淀"这条真语义红，且磁盘源码字节不变。

纪律：DB 一律 tmp_path 临时 SQLite（不碰 backend/data 生产库）；LLM 与记忆写入链打桩，
不真实调用模型、不写真实记忆库。挂点验证走真实 `_execute`（不是只测孤立纯函数）。
"""
import asyncio
import hashlib
import json
import pathlib
import types
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.life.decision import ACTIONS, Decision, StateSnapshot
from app.life.life_loop import _INTERNAL_STUDY_TOPICS, LifeLoopTask
from app.life.life_state import NEEDS

pytestmark = pytest.mark.slow

NEEDS50 = {k: 50 for k in NEEDS}

# _study_summary 里的 4 条固定收获句式（内部主题逐条验证不沉淀，真实主题逐条验证仍沉淀）
TAKEAWAYS = (
    "弄明白了一个之前卡住的点，顺手记了几笔。",
    "把之前模糊的地方理清了，有点收获。",
    "对其中一处细节想通了，记下来备查。",
    "整理了一遍要点，比昨天清楚一些。",
)

INTERNAL_TOPIC = "整理一段时间的记忆"
REAL_TOPIC = "机器学习入门"
# LLM 桩返回的文案：**非模板句**，一旦漏进 LLM 分支就会被 is_meaningful_summary 判为有意义
LLM_TEXT = "翻了旧笔记，发现上次的问题出在前提。"

# 生产取值：life_tick.py:141-148 内部兜底 growth 目标 priority=2（元组第 4 位是 progress_total=3）
INTERNAL_TOPIC_PRIORITY = 2


class _CharShim:
    """模板文案只需要 id/user_id/name（与 batch3 的 _Char 同形）。"""
    id = 1
    user_id = 1
    name = "小爱"


def _make_factory(path):
    """临时 SQLite：建全部模型表，绝不触碰生产库。返回（会话工厂, 引擎）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _init():
        import app.models  # noqa: F401  注册全部模型
        from app.models.base import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    return factory, engine


@pytest.fixture()
def life_db(tmp_path):
    factory, engine = _make_factory(tmp_path / "audit13_life.db")
    yield factory
    asyncio.run(engine.dispose())


def _snap() -> StateSnapshot:
    return StateSnapshot(character_id=1, user_id=1, energy=70, focus=50,
                         needs=dict(NEEDS50), phase="afternoon", mood=50, fatigue=30,
                         anger=10, location="home", current_room="living")


async def _seed(factory, *, goals):
    """goals: [(title, priority), ...]；同时建角色与 life_states 行。"""
    from app.models.character import AICharacter
    from app.models.life import LifeGoal, LifeState

    async with factory() as db:
        db.add(AICharacter(id=1, user_id=1, name="小爱", is_active=True))
        db.add(LifeState(character_id=1, energy=70, focus=50,
                         needs_json=json.dumps(NEEDS50, ensure_ascii=False),
                         phase="afternoon", location="home", current_room="living"))
        for title, prio in goals:
            db.add(LifeGoal(character_id=1, type="growth", title=title,
                            status="active", priority=prio, progress_total=3))
        await db.commit()


class _Spies(list):
    """列表本体 = 记忆写入调用记录（保持 assert mem_calls == [] 的可读写法）。"""

    def __init__(self):
        super().__init__()
        self.llm = []   # LLM 文案调用记录（用于证明「零 LLM 调用」）

    def reset(self) -> None:
        """两本台账一起清零（clear 只清列表本体，会漏掉 llm）。"""
        super().clear()
        self.llm.clear()


def _install_spies(monkeypatch, *, takeaway=None, llm_on=False, llm_text=LLM_TEXT,
                   extra_classes=()):
    """打桩记忆写入链 + LLM 文案 + 收获句式 + 事件广播。

    LLM 桩**始终装着**（并记录调用），这样两档都能断言"有没有真的调用过 LLM"：
    - llm_on=False：flag 关掉，链路不应触碰 chat_completion；
    - llm_on=True：内部主题必须在 chat_completion 之前就被哨兵拦回模板句。
    """
    from app.agent.loop import AGENT_FLAGS

    spies = _Spies()

    class _Mem:
        id = 555

    async def _save(**kw):
        spies.append(kw)
        return _Mem()

    async def _chat(messages=None, **kw):
        spies.llm.append(kw.get("task", "life_loop"))
        return llm_text

    monkeypatch.setattr("app.life.life_writer.save_life_memory_with_retry", _save)
    monkeypatch.setattr("app.agent.llm_client.chat_completion", _chat)
    if takeaway is not None:
        monkeypatch.setattr("app.life.life_loop.random.choice", lambda seq: takeaway)
    monkeypatch.setitem(AGENT_FLAGS, "life_loop_llm", llm_on)
    for cls in (LifeLoopTask, *extra_classes):
        monkeypatch.setattr(cls, "_llm_copy_allowed", lambda self, cid: True)
        monkeypatch.setattr(cls, "_bump_llm_copy", lambda self, cid: None)
        monkeypatch.setattr(cls, "_publish_event", lambda self, *a, **k: None)
    return spies


def _run_execute(factory, decision_action="study", task=None):
    """真实挂点：在临时库会话里跑一次 LifeLoopTask._execute，返回（本轮日志, energy, needs_json）。"""
    from app.models.character import AICharacter
    from app.models.life import LifeActivityLog, LifeState

    async def _go():
        async with factory() as db:
            char = await db.get(AICharacter, 1)
            st = (await db.execute(
                select(LifeState).where(LifeState.character_id == 1))).scalar_one()
            needs = json.loads(st.needs_json)
            await (task or LifeLoopTask())._execute(
                db, char, st, needs, Decision(decision_action, reason="need"), _snap())
            log = (await db.execute(
                select(LifeActivityLog).where(
                    LifeActivityLog.character_id == 1).order_by(
                    LifeActivityLog.id.desc()).limit(1))).scalars().first()
            return log, st.energy, st.needs_json

    return asyncio.run(_go())


def _probe(factory, task=None):
    """在同一份库上直接读 _study_topic / _study_summary / _build_summary 三段的取值。"""

    async def _go():
        from app.models.character import AICharacter
        async with factory() as db:
            t = task or LifeLoopTask()
            char = await db.get(AICharacter, 1)
            topic = await t._study_topic(db, 1)
            summary = await t._study_summary(db, char)
            built = await t._build_summary(db, char, Decision("study"), ACTIONS["study"])
        return t, topic, summary, built

    return asyncio.run(_go())


# ────────────────────────────── 1：内部主题 × 4 固定句式 × 两档 LLM，全部不沉淀

@pytest.mark.parametrize("takeaway", TAKEAWAYS)
@pytest.mark.parametrize("llm_on", [False, True], ids=["llm关闭", "llm开启"])
def test_内部兜底主题_两档LLM全部不沉淀(life_db, monkeypatch, takeaway, llm_on):
    """内部主题 → 哨兵 None → 回落模板 → 判无意义 → 不写记忆；LLM 开着也必须零调用。"""
    asyncio.run(_seed(life_db, goals=[(INTERNAL_TOPIC, INTERNAL_TOPIC_PRIORITY)]))
    spies = _install_spies(monkeypatch, takeaway=takeaway, llm_on=llm_on)
    task, topic, summary, built = _probe(life_db)
    tpl = task._template_summary(_CharShim(), Decision("study"), ACTIONS["study"])

    assert topic == INTERNAL_TOPIC          # 主题确实被选中（挂点真的走到）
    assert summary is None                  # 内部主题＝哨兵 None（区别于「没主题」的 ""）
    assert built == tpl                     # 直接回落恒定模板句
    assert task.is_meaningful_summary(built, tpl) is False
    assert spies.llm == []                  # 两档都没有调用 LLM

    log, _energy, _needs = _run_execute(life_db)      # 真实 _execute 挂点
    assert spies == []                                # 一条记忆都没写
    assert log.status == "completed"
    assert log.memory_id is None
    assert json.loads(log.output_json)["summary"] is None


# ────────────────────────────── 2：活动本体照常结算（禁早退），两档一致

@pytest.mark.parametrize("llm_on", [False, True], ids=["llm关闭", "llm开启"])
def test_内部主题_活动状态与完成记录不丢(life_db, monkeypatch, llm_on):
    """抑制的是「沉淀」不是活动：完成日志、energy/needs 结算、目标推进都按原流程发生。"""
    asyncio.run(_seed(life_db, goals=[(INTERNAL_TOPIC, INTERNAL_TOPIC_PRIORITY)]))
    _install_spies(monkeypatch, takeaway=TAKEAWAYS[1], llm_on=llm_on)
    act = ACTIONS["study"]

    log, energy, needs_json = _run_execute(life_db)
    needs = json.loads(needs_json)
    assert log.status == "completed"
    assert log.completed_at is not None
    assert log.memory_id is None
    assert energy == max(0, 70 - act.energy_cost)         # 精力按动作结算
    for k, v in act.needs_satisfied.items():
        assert needs[k] == max(0, 50 - v)                 # 需求按动作结算

    from app.models.life import LifeGoal

    async def _goal_after():
        async with life_db() as db:
            g = (await db.execute(
                select(LifeGoal).where(LifeGoal.character_id == 1))).scalar_one()
            return g.status, g.progress

    status, progress = asyncio.run(_goal_after())
    assert status in ("active", "completed")
    assert progress >= 0                                  # 目标推进未被打断


# ────────────────────────────── 3：真实主题照常沉淀（含 4 条普通短语）

@pytest.mark.parametrize("takeaway", TAKEAWAYS)
def test_真实学习主题_四条普通句式照常沉淀(life_db, monkeypatch, takeaway):
    """不按「有点收获/记下来备查」等普通短语全局拒收：真实主题 + 任一收获句仍写记忆。"""
    # 内部主题同时在场且同为 priority=2（生产取值），真实主题后建 → 靠 id desc 胜出
    asyncio.run(_seed(life_db, goals=[(INTERNAL_TOPIC, 2), (REAL_TOPIC, 2)]))
    spies = _install_spies(monkeypatch, takeaway=takeaway)

    log, _energy, _needs = _run_execute(life_db)
    assert len(spies) == 1
    assert REAL_TOPIC in spies[0]["content"]
    assert takeaway in spies[0]["content"]
    assert INTERNAL_TOPIC not in spies[0]["content"]
    assert log.status == "completed"
    assert log.memory_id == 555
    assert json.loads(log.output_json)["summary"] == spies[0]["content"]


def test_真实主题_llm开启档仍优先用确定性总结不烧配额(life_db, monkeypatch):
    """真实主题在两档都命中 _study_summary 的具体总结：不调 LLM、照常沉淀（原行为不变）。"""
    asyncio.run(_seed(life_db, goals=[(REAL_TOPIC, 3)]))
    spies = _install_spies(monkeypatch, takeaway=TAKEAWAYS[1], llm_on=True)

    task, topic, summary, built = _probe(life_db)
    assert topic == REAL_TOPIC
    assert summary == f"小爱学了「{REAL_TOPIC}」，{TAKEAWAYS[1]}"   # 非哨兵、非空串
    assert built == summary
    assert spies.llm == []

    log, _energy, _needs = _run_execute(life_db)
    assert len(spies) == 1 and log.memory_id == 555
    assert REAL_TOPIC in spies[0]["content"]


# ────────────────────── 4：无真实主题的旧行为不变（反例，防修复扩大化）

def test_无真实主题_llm开启档仍走旧fallback写入(life_db, monkeypatch):
    """没主题 ≠ 内部主题：_study_summary 返回 ""，life_loop_llm=True 时旧 fallback 照写。"""
    asyncio.run(_seed(life_db, goals=[]))                  # 无目标/无兴趣/无日程
    spies = _install_spies(monkeypatch, llm_on=True)

    task, topic, summary, built = _probe(life_db)
    assert topic == ""
    assert summary == ""                    # 空串（不是哨兵 None）
    assert built == LLM_TEXT                # 沿用旧的 LLM 回落
    assert spies.llm == ["life_loop"]

    log, _energy, _needs = _run_execute(life_db)
    assert len(spies) == 1
    assert spies[0]["content"] == LLM_TEXT
    assert log.status == "completed" and log.memory_id == 555


def test_无真实主题_llm关闭档回落模板不沉淀(life_db, monkeypatch):
    """没主题 + LLM 关闭：恒定模板句仍被闸门拦下（原行为不变）。"""
    asyncio.run(_seed(life_db, goals=[]))
    spies = _install_spies(monkeypatch, llm_on=False)

    task, _topic, _summary, built = _probe(life_db)
    tpl = task._template_summary(_CharShim(), Decision("study"), ACTIONS["study"])
    assert built == tpl and spies.llm == []

    log, _energy, _needs = _run_execute(life_db)
    assert spies == [] and log.memory_id is None
    assert json.loads(log.output_json)["summary"] is None


# ────────────────────────────── 5：判据精确匹配 / 其它动作不误拦 / 配额不变

def test_判据精确匹配_含整理字样的真实主题不误伤(life_db, monkeypatch):
    """真实主题里出现「整理」但不是内部主题原文 → 照旧沉淀（判据精确匹配、非子串）。"""
    real = "把「整理一段时间的记忆」这件事写进读书笔记"
    assert real not in _INTERNAL_STUDY_TOPICS
    asyncio.run(_seed(life_db, goals=[(real, 3)]))
    for llm_on in (False, True):
        spies = _install_spies(monkeypatch, takeaway=TAKEAWAYS[3], llm_on=llm_on)
        _run_execute(life_db)
        assert len(spies) == 1
        assert real in spies[0]["content"]


def test_create动作用真实LLM文案照常沉淀(life_db, monkeypatch):
    """非 study 动作完全不看 study 主题：LLM 文案照旧写记忆。"""
    asyncio.run(_seed(life_db, goals=[(INTERNAL_TOPIC, INTERNAL_TOPIC_PRIORITY)]))
    spies = _install_spies(monkeypatch, llm_on=True,
                           llm_text="今天把一段旋律写完了，很有实感。")

    log, _energy, _needs = _run_execute(life_db, decision_action="create")
    assert len(spies) == 1
    assert spies[0]["content"] == "今天把一段旋律写完了，很有实感。"
    assert log.status == "completed" and log.memory_id == 555


def test_当天配额口径不变(life_db, monkeypatch):
    """真实主题仍受同一「每角色每天 ≤5 条」闸门约束（本次改动不新增/放宽去重语义）。"""
    asyncio.run(_seed(life_db, goals=[(REAL_TOPIC, 3)]))
    from app.models.life import LifeActivityLog

    spies = _install_spies(monkeypatch, takeaway=TAKEAWAYS[0])

    async def _fill_quota():
        async with life_db() as db:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for i in range(5):
                db.add(LifeActivityLog(
                    character_id=1, activity_type="study", status="completed",
                    input_json=json.dumps({"origin": "life_loop"}),
                    output_json="{}", memory_id=900 + i, completed_at=now - timedelta(minutes=5)))
            await db.commit()

    asyncio.run(_fill_quota())
    log, _energy, _needs = _run_execute(life_db)
    assert spies == []                                      # 配额已满：不写记忆
    assert log.status == "completed" and log.memory_id is None
    # 但真实摘要仍留在活动日志里（原行为：配额满只跳过写记忆，不丢摘要）
    assert json.loads(log.output_json)["summary"] == f"小爱学了「{REAL_TOPIC}」，{TAKEAWAYS[0]}"


# ────────────────────────────── 6：变异守卫（撤哨兵挂点必须真语义变红，源字节不动）

_MUTE_NEEDLE = "        if topic.strip() in _INTERNAL_STUDY_TOPICS:\n            return None\n"
_MUTE_REPL = "        if topic.strip() in _INTERNAL_STUDY_TOPICS:\n            return ''\n"


def _compile_mutant(path: pathlib.Path) -> types.ModuleType:
    """动态 compile 一份「撤掉内部哨兵挂点」的 life_loop 变异体（只改内存，不写磁盘）。"""
    raw = path.read_bytes()
    src = raw.decode("utf-8").replace("\r\n", "\n")
    assert _MUTE_NEEDLE in src, "哨兵挂点形态与变异补丁不一致（改动前请同步本测试）"
    mod = types.ModuleType("app.life._audit13_mutant_life_loop")
    mod.__package__ = "app.life"
    mod.__file__ = str(path)
    exec(compile(src.replace(_MUTE_NEEDLE, _MUTE_REPL, 1),
                 f"{path}#audit13-mutant", "exec"), mod.__dict__)
    return mod


@pytest.mark.parametrize("source_eol", [b"\n", b"\r\n"], ids=["LF", "CRLF"])
def test_变异撤除内部哨兵_真语义变红且磁盘源码字节不变(tmp_path, monkeypatch, source_eol):
    """撤掉哨兵 → 内部主题在 llm=True 档又沉淀（证明断言测的是语义而非同义反复）；源码未动。"""
    import app.life.life_loop as life_loop_mod

    src_path = pathlib.Path(life_loop_mod.__file__).resolve()
    digest_before = hashlib.sha256(src_path.read_bytes()).hexdigest()

    # 用同一源码的两种检出换行运行原语义反例；不改现网源码或断言。
    fixture_path = tmp_path / "life_loop_source.py"
    fixture_bytes = src_path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", source_eol)
    fixture_path.write_bytes(fixture_bytes)
    mutant = _compile_mutant(fixture_path)
    factory, engine = _make_factory(tmp_path / "audit13_mutant.db")
    try:
        asyncio.run(_seed(factory, goals=[(INTERNAL_TOPIC, INTERNAL_TOPIC_PRIORITY)]))
        # 变异体与现网类都打桩（LLM 开着、桩返回非模板句），共用同一份调用台账
        spies = _install_spies(monkeypatch, takeaway=TAKEAWAYS[1], llm_on=True,
                               extra_classes=(mutant.LifeLoopTask,))

        from app.models.character import AICharacter

        async def _study_summary_of(task):
            async with factory() as db:
                char = await db.get(AICharacter, 1)
                return await task._study_summary(db, char)

        # 挂点差异本身：变异体把内部主题降级成 ""（= 没主题），现网是哨兵 None
        assert asyncio.run(_study_summary_of(mutant.LifeLoopTask())) == ""
        assert asyncio.run(_study_summary_of(LifeLoopTask())) is None

        # 红：撤掉哨兵后，内部主题在 llm=True 档真的走到 LLM 并沉淀了假收获
        mlog, _me, _mn = _run_execute(factory, task=mutant.LifeLoopTask())
        assert spies.llm == ["life_loop"]
        assert len(spies) == 1
        assert spies[0]["content"] == LLM_TEXT
        assert mlog.status == "completed" and mlog.memory_id == 555

        # 绿：同一份库、同一场景，现网源码零 LLM、零写入，活动照样完成
        spies.reset()
        plog, _pe, _pn = _run_execute(factory, task=LifeLoopTask())
        assert spies == [] and spies.llm == []
        assert plog.status == "completed" and plog.memory_id is None
    finally:
        asyncio.run(engine.dispose())

    assert hashlib.sha256(src_path.read_bytes()).hexdigest() == digest_before  # 源字节不变
    assert fixture_path.read_bytes() == fixture_bytes  # 变异只在内存，副本字节也不变


def test_内部主题LLM档只验实际副作用为零(life_db, monkeypatch):
    """Exercise the real activity path without asserting the sentinel implementation."""
    asyncio.run(_seed(life_db, goals=[(INTERNAL_TOPIC, INTERNAL_TOPIC_PRIORITY)]))
    spies = _install_spies(monkeypatch, llm_on=True, llm_text="A synthetic observation.")
    log, _energy, _needs = _run_execute(life_db)
    assert spies.llm == []
    assert spies == []
    assert log.status == "completed" and log.memory_id is None
