# -*- coding: utf-8 -*-
"""周期任务持久化台账（app/scheduling/periodic_state.py）回归 —— 2026-09-26 批次 PT / M1。

守的底线：
1. 到期判据来自持久化时间戳；读不到/坏内容一律视为「从未跑过」（宁补跑一次，不可漏跑）；
2. 失败按 15m→30m→60m→（任务自身 interval）阶梯退避，成功一次即归零；
3. 同 key 不可重入，不同 key 各用各的锁互不阻塞；任务异常绝不冒泡给主循环；
4. 写盘失败不静默：记 ERROR + 内存兜底，时间戳不倒退（否则会变成「每拍重跑」）；
5. scheduler.py 里 file_cleanup / pis_stale 两个分支确实改走台账，其余 tick 计数原样保留。

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

    # 其余 tick 分支一律不许动
    for keep in ("moment_counter", "comment_counter", "extract_counter", "identity_counter",
                 "state_decay_counter", "life_counter", "life_loop_counter", "game_stuck_counter",
                 "diary_counter", "reflection_counter", "memory_counter", "purge_counter"):
        assert f"{keep} += TICK" in src, f"{keep} 属于其它 tick 分支，必须原样保留"
