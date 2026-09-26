# -*- coding: utf-8 -*-
"""承重结构批 A（2026-09-26）回归：decay 幂等 / 评星失败冒泡 / maintenance 锁与退避 / 账本加锁。

守的底线：
1. 结算不是强化——decay 不得刷新 last_reinforce_at，否则每 6 小时把全表 importance 顶回近满值；
2. 「有候选但整批失败」必须冒泡，否则上层 maintenance 的失败回拨重试永不触发；
3. 同拍重入只跑一次；失败按 15m→30m→60m→6h 阶梯退避；
4. 状态文件 JSON 化但向后兼容旧纯文本；写盘失败不再静默；
5. 账本读改写串行化，并发不丢更新。

全部用 tmp_path + monkeypatch 隔离：不触生产库、不写 backend/data/。
"""
import asyncio
import json
import math
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.memory import ai_rating as ar
from app.memory import decay as decay_mod
from app.memory import maintenance_schedule as ms
from app.memory import rating_quota as rq
from app.utils.timeutil import now_naive_utc


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """维护状态文件落 tmp_path + 每例一把新锁 + 清掉写盘兜底（避免跨用例/跨事件循环串味）。"""
    path = tmp_path / "last_memory_maintenance"
    monkeypatch.setattr(ms, "_STATE_FILE", path)
    monkeypatch.setattr(ms, "_LOCAL_LAST_SUCCESS", None)
    monkeypatch.setattr(ms, "_MAINT_LOCK", asyncio.Lock())
    return path


@pytest.fixture()
def quota_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rq, "_STATE_FILE", tmp_path / "ai_rating_quota.json")


@pytest.fixture()
def fixed_clock(state_file, monkeypatch):
    """冻结 maintenance 的「本拍时间」，让退避阶梯可算。"""
    now = datetime(2026, 9, 26, 12, 0, 0)
    monkeypatch.setattr(ms, "now_naive_utc", lambda: now)
    return now


# ────────────────────────── ① decay 幂等 ──────────────────────────

def _mem(now, *, days=10, strength=7.0):
    return SimpleNamespace(
        id=1, character_id=1, memory_type="event", importance=80.0,
        strength_days=strength, is_pinned=False, is_locked=False, is_archived=False,
        delete_at=None, last_reinforce_at=now - timedelta(days=days),
        decay_base_at=None, created_at=now - timedelta(days=days),
        is_core=False, confirmation_count=0, why_it_matters=None,
        epistemic_status="FACT", reliability_score=0.9,
    )


class _FakeDB:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def test_结算不刷新基准且重跑同值(monkeypatch):
    """批 A 最高优先：删掉结算路径上的 last_reinforce_at = now（生产实测一次结算刷了 5535 行）。"""
    from app.memory import tiering

    monkeypatch.setattr(tiering, "tiered_decay_on", lambda: False)  # 走现状口径：S = strength_days
    now = now_naive_utc()
    mem = _mem(now, days=10, strength=7.0)
    base = mem.last_reinforce_at

    asyncio.run(decay_mod._apply_decay(_FakeDB(), mem, now=now))
    assert mem.last_reinforce_at == base, "结算不是强化，不得刷新基准"
    assert mem.importance == pytest.approx(math.exp(-10 / 7) * 120, abs=0.5)
    assert mem.delete_at is None  # 28.8% > 20% 阈值，不进删除倒计时

    first = mem.importance
    asyncio.run(decay_mod._apply_decay(_FakeDB(), mem, now=now))
    assert mem.importance == first, "基准固定 ⇒ 同一 now 重跑幂等"
    assert mem.last_reinforce_at == base


def test_到期删除与倒计时分支不受影响(monkeypatch):
    """只动结算那一行：base 缺失兜底、删除倒计时、到期删除三处行为保持。"""
    from app.memory import tiering

    monkeypatch.setattr(tiering, "tiered_decay_on", lambda: False)
    monkeypatch.setattr("app.memory.observability.obs_event", lambda *a, **k: None)
    now = now_naive_utc()
    deleted = []

    async def _fake_delete(mid):
        deleted.append(mid)

    monkeypatch.setattr("app.memory.service.delete_memory", _fake_delete)

    fresh = _mem(now)
    fresh.last_reinforce_at = None
    fresh.decay_base_at = None
    fresh.created_at = None
    assert asyncio.run(decay_mod._apply_decay(_FakeDB(), fresh, now=now)) is False
    assert fresh.last_reinforce_at == now, "base 兜底分支仍可写基准"

    countdown = _mem(now, days=40, strength=7.0)   # pct 跌破 20% → 进 3 天倒计时
    assert asyncio.run(decay_mod._apply_decay(_FakeDB(), countdown, now=now)) is False
    assert countdown.delete_at is not None and countdown.delete_at > now

    due = _mem(now)
    due.delete_at = now - timedelta(seconds=1)
    assert asyncio.run(decay_mod._apply_decay(_FakeDB(), due, now=now)) is True
    assert deleted == [1]


# ────────────────────────── ②③ 评星失败冒泡 / 无候选不抛 ──────────────────────────

class _FakeSession:
    """只喂「活跃角色」这一条查询，其余读路径全部 monkeypatch 掉，不落库。"""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def execute(self, *_a, **_k):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: self._rows))

    async def commit(self):
        pass


@pytest.fixture()
def rating_harness(monkeypatch, quota_file):
    events: list[tuple] = []

    def _trace(batch_id, character_id, route, detail, **_kw):
        events.append((route, detail))

    monkeypatch.setattr(ar, "async_session_factory", lambda: _FakeSession([SimpleNamespace(id=13)]))
    monkeypatch.setattr(ar, "_trace_event", _trace)
    monkeypatch.setattr(ar, "_trace_inputs", lambda *a, **k: None)
    return events


def _patch_rating(monkeypatch, *, candidates, results):
    async def _pick(_db, _cid, _limit):
        return list(candidates)

    async def _rate(_char, _items, *, obs=None):
        return list(results)

    monkeypatch.setattr(ar, "_pick_candidates", _pick)
    monkeypatch.setattr(ar, "_rate_batch", _rate)


def test_有候选但整批失败必须冒泡(rating_harness, monkeypatch):
    _patch_rating(monkeypatch, candidates=[SimpleNamespace(id=101, content="正文")], results=[])
    with pytest.raises(ar.RatingPartialFailure):
        asyncio.run(ar.run_ai_rating())
    end = [d for r, d in rating_harness if r == ar.TRACE_ROUTE_RUN and d.get("phase") == "end"]
    assert end, "抛之前收尾留痕必须先落"
    assert end[0]["outcomes"] == {"parse_failed": 1}


def test_角色调用异常也冒泡(rating_harness, monkeypatch):
    async def _pick(_db, _cid, _limit):
        return [SimpleNamespace(id=101, content="正文")]

    async def _boom(_char, _items, *, obs=None):
        raise RuntimeError("llm down")

    monkeypatch.setattr(ar, "_pick_candidates", _pick)
    monkeypatch.setattr(ar, "_rate_batch", _boom)
    with pytest.raises(ar.RatingPartialFailure):
        asyncio.run(ar.run_ai_rating())
    end = [d for r, d in rating_harness if r == ar.TRACE_ROUTE_RUN and d.get("phase") == "end"]
    assert end[0]["outcomes"] == {"call_failed": 1}


def test_无候选不抛只返回0(rating_harness, monkeypatch):
    _patch_rating(monkeypatch, candidates=[], results=[])
    assert asyncio.run(ar.run_ai_rating()) == 0


def test_评星成功不抛(rating_harness, monkeypatch):
    """有候选且成功评完 ⇒ 不冒泡（冒泡只针对整批失败）。"""
    mem = SimpleNamespace(id=101, content="正文", memory_type="event", importance=1.0,
                          strength_days=7.0, review_count=0, last_reinforce_at=None,
                          delete_at=None, next_review_at=None, ai_rated=False)

    async def _pick(_db, _cid, _limit):
        return [mem]

    async def _rate(_char, _items, *, obs=None):
        return [{"id": 101, "star": 4}]

    monkeypatch.setattr(ar, "_pick_candidates", _pick)
    monkeypatch.setattr(ar, "_rate_batch", _rate)
    assert asyncio.run(ar.run_ai_rating()) == 1
    assert mem.ai_rated is True
    assert mem.importance == 80.0


# ────────────────────────── ④ run_if_due 不可重入 ──────────────────────────

def test_run_if_due_同拍重入只跑一次(state_file, monkeypatch, fixed_clock):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    hold = asyncio.Event()
    seen = []

    async def _decay():
        seen.append("decay")
        await hold.wait()          # 占住锁直到放行

    async def _rate():
        return 0

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _decay, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)

    async def _go():
        first = asyncio.create_task(ms.run_if_due(reason="a"))
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        second = await ms.run_if_due(reason="b")
        hold.set()
        return await first, second

    r1, r2 = asyncio.run(_go())
    assert (r1, r2) == (True, False), "重入的这一拍必须 False（没跑）"
    assert seen == ["decay"], "decay 只被跑一次"


def test_run_if_due_锁内重新判到期(state_file, monkeypatch, fixed_clock):
    """预检一次 + 锁内再判一次（第二次不到期就不跑），消灭「读状态→执行」之间的双读窗口。"""
    asked = []
    ran = []

    def _fake_is_due(now=None, interval=ms.INTERVAL):
        asked.append(1)
        return len(asked) == 1     # 预检通过、锁内那次已被别的拍刷新

    async def _decay():
        ran.append("decay")

    async def _rate():
        ran.append("rate")
        return 0

    monkeypatch.setattr(ms, "is_due", _fake_is_due)
    monkeypatch.setattr("app.memory.run_memory_decay", _decay, raising=False)
    monkeypatch.setattr("app.memory.ai_rating.run_ai_rating", _rate, raising=False)
    assert asyncio.run(ms.run_if_due()) is False
    assert ran == [], "锁内复核拦下后不得执行"
    assert len(asked) == 2, "必须问两次（锁外预检 + 锁内复核）"


# ────────────────────────── ⑤ 失败退避阶梯 ──────────────────────────

def test_失败按阶梯退避(state_file, monkeypatch, fixed_clock):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    async def _boom():
        raise RuntimeError("decay boom")

    async def _rate():
        return 0

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _boom, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)

    now = fixed_clock
    for streak, wait_min in ((1, 15), (2, 30), (3, 60), (4, 360), (5, 360)):
        ms._write_state(now - ms.INTERVAL, fail_streak=streak - 1)   # 造到「恰好该跑」
        assert asyncio.run(ms.run_if_due(reason="ladder")) is True
        assert ms.fail_streak() == streak
        assert ms.is_due(now=now + timedelta(minutes=wait_min - 1)) is False
        assert ms.is_due(now=now + timedelta(minutes=wait_min)) is True, \
            f"streak={streak} 应在 {wait_min} 分钟后可重试"


def test_成功后streak归零(state_file, monkeypatch, fixed_clock):
    import app.memory as memory_pkg
    import app.memory.ai_rating as rating_mod

    async def _boom():
        raise RuntimeError("boom")

    async def _ok():
        return 0

    async def _rate():
        return 0

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _boom, raising=False)
    monkeypatch.setattr(rating_mod, "run_ai_rating", _rate, raising=False)
    now = fixed_clock
    ms._write_state(now - ms.INTERVAL, fail_streak=3)
    asyncio.run(ms.run_if_due(reason="ladder"))
    assert ms.fail_streak() == 4

    monkeypatch.setattr(memory_pkg, "run_memory_decay", _ok, raising=False)
    ms._write_state(now - ms.INTERVAL, fail_streak=4)
    assert asyncio.run(ms.run_if_due(reason="ladder")) is True
    assert ms.fail_streak() == 0, "成功必须把 streak 打回 0"
    assert ms.is_due(now=now + timedelta(minutes=15)) is False  # 回到 6 小时正常拍子


# ────────────────────────── ⑥ 状态文件兼容 / 写盘失败不静默 ──────────────────────────

def test_旧纯文本可读新JSON可回(state_file):
    legacy = datetime(2026, 9, 25, 3, 4, 5)
    state_file.write_text(legacy.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    assert ms.last_run_at() == legacy
    assert ms.fail_streak() == 0

    new = datetime(2026, 9, 26, 6, 7, 8)
    ms._write_state(new, fail_streak=2)
    assert json.loads(state_file.read_text(encoding="utf-8")) == {
        "last_success": "2026-09-26 06:07:08", "fail_streak": 2}
    assert ms.last_run_at() == new
    assert ms.fail_streak() == 2


def test_坏内容仍视为从未跑过(state_file):
    state_file.write_text("garbage", encoding="utf-8")
    assert ms.last_run_at() is None
    assert ms.fail_streak() == 0
    assert ms.is_due() is True


def test_写盘失败落到内存兜底(tmp_path, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file", encoding="utf-8")
    monkeypatch.setattr(ms, "_STATE_FILE", blocked / "last_memory_maintenance")
    monkeypatch.setattr(ms, "_LOCAL_LAST_SUCCESS", None)
    when = datetime(2026, 9, 26, 9, 9, 9)
    ms._write_state(when, fail_streak=0)          # 不抛，但也不再静默丢
    assert not (blocked / "last_memory_maintenance").exists()
    assert ms.last_run_at() == when, "写盘失败时用内存兜底，时间戳不能装作没发生"


# ────────────────────────── ⑦ 账本并发 ──────────────────────────

def test_账本并发加不计不丢更新(quota_file):
    threads, per_thread = 8, 10
    barrier = threading.Barrier(threads)

    def _worker():
        barrier.wait()
        for _ in range(per_thread):
            rq.add(13, 1)

    workers = [threading.Thread(target=_worker) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()
    assert rq.used_today(13) == threads * per_thread
    assert rq.add(13, 0) == threads * per_thread
