"""Provider 注册表（X3 Provider 端口，2026-08-31）。

内置实现与插件扩展包走同一注册入口（镜像 games/registry.py 的 X1 模式，消除硬编码双轨）：
- 内核启动时 register_provider(...) 注册内置实现（source="builtin"）：
  kind=llm → openai_compatible（app.agent.llm_client.get_llm_client 晚绑定）；
  kind=tts → dashscope（app.services.tts_service._synth_dashscope_sync 晚绑定）；
  其余 kind（asr/vision/image/push）本批只留枚举槽位，内置实现按方案渐进迁入；
- 插件 main.py 加载期经 sdk.register_provider(...) 注册（source=插件名）；
- resolve_provider 解析规则：配置的 provider 字段与注册名精确匹配优先，否则内置兜底；
  来源启用过滤（插件停用 → 不可选、list 隐藏）；provider_select hook（选择/兜底/健康检查
  走插件投票）为预留 hook，落地前本函数即唯一选择点；
- 插件目录被移除后 sync_plugins_db 经 unregister_providers_not_in 清理残留注册。

factory 契约：factory(config: dict) -> provider 对象（每次请求调用；密钥只经运行时 config
下发——注册表不存密钥，密钥不进扩展包）：
- kind=llm：config={api_key, base_url}，返回 OpenAI 兼容客户端（具备 .chat.completions.create）；
- kind=tts：config=speech_configs 全量 dict（enabled/base_url/api_key/model/provider），
  返回 async (text, voice, out_dir, fname) -> str|None（本地文件路径；None=失败由调用方兜底）；
- 工厂体一律晚绑定原实现模块（from ... import 放函数内），monkeypatch 接缝语义不变。
"""
from __future__ import annotations

import re

PROVIDER_KINDS = ("llm", "tts", "asr", "vision", "image", "push")

_NAME_RE = re.compile(r"^[a-z0-9_]{2,32}$")

# (kind, name) -> {"factory": callable, "meta": {label, description}, "source": "builtin" | 插件名}
_ENTRIES: dict[tuple[str, str], dict] = {}


def register_provider(kind: str, name: str, factory, meta: dict | None = None, source: str = "builtin") -> None:
    """开放注册口（X3）：内置与插件同一入口；重复注册/非法参数直接抛错（加载期暴露问题）。"""
    if kind not in PROVIDER_KINDS:
        raise ValueError(f"非法 kind（需 {'/'.join(PROVIDER_KINDS)}）: {kind!r}")
    if not isinstance(name, str) or not _NAME_RE.match(name or ""):
        raise ValueError(f"非法 provider name（需 2-32 位小写字母/数字/下划线）: {name!r}")
    key = (kind, name)
    if key in _ENTRIES:
        raise ValueError(f"provider already registered: {kind}/{name}")
    if not callable(factory):
        raise ValueError(f"factory 必须可调用: {factory!r}")
    meta = dict(meta or {})
    _ENTRIES[key] = {
        "factory": factory,
        "meta": {
            "label": str(meta.get("label") or name)[:32],
            "description": str(meta.get("description") or "")[:120],
        },
        "source": str(source or "builtin"),
    }


def unregister_providers_for_source(source: str) -> list[tuple[str, str]]:
    """注销某来源注册的全部 provider（插件卸载用）。返回被注销的 (kind, name) 列表。"""
    removed = [k for k, ent in _ENTRIES.items() if ent["source"] == source]
    for key in removed:
        _ENTRIES.pop(key, None)
    return removed


def unregister_providers_not_in(sources: set[str]) -> list[tuple[str, str]]:
    """清理：插件来源但来源已不在加载集合中的注册（sync_plugins_db 重扫后调用）。"""
    stale = [k for k, ent in _ENTRIES.items() if ent["source"] != "builtin" and ent["source"] not in sources]
    for key in stale:
        _ENTRIES.pop(key, None)
    return stale


def _source_enabled(key: tuple[str, str]) -> bool:
    ent = _ENTRIES.get(key)
    if ent is None or ent["source"] == "builtin":
        return ent is not None
    try:
        from app.plugins.registry import _enabled
        return bool(_enabled.get(ent["source"], False))
    except Exception:
        return False


def provider_factory(kind: str, name: str):
    """取指定 provider 的工厂；未注册或来源停用返回 None。"""
    key = (kind, name)
    if key not in _ENTRIES or not _source_enabled(key):
        return None
    return _ENTRIES[key]["factory"]


def resolve_provider(kind: str, config: dict | None = None) -> tuple[str, object] | None:
    """解析 kind 下应生效的 provider，返回 (name, factory)；无命中返回 None（调用方走内置直连）。

    本批最小规则（provider_select hook 落地前的唯一选择点）：
    1. config["provider"]（api_configs.provider / speech_configs.provider）与注册名
       精确匹配且来源启用 → 用它（插件 provider 的选中通道）；
    2. 否则 kind 下第一个启用的内置实现（注册序）；
    3. 无内置时第一个启用的插件来源（注册序）。
    """
    if kind not in PROVIDER_KINDS:
        return None
    config = config or {}
    tag = str(config.get("provider") or "").strip()
    if tag:
        factory = provider_factory(kind, tag)
        if factory is not None:
            return (tag, factory)
    for (k, name), ent in _ENTRIES.items():
        if k == kind and ent["source"] == "builtin" and _source_enabled((k, name)):
            return (name, ent["factory"])
    for (k, name), ent in _ENTRIES.items():
        if k == kind and _source_enabled((k, name)):
            return (name, ent["factory"])
    return None


def list_providers(kind: str | None = None) -> list[dict]:
    """可选 provider 列表（来源启用过滤；供配置页/未来 api_config tab 消费）。"""
    out = []
    for (k, name), ent in _ENTRIES.items():
        if kind is not None and k != kind:
            continue
        if not _source_enabled((k, name)):
            continue
        out.append({"kind": k, "name": name, "source": ent["source"], **ent["meta"]})
    return out


# ── 内置 provider 注册（与插件同一入口；实现体晚绑定原服务模块）──


def _openai_compatible_llm_factory(config: dict):
    """内置 LLM 工厂：晚绑定 app.agent.llm_client.get_llm_client（客户端缓存/超时/代理规避均在原处）。"""
    from app.agent import llm_client as _llm
    return _llm.get_llm_client(api_key=config.get("api_key"), base_url=config.get("base_url"))


def _dashscope_tts_factory(config: dict):
    """内置 TTS（百炼 DashScope）工厂：返回 async 合成器；端点降级/模型冷却在 tts_service 原处。"""
    import asyncio as _asyncio
    from app.services import tts_service as _tts

    async def _run(text: str, voice: str, out_dir, fname: str) -> str | None:
        return await _asyncio.to_thread(_tts._synth_dashscope_sync, text, voice, config, out_dir, fname)

    return _run


register_provider("llm", "openai_compatible", _openai_compatible_llm_factory, {
    "label": "OpenAI 兼容",
    "description": "api_configs/task_llm_configs 三级配置回退链的通用 LLM 通道（llm_client）",
})
register_provider("tts", "dashscope", _dashscope_tts_factory, {
    "label": "百炼 DashScope TTS",
    "description": "speech_configs 启用时生效的云端合成通道（tts_service，失败回退 edge-tts）",
})
