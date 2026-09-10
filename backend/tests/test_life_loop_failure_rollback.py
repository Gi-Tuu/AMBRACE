# -*- coding: utf-8 -*-
"""§4.4 / §4.4B（2026-09-09）life_loop 失败回滚与计数清理单测（stub DB，零真实库）。

§4.4：``_execute`` 里 started 日志先单独 commit、L5 又加了「pre-memory commit」，
慢操作（_build_summary / LLM / add_followup / 记忆写入）抛错时 energy/needs/location
早已固化，只标 failed 会留下「活动失败但状态按活动进行过落库」的脏状态。
修复：进入状态回流前做快照，except 分支用独立短事务按快照回写 life_states。

§4.4B：``_llm_copy_counts`` 的 key 已带北京日期；补每日首轮清理非今日 key，防长跑累积。
"""
import asyncio
import json

from app.life import life_loop
from app.life.decision import ACTIONS, Decision, StateSnapshot
from app.life.life_loop import LifeLoopTask


class _RowResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeSession:
    """独立短事务用的假 session：execute 返回给定行，统计 commit 次数。"""

    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _RowResult(self.row)

    async def commit(self):
        self.commits += 1


class _ExecDB:
    """_execute 主 session 的 stub（只需 add/commit/refresh）。"""

    def __init__(self):
        self.commits = 0

    def add(self, *a, **k):
        return None

    async def commit(self):
        self.commits += 1

    async def refresh(self, *a, **k):
        return None


class _Char:
    id = 7
    user_id = 3
    name = "小鹿"


class _State:
    """life_states 行的替身（只含 _execute 会改的字段）。"""

    def __init__(self):
        self.character_id = 7
        self.energy = 70
        self.location = "home"
        self.current_room = "living"
        self.location_updated_at = None
        self.needs_json = json.dumps(
            {k: 50 for k in ("curiosity", "productivity", "relaxation", "social",
                             "creativity", "learning", "reflection", "entertainment")},
            ensure_ascii=False,
        )


def _snap():
    return StateSnapshot(character_id=7, user_id=3, energy=70, focus=50,
                         needs={}, phase="afternoon", mood=50, fatigue=30, anger=10,
                         location="home", current_room="living")


def test_活动失败_状态按快照回写(monkeypatch):
    """_build_summary 抛错 → 活动日志 failed，且 life_states 回到活动前（非零成本脏改）。"""
    row = _State()
    sess = _FakeSession(row)

    async def _boom(self, db, char, decision, act):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(LifeLoopTask, "_build_summary", _boom)
    monkeypatch.setattr(life_loop, "async_session_factory", lambda: sess)

    st = _State()
    needs = json.loads(st.needs_json)
    before = (st.energy, st.location, st.current_room, st.needs_json)

    db = _ExecDB()
    asyncio.run(LifeLoopTask()._execute(
        db, _Char(), st, needs, Decision("study", reason="need"), _snap()))

    # 内存对象被改过（状态回流先于慢操作发生——这正是脏状态来源）
    assert st.energy != before[0] and st.current_room != before[2]
    # 独立短事务按快照回写：DB 行回到活动前
    assert row.energy == before[0]
    assert row.location == before[1]
    assert row.current_room == before[2]
    assert row.needs_json == before[3]
    assert sess.commits == 1


def test_活动失败_回写异常只记warning不二次抛(monkeypatch):
    """回写自身失败（如 DB 不可用）不得把异常抛回调用方。"""

    async def _boom(self, db, char, decision, act):
        raise RuntimeError("summary failed")

    class _BrokenSession(_FakeSession):
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    monkeypatch.setattr(LifeLoopTask, "_build_summary", _boom)
    monkeypatch.setattr(life_loop, "async_session_factory", lambda: _BrokenSession(None))

    st = _State()
    needs = json.loads(st.needs_json)
    asyncio.run(LifeLoopTask()._execute(  # 不抛即通过
        _ExecDB(), _Char(), st, needs, Decision("browse", reason="need"), _snap()))


def test_成功路径不回写(monkeypatch):
    """成功完成的活动不得触发回写（_restore_state_after_failure 只在 except 调用）。"""
    row = _State()
    sess = _FakeSession(row)
    called = {"n": 0}

    async def _ok_summary(self, db, char, decision, act):
        return "模板文案"

    async def _no_mem(self, db, character_id):
        return False  # 记忆节流命中 → 跳过记忆写入与 pre-memory commit

    def _count_restore(self, character_id, snapshot):
        called["n"] += 1

    monkeypatch.setattr(LifeLoopTask, "_build_summary", _ok_summary)
    monkeypatch.setattr(LifeLoopTask, "_memory_allowed_today", _no_mem)
    monkeypatch.setattr(LifeLoopTask, "_restore_state_after_failure", _count_restore)
    monkeypatch.setattr(life_loop, "async_session_factory", lambda: sess)

    st = _State()
    needs = json.loads(st.needs_json)
    asyncio.run(LifeLoopTask()._execute(
        _ExecDB(), _Char(), st, needs,
        Decision("study", reason="need"), _snap()))
    assert called["n"] == 0


def test_llm_copy_counts_非今日key被清理():
    """§4.4B：取 key 时顺手清理非今日的过期 key（key 已带北京日期，限额语义跨天重置）。"""
    from app.life.life_loop import _llm_copy_counts

    _llm_copy_counts.clear()
    try:
        today = life_loop._beijing_date_str()
        _llm_copy_counts[(1, today)] = 2
        _llm_copy_counts[(2, "2020-01-01")] = 1
        assert LifeLoopTask()._llm_copy_key(1) == (1, today)
        assert _llm_copy_counts == {(1, today): 2}
        # 当日限额语义不变：已达上限的角色仍判定不可再用 LLM 文案
        assert LifeLoopTask()._llm_copy_allowed(1) is False
        assert LifeLoopTask()._llm_copy_allowed(3) is True
    finally:
        _llm_copy_counts.clear()


def test_动作表study落记忆_保证用例命中失败段():
    """前置断言：study 的 memory=True，才会走到会抛错的 _build_summary。"""
    assert ACTIONS["study"].memory is True
