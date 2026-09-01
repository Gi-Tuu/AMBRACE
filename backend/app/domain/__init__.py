"""domain 层（F 线重构目标骨架，F2+ 逐批迁入）：核心引擎——纯业务，不感知 FastAPI/HTTP。

职责边界（强约束，写进各模块 docstring）：
- cognition：在线认知（用户消息驱动，一次请求→一次回复，同步低延迟）
- proactivity：离线主动决策（定时器驱动，决定"AI 要不要主动做点什么"）
- memory / relationship / emotion / games / weave / life：领域引擎
依赖只允许向内：api → application → domain → infra → shared(models/schemas/utils)。
约定：每域一个 __init__ 门面只导出稳定接口；跨域联动走 events 总线，不互相直 import。
详见 docs/执行方案_全项目重构_20260831.md。
"""
