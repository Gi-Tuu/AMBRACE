"""执行器上下文 — GateBundle（A20 批 3a，方案 §2.3）

**为什么要有这个 dataclass，而不是让 guards 直接 import 闸函数**：
tests/ 里 197 处 ``monkeypatch.setattr(arbiter, "<name>", stub)`` 依赖的是
「调用方在 arbiter 命名空间里解析裸名」。guards 若直接 import，调用点就搬到新模块，
arbiter 上的桩全部失效——测试不会红，而是**静默绕过打桩去查真库**（比红更糟）。

Bundle 的字段由 arbiter 的 ``_gates()`` **在调用时刻现取**（裸名解析走 arbiter 全局），
所以打桩永远生效。字段名与 arbiter 里的符号一一对应，便于机械核对。

约定：本 dataclass 只装「闸函数」与「被 tests/ 打桩的依赖」（会话工厂），不装常量/配置
（常量由执行器直接 import，无人打桩）；批 3b / 批 4 会继续加字段，不要预埋用不到的字段。
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class GateBundle:
    """``_execute`` 前置闸与执行器所需的全部现取依赖（由 arbiter 装配）。"""

    is_dnd_now: Callable[..., Awaitable[bool]]
    has_user_said_sleep: Callable[..., Awaitable[bool]]
    is_user_active: Callable[..., Awaitable[bool]]
    hourly_active: Callable[..., Awaitable[int]]
    pacing_gate: Callable[..., Awaitable[str | None]]
    mark_gate: Callable[..., None]
    # A20 批 3b：async 上下文管理器；arbiter 的 _gates() 现取 async_session_factory
    # （tests/ 有 13 处 setattr(arbiter, "async_session_factory", …)，执行器不得自行 import）
    session_factory: Callable[[], object]
    # A20 批 4b：应用日界（想念每日配额）；_gates() 现取 arbiter 的裸名 app_day_start_utc
    # （tests/test_daily_window_alignment 按 setattr(arbiter, "app_day_start_utc", …) 断言口径同源，
    #   执行器 import 到本地命名空间会让该桩静默失效）
    app_day_start: Callable[[], object]


def agent_flag_on(key: str) -> bool:
    """读 AGENT_FLAGS（失败 fail-safe 返回 False=走旧路径）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get(key, False))
    except Exception:
        return False
