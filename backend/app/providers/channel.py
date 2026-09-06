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
            # 同源重载=整体替换（factory/meta 刷新），但在场 binding_hooks 保留——
            # 渠道插件 main.py 重扫时 register_channel 先跑、set_channel_binding_hooks 后跑，
            # 保留旧 hook 保证中间态（写 hook 前被后续查询误判为无联动面）不闪断。
            _reg._ENTRIES[key] = {"factory": _as_factory(port), "meta": full_meta, "source": source,
                                  "binding_hooks": ent.get("binding_hooks") or {}}
            return
        raise ValueError(f"channel already registered by another source: {name}")
    _reg.register_provider(_CHANNEL_KIND, name, _as_factory(port), meta=full_meta, source=source)
    # X3 注册口会把 meta 归一化为 {label, description}——渠道 meta 契约字段需全量保留
    _reg._ENTRIES[key]["meta"] = full_meta
    _reg._ENTRIES[key]["binding_hooks"] = {}


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


# ── 渠道级「绑定联动」回调（插件自洽：内核 channels API 不 import 各渠道内部实现）──
#
# 背景（2026-09-06 解绑同步修复）：App 渠道卡解绑（DELETE /api/v1/channels/{channel}/bindings/{bot}）
# 删 channel_bindings 行后，须联动渠道自有绑定表（wechat_ilink 的 wechat_ilink_bindings），
# 把 (tenant, bot) 行停用（enabled=0、token 清空、保留行历史——对齐插件 _clear_binding 语义）。
# 反方向：PUT 绑定/换绑后也要回写渠道自有行（重绑不重扫）。回调按渠道名注册在渠道注册条目上
# （随渠道注册/注销生命周期），channel_bindings API 经 invoke_channel_binding_hook 调用。
#
# 回调签名（async）：
# - "on_binding_saved":   (db, tenant_id, bot_account_id, character_id, *, user_id=None)
#                         绑定/换绑决策成功后再写渠道自有行；目标不可绑（他租户/未登录/任意 id）
#                         抛 HTTPException(404) → 调用方不 commit，整事务回滚。
# - "on_binding_removed": (db, tenant_id, bot_account_id) -> bool
#                         解绑后停用渠道自有行；幂等，无命中行返回 False（不报错）。
def set_channel_binding_hooks(name: str, hooks: dict) -> None:
    """设置渠道级「绑定联动」回调（插件 main.py 加载期经 sdk 调用；内核不 import 插件实现）。

    hooks: {"hook_name": async handler, ...}；覆盖式写入（同渠道同名 hook 后写胜）。
    """
    from app.providers import registry as _reg
    key = (_CHANNEL_KIND, str(name))
    ent = _reg._ENTRIES.get(key)
    if ent is None:
        raise ValueError(f"channel not registered: {name}")
    ent["binding_hooks"] = dict(hooks or {})


def _channel_binding_hooks(name: str) -> dict:
    """取渠道的「绑定联动」回调表（无注册返回空 dict）。"""
    from app.providers import registry as _reg
    key = (_CHANNEL_KIND, str(name))
    ent = _reg._ENTRIES.get(key)
    return (ent or {}).get("binding_hooks") or {}


async def invoke_channel_binding_hook(name: str, hook_name: str, *args, **kwargs):
    """内核调用渠道的「绑定联动」回调。无注册回调返回 None（无联动面=原行为）。

    回调内部可能抛 HTTPException（如可用性 404），由调用方按渠道语义透出；
    回调返回其 handler 的返回值（供调用方判断是否实际联动）。
    """
    handler = _channel_binding_hooks(str(name)).get(hook_name)
    if handler is None:
        return None
    return await handler(*args, **kwargs)
