"""设备能力契约（X7-M0 骨架 → M4a 行动能力）。

把「设备能力」定义成一份可注册、可授权、可被内核消费的契约：能力清单是唯一权威源，
读侧端口（``app.device.port``）与插件权限名（``plugins.manifest``）都从它取，不另立第二份。

里程碑状态（M0 只声明、不接线，避免插件现在就能声明一个还调不到的能力）：
- 字段级结构化解析：**M1 已落地** —— ``read_capability.value.structured``
  （该条快照的 ``payload_json`` 解析出的字段级对象；无载荷 / 非对象 / 坏 JSON 时回落
  ``raw_text``，即 ``structured`` 为 ``None``）；
- 能力级权限名（``device:<id>:read``）并入 ``plugins.manifest.VALID_PERMISSIONS``：**M3 已落地**
  （manifest 经局部 import 动态取 :func:`capability_permissions`，清单只有这一份权威源）；
  插件侧的「逐条同意 + fail-closed」判定见 ``plugins.registry.has_capability_permission``。
- 行动类能力（``kind="act"``，权限名 ``device:<id>:write``）：**M4a 已登记**——本模块只声明契约
  （清单 + 意图字段 schema + 确认策略），裁决/闸门/队列在 ``app.device.actions``，
  **执行体留 M4b**（本批零真实执行）。只读端口 :func:`app.device.port.read_capability`
  不消费行动类能力（其 ``sources`` 为空，读它恒 ``empty``）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

# requires（系统前置）合法取值：无障碍 / 通知监听 / 使用统计访问 / Shizuku / 无需前置
VALID_REQUIRES = ("accessibility", "notification_listener", "usage_access", "shizuku", "none")
# read = 只读能力（取快照）；act = 行动能力（提交意图，M4a 起只到裁决侧）
VALID_KINDS = ("read", "act")
# 权限名后缀：只读给 :read，行动给 :write（读写权限分离——同意读不等于可以写）
PERM_SUFFIXES = MappingProxyType({"read": "read", "act": "write"})
# 确认策略：none = 无需确认（只读）；first_per_type = 每类动作首次确认、之后放行（决策④，2026-09-23）
VALID_CONFIRMATIONS = ("none", "first_per_type")


@dataclass
class CapabilitySpec:
    """一条设备能力的声明（只读或行动）。"""

    id: str
    kind: str                                   # VALID_KINDS：read / act
    permission: str                             # 能力级权限名，如 device:foreground_app:read
    requires: str                               # 系统前置，取值见 VALID_REQUIRES
    sensitive: bool                             # 通知正文 / 位置 / 行动类敏感能力为 True
    sources: tuple[str, ...]                    # 该能力对应哪些 phone_snapshots.source（行动类为空）
    schema: dict = field(default_factory=dict)  # 字段名 → 类型字符串（read: raw_text；act: 意图字段）
    confirmation: str = "none"                  # 确认策略，取值见 VALID_CONFIRMATIONS


def _perm(capability_id: str, kind: str) -> str:
    return f"device:{capability_id}:{PERM_SUFFIXES[kind]}"


def _read(cid: str, requires: str, sensitive: bool, sources: tuple[str, ...]) -> CapabilitySpec:
    return CapabilitySpec(
        id=cid, kind="read", permission=_perm(cid, "read"), requires=requires,
        sensitive=sensitive, sources=sources, schema={"raw_text": "str"},
    )


def _act(cid: str, requires: str, schema: dict) -> CapabilitySpec:
    """行动类能力：默认敏感（sensitive=True）、无读取来源、每类首次确认（决策④）。

    ``schema`` 即「该能力接受哪些意图字段」——``app.device.actions`` 按它逐字段对账
    （不在 schema 里的字段即「本能力不认」），故清单只有一份，勿在裁决侧另写字段表。
    """
    return CapabilitySpec(
        id=cid, kind="act", permission=_perm(cid, "act"), requires=requires,
        sensitive=True, sources=(), schema=schema, confirmation="first_per_type",
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
    # ── M4a 行动能力（决策②：首批只开这三条；id 固定，勿改名）──
    # schema 里的键＝该能力接受的意图字段（target_app 恒必填，dry_run 为干跑开关，
    # 其余按能力而定）；裁决侧按这份 schema 逐字段对账，故此处是唯一字段清单。
    _act("action_open_app", "accessibility", {"target_app": "str", "dry_run": "bool"}),
    _act("action_tap", "accessibility",
         {"target_app": "str", "by": "str", "query": "str", "dry_run": "bool"}),
    _act("action_set_text", "accessibility",
         {"target_app": "str", "text": "str", "dry_run": "bool"}),
)

CAPABILITY_IDS: tuple[str, ...] = tuple(spec.id for spec in _CAP_LIST)

# 按 id 的映射（不可变视图）
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

    M4a（2026-09-23）：行动类能力在此自动带上 ``device:<id>:write``（后缀按 ``kind`` 分流，
    见 :data:`PERM_SUFFIXES`）——读/写是两条不同权限，同意读不会连带放开行动。
    """
    return tuple(spec.permission for spec in _CAP_LIST)
