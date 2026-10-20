# -*- coding: utf-8 -*-
"""角色创建幂等（M1，2026-10-19）：把「一次创建意图」收敛为「至多一个角色」。

契约：
- 不带 ``Idempotency-Key`` ⇒ 走旧路径，每次可新建（手机端不受影响）；
- 同用户 + 同键 + 同规范请求（重复/并发）⇒ 返回**同一个**角色，库里只 1 角色 + 1 收据；
- 同用户 + 同键 + 不同规范请求 ⇒ 409，不返回错误角色、不新建；
- 同键不同用户相互独立；
- 收据目标角色已删除 ⇒ 410，不重建、不回放已删除对象；
- 全部写操作在**同一事务**内：收据与角色一起提交或一起回滚（不提前 commit）。
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.characters import create_character
from app.models.character import AICharacter
from app.models.character.creation_request import CharacterCreationRequest
from app.schemas.character import CharacterCreate

MAX_KEY_LEN = 128


def validate_idempotency_key(raw: str) -> str:
    """键必须非空、≤128、且全部是可打印 ASCII 安全字符（33–126，不含空格）。"""
    key = raw or ""
    if not key or len(key) > MAX_KEY_LEN or any(not (33 <= ord(ch) <= 126) for ch in key):
        raise HTTPException(status_code=422, detail="invalid_idempotency_key")
    return key


def canonical_payload_digest(data: CharacterCreate) -> str:
    """Pydantic 校验后的**有效字段**规范化摘要（含完整 CharacterCreate 契约，非仅 10 字段）。"""
    payload = data.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _replay(
    db: AsyncSession, user_id: int, key: str, digest: str
) -> AICharacter:
    """键已被占用：按收据判定「同请求重放 / 冲突 / 已删除 / 仍在进行」。"""
    row = (await db.execute(
        select(CharacterCreationRequest).where(
            CharacterCreationRequest.user_id == user_id,
            CharacterCreationRequest.idempotency_key == key,
        )
    )).scalar_one_or_none()
    if row is None:
        # 极端竞争窗口（对方回滚）：让调用方重试，绝不静默新建
        raise HTTPException(status_code=409, detail="creation_in_progress")
    if row.payload_digest != digest:
        raise HTTPException(status_code=409, detail="idempotency_key_payload_mismatch")
    if row.character_id is None:
        if row.state == "pending":
            raise HTTPException(status_code=409, detail="creation_in_progress")
        raise HTTPException(status_code=410, detail="creation_target_deleted")
    character = await db.get(AICharacter, row.character_id)
    if character is None or character.user_id != user_id:
        # 角色已被删除（或不属于本人）：键不复活、不回放已删除对象
        raise HTTPException(status_code=410, detail="creation_target_deleted")
    return character


async def create_character_once(
    db: AsyncSession,
    data: CharacterCreate,
    user_id: int,
    idempotency_key: str,
) -> AICharacter:
    """带幂等键的创建：先原子占键，再在**同一事务**里建角色。"""
    key = validate_idempotency_key(idempotency_key)
    digest = canonical_payload_digest(data)

    # ① 先查（快路径）：已有收据直接按契约回放/拒绝，不产生任何写。
    existing = (await db.execute(
        select(CharacterCreationRequest).where(
            CharacterCreationRequest.user_id == user_id,
            CharacterCreationRequest.idempotency_key == key,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return await _replay(db, user_id, key, digest)

    # ② 同一事务内「占键 + 建角色」：唯一约束是并发最后防线。
    #    本项目先前 begin_nested() 实现的失败回滚测试曾残留收据（实测 9 vs 8）。
    #    此处不依赖保存点启动外层事务；改为 flush 时就近捕获，
    #    冲突则整体回滚（本请求尚无其它写），再用新事务按收据回放。
    receipt = CharacterCreationRequest(
        user_id=user_id,
        idempotency_key=key,
        payload_digest=digest,
        state="pending",
    )
    db.add(receipt)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return await _replay(db, user_id, key, digest)

    # 复用既有业务（ensure_bindable / 系统提示 / 字段口径全不改）；
    # 这里若抛错，外层 get_db 整体回滚 ⇒ 收据与角色都不留痕，键不被消耗。
    character = await create_character(db, data, user_id)
    receipt.character_id = character.id
    receipt.state = "active"
    await db.flush()
    return character
