# -*- coding: utf-8 -*-
"""wechat_ilink http_router：绑定二维码 / 扫码状态 / 绑定 / 解绑 / 换绑 / 状态。

【命名空间】渠道插件顶层模块名易撞，本插件沿用包内相对导入/文件名，新渠道须用包内相对导入或模块名前缀（registry 加载器保持现状）。
【绑定/解绑裁决：走内核完整 PUT 路径（唯一裁决点）】
- 插件**不 import 复用内部校验函数** ``_validate_channel_binding``、不重抄裁决逻辑；
- 绑定 = 调内核 ``update_plugin``（即 ``PUT /api/v1/plugins/wechat_ilink``）传入
  ``allowed_character_ids``，由内核完成子账号 403 / 跨家庭 403 / 多角色 400 / 家庭单选 /
  换绑 400 / 空数组解绑（与 douyin_mcp 完全同语义的裁决模板）后，再落插件自有绑定表；
- 解绑 = 调内核 PUT 传空 ``allowed_character_ids``（同抖音空数组解绑），再清绑定停状态。
- 换绑（rebind，任务 B）= 调内核 PUT 空数组解绑 → 再调内核 PUT 单选绑定（目标须同家庭），
  两步成功后在同一事务迁移绑定行（保留 bot_token_enc/baseurl，区别于解绑）。
- 校验失败由内核抛 ``HTTPException``，原样透出给前端（状态码/文案由内核统一）。
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException

from app.auth.deps import get_current_user_id

# iLink baseurl 白名单（P3-2：防 confirmed 返回任意域把 bot_token 投毒外发；只允许微信官方域）
_ALLOWED_HOST_SUFFIXES = ("weixin.qq.com", "wechat.com")
_DEFAULT_HOST = "https://ilinkai.weixin.qq.com"


def _parse_character_id(body: dict) -> int:
    """从绑定/解绑请求体提取 character_id；缺失/非法 → 400。"""
    raw = (body or {}).get("character_id") if isinstance(body, dict) else None
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="character_id 必须为整数")
    if cid <= 0:
        raise HTTPException(status_code=400, detail="character_id 必须为正整数")
    return cid


def _parse_character_ids(raw) -> int | None:
    """把配置里 allowed_character_ids（逗号串/列表/空）解析为单选 int；无有效值返回 None。"""
    if isinstance(raw, list):
        ids = [int(x) for x in raw if str(x).strip().isdigit()]
    else:
        ids = [int(x) for x in str(raw or "").split(",") if x.strip().isdigit()]
    return ids[0] if len(ids) == 1 else None


def _confirmed_payload(body: dict) -> dict:
    """提取 iLink 扫码 confirmed 载荷（bot_token/baseurl/ilink_user_id/ilink_bot_id）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="请求体必须为对象")
    bot_token = str(body.get("bot_token") or "").strip()
    if not bot_token:
        raise HTTPException(status_code=400, detail="缺少 bot_token（confirmed 载荷不完整）")
    return {
        "ilink_user_id": str(body.get("ilink_user_id") or "").strip(),
        "ilink_bot_id": str(body.get("ilink_bot_id") or "").strip(),
        "bot_token": bot_token,
        "baseurl": str(body.get("baseurl") or "").strip(),
    }


def _validate_baseurl(baseurl: str) -> str:
    """baseurl 白名单校验（P3-2 SSRF 防投毒）：只允许 https + *.weixin.qq.com / *.wechat.com。"""
    b = (baseurl or _DEFAULT_HOST).strip().rstrip("/")
    if not b:
        raise HTTPException(status_code=400, detail="baseurl 不能为空")
    try:
        parsed = urlparse(b)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="baseurl 非法") from e
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == s or host.endswith("." + s) for s in _ALLOWED_HOST_SUFFIXES):
        raise HTTPException(status_code=400, detail="baseurl 不在微信官方域名白名单（P3-2）")
    return b


def _mask_uid(uid: str) -> str:
    """微信稳定 user_id 脱敏展示：只保留首尾，防泄露完整标识（前端/日志）。"""
    uid = str(uid or "")
    if len(uid) <= 8:
        return "***" if uid else ""
    return f"{uid[:4]}***{uid[-4:]}"


# ------------------------------------------------------------------ 内核裁决（唯一仲裁点）

def _binding_v2_enabled() -> bool:
    """一机多主 flag（channel_binding_v2，默认关=回落旧全局 config 路径，零行为变化）。"""
    try:
        from app.agent.loop import AGENT_FLAGS  # noqa: PLC0415

        return bool(AGENT_FLAGS.get("channel_binding_v2", False))
    except Exception:
        return False


def _svc_http_exc(e: Exception, lang: str) -> HTTPException:
    """ChannelBindingService 异常 → HTTP（沿用内核既有 i18n key 与状态码语义）。"""
    from app.application import channel_binding_service as _svc  # noqa: PLC0415
    from app.i18n import tr_lang  # noqa: PLC0415

    if isinstance(e, _svc.SubAccountForbidden):
        return HTTPException(status_code=403, detail=tr_lang(lang, "channel_bind_main_only"))
    if isinstance(e, _svc.CrossFamilyCharacter):
        return HTTPException(status_code=403, detail=tr_lang(lang, "channel_bind_cross_family"))
    if isinstance(e, _svc.ChannelOccupied):
        return HTTPException(status_code=400, detail=tr_lang(lang, "channel_bind_occupied"))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


def _resolve_bridge_secret(tenant_id: int | None) -> str:
    """桥共享密钥解析（Q3 拍板：本期全局 WECHAT_ILINK_BRIDGE_SECRET；SaaS 阶段切 per-tenant
    时只改本函数（按 tenant_id 反查该租户配置的 secret），调用点不动）。"""
    import os  # noqa: PLC0415

    return os.environ.get("WECHAT_ILINK_BRIDGE_SECRET", "") or ""


async def _kernel_bind(user_id: int, character_id: int, lang: str) -> None:
    """走内核完整 PUT 路径做绑定裁决（子账号/跨家庭/多角色/家庭唯一/换绑）；失败抛 HTTPException。"""
    from app.api.plugins import update_plugin  # noqa: PLC0415 - 避免加载期重链

    await update_plugin("wechat_ilink", {"config": {"allowed_character_ids": [character_id]}},
                        user_id=user_id, lang=lang)


async def _kernel_unbind(user_id: int, lang: str) -> None:
    """走内核完整 PUT 路径做解绑裁决（空 allowed_character_ids = 同抖音空数组解绑语义）。"""
    from app.api.plugins import update_plugin  # noqa: PLC0415

    await update_plugin("wechat_ilink", {"config": {"allowed_character_ids": ""}},
                        user_id=user_id, lang=lang)


# ------------------------------------------------------------------ 插件自有绑定表

async def _save_binding(db, *, user_id: int, character_id: int,
                        ilink_user_id: str = "", ilink_bot_id: str = "",
                        bot_token: str = "", baseurl: str = "",
                        bot_account_id: str = "default") -> object:
    """落库绑定（bot_token 加密存储）。P1-3：同稳定 ilink_user_id 重新扫码 → 轮换凭据不新建行。

    一机多主（2026-09-05）：行带 tenant_id（家庭 root）/ bot_account_id（ClawBot 稳定键，单 bot 恒 default）。
    匹配优先级（W4 防串绑语义保持）：
      (bot_account_id, ilink_user_id, user_id) → (user_id, bot_account_id, character_id) → 新建；
    跨主账号同 (bot,wxuser) 撞 uq_wechat_bot_wxuser 唯一索引 → IntegrityError 由调用方转 4xx。
    """
    import crypto_util  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from app.application.family_service import get_family_root_id  # noqa: PLC0415

    import models  # noqa: PLC0415

    baseurl = _validate_baseurl(baseurl)
    token_enc = crypto_util.encrypt(bot_token)
    tenant_id = await get_family_root_id(db, user_id) or user_id

    row = None
    if ilink_user_id:
        row = (await db.execute(
            select(models.WeChatILinkBinding).where(
                models.WeChatILinkBinding.bot_account_id == bot_account_id,
                models.WeChatILinkBinding.ilink_user_id == ilink_user_id,
                models.WeChatILinkBinding.user_id == user_id,  # W4（v3.4.4 审查）：防多主账号理论串绑
            )
        )).scalars().first()
    if row is None:
        row = (await db.execute(
            select(models.WeChatILinkBinding).where(
                models.WeChatILinkBinding.user_id == user_id,
                models.WeChatILinkBinding.bot_account_id == bot_account_id,
                models.WeChatILinkBinding.character_id == character_id)
        )).scalars().first()
    if row is None:
        row = models.WeChatILinkBinding(user_id=user_id, character_id=character_id,
                                        tenant_id=int(tenant_id), bot_account_id=bot_account_id)
        db.add(row)
    # 轮换/首次写入：token/绑定标识一律更新，保证不残留旧凭据
    row.user_id = user_id
    row.tenant_id = int(tenant_id)
    row.bot_account_id = bot_account_id
    row.character_id = character_id
    row.ilink_user_id = ilink_user_id
    row.ilink_bot_id = ilink_bot_id
    row.bot_token_enc = token_enc
    row.baseurl = baseurl
    row.enabled = True
    return row


async def _clear_binding(db, user_id: int, character_id: int) -> None:
    """解绑：清凭据 + 停状态（token 解绑即删，P0-4；保留行便于重绑复用/历史追溯）。"""
    import models  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    row = (await db.execute(
        select(models.WeChatILinkBinding).where(
            models.WeChatILinkBinding.user_id == user_id,
            models.WeChatILinkBinding.character_id == character_id)
    )).scalars().first()
    if row is not None:
        row.enabled = False
        row.bot_token_enc = ""
        row.ilink_bot_id = ""
        row.baseurl = ""
        row.poll_buf = ""


async def get_binding_view(user_id: int | None = None) -> dict:
    """绑定状态视图（供 /status 路由与 ChannelPort.binding_status 复用，避免逻辑漂移）。

    一机多主：flag 开时绑定角色读 channel_bindings 新表（按当前登录者租户）；flag 关回落旧
    全局 config 串（响应形态两路一致）。
    """
    from sqlalchemy import select  # noqa: PLC0415
    from app.db.database import async_session_factory  # noqa: PLC0415

    import models  # noqa: PLC0415

    char_id: int | None = None
    if _binding_v2_enabled() and user_id is not None:
        from app.application import channel_binding_service as _svc  # noqa: PLC0415
        from app.application.tenant_scope import resolve_tenant  # noqa: PLC0415

        async with async_session_factory() as db:
            tenant = await resolve_tenant(db, user_id)
            _rows = [r for r in await _svc.list_bindings(db, tenant, "wechat") if r.enabled]
            char_id = int(_rows[0].character_id) if _rows else None
    else:
        from app.plugins import registry  # noqa: PLC0415

        plugin = registry.get_plugin("wechat_ilink") or {}
        config = plugin.get("config") or {}
        char_id = _parse_character_ids(config.get("allowed_character_ids"))

    bound = enabled = has_cred = False
    uid_masked = ""
    if char_id is not None:
        async with async_session_factory() as db:
            q = select(models.WeChatILinkBinding).where(models.WeChatILinkBinding.character_id == char_id)
            if user_id is not None:
                q = q.where(models.WeChatILinkBinding.user_id == user_id)
            q = q.order_by(models.WeChatILinkBinding.id.desc())
            row = (await db.execute(q)).scalars().first()
        if row is not None:
            enabled = bool(row.enabled)
            bound = enabled
            has_cred = bool(row.bot_token_enc)
            uid_masked = _mask_uid(row.ilink_user_id)
    return {
        "ok": True,
        "bound": bound,
        "character_id": char_id,
        "enabled": enabled,
        "has_credentials": has_cred,
        "ilink_user_id_masked": uid_masked,
        "binding": {"unique_per_family": True, "mode": "bot_single"},
    }


# ------------------------------------------------------------------ 路由挂载

def mount(router):
    """在 sdk.router() 返回的插件路由器上挂载 http_router 端点（前缀 /api/v1/plugins/wechat_ilink，强制登录态）。

    端点：/qrcode、/qrcode/{qrcode}、/bind、/unbind、/rebind（任务 B）、/status。
    """

    @router.get("/qrcode")
    async def create_qrcode():
        """申请绑定二维码（转 iLink get_bot_qrcode，NEEDS_RUNTIME_VERIFICATION）。"""
        from ilink_client import ILinkClient  # noqa: PLC0415

        return await ILinkClient.fetch_qrcode()

    @router.get("/qrcode/{qrcode}")
    async def qrcode_status(qrcode: str):
        """轮询扫码状态；confirmed 时含 bot_token/baseurl/ilink_user_id/ilink_bot_id（真机校准）。"""
        from ilink_client import ILinkClient  # noqa: PLC0415

        return await ILinkClient.fetch_qrcode_status(qrcode)

    @router.post("/bind")
    async def bind(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
        """绑定：先裁决（flag 开=ChannelBindingService 租户化裁决；关=内核完整 PUT 路径——家庭唯一/
        单选/子账号/跨家庭/换绑语义一致），通过后才落库加密 token。

        一机多主：body 可带 bot_account_id（ClawBot 稳定键；缺省 "default"，多 bot 阶段由扫码上下文带上）。
        """
        character_id = _parse_character_id(body)
        confirmed = _confirmed_payload(body)
        bot_account_id = str(body.get("bot_account_id") or "default").strip() or "default"
        # P3-2 SSRF：先验 baseurl 白名单，失败即拒（不先改绑定，避免半绑定态）
        _validate_baseurl(confirmed["baseurl"])
        from app.db.database import async_session_factory  # noqa: PLC0415

        if _binding_v2_enabled():
            from app.application import channel_binding_service as _svc  # noqa: PLC0415

            try:
                async with async_session_factory() as db:
                    await _svc.upsert_binding(db, user_id, "wechat", character_id,
                                              bot_account_id=bot_account_id)
                    await db.commit()
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001 - 服务异常统一映射 i18n key
                raise _svc_http_exc(e, lang)
        else:
            await _kernel_bind(user_id, character_id, lang)

        # W5（v3.4.4 审查）：落库失败补偿（对齐 /rebind 的 P2-1）——裁决已生效、
        # 落库失败时回滚裁决（flag 关=内核 PUT 空数组；flag 开=删 channel_bindings 行），
        # 避免半绑定态；补偿失败记日志后原样抛出。
        try:
            async with async_session_factory() as db:
                await _save_binding(db, user_id=user_id, character_id=character_id,
                                    bot_account_id=bot_account_id, **confirmed)
                await db.commit()
        except Exception:
            try:
                if _binding_v2_enabled():
                    from app.application import channel_binding_service as _svc  # noqa: PLC0415

                    async with async_session_factory() as db:
                        await _svc.remove_binding(db, user_id, "wechat", bot_account_id)
                        await db.commit()
                else:
                    await _kernel_unbind(user_id, lang)
            except Exception:
                from app.plugins import sdk  # noqa: PLC0415
                sdk.log("wechat_ilink /bind 补偿回滚失败 user=%s char=%s，请人工核对绑定裁决面",
                        user_id, character_id)
            raise
        return {"ok": True, "character_id": character_id}

    @router.post("/unbind")
    async def unbind(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
        """解绑：flag 开=ChannelBindingService 删绑定行；关=内核 PUT 空数组解绑裁决。
        两路都再清插件绑定停状态。"""
        character_id = _parse_character_id(body)
        from app.db.database import async_session_factory  # noqa: PLC0415

        if _binding_v2_enabled():
            from app.application import channel_binding_service as _svc  # noqa: PLC0415

            try:
                async with async_session_factory() as db:
                    await _svc.remove_binding(db, user_id, "wechat")
                    await db.commit()
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise _svc_http_exc(e, lang)
        else:
            await _kernel_unbind(user_id, lang)

        async with async_session_factory() as db:
            await _clear_binding(db, user_id, character_id)
            await db.commit()
        return {"ok": True, "unbound": True, "character_id": character_id}

    @router.post("/rebind")
    async def rebind(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
        """换绑（任务 B）：主账号在 App「扩展」页对 wechat_ilink 换绑角色，无需重扫微信。

        flag 开（channel_binding_v2）：ChannelBindingService.upsert_binding 单次裁决+改绑（原子，
        无两步中间态，无需补偿）。
        flag 关（旧路径，语义保持）：1) 内核 PUT 空数组解绑裁决；2) 内核 PUT 单选绑定裁决；
        3) 两步成功后同一事务迁绑定行角色（保留 bot_token_enc/baseurl，区别于解绑）。
        P2-1（2026-09-05）：第 2) 步失败时补偿回滚第 1) 步（恢复内核绑定为旧角色），
        避免「内核已清空、插件仍指旧角色」的中间态；补偿失败记日志不静默。
        """
        character_id = _parse_character_id(body)
        from app.db.database import async_session_factory  # noqa: PLC0415

        if _binding_v2_enabled():
            from app.application import channel_binding_service as _svc  # noqa: PLC0415

            try:
                async with async_session_factory() as db:
                    await _svc.upsert_binding(db, user_id, "wechat", character_id)
                    await db.commit()
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                raise _svc_http_exc(e, lang)
            async with async_session_factory() as db:
                from sqlalchemy import select  # noqa: PLC0415
                import models  # noqa: PLC0415

                rows = (await db.execute(
                    select(models.WeChatILinkBinding).where(
                        models.WeChatILinkBinding.user_id == user_id,
                        models.WeChatILinkBinding.enabled.is_(True),
                    )
                )).scalars().all()
                for row in rows:
                    row.character_id = character_id
                    row.enabled = True
                    # bot_token_enc / baseurl / ilink_user_id / ilink_bot_id 保留不清（区别于解绑）
                await db.commit()
            return {"ok": True, "rebound": True, "character_id": character_id}

        from sqlalchemy import select  # noqa: PLC0415
        import models  # noqa: PLC0415

        # 换绑前快照旧角色（当前主账号名下 enabled 绑定行；单行场景取第一行）
        old_cid = None
        async with async_session_factory() as db:
            _old_rows = (await db.execute(
                select(models.WeChatILinkBinding).where(
                    models.WeChatILinkBinding.user_id == user_id,
                    models.WeChatILinkBinding.enabled.is_(True),
                )
            )).scalars().all()
            if _old_rows:
                old_cid = int(_old_rows[0].character_id)
        if old_cid == character_id:
            return {"ok": True, "rebound": True, "character_id": character_id}

        try:
            await _kernel_unbind(user_id, lang)
            await _kernel_bind(user_id, character_id, lang)
        except HTTPException:
            # P2-1：第 2) 步失败 → 第 1) 步已生效，补偿恢复旧角色后原样抛出
            if old_cid is not None:
                try:
                    await _kernel_bind(user_id, old_cid, lang)
                except Exception:
                    from app.plugins import sdk  # noqa: PLC0415
                    sdk.log("wechat_ilink rebind 补偿回滚失败 old_cid=%s，请人工核对内核 allowed_character_ids", old_cid)
            raise

        async with async_session_factory() as db:
            rows = (await db.execute(
                select(models.WeChatILinkBinding).where(
                    models.WeChatILinkBinding.user_id == user_id,
                    models.WeChatILinkBinding.enabled.is_(True),
                )
            )).scalars().all()
            for row in rows:
                row.character_id = character_id
                row.enabled = True
                # bot_token_enc / baseurl / ilink_user_id / ilink_bot_id 保留不清（区别于解绑）
            await db.commit()
        return {"ok": True, "rebound": True, "character_id": character_id}

    @router.get("/status")
    async def binding_status(user_id: int = Depends(get_current_user_id)):
        """绑定状态 + 本窗口剩余配额（PR2 只报绑定面；配额闸门待 PR3/PR4）。"""
        return await get_binding_view(user_id)


# ===== 服务到服务桥（openclaw→拥爱）：由内核免登录端点调用，勿挂 @router =====

async def bridge_relay_impl(body: dict, secret_header: str):
    """服务到服务（openclaw → 拥爱桥）：网关收微信消息后把文本转发到拥爱生成回复。

    鉴权：共享密钥（X-AMBRACE-Bridge-Secret 由内核端点传入，常量时间比较；密钥经
    _resolve_bridge_secret 解析——本期全局环境变量，SaaS 切 per-tenant 只改该函数）。
    语义：按 (bot_account_id, ilink_user_id) 定位绑定（一机多主：多 ClawBot 并存时同一微信用户
    对不同 bot 是不同会话，单条件 .first() 会串台；payload 缺 bot_account_id 回落 "default"
    兼容现有 openclaw 插件）→ 幂等落库入站（ilink_msg_id）→ 重置配额窗口 →
    走主认知链路生成整段回复 → QuotaGate 裁决"可否由网关下发"。空回复/额度不足 → sendable=false。
    回执稳定键（消除 lastOutRowId 单值竞态）：响应带 out_row_id + bot_account_id + in_msg_id，
    out 流水行 ilink_msg_id 落 "gw:{in_msg_id}"；/bridge/wechat-delivery 可按稳定键定位。
    """
    import os  # noqa: PLC0415
    import secrets as _secrets

    expected = _resolve_bridge_secret(None)
    if not expected:
        raise HTTPException(status_code=503, detail="bridge not configured")
    if not _secrets.compare_digest(secret_header, expected):
        raise HTTPException(status_code=401, detail="bad secret")
    payload = body if isinstance(body, dict) else {}
    bot_account_id = str(payload.get("bot_account_id") or "default").strip() or "default"
    ilink_user_id = str(payload.get("ilink_user_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    msg_id = str(payload.get("msg_id") or "").strip()
    if not ilink_user_id or not text:
        raise HTTPException(status_code=400, detail="ilink_user_id/text required")

    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415  # W3：双消费通道幂等
    from app.db.database import async_session_factory  # noqa: PLC0415
    import models  # noqa: PLC0415  (插件目录在 sys.path，main.py 已 import)
    from quota import QuotaGate  # noqa: PLC0415
    import inbound  # noqa: PLC0415

    quota_n = int(os.environ.get("WECHAT_ILINK_QUOTA_PER_24H", "10") or 10)

    async with async_session_factory() as db:
        row = (await db.execute(
            select(models.WeChatILinkBinding).where(
                models.WeChatILinkBinding.bot_account_id == bot_account_id,   # ★第一定位键（一机多主）
                models.WeChatILinkBinding.ilink_user_id == ilink_user_id,
                models.WeChatILinkBinding.enabled.is_(True),
            ).with_for_update()
        )).scalars().first()
        if row is None:
            return {"ok": False, "code": "no_binding", "reply": "", "sendable": False, "quota": None}
        if msg_id:
            dup = (await db.execute(
                select(models.WeChatILinkMessage.id).where(
                    models.WeChatILinkMessage.ilink_msg_id == msg_id,
                    models.WeChatILinkMessage.binding_id == row.id,
                )
            )).scalar_one_or_none()
            if dup is not None:
                return {"ok": True, "duplicate": True, "reply": "", "sendable": False, "quota": None}
        QuotaGate(quota_n).on_inbound(row)  # 用户入站：重置 24h 窗口（只重置本行，不跨 bot/租户累加）
        db.add(models.WeChatILinkMessage(
            binding_id=row.id, character_id=int(row.character_id),
            ilink_msg_id=msg_id, context_token="", direction="in",
            content=text, quota_charged=False, status="ok",
        ))
        # W3（v3.4.4 审查）：自轮询与 openclaw 网关为两条并行入站通道（生产二选一）。
        # 入场 commit 撞 uq_wechat_ilink_msg_in 唯一索引 = 消息已被另一条通道消费 →
        # 幂等返回 duplicate（不冒泡 500、不二次生成回复）。
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return {"ok": True, "duplicate": True, "reply": "", "sendable": False, "quota": None}

    reply = await inbound._run_companion_reply(int(row.user_id), int(row.character_id), text)
    # L2 微信出口净文（2026-09-05）：桥返回给 openclaw 的整段文本先做一次「可直接读」收敛，
    # 在 QuotaGate 判定前调用（reply 保留 App 落库原文，真正下发的是净文后的 send_text）。
    # 净文失败/模块缺失时兜底为原文本，绝不影响下发。
    send_text = reply or ""
    sent_cleanup = {"stripped": [], "truncated": False}
    if reply:
        try:
            from wechat_text import clean_wechat_text  # noqa: PLC0415
            _clean = clean_wechat_text(reply)
        except Exception:  # noqa: BLE001 - 净文兜底，不影响主链路
            _clean = {"text": reply, "stripped": [], "truncated": False, "original_len": len(reply)}
        send_text = _clean.get("text") or ""
        sent_cleanup = {
            "stripped": list(_clean.get("stripped") or []),
            "truncated": bool(_clean.get("truncated")),
        }
    sendable, remaining, out_row_id = False, 0, None
    if send_text:
        async with async_session_factory() as db:
            row2 = (await db.execute(
                select(models.WeChatILinkBinding)
                .where(models.WeChatILinkBinding.id == row.id).with_for_update()
            )).scalars().first()
            if row2 is not None:
                dec = QuotaGate(quota_n).acquire(row2)
                # P3-1（2026-09-05）：网关出站流水——配额裁决结果即落 out 台账，与自轮询路径语义对齐：
                # 放行=已交由 openclaw 网关下发（status=sent_by_gateway、quota_charged=True）；
                # 拒绝=本次不下发（status=deferred、quota_charged=False）。
                # openclaw message_sent 回执通道（失败回标/回补）登记排期，本台账先保证可排查「网关回了什么」。
                _out_row = models.WeChatILinkMessage(
                    binding_id=row2.id, character_id=int(row2.character_id),
                    # 回执稳定键：出站行 ilink_msg_id 落 "gw:{入站 msg_id}"（partial 唯一索引只约束
                    # 非空行，同 binding 一条入站至多一条网关回复，不冲突）；delivery 回执可按它定位。
                    ilink_msg_id=(f"gw:{msg_id}" if msg_id else ""), context_token="", direction="out",
                    content=send_text,
                    quota_charged=bool(dec.allowed),
                    status="sent_by_gateway" if dec.allowed else "deferred",
                )
                db.add(_out_row)
                await db.flush()  # 取刚落的 out 流水行 id，供 openclaw message_sent 失败回执（P3-1 回执通道）
                out_row_id = _out_row.id
                await db.commit()
                sendable, remaining = dec.allowed, dec.remaining
    return {"ok": True, "reply": send_text, "sendable": sendable,
            "out_row_id": out_row_id,
            "bot_account_id": bot_account_id,
            "in_msg_id": msg_id,
            "quota": {"remaining": remaining, "charged": bool(send_text and sendable)},
            "sent_cleanup": sent_cleanup}


async def bridge_delivery_impl(body: dict, secret_header: str):
    """服务到服务（openclaw → 拥爱桥）发送回执：openclaw message_sent 失败回调。

    鉴权：共享密钥（X-AMBRACE-Bridge-Secret，常量时间比较；经 _resolve_bridge_secret 解析，
    未配置即 503 fail-closed，不进日志/不进前端——与 bridge_relay_impl 同源同语义）。
    语义：body {out_row_id | (bot_account_id + in_msg_id), ok, error}；ok=false 时把该 out 流水行
    status 改为 failed（配额不回补，保持已计费）。ok=true 不回传/按现状（保持 sent_by_gateway）。
    幂等：重复回调无副作用。
    定位（一机多主回执稳定键）：优先 out_row_id；缺省/未命中时按稳定键回退——
    out 流水行 ilink_msg_id == "gw:{in_msg_id}"（可再叠加 bot_account_id 过滤防串 bot）。
    """
    import secrets as _secrets

    from sqlalchemy import select  # noqa: PLC0415
    from app.db.database import async_session_factory  # noqa: PLC0415
    import models  # noqa: PLC0415

    expected = _resolve_bridge_secret(None)
    if not expected:
        raise HTTPException(status_code=503, detail="bridge not configured")
    if not _secrets.compare_digest(secret_header, expected):
        raise HTTPException(status_code=401, detail="bad secret")
    payload = body if isinstance(body, dict) else {}
    ok = bool(payload.get("ok", False))
    bot_account_id = str(payload.get("bot_account_id") or "").strip()
    in_msg_id = str(payload.get("in_msg_id") or "").strip()
    rid: int | None
    try:
        rid = int(payload.get("out_row_id"))
    except (TypeError, ValueError):
        rid = None

    async with async_session_factory() as db:
        row = None
        if rid is not None:
            row = await db.get(models.WeChatILinkMessage, rid)
        if row is None and in_msg_id:
            # 稳定键回退定位（out 行 ilink_msg_id = "gw:{in_msg_id}"）；bot 归属可校验则校验
            q = (
                select(models.WeChatILinkMessage)
                .join(models.WeChatILinkBinding,
                      models.WeChatILinkBinding.id == models.WeChatILinkMessage.binding_id)
                .where(
                    models.WeChatILinkMessage.direction == "out",
                    models.WeChatILinkMessage.ilink_msg_id == f"gw:{in_msg_id}",
                )
            )
            if bot_account_id:
                q = q.where(models.WeChatILinkBinding.bot_account_id == bot_account_id)
            row = (await db.execute(q.order_by(models.WeChatILinkMessage.id.desc()).limit(1))).scalars().first()
        if row is None:
            return {"ok": False, "code": "not_found"}
        if row.direction != "out":
            return {"ok": False, "code": "not_out"}
        # 仅当已乐观标记 sent_by_gateway 且收到明确失败才回改为 failed；deferred 本就未下发，保持不动。
        if ok is False and row.status == "sent_by_gateway":
            row.status = "failed"
            await db.commit()
        return {"ok": True, "out_row_id": row.id, "status": row.status}
