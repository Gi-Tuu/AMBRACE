"""AMBRACE 3.10 —— arbiter 事件源（TriggerSource）包。

- base.py       ：TriggerSource Protocol / TriggerItem / SourceContext；
- registry.py   ：register_source 装饰器 + all_sources() + 按名取源/测试注入；
- <源>.py        ：每个 arbiter 事件源一个 TriggerSource 实现类。

下方「导入顺序」即 all_sources() 的注册顺序 = arbiter run_tick 合并候选的顺序，勿改动。
"""
from .base import SourceContext, TriggerItem, TriggerSource, to_item_dict
from .registry import register_source, all_sources, get_source, set_source, unregister_source, reset_sources

# ── 注册顺序：与 run_tick 合并候选顺序逐项对应（勿增删、勿改序）──
from . import (
    timer,          # type=timer
    special,        # birthday/holiday/anniversary
    rhythm,         # 随机节律（greeting/proactive_chat/goodnight/status_update/moment_*）
    state_trigger,  # state_trigger
    motivation,     # motivation
    memory_review,  # memory_review + memory_review_contextual
    emotion_care,   # emotion_care
    pet_care,       # pet_remind / ai_care / ai_adopt / pet_visit
    ai_social,      # ai_social
    group_active,   # group_active
    plugin,         # plugin
    unfinished_topic,  # unfinished_topic
    life_regression,   # life_regression
)

# 源模块以「注册副作用」引入，同时列入 __all__ 表明其为包内公开子模块（ruff F401 友好）
__all__ = [
    "SourceContext",
    "TriggerItem",
    "TriggerSource",
    "to_item_dict",
    "register_source",
    "all_sources",
    "get_source",
    "set_source",
    "unregister_source",
    "reset_sources",
    # 事件源子模块（按注册顺序）
    "timer",
    "special",
    "rhythm",
    "state_trigger",
    "motivation",
    "memory_review",
    "emotion_care",
    "pet_care",
    "ai_social",
    "group_active",
    "plugin",
    "unfinished_topic",
    "life_regression",
]
