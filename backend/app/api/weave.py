"""织库 API（2026-08-12）：卡片列表/详情/删除/生成 + 画布图数据"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import func, or_, select

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import async_session_factory
from app.memory.sources import memory_source_meta
from app.models.character import AICharacter
from app.models.memory import Memory
from app.models.weave_card import WeaveCard, WeaveCardCharacter, WeaveCardMemory
from app.schemas.weave import (
    WeaveCardDetailResponse,
    WeaveCardListResponse,
    WeaveCardResponse,
    WeaveDetail,
    WeaveGenerateResponse,
    WeaveGraphResponse,
    WeaveMemoryRef,
)
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/weave", tags=["Weave"])
_logger = get_logger("api.weave")

DEFAULT_DETAIL = {"time": "不详", "weather": "不详", "location": "不详", "mood": "不详", "events": [], "details": []}


def _parse_detail(raw: str | None) -> dict:
    """解析卡片详情 JSON，失败兜底默认值"""
    if not raw:
        return dict(DEFAULT_DETAIL)
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            out = dict(DEFAULT_DETAIL)
            for k in out:
                v = data.get(k)
                if k in ("events", "details"):
                    out[k] = [str(x) for x in v] if isinstance(v, list) else []
                elif v is not None and str(v).strip():
                    out[k] = str(v).strip()
            return out
    except Exception:
        pass
    return dict(DEFAULT_DETAIL)


async def _owned_card(card_id: int, user_id: int):
    async with async_session_factory() as db:
        return (
            await db.execute(select(WeaveCard).where(WeaveCard.id == card_id, WeaveCard.user_id == user_id))
        ).scalar_one_or_none()


@router.get("/cards", response_model=WeaveCardListResponse)
async def list_weave_cards(
    character_id: int | None = None,
    q: str | None = None,
    skip: int = 0,
    limit: int = Query(50, le=200),
    domain: str = "shared",
    user_id: int = Depends(get_current_user_id),
):
    """织库卡片列表（概要级；character_id 可选过滤；domain=shared 全·织库 / private 私·织库）"""
    async with async_session_factory() as db:
        cond = [WeaveCard.user_id == user_id, WeaveCard.domain == domain]
        if character_id is not None:
            # 跨角色合并卡片：主角色匹配 或 关联角色包含
            cond.append(
                or_(
                    WeaveCard.character_id == character_id,
                    WeaveCard.id.in_(
                        select(WeaveCardCharacter.card_id).where(
                            WeaveCardCharacter.character_id == character_id
                        )
                    ),
                )
            )
        if q:
            cond.append(WeaveCard.title.contains(q) | WeaveCard.summary.contains(q))
        total = (
            await db.execute(select(func.count()).select_from(WeaveCard).where(*cond))
        ).scalar() or 0
        cards = (
            await db.execute(
                select(WeaveCard)
                .where(*cond)
                .order_by(WeaveCard.importance.desc(), WeaveCard.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
        ).scalars().all()
        counts: dict[int, int] = {}
        if cards:
            rows = (
                await db.execute(
                    select(WeaveCardMemory.card_id, func.count()).group_by(WeaveCardMemory.card_id)
                )
            ).all()
            counts = {r[0]: r[1] for r in rows}
    return WeaveCardListResponse(
        cards=[
            WeaveCardResponse(
                id=c.id,
                character_id=c.character_id,
                title=c.title,
                summary=c.summary,
                importance=round(float(c.importance or 0), 1),
                memory_count=counts.get(c.id, 0),
                created_at=c.created_at,
            )
            for c in cards
        ],
        total=total,
    )


@router.get("/cards/{card_id}", response_model=WeaveCardDetailResponse)
async def get_weave_card(
    card_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """织库卡片详情（含结构化详情 + 参与记忆清单）"""
    card = await _owned_card(card_id, user_id)
    if card is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "card_not_found"))
    async with async_session_factory() as db:
        # 跨角色合并卡片：角色名取全部参与角色（前 2 个 + 等）
        cc_rows = (
            await db.execute(
                select(WeaveCardCharacter.character_id).where(WeaveCardCharacter.card_id == card.id)
            )
        ).all()
        cids = [card.character_id] + [r[0] for r in cc_rows if r[0] != card.character_id]
        name_parts = []
        for cid in cids:
            _ch = await db.get(AICharacter, cid)
            if _ch:
                name_parts.append(_ch.name)
        character_name = (
            "、".join(name_parts[:2]) + (f" 等{len(name_parts)}" if len(name_parts) > 2 else "")
        )
        rows = (
            await db.execute(
                select(Memory).where(Memory.id.in_(select(WeaveCardMemory.memory_id).where(WeaveCardMemory.card_id == card.id)))
            )
        ).scalars().all()
    memories = []
    for m in rows:
        meta = memory_source_meta(m.source, m.sub_type)
        memories.append(
            WeaveMemoryRef(
                id=m.id,
                memory_type=m.memory_type,
                sub_type=m.sub_type,
                content=m.content,
                importance_pct=round(float(m.importance or 0), 1),
                source_label=meta["label"],
                source_icon=meta["icon"],
                created_at=m.created_at,
            )
        )
    return WeaveCardDetailResponse(
        id=card.id,
        character_id=card.character_id,
        character_name=character_name,
        title=card.title,
        summary=card.summary,
        importance=round(float(card.importance or 0), 1),
        memory_count=len(memories),
        detail=WeaveDetail(**_parse_detail(card.detail)),
        memories=memories,
        created_at=card.created_at,
    )


@router.delete("/cards/{card_id}")
async def delete_weave_card(
    card_id: int,
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除织库卡片（仅删卡片，不删记忆）"""
    card = await _owned_card(card_id, user_id)
    if card is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "card_not_found"))
    async with async_session_factory() as db:
        from sqlalchemy import delete

        await db.execute(delete(WeaveCardCharacter).where(WeaveCardCharacter.card_id == card.id))
        await db.execute(delete(WeaveCardMemory).where(WeaveCardMemory.card_id == card.id))
        await db.execute(delete(WeaveCard).where(WeaveCard.id == card.id))
        await db.commit()
    return {"status": "ok", "deleted": True}


@router.post("/cards/generate", response_model=WeaveGenerateResponse)
async def generate_weave_cards(
    character_id: int | None = None,
    force: bool = False,
    domain: str = "shared",
    user_id: int = Depends(get_current_user_id),
):
    """手动整理：把重要记忆编排成织库卡片（同步执行，LLM 批量 ≤20 条/卡，单次 ≤10 卡；domain=shared/private）"""
    from app.weave.card_generator import generate_cards

    result = await generate_cards(user_id, character_id=character_id, force=force, domain=domain)
    _logger.info("weave generate: user=%d char=%s force=%s domain=%s result=%s", user_id, character_id, force, domain, result)
    return WeaveGenerateResponse(**result)


@router.post("/cards/dedup-check")
async def weave_dedup_check(domain: str = "shared", user_id: int = Depends(get_current_user_id)):
    """织库卡片查重：返回重复组预览（keeper + duplicates），不修改数据"""
    from app.weave.dedup import dedup_check

    return await dedup_check(user_id, domain=domain)


@router.post("/cards/dedup")
async def weave_dedup(domain: str = "shared", user_id: int = Depends(get_current_user_id)):
    """织库卡片去重：每组保留信息最全的一张，删除其余（转移记忆关联，不删原始记忆）"""
    from app.weave.dedup import dedup_execute

    result = await dedup_execute(user_id, domain=domain)
    _logger.info("weave dedup: user=%d domain=%s result=%s", user_id, domain, result)
    return result


@router.get("/graph", response_model=WeaveGraphResponse)
async def weave_graph(
    character_id: int | None = None,
    domain: str = "shared",
    user_id: int = Depends(get_current_user_id),
):
    """画布图数据（节点 + 关联边；坐标由前端计算；domain=shared/private）"""
    from app.weave.graph import build_graph

    return await build_graph(user_id, character_id, domain=domain)
