# -*- coding: utf-8 -*-
"""周期任务持久化台账（app/scheduling/periodic_state.py）回归 —— 2026-09-26 批次 PT / M1。

守的底线：
1. 到期判据来自持久化时间戳；读不到/坏内容一律视为「从未跑过」（宁补跑一次，不可漏跑）；
2. 失败按 15m→30m→60m→（任务自身 interval）阶梯退避，成功一次即归零；
3. 同 key 不可重入，不同 key 各用各的锁互不阻塞；任务异常绝不冒泡给主循环；
4. 写盘失败不静默：记 ERROR + 内存兜底，时间戳不倒退（否则会变成「每拍重跑」）；
5. scheduler.py 里 file_cleanup / pis_stale 两个分支确实改走台账（M1）；M2 把剩下 13 个任务
   也迁到台账，并新增「每日一次」口径 run_daily_if_due（见本文件末尾 M2 用例）。

全部用 tmp_path + monkeypatch 隔离：不触生产库、不写 backend/data/。
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.scheduling import periodic_state as pst

H6 = timedelta(hours=6)
H1 = timedelta(hours=1)
_SCHEDULER_PY = Path(__file__).resolve().parent.parent / "app" / "scheduling" / "scheduler.py"


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    """台账落 tmp_path + 每例一套新锁/新兜底（避免跨用例、跨事件循环串味）。"""
    path = tmp_path / "periodic_state.json"
    monkeypatch.setattr(pst, "_STATE_FILE", path)
    monkeypatch.setattr(pst, "_LOCKS", {})
    monkeypatch.setattr(pst, "_LOCAL_STAMPS", {})
    return path


@pytest.fixture()
def frozen_clock(monkeypatch):
    """冻结台账的「本拍时间」，退避阶梯与到期判断才可精确计算；clock['now'] 可手动推进。"""
    clock = {"now": datetime(2026, 9, 26, 12, 0, 0)}
    monkeypatch.setattr(pst, "now_naive_utc", lambda: clock["now"])
    return clock


# ────────────────────────── ① 到期判断 ──────────────────────────

def test_无记录即到期_成功后按间隔再到期(state_file):
    now = datetime(2026, 9, 26, 12, 0, 0)
    assert pst.last_done("file_cleanup") is None
    assert pst.is_due("file_cleanup", H6, now=now) is True, "从未跑过必须补跑"

    pst.mark_done("file_cleanup", when=now)
    assert pst.last_done("file_cleanup") == now
    assert pst.is_due("file_cleanup", H6, now=now + H6 - timedelta(seconds=1)) is False
    assert pst.is_due("file_cleanup", H6, now=now + H6) is True


# ────────────────────────── ② 坏内容不抛 ──────────────────────────

@pytest.mark.parametrize("junk", [
    "{not json}",                     # 坏 JSON
    "",                               # 空文件
    "[1, 2]",                         # 合法 JSON 但不是对象
    json.dumps({"k": "不是对象"}),      # 条目不成形
    json.dumps({"k": {"last_success": "瞎写的", "fail_streak": "x"}}),  # 字段读不出
])
def test_坏内容视为从未跑过且不抛(state_file, junk):
    state_file.write_text(junk, encoding="utf-8")
    assert pst.last_done("k") is None
    assert pst.fail_streak("k") == 0
    assert pst.is_due("k", H6) is True


# ────────────────────────── ③ 失败退避阶梯 ──────────────────────────

@pytest.mark.parametrize("iv", [H6, H1], ids=["interval=6h", "interval=1h"])
def test_失败退避阶梯_15m_30m_60m_然后等整拍(state_file, iv):
    """第 4 档及以后不再是快速重试，而是等满任务自己的 interval（故按 6h / 1h 各测一遍）。"""
    now = datetime(2026, 9, 26, 12, 0, 0)
    ladder = [timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=60), iv, iv]
    for streak, backoff in enumerate(ladder, start=1):
        # 造到「恰好该跑」且已连败 streak-1 次（mark_done 会把 streak 归零，故直接预置台账）
        pst._write_entry("k", now - iv, streak - 1)
        assert pst.fail_streak("k") == streak - 1
        pst.mark_failed("k", iv, when=now)
        assert pst.fail_streak("k") == streak
        assert pst.last_done("k") == now - iv + backoff
        assert pst.is_due("k", iv, now=now + backoff - timedelta(seconds=1)) is False
        assert pst.is_due("k", iv, now=now + backoff) is True, \
            f"streak={streak} 应在 {backoff} 后可重试"


def test_成功一次后streak归零(state_file, frozen_clock):
    now = frozen_clock["now"]
    pst.mark_done("k", when=now - H6)

    async def _boom():
        raise RuntimeError("llm down")

    async def _ok():
        return None

    assert asyncio.run(pst.run_if_due("k", H6, _boom)) is True
    assert pst.fail_streak("k") == 1

    frozen_clock["now"] = now + timedelta(minutes=15)          # 退避到点
    assert asyncio.run(pst.run_if_due("k", H6, _ok)) is True
    assert pst.fail_streak("k") == 0, "成功必须把 streak 打回 0"
    assert pst.last_done("k") == now + timedelta(minutes=15)
    assert pst.is_due("k", H6, now=now + timedelta(minutes=15) + H6 - timedelta(seconds=1)) is False, \
        "归零后回到任务自己的正常拍子"


# ────────────────────────── ④ 同 key 不可重入 ──────────────────────────

def test_run_if_due_同拍重入只跑一次(state_file):
    hold = asyncio.Event()
    ran = []

    async def _body():
        ran.append(1)
        await hold.wait()                                      # 占住这一拍直到放行

    async def _go():
        first = asyncio.create_task(pst.run_if_due("k", H6, _body, reason="a"))
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)
        second = await pst.run_if_due("k", H6, _body, reason="b")
        hold.set()
        return await first, second

    r1, r2 = asyncio.run(_go())
    assert (r1, r2) == (True, False), "上一拍没跑完的这一拍必须返回 False"
    assert ran == [1], "任务体只被跑一次"
    assert pst.last_done("k") is not None, "跑完必须记账（否则每拍重跑）"


def test_run_if_due_锁内复核到期(state_file, monkeypatch):
    """锁外预检 + 锁内再判一次，消灭「读状态→执行」之间的双读窗口。"""
    asked = []
    ran = []

    def _fake_is_due(key, interval, now=None):
        asked.append(1)
        return len(asked) == 1                                 # 第二次已被别的拍刷新

    async def _body():
        ran.append(1)

    monkeypatch.setattr(pst, "is_due", _fake_is_due)
    assert asyncio.run(pst.run_if_due("k", H6, _body)) is False
    assert ran == [], "锁内复核拦下后不得执行"
    assert len(asked) == 2


# ────────────────────────── ⑤ 异常不外抛 ──────────────────────────

def test_任务异常不冒泡_返回True且streak加一(state_file, frozen_clock, caplog):
    now = frozen_clock["now"]
    pst.mark_done("k", when=now - H6)

    async def _boom():
        raise RuntimeError("sweep boom")

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(pst.run_if_due("k", H6, _boom, reason="tick")) is True, \
            "这一拍确实跑了，只是失败"
    assert pst.fail_streak("k") == 1
    assert pst.last_done("k") == now - H6 + pst.RETRY_BACKOFF[0]
    assert any("Periodic task failed" in r.getMessage() for r in caplog.records), "失败必须留痕"


# ────────────────────────── ⑥ 不同 key 互不影响 ──────────────────────────

def test_不同key各自独立(state_file):
    now = datetime(2026, 9, 26, 12, 0, 0)
    pst.mark_done("file_cleanup", when=now)
    pst.mark_failed("pis_stale", H1, when=now)

    assert pst.is_due("file_cleanup", H6, now=now + timedelta(minutes=59)) is False
    assert pst.is_due("pis_stale", H1, now=now + timedelta(minutes=14)) is False
    assert pst.is_due("pis_stale", H1, now=now + timedelta(minutes=15)) is True
    assert (pst.fail_streak("file_cleanup"), pst.fail_streak("pis_stale")) == (0, 1)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert set(data) == {"file_cleanup", "pis_stale"}, "两条台账互不覆盖"


def test_不同key的锁互不阻塞(state_file):
    hold = asyncio.Event()
    ran = []

    async def _slow():
        ran.append("slow")
        await hold.wait()

    async def _quick():
        ran.append("quick")

    async def _go():
        busy = asyncio.create_task(pst.run_if_due("busy", H6, _slow))
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)
        other = await pst.run_if_due("free", H1, _quick)       # 别的 key 不受 busy 影响
        same = await pst.run_if_due("busy", H1, _quick)        # 同 key 才拦
        hold.set()
        return await busy, other, same

    r_busy, r_other, r_same = asyncio.run(_go())
    assert (r_busy, r_other, r_same) == (True, True, False)
    assert ran == ["slow", "quick"]


# ────────────────────────── ⑦ 写盘失败口径 ──────────────────────────

def test_写盘失败记ERROR并落内存兜底不倒退(tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file", encoding="utf-8")        # 父目录是个文件 ⇒ 必写失败
    monkeypatch.setattr(pst, "_STATE_FILE", blocked / "periodic_state.json")
    monkeypatch.setattr(pst, "_LOCKS", {})
    monkeypatch.setattr(pst, "_LOCAL_STAMPS", {})
    when = datetime(2026, 9, 26, 9, 9, 9)

    with caplog.at_level(logging.ERROR):
        pst.mark_done("k", when=when)                          # 不抛
    assert not (blocked / "periodic_state.json").exists()
    assert any("Write periodic state failed" in r.getMessage() for r in caplog.records), "写失败不得静默"
    assert pst.last_done("k") == when, "内存兜底：时间戳不能装作没发生"
    assert pst.is_due("k", H6, now=when + timedelta(minutes=5)) is False, "不能退化成每拍重跑"
    assert pst.is_due("k", H6, now=when + H6) is True


# ────────────────────────── ⑧ scheduler 接线静态断言 ──────────────────────────

def test_scheduler接线_两个分支改走台账其余不动():
    src = _SCHEDULER_PY.read_text(encoding="utf-8-sig")

    assert "file_cleanup_counter" not in src
    assert "pis_stale_counter" not in src
    assert 'run_if_due("file_cleanup", FILE_CLEANUP_INTERVAL, _cleanup_files_tick, reason="tick")' in src
    assert 'run_if_due("pis_stale", PIS_STALE_INTERVAL, _pis_stale_tick, reason="tick")' in src
    assert "FILE_CLEANUP_INTERVAL = timedelta(hours=6)" in src
    assert "PIS_STALE_INTERVAL = timedelta(hours=1)" in src

    # 搬走的分支体逐字保留（缩进换成函数体层级）
    assert '\n    spawn_background(cleanup_expired_files(days=5), name="sched-cleanup-files")' in src
    assert '\n    spawn_background(cleanup_expired_voice(days=14), name="sched-cleanup-voice")' in src
    assert "\n    from app.events.store import purge_expired_domain_events" in src
    assert "\n        _n_stale = await _pis_stale()" in src
    assert '"Prospective intent sweep: stale=%d expired=%d stale_cue=%d"' in src

    # M2（2026-09-26 同批第二棒）：原先这里的「其余 tick 分支一律不许动」断言已由 M2 派单
    # 明确推翻（那 12 个计数分支正是本批要迁走的目标），故改为断言它们已全部消失——
    # 完整的 13 个 key / 工厂函数接线断言见本文件末尾 ⑨⑩。
    assert "+= TICK" not in src, "主循环里不该再有进程内计数自增"


# ──────────────────── ⑨ 「每日一次」口径 run_daily_if_due（M2） ────────────────────

@pytest.fixture()
def local_clock(state_file, monkeypatch):
    """冻结 UTC 本拍时间并把应用时区钉在 +8：本地小时/本地日期完全可控。

    约定：UTC 15:00 == 本地 23:00（北京），于是「23:00 窗口」可用整点时间直接摆。
    """
    monkeypatch.setattr(pst, "_LOCAL_RETRY_AT", {})
    monkeypatch.setattr(pst, "app_tz_offset_hours", lambda: 8)
    clock = {"now": datetime(2026, 9, 26, 15, 0, 0)}                        # 本地 09-26 23:00
    monkeypatch.setattr(pst, "now_naive_utc", lambda: clock["now"])
    return clock


def _ok_body(ran):
    async def _body():
        ran.append(1)
    return _body


def test_每日一次_窗口外不跑也不记账(local_clock):
    local_clock["now"] = datetime(2026, 9, 26, 14, 0, 0)                   # 本地 22:00
    ran = []
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is False
    assert ran == [], "窗口外不得执行"
    assert not pst._STATE_FILE.exists(), "窗口外不记账：台账里连这个 key 都不该出现"
    assert pst.last_done("diary") is None


def test_每日一次_当天只跑一次跨天再跑(local_clock):
    ran = []
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is True
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is False
    assert ran == [1], "同一本地日内第二次必须被台账拦下"
    assert pst.last_done("diary") == datetime(2026, 9, 26, 15, 0, 0)

    local_clock["now"] = datetime(2026, 9, 26, 15, 30, 0)                   # 同一天窗口内
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is False
    local_clock["now"] = datetime(2026, 9, 27, 15, 0, 0)                    # 次日窗口内（本地 09-27 23:00）
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is True
    assert ran == [1, 1], "跨天后窗口内应再跑一次"


def test_每日一次_窗口边界含起点不含前一天(local_clock):
    """min_local_hour 边界：本地小时 == 阈值 ⇒ 跑；== 阈值-1 ⇒ 不跑。"""
    ran = []
    local_clock["now"] = datetime(2026, 9, 26, 15, 0, 0)                    # 本地 23:00 == 阈值
    assert pst.is_daily_due("diary", 23) is True
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is True

    local_clock["now"] = datetime(2026, 9, 27, 14, 0, 0)                    # 本地 22:00 == 阈值-1
    assert pst.is_daily_due("diary", 23) is False
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is False
    assert ran == [1], "阈值前一小时不得执行"
    # min_local_hour=0（纪念日口径）：任何本地小时都在窗口内
    local_clock["now"] = datetime(2026, 9, 27, 3, 0, 0)                     # 本地 11:00
    assert asyncio.run(pst.run_daily_if_due("anniversary", _ok_body(ran))) is True
    assert ran == [1, 1]


def test_每日一次_失败不动当天标记退避到点后窗口内重试(local_clock, caplog):
    """失败 ⇒ 返回 True、streak+1、当天仍未成功 ⇒ 15 分钟后窗口内再试（成功才清闸门）。"""
    async def _boom():
        raise RuntimeError("diary llm down")

    with caplog.at_level(logging.WARNING):
        assert asyncio.run(pst.run_daily_if_due("diary", _boom, min_local_hour=23)) is True
    assert pst.fail_streak("diary") == 1
    assert pst.last_done("diary") is None, "失败绝不能把当天标记写成「已成功」"
    assert any("Daily task failed" in r.getMessage() for r in caplog.records), "失败必须留痕"

    local_clock["now"] = datetime(2026, 9, 26, 15, 10, 0)                   # +10 分钟，退避未到
    assert pst.is_daily_due("diary", 23) is False
    local_clock["now"] = datetime(2026, 9, 26, 15, 15, 0)                   # +15 分钟，退避到点
    assert pst.is_daily_due("diary", 23) is True, "当天没成功 ⇒ 退避后窗口内应重试"
    ran = []
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is True
    assert ran == [1] and pst.fail_streak("diary") == 0, "成功一次即归零并清掉退避闸门"
    assert pst.is_daily_due("diary", 23) is False, "当天已成功 ⇒ 同窗口内不再跑"


def test_每日一次_退避阶梯15m_30m_60m_然后等第二天窗口(local_clock):
    """连败按 15m→30m→60m 退避；阶梯用尽后 next_retry_at 推到 +24h（＝第二天的窗口）。"""
    now = datetime(2026, 9, 26, 15, 0, 0)
    for streak, step in enumerate(
        (timedelta(minutes=15), timedelta(minutes=30), timedelta(minutes=60),
         pst.DAILY_INTERVAL, pst.DAILY_INTERVAL), start=1):
        pst.mark_failed_daily("diary", when=now)
        assert pst.fail_streak("diary") == streak
        assert pst.next_retry_at("diary") == now + step, "第 %d 次失败应退避 %s" % (streak, step)
        # 这里只验退避闸门，窗口阈值传 0（推进时间会跨过 23:00 窗口，那是上面两个用例管的事）
        assert pst.is_daily_due("diary", 0, now=now + step - timedelta(seconds=1)) is False
        assert pst.is_daily_due("diary", 0, now=now + step) is True
        now = now + step
    assert pst.last_done("diary") is None, "整串失败记账都不许伪造当天成功"


def test_每日一次_写盘失败落内存兜底不丢退避闸门(tmp_path, monkeypatch, caplog):
    """台账写不进去时，退避闸门也要落内存兜底，否则会退化成「每拍重试」打爆 LLM。"""
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file", encoding="utf-8")                    # 父目录是个文件 ⇒ 必写失败
    monkeypatch.setattr(pst, "_STATE_FILE", blocked / "periodic_state.json")
    monkeypatch.setattr(pst, "_LOCKS", {})
    monkeypatch.setattr(pst, "_LOCAL_STAMPS", {})
    monkeypatch.setattr(pst, "_LOCAL_RETRY_AT", {})
    monkeypatch.setattr(pst, "app_tz_offset_hours", lambda: 8)
    when = datetime(2026, 9, 26, 15, 0, 0)
    monkeypatch.setattr(pst, "now_naive_utc", lambda: when)

    with caplog.at_level(logging.ERROR):
        pst.mark_failed_daily("diary", when=when)                           # 不抛
    assert any("Write periodic state failed" in r.getMessage() for r in caplog.records)
    assert pst.next_retry_at("diary") == when + pst.RETRY_BACKOFF[0], "闸门不能装作没发生"
    assert pst.is_daily_due("diary", 23) is False
    assert pst.last_done("diary") is None, "写失败也不得伪造当天成功"
    # 与 M1 同款口径：内存兜底只兜「判据时间戳」，fail_streak 仍以文件为准（写不进去时档位不涨）


def test_每日一次_同拍重入只跑一次(local_clock):
    hold = asyncio.Event()
    ran = []

    async def _body():
        ran.append(1)
        await hold.wait()

    async def _go():
        first = asyncio.create_task(pst.run_daily_if_due("reflection", _body, min_local_hour=23))
        for _ in range(100):
            if ran:
                break
            await asyncio.sleep(0.01)
        second = await pst.run_daily_if_due("reflection", _body, min_local_hour=23)
        hold.set()
        return await first, second

    r1, r2 = asyncio.run(_go())
    assert (r1, r2) == (True, False), "上一拍没跑完的这一拍必须返回 False"
    assert ran == [1]


# ──────────────────── ⑩ 13 个任务接线静态断言（M2） ────────────────────

M2_KEYS = {
    # key -> (interval 常量名 / None 表示每日一次, 协程工厂名)
    "moment": ("MOMENT_INTERVAL", "_moment_tick"),
    "comment": ("COMMENT_INTERVAL", "_comment_tick"),
    "extract": ("EXTRACT_INTERVAL", "_extract_tick"),
    "identity": ("IDENTITY_INTERVAL", "_identity_tick"),
    "state_decay": ("STATE_DECAY_INTERVAL", "_state_decay_tick"),
    "life": ("LIFE_INTERVAL", "_life_tick"),
    "life_loop": ("LIFE_LOOP_INTERVAL", "_life_loop_tick"),
    "game_stuck": ("GAME_STUCK_INTERVAL", "_game_stuck_tick"),
    "purge": ("PURGE_INTERVAL", "_account_purge_tick"),
    "diary": (None, "_diary_tick"),
    "reflection": (None, "_reflection_tick"),
    "group_compact": (None, "_group_compact_tick"),
    "anniversary": (None, "_anniversary_tick"),
}
# 每天一次的四个任务里，只有纪念日本来就没有小时窗口（「每天第一拍」），故 min_local_hour 用默认 0
DAILY_NO_WINDOW = {"anniversary"}
# 原 memory 分支里挤着的第二件事（日终记忆维护）不在派单的 13 个 key 表里，但派单同时要求
# 删掉它的当天标记与本任务无关的逻辑不搬 ⇒ 只能同样挂台账，见交付说明「拿不准」一节
EXTRA_DAILY_KEYS = {"memory_maintenance": "_memory_maintenance_tick"}


def _flat(src: str) -> str:
    """折叠所有空白：断言实参时不受「一行写不下换行」影响。"""
    return " ".join(src.split())


def test_M2接线_十三个key全部改走台账():
    src = _SCHEDULER_PY.read_text(encoding="utf-8-sig")
    flat = _flat(src)

    # ① 13 个 key 都出现在 run_if_due( / run_daily_if_due( 的实参里，且各就各位
    for key, (iv, fn) in M2_KEYS.items():
        if iv is None:
            window = "" if key in DAILY_NO_WINDOW else "min_local_hour=23, "
            want = 'await run_daily_if_due("%s", %s, %sreason="tick")' % (key, fn, window)
        else:
            want = 'await run_if_due("%s", %s, %s, reason="tick")' % (key, iv, fn)
        assert want in flat, want
    for key, fn in EXTRA_DAILY_KEYS.items():
        assert 'await run_daily_if_due("%s", %s, min_local_hour=23, reason="tick")' % (key, fn) in flat

    # ② 进程内计数判据全部消失（派单 §4：搬完后主循环里不应再出现任何计数变量）
    assert "counter" not in src.lower(), "scheduler.py 里不得再出现任何 tick 计数变量"
    assert "+= TICK" not in src, "主循环里不该再有计数自增"
    assert "date.today()" not in src, "日期标记改由台账按「应用本地日期」记账"
    assert "TICK = 30" in src and "await asyncio.sleep(TICK)" in src, "30 秒节拍本身不动"
    assert "supervisor.heartbeat(\"scheduler\")" in src, "心跳不动"

    # ③ 每个工厂函数都定义且被引用（定义处 + 接线处至少各一次）
    factories = [(k, f) for k, (_iv, f) in M2_KEYS.items()] + list(EXTRA_DAILY_KEYS.items())
    for key, fn in factories:
        assert "async def %s():" % fn in src, "%s（%s）未定义" % (fn, key)
        assert src.count(fn) >= 2, "%s 定义了却没被接线引用" % fn
    for fn in ("_cleanup_files_tick", "_pis_stale_tick"):
        assert src.count(fn) >= 2, "M1 的两个工厂不许动"


def test_M2间隔常量与迁走前的秒数阈值等价():
    src = _SCHEDULER_PY.read_text(encoding="utf-8-sig")
    for decl in ("MOMENT_INTERVAL = timedelta(seconds=MOMENT_CHECK_INTERVAL)",      # 600s
                 "COMMENT_INTERVAL = timedelta(seconds=300)",
                 "EXTRACT_INTERVAL = timedelta(seconds=900)",
                 "IDENTITY_INTERVAL = timedelta(seconds=300)",
                 "STATE_DECAY_INTERVAL = timedelta(seconds=3600)",
                 "LIFE_INTERVAL = timedelta(seconds=3600)",
                 "LIFE_LOOP_INTERVAL = timedelta(seconds=1800)",
                 "GAME_STUCK_INTERVAL = timedelta(seconds=300)",
                 "PURGE_INTERVAL = timedelta(seconds=ACCOUNT_PURGE_CHECK_INTERVAL)"):  # 沿用常量
        assert decl in src, decl
    # 每日一次没有 interval 概念 ⇒ 不该冒出无用的窗口常量
    assert "DIARY_WINDOW_INTERVAL" not in src


def test_M2搬用后关键分支体仍在源码里():
    """逐字搬用的抽样钉子：日志文案 / 窗口判断 / 任务名一旦被动过，这里就会红。"""
    src = _SCHEDULER_PY.read_text(encoding="utf-8-sig")
    for needle in (
        'if 7 <= local_hour < 24:',
        '_logger.warning("Publish pending moments error: %s", e)',
        '_logger.warning("Generate comments error: %s", e)',
        'name="sched-catchup-extract"',
        '"memory_summary",',
        '_logger.warning("Identity profile extraction failed: %s", _ipe)',
        'name="sched-state-drift"',
        '_logger.warning("Life tick error: %s", e)',
        '_logger.warning("Life loop error: %s", e)',
        'name="sched-resume-games"',
        '_logger.debug("Scheduler: generating diaries...")',
        'await generate_missing_diaries()',
        '_logger.warning("Daily reflections error: %s", e)',
        'await run_daily_memory_maintenance()',
        'name="sched-group-memory-compact"',
        'name="sched-account-purge"',
        'from app.scheduling.scheduler import _check_anniversaries_today as _run_anniv',
    ):
        assert needle in src, needle


# ──────────── ⑪ 跨午夜按「完成时刻」记账（P3-6，2026-09-26 批 C/D） ────────────

def test_每日一次_跨午夜按完成日记账次日窗口不再跑(local_clock):
    """23:59 开跑、00:05 跑完 ⇒ 成功日记「完成那天」。

    旧口径（按 started 记账）会把成功日记成前一天 ⇒ 次日窗口内再跑一次；本例即该缺口的红灯。
    """
    started = datetime(2026, 9, 26, 15, 59)     # UTC ⇒ 本地 09-26 23:59
    finished = datetime(2026, 9, 26, 16, 5)     # UTC ⇒ 本地 09-27 00:05（已跨午夜）
    local_clock["now"] = started

    async def _body():
        local_clock["now"] = finished           # 任务跑着跑着跨了午夜

    assert asyncio.run(pst.run_daily_if_due("anniversary", _body)) is True
    assert pst.last_done("anniversary") == finished, "必须按完成时刻记账"
    assert finished.strftime(pst._STATE_FMT) in pst._STATE_FILE.read_text(encoding="utf-8"), \
        "台账里落的必须是完成时刻"
    # 完成日（本地 09-27）当天：不得再判到期
    assert pst.is_daily_due("anniversary", 0, now=datetime(2026, 9, 26, 20, 0)) is False
    # 再往后一个本地日（09-28）恢复到期
    assert pst.is_daily_due("anniversary", 0, now=datetime(2026, 9, 27, 20, 0)) is True


def test_每日一次_同日执行行为不变(local_clock):
    """回归保护：没跨午夜时完成时刻与开始时刻同日 ⇒ 当天仍只跑一次、次日窗口再跑。"""
    started = datetime(2026, 9, 26, 15, 0)      # UTC ⇒ 本地 09-26 23:00
    finished = datetime(2026, 9, 26, 15, 20)    # UTC ⇒ 本地 09-26 23:20（同日）
    local_clock["now"] = started
    ran = []

    async def _body():
        ran.append(1)
        local_clock["now"] = finished

    assert asyncio.run(pst.run_daily_if_due("diary", _body, min_local_hour=23)) is True
    assert ran == [1]
    assert pst.last_done("diary") == finished
    assert pst.is_daily_due("diary", 23, now=finished + timedelta(minutes=30)) is False, \
        "同日窗口内不得再跑"
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is False
    local_clock["now"] = datetime(2026, 9, 27, 15, 0)   # 次日窗口（本地 09-27 23:00）
    assert asyncio.run(pst.run_daily_if_due("diary", _ok_body(ran), min_local_hour=23)) is True
    assert ran == [1, 1]
