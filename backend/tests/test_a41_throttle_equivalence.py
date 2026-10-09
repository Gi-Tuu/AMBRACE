# -*- coding: utf-8 -*-
"""A41（A37 批 4）节流态迁移的「迁移前 vs 迁移后」对照 + 重启不突发单测。

任务书 §2 硬口径要求每处迁移都能回答两件事：

1. **行为等价**：同一输入下判据与迁移前的进程内实现逐字相同。本文件把旧实现原地重写成
   ``_old_*`` 纯函数，与迁移后的台账实现**并排跑同一批输入**比结果，而不是只测新实现；
2. **重启不突发**：把台账状态交给一个「新进程」（清空内存兜底），判据不得复活成
   「从没发过／从没计过」⇒ 推送不会补发、生活拍不会当拍就活动、LLM 文案不会多花两次。

失败方向（任务书 §2）：台账读不出 ⇒ 一律按最保守（不发／不重复发），且**不许**把
「读不到」记成「已发送」。零真实库、零网络，台账一律指向 ``tmp_path`` 的临时文件。
"""
import json
import time
from datetime import timedelta

import pytest

from app.scheduling import periodic_state as pst

USER = 999
WINDOW = 1800
MAX = 5
BASE = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _ledger_tmp(tmp_path, monkeypatch):
    """每例一份独立台账文件 + 独立落盘队列 + 清空内存兜底，模拟「进程刚起来」的干净状态。"""
    monkeypatch.setattr(pst, "_STATE_FILE", tmp_path / "periodic_state.json")
    for name in ("_LOCAL_COUNTERS", "_LOCAL_MARKS", "_LOCAL_STAMPS", "_LOCAL_RETRY_AT"):
        monkeypatch.setattr(pst, name, {})
    from app.memory import extract_queue
    monkeypatch.setattr(extract_queue, "_QUEUE_FILE", tmp_path / "extract_queue.json")
    yield


# ── C42 推送滑窗 ──

def _old_prune(bucket: list[float], now: float) -> list[float]:
    """迁移前的剪枝判据（push_service 旧代码逐字照抄）：`t > now - _RATE_WINDOW`。"""
    cutoff = now - WINDOW
    return [t for t in bucket if t > cutoff]


def test_推送滑窗_剪枝判据逐字一致():
    """旧 `t > now-window` 与新 `now-t < window` 在窗口边界上同结果（含恰好等于边界的戳）。"""
    key = "push_rate:probe"
    for delta in (0, 1, WINDOW - 1, WINDOW, WINDOW + 1, WINDOW * 2, WINDOW * 3):
        marks = [BASE, BASE + delta / 2, BASE + delta]
        assert pst._patch_entry(key, {"marks": marks}), "样例时间戳先写进台账"
        old = _old_prune(marks, BASE + delta)
        new = pst._window_marks_raw(key, WINDOW, BASE + delta)
        assert new == old, (delta, old, new)


def test_推送滑窗_连发序列与旧实现同判定():
    """同一串「先发 7 次」的输入 ⇒ 新旧实现的放行/拦截序列逐位相同（前 5 放行、后 2 拦）。"""
    from app.application.push_service import _check_rate_limit, _consume_rate_slot

    old_bucket: list[float] = []
    seq_new, seq_old = [], []
    for i in range(7):
        now = BASE + i * 100                 # 每 100 秒一发，全部落在同一个 30 分钟窗内
        old_ok = len(_old_prune(old_bucket, now)) < MAX
        new_ok = _check_rate_limit(USER, "normal")
        seq_old.append(old_ok)
        seq_new.append(new_ok)
        if old_ok:
            old_bucket = _old_prune(old_bucket, now) + [now]
        if new_ok:
            _consume_rate_slot(USER)
    assert seq_new == seq_old == [True] * MAX + [False] * 2
    assert _check_rate_limit(USER, "high") is True, "高优先级豁免语义不变"


def test_推送滑窗_重启不突发(tmp_path):
    """旧写法重启即空桶 ⇒ 能一口气补发 5 条；台账必须把已发的 5 条带过重启。"""
    from app.application.push_service import _check_rate_limit, _consume_rate_slot

    for _ in range(MAX):
        _consume_rate_slot(USER)
    state_file = tmp_path / "periodic_state.json"
    assert state_file.exists(), "滑窗时间戳必须落盘，否则重启即归零"
    doc = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(doc[f"push_rate:{USER}"]["marks"]) == MAX

    setattr(pst, "_LOCAL_MARKS", {})          # 模拟新进程：内存兜底全清，只剩磁盘那份
    assert _check_rate_limit(USER, "normal") is False, "重启后第 6 条仍须被频控（不得补发）"


def test_推送滑窗_读不出按最保守且检查路径不回写(tmp_path):
    """坏台账 ⇒ 判 False（不发），并且绝不因为「读不到」就写一条已发送记账。"""
    from app.application.push_service import _check_rate_limit

    state_file = tmp_path / "periodic_state.json"
    raw = "{ 这不是合法 JSON"
    state_file.write_text(raw, encoding="utf-8")
    assert _check_rate_limit(USER, "normal") is False
    assert state_file.read_text(encoding="utf-8") == raw, "只读检查不得改写台账文件"
    assert pst.window_count("push_rate:%d" % USER, WINDOW) is None


def test_推送滑窗_写盘失败仍记账不漏发(tmp_path, monkeypatch):
    """落盘失败 ⇒ 时间戳进内存兜底；同进程内后续检查仍按已发计数（宁少发不多发）。"""
    from app.application.push_service import _check_rate_limit, _consume_rate_slot

    monkeypatch.setattr(pst, "_patch_entry", lambda key, fields: False)
    for _ in range(MAX):
        _consume_rate_slot(USER)
    assert not tmp_path.joinpath("periodic_state.json").exists()
    assert _check_rate_limit(USER, "normal") is False, "写不进去时也必须记住这一发"


# ── C36 离线生活拍计数 ──

def test_生活拍_判据与迁移前逐字一致():
    """旧判据 `n % step == 0` 与 `_activity_due(n, step)` 在 n=1..12 × step∈{1,2,3} 上同结果。"""
    from app.life.life_tick import _activity_due

    for step in (1, 2, 3):
        for n in range(1, 13):
            assert _activity_due(n, step) is (n % step == 0)
    assert _activity_due(None, 1) is False, "记不清 ⇒ 不尝试活动（最保守）"


def test_生活拍_重启不清零():
    """旧写法 `_tick_count` 重启归零 ⇒ 强度 high（step=1）的角色重启当拍就活动。"""
    from app.life.life_tick import _activity_due, _next_tick_ordinal

    assert _next_tick_ordinal(7) == 1
    assert _next_tick_ordinal(7) == 2
    assert pst._STATE_FILE.exists(), "拍数必须落盘"
    setattr(pst, "_LOCAL_COUNTERS", {})          # 模拟新进程
    assert _next_tick_ordinal(7) == 3, "跨重启接着数，不回到 1"
    assert _activity_due(3, 3) is True, "低强度第 3 拍尝试，与旧口径同点"


def test_生活拍_读不出按最保守():
    from app.life.life_tick import _activity_due, _next_tick_ordinal

    pst._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pst._STATE_FILE.write_text("[坏文件]", encoding="utf-8")
    assert _next_tick_ordinal(7) is None
    assert _activity_due(_next_tick_ordinal(7), 1) is False


# ── C44 批量提取队列（extractor._pending / _pending_ids / _last_batch_at）──

SID = 900001


def _install_runner(monkeypatch, impl=None):
    """把 `run_internal` 换成记账假件；返回（提取调用记录, asyncio, extractor）。"""
    import asyncio
    from app.memory import extractor

    calls: list[dict] = []

    async def _fake(tool, payload, **kw):
        calls.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr("app.agent.internal_runner.run_internal", impl or _fake)
    return calls, asyncio, extractor


def test_队列_凑批与节流刷点与迁移前逐字一致(monkeypatch):
    """真·对照：把旧实现（进程内 dict + `_last_batch_at`）原样写成 `_old_model`，
    与迁移后的实现（落盘队列 + 台账节流）**并排喂同一串入队**，逐步比累计提取条数。

    旧实现的刷点：凑满 `BATCH_SIZE(4)` 提一批；节流窗内继续累积，累积到 `MAX_PENDING_PAIRS(10)`
    强刷；滞留超 `MAX_PENDING_AGE` 强刷。
    """
    calls, asyncio, extractor = _install_runner(monkeypatch)
    from app.memory import extractor as ex

    q: list[dict] = []
    last_at: dict[int, float] = {}
    old_total = 0
    per_step = []

    def _old_model(cid: int) -> int:
        """迁移前 `add_chat_memory_extraction` 的判据本体（逐字照抄，仅把提取动作换成计数）。"""
        now = time.time()
        q.append({"ts": now})
        stale = any(now - float(p.get("ts") or now) > ex.MAX_PENDING_AGE for p in q)
        if len(q) < ex.BATCH_SIZE and not stale:
            return 0
        if (now - last_at.get(cid, 0) < ex.EXTRACT_THROTTLE_SECONDS
                and len(q) < ex.MAX_PENDING_PAIRS and not stale):
            return 0
        n = len(q)
        q.clear()
        last_at[cid] = now
        return n

    for i in range(1, 17):
        old_total += _old_model(11)
        asyncio.run(extractor.add_chat_memory_extraction(
            SID, 11, 4, f"m{i}", f"r{i}", source_id=i))
        per_step.append((old_total, len(calls)))
        assert per_step[-1][0] == per_step[-1][1], (i, per_step[-1], "刷点与旧实现不同")
    # 触发点：第 4 条凑满一批、第 14 条累积触顶（5~14 共 10 条）
    assert [k for k, (o, _n) in enumerate(per_step, 1) if o and o != per_step[k - 2][0]] == [4, 14]
    assert per_step[-1][0] == 14


def test_队列_跨重启不丢(monkeypatch):
    """任务书 §1 目标 1 的硬要求：重启前攒下的配对，重启后凑满批必须照样提取（旧写法直接丢）。"""
    calls, asyncio, extractor = _install_runner(monkeypatch)
    from app.memory import extract_queue

    for i in (1, 2, 3):
        asyncio.run(extractor.add_chat_memory_extraction(
            SID, 11, 4, f"m{i}", f"r{i}", source_id=i))
    assert calls == [], "未满 BATCH_SIZE 不提取（旧新一致）"
    assert extract_queue._QUEUE_FILE.exists(), "配对必须落盘，否则重启即丢"
    assert len(extract_queue.peek(SID)) == 3

    # 模拟新进程：本模块不缓存任何队列内容，读盘即全部真相
    assert extract_queue.pending_source_ids() == {1, 2, 3}, "占位集合跨重启仍在"
    asyncio.run(extractor.add_chat_memory_extraction(
        SID, 11, 4, "m4", "r4", source_id=4))
    assert [c["source_id"] for c in calls] == [1, 2, 3, 4], "重启前那 3 条必须一起被提取"
    assert extract_queue.peek(SID) == []
    assert extract_queue.pending_source_ids() == set(), "提取完放开占位"


def test_队列_节流判据落台账_重启不复位(monkeypatch):
    """旧写法 `_last_batch_at` 重启归零 ⇒ 重启后第一拍立刻又提一批；台账必须记住节流窗。"""
    calls, asyncio, extractor = _install_runner(monkeypatch)

    for i in range(1, 5):
        asyncio.run(extractor.add_chat_memory_extraction(
            SID, 11, 4, f"m{i}", f"r{i}", source_id=i))
    assert len(calls) == 4
    for i in range(5, 9):
        asyncio.run(extractor.add_chat_memory_extraction(
            SID, 11, 4, f"m{i}", f"r{i}", source_id=i))
    assert len(calls) == 4, "节流窗内不刷（累积到 10 条才强刷）"

    setattr(pst, "_LOCAL_STAMPS", {})          # 模拟新进程：只剩磁盘上的台账
    assert pst.is_due(extractor._throttle_key(11),
                      timedelta(seconds=extractor.EXTRACT_THROTTLE_SECONDS)) is False, \
        "重启后节流窗不得复位"


def test_队列_滞留超时强刷用落盘的旧时间戳(monkeypatch):
    """滞留判据（MAX_PENDING_AGE）读的是配对自带的 ts；ts 落盘 ⇒ 超龄判定也跨重启成立。"""
    import time

    calls, asyncio, extractor = _install_runner(monkeypatch)
    from app.memory import extract_queue

    stale_ts = time.time() - extractor.MAX_PENDING_AGE - 1
    extract_queue.add(SID, {"user_message": "old", "ai_response": "reply",
                            "source_id": 77, "ts": stale_ts})
    asyncio.run(extractor.add_chat_memory_extraction(
        SID, 11, 4, "m", "r", source_id=78))
    assert sorted(c["source_id"] for c in calls) == [77, 78], "超龄即刻刷，不等凑批"


def test_队列_提取中占位不放开(monkeypatch):
    """旧语义：占位在每条提取的 finally 才放开（catchup 因此在途期间也跳过同一条）。"""
    seen: list[set[int]] = []

    async def _probe(tool, payload, **kw):
        from app.memory import extract_queue
        seen.append(extract_queue.pending_source_ids())
        return {"status": "ok"}

    calls, asyncio, extractor = _install_runner(monkeypatch, _probe)
    for i in (11, 12, 13):
        asyncio.run(extractor.add_chat_memory_extraction(
            SID, 11, 4, f"m{i}", f"r{i}", source_id=i))
    asyncio.run(extractor.add_chat_memory_extraction(
        SID, 11, 4, "m14", "r14", source_id=14))
    assert seen == [{11, 12, 13, 14}, {12, 13, 14}, {13, 14}, {14}], \
        "在途期间逐条递减：取出时四个都占位，每条提完才放开自己那条"
    from app.memory import extract_queue
    assert extract_queue.pending_source_ids() == set(), "逐条提完各自放开"


def test_队列_坏文件判空不抛():
    from app.memory import extract_queue

    extract_queue._QUEUE_FILE.write_text("{坏 JSON", encoding="utf-8")
    assert extract_queue.peek(SID) == []
    assert extract_queue.pending_source_ids() == set()
    extract_queue.remove_source(SID, 1)      # 不炸
    extract_queue.release_source(1)          # 不炸


def test_队列_超龄配对被丢弃以解锁catchup():
    """超过 HARD_AGE_SEC 的配对已出 catchup 的 2h 窗，留着只会挡住补采 ⇒ 丢弃并解锁。"""
    import time

    from app.memory import extract_queue

    dead_ts = time.time() - extract_queue.HARD_AGE_SEC - 1
    extract_queue.add(SID, {"user_message": "x", "ai_response": "y",
                            "source_id": 91, "ts": dead_ts})
    assert extract_queue.pending_source_ids() == set(), "超龄不再占位"
    assert extract_queue.peek(SID) == []


# ── C30/C32/C34 置顶摘要／身份画像：迁移前「只按时间淘汰」vs 迁移后「有新事实即失效」 ──

def _old_throttle(last, now, ttl: timedelta) -> bool:
    """迁移前语义（summary.py 旧代码逐字照抄）：``now - last < ttl`` 就沿用旧摘要。"""
    return (now - last) < ttl


def test_摘要判据_逐格对照_无新原料时逐字保留旧口径():
    from datetime import datetime

    from app.memory import summary as _s
    from app.memory.constants import SUMMARY_TTL_HOURS

    ttl = timedelta(hours=SUMMARY_TTL_HOURS)
    floor = _s.SUMMARY_REWRITE_FLOOR
    base = datetime(2026, 10, 10, 12, 0, 0)
    for age_min in (0, 30, 59, 60, 90, 6 * 60 - 1, 6 * 60, 7 * 60, 30 * 60):
        last = base - timedelta(minutes=age_min)
        old = _old_throttle(last, base, ttl)
        for nm in range(0, _s.STALE_NEW_MATERIAL_MIN + 3):
            new = _s._pin_still_usable(last, now=base, ttl=ttl, new_material=nm, floor=floor)
            if nm < _s.STALE_NEW_MATERIAL_MIN or (base - last) < floor:
                assert new == old, (age_min, nm)      # 够不着新事实判据 ⇒ 旧口径一字不变
            else:
                assert new is False, (age_min, nm)     # 新事实失效路径：TTL 没走完也判过期


def test_摘要成本护栏_地板必须各自低于TTL():
    """护栏的存在意义：身份画像每 5 分钟被 scheduler 问一次，没地板就退化成每 5 分钟一次 LLM。"""
    from app.memory import summary as _s
    from app.memory.constants import SUMMARY_TTL_HOURS

    assert _s.STALE_NEW_MATERIAL_MIN >= 2, "一条零碎写入不该把摘要打回炉"
    assert _s.SUMMARY_REWRITE_FLOOR < timedelta(hours=SUMMARY_TTL_HOURS)
    assert _s.IDENTITY_REWRITE_FLOOR < timedelta(hours=_s.IDENTITY_TTL_HOURS)
    assert _s.IDENTITY_REWRITE_FLOOR > _s.SUMMARY_REWRITE_FLOOR, "画像是长期口径，地板比摘要更宽"


# ── C43 事件总线：投递侧「过期即丢」 ──

def test_总线_默认窗就是facts的status窗():
    """§1 目标 3「按同一口径收口」：总线不许自己造一个新鲜度数值。"""
    from app.events import bus as _bus
    from app.events import facts as _facts

    assert _bus._default_ttl_sec() == float(_facts.STATUS_FRESH_HOURS * 3600)


def test_总线_迁移前后同输入对照_超窗才丢():
    """迁移前语义＝无到期（任何事件都投）；迁移后只丢「发出时刻晚于窗」的那些。"""
    import asyncio

    from app.events import bus as _bus

    eb = _bus.EventBus()
    seen: list[dict] = []

    async def _h(payload):
        seen.append(payload)

    eb.subscribe("probe", _h)
    now = time.time()
    for age_sec, expect in ((0, True), (3600, True),
                            (_bus._default_ttl_sec() - 1, True),
                            (_bus._default_ttl_sec() + 1, False),
                            (13 * 3600, False)):
        seen.clear()
        asyncio.run(eb.publish("probe", {"timestamp": now - age_sec}))
        assert (len(seen) == 1) is expect, (age_sec, seen)


def test_总线_缺时刻的事件补emitted_at照投且不污染调用方dict():
    """「读不到时刻」不许当成「很旧」⇒ 丢一条事件＝订阅侧永久丢一次落库，方向偏保守到「发」。"""
    import asyncio

    from app.events import bus as _bus

    eb = _bus.EventBus()
    seen: list[dict] = []

    async def _h(payload):
        seen.append(payload)

    eb.subscribe("probe", _h)
    caller = {"a": 1}
    asyncio.run(eb.publish("probe", caller))
    assert len(seen) == 1
    assert seen[0]["a"] == 1
    assert seen[0] is not caller, "总线投递的是副本，不返回头改调用方的 dict"
    assert "emitted_at" not in caller
    assert time.time() - seen[0]["emitted_at"] < 5, "补的时刻＝publish 那一刻"
    assert asyncio.run(eb.publish("probe", None)) is None, "空 payload 也不许炸"


def test_总线_慢订阅者把后面的推后时到点不投递():
    """审计 C43 的正修法：同一任务内顺序 await，前置 handler 阻塞 ⇒ 轮到后面时已过期就丢掉。"""
    import asyncio

    from app.events import bus as _bus

    eb = _bus.EventBus()
    order: list[str] = []

    async def _slow(payload):
        order.append("slow")
        time.sleep(0.05)                     # 阻塞事件循环＝等价于「一个慢订阅者把后面的推后」

    async def _tail(payload):
        order.append("tail")

    eb.subscribe("probe", _slow)
    eb.subscribe("probe", _tail)
    asyncio.run(eb.publish("probe", {"timestamp": time.time()}, ttl_sec=0.005))
    assert order == ["slow"], "第一个已投递；第二个轮到时时事件已过期 ⇒ 不再投递"


# ── C34 落库类「按事实淘汰」的端到端一例（真临时库，钉住验收句「有新记忆写入后立刻可重生」）──

@pytest.fixture()
def sum_env(monkeypatch, tmp_path):
    """临时库 + LLM 打桩（零计费）；pin 与原料的时间戳由用例逐条指定。"""
    import asyncio

    from _dbclone import clone_engine, make_session_factory

    from app.memory import summary as _s
    from app.models.character import AICharacter
    from app.models.memory import Memory
    from app.models.user import User
    from app.utils.timeutil import now_naive_utc

    engine = clone_engine(tmp_path / "a41_sum.db")
    factory = make_session_factory(engine)
    monkeypatch.setattr(_s, "async_session_factory", factory)
    calls: list[str] = []

    async def _llm(messages=None, **_kw):
        calls.append((messages or [{}])[0].get("content", ""))
        return "A41 重写后的摘要文本"

    async def _v2_true(*_a, **_kw):
        return True

    monkeypatch.setattr("app.agent.llm_client.chat_completion", _llm)
    monkeypatch.setattr("app.memory.flags.memory_v2_enabled", _v2_true)

    async def _seed_owner():
        async with factory() as db:
            db.add(User(id=1, username="a41_u", nickname="A41 用户"))
            db.add(AICharacter(id=201, user_id=1, name="A41 角色"))
            await db.commit()

    asyncio.run(_seed_owner())

    async def _add(*, content, mtype="event", sub_type=None, pinned=False, at=None):
        async with factory() as db:
            ts = at or now_naive_utc()
            m = Memory(user_id=1, character_id=201, memory_type=mtype, sub_type=sub_type,
                       source="chat", content=content, importance=50.0, is_pinned=pinned,
                       created_at=ts)
            m.updated_at = ts
            db.add(m)
            await db.commit()
            return m.id

    yield {"factory": factory, "add": lambda **kw: asyncio.run(_add(**kw)), "calls": calls}
    asyncio.run(engine.dispose())


@pytest.mark.slow
def test_摘要_三条新原料即重生_两条仍按旧口径节流(sum_env):
    """迁移前：2h 前的置顶必须等满 6h。迁移后：≥3 条新原料就回炉，<3 条时逐字不变。"""
    import asyncio

    from app.memory import summary as _s
    from app.utils.timeutil import now_naive_utc

    add = sum_env["add"]
    pin = add(content="旧印象摘要", sub_type="summary", pinned=True,
              at=now_naive_utc() - timedelta(hours=2))
    for i in range(_s.STALE_NEW_MATERIAL_MIN - 1):          # 只有 2 条新原料
        add(content=f"新事实{i}")
    out = asyncio.run(_s.summarize_memories(201, "event", force=False))
    assert out == {"generated": False, "memory_id": pin, "reason": "throttled"}, "旧口径的那一半"
    assert sum_env["calls"] == []

    add(content="第 3 条新事实")                                # 够了：3 条比置顶更新
    out = asyncio.run(_s.summarize_memories(201, "event", force=False))
    assert out["generated"] is True and out["memory_id"] == pin
    assert len(sum_env["calls"]) == 1, "重写一次 LLM（不是每次问都烧）"


@pytest.mark.slow
def test_摘要_重写后立刻再来被成本地板挡住(sum_env):
    """护栏实测：刚重写过的置顶，哪怕又落 3 条新原料，未过地板仍不重写（否则 LLM 被当轮询打）。"""
    import asyncio

    from app.memory import summary as _s
    from app.utils.timeutil import now_naive_utc

    add = sum_env["add"]
    add(content="旧印象摘要", sub_type="summary", pinned=True,
        at=now_naive_utc() - timedelta(hours=2))
    for i in range(_s.STALE_NEW_MATERIAL_MIN):
        add(content=f"新事实{i}")
    assert asyncio.run(_s.summarize_memories(201, "event", force=False))["generated"] is True
    sum_env["calls"].clear()
    for i in range(_s.STALE_NEW_MATERIAL_MIN):               # 再落 3 条
        add(content=f"又一条{i}")
    out = asyncio.run(_s.summarize_memories(201, "event", force=False))
    assert out["reason"] == "throttled", "未到 SUMMARY_REWRITE_FLOOR ⇒ 成本地板生效"
    assert sum_env["calls"] == []


@pytest.mark.slow
def test_身份画像_按各自地板失效_比摘要宽(sum_env):
    """画像 TTL 24h、地板 6h：7h 前的置顶 + 3 条新原料 ⇒ 回炉；2h 前的同输入 ⇒ 仍节流。"""
    import asyncio

    from app.memory import summary as _s
    from app.utils.timeutil import now_naive_utc

    add = sum_env["add"]

    def _probe(ago):
        add(content="旧画像", mtype="user_info", sub_type="identity",
            pinned=True, at=now_naive_utc() - timedelta(hours=ago))
        for i in range(_s.STALE_NEW_MATERIAL_MIN):
            add(content=f"画像原料{i}", mtype="user_info")
        return asyncio.run(_s.summarize_identity(201, 1, force=False))

    assert _probe(7)["generated"] is True, "已过 6h 地板 + 有新事实 ⇒ 按事实淘汰"
    sum_env["calls"].clear()
    assert _probe(2)["reason"] == "throttled", "未过地板时逐字走旧 24h 口径"
    assert sum_env["calls"] == []
