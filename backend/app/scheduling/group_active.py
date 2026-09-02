"""家庭群聊·角色主动冒泡（2026-08-15）

背景：群聊只有用户发言后才生成回应，角色从不主动说话。
方案：arbiter 事件源——群内最近一段时间无 AI 消息时，概率性选一个角色主动冒泡 1 句。

- collect_group_events：扫描用户所有群，空闲超时（默认 6h 无 AI 消息）且群内任一角色
  开启主动交流 → 概率（30s tick 下低概率）产出候选，绑定一个发言人
- run_group_active：LLM 生成 1 句话（结合最近群消息 + 发言人性格），落库为群消息
  （带 sender_name/sender_avatar，前端轮询拉到即显示）
"""
import json
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage
from app.models.character import AICharacter
from app.utils.logger import get_logger

_logger = get_logger("scheduler.group_active")

GROUP_ACTIVE_TYPE = "group_active"
# 群内无 AI 消息超过此时长才冒泡（避免刷屏）；概率按 30s tick 估，期望约 2-4 小时一次
IDLE_HOURS = 6
PROBABILITY = 0.05
MAX_CHARS = 200


async def collect_group_events() -> list[dict]:
    """群空闲（无 AI 消息 > IDLE_HOURS）且有角色开主动 → 概率产出候选。"""
    try:
        now = datetime.now(timezone.utc)
        async with async_session_factory() as db:
            groups = (await db.execute(
                select(ChatGroup).order_by(ChatGroup.id.desc())
            )).scalars().all()
            events = []
            for g in groups:
                # 群聊游戏 Phase 1：该群有进行中的对局时跳过主动冒泡（避免游戏期间打扰）。
                from app.models.game import GameSession as _GS
                _gactive = (await db.execute(
                    select(_GS.id).where(
                        _GS.group_id == g.id, _GS.status.in_(("created", "playing"))
                    ).limit(1)
                )).scalar_one_or_none()
                if _gactive is not None:
                    continue
                # 该群最近一条 AI 消息时间
                last_ai = (
                    await db.execute(
                        select(func.max(ChatGroupMessage.created_at)).where(
                            ChatGroupMessage.group_id == g.id,
                            ChatGroupMessage.sender_type == "ai",
                        )
                    )
                ).scalar_one_or_none()
                idle = True
                if last_ai is not None:
                    ts = last_ai
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    idle = (now - ts) > timedelta(hours=IDLE_HOURS)
                if not idle:
                    continue
                # 群成员
                member_ids = (
                    await db.execute(
                        select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == g.id)
                    )
                ).scalars().all()
                if not member_ids:
                    continue
                # 群内是否有角色开启主动交流（沿用 triggers.proactive_enabled 逻辑）
                from app.scheduling.triggers import proactive_enabled
                enabled_ids = []
                for cid in member_ids:
                    try:
                        if await proactive_enabled(cid):
                            enabled_ids.append(cid)
                    except Exception:
                        pass
                if not enabled_ids:
                    continue
                if random.random() > PROBABILITY:
                    continue
                # 发起者 + 搭档：优先选同样开启主动交流的成员（自然感），否则任意其他成员
                speaker_id = random.choice(enabled_ids)
                partners = [cid for cid in member_ids if cid != speaker_id]
                enabled_set = set(enabled_ids)
                open_partners = [cid for cid in partners if cid in enabled_set]
                with_id = random.choice(open_partners or partners) if partners else None
                if with_id is None:
                    continue
                events.append({
                    "type": GROUP_ACTIVE_TYPE,
                    "priority": 0.5,  # 低优先级，不抢占重要主动消息
                    "candidate": {
                        "character_id": speaker_id,
                        "group_id": g.id,
                        "user_id": g.user_id,
                        "with_id": with_id,
                    },
                })
            if events:
                _logger.info("Group active candidates: %d", len(events))
            return events
    except Exception as e:
        _logger.warning("collect_group_events failed: %s", e)
        return []


async def run_group_active(char_id: int, group_id: int, user_id: int,
                           with_id: int | None = None) -> bool:
    """生成 2-4 轮双角色互聊并逐条落库（发起者 + 搭档交替发言；无搭档时退化为单句冒泡）。"""
    try:
        from app.agent.llm_client import chat_completion
        async with async_session_factory() as db:
            char = await db.get(AICharacter, char_id)
            if char is None:
                return False
            member_ids = (
                await db.execute(
                    select(ChatGroupMember.character_id).where(ChatGroupMember.group_id == group_id)
                )
            ).scalars().all()
            char_map = {}
            if member_ids:
                rows = (await db.execute(
                    select(AICharacter).where(AICharacter.id.in_(member_ids))
                )).scalars().all()
                char_map = {c.id: c for c in rows}
            partner = char_map.get(with_id or 0)
            # 最近群消息（名字前缀）
            recent = (
                await db.execute(
                    select(ChatGroupMessage)
                    .where(ChatGroupMessage.group_id == group_id)
                    .order_by(ChatGroupMessage.id.desc())
                    .limit(8)
                )
            ).scalars().all()
            recent_lines = []
            for m in reversed(recent):
                if m.sender_type == "user":
                    recent_lines.append(f"[用户] {m.content[:60]}")
                elif m.character_id in char_map:
                    recent_lines.append(f"[{char_map[m.character_id].name}] {m.content[:60]}")
            context = "\n".join(recent_lines) or "（群聊刚开始）"

            if partner is None:
                # 退化为单句冒泡（无搭档）
                prompt = (
                    f"你在一个家庭群聊里，成员有：{'、'.join(c.name for c in char_map.values())}。\n"
                    f"最近群聊记录：\n{context}\n\n"
                    f"你是{char.name}（性格：{char.personality or '友善'}，聊天风格：{char.chat_style or '自然'}）。\n"
                    "你有点想大家了，主动在群里冒个泡说一句话（20-40 字，口语化、符合你的性格，"
                    "像家人闲聊一样自然；不要说'AI''群聊'，不要@别人）。"
                )
                text = await chat_completion(
                    messages=[
                        {"role": "system", "content": "直接输出要说的话，不要加引号和标注。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.9,
                    max_tokens=128,
                    task="message",
                    user_id=user_id,
                )
                text = (text or "").strip().strip('"').strip("'")
                if len(text) < 2:
                    return False
                db.add(ChatGroupMessage(
                    group_id=group_id, sender_type="ai", character_id=char_id, content=text[:MAX_CHARS],
                ))
                await db.commit()
                _logger.info("Group active sent char=%d group=%d", char_id, group_id)
                return True

            # 双角色互聊：单次 LLM 输出 2-4 轮 JSON，交替发言，逐条落库
            prompt = (
                f"你在一个家庭群聊里，成员有：{'、'.join(c.name for c in char_map.values())}。\n"
                f"最近群聊记录：\n{context}\n\n"
                f"你是{char.name}（性格：{char.personality or '友善'}，聊天风格：{char.chat_style or '自然'}）。\n"
                f"{partner.name}（性格：{partner.personality or '友善'}，聊天风格：{partner.chat_style or '自然'}）也在群里。\n"
                "你有点想大家了，主动在群里和" + partner.name + "聊几句家常（2-4 轮来回，像家人闲聊一样自然；"
                "不要说'AI''群聊'，不要@别人，不要生硬复述记录）。\n"
                "只输出 JSON：{\"messages\": [{\"character_id\": 1, \"content\": \"...\"}]}。要求：\n"
                "1. 第一条必须是你（发起者）先开口，之后两人交替发言（对方接话，你可再回，最多 4 条）；\n"
                f"2. character_id 只能是 {char_id}（你）或 {with_id}（{partner.name}）；\n"
                "3. 每条 15-40 字，口语化、符合各自性格，内容自然承接上一条；\n"
                "4. 不要互相矛盾，不要两人同时做同一件事。"
            )
            text = await chat_completion(
                messages=[
                    {"role": "system", "content": "你是输出 JSON 的助手，直接输出 JSON，不要多余文字。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
                max_tokens=512,
                task="message",
                user_id=user_id,
            )
            raw = (text or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                if raw.startswith("json"):
                    raw = raw[4:]
            try:
                data = json.loads(raw)
                msgs = data.get("messages") or []
            except Exception:
                msgs = []
            valid = []
            allowed = {char_id, with_id}
            for m in msgs:
                cid = int(m.get("character_id") or 0)
                content = str(m.get("content") or "").strip()
                if cid in allowed and content and len(content) <= MAX_CHARS:
                    valid.append((cid, content[:MAX_CHARS]))
            if not valid:
                _logger.warning("Group multi-chat: no valid messages, raw=%.120s", raw)
                return False
            for cid, content in valid:
                db.add(ChatGroupMessage(
                    group_id=group_id, sender_type="ai", character_id=cid, content=content,
                ))
            await db.commit()
            _logger.info("Group multi-chat sent char=%d group=%d rounds=%d", char_id, group_id, len(valid))
            return True
    except Exception as e:
        _logger.warning("run_group_active failed char=%d: %s", char_id, e)
        return False
