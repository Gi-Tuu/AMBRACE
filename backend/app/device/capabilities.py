"""设备能力契约骨架（X7-M0）。

把「设备能力」定义成一份可注册、可授权、可被内核消费的只读契约：本批只登记能力清单
与端口读取，不改变任何现有行为。

里程碑状态（M0 只声明、不接线，避免插件现在就能声明一个还调不到的能力）：
- 字段级结构化解析：**M1 已落地** —— ``read_capability.value.structured``
  （该条快照的 ``payload_json`` 解析出的字段级对象；无载荷 / 非对象 / 坏 JSON 时回落
  ``raw_text``，即 ``structured`` 为 ``None``）；
- 能力级权限名（``device:<id>:read``）并入 ``plugins.manifest.VALID_PERMISSIONS``：**M3 已落地**
  （manifest 经局部 import 动态取 :func:`capability_permissions`，清单只有这一份权威源）；
  插件侧的「逐条同意 + fail-closed」判定见 ``plugins.registry.has_capability_permission``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

# requires（系统前置）合法取值：无障碍 / 通知监听 / 使用统计访问 / Shizuku / 无需前置
VALID_REQUIRES = ("accessibility", "notification_listener", "usage_access", "shizuku", "none")
# 本批能力一律只读
VALID_KINDS = ("read",)


@dataclass
class CapabilitySpec:
    """一条只读设备能力的声明。"""

    id: str
    kind: str                                   # 本批恒 "read"
    permission: str                             # 能力级权限名，如 device:foreground_app:read
    requires: str                               # 系统前置，取值见 VALID_REQUIRES
    sensitive: bool                             # 通知正文 / 位置等敏感能力为 True
    sources: tuple[str, ...]                    # 该能力对应哪些 phone_snapshots.source
    schema: dict = field(default_factory=dict)  # 字段名 → 类型字符串；M0 先声明 raw_text


def _perm(capability_id: str) -> str:
    return f"device:{capability_id}:read"


def _read(cid: str, requires: str, sensitive: bool, sources: tuple[str, ...]) -> CapabilitySpec:
    return CapabilitySpec(
        id=cid, kind="read", permission=_perm(cid), requires=requires,
        sensitive=sensitive, sources=sources, schema={"raw_text": "str"},
    )


# 8 条只读能力（id 固定，勿改名）。battery/network/dnd/location 当前无采集通道，
# sources 先声明同名约定 source（read_capability 对它们恒 empty）；载荷解析自 M1 起对所有
# source 统一生效（有 payload_json 就出 structured），这四项只是暂无数据。
_CAP_LIST: tuple[CapabilitySpec, ...] = (
    _read("foreground_app", "accessibility", False, ("accessibility", "shizuku_system")),
    _read("screen_state", "accessibility", False, ("accessibility",)),
    _read("battery", "shizuku", False, ("battery",)),
    _read("network", "shizuku", False, ("network",)),
    _read("dnd", "none", False, ("dnd",)),
    _read("notifications", "notification_listener", True, ("notification",)),
    _read("usage_stats", "usage_access", False, ("usage_stats",)),
    _read("location", "none", True, ("location",)),
)

CAPABILITY_IDS: tuple[str, ...] = tuple(spec.id for spec in _CAP_LIST)

# 按 id 的只读映射
CAPABILITIES = MappingProxyType({spec.id: spec for spec in _CAP_LIST})


def get_capability(capability_id: str) -> CapabilitySpec | None:
    """按 id 取能力声明；未知 id 返回 None。"""
    return CAPABILITIES.get(capability_id)


def capability_permissions() -> tuple[str, ...]:
    """全部能力级权限名的**权威视图**（与 :data:`CAPABILITIES` 同源 ``_CAP_LIST``，一一对应不漂移）。

    M3（2026-09-22）：``plugins.manifest.VALID_PERMISSIONS`` 经局部 import 动态并入本清单——
    插件可声明 ``device:<capability>:read`` 并通过 manifest 校验，安装期随之逐条同意；
    调用期判定见 ``plugins.registry.has_capability_permission``。本函数是「能力 ↔ 权限名」
    的唯一口径，任何一处新增能力都自动同时生效，勿在别处再写一份清单。
    """
    return tuple(spec.permission for spec in _CAP_LIST)
