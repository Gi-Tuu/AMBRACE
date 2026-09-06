# -*- coding: utf-8 -*-
"""进程内关键常驻任务监督器（AMBRACE 3.4.x 增量）。

职责边界（与 readiness 区分）：
- readiness 只回答「启动期组件是否就绪」；本模块回答「运行期关键循环是否还活着、还在不在前进」。
- 不替代业务协程自身的 try/except（主回复链路「失败静默」铁律不变）；只在「整个常驻 task
  意外终结 / 长时间无心跳」时兜底重建，并把状态暴露给 /liveness。

设计要点：
- register(name, factory, stall_sec)：登记一个「返回协程」的工厂（幂等），不立即启动。
- start()：统一拉起全部登记目标 + 监督循环。
- heartbeat(name)：被业务循环每轮调用，标记「我还在前进」——done 监督管不到卡死，靠它。
- stop()：置 stopped 标记并取消全部目标（主动停，监督循环据此「不误拉」）；不清空登记。
- liveness()：暴露每个目标 {alive, stalled, seconds_since_heartbeat, restarts, last_error}，
  供 /api/v1/system/liveness 消费。
"""
import asyncio
import time
from typing import Awaitable, Callable

from app.utils.logger import get_logger

_log = get_logger("supervisor")

# 认为「心跳停滞」的默认阈值（秒）：scheduler TICK=30s，给到 5 倍冗余
DEFAULT_STALL_SEC = 180
# 重建退避：1,2,4,…封顶 60s
_BACKOFF = [1, 2, 4, 8, 16, 32, 60]
# 监督巡检间隔（秒）
_WATCH_INTERVAL = 15


class _Target:
    __slots__ = (
        "factory", "task", "restarts", "last_beat", "last_start",
        "stopped", "stall_sec", "last_err",
    )

    def __init__(self, factory: Callable[[], Awaitable], stall_sec: int):
        self.factory = factory
        self.task: asyncio.Task | None = None
        self.restarts = 0
        self.last_beat = time.monotonic()
        self.last_start = 0.0
        self.stopped = False
        self.stall_sec = stall_sec
        self.last_err = ""


class TaskSupervisor:
    """进程内关键常驻任务监督者（单例 supervisor 全局复用）。"""

    def __init__(self) -> None:
        self._targets: dict[str, _Target] = {}
        self._watch_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ 登记/心跳

    def register(self, name: str, factory: Callable[[], Awaitable],
                 *, stall_sec: int = DEFAULT_STALL_SEC) -> None:
        """登记一个常驻任务工厂（不立即启动，start() 时统一拉起）。幂等。"""
        if name not in self._targets:
            self._targets[name] = _Target(factory, stall_sec)

    def heartbeat(self, name: str) -> None:
        """被业务循环每轮调用，标记「我还在前进」。"""
        t = self._targets.get(name)
        if t is not None:
            t.last_beat = time.monotonic()

    # ------------------------------------------------------------------ 拉起

    def _spawn_one(self, name: str, t: _Target) -> None:
        if t.task is not None and not t.task.done():
            return

        async def _guarded():
            try:
                await t.factory()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # 业务循环本该自隔离；这里兜住「漏网」异常，避免监督者误判节奏
                t.last_err = repr(e)
                _log.error("supervised task %s crashed: %s", name, e)

        t.task = asyncio.ensure_future(_guarded())
        t.last_start = time.monotonic()
        t.last_beat = time.monotonic()

        def _on_done(done: asyncio.Task):
            if not done.cancelled() and done.exception() is not None:
                t.last_err = repr(done.exception())

        t.task.add_done_callback(_on_done)

    # ------------------------------------------------------------------ 监督循环

    async def _tick(self) -> None:
        """单次监督巡检（无 sleep；供 _watch 循环与确定性单测复用）。"""
        now = time.monotonic()
        for name, t in self._targets.items():
            if t.stopped:
                continue
            # 1) 任务意外终结 → 退避重建
            if t.task is None or t.task.done():
                wait = _BACKOFF[min(t.restarts, len(_BACKOFF) - 1)]
                if now - t.last_start >= wait:
                    t.restarts += 1
                    _log.warning("supervisor restart target=%s count=%d", name, t.restarts)
                    self._spawn_one(name, t)
                continue
            # 2) 任务没 done 但长时间无心跳（卡死）→ 取消后由下一轮重建
            if t.stall_sec and now - t.last_beat > t.stall_sec and now - t.last_start > t.stall_sec:
                _log.error("supervisor stall detected target=%s, cancelling for restart", name)
                t.last_err = "stalled: no heartbeat > %ds" % t.stall_sec
                try:
                    t.task.cancel()
                except Exception:
                    pass

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(_WATCH_INTERVAL)
            await self._tick()

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """拉起全部登记目标 + 启动监督循环（幂等：已存活的不重复拉起）。"""
        for name, t in self._targets.items():
            t.stopped = False
            self._spawn_one(name, t)
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.ensure_future(self._watch())

    async def stop(self) -> None:
        """主动关停：置 stopped 标记（监督循环据此不重建）并取消全部目标与监督循环。"""
        for t in self._targets.values():
            t.stopped = True
            if t.task is not None and not t.task.done():
                t.task.cancel()
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._watch_task = None

    # ------------------------------------------------------------------ 可观测性

    def liveness(self) -> dict:
        now = time.monotonic()
        out: dict = {}
        for name, t in self._targets.items():
            alive = t.task is not None and not t.task.done()
            since_beat = int(now - t.last_beat) if t.last_beat else None
            stalled = alive and t.stall_sec and since_beat is not None and since_beat > t.stall_sec
            out[name] = {
                "alive": alive,
                "stalled": bool(stalled),
                "seconds_since_heartbeat": since_beat,
                "restarts": t.restarts,
                "last_error": t.last_err or None,
            }
        return out


# 模块级单例（scheduler / liveness 端点 / 测试全局复用）
supervisor = TaskSupervisor()
