# -*- coding: utf-8 -*-
"""用户多 LLM 配置服务（#68 账号体系 × API 配置整合 P0）。

- CRUD（list/create/update/delete/get）：同一用户 is_default 至多一个（设新默认自动清其他）。
- share 开关：shared_with_subs 标记该配置可共享给子账号。
- 安全：list/get 对 api_key 一律脱敏；子账号不可见不可改主账号配置（共享配置仅只读）。
- 删除配置时把引用该配置的角色 ai_characters.user_llm_config_id 自动置 NULL。
- 解析链辅助（供 app.agent.llm_client 使用）：角色绑定 / 用户默认 / 主账号共享默认。
"""
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import UserLlmConfig
from app.models.character import AICharacter
from app.services.family_service import get_family_root_id, is_sub_account


def mask_api_key(api_key: str | None) -> str:
    """脱敏 api_key：返回 sk-...abcd 形式；空则空串。"""
    if not api_key:
        return ""
    k = str(api_key).strip()
    if not k:
        return ""
    if len(k) <= 8:
        return f"{k[0] if k else ''}***"
    return f"{k[:5]}...{k[-4:]}"


def _serialize_config(cfg: UserLlmConfig, *, is_shared: bool = False) -> dict:
    return {
        "id": cfg.id,
        "user_id": cfg.user_id,
        "name": cfg.name,
        "base_url": cfg.base_url,
        "api_key": "" if is_shared else mask_api_key(cfg.api_key),
        "model": cfg.model,
        "provider": getattr(cfg, "provider", None),
        "enabled": bool(cfg.enabled),
        "is_default": bool(cfg.is_default),
        "shared_with_subs": bool(cfg.shared_with_subs),
        "has_api_key": bool(cfg.api_key),
        "is_shared": bool(is_shared),
        "created_at": cfg.created_at.isoformat() if cfg.created_at else None,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


def _validate_base_url(base_url: str | None) -> str | None:
    """校验 base_url 协议（防 file:// 等 SSRF）；空返回 None。"""
    if not base_url:
        return None
    b = str(base_url).strip()
    if not b:
        return None
    if not (b.startswith("http://") or b.startswith("https://")):
        raise HTTPException(status_code=400, detail="base_url must be http(s)")
    return b


def _norm(v, maxlen: int) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s[:maxlen] or None


# ── 家庭（主/子账号）关系辅助（P0-P2 仅用于共享配置判定；P3 受邀码关联完整启用）──
# _family_root_id / _is_sub_account 已迁至 app.services.family_service，此处统一 import。

async def _can_access(db: AsyncSession, acting_user_id: int | None, cfg: UserLlmConfig) -> bool:
    """判断 acting_user_id 是否有权使用 cfg：本人 或（主账号共享 → 其子账号只读）。"""
    if not acting_user_id:
        return False
    if cfg.user_id == acting_user_id:
        return True
    # 子账号访问主账号共享配置：acting_user 的根 == cfg.user_id 且 cfg.shared_with_subs
    if cfg.shared_with_subs:
        root = await get_family_root_id(db, acting_user_id)
        if root == cfg.user_id:
            return True
    return False


async def ensure_bindable(db: AsyncSession, user_id: int | None, config_id: int | None):
    """校验 user 是否可将角色绑定到指定 LLM 配置：本人 或（主账号共享 → 其子账号）。返回配置。"""
    if config_id is None:
        return None
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail="invalid llm config")
    if not await _can_access(db, user_id, cfg):
        raise HTTPException(status_code=403, detail="llm config forbidden")
    return cfg


# ── 解析链辅助（供 llm_client._resolve_llm_config 调用；无 db 时自开会话）──

async def _with_db(db: AsyncSession | None):
    from app.db.database import async_session_factory
    if db is not None:
        yield db
        return
    async with async_session_factory() as _own:
        yield _own


async def resolve_character_llm_config(character_id: int | None, user_id: int | None,
                                       db: AsyncSession | None = None) -> dict | None:
    """角色绑定配置：ai_characters.user_llm_config_id 指向的启用配置。

    校验：配置属于角色归属用户，或（子账号）指向主账号共享配置；否则视为未绑定。
    """
    if not character_id:
        return None
    async for session in _with_db(db):
        char = (await session.execute(
            select(AICharacter).where(AICharacter.id == character_id)
        )).scalar_one_or_none()
        if char is None or not char.user_llm_config_id:
            return None
        cfg = (await session.execute(
            select(UserLlmConfig).where(UserLlmConfig.id == char.user_llm_config_id)
        )).scalar_one_or_none()
        if cfg is None or not cfg.enabled or not (cfg.base_url or cfg.api_key):
            return None
        # 归属校验：角色归属用户 == 配置 owner；子账号也可用主账号共享配置
        owner = await get_family_root_id(session, char.user_id)
        if cfg.user_id != char.user_id and not (cfg.shared_with_subs and owner == cfg.user_id):
            return None
        return {
            "base_url": cfg.base_url, "api_key": cfg.api_key,
            "model": cfg.model, "provider": getattr(cfg, "provider", None),
            "config_id": cfg.id,
        }


async def resolve_user_default_config(user_id: int | None, db: AsyncSession | None = None) -> dict | None:
    """用户默认配置：user_id 的 is_default=True 且 enabled 配置。"""
    if not user_id:
        return None
    async for session in _with_db(db):
        cfg = (await session.execute(
            select(UserLlmConfig).where(
                UserLlmConfig.user_id == user_id,
                UserLlmConfig.is_default == True,  # noqa: E712
                UserLlmConfig.enabled == True,  # noqa: E712
            )
        )).scalar_one_or_none()
        if cfg is None or not (cfg.base_url or cfg.api_key):
            return None
        return {
            "base_url": cfg.base_url, "api_key": cfg.api_key,
            "model": cfg.model, "provider": getattr(cfg, "provider", None),
            "config_id": cfg.id,
        }


async def resolve_family_default_config(user_id: int | None, db: AsyncSession | None = None) -> dict | None:
    """主账号共享默认（仅子账号）：子账号的根主账号 is_default+enabled 且 shared_with_subs 的配置。"""
    if not user_id:
        return None
    async for session in _with_db(db):
        root = await get_family_root_id(session, user_id)
        if root is None or root == int(user_id):  # 独立主账号：用户默认即家庭默认，已由上层处理
            return None
        cfg = (await session.execute(
            select(UserLlmConfig).where(
                UserLlmConfig.user_id == root,
                UserLlmConfig.is_default == True,  # noqa: E712
                UserLlmConfig.enabled == True,  # noqa: E712
                UserLlmConfig.shared_with_subs == True,  # noqa: E712
            )
        )).scalar_one_or_none()
        if cfg is None or not (cfg.base_url or cfg.api_key):
            return None
        return {
            "base_url": cfg.base_url, "api_key": cfg.api_key,
            "model": cfg.model, "provider": getattr(cfg, "provider", None),
            "config_id": cfg.id,
        }


# ── CRUD（API 用；接受显式 db 会话）──

async def list_configs(db: AsyncSession, user_id: int) -> dict:
    """我的配置列表；子账号额外返回主账号共享配置（只读，api_key 不泄）。"""
    mine = (await db.execute(
        select(UserLlmConfig).where(UserLlmConfig.user_id == user_id)
        .order_by(UserLlmConfig.id.desc())
    )).scalars().all()
    items = [_serialize_config(c, is_shared=False) for c in mine]
    if await is_sub_account(db, user_id):
        root = await get_family_root_id(db, user_id)
        if root and root != user_id:
            shared = (await db.execute(
                select(UserLlmConfig).where(
                    UserLlmConfig.user_id == root,
                    UserLlmConfig.shared_with_subs == True,  # noqa: E712
                ).order_by(UserLlmConfig.id.desc())
            )).scalars().all()
            for c in shared:
                items.append(_serialize_config(c, is_shared=True))
    # 把共享配置排到最前（只读区，便于前端区分）
    items.sort(key=lambda x: (0 if x["is_shared"] else 1, x["id"]))
    return {"items": items}


async def create_config(db: AsyncSession, user_id: int, data: dict) -> dict:
    name = _norm(data.get("name"), 100)
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    base_url = _validate_base_url(_norm(data.get("base_url"), 255))
    api_key = _norm(data.get("api_key"), 500)
    model = _norm(data.get("model"), 80)
    provider = _norm(data.get("provider"), 30)
    enabled = bool(data.get("enabled", True))
    is_default = bool(data.get("is_default", False))
    # 排重：UNIQUE(user_id, name)
    exists = (await db.execute(
        select(UserLlmConfig.id).where(UserLlmConfig.user_id == user_id, UserLlmConfig.name == name)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="name already exists")
    cfg = UserLlmConfig(
        user_id=user_id, name=name, base_url=base_url, api_key=api_key,
        model=model, provider=provider, enabled=enabled, is_default=is_default,
        shared_with_subs=bool(data.get("shared_with_subs", False)),
    )
    db.add(cfg)
    await db.flush()
    if is_default:
        await _clear_other_defaults(db, user_id, cfg.id)
    await db.refresh(cfg)
    return _serialize_config(cfg)


async def get_config(db: AsyncSession, config_id: int, user_id: int) -> dict:
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if not await _can_access(db, user_id, cfg):
        raise HTTPException(status_code=403, detail="forbidden")
    is_shared = cfg.user_id != user_id
    return _serialize_config(cfg, is_shared=is_shared)


async def update_config(db: AsyncSession, config_id: int, user_id: int, data: dict) -> dict:
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if cfg.user_id != user_id:
        # 子账号对主账号共享配置禁止修改
        raise HTTPException(status_code=403, detail="shared config read-only")
    if "name" in data:
        name = _norm(data.get("name"), 100)
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        dup = (await db.execute(
            select(UserLlmConfig.id).where(
                UserLlmConfig.user_id == user_id, UserLlmConfig.name == name,
                UserLlmConfig.id != config_id,
            )
        )).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="name already exists")
        cfg.name = name
    if "base_url" in data:
        cfg.base_url = _validate_base_url(_norm(data.get("base_url"), 255))
    if "api_key" in data:
        v = _norm(data.get("api_key"), 500)
        # 前端回传脱敏串或空串时视为未改动（保留原 Key）
        if v:
            cfg.api_key = v
    if "model" in data:
        cfg.model = _norm(data.get("model"), 80)
    if "provider" in data:
        cfg.provider = _norm(data.get("provider"), 30)
    if "enabled" in data:
        cfg.enabled = bool(data.get("enabled"))
    if "is_default" in data:
        cfg.is_default = bool(data.get("is_default"))
        if cfg.is_default:
            await _clear_other_defaults(db, user_id, cfg.id)
    if "shared_with_subs" in data:
        cfg.shared_with_subs = bool(data.get("shared_with_subs"))
    await db.flush()
    await db.refresh(cfg)
    return _serialize_config(cfg)


async def delete_config(db: AsyncSession, config_id: int, user_id: int) -> None:
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if cfg.user_id != user_id:
        raise HTTPException(status_code=403, detail="shared config read-only")
    # 引用该配置的角色自动置 NULL
    await db.execute(
        update(AICharacter)
        .where(AICharacter.user_llm_config_id == config_id)
        .values(user_llm_config_id=None)
    )
    await db.delete(cfg)
    await db.flush()


async def set_default(db: AsyncSession, config_id: int, user_id: int) -> dict:
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if cfg.user_id != user_id:
        raise HTTPException(status_code=403, detail="shared config read-only")
    await _clear_other_defaults(db, user_id, cfg.id)
    cfg.is_default = True
    await db.flush()
    await db.refresh(cfg)
    return _serialize_config(cfg)


async def set_share(db: AsyncSession, config_id: int, user_id: int, shared: bool) -> dict:
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if cfg.user_id != user_id:
        raise HTTPException(status_code=403, detail="shared config read-only")
    cfg.shared_with_subs = bool(shared)
    await db.flush()
    await db.refresh(cfg)
    return _serialize_config(cfg)


async def _clear_other_defaults(db: AsyncSession, user_id: int, keep_id: int) -> None:
    await db.execute(
        update(UserLlmConfig)
        .where(UserLlmConfig.user_id == user_id, UserLlmConfig.id != keep_id)
        .values(is_default=False)
    )


async def test_config(db: AsyncSession, config_id: int, user_id: int) -> dict:
    """最小连接测试：返回 ok/耗时/命中 Key 尾号。"""
    cfg = await db.get(UserLlmConfig, config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="config not found")
    if not await _can_access(db, user_id, cfg):
        raise HTTPException(status_code=403, detail="forbidden")
    return await _run_test(base_url=cfg.base_url, api_key=cfg.api_key,
                           model=cfg.model, provider=getattr(cfg, "provider", None))


async def _run_test(base_url: str | None, api_key: str | None, model: str | None,
                    provider: str | None) -> dict:
    """执行最小连接测试（与 system.api-config/test 一致的口径）。"""
    import asyncio
    import time
    from app.agent.llm_client import get_llm_client, _split_api_keys

    base_url = (base_url or "").strip()
    api_key = (api_key or "").strip()
    model = (model or "").strip() or None
    keys = _split_api_keys(api_key)
    if not keys:
        return {"ok": False, "error": "未配置 API Key"}
    if not base_url:
        return {"ok": False, "error": "未配置 Base URL"}
    last_err = "连接失败"
    for k in keys:
        cli = get_llm_client(api_key=k, base_url=base_url)
        _t0 = time.monotonic()
        try:
            if model:
                await asyncio.wait_for(cli.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                ), timeout=15.0)
            else:
                await asyncio.wait_for(cli.models.list(), timeout=15.0)
            latency_ms = int((time.monotonic() - _t0) * 1000)
            tail = (k[-4:] if k else "")
            return {"ok": True, "latency_ms": latency_ms, "model": model, "api_key_tail": tail}
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:200]
    return {"ok": False, "error": last_err}
