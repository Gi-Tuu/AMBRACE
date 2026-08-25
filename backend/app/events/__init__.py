"""统一事件总线（2026-08-14，演进规划 v2 Phase A）

轻量进程内 pub/sub，替代散点式硬编码联动；先接 3 个发布点：
- life.activity_completed（Life 活动完成）
- life.moment_published（朋友圈发布成功）
- memory.written（记忆写入成功）
订阅者注册：main.py lifespan 调 register_builtin_handlers()。
"""
from app.events.bus import EventBus, event_bus, publish, subscribe
from app.events.types import EventType
from app.events.handlers import register_builtin_handlers

__all__ = ["EventBus", "event_bus", "publish", "subscribe", "EventType", "register_builtin_handlers"]
