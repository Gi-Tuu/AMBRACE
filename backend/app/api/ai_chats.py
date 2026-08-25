"""AI 间私聊 API（Phase 1 只读展示）：按用户隔离返回最近 AI-AI 对话记录"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.db.database import get_db
from app.models.ai_chat import AIChat
from app.models.character import AICharacter

router = APIRouter(prefix="/api/v1/ai-chats", tags=["AI Chats"])


@router.get("")
async def list_ai_chats(
    limit: int = 100,
    char_a: int | None = None,
    char_b: int | None = None,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """该用户最近的 AI-AI 私聊记录（最新在前，limit 上限 500；char_a/char_b 可按角色对过滤，供畅聊聊天记录箱用）"""
    limit = max(1, min(limit, 500))
    stmt = select(AIChat).where(AIChat.user_id == user_id)
    if char_a is not None and char_b is not None:
        stmt = stmt.where(
            ((AIChat.character_a_id == char_a) & (AIChat.character_b_id == char_b))
            | ((AIChat.character_a_id == char_b) & (AIChat.character_b_id == char_a))
        )
    result = await db.execute(stmt.order_by(AIChat.id.desc()).limit(limit))
    rows = list(reversed(result.scalars().all()))  # 时间正序返回

    char_ids = set()
    for r in rows:
        char_ids.add(r.character_a_id)
        char_ids.add(r.character_b_id)
        char_ids.add(r.speaker_id)
    names: dict[int, str] = {}
    if char_ids:
        cr = await db.execute(
            select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(char_ids))
        )
        names = {cid: cname for cid, cname in cr.all()}

    items = [
        {
            "id": r.id,
            "character_a_id": r.character_a_id,
            "character_a_name": names.get(r.character_a_id, f"角色{r.character_a_id}"),
            "character_b_id": r.character_b_id,
            "character_b_name": names.get(r.character_b_id, f"角色{r.character_b_id}"),
            "speaker_id": r.speaker_id,
            "speaker_name": names.get(r.speaker_id, f"角色{r.speaker_id}"),
            "round_seq": r.round_seq,
            "content": r.content,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}
