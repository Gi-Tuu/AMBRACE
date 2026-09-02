"""织库卡片生成：LLM 将重要记忆整理编排成卡片（标题/概要/详情），幂等落库。

2026-08-12 织库·全景记忆 v1 + 增强：
- 每张卡聚合 ≤20 条记忆（importance≥60 的未入卡记忆）
- content_hash = 参与记忆 id 有序集合的 sha256 → 幂等（同集合不重复生成）
- 详情 detail 为结构化 JSON（time/weather/location/mood/events/details）
- 全局生成按时间窗聚类（7 天），支持跨角色合并成一张卡（weave_card_characters）
- 元数据补全：LLM 编排时注入用户位置/当天天气，location/weather 填得更准
"""
import hashlib
import json
from app.utils.logger import get_logger
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.agent.llm_client import chat_completion
from app.db.database import async_session_factory
from app.memory.embedding import text_embedding
from app.models.memory import Memory
from app.models.memory import WeaveCard, WeaveCardCharacter, WeaveCardMemory

_logger = get_logger("weave.card_generator")

WEAVE_MIN_IMPORTANCE = 60.0  # 入卡门槛：importance 百分比 ≥60（3 星）
BATCH_SIZE = 20  # 每张卡最多聚合的记忆条数
MAX_CARDS_PER_RUN = 10  # 单次手动整理最多生成卡数（防超时）
CLUSTER_WINDOW_DAYS = 7  # 跨角色合并：同一时间窗内的记忆聚为一批
DEFAULT_DETAIL = {"time": "不详", "weather": "不详", "location": "不详", "mood": "不详", "events": [], "details": []}


def _content_hash(memory_ids: list[int]) -> str:
    """参与记忆 id 有序集合的 sha256（幂等键）"""
    return hashlib.sha256(":".join(str(i) for i in sorted(memory_ids)).encode("utf-8")).hexdigest()


def _cluster_by_time(cands: list[Memory]) -> list[list[Memory]]:
    """按创建时间贪心聚类（7 天窗口，可跨角色），每簇 ≤BATCH_SIZE"""
    ordered = sorted(cands, key=lambda m: m.created_at or datetime.min)
    clusters: list[list[Memory]] = []
    cur: list[Memory] = []
    win_start: datetime | None = None
    for m in ordered:
        ts = m.created_at or datetime.min
        if win_start is not None and ts - win_start > timedelta(days=CLUSTER_WINDOW_DAYS):
            clusters.append(cur)
            cur = []
            win_start = None
        if win_start is None:
            win_start = ts
        cur.append(m)
        if len(cur) >= BATCH_SIZE:
            clusters.append(cur)
            cur = []
            win_start = None
    if cur:
        clusters.append(cur)
    # 批次内对话原文优先（卡片主材料在前，其余来源作为参考）
    for c in clusters:
        c.sort(key=lambda m: (0 if m.source == "chat" else 1, -(m.importance or 0)))
    return clusters


def _clean_llm_json(text: str) -> dict:
    """清洗 LLM 输出为 dict（容忍 markdown 代码块围栏与前后缀文本）"""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
        if t.lower().startswith("json"):
            t = t[4:].strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出中未找到 JSON 对象")
    data = json.loads(t[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是对象")
    return data


def _normalize_card(data: dict) -> tuple[str, str, dict]:
    """规范化卡片字段（标题/概要/详情），缺失字段兜底"""
    title = str(data.get("title") or "").strip()
    summary = str(data.get("summary") or "").strip()
    if not title:
        raise ValueError("标题为空")
    if not summary:
        raise ValueError("概要为空")
    raw = data.get("detail")
    detail = dict(DEFAULT_DETAIL)
    if isinstance(raw, dict):
        for k in detail:
            v = raw.get(k)
            if k in ("events", "details"):
                detail[k] = [str(x).strip() for x in v if str(x).strip()][:6] if isinstance(v, list) else []
            elif v is not None and str(v).strip():
                detail[k] = str(v).strip()[:100]
    return title, summary, detail


def _prompt(character_name: str, memories: list[Memory], weather_line: str = "") -> list[dict]:
    lines = []
    for i, m in enumerate(memories, 1):
        created = m.created_at.strftime("%Y-%m-%d") if m.created_at else ""
        src = "对话原文" if m.source == "chat" else (m.source or "其他来源")
        lines.append(f"{i}. [{created}] [{src}] (重要度 {float(m.importance or 0):.0f}) {m.content[:300]}")
    text = "\n".join(lines)
    env_hint = (f"\n（环境参考：{weather_line}）记忆片段中若提到时间/地点/天气请优先采用；没有则填 不详。"
               if weather_line else "")
    system = (
        "你是记忆整理师，负责把零散记忆片段整理成结构化的「织库卡片」。\n"
        "只输出 JSON，不要任何解释或 markdown 代码块。"
    )
    user = (
        f"下面是与「{character_name}」相关的 {len(memories)} 条记忆片段，请整理成 1 张织库卡片：\n\n"
        f"{text}\n\n"
        "要求：\n"
        "1. title：不超过 12 字的标题概括这段记忆\n"
        "2. summary：2-3 句话概述（用于卡片概要展示，60 字内）\n"
        "3. detail 为 JSON 对象，含：time（时间，如 2026-08-10 下午，不确定写 不详）、"
        "weather（天气，无则 不详）、location（地点，无则 不详）、mood（心情，如 开心/难过，可逗号分隔）、"
        "events（事件列表 1-5 条，每条 15 字内）、details（细节补充 1-5 条，每条 30 字内）\n"
        "4. 输出格式：{\"title\": \"...\", \"summary\": \"...\", \"detail\": {\"time\": \"...\", "
        "\"weather\": \"...\", \"location\": \"...\", \"mood\": \"...\", \"events\": [\"...\"], \"details\": [\"...\"]}}\n"
        "5. 记忆之间矛盾或重复时，保留更完整、重要度更高的一条信息。\n"
        "6. 记忆片段可能来自多个角色，合并为一张卡时按“共同经历/同一件事”整合。\n"
        "7. 卡片内容以【对话原文】为主（角色与用户的真实对话），日记/朋友圈/状态等其他来源仅作补充参考，不要喧宾夺主。\n"
        "8. summary/detail 中禁止使用'今天/昨天/最近'等相对时间词；事件时间按记忆片段的 [日期] 标注写具体日期，不确定写 不详。"
    ) + env_hint
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _embed_text(text: str) -> str | None:
    """卡片向量（bge-m3）→ JSON 字符串存库；失败返回 None（不影响主流程）"""
    try:
        vec = await text_embedding(text)
        return json.dumps(vec)
    except Exception as e:
        _logger.warning("weave card embedding failed: %s", e)
        return None


async def _create_card(user_id: int, memories: list[Memory], domain: str = "shared") -> dict | None:
    """对一批记忆生成 1 张卡（可跨角色）；LLM 失败重试 1 次，仍失败返回 None"""
    char_counts = Counter(m.character_id for m in memories)
    main_cid = char_counts.most_common(1)[0][0]
    char_ids = sorted(char_counts)
    names: dict[int, str] = {}
    try:
        from app.models.character import AICharacter

        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(char_ids))
                )
            ).all()
            names = {r[0]: r[1] for r in rows}
    except Exception:
        pass
    name_parts = [names.get(c, f"角色{c}") for c in char_ids]
    if len(name_parts) > 2:
        character_name = "、".join(name_parts[:2]) + f" 等{len(name_parts)}位伙伴"
    else:
        character_name = "、".join(name_parts)
    # 元数据补全（2026-08-12）：注入用户位置/当天天气，让 LLM 填 location/weather 更准
    weather_line = ""
    try:
        from app.application.weather_service import get_user_weather_line

        weather_line = (await get_user_weather_line(user_id) or "").strip()
    except Exception:
        pass
    messages = _prompt(character_name, memories, weather_line)
    last_err = None
    for attempt in range(2):
        try:
            raw = await chat_completion(messages, temperature=0.3, max_tokens=800,
                                        task="card", user_id=user_id)
            data = _clean_llm_json(raw)
            title, summary, detail = _normalize_card(data)
            break
        except Exception as e:
            last_err = e
            _logger.warning("weave LLM card attempt %d failed: %s", attempt + 1, e)
    else:
        _logger.error("weave LLM card failed twice: %s", last_err)
        return None

    emb = await _embed_text(f"{title}：{summary}")
    ids = [m.id for m in memories]
    card = WeaveCard(
        user_id=user_id,
        domain=domain,
        character_id=main_cid,
        title=title,
        summary=summary,
        detail=json.dumps(detail, ensure_ascii=False),
        importance=round(float(sum(m.importance or 0 for m in memories)) / len(memories), 1),
        content_hash=_content_hash(ids),
        embedding=emb,
    )
    async with async_session_factory() as db:
        db.add(card)
        await db.flush()
        for mid in ids:
            db.add(WeaveCardMemory(card_id=card.id, memory_id=mid))
        for cid in char_ids:
            db.add(WeaveCardCharacter(card_id=card.id, character_id=cid))
        await db.commit()
    return {"card_id": card.id, "title": title}


async def _collect_candidates(user_id: int, character_id: int | None, domain: str = "shared") -> dict[int, list[Memory]]:
    """取未入卡的候选记忆（importance≥60、未归档、未删除），按角色分组；domain=private 只取 AI 生活（source=life）"""
    async with async_session_factory() as db:
        used_rows = await db.execute(select(WeaveCardMemory.memory_id))
        used = {r[0] for r in used_rows.all()}
        q = select(Memory).where(
            Memory.user_id == user_id,
            Memory.is_archived.is_(False),
            Memory.delete_at.is_(None),
            Memory.importance >= WEAVE_MIN_IMPORTANCE,
        )
        if domain == "private":
            q = q.where(Memory.source == "life")
        else:
            q = q.where(Memory.source != "life")
        if character_id is not None:
            q = q.where(Memory.character_id == character_id)
        # 对话原文（source=chat）优先作为卡片主材料，其余来源（日记/朋友圈/状态等）作补充
        from sqlalchemy import case
        q = q.order_by(
            case((Memory.source == "chat", 0), else_=1),
            Memory.importance.desc(),
            Memory.created_at.desc(),
        )
        rows = await db.execute(q)
        cands = [m for m in rows.scalars().all() if m.id not in used]
    grouped: dict[int, list[Memory]] = {}
    for m in cands:
        grouped.setdefault(m.character_id, []).append(m)
    return grouped


async def _drop_stale_cards(user_id: int) -> None:
    """懒重建：删除 is_stale 卡片及其关联（参与记忆重新纳入候选）"""
    async with async_session_factory() as db:
        stale_ids = (
            await db.execute(
                select(WeaveCard.id).where(WeaveCard.user_id == user_id, WeaveCard.is_stale.is_(True))
            )
        ).scalars().all()
        if not stale_ids:
            return
        await db.execute(delete(WeaveCardMemory).where(WeaveCardMemory.card_id.in_(stale_ids)))
        await db.execute(delete(WeaveCardCharacter).where(WeaveCardCharacter.card_id.in_(stale_ids)))
        await db.execute(delete(WeaveCard).where(WeaveCard.id.in_(stale_ids)))
        await db.commit()
        _logger.info("weave stale cards dropped: %d", len(stale_ids))


async def generate_cards(
    user_id: int,
    character_id: int | None = None,
    force: bool = False,
    max_cards: int | None = None,
    domain: str = "shared",
) -> dict:
    """手动/增量整理：生成织库卡片。

    指定角色 = 该角色记忆分批；全局 = 时间窗聚类（可跨角色合并成一张卡）。

    Returns: {"created": int, "updated": int, "skipped": int, "token_estimate": int}
    """
    await _drop_stale_cards(user_id)
    grouped = await _collect_candidates(user_id, character_id, domain)
    cap = max_cards or MAX_CARDS_PER_RUN
    if character_id is not None:
        batches: list[list[Memory]] = []
        for cid in sorted(grouped, key=lambda c: -max(m.importance or 0 for m in grouped[c])):
            cands = grouped[cid]
            for i in range(0, len(cands), BATCH_SIZE):
                batches.append(cands[i : i + BATCH_SIZE])
    else:
        all_cands = [m for cands in grouped.values() for m in cands]
        batches = _cluster_by_time(all_cands)
        batches.sort(key=lambda b: -max(m.importance or 0 for m in b))
    created = updated = skipped = 0
    token_estimate = 0
    for batch in batches:
        if created >= cap:
            break
        h = _content_hash([m.id for m in batch])
        if force:
            async with async_session_factory() as db:
                old = (
                    await db.execute(
                        select(WeaveCard).where(WeaveCard.user_id == user_id, WeaveCard.domain == domain,
                                                WeaveCard.content_hash == h)
                    )
                ).scalar_one_or_none()
                if old is not None:
                    await db.execute(delete(WeaveCardMemory).where(WeaveCardMemory.card_id == old.id))
                    await db.execute(delete(WeaveCardCharacter).where(WeaveCardCharacter.card_id == old.id))
                    await db.execute(delete(WeaveCard).where(WeaveCard.id == old.id))
                    await db.commit()
                    updated += 1
        else:
            async with async_session_factory() as db:
                exist = (
                    await db.execute(
                        select(WeaveCard).where(WeaveCard.user_id == user_id, WeaveCard.domain == domain,
                                                WeaveCard.content_hash == h)
                    )
                ).scalar_one_or_none()
            if exist is not None:
                skipped += 1
                continue
        token_estimate += sum(len(m.content or "") for m in batch) // 2 + 200
        card = await _create_card(user_id, batch, domain)
        if card is None:
            skipped += 1
            continue
        created += 1
    return {"created": created, "updated": updated, "skipped": skipped, "token_estimate": token_estimate}
