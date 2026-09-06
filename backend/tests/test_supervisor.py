# -*- coding: utf-8 -*-
"""常驻任务监督器（app/utils/supervisor.py）单测：崩溃重建 / 主动 stop 不重建 / liveness 停滞判定。"""
import asyncio
import time

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
