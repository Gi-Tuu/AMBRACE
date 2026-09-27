# -*- coding: utf-8 -*-
"""常驻任务监督器（app/utils/supervisor.py）单测：崩溃重建 / 主动 stop 不重建 / liveness 停滞判定 / 心跳停滞自检。"""
import asyncio
import time
from datetime import datetime, timezone

import app.utils.supervisor as sv_mod
from app.utils.supervisor import TaskSupervisor


async def _run_forever():
    """一个「运行到被取消」的常驻协程工厂目标。"""
    await asyncio.Event().wait()


def test_restart_after_crash_increments_and_respawns():
    """目标 task 意外（被 cancel 模拟崩溃）终结 → 退避重建 restarts++ 且新 task 存活。"""

    async def scenario():
        s = TaskSupervisor()
        s.register("scheduler", _run_forever, stall_sec=180)
        s.start()
        first = s._targets["scheduler"].task
        assert first is not None and not first.done(), "task should be alive after start"

        # 模拟崩溃：取消任务（等价「任务意外终结」）
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            pass
        assert first.done()

        # 让退避窗口「已过」，再巡检一次
        s._targets["scheduler"].last_start = time.monotonic() - 10
        await s._tick()

        second = s._targets["scheduler"].task
        assert s._targets["scheduler"].restarts == 1, s._targets["scheduler"].restarts
        assert second is not None and not second.done(), "new task should be alive after restart"
        assert second is not first, "a NEW task (not the dead one) should be running"

        await s.stop()

    asyncio.run(scenario())


def test_stop_marks_stopped_and_no_rebuild():
    """主动 stop() 后置 stopped 标记：监督循环不得重建（restarts 保持 0）。"""

    async def scenario():
        s = TaskSupervisor()
        s.register("scheduler", _run_forever, stall_sec=180)
        s.start()
        assert not s._targets["scheduler"].task.done()

        await s.stop()
        assert s._targets["scheduler"].stopped is True

        # 退避窗口早已过去，即使巡检也不应拉起
        s._targets["scheduler"].last_start = time.monotonic() - 100
        await s._tick()
        assert s._targets["scheduler"].restarts == 0, "must not rebuild after stop"
        assert s._targets["scheduler"].task is not None and s._targets["scheduler"].task.done()

    asyncio.run(scenario())


def test_register_is_idempotent_and_double_start_no_dup():
    """register 幂等 + start 重复调用不产生第二个任务（done 双检）。"""

    async def scenario():
        s = TaskSupervisor()
        s.register("scheduler", _run_forever, stall_sec=180)
        s.register("scheduler", _run_forever, stall_sec=180)  # 重复登记应幂等
        s.start()
        s.start()  # 重复 start 不应再拉
        assert len(s._targets) == 1
        assert not s._targets["scheduler"].task.done()
        await s.stop()

    asyncio.run(scenario())


def test_liveness_reports_alive_and_stall():
    """liveness()：新鲜心跳 → alive/不 stalled；无心跳长时间 → stalled 正确。"""

    async def scenario():
        s = TaskSupervisor()
        s.register("loop_alive", _run_forever, stall_sec=180)
        s.register("loop_stalled", _run_forever, stall_sec=10)
        s.start()

        # loop_alive：刷新心跳 → 新鲜
        s.heartbeat("loop_alive")
        # loop_stalled：让心跳变陈旧（不 tick，任务仍存活）
        s._targets["loop_stalled"].last_beat = time.monotonic() - 100

        lv = s.liveness()
        a = lv["loop_alive"]
        b = lv["loop_stalled"]
        assert a["alive"] is True and a["stalled"] is False, a
        assert b["alive"] is True and b["stalled"] is True, b
        assert b["seconds_since_heartbeat"] >= 100, b

        # 端点「总体 stalled」判定口径：任意循环 stalled 或 not alive
        assert any(v.get("stalled") or not v.get("alive") for v in lv.values())

        await s.stop()

    asyncio.run(scenario())


# ── HB-1（2026-09-28）：心跳停滞自检 + 只读心跳快照 ────────────────────────────

def _capture_errors(monkeypatch) -> list:
    """抓 supervisor 模块 logger.error（不依赖 caplog：项目 logger 自带 handler/propagate 配置）。"""
    got = []

    def _rec(msg, *args):
        got.append(str(msg) % args if args else str(msg))

    monkeypatch.setattr(sv_mod._log, "error", _rec)
    return got


def test_selfcheck_detects_stall_and_rebuilds_without_watch_loop(monkeypatch):
    """_watch 监督循环没跑时，自检仍应：①打含目标名/停滞秒数/上次心跳的 ERROR；②复用 _tick 取消并重建。"""
    errs = _capture_errors(monkeypatch)

    async def scenario():
        s = TaskSupervisor()
        s.register("scheduler", _run_forever, stall_sec=10)
        s.start()

        # 模拟「supervisor 自身也没跑」：拆掉监督循环（09-27 第二段静默的最可能形态）
        s._watch_task.cancel()
        try:
            await s._watch_task
        except asyncio.CancelledError:
            pass
        assert s._watch_task.done()

        first = s._targets["scheduler"].task
        s._targets["scheduler"].last_beat = time.monotonic() - 100
        s._targets["scheduler"].last_start = time.monotonic() - 100

        stalled = await s.selfcheck_once()
        assert stalled == ["scheduler"], stalled
        await asyncio.sleep(0.05)  # 取消需一轮事件循环才落地
        assert first.done() or first.cancelled(), "停滞任务应被交给既有取消路径"

        # 监督循环被自检重新拉起（不进被监督列表，靠这条兜底）
        assert s._watch_task is not None and not s._watch_task.done()

        # 重建走既有 _tick 退避路径：退避窗口过后应拉起新 task、restarts++
        s._targets["scheduler"].last_start = time.monotonic() - 10
        await s._tick()
        second = s._targets["scheduler"].task
        assert second is not None and not second.done()
        assert second is not first
        assert s._targets["scheduler"].restarts >= 1

        await s.stop()

    asyncio.run(scenario())

    joined = "\n".join(errs)
    assert "heartbeat selfcheck" in joined and "scheduler" in joined, joined
    assert "threshold=10s" in joined and "last heartbeat=" in joined, joined
    assert "watch loop not running" in joined, joined


def test_selfcheck_ignores_fresh_and_stopped_targets(monkeypatch):
    """自检不误伤：新鲜心跳不报；已主动 stop 的目标不报、也不复活监督循环。"""
    errs = _capture_errors(monkeypatch)

    async def scenario():
        s = TaskSupervisor()
        s.register("fresh", _run_forever, stall_sec=180)
        s.start()
        s.heartbeat("fresh")
        assert await s.selfcheck_once() == []
        assert errs == []
        watch = s._watch_task

        await s.stop()
        assert await s.selfcheck_once() == []
        assert s._watch_task is None, "stop 后自检不得复活监督循环"
        assert watch.done()

    asyncio.run(scenario())


def test_selfcheck_criteria_matches_tick(monkeypatch):
    """判定口径必须与 _tick 一致：刚重建（last_start 新鲜）的目标即使无心跳也不报，避免假告警/误杀。"""
    errs = _capture_errors(monkeypatch)

    async def scenario():
        s = TaskSupervisor()
        s.register("memory_maintenance", _run_forever, stall_sec=10)
        s.start()
        now = time.monotonic()
        s._targets["memory_maintenance"].last_beat = now - 100  # 首轮本来就该跑很久
        s._targets["memory_maintenance"].last_start = now - 1   # 但刚拉起
        assert await s.selfcheck_once() == []
        assert errs == []
        await s.stop()

    asyncio.run(scenario())


def test_snapshot_exposes_heartbeat_iso_without_mutating():
    """snapshot() 只读：目标名 → 最后心跳 ISO8601(UTC naive)，且不改任何 heartbeat/重建状态。"""
    async def scenario():
        s = TaskSupervisor()
        s.register("scheduler", _run_forever, stall_sec=180)
        s.start()
        beat = s._targets["scheduler"].last_beat
        task = s._targets["scheduler"].task

        snap = s.snapshot()
        assert set(snap) == {"scheduler"}
        iso = snap["scheduler"]
        assert isinstance(iso, str) and iso, iso
        back = datetime.fromisoformat(iso)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        assert back.tzinfo is None, "口径与库内时间一致：UTC naive"
        assert abs((now_utc - back).total_seconds()) < 5, iso

        # 纯读：状态一字不变
        assert s._targets["scheduler"].last_beat == beat
        assert s._targets["scheduler"].task is task
        assert s._targets["scheduler"].restarts == 0
        lv = s.liveness()["scheduler"]
        assert lv["last_heartbeat"] == iso or abs(
            (datetime.fromisoformat(lv["last_heartbeat"]) - back).total_seconds()) < 2

        await s.stop()

    asyncio.run(scenario())
