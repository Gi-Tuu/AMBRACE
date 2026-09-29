# -*- coding: utf-8 -*-
"""架构地图断点 #1 · domain 去 IO 铺开（1/2）回归：emotion/timeline.py ＋ relationship/decay.py。

覆盖四件事：
1. 两个 domain 模块顶层零 IO import（断点 #1 目标口径），端口文件本身也是纯类型。
2. timeline 的三源合并在**假端口**下可完整单测：解析/标签映射/快照前 3 维/dimension 过滤/
   倒序/概览文案/days 钳制/逐源异常降级（判定与文案逐字断言，对齐重构前的原实现）。
3. decay 的衰减判定在**假端口**下可完整单测：每日节流/阈值/drop 取整/下限钳制/NULL 兜底/
   单列越下限/无变化不写库/读写顺序。
4. 生产实现（app/application/emotion_timeline_ports.py、relationship_decay_ports.py）与旧行为
   等价：真实 SQL 对照（同一份种子数据分别跑「旧写法」与「新端口」，逐字段对齐），
   并跑两条调用方接线（application/characters.py、scheduling/arbiter.py）证明注入生效。

项目未装 pytest-asyncio，统一 asyncio.run 同步执行；真 SQL 用例走 tests/_dbclone.py 的
临时文件库（页级克隆模板库）+ monkeypatch 会话工厂，不触碰 backend/data 与生产库。
"""
import ast
import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

import app.domain.emotion.timeline as timeline_mod
import app.domain.relationship.decay as decay_mod
from app.domain.emotion.timeline_ports import (
    EmotionMemoryView,
    StateTriggerLogView,
    StorylineEventView,
    TimelinePortsNotInjected,
)
from app.domain.relationship.ports import CharacterStateView, DecayPortsNotInjected

_IO_PREFIXES = ("app.db", "app.models", "app.agent", "app.application", "app.scheduling",
                "app.memory", "app.services", "sqlalchemy")


def _top_imports(module) -> list[str]:
    """模块级 import 的顶层模块名（函数体内的惰性 import 不算，与 care 样板同口径）。"""
    tree = ast.parse(inspect.getsource(module))
    tops = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            tops += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            tops.append(node.module or "")
    return tops


# ─────────────────────────── 1. 端口注入契约 ───────────────────────────

def test_timeline与decay模块顶层零IO依赖():
    """两个 domain 模块的模块级 import 不得出现 DB/ORM/上层模块（断点 #1 目标口径）。"""
    for mod in (timeline_mod, decay_mod):
        tops = _top_imports(mod)
        bad = [m for m in tops if any(m == p or m.startswith(p + ".") for p in _IO_PREFIXES)]
        assert not bad, f"{mod.__name__} 顶层仍有 IO 依赖：{bad}"


def test端口文件本身也是纯类型():
    """两个端口声明文件只依赖 dataclasses / datetime / typing，零业务模块、零 IO。"""
    import app.domain.emotion.timeline_ports as tl_ports_mod
    import app.domain.relationship.ports as rd_ports_mod
    for mod in (tl_ports_mod, rd_ports_mod):
        tops = _top_imports(mod)
        assert all(not m.startswith(("app.", "sqlalchemy")) for m in tops), (mod.__name__, tops)
        assert set(tops) <= {"__future__", "dataclasses", "datetime", "typing"}, (mod.__name__, tops)


def test_timeline_未注入端口抛清晰错误且不静默降级():
    with pytest.raises(TimelinePortsNotInjected) as ei:
        asyncio.run(timeline_mod.get_emotion_timeline(3))
    assert "emotion_timeline_ports" in str(ei.value)


def test_decay_未注入端口抛清晰错误且不静默降级():
    with pytest.raises(DecayPortsNotInjected) as ei:
        asyncio.run(decay_mod.run_relationship_decay())
    assert "relationship_decay_ports" in str(ei.value)


# ─────────────────── 2. timeline：假端口下的纯函数单测 ───────────────────

class _FakeTimelinePorts:
    def __init__(self, **overrides):
        self.calls: list = []
        self.windows: list = []
        self.mems: list = []
        self.logs: list = []
        self.storylines: list = []
        self.raise_on: set = set()
        for k, v in overrides.items():
            setattr(self, k, v)

    def _hit(self, name, args):
        self.calls.append(name)
        self.windows.append(args)
        if name in self.raise_on:
            raise RuntimeError(f"boom:{name}")

    async def recent_emotion_memories(self, character_id, start):
        self._hit("recent_emotion_memories", (character_id, start))
        return self.mems

    async def recent_state_trigger_logs(self, character_id, start):
        self._hit("recent_state_trigger_logs", (character_id, start))
        return self.logs

    async def recent_storyline_events(self, character_id, start):
        self._hit("recent_storyline_events", (character_id, start))
        return self.storylines


_T0 = datetime(2026, 9, 1, 3, 0)          # 北京 11:00 → 下午
_T_EVENING = datetime(2026, 9, 1, 12, 0)  # 北京 20:00 → 晚上


def test_timeline_三源合并顺序与事件结构逐字一致():
    ports = _FakeTimelinePorts(
        mems=[EmotionMemoryView(id=1, created_at=_T0, content="心情降到30")],
        logs=[StateTriggerLogView(id=2, created_at=_T0 + timedelta(hours=1),
                                  trigger_key="anger_high", value="心情=40；怒气值=80")],
        storylines=[StorylineEventView(id=3, created_at=_T0 - timedelta(hours=1),
                                       storyline_key="cold_war", node_index=1,
                                       output_text="不想说话")],
    )
    out = asyncio.run(timeline_mod.get_emotion_timeline(7, days=7, ports=ports))
    assert out["character_id"] == 7 and out["days"] == 7
    assert [e["id"] for e in out["events"]] == [2, 1, 3]        # created_at 倒序
    assert out["events"][0] == {
        "id": 2, "source": "state_trigger", "source_id": 2,
        "at": (_T0 + timedelta(hours=1)).isoformat(), "label": "状态触发 · 生气",
        "dim_changes": [{"key": "anger", "cn": "怒气值", "from": None, "to": 80, "delta": None},
                        {"key": "mood", "cn": "心情", "from": None, "to": 40, "delta": None}],
        "content": "心情=40；怒气值=80",
    }
    assert out["events"][1]["label"] == "低落"                  # mood=30 <= 35
    assert out["events"][2] == {
        "id": 3, "source": "storyline", "source_id": 3,
        "at": (_T0 - timedelta(hours=1)).isoformat(), "label": "剧情 · 冷战（冷战）",
        "dim_changes": [], "content": "不想说话",
    }
    assert ports.calls == ["recent_emotion_memories", "recent_state_trigger_logs",
                           "recent_storyline_events"]


def test_timeline_days钳制1到90且窗口按days回推():
    for raw, expected in ((None, 7), (0, 7), (200, 90), (-5, 1), (3, 3)):
        ports = _FakeTimelinePorts()
        out = asyncio.run(timeline_mod.get_emotion_timeline(1, days=raw, ports=ports))
        assert out["days"] == expected, raw
        assert out["summary"]["text"].startswith(f"近{expected}天共0次情绪波动")
        cid, start = ports.windows[0]
        assert cid == 1
        elapsed = datetime.now(timezone.utc).replace(tzinfo=None) - start
        assert timedelta(days=expected) <= elapsed < timedelta(days=expected + 1)


def test_timeline_dimension过滤只留含该维度的事件():
    ports = _FakeTimelinePorts(
        mems=[EmotionMemoryView(id=1, created_at=_T0, content="怒气值升到70")],
        logs=[StateTriggerLogView(id=2, created_at=_T_EVENING, trigger_key="mood_low",
                                  value="心情=20")],
        storylines=[StorylineEventView(id=3, created_at=_T0, storyline_key="jealousy",
                                       node_index=0)],
    )
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, dimension="mood", ports=ports))
    assert [e["id"] for e in out["events"]] == [2]
    assert out["summary"]["total"] == 1
    assert out["summary"]["storyline_count"] == 0    # 过滤后才计数（与旧实现同口径）
    assert out["summary"]["emotion_count"] == 0 and out["summary"]["trigger_count"] == 1


def test_timeline_情绪记忆解析最多3维且标签按优先级():
    ports = _FakeTimelinePorts(mems=[EmotionMemoryView(
        id=1, created_at=_T0,
        content="心情降到30、怒气值升到70、疲惫感升到80、敏感度升到90")])
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))
    ev = out["events"][0]
    assert [c["key"] for c in ev["dim_changes"]] == ["mood", "anger", "fatigue"]  # 第 4 维被截
    assert ev["label"] == "低落"                       # mood<=35 优先命中
    assert ev["content"].endswith("敏感度升到90")      # 正文不截到 3 维


def test_timeline_快照取偏离50最大的前3维并按偏离降序():
    ports = _FakeTimelinePorts(logs=[StateTriggerLogView(
        id=1, created_at=_T0, trigger_key="anger_mood_low",
        value="心情=48；怒气值=91；疲惫感=61；舒适感=49；体温=50")])
    ev = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))["events"][0]
    assert [(c["cn"], c["to"]) for c in ev["dim_changes"]] == [("怒气值", 91), ("疲惫感", 61),
                                                               ("心情", 48)]


def test_timeline_触发标签未知key原样且恢复加后缀():
    ports = _FakeTimelinePorts(logs=[
        StateTriggerLogView(id=1, created_at=_T0, trigger_key="unknown_rule", value=""),
        StateTriggerLogView(id=2, created_at=_T0, trigger_key="fatigue_high", value="",
                            recovered=True),
    ])
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))
    labels = {e["id"]: e["label"] for e in out["events"]}
    assert labels[1] == "状态触发 · unknown_rule"
    assert labels[2] == "状态触发 · 疲惫（已恢复）"


def test_timeline_剧情线节点名映射与正文优先级():
    ports = _FakeTimelinePorts(storylines=[
        StorylineEventView(id=1, created_at=_T0, storyline_key="cold_war", node_index=5,
                           output_text="", user_context="", trigger_source="anger_mood_low"),
        StorylineEventView(id=2, created_at=_T0, storyline_key="fatigue", node_index=2,
                           output_text="", user_context="用户说累了", trigger_source="x"),
        StorylineEventView(id=3, created_at=_T0, storyline_key="", node_index=9,
                           user_context=""),
    ])
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))
    by_id = {e["id"]: e for e in out["events"]}
    assert by_id[1]["label"] == "剧情 · 冷战（和好后遗症）"
    assert by_id[1]["content"] == "anger_mood_low"        # output/user_context 空 → trigger_source
    assert by_id[2]["label"] == "剧情 · 疲惫（高潮）"
    assert by_id[2]["content"] == "用户说累了"
    assert by_id[3]["label"] == "剧情 · storyline（节点9）"  # key 空 → 通用名 + 越界节点号


def test_timeline_概览统计时段与最明显维度逐字对齐旧实现():
    ports = _FakeTimelinePorts(
        mems=[EmotionMemoryView(id=1, created_at=_T0, content="心情降到30"),
              EmotionMemoryView(id=2, created_at=_T0 + timedelta(hours=2), content="怒气值升到95"),
              EmotionMemoryView(id=3, created_at=_T_EVENING, content="疲惫感升到60")],
    )
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, days=7, ports=ports))
    s = out["summary"]
    assert (s["total"], s["emotion_count"], s["trigger_count"], s["storyline_count"]) == (3, 3, 0, 0)
    assert s["top_period"] == "下午"                   # 2 条下午 > 1 条晚上
    assert s["top_dimension"] == "怒气值"              # |95-50|=45 最大
    assert s["text"] == "近7天共3次情绪波动，（对话评估3次、状态触发0次、剧情0次），多发生在下午，怒气值波动最明显。"


def test_timeline_空记录时概览为0次且时段维度为空():
    ports = _FakeTimelinePorts()
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, days=7, ports=ports))
    assert out["events"] == []
    assert out["summary"] == {
        "total": 0, "emotion_count": 0, "trigger_count": 0, "storyline_count": 0,
        "top_period": "", "top_dimension": None, "text": "近7天共0次情绪波动。",
    }


def test_timeline_单源异常只降级该源三条warning口径不变(caplog):
    ports = _FakeTimelinePorts(
        mems=[EmotionMemoryView(id=1, created_at=_T0, content="心情降到30")],
        logs=[StateTriggerLogView(id=2, created_at=_T0, trigger_key="mood_low", value="心情=20")],
        storylines=[StorylineEventView(id=3, created_at=_T0, storyline_key="jealousy")],
        raise_on={"recent_state_trigger_logs"},
    )
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))
    assert [e["id"] for e in out["events"]] == [1, 3]
    assert out["summary"]["trigger_count"] == 0
    assert "Emotion timeline trigger query failed" in caplog.text
    assert ports.calls == ["recent_emotion_memories", "recent_state_trigger_logs",
                           "recent_storyline_events"]      # 异常不中断后续源


def test_timeline_三源全异常仍返回完整结构(caplog):
    ports = _FakeTimelinePorts(mems=[EmotionMemoryView(id=1, created_at=_T0)],
                              raise_on={"recent_emotion_memories", "recent_state_trigger_logs",
                                        "recent_storyline_events"})
    out = asyncio.run(timeline_mod.get_emotion_timeline(1, ports=ports))
    assert out["events"] == [] and out["summary"]["total"] == 0
    for msg in ("Emotion timeline memory query failed", "Emotion timeline trigger query failed",
                "Emotion timeline storyline query failed"):
        assert msg in caplog.text


# ─────────────────── 3. decay：假端口下的纯判定单测 ───────────────────

class _FakeDecayPorts:
    def __init__(self, states=None, **overrides):
        self.calls: list = []
        self.states = states or []
        self.updates: list = []
        self.raise_on: set = set()
        for k, v in overrides.items():
            setattr(self, k, v)

    def _hit(self, name):
        self.calls.append(name)
        if name in self.raise_on:
            raise RuntimeError(f"boom:{name}")

    async def fetch_character_states(self):
        self._hit("fetch_character_states")
        return self.states

    async def apply_decay(self, updates):
        self._hit("apply_decay")
        self.updates.append(list(updates))


@pytest.fixture(autouse=True)
def _reset_decay_throttle():
    """decay 的进程内每日节流是模块全局：用例前后归零，避免互相吃掉执行机会。"""
    orig = decay_mod._last_run_date
    decay_mod._last_run_date = None
    yield
    decay_mod._last_run_date = orig


def _ago(**kw):
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**kw)


def test_decay_闲置未过阈值不写库():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=None, trust=50, attachment=50),
        CharacterStateView(id=2, last_activity_at=_ago(hours=30), trust=50, attachment=50),
        CharacterStateView(id=3, last_activity_at=_ago(days=3), trust=50, attachment=50),
    ])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert ports.updates[0][0].state_id == 3       # 1/2 被跳过
    assert len(ports.updates[0]) == 1
    assert ports.updates[0][0].trust == 49         # drop=int((3-1)*0.5)=1


def test_decay_步长与下限钳制及NULL兜底():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=_ago(days=11), trust=50, attachment=30),
        CharacterStateView(id=2, last_activity_at=_ago(days=100), trust=22, attachment=21),
        CharacterStateView(id=3, last_activity_at=_ago(days=11), trust=None, attachment=None),
    ])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    u = {x.state_id: x for x in ports.updates[0]}
    assert (u[1].trust, u[1].attachment) == (45, 25)     # drop=5
    assert (u[2].trust, u[2].attachment) == (20, 20)     # drop=49 → 钳到 RELATION_MIN
    assert (u[3].trust, u[3].attachment) == (45, 45)     # NULL 按 50 兜底


def test_decay_单列已到下限只写另一列():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=_ago(days=11), trust=20, attachment=50)])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert len(ports.updates[0]) == 1
    assert ports.updates[0][0].state_id == 1
    assert ports.updates[0][0].trust is None             # 20 不 > 20 → 该列不写
    assert ports.updates[0][0].attachment == 45


def test_decay_全部无需衰减时不调用写库():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=None, trust=50, attachment=50),
        CharacterStateView(id=2, last_activity_at=_ago(hours=6), trust=15, attachment=10),
    ])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert ports.calls == ["fetch_character_states"]     # changed=0 → 不 apply
    assert ports.updates == []


def test_decay_读写各一次且按原顺序全表提交():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=5, last_activity_at=_ago(days=11), trust=50, attachment=50),
        CharacterStateView(id=3, last_activity_at=_ago(days=11), trust=50, attachment=None),
    ])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert ports.calls == ["fetch_character_states", "apply_decay"]   # 一次读 + 一次批量写
    assert [x.state_id for x in ports.updates[0]] == [5, 3]           # 保持 fetch 返回顺序


def test_decay_同一天第二次直接返回不再查库():
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=_ago(days=11), trust=50, attachment=50)])
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert ports.calls == ["fetch_character_states", "apply_decay"]
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert ports.calls == ["fetch_character_states", "apply_decay"]   # 节流生效
    assert decay_mod._last_run_date == datetime.now(timezone.utc).date().isoformat()


def test_decay_读库异常按旧行为静默降级只留warning(caplog):
    ports = _FakeDecayPorts(raise_on={"fetch_character_states"})
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert "Relationship decay failed" in caplog.text
    assert ports.calls == ["fetch_character_states"]


def test_decay_写库异常不再打applied日志(caplog):
    ports = _FakeDecayPorts(states=[
        CharacterStateView(id=1, last_activity_at=_ago(days=11), trust=50, attachment=50)],
        raise_on={"apply_decay"})
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    assert "Relationship decay applied" not in caplog.text
    assert "Relationship decay failed" in caplog.text


def test_decay_阈值常量与旧实现一致():
    assert (decay_mod.IDLE_DAYS_THRESHOLD, decay_mod.DAILY_DECAY_STEP, decay_mod.RELATION_MIN) \
        == (1, 0.5, 20)


# ─────────── 4. 生产实现与旧行为等价（真实 SQL + 调用方接线）───────────

@pytest.fixture()
def v2a_db(monkeypatch, tmp_path):
    """临时文件库 + 两个生产端口模块与 database 的会话工厂接到该库（不触碰生产库）。"""
    from _dbclone import clone_engine, make_session_factory

    import app.application.emotion_timeline_ports as etp
    import app.application.relationship_decay_ports as rdp
    import app.db.database as db_mod

    engine = clone_engine(tmp_path / "v2a.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(etp, "async_session_factory", factory)
    monkeypatch.setattr(rdp, "async_session_factory", factory)
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())


def _seed_tl_rows(factory):
    """三源各撒若干行（含应被过滤掉的：别的角色、非 emotion 子类型、已归档、更旧）。"""
    from app.models.character import AICharacter, StateTriggerLog, StorylineEvent
    from app.models.memory import Memory
    from app.models.user import User

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _go():
        async with factory() as db:
            db.add(User(id=41, username="u_v2a", nickname="去IO批次"))
            db.add(AICharacter(id=41, user_id=41, name="小阳", personality="活泼"))
            db.add(AICharacter(id=42, user_id=41, name="小冰", personality="安静"))
            await db.commit()
        async with factory() as db:
            db.add_all([
                Memory(user_id=41, character_id=41, memory_type="event", sub_type="emotion",
                       content="心情降到30、怒气值升到62", created_at=now - timedelta(days=1)),
                Memory(user_id=41, character_id=41, memory_type="event", sub_type="emotion",
                       content="疲惫感升到70", created_at=now - timedelta(hours=5)),
                Memory(user_id=41, character_id=41, memory_type="event", sub_type="insight",
                       content="心情降到10", created_at=now),
                Memory(user_id=41, character_id=42, memory_type="event", sub_type="emotion",
                       content="心情降到11", created_at=now),
                Memory(user_id=41, character_id=41, memory_type="event", sub_type="emotion",
                       content="心情降到12", is_archived=True, created_at=now),
                Memory(user_id=41, character_id=41, memory_type="event", sub_type="emotion",
                       content="心情降到13", created_at=now - timedelta(days=40)),
            ])
            db.add_all([
                StateTriggerLog(character_id=41, trigger_key="anger_high",
                                value="心情=45；怒气值=80", created_at=now - timedelta(days=2)),
                StateTriggerLog(character_id=41, trigger_key="mood_low", value="心情=25",
                                recovered=True, created_at=now - timedelta(hours=2)),
                StateTriggerLog(character_id=42, trigger_key="fatigue_high", value="疲惫=88",
                                created_at=now),
            ])
            db.add_all([
                StorylineEvent(character_id=41, storyline_key="cold_war", node_index=1,
                               output_text="不想说话", created_at=now - timedelta(days=3)),
                StorylineEvent(character_id=41, storyline_key="jealousy", node_index=9,
                               user_context="夸了别人", trigger_source="t", created_at=now),
                StorylineEvent(character_id=42, storyline_key="fatigue", node_index=0,
                               created_at=now),
            ])
            await db.commit()

    asyncio.run(_go())
    return now


@pytest.mark.slow
def test_生产实现_时间线三源SQL与旧写法逐字等价(v2a_db):
    """同一份数据分别跑「旧 SQL」与「新端口」，逐字段对齐（含过滤条件与排序）。"""
    from sqlalchemy import select as sa_select

    from app.application.emotion_timeline_ports import production_emotion_timeline_ports as ports
    from app.models.character import StateTriggerLog, StorylineEvent
    from app.models.memory import Memory

    now = _seed_tl_rows(v2a_db)
    start = now - timedelta(days=7)

    async def _legacy():
        """重构前 timeline.py 里的三段查询原样（对照基准）。"""
        from app.memory.service import _active_status_clause
        async with v2a_db() as db:
            mems = (await db.execute(
                sa_select(Memory).where(
                    Memory.character_id == 41,
                    Memory.sub_type == "emotion",
                    Memory.is_archived == False,
                    Memory.created_at >= start,
                    _active_status_clause(),
                ).order_by(Memory.created_at.desc())
            )).scalars().all()
            logs = (await db.execute(
                sa_select(StateTriggerLog).where(
                    StateTriggerLog.character_id == 41,
                    StateTriggerLog.created_at >= start,
                ).order_by(StateTriggerLog.created_at.desc())
            )).scalars().all()
            sts = (await db.execute(
                sa_select(StorylineEvent).where(
                    StorylineEvent.character_id == 41,
                    StorylineEvent.created_at >= start,
                ).order_by(StorylineEvent.created_at.desc())
            )).scalars().all()
        return ([(m.id, m.created_at, m.content or "") for m in mems],
                [(lg.id, lg.created_at, lg.trigger_key, lg.value or "", bool(lg.recovered))
                 for lg in logs],
                [(se.id, se.created_at, se.storyline_key or "", se.node_index or 0,
                  se.output_text or "", se.user_context or "", se.trigger_source or "")
                 for se in sts])

    async def _new():
        mems = await ports.recent_emotion_memories(41, start)
        logs = await ports.recent_state_trigger_logs(41, start)
        sts = await ports.recent_storyline_events(41, start)
        return ([(m.id, m.created_at, m.content) for m in mems],
                [(lg.id, lg.created_at, lg.trigger_key, lg.value, lg.recovered) for lg in logs],
                [(se.id, se.created_at, se.storyline_key, se.node_index, se.output_text,
                  se.user_context, se.trigger_source) for se in sts])

    legacy, new = asyncio.run(_legacy()), asyncio.run(_new())
    assert new == legacy
    assert len(legacy[0]) == 2 and [r[2] for r in legacy[0]] == ["疲惫感升到70",
                                                                  "心情降到30、怒气值升到62"]
    assert len(legacy[1]) == 2 and len(legacy[2]) == 2


@pytest.mark.slow
def test_生产实现_全链路时间线与接线后结果一致(v2a_db):
    """真端口跑 domain：过滤/排序/概览与手工按旧口径核算一致（不依赖假端口）。"""
    from app.application.emotion_timeline_ports import production_emotion_timeline_ports as ports

    _seed_tl_rows(v2a_db)
    out = asyncio.run(timeline_mod.get_emotion_timeline(41, days=7, ports=ports))
    assert [e["source"] for e in out["events"]] == ["storyline", "state_trigger", "emotion",
                                                    "emotion", "state_trigger", "storyline"]
    assert out["summary"]["total"] == 6
    assert (out["summary"]["emotion_count"], out["summary"]["trigger_count"],
            out["summary"]["storyline_count"]) == (2, 2, 2)
    assert out["events"][0]["label"] == "剧情 · 吃醋（节点9）"
    assert out["events"][1]["label"] == "状态触发 · 低落（已恢复）"
    assert out["summary"]["top_dimension"] == "怒气值"   # |80-50|=30 为最大偏离
    assert out["summary"]["text"].startswith("近7天共6次情绪波动，（对话评估2次、状态触发2次、剧情2次）")
    assert out["summary"]["text"].endswith("波动最明显。")
    # days=90 窗口把那条 40 天前的记忆也纳进来（证明 start 真的传到了 SQL）
    out90 = asyncio.run(timeline_mod.get_emotion_timeline(41, days=90, ports=ports))
    assert (out90["summary"]["emotion_count"], out90["summary"]["total"]) == (3, 7)


@pytest.mark.slow
def test_接线_application层characters取数走生产端口(v2a_db):
    """调用方漏注入会当场抛错；此用例用真 db + 真端口跑通 application/characters.py 的接线行。"""
    from app.application.characters import get_emotion_timeline as svc_get

    _seed_tl_rows(v2a_db)

    async def _go():
        async with v2a_db() as db:
            return await svc_get(db, 41, 41, "zh", days=7, dimension=None)

    out = asyncio.run(_go())
    assert out["character_id"] == 41 and out["days"] == 7
    assert out["summary"]["total"] == 6
    with pytest.raises(TimelinePortsNotInjected):    # domain 侧没有静默兜底（不 fail-open）
        asyncio.run(timeline_mod.get_emotion_timeline(41, days=7))


@pytest.mark.slow
def test_生产实现_衰减读写与旧实现逐字段等价(v2a_db, tmp_path, monkeypatch):
    """同一份种子分别跑「旧写法（同会话改 ORM 再 commit）」与「新端口」，最终列值一致。"""
    from _dbclone import clone_engine, make_session_factory
    from sqlalchemy import select as sa_select

    import app.application.relationship_decay_ports as rdp
    from app.application.relationship_decay_ports import production_relationship_decay_ports as ports
    from app.models.character import AICharacter, CharacterState
    from app.models.user import User

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeds = [
        # (character_id, trust, attachment, last_activity_at)
        (51, 50, 30, now - timedelta(days=11)),      # drop=5 → 45 / 25
        (52, 22, 20, now - timedelta(days=100)),     # 49 步长 → 钳到 20；20 不 > 20 不动
        (53, None, 50, now - timedelta(days=11)),    # NULL 按 50 兜底 → 45 / 45
        (54, 50, 50, None),                          # 无互动时间 → 不动
        (55, 50, 50, now - timedelta(hours=30)),     # drop=0 → 不动
        (56, 15, 60, now - timedelta(days=11)),      # trust 不动 / attachment 55
    ]

    async def _seed(factory):
        async with factory() as db:
            db.add(User(id=61, username="u_v2a_decay", nickname="衰减"))
            for cid, *_ in seeds:
                db.add(AICharacter(id=cid, user_id=61, name=f"c{cid}", personality="p"))
            await db.commit()
        async with factory() as db:
            for cid, trust, att, last in seeds:
                db.add(CharacterState(character_id=cid, trust=trust, attachment=att,
                                      last_activity_at=last, mood=77))
            await db.commit()

    async def _legacy(factory):
        """重构前 decay.py 的原实现（对照基准：原 SQL、原会话形态、原赋值顺序）。"""
        async with factory() as db:
            states = (await db.execute(sa_select(CharacterState))).scalars().all()
            now2 = datetime.now(timezone.utc).replace(tzinfo=None)
            changed = 0
            for st in states:
                last = st.last_activity_at
                if last is None:
                    continue
                last = last.replace(tzinfo=None) if last.tzinfo else last
                idle_days = (now2 - last).total_seconds() / 86400.0
                if idle_days <= decay_mod.IDLE_DAYS_THRESHOLD:
                    continue
                drop = int((idle_days - decay_mod.IDLE_DAYS_THRESHOLD) * decay_mod.DAILY_DECAY_STEP)
                if drop <= 0:
                    continue
                if (st.trust or 50) > decay_mod.RELATION_MIN:
                    st.trust = max(decay_mod.RELATION_MIN, int((st.trust or 50) - drop))
                    changed += 1
                if (st.attachment or 50) > decay_mod.RELATION_MIN:
                    st.attachment = max(decay_mod.RELATION_MIN, int((st.attachment or 50) - drop))
                    changed += 1
            if changed:
                await db.commit()
        return changed

    async def _dump(factory):
        async with factory() as db:
            rows = (await db.execute(sa_select(CharacterState).order_by(
                CharacterState.character_id))).scalars().all()
            return [(r.character_id, r.trust, r.attachment, r.mood) for r in rows]

    engines, factories = [], []
    for _ in range(2):
        eng = clone_engine(tmp_path / f"decay_{len(factories)}.db")
        engines.append(eng)
        factories.append(make_session_factory(eng))
    legacy_factory, new_factory = factories

    asyncio.run(_seed(legacy_factory))
    legacy_changed = asyncio.run(_legacy(legacy_factory))
    legacy_out = asyncio.run(_dump(legacy_factory))

    asyncio.run(_seed(new_factory))
    monkeypatch.setattr(rdp, "async_session_factory", new_factory)
    asyncio.run(decay_mod.run_relationship_decay(ports=ports))
    new_out = asyncio.run(_dump(new_factory))

    assert new_out == legacy_out
    assert legacy_changed == 6          # 变化列数：51 两列 + 52 一列 + 53 两列 + 56 一列
    assert [r[1:] for r in new_out] == [(45, 25, 77), (20, 20, 77), (45, 45, 77),
                                        (50, 50, 77), (50, 50, 77), (15, 55, 77)]
    assert [r[0] for r in new_out] == [51, 52, 53, 54, 55, 56]
    for eng in engines:
        asyncio.run(eng.dispose())


@pytest.mark.slow
def test_接线_arbiter每日衰减注入生产端口(monkeypatch):
    """run_tick 的接线行必须把生产端口传进来（漏传即 DecayPortsNotInjected，被 run_tick 静默吞掉
    → 衰减再也不生效），这里用捕获桩断言实参就是生产实现。"""
    import app.application.relationship_decay_ports as rdp
    import app.scheduling.arbiter as arbiter

    seen = {}

    async def _capture(ports=None):
        seen["ports"] = ports
        return None

    monkeypatch.setattr(decay_mod, "run_relationship_decay", _capture)
    monkeypatch.setattr(arbiter, "all_sources", lambda: [])
    asyncio.run(arbiter.run_tick())
    assert seen["ports"] is rdp.production_relationship_decay_ports
