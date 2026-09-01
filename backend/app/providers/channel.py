"""ChannelPort 渠道端口（X5 渠道外迁，2026-09-01）。

渠道（外部社交平台：短视频/即时通讯等）以插件扩展形式接入内核；内核不持有任何具体
渠道的名字与业务知识，只认本端口契约 + 注册表元数据：

- 注册：`register_channel(name, port, meta, source)` —— 薄封装 register_provider(kind="channel")；
  插件经 sdk.register_channel（source=插件名，仅 main.py 加载期）；同源重载（sync_plugins_db
  重扫 / 测试重复加载）按「同源替换」处理，异源重名抛错；
- port：实现 ChannelPort 协议的对象（async 方法集合；payload 为渠道自解释 dict，端口层不解释
  渠道内部语义）；注册表 factory 槽位即 port 对象（resolve 不做二次调用）；
- meta 契约（渠道只上报，内核裁决）：
  {"label": 展示名,
   "plugin": 绑定插件名（plugins 表 name；绑定校验/权限 scope 经它关联）,
   "permissions": [渠道自有权限名（约定 <渠道名>_ 前缀，manifest 校验放行）],
   "scope": 工具权限 scope 值, "scope_label": str, "scope_desc": str,
   "risk_level": "high" | "medium"（插件 action 默认风险档）,
   "binding": {...}}（绑定策略上报；「每独立主账号全局唯一绑定」裁决在内核）};
- 查询：resolve_channel(name) / channel_meta(name) / list_channels() /
  channel_for_plugin(plugin_name)（api 绑定校验与权限 scope 的通用入口）；
- 内核保留裁决面：家庭内全局唯一绑定（api/plugins._validate_channel_binding）、
  平台档案白名单（api/platform_profiles 仅注册渠道可建档）。

来源启用过滤复用 X3 的 _source_enabled（插件停用 → resolve 不可见）；provider_select hook
预留同 X3。模型归属：渠道自有 ORM 模型定义在扩展包内、经插件 main.py 加载期 import 注册进
Base.metadata（早于 init_db create_all 的渠道插件预加载由 main.py lifespan 负责）。
"""
from __future__ import annotations

from typing import Protocol

_CHANNEL_KIND = "channel"


class ChannelPort(Protocol):
    """渠道端口契约（X5）：发布 / 拉评论 / 回评 / 媒体上传 / 账号绑定状态。

    payload 为渠道自解释 dict（校验与语义由渠道实现负责）；未支持的操作可抛
    NotImplementedError（内核侧按失败静默降级，不拖垮主链路）。
    """

    async def publish(self, payload: dict) -> dict:  # 发布内容（进入渠道自身审批/队列语义）
        ...

    async def pull_comments(self, payload: dict) -> list[dict]:  # 拉取评论（渠道侧状态/采集）
        ...

    async def reply_comment(self, payload: dict) -> dict:  # 回评（进入渠道审批/队列语义）
        ...

    async def upload_media(self, payload: dict) -> dict:  # 媒体上传（返回渠道可引用的路径）
        ...

    async def binding_status(self, payload: dict) -> dict:  # 账号绑定状态
        ...


def _as_factory(port):
    """把 port 对象包装成注册表要求的零参可调用（factory 槽位存取回函数，resolve 时调用取回 port）"""
    return lambda: port


def register_channel(name: str, port, meta: dict | None = None, source: str = "builtin") -> None:
    """注册渠道扩展：同源重载=替换，异源重名抛错（复用 provider 注册表的校验与来源过滤）"""
    from app.providers import registry as _reg
    key = (_CHANNEL_KIND, name)
    ent = _reg._ENTRIES.get(key)
    full_meta = dict(meta or {})
    if ent is not None:
        if ent.get("source") == source:
            _reg._ENTRIES[key] = {"factory": _as_factory(port), "meta": full_meta, "source": source}
            return
        raise ValueError(f"channel already registered by another source: {name}")
    _reg.register_provider(_CHANNEL_KIND, name, _as_factory(port), meta=full_meta, source=source)
    # X3 注册口会把 meta 归一化为 {label, description}——渠道 meta 契约字段需全量保留
    _reg._ENTRIES[key]["meta"] = full_meta


def _channel_entries() -> list[tuple[str, dict]]:
    from app.providers import registry as _reg
    return [(name, ent) for (k, name), ent in _reg._ENTRIES.items() if k == _CHANNEL_KIND]


def resolve_channel(name: str):
    """取渠道 port（来源启用过滤：插件停用 → None）；无命中返回 None"""
    from app.providers.registry import resolve_provider
    hit = resolve_provider(_CHANNEL_KIND, {"provider": name})
    if hit is None:
        return None
    factory = hit[1]
    return factory() if callable(factory) else factory


def channel_meta(name: str) -> dict:
    """渠道元数据（不存在返回空 dict）"""
    for name_, ent in _channel_entries():
        if name_ == name:
            return dict(ent.get("meta") or {})
    return {}


def list_channels() -> list[dict]:
    """全部已注册渠道（含停用来源，调用方按需过滤；供配置页/平台档案白名单）"""
    out = []
    for name, ent in _channel_entries():
        meta = dict(ent.get("meta") or {})
        out.append({"name": name, "label": meta.get("label", name),
                    "source": ent.get("source"), "meta": meta})
    return out


def channel_for_plugin(plugin_name: str) -> tuple[str, dict] | None:
    """插件名 → (渠道名, meta)；未注册渠道返回 None（绑定校验/权限 scope 的通用入口）"""
    for name, ent in _channel_entries():
        meta = ent.get("meta") or {}
        if meta.get("plugin") == plugin_name or ent.get("source") == plugin_name:
            return name, meta
    return None
