# -*- coding: utf-8 -*-
"""World State 只读统一入口测试（P0 语义统一 · 第 2 步，2026-09-29；**零行为**）。

钉住七条底线（派单 §要求 逐条对齐）：
1. **形状与标签**：八路（六存储 + 两派生视图）齐全，逐路带来源模块/表、as_of 口径、条目数、耗时 ms；
2. **等价性**：每路的值与「直接调用对应原函数 / 直接查同一张表」逐例相等（入口不复制过滤逻辑，
   所以注入侧与快照侧不可能漂移）；
3. **as_of 口径**：``None`` ⇒ ``now_naive_utc()``；显式传入 ⇒ 逐路透传并**真的驱动新鲜判定**；
   带 tzinfo 的入参按「同一时刻」归一为 naive UTC（不做本地时区裁剪）；
4. **异常隔离**：单路抛错只把该路标 ``degraded``（WARNING 日志），其余路照常，整体不抛；
5. **纯只读**：把会话工厂换成「一写就报错」的壳，八路仍全部读通（证明入口不写库）；AST 侧再锁一道；
6. **死读路径埋点**：subject=user 命中行数计数在 current_state 路径上**每轮只触发一次**，
   且埋点炸掉不影响 ``_char_world_user_facts`` 的返回值；
7. **零行为**：注入分区一律不得引用本入口（步骤 5 才接线）。

纪律：临时库走 ``tests/_dbclone``（禁止连生产库）；``memory_trace_debug`` 拨关 ⇒ obs_event 零 IO，
需要观测埋点的用例单独把 obs_event 换成计数器（不依赖真写 trace）。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from _dbclone import clone_engine, make_session_factory

from app import actors
from app.events import facts as facts_mod
from app.events import world_state as ws
from app.flags.agent_flags import AGENT_FLAGS
from app.models.character import AICharacter, CharacterState
from app.models.life import LifeState
from app.models.memory import Memory, WorldFact
from app.models.user import GlobalUserFact, User
from app.utils.timeutil import now_naive_utc

pytestmark = pytest.mark.slow

UID = 3
CID = 13
OTHER_UID = 4      # 跨用户/跨角色串味检测
OTHER_CID = 14
METRIC = "user_subject_world_fact_rows"
_STATUS_MEM_VALUE = "状态更新: 在加班"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    """flag 复位：user_facts 总闸开（否则 get_active_user_facts 恒空、测不出东西）；trace 关（零 IO）。"""
    for key, val in (("global_user_facts", True), ("user_fact_location", True),
                     ("memory_trace_debug", False)):
        monkeypatch.setitem(AGENT_FLAGS, key, val)
    yield


@pytest.fixture()
def snap_db(monkeypatch, tmp_path):
    """临时库 + 把入口与各原函数的会话工厂全指向它（各模块均模块级 import，逐个打桩）。"""
    engine = clone_engine(os.path.join(str(tmp_path), "ws.db"))
    factory = make_session_factory(engine)

    import app.db.database as db_mod
    import app.memory.user_facts as uf_mod
    for mod in (db_mod, facts_mod, uf_mod):
        monkeypatch.setattr(mod, "async_session_factory", factory)

    async def _seed():
        now = now_naive_utc()
        async with factory() as db:
            db.add(User(id=UID, username=f"u{UID}", nickname="用户3"))
            db.add(User(id=OTHER_UID, username=f"u{OTHER_UID}", nickname="用户4"))
            await db.commit()
            db.add(AICharacter(id=CID, user_id=UID, name="角色13",
                               current_status="在加班，心情一般"))
            db.add(AICharacter(id=OTHER_CID, user_id=OTHER_UID, name="角色14",
                               current_status="别人的角色"))
            await db.commit()
            db.add(CharacterState(character_id=CID, mood=71, body_temp=50, desire=30,
                                  possessiveness=40, fatigue=66, sensitivity=55,
                                  comfort=48, anger=12, trust=77))
            db.add(LifeState(character_id=CID, needs_json='{"curiosity": 62, "rest": 21}',
                             last_tick_at=now - timedelta(hours=2), location="home"))
            await db.commit()
            # ① 新鲜瞬时状态（character 口述）+ ② subject=user 的现状（死读路径本源）
            db.add(WorldFact(user_id=UID, character_id=CID, subject_type="character",
                             subject_id=CID, predicate="status", object_value="在加班",
                             status="active", audience='["public"]', epistemic_status="FACT",
                             author="system", kind="status", asserted_at=now - timedelta(hours=1)))
            db.add(WorldFact(user_id=UID, character_id=CID, subject_type="user",
                             subject_id=UID, predicate="location", object_value="在杭州",
                             status="active", audience='["public"]', epistemic_status="FACT",
                             author="user", kind="status", asserted_at=now - timedelta(hours=1)))
            # ③ 别人家的事实（串味检测）+ ④ 同谓词旧值（get_active_facts 会折叠掉）
            db.add(WorldFact(user_id=OTHER_UID, character_id=OTHER_CID, subject_type="character",
                             subject_id=OTHER_CID, predicate="status", object_value="别人的状态",
                             status="active", audience='["public"]', epistemic_status="FACT",
                             author="system", kind="status", asserted_at=now))
            db.add(WorldFact(user_id=UID, character_id=CID, subject_type="character",
                             subject_id=CID, predicate="status", object_value="三小时前的旧状态",
                             status="active", audience='["public"]', epistemic_status="FACT",
                             author="system", kind="status", asserted_at=now - timedelta(hours=3)))
            # ⑤ working_state 快照（Memory 表）
            db.add(Memory(user_id=UID, character_id=CID, memory_type="working_state",
                          content='{"hot": ["项目上线"], "warm": [], "cold": []}',
                          source="system", speaker_type="system",
                          created_at=now - timedelta(minutes=5)))
            # ⑥ 派生 B：状态派生记忆条（新鲜 + 超 12h）+ 一条非状态条（不得混入）
            db.add(Memory(user_id=UID, character_id=CID, memory_type="insight",
                          content=_STATUS_MEM_VALUE, sub_type="status", source="status",
                          speaker_type="character", speaker_id=CID, epistemic_status="FACT",
                          created_at=now - timedelta(hours=1)))
            db.add(Memory(user_id=UID, character_id=CID, memory_type="insight",
                          content="状态更新: 很久以前在加班", sub_type="status", source="status",
                          speaker_type="character", speaker_id=CID, epistemic_status="FACT",
                          created_at=now - timedelta(hours=30)))
            db.add(Memory(user_id=UID, character_id=CID, memory_type="insight",
                          content="普通洞察，不是状态条", sub_type="chat", source="chat",
                          speaker_type="character", speaker_id=CID, epistemic_status="INFERRED",
                          created_at=now))
            # ⑦ 用户硬档案（GlobalUserFact，跨角色共享）
            db.add(GlobalUserFact(user_id=UID, slot="location", value="常驻杭州",
                                  source="gps", epistemic_status="FACT", confidence=0.9,
                                  valid_from=now - timedelta(days=1), valid_to=None))
            await db.commit()
    _run(_seed())
    yield factory
    _run(engine.dispose())


def _items(snap, route):
    return snap["routes"][route]["items"]


def _values(snap, route):
    return [i["value"] for i in _items(snap, route)]


def _rows(snap, route):
    """world_facts 路里剔除「注入文本」那一条，只留谓词级明细（等价性对照用）。"""
    return [i for i in _items(snap, route) if i["kind"] != "_view_text"]


async def _select_all(factory, stmt):
    async with factory() as db:
        return list((await db.execute(stmt)).scalars().all())


# ─────────────── ① 形状与逐路标签 ───────────────

def test_八路齐全且顺序为六存储两派生(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    assert list(snap["routes"].keys()) == list(ws.ALL_ROUTES)
    assert ws.STORE_ROUTES == (
        "world_facts", "character_current_status", "character_states",
        "life_states", "working_state", "user_facts"), "六存储须与方案 §1.4 表序一致"
    assert ws.DERIVED_ROUTES == ("current_state_anchor", "status_memories"), "两派生视图"
    assert snap["summary"]["route_count"] == 8


def test_逐路标签字段齐全且来源路径真实(snap_db):
    import pathlib
    root = pathlib.Path(inspect.getfile(ws)).parents[2]   # .../backend/app/events/x.py → backend/
    snap = _run(ws.world_state_snapshot(UID, CID))
    for name, meta in snap["routes"].items():
        for key in ("route", "kind", "source_module", "source_at", "source_table",
                    "authoritative_source", "as_of_basis", "status", "error",
                    "elapsed_ms", "count", "items", "predicates"):
            assert key in meta, f"{name} 缺标签 {key}"
        assert meta["route"] == name
        assert meta["kind"] == ("store" if name in ws.STORE_ROUTES else "derived_view")
        assert meta["count"] == len(meta["items"])
        assert isinstance(meta["elapsed_ms"], float) and meta["elapsed_ms"] >= 0.0
        assert meta["status"] in ("ok", "empty", "degraded")
        assert meta["as_of_basis"], f"{name} 未登记 as_of 口径"
        assert meta["authoritative_source"], f"{name} 未登记权威边界"
        # file:line 不许是凭记忆写的假值：路径必须真存在于仓库
        for part in meta["source_at"].split():
            if part.startswith("backend/"):
                assert (root / part.split(":")[0].removeprefix("backend/")).exists(), part
    assert snap["tz_basis"].startswith("naive UTC")


def test_每条目标注六元组且取值走actors(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    tagged = 0
    for name, meta in snap["routes"].items():
        for it in meta["items"]:
            for key in ("source_store", "subject_type", "actor", "epistemic_status",
                        "asserted_at", "fresh_until"):
                assert key in it, f"{name} 的条目缺 {key}"
            assert it["source_store"] == name
            # actor / 认知态必须落在 actors.py（第 1 步的单一来源）登记的规范值域内，不新造字面量
            assert it["actor"] in (*actors.ACTORS, actors.ACTOR_UNSET), it["actor"]
            assert it["epistemic_status"] in actors.EPISTEMIC_VALUES, it["epistemic_status"]
            tagged += 1
    assert tagged >= 8, "种子数据下八路不该全空"


def test_返回值形状稳定且可JSON序列化(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    again = _run(ws.world_state_snapshot(UID, CID, as_of=datetime.fromisoformat(snap["as_of"])))
    assert set(again["routes"]) == set(snap["routes"])
    assert json.loads(json.dumps(snap, ensure_ascii=False, default=str))["version"] == ws.SNAPSHOT_VERSION


def test_summary条目数与耗时自洽(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    s = snap["summary"]
    assert s["item_count"] == sum(m["count"] for m in snap["routes"].values())
    assert s["ok"] + s["empty"] + s["degraded"] == 8
    assert s["total_ms"] >= max(m["elapsed_ms"] for m in snap["routes"].values())


# ─────────────── ② 等价性：每路 == 直接调用原读法 ───────────────

def test_等价_world_facts与get_active_facts逐字段相等(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(facts_mod.get_active_facts(
        character_id=CID, user_id=UID, viewer_type="character", viewer_id=CID,
        limit=facts_mod.MAX_FACTS_PER_CHAR))
    got = _rows(snap, "world_facts")
    assert [i["value"] for i in got] == [f.object_value for f in direct]
    assert [i["predicate"] for i in got] == [f.predicate for f in direct]
    assert [i["subject_type"] for i in got] == [f.subject_type for f in direct]
    assert [i["epistemic_status"] for i in got] == [f.epistemic_status for f in direct]
    assert [i["asserted_at"] for i in got] == [
        facts_mod._naive_utc(f.asserted_at).isoformat(sep=" ", timespec="seconds") for f in direct]
    assert [i["line"] for i in got] == [facts_mod.fact_text(f) for f in direct]
    # 注入文本与 order=45 分区实际喂给模型的那串字逐字节相同
    view = _run(facts_mod.get_character_view(CID, UID))
    assert [i["value"] for i in _items(snap, "world_facts") if i["kind"] == "_view_text"] == [view]
    # 旧值被同谓词折叠、别人家的事实不串味
    assert "三小时前的旧状态" not in [i["value"] for i in got]
    assert "别人的状态" not in [i["value"] for i in got]


def test_等价_character_current_status与直接查列相等(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    async def _direct():
        async with snap_db() as db:
            row = await db.get(AICharacter, CID)
            return row.current_status
    assert _values(snap, "character_current_status") == [_run(_direct())]
    assert _items(snap, "character_current_status")[0]["fresh_until"] is None, "该列无 TTL"


def test_等价_character_states与直接查同表逐字段相等(snap_db):
    from sqlalchemy import select
    from app.application.character_state_service import DIMENSIONS
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(_select_all(snap_db, select(CharacterState).where(
        CharacterState.character_id == CID)))
    assert len(direct) == 1
    dims = [k for k, _l, _d in DIMENSIONS]
    assert len(dims) == 8, "八维"
    assert _values(snap, "character_states") == [
        {**{k: getattr(direct[0], k) for k in dims}, "trust": direct[0].trust}]
    assert _items(snap, "character_states")[0]["labels"]["mood"] == "心情"


def test_等价_life_states与直接查同表相等(snap_db):
    from sqlalchemy import select
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(_select_all(snap_db, select(LifeState).where(LifeState.character_id == CID)))
    assert _values(snap, "life_states") == [json.loads(direct[0].needs_json)]
    assert _items(snap, "life_states")[0]["extra"] == {
        "location": direct[0].location, "current_room": direct[0].current_room,
        "phase": direct[0].phase}


def test_等价_working_state与get_latest直接调用相等(snap_db):
    from app.application.working_state_service import get_latest
    snap = _run(ws.world_state_snapshot(UID, CID))
    async def _direct():
        async with snap_db() as db:
            return await get_latest(db, UID, CID)
    row = _run(_direct())
    assert _values(snap, "working_state") == [json.loads(row.content)]
    assert _items(snap, "working_state")[0]["memory_id"] == row.id


def test_等价_user_facts与get_active_user_facts逐字段相等(snap_db):
    from app.memory import user_facts as uf
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(uf.get_active_user_facts(UID))          # 与路 6 同参数（slots=None＝启用槽）
    got = _items(snap, "user_facts")
    assert [(i["predicate"], i["value"]) for i in got] == [(r.slot, r.value) for r in direct]
    assert [i["confidence"] for i in got] == [r.confidence for r in direct]
    assert [i["epistemic_status"] for i in got] == [r.epistemic_status for r in direct]
    assert [i["subject_type"] for i in got] == [actors.ACTOR_USER] * len(direct)
    assert got, "种子下不该为空（空 ⇒ 等价性断言是假绿）"


def test_等价_current_state_anchor与锚点函数逐字相等(snap_db):
    from app.memory.current_state import current_user_state_anchor
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(current_user_state_anchor(character_id=CID, user_id=UID))
    assert _values(snap, "current_state_anchor") == [direct]
    assert direct, "种子下锚点应有内容（空 ⇒ 等价性断言是假绿）"


def test_等价_status_memories与直接查同表相等(snap_db):
    from sqlalchemy import select
    snap = _run(ws.world_state_snapshot(UID, CID))
    direct = _run(_select_all(snap_db, select(Memory).where(
        Memory.user_id == UID, Memory.character_id == CID,
        Memory.sub_type == facts_mod.STATUS_MEMORY_SUB_TYPE,
        Memory.source == facts_mod.STATUS_MEMORY_SOURCE).order_by(Memory.id.desc())))
    assert _values(snap, "status_memories") == [r.content for r in direct]
    assert [i["actor"] for i in _items(snap, "status_memories")] == [
        actors.normalize_sender(r.speaker_type) or actors.ACTOR_UNSET for r in direct]


def test_非状态记忆条不得混入派生B(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    assert all("普通洞察" not in v for v in _values(snap, "status_memories"))


def test_cross_store暴露同一谓词的跨路说法(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    cs = snap["cross_store"]
    assert "status" in cs, "status 同时存在于 world_facts / current_status / status_memories"
    assert len(cs["status"]) >= 3, f"应跨 ≥3 套存储，实得 {sorted(cs['status'])}"
    assert "location" in cs and len(cs["location"]) >= 2


# ─────────────── ③ as_of 口径 ───────────────

def test_as_of缺省取now_naive_utc(snap_db):
    snap = _run(ws.world_state_snapshot(UID, CID))
    assert snap["as_of_source"] == "now_naive_utc()"
    assert abs((now_naive_utc() - datetime.fromisoformat(snap["as_of"])).total_seconds()) < 60


def test_as_of显式传入则逐路透传且八路齐命同一瞬间(snap_db):
    fixed = datetime(2026, 5, 8, 6, 30, 0)
    snap = _run(ws.world_state_snapshot(UID, CID, as_of=fixed))
    assert snap["as_of_source"] == "caller"
    assert snap["as_of"] == "2026-05-08 06:30:00"
    assert snap["summary"]["degraded"] == 0, snap["summary"]["degraded_routes"]
    assert snap["summary"]["ok"] == 8, "八路必须都读到东西（否则等价性/透传都是假绿）"
    # 新鲜窗判据按 as_of 算：把 as_of 推到 100 天后 ⇒ 种子里 1h 前的状态必然掉出 12h 窗
    future = _run(ws.world_state_snapshot(
        UID, CID, as_of=now_naive_utc() + timedelta(days=100)))
    fresh_now = _run(ws.world_state_snapshot(UID, CID))

    def _flag(snap, value):
        return next(i["fresh_at_as_of"] for i in _rows(snap, "world_facts") if i["value"] == value)
    assert _flag(fresh_now, "在加班") is True
    assert _flag(future, "在加班") is False
    assert _flag(future, "在杭州") is False, "location 窗 72h，同样掉出"


def test_as_of带tzinfo按同一时刻归一为naive_UTC(snap_db):
    aware = datetime(2026, 5, 8, 14, 30, tzinfo=timezone(timedelta(hours=8)))   # 北京 14:30
    snap = _run(ws.world_state_snapshot(UID, CID, as_of=aware))
    assert snap["as_of"] == "2026-05-08 06:30:00", "换算同一时刻，不做本地时区裁剪"


def test_as_of真的驱动新鲜判定而非只是打标(snap_db):
    now = now_naive_utc()
    fresh = _run(ws.world_state_snapshot(UID, CID, as_of=now - timedelta(hours=2)))
    stale = _run(ws.world_state_snapshot(UID, CID, as_of=now + timedelta(hours=24)))

    def _flag(snap):
        return next(i["fresh_at_as_of"] for i in _items(snap, "status_memories")
                    if i["value"] == _STATUS_MEM_VALUE)
    assert _flag(fresh) is True
    assert _flag(stale) is False


# ─────────────── ④ 异常隔离 ───────────────

def test_单路抛错只标该路其余照常(snap_db, monkeypatch):
    import app.memory.user_facts as uf

    async def _raise(*_a, **_kw):
        raise RuntimeError("假会话炸了")

    monkeypatch.setattr(uf, "get_active_user_facts", _raise)
    snap = _run(ws.world_state_snapshot(UID, CID))
    meta = snap["routes"]["user_facts"]
    assert meta["status"] == "degraded"
    assert "RuntimeError: 假会话炸了" in meta["error"]
    assert meta["items"] == [] and meta["count"] == 0 and meta["elapsed_ms"] >= 0.0
    assert snap["summary"]["degraded"] == 1
    assert snap["summary"]["degraded_routes"] == ["user_facts"]
    # 其余路没被污染（共享会话的 PendingRollback 已解除），内容照读
    assert any(i["value"] == "在加班，心情一般" for i in _items(snap, "character_current_status"))
    assert snap["routes"]["world_facts"]["count"] >= 2
    assert snap["routes"]["character_states"]["count"] == 1
    assert snap["routes"]["working_state"]["count"] == 1
    assert snap["routes"]["status_memories"]["count"] == 2


def test_单路抛错打WARNING日志(snap_db, monkeypatch, caplog):
    async def _raise(**_kw):
        raise RuntimeError("炸给我看")

    monkeypatch.setitem(ws._ROUTE_READERS_IMPL, "life_states", _raise)
    with caplog.at_level("WARNING"):
        snap = _run(ws.world_state_snapshot(UID, CID))
    assert snap["routes"]["life_states"]["status"] == "degraded"
    assert any(r.levelname == "WARNING" and "life_states" in r.getMessage()
               for r in caplog.records), "降级必须留 WARNING 痕迹"
    assert snap["routes"]["character_states"]["status"] == "ok"


def test_会话开不起来八路全降级仍返回完整结构(snap_db, monkeypatch):
    import app.db.database as db_mod

    def _boom():
        raise RuntimeError("数据库失联")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    snap = _run(ws.world_state_snapshot(UID, CID))
    assert set(snap["routes"]) == set(ws.ALL_ROUTES)
    assert snap["summary"]["degraded"] == 8
    assert all("session_unavailable" in m["error"] for m in snap["routes"].values())
    assert snap["as_of"] and snap["cross_store"] == {}


def test_空库不抛(snap_db):
    snap = _run(ws.world_state_snapshot(9999, 9999))
    assert snap["summary"]["degraded"] == 0
    assert snap["summary"]["empty"] == 8, snap["routes"]
    assert snap["summary"]["item_count"] == 0
    assert snap["cross_store"] == {}


# ─────────────── ⑤ 纯只读 ───────────────

def test_零写库_一写就报错的会话壳下八路仍读通(snap_db, monkeypatch):
    """把会话换成「add/flush/commit 立刻抛错」的壳（读操作走真会话）。

    入口若试图写库，对应该路会被打成 degraded——所以「degraded==0 且真读到内容」
    才是零写库的正证，而不是空库假绿。
    """
    import app.db.database as db_mod
    import app.memory.user_facts as uf
    real = snap_db
    forbidden = ("add", "add_all", "merge", "delete", "flush", "commit")

    class _NoWrite:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        async def __aenter__(self):
            await self._inner.__aenter__()
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_inner"), name)

    def _patched_factory(*_a, **_k):
        inner = real()
        for name in forbidden:
            def _forbid(*_aa, _n=name, **_kk):
                raise AssertionError(f"只读入口不得调用 {_n}()")
            setattr(inner, name, _forbid)
        return inner

    for mod in (db_mod, facts_mod, uf):
        monkeypatch.setattr(mod, "async_session_factory", _patched_factory)
    # 先自证绊子真有效（否则本用例是假绿）：直接写一下必须当场炸
    async def _probe():
        async with _patched_factory() as db:
            with pytest.raises(AssertionError, match="只读入口不得调用"):
                db.add(User(id=77, username="probe", nickname="probe"))
    _run(_probe())
    snap = _run(ws.world_state_snapshot(UID, CID))
    assert snap["summary"]["degraded"] == 0, snap["summary"]["degraded_routes"]
    assert snap["summary"]["ok"] == 8
    assert any(i["value"] == "在加班，心情一般" for i in _items(snap, "character_current_status"))
    assert snap["routes"]["world_facts"]["count"] >= 2
    assert snap["routes"]["user_facts"]["count"] >= 1


def test_入口AST无写库调用无缓存():
    """静态锁：AST 里不得出现写库/缓存调用（比 grep 稳，文档字符串提到 commit 也不算违规）。"""
    tree = ast.parse(inspect.getsource(ws))
    bad_calls = {"commit", "flush", "add", "add_all", "merge", "delete", "execute"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                # db.execute 属正常读；只禁写口
                if f.attr in (bad_calls - {"execute"}):
                    offenders.append(f.attr)
                if f.attr in ("cache", "cached", "lru_cache"):
                    offenders.append(f.attr)
        if isinstance(node, ast.ImportFrom) and (node.module or "") in ("functools", "cachetools"):
            offenders.append(f"import {node.module}")
    assert offenders == [], f"只读聚合入口越界调用：{sorted(set(offenders))}"


# ─────────────── ⑥ 死读路径埋点 ───────────────

def _spy_obs(monkeypatch):
    import app.memory.observability as obs
    calls: list[tuple] = []
    monkeypatch.setattr(obs, "obs_event",
                        lambda cid, metric, detail, kind=None: calls.append((cid, metric, detail)))
    return calls


def test_埋点在current_state路径每轮只触发一次(snap_db, monkeypatch):
    calls = _spy_obs(monkeypatch)
    _run(ws.world_state_snapshot(UID, CID))
    hit = [c for c in calls if c[1] == METRIC]
    assert len(hit) == 1, f"埋点应每轮一次（world_facts/user_facts 各路不得重复计数），实得 {len(hit)}"
    assert hit[0][0] == CID
    assert set(hit[0][2]) == {"user_id", "rows", "kept", "predicates"}
    # 再跑一轮 ⇒ 累计两次（证明它挂在读路径上、不是一次性注册）
    _run(ws.world_state_snapshot(UID, CID))
    assert len([c for c in calls if c[1] == METRIC]) == 2


def test_埋点计数等于subject_user命中行数(snap_db, monkeypatch):
    calls = _spy_obs(monkeypatch)
    async def _add_user_fact():
        async with snap_db() as db:
            db.add(WorldFact(user_id=UID, character_id=CID, subject_type="user", subject_id=UID,
                             predicate="mood", object_value="开心", status="active",
                             audience='["public"]', asserted_at=now_naive_utc()))
            await db.commit()
    _run(_add_user_fact())
    _run(ws.world_state_snapshot(UID, CID))
    detail = next(c[2] for c in calls if c[1] == METRIC)
    assert detail == {"user_id": UID, "rows": 2, "kept": 2,
                      "predicates": ["location", "mood"]}, "rows=subject=user 命中行数"


def test_埋点炸掉不影响锚点返回值(snap_db, monkeypatch):
    import app.memory.observability as obs
    import app.memory.current_state as cs

    def _boom(*_a, **_k):
        raise RuntimeError("埋点炸了")

    monkeypatch.setattr(obs, "obs_event", _boom)
    assert _run(cs._char_world_user_facts(CID, UID)) == {"location": "在杭州"}


# ─────────────── ⑦ 零行为：分区一行未改 ───────────────

def test_注入分区未引用本入口():
    """步骤 2 只建入口、步骤 5 才接线：任何 context 分区 import world_state 都算越界。"""
    import pathlib
    root = pathlib.Path(inspect.getfile(ws)).parents[1] / "agent" / "context"
    offenders = [p.name for p in sorted(root.glob("*.py"))
                 if "events.world_state" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"分区越界接线：{offenders}"
