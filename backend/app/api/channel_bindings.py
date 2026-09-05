# -*- coding: utf-8 -*-
"""渠道绑定 API（S3，一机多主 per-账号化，2026-09-05）。

App 渠道卡统一走本路由：GET/PUT/DELETE /api/v1/channels/{channel}/bindings[/{bot_account_id}]。
- tenant = 当前登录主账号（家庭 root），经 app/application/tenant_scope 解析（SaaS S0 同源）；
- **灰度双读/双写**：flag channel_binding_v2 开 → channel_bindings 新表（ChannelBindingService）；
  关或新表无行 → 回落旧全局 config（plugin allowed_character_ids 合成 "default" 单行只读展示 /
  PUT 走既有内核 update_plugin 裁决），保证灰度期 App 面板照常可用；
- 子账号只读（写操作 403，i18n channel_bind_main_only）；所有查询/写入带 tenant。
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError

from app.auth.deps import get_current_user_id
from app.application import channel_binding_service as svc
from app.application.tenant_scope import resolve_tenant
from app.i18n import tr_lang
from app.providers.channel_binding_reader import (
    fallback_global_ids,
    channel_binding_v2_enabled,
    channel_taken_over,
    plugin_of,
)


async def _filtered_fallback(db, channel: str, tenant: int) -> list[int]:
    """C2：渠道表全空时的回落合成——全局串按角色归属家庭 root 过滤（不越权展示他租户角色）。"""
    from app.providers.channel_binding_reader import filter_ids_by_tenant

    return await filter_ids_by_tenant(db, await fallback_global_ids(db, channel), tenant)

router = APIRouter(prefix="/api/v1/channels", tags=["ChannelBindings"])


def _svc_http_exc(e: Exception, lang: str) -> HTTPException:
    """ChannelBindingService 异常 → HTTP（沿用内核既有 i18n key 与状态码语义）。"""
    if isinstance(e, svc.SubAccountForbidden):
        return HTTPException(status_code=403, detail=tr_lang(lang, "channel_bind_main_only"))
    if isinstance(e, svc.CrossFamilyCharacter):
        return HTTPException(status_code=403, detail=tr_lang(lang, "channel_bind_cross_family"))
    if isinstance(e, svc.ChannelOccupied):
        return HTTPException(status_code=400, detail=tr_lang(lang, "channel_bind_occupied"))
    if isinstance(e, svc.PhysicalSingletonTaken):
        return HTTPException(status_code=409, detail=tr_lang(lang, "channel_bind_physical_taken"))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, IntegrityError):
        # C6：并发撞 UQ(channel,tenant,bot) 等唯一约束 → 409 语义化，不再 500。
        return HTTPException(status_code=409, detail=tr_lang(lang, "channel_bind_occupied"))
    return HTTPException(status_code=500, detail=str(e))


def _row_json(r) -> dict:
    return {
        "bot_account_id": r.bot_account_id,
        "bot_label": r.bot_label or "",
        "character_id": int(r.character_id),
        "enabled": bool(r.enabled),
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _synthetic_row(character_id: int | None) -> dict:
    """旧全局 config 合成行（灰度期展示兜底；updated_at 无权威值置 null）。"""
    return {
        "bot_account_id": "default",
        "bot_label": "",
        "character_id": int(character_id) if character_id is not None else None,
        "enabled": character_id is not None,
        "updated_at": None,
    }


@router.get("/{channel}/bindings")
async def list_my_bindings(channel: str, user_id: int = Depends(get_current_user_id)):
    """列出当前主账号（tenant）在该渠道的 bot 绑定（C2 判据，2026-09-05）：

    - flag 开：本租户新表行 → 原样返回；无行但渠道已被 v2 接管（任意租户有行）→ 空 items
      （不合成全局行，杜绝跨租户幽灵）；渠道表全空 → 旧全局串（按归属过滤）合成行；
    - flag 关：旧全局 config 合成行（灰度期面板照常可用）。
    """
    from app.db.database import async_session_factory

    async with async_session_factory() as db:
        tenant = await resolve_tenant(db, user_id)
        rows: list[dict] = []
        if channel_binding_v2_enabled():
            rows = [_row_json(r) for r in await svc.list_bindings(db, tenant, channel)]
            if not rows and await channel_taken_over(db, channel):
                return {"items": []}
            if not rows:
                ids = await _filtered_fallback(db, channel, tenant)
                rows = [_synthetic_row(ids[0] if ids else None)]
        if not rows:
            ids = await fallback_global_ids(db, channel)
            rows = [_synthetic_row(ids[0] if ids else None)]
        return {"items": rows}


@router.put("/{channel}/bindings/{bot_account_id}")
async def put_binding(channel: str, bot_account_id: str, body: dict,
                      user_id: int = Depends(get_current_user_id),
                      lang: str = Header(default="zh")):
    """绑定/换绑指定 bot：flag 开=ChannelBindingService 租户化裁决；
    flag 关=回落既有内核 update_plugin 单选裁决（灰度期与现 App 行为一致）。"""
    from app.db.database import async_session_factory

    try:
        character_id = int(body.get("character_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="character_id 必须为整数")
    if character_id <= 0:
        raise HTTPException(status_code=400, detail="character_id 必须为正整数")
    bot_label = str(body.get("bot_label") or "")

    if channel_binding_v2_enabled():
        try:
            async with async_session_factory() as db:
                row = await svc.upsert_binding(db, user_id, channel, character_id,
                                               bot_account_id=bot_account_id, bot_label=bot_label)
                await db.commit()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 - 服务异常统一映射 i18n key
            raise _svc_http_exc(e, lang)
        return {"ok": True, "character_id": int(row.character_id), "bot_account_id": row.bot_account_id}

    # 灰度回落：既有内核裁决（单选/占用/跨家庭/子账号 403 全由 _validate_channel_binding 保证）
    from app.api.plugins import update_plugin

    await update_plugin(plugin_of(channel), {"config": {"allowed_character_ids": [character_id]}},
                        user_id=user_id, lang=lang)
    return {"ok": True, "character_id": character_id, "bot_account_id": bot_account_id or "default"}


@router.delete("/{channel}/bindings/{bot_account_id}")
async def del_binding(channel: str, bot_account_id: str,
                      user_id: int = Depends(get_current_user_id),
                      lang: str = Header(default="zh")):
    """解绑：flag 开=删该 bot 的 channel_bindings 行；flag 关=内核空串解绑（旧语义）。"""
    from app.db.database import async_session_factory

    if channel_binding_v2_enabled():
        try:
            async with async_session_factory() as db:
                await svc.remove_binding(db, user_id, channel, bot_account_id)
                await db.commit()
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise _svc_http_exc(e, lang)
        return {"ok": True, "deleted": True}

    from app.api.plugins import update_plugin

    await update_plugin(plugin_of(channel), {"config": {"allowed_character_ids": ""}},
                        user_id=user_id, lang=lang)
    return {"ok": True, "deleted": True}


# 供 S0/SaaS 复用的请求级租户依赖（S4）：路由参数直接 current_tenant = Depends(get_current_tenant)
async def get_current_tenant(user_id: int = Depends(get_current_user_id)) -> int:
    """FastAPI 依赖：登录态 → 统一租户键（家庭 root）。渠道绑定 API 已用；S0 全量铺开时直接 import。"""
    from app.db.database import async_session_factory

    async with async_session_factory() as db:
        return await resolve_tenant(db, user_id)


__all__ = ["router", "get_current_tenant"]
