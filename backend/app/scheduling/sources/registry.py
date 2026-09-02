"""AMBRACE 3.10 —— arbiter 触发源注册表。

- @register_source 装饰器：把一个 TriggerSource 类（或实例）注册为某源；
- all_sources()：按注册顺序返回全部源（顺序 = run_tick 合并候选的顺序，勿改动）；
- get_source(name) / set_source(name, src)：按名取源 / 测试注入。
"""
from __future__ import annotations

from typing import Any

from .base import TriggerSource

# name -> TriggerSource 实例（dict 有序 = 注册顺序 = run_tick 合并顺序）
_SOURCES: dict[str, TriggerSource] = {}


def register_source(cls=None, *, name: str | None = None):
    """注册一个触发源。用法：

        @register_source              # 类自带 name 属性
        @register_source(name="x")    # 显式指定 name

    注册的是「实例」（由类实例化得到）；重复注册同名源时覆盖（幂等）。
    """
    def _wrap(target):
        inst = target() if isinstance(target, type) else target
        src_name = name or getattr(inst, "name", None)
        if not src_name:
            raise ValueError("TriggerSource 必须定义 name 属性，或经 @register_source(name=...) 指定")
        _SOURCES[src_name] = inst  # 覆盖注册（模块重载/测试重复导入幂等）
        return target

    if cls is None:
        return _wrap
    return _wrap(cls)


def all_sources() -> list[TriggerSource]:
    """按注册顺序返回全部触发源（即 arbiter run_tick 合并候选的顺序）。"""
    return list(_SOURCES.values())


def get_source(name: str) -> TriggerSource:
    """按名取源；未知源抛 KeyError。"""
    return _SOURCES[name]


def set_source(name: str, source: Any) -> None:
    """按名注入 / 替换源（测试可测性）。"""
    _SOURCES[name] = source


def unregister_source(name: str) -> None:
    """按名移除源（测试隔离用）。"""
    _SOURCES.pop(name, None)


def reset_sources() -> None:
    """清空注册表（全量隔离用；仅在需要重建全部源注册的场景使用）。"""
    _SOURCES.clear()
