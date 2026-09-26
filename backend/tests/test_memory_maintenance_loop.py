# -*- coding: utf-8 -*-
"""长周期记忆维护「独立循环」回归（2026-09-26）。

背景（实测事故，理由写在 scheduler.memory_maintenance_loop 的说明里）：维护原本挂在主调度循环的
tick 计数分支上，而主循环发主动消息时会在循环内 await 多次 LLM 调用、单轮涨到分钟级 ⇒ 超 180 秒
被监督者判 stalled 取消重建，重建又把所有 tick 计数归零 ⇒ 长周期任务在忙时段严重延迟甚至不跑。
本文件锁住迁移后的四件事：按间隔问、单次异常不掀循环、心跳名与登记名一致、主循环旧分支已移除
（顺带锁住「身份画像提炼没被当成旧分支一起删掉」—— 它历史上与维护挤在同一个计数分支里）。
"""
import asyncio
import inspect
import re

import pytest

import app.utils.supervisor as supervisor_mod
from app.memory import maintenance_schedule as ms
from app.scheduling import scheduler
from app.utils.supervisor import TaskSupervisor


@pytest.fixture()
def slept_seconds(monkeypatch):
    """asyncio.sleep → 记录名义时长并立刻返回；同时把 _running 置起（测试不真等 5 分钟）。"""
    real_sleep = asyncio.sleep
    record: list[float] = []

    async def fake_sleep(sec, *args, **kwargs):
        record.append(sec)
        await real_sleep(0)

    monkeypatch.setattr(scheduler.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(scheduler, "_running", True)
    return record


def _drive():
    async def go():
        await asyncio.wait_for(scheduler.memory_maintenance_loop(), timeout=10)
    asyncio.run(go())


def _counting_run_if_due(seen, stop_after, *, fail_times=0):
    """替身：记录被问到的 reason；前 fail_times 次抛异常；问到第 stop_after 次时收摊。"""
    async def fake(*, reason="tick", **kwargs):
        idx = len(seen)
        seen.append(reason)
        if idx >= stop_after - 1:
            scheduler._running = False
        if idx < fail_times:
            raise RuntimeError("boom")
        return False
    return fake


def test_loop_asks_run_if_due_every_interval(slept_seconds, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ms, "run_if_due", _counting_run_if_due(seen, 3))

    _drive()

    assert seen == ["loop", "loop", "loop"]              # 每轮都问，reason=loop
    assert slept_seconds == [300, 300, 300]               # 间隔 300 秒，外面不再加任何判断


def test_single_failure_does_not_break_loop(slept_seconds, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ms, "run_if_due",
                        _counting_run_if_due(seen, 3, fail_times=1))

    _drive()                                              # 不抛出来 = 异常已在循环内隔离成 WARNING

    assert len(seen) == 3                                 # 第一次失败后仍按间隔继续问


def test_loop_heartbeats_under_the_registered_name(slept_seconds, monkeypatch):
    class SpySupervisor(TaskSupervisor):
        def __init__(self):
            super().__init__()
            self.beats: list[str] = []

        def heartbeat(self, name):
            self.beats.append(name)

    spy = SpySupervisor()
    registered = re.findall(r'supervisor\.register\("([^"]+)"', inspect.getsource(scheduler.start))
    assert "memory_maintenance" in registered              # 新循环确实纳入监督
    spy.register("memory_maintenance", scheduler.memory_maintenance_loop,
                 stall_sec=scheduler.MEMORY_MAINTENANCE_STALL_SEC)
    monkeypatch.setattr(supervisor_mod, "supervisor", spy)
    seen: list[str] = []
    monkeypatch.setattr(ms, "run_if_due", _counting_run_if_due(seen, 2))

    _drive()

    # 心跳名写错时 supervisor 只会静默不更新（等于没有可观测性），故按名字逐轮断言
    assert spy.beats == ["memory_maintenance", "memory_maintenance"]


def test_stall_threshold_covers_one_full_round():
    # 阈值要罩住「间隔 + 单轮维护最坏耗时」，否则新循环自己会被误判 stalled 反复重建
    assert scheduler.MEMORY_MAINTENANCE_INTERVAL == 300
    assert scheduler.MEMORY_MAINTENANCE_STALL_SEC >= 6 * scheduler.MEMORY_MAINTENANCE_INTERVAL


def test_main_loop_no_longer_mounts_maintenance():
    src = inspect.getsource(scheduler.scheduler_loop)
    # \b 是必要的：主循环里还有 state_decay_counter（状态八维回落），子串匹配会误报
    assert not re.search(r"\bdecay_counter\b", src)        # 旧计数分支连同计数变量清干净
    # 2026-09-26 批次 PT 起，主循环里确实出现了 reason="tick"（文件清理 / 约定清扫改走
    # periodic_state 的持久化台账），所以「没有第二个挂载点」改为**精确钉住维护自己的挂载次数**：
    # `_run_maintenance(` 只允许出现在「启动补跑」那一处。
    assert src.count("_run_maintenance(") == 1, "长周期维护只剩「启动补跑」一个挂载点"
    assert 'reason="startup"' in src                       # 启动补跑仍在
    assert "memory_summary" in src                         # 身份画像提炼仍在（行为等价）
    assert "identity_counter >= 300" in src
