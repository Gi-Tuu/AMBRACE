# -*- coding: utf-8 -*-
"""用户多 LLM 配置 API（/api/v1/llm-configs，#68 P0）。

- GET / : 我的配置；子账号额外返回主账号共享配置（只读）
- POST / : 创建
- PUT /{id} : 更新（仅本人；子账号对共享配置禁止修改）
- DELETE /{id} : 删除（引用该配置的角色自动置 NULL）
- POST /{id}/default : 设为默认（同用户默认唯一）
- POST /{id}/share : 切换 shared_with_subs
- POST /{id}/test : 最小连接测试（ok/耗时/Key 尾号）
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.application import llm_config_service as _svc

router = APIRouter(prefix="/api/v1/llm-configs", tags=["LLM Configs"])


class LlmConfigUpsert(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    provider: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    shared_with_subs: bool | None = None


class LlmShareBody(BaseModel):
    shared: bool = True


@router.get("")
async def list_llm_configs(db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """我的 LLM 配置列表；子账号额外返回主账号共享配置（只读）。"""
    return await _svc.list_configs(db, user_id)


@router.post("", status_code=201)
async def create_llm_config(data: LlmConfigUpsert, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """创建我的 LLM 配置。"""
    return await _svc.create_config(db, user_id, data.model_dump(exclude_none=True))


@router.get("/{config_id}")
async def get_llm_config(config_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """查看单个 LLM 配置（api_key 脱敏；子账号对共享配置只读且不泄 Key）。"""
    return await _svc.get_config(db, config_id, user_id)


@router.put("/{config_id}")
async def update_llm_config(config_id: int, data: LlmConfigUpsert, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """更新我的 LLM 配置（仅本人；子账号对主账号共享配置返回 403）。"""
    return await _svc.update_config(db, config_id, user_id, data.model_dump(exclude_none=True))


@router.delete("/{config_id}", status_code=204)
async def delete_llm_config(config_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """删除我的 LLM 配置（引用该配置的角色自动置 NULL）。"""
    await _svc.delete_config(db, config_id, user_id)
    return {"ok": True}


@router.post("/{config_id}/default")
async def set_default_llm_config(config_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """设为默认（同一用户默认唯一，设置新默认自动清其他）。"""
    return await _svc.set_default(db, config_id, user_id)


@router.post("/{config_id}/share")
async def set_share_llm_config(config_id: int, data: LlmShareBody, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """切换 shared_with_subs（是否可共享给子账号）。"""
    return await _svc.set_share(db, config_id, user_id, data.shared)


@router.post("/{config_id}/test")
async def test_llm_config(config_id: int, db: AsyncSession = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """最小连接测试：返回 ok/耗时/Key 尾号。"""
    return await _svc.test_config(db, config_id, user_id)
