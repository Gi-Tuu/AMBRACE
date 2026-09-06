# -*- coding: utf-8 -*-
"""per-tenant bridge secret（包 C，2026-09-06 待排期清理；SaaS S0 前置最后一环的函数位落地）。

存储：内核 `plugin_stores` KV（plugin_name="wechat_ilink"、user_id=tenant 家庭 root、
key="bridge_secret"），value_json={"secret_enc": Fernet 密文}——加密复用本插件 crypto_util
（Fernet + AMBRACE_SECRET_KEY/ILINK_TOKEN_KEY 环境变量派生，与 bot_token 同体系）。
**不落明文、不进日志、不进前端返回**（GET 只回脱敏预览）。

解析（`resolve_bridge_secret`）：查无该租户密钥 → 返回空串（调用方回落全局 env
WECHAT_ILINK_BRIDGE_SECRET，现网单通道零改动）；解密失败视为未配置（fail-closed 不静默放行）。
"""
from __future__ import annotations

_STORE_PLUGIN = "wechat_ilink"
_STORE_KEY = "bridge_secret"


def _mask(secret: str) -> str:
    s = str(secret or "")
    if len(s) <= 8:
        return "***" if s else ""
    return f"{s[:4]}***{s[-4:]}"


async def get_bridge_secret_row(db, tenant_id: int) -> dict | None:
    """读取租户 bridge secret 存储行（解密后 {"secret": ...}）；无则 None。"""
    import json  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415
    from app.models.plugin import PluginStore  # noqa: PLC0415

    import crypto_util  # noqa: PLC0415

    row = (await db.execute(select(PluginStore).where(
        PluginStore.plugin_name == _STORE_PLUGIN,
        PluginStore.user_id == int(tenant_id),
        PluginStore.key == _STORE_KEY,
    ))).scalar_one_or_none()
    if row is None:
        return None
    try:
        data = json.loads(row.value_json or "{}")
        return {"secret": crypto_util.decrypt(data.get("secret_enc") or "")}
    except Exception:
        return None  # 密文损坏/密钥轮换 → 视为未配置（调用方回落全局，不放行错误密钥）


async def resolve_bridge_secret(db, tenant_id: int | None) -> str:
    """解析租户桥密钥；未配置返回空串（调用方回落全局 env）。"""
    if tenant_id is None:
        return ""
    row = await get_bridge_secret_row(db, tenant_id)
    return (row or {}).get("secret") or ""


async def set_bridge_secret(db, tenant_id: int, secret: str) -> str:
    """写入/轮换租户密钥（Fernet 加密落 KV；upsert 幂等）。返回脱敏预览。"""
    import json  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415
    from app.models.plugin import PluginStore  # noqa: PLC0415

    import crypto_util  # noqa: PLC0415

    secret = str(secret or "").strip()
    if not secret:
        raise ValueError("secret 不能为空")
    enc = crypto_util.encrypt(secret)
    row = (await db.execute(select(PluginStore).where(
        PluginStore.plugin_name == _STORE_PLUGIN,
        PluginStore.user_id == int(tenant_id),
        PluginStore.key == _STORE_KEY,
    ))).scalar_one_or_none()
    if row is None:
        db.add(PluginStore(plugin_name=_STORE_PLUGIN, user_id=int(tenant_id),
                           key=_STORE_KEY, value_json=json.dumps({"secret_enc": enc})))
    else:
        row.value_json = json.dumps({"secret_enc": enc})
    await db.flush()
    return _mask(secret)


async def delete_bridge_secret(db, tenant_id: int) -> bool:
    """删除租户密钥（回落全局语义）。返回是否删除了行。"""
    from sqlalchemy import delete as sa_delete  # noqa: PLC0415
    from app.models.plugin import PluginStore  # noqa: PLC0415

    result = await db.execute(sa_delete(PluginStore).where(
        PluginStore.plugin_name == _STORE_PLUGIN,
        PluginStore.user_id == int(tenant_id),
        PluginStore.key == _STORE_KEY,
    ))
    return bool(result.rowcount)


async def _resolve_bridge_secret(db, tenant_hint: int | None) -> str:
    """桥密钥解析（Q3/S4 函数位正式实现，2026-09-06）：

    - tenant_hint 非空 → 先查该租户 per-tenant 密钥（plugin_stores Fernet），查无回落全局 env；
    - tenant_hint 空 → 全局 env（现网单通道零改动）。
    返回空串 = 未配置（调用方 503 fail-closed）。
    """
    import os  # noqa: PLC0415

    if tenant_hint is not None:
        per_tenant = await resolve_bridge_secret(db, tenant_hint)
        if per_tenant:
            return per_tenant
    return os.environ.get("WECHAT_ILINK_BRIDGE_SECRET", "") or ""
