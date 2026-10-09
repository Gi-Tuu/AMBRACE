# -*- coding: utf-8 -*-
"""§4.4 / §4.4B（2026-09-09）life_loop 失败回滚与计数清理单测（stub DB，零真实库）。

§4.4：``_execute`` 里 started 日志先单独 commit、L5 又加了「pre-memory commit」，
慢操作（_build_summary / LLM / add_followup / 记忆写入）抛错时 energy/needs/location
早已固化，只标 failed 会留下「活动失败但状态按活动进行过落库」的脏状态。
修复：进入状态回流前做快照，except 分支用独立短事务按快照回写 life_states。

§4.4B：LLM 文案日配额按北京日期分 key（限额语义跨天重置）。A41（2026-10-10）把这份计数
从进程内 ``_llm_copy_counts`` 迁到持久台账 ``periodic_state``，重启不再归零补发。
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


def test_llm_copy_日配额迁台账_跨天重置与当日限不变(tmp_path, monkeypatch):
    """§4.4B → A41：日配额计数从进程内 dict 迁到持久台账（key 自带北京日期 ⇒ 跨天重置）。

    对照（同一输入同一输出）：
    - 迁移前 ``_llm_copy_counts[(char, 北京日期)]``：未记过 ⇒ 可用；当日累计 2 次 ⇒ 不可用；
      换到另一天的 key ⇒ 重新可用。
    - 迁移后同一角色同一日走台账 key ``life_loop_llm_copy:{char}:{日期}``，判据逐字相同。
    """
    from app.scheduling import periodic_state as pst

    monkeypatch.setattr(pst, "_STATE_FILE", tmp_path / "periodic_state.json")
    monkeypatch.setattr(pst, "_LOCAL_COUNTERS", {})
    task = LifeLoopTask()
    today = life_loop._beijing_date_str()

    assert task._llm_copy_key(1) == f"life_loop_llm_copy:1:{today}"
    assert task._llm_copy_allowed(1) is True          # 从未记过 ⇒ 0 < 2
    task._bump_llm_copy(1)
    task._bump_llm_copy(1)
    assert task._llm_copy_allowed(1) is False         # 当日限额语义不变
    assert task._llm_copy_allowed(3) is True          # 不牵连其它角色
    monkeypatch.setattr(life_loop, "_beijing_date_str", lambda: "2020-01-01")
    assert task._llm_copy_allowed(1) is True          # 跨天 key 变 ⇒ 自然重置（旧剪枝的用途）


def test_llm_copy_重启不丢计数(tmp_path, monkeypatch):
    """A41 硬口径「重启不突发」：台账文件里已有 2 次 ⇒ 换新进程（清掉内存兜底）后仍判不可用。"""
    from app.scheduling import periodic_state as pst

    state_file = tmp_path / "periodic_state.json"
    monkeypatch.setattr(pst, "_STATE_FILE", state_file)
    monkeypatch.setattr(pst, "_LOCAL_COUNTERS", {})
    task = LifeLoopTask()
    task._bump_llm_copy(7)
    task._bump_llm_copy(7)
    assert state_file.exists(), "计数必须落盘，否则重启即归零"

    # 模拟进程重启：内存兜底清空，只剩磁盘上那份台账
    monkeypatch.setattr(pst, "_LOCAL_COUNTERS", {})
    assert task._llm_copy_allowed(7) is False


def test_动作表study落记忆_保证用例命中失败段():
    """前置断言：study 的 memory=True，才会走到会抛错的 _build_summary。"""
    assert ACTIONS["study"].memory is True
