# -*- coding: utf-8 -*-
"""F5/F3（2026-09-08）：life_loop 写库锁退避重试 + 不可映射意图清障（mock，无真实 DB）。

- _retry_on_lock：database is locked 退避重试 0.3s/0.6s（共 2 次重试后放弃）；
  非锁 OperationalError 不重试直接抛。
- LifeLoopTask._consume_unmappable_intents：映射外 pending 意图置 consumed，防永挂。
"""
import asyncio
import time

import pytest
from sqlalchemy.exc import OperationalError

from app.life.life_loop import LifeLoopTask, _retry_on_lock, _LOCK_RETRY_DELAYS


def _locked_err():
    return OperationalError("INSERT ...", {}, Exception("database is locked"))


def test_retry_on_lock_重试后成功():
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise _locked_err()
        return "ok"

    assert asyncio.run(_retry_on_lock(flaky, "test")) == "ok"
    assert len(calls) == 2


def test_retry_on_lock_重试耗尽放弃():
    calls = []

    async def always_locked():
        calls.append(1)
        raise _locked_err()

    with pytest.raises(OperationalError):
        asyncio.run(_retry_on_lock(always_locked, "test"))
    assert len(calls) == 3  # 首次 + 2 次重试
    assert _LOCK_RETRY_DELAYS == (0.3, 0.6)


def test_retry_on_lock_非锁错误不重试():
    calls = []

    async def boom():
        calls.append(1)
        raise OperationalError("INSERT ...", {}, Exception("no such table: x"))

    with pytest.raises(OperationalError):
        asyncio.run(_retry_on_lock(boom, "test"))
    assert len(calls) == 1


def test_retry_on_lock_退避间隔():
    """退避 0.3s/0.6s（对齐 chat_intent._persist 的有限重试模式）"""
    marks = []

    async def always_locked():
        marks.append(time.monotonic())
        raise _locked_err()

    with pytest.raises(OperationalError):
        asyncio.run(_retry_on_lock(always_locked, "test"))
    assert len(marks) == 3
    assert marks[1] - marks[0] >= 0.25
    assert marks[2] - marks[1] >= 0.5


# ─────────── F3a：不可映射意图清障 ───────────

class _IntentRow:
    def __init__(self, id, action_type):
        self.id = id
        self.action_type = action_type
        self.status = "pending"
        self.consumed_at = None


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._scalars = _Scalars(rows)

    def scalars(self):
        return self._scalars


class _StubDB:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def execute(self, *a, **kw):
        return _Result(list(self.rows))

    async def commit(self):
        self.commits += 1


def test_f3_不可映射意图置consumed():
    """rest/eat 可映射 → 保留；dance 不可映射 → 置 consumed（不再永挂）"""
    rows = [_IntentRow(2, "rest"), _IntentRow(9, "eat"), _IntentRow(10, "dance")]
    db = _StubDB(rows)
    asyncio.run(LifeLoopTask()._consume_unmappable_intents(db, 13))
    assert rows[0].status == "pending" and rows[0].consumed_at is None
    assert rows[1].status == "pending"
    assert rows[2].status == "consumed" and rows[2].consumed_at is not None
    assert db.commits == 1


def test_f3_全部可映射不提交():
    rows = [_IntentRow(9, "eat")]
    db = _StubDB(rows)
    asyncio.run(LifeLoopTask()._consume_unmappable_intents(db, 13))
    assert rows[0].status == "pending"
    assert db.commits == 0


def test_f3_清障异常静默():
    class _BoomDB:
        async def execute(self, *a, **kw):
            raise RuntimeError("boom")

    asyncio.run(LifeLoopTask()._consume_unmappable_intents(_BoomDB(), 13))  # 不抛即通过
