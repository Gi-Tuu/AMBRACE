"""Provider 注册表（X3，2026-08-31）：llm/tts/asr/vision/image/push 统一注册口。

内置实现与插件扩展包走同一注册入口；实现体留在原服务模块（引擎留核、变化点外放）。
API 从 app.providers.registry 导入，本包仅做门面重导出。
"""
from app.providers.registry import (
    PROVIDER_KINDS,
    list_providers,
    provider_factory,
    register_provider,
    resolve_provider,
    unregister_providers_for_source,
    unregister_providers_not_in,
)

__all__ = [
    "PROVIDER_KINDS",
    "register_provider",
    "unregister_providers_for_source",
    "unregister_providers_not_in",
    "provider_factory",
    "resolve_provider",
    "list_providers",
]
