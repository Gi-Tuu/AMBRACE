"""家庭群聊 API（Phase 2）：群 CRUD + 群消息 + 用户发言多角色回应（方案A：单次调用 JSON 多回应）

- 群聊不推送；记忆按群归属（Phase 2 v1 先落库群消息，记忆写入后续）
- 用户发言 → 存 user 消息 → LLM 生成 1-3 个角色的回应（JSON）→ 逐条落库
"""
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user_id
from app.i18n import tr_lang
from app.db.database import async_session_factory, get_db
from app.models.chat_group import ChatGroup, ChatGroupMember, ChatGroupMessage
from app.models.character import AICharacter
from app.utils.logger import get_logger

router = APIRouter(prefix="/api/v1/chat-groups", tags=["Chat Groups"])
_logger = get_logger("api.chat_groups")

MIN_MEMBERS = 2
MAX_MEMBERS = 8
MAX_GROUP_SPEAKERS = 3  # F1（2026-08-18）：群聊单轮回应角色数上限（被 @ 角色强制回应，可超限不裁）

# 话痨度启发式关键词（L1，2026-08-25）：talkativeness=NULL 时从 personality/chat_style 文本打分。
# 高活跃：外向/活泼/话多/热情…；低活跃：内向/安静/高冷/寡言…；中性/无信息 → 50（保守）。
_TALK_HIGH = (
    "外向", "活泼", "话多", "热情", "开朗", "健谈", "活跃", "自来熟", "元气",
    "爱笑", "话痨", "爱说话", "喜欢分享", "滔滔不绝", "爱聊天", "主动", "积极",
    "热闹", "人来疯", "社牛", "阳光",
)
_TALK_LOW = (
    "内向", "安静", "高冷", "寡言", "沉默", "少言", "害羞", "腼腆", "内敛",
    "冷淡", "文静", "慢热", "低调", "不爱说话", "不爱聊", "惜字如金", "安静寡言",
    "清冷", "社恐", "不爱主动",
)


def _heuristic_talkativeness(personality: str | None, chat_style: str | None) -> int:
    """从 personality/chat_style 文本启发式推断话痨度 0-100（NULL=未设置时用，不落库）。

    规则：外向/活泼/话多/热情 → 高；内向/安静/高冷/寡言 → 低；中性/无信息 → 50（保守）。
    """
    text = " ".join(x for x in [personality or "", chat_style or ""] if x)
    if not text.strip():
        return 50
    high = sum(1 for k in _TALK_HIGH if k in text)
    low = sum(1 for k in _TALK_LOW if k in text)
    delta = high - low
    if delta > 0:
        return min(100, 60 + delta * 20)  # 1 股外向信号→80；2 股→100（强外向=必活跃）
    if delta < 0:
        return max(0, 40 + delta * 20)  # 1 股内向→20；2 股→0
    return 50


def _talkativeness_score(char) -> int:
    """角色有效话痨度：显式 talkativeness 非空用显式（钳到 0-100）；否则按性格/聊天风格启发式推断。"""
    t = getattr(char, "talkativeness", None)
    if t is not None:
        try:
            t = int(t)
        except (TypeError, ValueError):
            t = None
    if t is not None:
        return max(0, min(100, t))
    return _heuristic_talkativeness(getattr(char, "personality", None), getattr(char, "chat_style", None))


def _select_speakers(chars, at_chars, muted_ids=frozenset(), max_speakers: int = MAX_GROUP_SPEAKERS, rng=None) -> list:
    """三层漏斗选回应者（纯函数可测，L1，2026-08-25）：

    ① @ 目标必回（确定性，含静音者被 @ 也强制回，可超限不裁）；
    ② 未 @ 角色按 talkativeness 概率激活（=0 除非被@否则不激活；=100 必激活；之间按概率）；
    ③ 候选为空 → 随机兜底选 1 人（防冷场；静音者不参与自动选择）。
    最终从候选集随机选 ≤max_speakers 人（@ 已占位则用剩余名额填充）。

    返回与旧链路同形态的 [char,...]（顺序即候选顺序；rng 可注入以做确定性单测）。
    """
    rng = rng or random
    at_ids = {c.id for c in at_chars}
    speakers = list(at_chars)  # ① @ 必回（保留旧语义）
    activated: list = []  # ② 未@ 按概率激活
    for c in chars:
        if c.id in at_ids or c.id in muted_ids:
            continue
        score = _talkativeness_score(c)
        if score <= 0:
            continue  # 除非被 @ 否则不激活
        if score >= 100:
            activated.append(c)
        elif rng.random() < score / 100.0:
            activated.append(c)
    remaining = max_speakers - len(speakers)
    if remaining > 0 and activated:
        rng.shuffle(activated)
        speakers.extend(activated[:remaining])
    # ③ 兜底：候选为空（无 @ 且无激活）→ 随机选 1 人防冷场；静音者不参与自动选择
    if not speakers and chars:
        auto = [c for c in chars if c.id not in muted_ids]
        if auto:
            speakers.append(rng.choice(auto))
    return speakers


async def _owned_group(db: AsyncSession, group_id: int, user_id: int, lang: str = "zh") -> ChatGroup:
    g = (
        await db.execute(
            select(ChatGroup).where(ChatGroup.id == group_id, ChatGroup.user_id == user_id)
        )
    ).scalar_one_or_none()
    if g is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "group_not_found"))
    return g


async def _member_names(db: AsyncSession, group_id: int) -> dict[int, str]:
    rows = (
        await db.execute(
            select(ChatGroupMember.character_id)
            .where(ChatGroupMember.group_id == group_id)
        )
    ).scalars().all()
    if not rows:
        return {}
    cr = await db.execute(select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(rows)))
    return {cid: cname for cid, cname in cr.all()}


@router.post("")
async def create_group(
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """创建家庭群聊（name + character_ids，至少 2 个本人角色）"""
    name = str(data.get("name") or "").strip() or "家庭群聊"
    try:
        _raw_ids = data.get("character_ids") or []
        char_ids = [int(x) for x in _raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "invalid_character_id"))
    char_ids = list(dict.fromkeys(cid for cid in char_ids if cid > 0))
    if len(char_ids) < MIN_MEMBERS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_min_two"))
    if len(char_ids) > MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_max_members", max=MAX_MEMBERS))
    # 校验角色归属
    cr = await db.execute(
        select(AICharacter.id).where(AICharacter.id.in_(char_ids), AICharacter.user_id == user_id)
    )
    owned = {row[0] for row in cr.all()}
    if owned != set(char_ids):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "chat_group_foreign_char"))

    group = ChatGroup(user_id=user_id, name=name)
    db.add(group)
    await db.flush()
    for cid in char_ids:
        db.add(ChatGroupMember(group_id=group.id, character_id=cid))
    await db.commit()
    await db.refresh(group)
    return {"id": group.id, "name": group.name}


@router.get("")
async def list_groups(
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """群列表（含成员角色名）"""
    groups = (
        await db.execute(
            select(ChatGroup).where(ChatGroup.user_id == user_id).order_by(ChatGroup.id.desc())
        )
    ).scalars().all()
    items = []
    for g in groups:
        names = await _member_names(db, g.id)
        # L1 群控（2026-08-25）：成员带 muted 供前端气泡显示静音/取消
        mrows = (await db.execute(
            select(ChatGroupMember).where(ChatGroupMember.group_id == g.id)
        )).scalars().all()
        muted_map = {getattr(m, "character_id", m): bool(getattr(m, "muted", False)) for m in mrows}
        items.append({
            "id": g.id,
            "name": g.name,
            "members": [{"id": cid, "name": n, "muted": muted_map.get(cid, False)} for cid, n in names.items()],
            "created_at": g.created_at.isoformat(),
        })
    return {"items": items, "total": len(items)}


@router.post("/{group_id}/members")
async def add_members(
    group_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """拉群：把角色加进群（仅本人角色，去重，最多 MAX_MEMBERS 人）"""
    await _owned_group(db, group_id, user_id, lang)
    try:
        _raw_ids = data.get("character_ids") or []
        char_ids = [int(x) for x in _raw_ids]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "invalid_character_id"))
    char_ids = list(dict.fromkeys(cid for cid in char_ids if cid > 0))
    if not char_ids:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_min_two"))
    cr = await db.execute(
        select(AICharacter.id).where(AICharacter.id.in_(char_ids), AICharacter.user_id == user_id)
    )
    owned = {row[0] for row in cr.all()}
    if owned != set(char_ids):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "chat_group_foreign_char"))
    existing = (await db.execute(
        select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
    )).scalars().all()
    existing_set = set(existing)
    new_ids = [cid for cid in char_ids if cid not in existing_set]
    if not new_ids:
        return {"added": [], "members": len(existing)}
    if len(existing_set) + len(new_ids) > MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_max_members", max=MAX_MEMBERS))
    for cid in new_ids:
        db.add(ChatGroupMember(group_id=group_id, character_id=cid))
    await db.commit()
    return {"added": new_ids, "members": len(existing_set) + len(new_ids)}


@router.delete("/{group_id}/members/{character_id}")
async def remove_member(
    group_id: int,
    character_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """移除角色（群至少保留 2 个成员）"""
    await _owned_group(db, group_id, user_id, lang)
    existing = (await db.execute(
        select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
    )).scalars().all()
    if character_id not in existing:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "group_member_not_found"))
    if len(existing) <= MIN_MEMBERS:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_min_two"))
    await db.execute(
        delete(ChatGroupMember).where(
            ChatGroupMember.group_id == group_id,
            ChatGroupMember.character_id == character_id,
        )
    )
    await db.commit()
    return {"status": "ok", "members": len(existing) - 1}


@router.put("/{group_id}/members/{character_id}")
async def update_member_mute(
    group_id: int,
    character_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """成员设置：静音/取消静音（muted 布尔，L1 群聊群控，2026-08-25）。

    静音角色不参与自动选择（被 @ 仍强制回）。仅本人群可操作；成员不存在 404。
    """
    await _owned_group(db, group_id, user_id, lang)
    member = (await db.execute(
        select(ChatGroupMember).where(
            ChatGroupMember.group_id == group_id,
            ChatGroupMember.character_id == character_id,
        )
    )).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "group_member_not_found"))
    member.muted = data.get("muted") is True
    await db.commit()
    return {"status": "ok", "character_id": character_id, "muted": member.muted}


@router.delete("/{group_id}")
async def delete_group(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """删除群（级联成员与消息）"""
    await _owned_group(db, group_id, user_id, lang)
    await db.execute(delete(ChatGroupMember).where(ChatGroupMember.group_id == group_id))
    await db.execute(delete(ChatGroupMessage).where(ChatGroupMessage.group_id == group_id))
    await db.execute(delete(ChatGroup).where(ChatGroup.id == group_id))
    await db.commit()
    return {"status": "ok"}


@router.get("/mentions")
async def list_mentions(
    after_id: int = 0,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """@我的才弹：返回用户 @ 角色后该角色的回应（notify_user=1 且 id>after_id，时间正序）"""
    gids = select(ChatGroup.id).where(ChatGroup.user_id == user_id)
    rows = (
        await db.execute(
            select(ChatGroupMessage)
            .where(
                ChatGroupMessage.group_id.in_(gids),
                ChatGroupMessage.notify_user == 1,
                ChatGroupMessage.id > after_id,
            )
            .order_by(ChatGroupMessage.id.asc())
        )
    ).scalars().all()
    if not rows:
        return {"items": [], "total": 0}
    gmap = {}
    _grows = (await db.execute(
        select(ChatGroup.id, ChatGroup.name).where(ChatGroup.id.in_({r.group_id for r in rows}))
    )).all()
    gmap = {row[0]: (row[1] or "家庭群聊") for row in _grows}
    cmap = {}
    _cids = {r.character_id for r in rows if r.character_id}
    if _cids:
        _crows = (await db.execute(
            select(AICharacter.id, AICharacter.name, AICharacter.avatar_url).where(AICharacter.id.in_(_cids))
        )).all()
        cmap = {row[0]: {"name": row[1], "avatar": (row[2] or "")} for row in _crows}
    items = [
        {
            "id": r.id,
            "group_id": r.group_id,
            "group_name": gmap.get(r.group_id, "家庭群聊"),
            "character_id": r.character_id,
            "sender_name": cmap.get(r.character_id, {}).get("name", "") if r.character_id else "",
            "sender_avatar": cmap.get(r.character_id, {}).get("avatar") or None if r.character_id else None,
            "content": r.content,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/{group_id}/messages")
async def list_messages(
    group_id: int,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """群消息（时间正序）"""
    await _owned_group(db, group_id, user_id, lang)
    limit = max(1, min(limit, 300))
    rows = (
        await db.execute(
            select(ChatGroupMessage)
            .where(ChatGroupMessage.group_id == group_id)
            .order_by(ChatGroupMessage.id.desc())
            .limit(limit)
        )
    ).scalars().all()
    rows = list(reversed(rows))
    names = await _member_names(db, group_id)
    # 头像（2026-08-14）：群消息带 sender_avatar 供前端显示角色头像
    avatars: dict[int, str] = {}
    try:
        cids = {r.character_id for r in rows if r.character_id}
        if cids:
            _cr = await db.execute(
                select(AICharacter.id, AICharacter.avatar_url).where(AICharacter.id.in_(cids))
            )
            avatars = {row[0]: (row[1] or "") for row in _cr.all()}
    except Exception:
        pass
    items = [
        {
            "id": r.id,
            "sender_type": r.sender_type,
            "character_id": r.character_id,
            "sender_name": names.get(r.character_id, "") if r.character_id else "你",
            "sender_avatar": avatars.get(r.character_id) if r.character_id else None,
            "content": r.content,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "total": len(items)}


# 用户消息中的 @ 目标（Phase 3：@ 指定角色优先回应）
_AT_RE = re.compile(r"@([^\s@，。！？!?、]+)")


def _parse_at_names(content: str) -> list[str]:
    """提取 @ 目标；排除邮箱/网址类英文片段（a@b.com、@example.com）避免误报"""
    names = []
    for m in _AT_RE.finditer(content):
        n = m.group(1).strip()
        if not n:
            continue
        if "." in n:  # 邮箱/域名特征
            continue
        names.append(n)
    return names


async def _state_line(char) -> str:
    """角色当前八维状态的人话描述（Phase 3：状态联动——心情低时群聊也蔫蔫的）"""
    try:
        from app.services.character_state_service import get_character_states
        st = await get_character_states(char.id)
        parts = []
        mood = st.get("mood", 50) or 50
        if mood <= 40:
            parts.append("心情低落")
        elif mood >= 70:
            parts.append("心情很好")
        if (st.get("fatigue", 50) or 50) >= 65:
            parts.append("很疲惫")
        if (st.get("anger", 50) or 50) >= 60:
            parts.append("在生气")
        if (st.get("comfort", 50) or 50) <= 40:
            parts.append("不太舒服")
        if not parts:
            return ""
        return f"{char.name}当前状态：{'、'.join(parts)}（回应要符合 TA 现在的状态）"
    except Exception:
        return ""


def build_group_memory_entries(user_content: str, replies: list[dict], name_map: dict) -> list[dict]:
    """纯函数：把一轮群聊按发言者拆成记忆条目（speaker 归属正确，2026-08-18 修复）。

    原实现把整轮群聊合并成一条记忆且 speaker 只标第一个回应角色（无回应时把用户
    发言标成某个角色），导致群聊事件归属错乱（角色把别人的话/事当成自己的）。
    修复：用户发言一条（speaker=user，speaker_id=0 占位由调用方替换为 user_id），
    每个角色回应各一条（speaker=该角色本人），内容为单发言者陈述。
    """
    entries: list[dict] = []
    user_text = str(user_content or "").strip()[:100]
    if user_text:
        entries.append({
            "speaker_type": "user", "speaker_id": 0,
            "content": f"用户在群里说：{user_text}",
        })
    for r in replies or []:
        cid = r.get("character_id")
        if not cid:
            continue
        text = str(r.get("content") or "").strip()[:80]
        if not text:
            continue
        who = name_map.get(cid, "角色")
        entries.append({
            "speaker_type": "character", "speaker_id": cid,
            "content": f"{who}在群里说：{text}",
        })
    return entries


async def _save_group_memory(group_id: int, user_id: int, user_content: str, replies: list[dict]) -> None:
    """群聊记忆（Phase 3，2026-08-18 修复）：按发言者拆分存储，speaker 归属正确。

    - 每轮群聊：用户发言 + 每个角色回应各存一条记忆（speaker=发言者本人）；
    - 群聊是公开的 → 存给每个群成员（原实现只存第一个回应角色名下）；
    - 每群 30 分钟节流 + skip_dedup（事件类，防字符相似合并串扰 speaker）。
    """
    try:
        from app.models.memory import Memory
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        async with async_session_factory() as db:
            # 群聊游戏 Phase 1：游戏消息不进群聊记忆——群内有进行中的对局时跳过本轮沉淀。
            from app.models.game import GameSession as _GS
            _active_game = (await db.execute(
                select(_GS.id).where(
                    _GS.group_id == group_id, _GS.status.in_(("created", "playing"))
                ).limit(1)
            )).scalar_one_or_none()
            if _active_game is not None:
                return
            members = (
                await db.execute(
                    select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
                )
            ).scalars().all()
            if not members:
                return
            recent = (
                await db.execute(
                    select(Memory.id).where(
                        Memory.user_id == user_id,
                        Memory.character_id.in_(members),
                        Memory.source == "group",
                        Memory.created_at >= now_naive - timedelta(minutes=30),
                        # P3-3（2026-08-25）：按群节流——只被同一群的 30 分钟内群记忆抑制；
                        # 旧数据 group_id IS NULL 时按旧行为（不区分群，任意群记忆都抑制）。
                        or_(Memory.group_id == group_id, Memory.group_id.is_(None)),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if recent:
                return
            name_map = {}
            cids = [r.get("character_id") for r in (replies or []) if r.get("character_id")]
            if cids:
                _cr = await db.execute(select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(cids)))
                name_map = {row[0]: row[1] for row in _cr.all()}
        entries = build_group_memory_entries(user_content, replies, name_map)
        if not entries:
            return
        from app.memory.service import save_memory
        for member_id in members:
            for e in entries:
                sp_id = user_id if e["speaker_type"] == "user" else e["speaker_id"]
                await save_memory(
                    user_id=user_id, character_id=member_id,
                    memory_type="event", content=e["content"][:300],
                    title="家庭群聊", importance=40.0,
                    sub_type="group", source="group", group_id=group_id,
                    speaker_type=e["speaker_type"], speaker_id=sp_id,
                    epistemic_status="FACT",
                    skip_dedup=True,
                )
        _logger.info("Group memory saved: group=%d members=%d entries=%d", group_id, len(members), len(entries))
    except Exception as e:
        _logger.warning("Group memory save failed: %s", e)




def _trace_group_reply(group_id: int, user_id: int, replies: list, ok: bool, latency_ms: int) -> None:
    """群聊角色回应 → AgentTask trace（Phase I：可观测群聊 AI 行为；只写不读，失败静默）。

    Feature Flag agent_trace_group 关闭 = 完全无记录（各平台可独立回退）。
    """
    try:
        from app.agent import loop as _loop
        if not _loop.AGENT_FLAGS.get("agent_trace_group", True):
            return
        from app.agent import trace as _trace
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=None,
            user_id=user_id,
            session_id=None,
            trigger="group_chat",
            route="group_chat",
            steps_json=json.dumps([
                {"action": "group_reply", "group_id": group_id,
                 "replies": [r.get("character_id") for r in replies if r.get("character_id")],
                 "ok": ok},
            ], ensure_ascii=False),
            llm_calls=1 if ok else 0,
            tool_calls=0,
            latency_ms=latency_ms,
            status="ok" if ok else "error",
            error=None if ok else "群聊回应生成失败",
        )
    except Exception:
        pass


async def _generate_replies(db: AsyncSession, group_id: int, user_content: str, user_name: str,
                            user_id: int | None = None) -> list[dict]:
    """单次 LLM 调用生成 1-3 个角色的群聊回应（JSON 数组）；失败重试 1 次后返回 []

    Phase 3（2026-08-14）：@名字 → 被 @ 角色必须回应；角色八维状态注入（状态联动）
    """
    member_rows = (
        await db.execute(
            select(ChatGroupMember).where(ChatGroupMember.group_id == group_id)
        )
    ).scalars().all()
    if not member_rows:
        return []
    # 真实 DB 返回 ChatGroupMember 对象；测试假库可能返回裸 id → 用 getattr 兼容（muted 缺省 False）
    members = [getattr(m, "character_id", m) for m in member_rows]
    muted_ids = {getattr(m, "character_id", m) for m in member_rows if getattr(m, "muted", False)}
    cr = await db.execute(select(AICharacter).where(AICharacter.id.in_(members)))
    chars = cr.scalars().all()
    if not chars:
        return []
    char_map = {c.id: c for c in chars}

    # Phase 3：用户 @ 的目标必须回应
    at_chars = []
    for n in _parse_at_names(user_content):
        hit = next(
            (c for c in chars if c.name == n or (len(n) >= 2 and (c.name.startswith(n) or n in c.name))),
            None,
        )
        if hit is not None and hit not in at_chars:
            at_chars.append(hit)

    # F1（2026-08-18）：用户 @ 角色数超上限时告警但不裁减（被 @ 必须回应合规优先；当前群成员 2-3 人实际不会触发）
    if len(at_chars) > MAX_GROUP_SPEAKERS:
        _logger.warning("group at exceeds MAX_GROUP_SPEAKERS: %d (max=%d)", len(at_chars), MAX_GROUP_SPEAKERS)

    # L1（2026-08-25）：三层漏斗选回应者——①@ 必回 + ②talkativeness 概率激活 + ③随机兜底，≤MAX_GROUP_SPEAKERS。
    # 静音角色不参与自动选择（被 @ 仍强制回）；生成部分（runtime / 旧单次 JSON 链路）保持不变。
    speakers = _select_speakers(chars, at_chars, muted_ids=muted_ids)
    if not speakers:
        return []
    speaker_ids = {c.id for c in speakers}

    # Phase E（2026-08-18）：群聊回应走统一 Runtime（Feature Flag agent_loop_group_chat，默认关=旧链路零变化）。
    # 开=逐角色 build_context 注入世界认知（知识不串线：角色只知道自己记忆+群里公开信息），
    # 经 app/agent/runtime.py 薄封装生成；异常回退旧链路兜底。
    try:
        from app.agent import loop as _loop
        if _loop.AGENT_FLAGS.get("agent_loop_group_chat", False):
            return await _generate_replies_runtime(
                db, group_id, user_content, user_name, user_id,
                chars=chars, char_map=char_map,
                speakers=speakers, at_chars=at_chars,
            )
    except Exception as e:
        _logger.warning("Group replies runtime failed, fallback to legacy: %s", e)

    char_lines = []
    for c in chars:
        desc = "；".join(x for x in [c.name, c.personality, c.chat_style] if x)
        char_lines.append(f"- {c.id}：{desc}")
    # 成员描述列全体（LLM 才知道谁在群里），回应约束在 speakers
    members_text = "\n".join(char_lines)
    # 最近群消息上下文（最近 6 条，角色显示名字）
    recent = (
        await db.execute(
            select(ChatGroupMessage)
            .where(ChatGroupMessage.group_id == group_id)
            .order_by(ChatGroupMessage.id.desc())
            .limit(6)
        )
    ).scalars().all()
    recent_lines = []
    for m in reversed(recent):
        if m.sender_type == "user":
            who = user_name
        elif m.character_id in char_map:
            who = char_map[m.character_id].name
        else:
            who = f"角色{m.character_id}"
        recent_lines.append(f"[{who}] {m.content[:80]}")
    context = "\n".join(recent_lines) or "（群聊刚开始）"

    # Phase 3：状态联动（只注入本轮回应者）
    state_lines = []
    for c in speakers:
        _sl = await _state_line(c)
        if _sl:
            state_lines.append(_sl)
    state_text = "\n".join(state_lines) or "（状态正常）"
    at_hint = ""
    if at_chars:
        at_hint = f"用户 @ 了：{'、'.join(c.name for c in at_chars)}——这些角色必须回应。\n"

    prompt = (
        f"这是一个家庭群聊，成员包括：\n{members_text}\n\n"
        f"用户{user_name}在群里说：{user_content}\n\n"
        f"最近群聊记录：\n{context}\n\n"
        f"{at_hint}"
        f"角色当前状态：\n{state_text}\n\n"
        f"请从以上成员中选择 1-{MAX_GROUP_SPEAKERS} 个最可能回应的角色（被 @ 的角色必须回应），各用一句话自然回应（符合各自性格，"
        "20-40 字，不要提及'AI/群聊'，不要互相@）。回应要符合各角色当前状态（心情低落就别太兴奋）。\n"
        "重要——多人回应时按真实对话顺序排列（数组顺序即发言顺序）：\n"
        "1. 第一个回应的人针对用户的话说；\n"
        "2. 之后每个回应的人要自然承接上一位说的话（可以同意、追问、打趣、岔开，但要接得上，不能自说自话）；\n"
        "3. 回应之间不能互相矛盾（例如不要两个人同时说'我来做饭'；也不要 A 邀约 B 答应后 C 又说 B 不答应）。\n"
        "知识边界（必须遵守）：\n"
        "每个角色只能知道上面'最近群聊记录'里公开出现的信息；\n"
        "用户私下只跟某个角色说过的事、或某角色私下的记忆，其他角色并不知道——\n"
        "不要替任何角色说出它不可能知道的私事，除非该信息已经在群里被公开提起过。\n"
        "认知状态规则：提到推测/猜测要用“可能、我觉得、也许”等不确定语气；提到计划（如“打算去、准备做”）不能说成已经做完。\n"
        "指代规则：每个角色只能用“我”自称，提到其他角色必须直接用名字（如“小丽”），禁止用“他/她”等模糊指代。\n"
        '只输出 JSON：{"replies": [{"character_id": 1, "content": "..."}]}'
    )
    from app.agent.llm_client import chat_completion
    for _attempt in range(2):
        try:
            text = await chat_completion(
                messages=[
                    {"role": "system", "content": "你是一个输出 JSON 的助手，直接输出 JSON，不要多余文字。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.85,
                max_tokens=400,
                task="message",
                user_id=user_id,
            )
            raw = (text or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            data = json.loads(raw)
            replies = data.get("replies") or []
            valid = []
            for r in replies:
                cid = int(r.get("character_id") or 0)
                content = str(r.get("content") or "").strip()
                if cid in speaker_ids and content and len(content) <= 200:
                    valid.append({"character_id": cid, "content": content[:200]})
            if valid:
                return valid
            _logger.warning(
                "Group replies: LLM returned but no valid reply (attempt %d, raw=%.120s)",
                _attempt + 1, raw,
            )
        except Exception as e:
            _logger.warning("Group replies generation failed (attempt %d): %s", _attempt + 1, e)
    _logger.warning("Group replies: no reply generated for group=%d", group_id)
    return []


async def _generate_replies_runtime(
    db: AsyncSession, group_id: int, user_content: str, user_name: str,
    user_id: int | None, *, chars, char_map, speakers, at_chars,
) -> list[dict]:
    """统一 Runtime 群聊回应（Phase E，Feature Flag agent_loop_group_chat 默认关）：

    - 每个回应角色独立 build_context（世界认知/记忆/群聊动态按角色注入）→ 知识不串线：
      角色只知道自己的记忆与群里公开说过的信息，看不到其他角色的私事；
    - 群公开上下文（成员列表/最近群消息/本轮发言/@规则/状态联动/知识边界规则）作为共享 system 注入；
    - 逐角色生成并依次追加已生成回应（模拟真实发言顺序，后发言者自然承接、不重复不矛盾）；
    - 经统一 Runtime 薄封装（app/agent/runtime.py）：单次 LLM + 动作标记剥离；
      失败/空回应静默降级（该角色不回应），绝不编造成功。
    返回与旧链路同构的 [{"character_id": cid, "content": text}]。
    """
    from app.agent import runtime as _runtime
    # F1（2026-08-18）：群聊轻量上下文由 Feature Flag agent_social_light_context 控制（默认关=全量 build_context 零变化；
    # 灰度开 True 重启生效，回退改 False 重启；与 agent_loop_group_chat 正交——后者管走不走 Runtime）
    from app.agent import loop as _loop
    light_context = bool(_loop.AGENT_FLAGS.get("agent_social_light_context", False))

    # 最近群消息（公开，所有人可见；与旧链路同一数据源与条数）
    recent = (
        await db.execute(
            select(ChatGroupMessage)
            .where(ChatGroupMessage.group_id == group_id)
            .order_by(ChatGroupMessage.id.desc())
            .limit(6)
        )
    ).scalars().all()
    recent_lines = []
    for m in reversed(recent):
        if m.sender_type == "user":
            who = user_name
        elif m.character_id in char_map:
            who = char_map[m.character_id].name
        else:
            who = f"角色{m.character_id}"
        recent_lines.append(f"[{who}] {m.content[:80]}")
    group_context = "\n".join(recent_lines) or "（群聊刚开始）"

    members_text = "\n".join(
        f"- {c.id}：{'；'.join(x for x in [c.name, c.personality, c.chat_style] if x)}" for c in chars
    )
    at_hint = ""
    if at_chars:
        at_hint = f"用户 @ 了：{'、'.join(c.name for c in at_chars)}——这些角色必须回应。\n"

    replies: list[dict] = []
    spoken: list[str] = []  # 已生成回应（群公开，供后发言者承接）
    for c in speakers:
        try:
            _sl = await _state_line(c)
        except Exception:
            _sl = ""
        state_text = _sl or "（状态正常）"
        public = (
            f"【家庭群聊】你现在在家庭群聊中回复。群成员：\n{members_text}\n\n"
            f"用户{user_name}在群里说：{user_content}\n\n"
            f"最近群聊记录：\n{group_context}\n\n"
            f"{at_hint}"
            f"角色当前状态：\n{state_text}\n\n"
            "回应要求：用 1 句话自然回应（符合你的性格，20-40 字，不要提及'AI/群聊'，不要互相@）。"
            "多人回应时按真实对话顺序——第一个回应的人针对用户的话说，之后每个回应的人要自然承接上一位说的话"
            "（可以同意、追问、打趣、岔开，但要接得上，不能自说自话）；回应之间不能互相矛盾。\n"
            "知识边界（必须遵守）：你只知道'最近群聊记录'里公开出现的信息和你自己私下与用户的记忆；"
            "用户私下只跟别的角色说过的事、或别的角色私下的记忆，你不知道——不要替任何角色说出它不可能知道的私事。\n"
            "认知状态规则：提到推测/猜测用'可能、我觉得、也许'；提到计划（如'打算去、准备做'）不能说成已经做完。\n"
            "指代规则：用'我'自称，提到其他角色必须直接用名字（如'小丽'），禁止用'他/她'等模糊指代。\n"
            "不要输出任何动作标记（如 [SEARCH]/[GEN_IMAGE]/[CAL_NOTE]/[MEMO]/【状态更新】），直接输出要说的话。"
        )
        if spoken:
            public += "\n\n已有人先说了（请自然承接，不要重复）：\n" + "\n".join(spoken)
        res = await _runtime.run_social_reply(
            character_id=c.id,
            user_id=user_id,
            session_id=None,  # 群聊无私有会话：runtime 按 (user, char) 解析最新会话注入角色自己的私聊记忆
            user_message=user_content,
            extra_system=[{"role": "system", "content": public}],
            lang="zh",
            max_text=200,
            save_memory=False,  # P3-4（2026-08-25）：群聊记忆统一由 _save_group_memory 按群落库；这里不再重复落记忆。
            # 原 save_memory=True 会让 Runtime 的 generate_response 走 extractor 落一条（extractor 不感知群聊，
            # 且与 _save_group_memory 双写重复记忆、speaker/归属不一致）。改 False 后行为变化仅在
            # agent_loop_group_chat 开启时发生（该路径才调用 _generate_replies_runtime），默认仍关=零变化。
            light_context=light_context,  # F1（2026-08-18）：Flag 控制群聊轻量上下文
        )
        text = (res.get("text") or "").strip()
        if res.get("status") == "ok" and text:
            replies.append({"character_id": c.id, "content": text[:200]})
            spoken.append(f"[{char_map[c.id].name}] {text[:80]}")
        else:
            _logger.info("Group reply runtime skipped char=%d (status=%s)", c.id, res.get("status"))
    return replies


# ── 群聊游戏 Phase 2：/play 命令 ──────
_GAME_ALIASES = {
    "undercover": "undercover", "谁是卧底": "undercover",
    "werewolf": "werewolf", "狼人杀": "werewolf",
    "liars_bar": "liars_bar", "骗子酒馆": "liars_bar",
    "turtle_soup": "turtle_soup", "海龟汤": "turtle_soup",
    "truth_or_dare": "truth_or_dare", "真心话大冒险": "truth_or_dare",
    "twenty_q": "twenty_q", "猜词20问": "twenty_q", "猜词": "twenty_q",
}


async def _group_active_chars(db: AsyncSession, group_id: int) -> list:
    """返回群里活跃的 AI 角色列表。"""
    rows = (await db.execute(
        select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
    )).scalars().all()
    if not rows:
        return []
    from app.models.character import AICharacter as _AC
    chars = (await db.execute(
        select(_AC).where(_AC.id.in_(rows), _AC.is_active.is_(True))
    )).scalars().all()
    return list(chars)


async def _handle_play_command(db: AsyncSession, group_id: int, content: str,
                               user_name: str, user_id: int, msg) -> dict:
    """处理 /play 命令：解析游戏 → 校验人数 → 创建群游戏会话 → 镜像 GM 播报进群。

    用户 /play 后不再走普通群聊回复。非法游戏名/人数不足返回群聊提示（不报 500）。
    """
    from app.api.games import list_games, engine_for, _create_session_in_db, _spawn_background, \
        _mirror_to_group as _mirror, _resume_ai_turns

    arg = (content[len("/play"):] or "").strip()
    group_chars = await _group_active_chars(db, group_id)
    group_count = len(group_chars)
    gm = {g["game_type"]: g for g in list_games()}

    # 解析游戏类型
    game_type = None
    if arg:
        game_type = _GAME_ALIASES.get(arg.lower()) or _GAME_ALIASES.get(arg)
        if game_type is None:
            return await _play_reply(db, msg, user_name,
                               "没听懂要玩哪个游戏，试试 /play 狼人杀、/play werewolf 或 /play 骗子酒馆。")
    else:
        # /play 默认：随机一个能容纳当前群成员人数的多人游戏
        candidates = [g["game_type"] for g in list_games()
                      if g["player_mode"] == "multi" and g["needs_gm"]
                      and g["min_players"] <= group_count <= g["max_players"]]
        game_type = random.choice(candidates) if candidates else None
    if game_type is None:
        return await _play_reply(db, msg, user_name, "当前群成员不足以开局，试试 /play 狼人杀 或先多拉几个角色。")

    meta = gm.get(game_type) or engine_for(game_type)(None).meta()
    if not (meta["min_players"] <= group_count <= meta["max_players"]):
        return await _play_reply(
            db, msg, user_name,
            f"「{meta['name']}」需要 {meta['min_players']}-{meta['max_players']} 名玩家，当前群成员 {group_count} 人。",
        )

    # 创建群游戏会话（玩家=群内活跃 AI 角色，用户观战）
    char_ids = [c.id for c in group_chars]
    try:
        session, engine = await _create_session_in_db(
            db, user_id=user_id, game_type=game_type,
            player_ids=char_ids, spectator_ids=[], user_as_player=False,
            group_id=group_id, trigger="user_initiated",
        )
    except ValueError as e:
        return await _play_reply(db, msg, user_name, str(e))

    # 镜像 GM 初始播报进群（msg_type=game_event）
    for ev in engine.all_events_ordered():
        if ev.get("visibility", "public") == "public":
            await _mirror(db, engine, session, ev)
    await db.commit()

    # 首个行动者是 AI 时触发自动续跑
    ts = engine.current_turn_seat()
    if ts is not None and engine.is_ai(ts):
        _spawn_background(_resume_ai_turns(session.id))

    _logger.info("group /play started group=%d game=%s session=%d players=%d",
                 group_id, game_type, session.id, len(char_ids))
    return {
        "user_message": {
            "id": msg.id, "sender_type": "user", "character_id": None,
            "sender_name": user_name, "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        },
        "replies": [],
        "game": {"session_id": session.id, "game_type": game_type,
                 "name": meta["name"], "player_mode": meta["player_mode"],
                 "player_count": len(char_ids)},
        "notice": f"🎮 已开启「{meta['name']}」，角色们开始啦！",
    }


async def _play_reply(db: AsyncSession, msg, user_name: str, notice: str) -> dict:
    """人数不足/非法游戏名：写入群提示消息并返回（不报 500）。"""
    try:
        db.add(ChatGroupMessage(
            group_id=msg.group_id, sender_type="ai", character_id=None,
            content=notice[:200], msg_type="game_event",
        ))
        await db.commit()
    except Exception:
        await db.rollback()
    return {
        "user_message": {
            "id": msg.id, "sender_type": "user", "character_id": None,
            "sender_name": user_name, "content": msg.content,
            "created_at": msg.created_at.isoformat(),
        },
        "replies": [], "game": None, "notice": notice,
    }


@router.post("/{group_id}/messages")
async def send_message(
    group_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    lang: str = Header(default="zh"),
):
    """用户发言 → 落库 → 生成多角色回应（单次 LLM）"""
    await _owned_group(db, group_id, user_id, lang)
    content = str(data.get("content") or "").strip()
    if not content or len(content) > 500:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "group_msg_empty"))

    msg = ChatGroupMessage(group_id=group_id, sender_type="user", content=content)
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    user_name = ""
    try:
        from app.models.user import User
        u = await db.get(User, user_id)
        user_name = (u.nickname if u and u.nickname else (u.username if u else "")) or "用户"
    except Exception:
        user_name = "用户"

    # 群聊游戏 Phase 2：/play 命令入口（不走普通群聊回复）
    if content.startswith("/play"):
        return await _handle_play_command(db, group_id, content, user_name, user_id, msg)

    _t0 = time.monotonic()
    replies = await _generate_replies(db, group_id, content, user_name, user_id=user_id)
    # Phase I：群聊角色回应 → AgentTask trace（只写不读；失败静默）
    try:
        _trace_group_reply(group_id, user_id, replies, bool(replies), int((time.monotonic() - _t0) * 1000))
    except Exception:
        pass
    # 群成员名字 + 头像（2026-08-15：返回给前端直接显示，避免 sender_name 兜底成"角色"）
    _names = await _member_names(db, group_id)
    _avatars: dict[int, str] = {}
    try:
        _cids = {r["character_id"] for r in replies if r.get("character_id")}
        if _cids:
            _cr = await db.execute(
                select(AICharacter.id, AICharacter.avatar_url).where(AICharacter.id.in_(_cids))
            )
            _avatars = {row[0]: (row[1] or "") for row in _cr.all()}
    except Exception:
        pass
    # @我的才弹：用户 @ 了某角色 → 该角色的回应 notify_user=1（前端轮询 /mentions 弹通知）
    at_cids: set[int] = set()
    if _parse_at_names(content):
        try:
            _all_members = (await db.execute(
                select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
            )).scalars().all()
            if _all_members:
                _mrows = (await db.execute(
                    select(AICharacter.id, AICharacter.name).where(AICharacter.id.in_(_all_members))
                )).all()
                _mnames = {row[1]: row[0] for row in _mrows}
                for _n in _parse_at_names(content):
                    _hit = next(
                        (cid for cname, cid in _mnames.items()
                         if cname == _n or (len(_n) >= 2 and (cname.startswith(_n) or _n in cname))),
                        None,
                    )
                    if _hit is not None:
                        at_cids.add(_hit)
        except Exception:
            pass

    ai_msgs = []
    for r in replies:
        am = ChatGroupMessage(
            group_id=group_id, sender_type="ai", character_id=r["character_id"], content=r["content"],
        )
        if am.character_id in at_cids:
            am.notify_user = 1
        db.add(am)
        await db.flush()
        await db.refresh(am)
        ai_msgs.append({
            "id": am.id,
            "sender_type": "ai",
            "character_id": am.character_id,
            "sender_name": _names.get(am.character_id, "") if am.character_id else "角色",
            "sender_avatar": _avatars.get(am.character_id) if am.character_id else None,
            "content": am.content,
            "created_at": am.created_at.isoformat(),
            "notify_user": am.notify_user,
        })
    await db.commit()

    # 群记忆（Phase 3，2026-08-14）：异步把本轮群聊提炼为记忆（每群 30 分钟节流，失败静默）
    try:
        from app.api.games import _spawn_background
        _spawn_background(_save_group_memory(group_id, user_id, content, ai_msgs))
    except Exception:
        pass

    return {
        "user_message": {
            "id": msg.id, "sender_type": "user", "character_id": None,
            "sender_name": user_name,
            "content": msg.content, "created_at": msg.created_at.isoformat(),
        },
        "replies": ai_msgs,
    }
