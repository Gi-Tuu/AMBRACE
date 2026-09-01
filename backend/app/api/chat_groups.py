"""家庭群聊 API（Phase 2）——F5 瘦身后只留 FastAPI 壳：收参 → 调 application 服务 → 返回。

业务体在 app/application/chat_groups.py（F5-a，2026-08-31 迁入）；本文件保留路由与
参数依赖注入，并对历史顶层名字做门面重导出保旧 import 路径兼容（F8 删旧时移除）。
"""
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.application import chat_groups as _svc
from app.application.chat_groups import (  # F5 门面重导出（历史名字保兼容，F8 删旧时移除）
    MAX_GROUP_SPEAKERS,  # noqa: F401
    MAX_MEMBERS,  # noqa: F401
    MIN_MEMBERS,  # noqa: F401
    _GAME_ALIASES,  # noqa: F401
    _generate_replies,  # noqa: F401
    _generate_replies_runtime,  # noqa: F401
    _group_active_chars,  # noqa: F401
    _handle_play_command,  # noqa: F401
    _heuristic_talkativeness,  # noqa: F401
    _member_names,  # noqa: F401
    _owned_group,  # noqa: F401
    _parse_at_names,  # noqa: F401
    _play_reply,  # noqa: F401
    _save_group_memory,  # noqa: F401
    _select_speakers,  # noqa: F401
    _state_line,  # noqa: F401
    _talkativeness_score,  # noqa: F401
    _trace_group_reply,  # noqa: F401
    build_group_memory_entries,  # noqa: F401
)
from app.auth.deps import get_current_user_id
from app.db.database import get_db

router = APIRouter(prefix="/api/v1/chat-groups", tags=["Chat Groups"])


@router.post("")
async def create_group(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """创建家庭群聊（name + character_ids，至少 2 个本人角色）"""
    return await _svc.create_group(db, data, user_id, lang)


@router.get("")
async def list_groups(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """群列表（含成员角色名）"""
    return await _svc.list_groups(db, user_id)


@router.post("/{group_id}/members")
async def add_members(
    group_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """拉群：把角色加进群（仅本人角色，去重，最多 MAX_MEMBERS 人）"""
    return await _svc.add_members(db, group_id, data, user_id, lang)


@router.delete("/{group_id}/members/{character_id}")
async def remove_member(
    group_id: int,
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """移除角色（群至少保留 2 个成员）"""
    return await _svc.remove_member(db, group_id, character_id, user_id, lang)


@router.put("/{group_id}/members/{character_id}")
async def update_member_mute(
    group_id: int,
    character_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """成员静音/取消静音（muted 布尔，L1 群控，2026-08-25；静音不参与自动选择，被 @ 仍强制回）"""
    return await _svc.update_member_mute(db, group_id, character_id, data, user_id, lang)


@router.delete("/{group_id}")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除群（级联成员与消息）"""
    return await _svc.delete_group(db, group_id, user_id, lang)


@router.get("/mentions")
async def list_mentions(
    after_id: int = 0,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """@我的才弹：返回用户 @ 角色后该角色的回应（notify_user=1 且 id>after_id）"""
    return await _svc.list_mentions(db, user_id, after_id)


@router.get("/{group_id}/messages")
async def list_messages(
    group_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """群消息（时间正序）"""
    return await _svc.list_messages(db, group_id, user_id, lang, limit)


@router.post("/{group_id}/messages")
async def send_message(
    group_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """用户发言 → 落库 → 生成多角色回应（单次 LLM）"""
    return await _svc.send_message(db, group_id, data, user_id, lang)
