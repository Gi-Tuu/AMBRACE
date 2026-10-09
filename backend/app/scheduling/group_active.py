"""家庭群聊·角色主动冒泡（2026-08-15）

背景：群聊只有用户发言后才生成回应，角色从不主动说话。
方案：arbiter 事件源——群内最近一段时间无 AI 消息时，概率性选一个角色主动冒泡 1 句。

- collect_group_events：扫描用户所有群，空闲超时（默认 6h 无 AI 消息）且群内任一角色
  开启主动交流 → 概率（30s tick 下低概率）产出候选，绑定一个发言人
- run_group_active：LLM 生成 1 句话（结合最近群消息 + 发言人性格），落库为群消息
  （带 sender_name/sender_avatar，前端轮询拉到即显示）
- 「已发送」口径（E15，2026-09-29）：本通道按设计不受主动频控（arbiter 对 group_active
  早退、跳过每小时/最小间隔计数），发送时补写 ProactiveMessageLog 只为让其它通道的
  计数「看得见」它——不改任何闸门行为。
- 「每 tick 每群最多落地 1 条」（A34 批3，2026-10-07，B.3.5）：群内同秒出现 2 条 AI 冒泡
  （现场 chat_group_messages 2041/2043、2023/2025）。同秒多落的唯一来源是**双角色互聊**：
  采集侧每群每 tick 只产 1 个候选、arbiter 每角色每 tick 只成一条，所以只在落库侧收口——
  按 MAX_LANDS_PER_GROUP_TICK 截断（只落发起者第一句，其余轮次 INFO 留痕后丢弃，不排队顺延，
  避免跨 tick 补偿式刷屏）。只收紧落地条数，不改概率、不改空闲判定、不改双角色互聊的生成。
"""
import json
import random
from datetime import timedelta

from sqlalchemy import select, func

from app.db.database import async_session_factory
from app.models.chat import ChatGroup, ChatGroupMember, ChatGroupMessage
from app.models.character import AICharacter, ProactiveMessageLog
from app.utils.logger import get_logger
from app.utils.timeutil import now_naive_utc, to_naive_utc

_logger = get_logger("scheduler.group_active")

GROUP_ACTIVE_TYPE = "group_active"
# 群内无 AI 消息超过此时长才冒泡（避免刷屏）；概率按 30s tick 估，期望约 2-4 小时一次
IDLE_HOURS = 6
PROBABILITY = 0.05
MAX_CHARS = 200
# A34 批3（2026-10-07，B.3.5）：每 tick 每群最多落地几条 AI 冒泡。
# 1 = 现场口径「一次冒泡只发一句」；互聊其余轮次 INFO 留痕后丢弃（不排队顺延，避免跨 tick 补偿式刷屏）。
MAX_LANDS_PER_GROUP_TICK = 1


async def collect_group_events() -> list[dict]:
    """群空闲（无 AI 消息 > IDLE_HOURS）且有角色开主动 → 概率产出候选。"""
    try:
        now = now_naive_utc()
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
                    idle = (now - to_naive_utc(last_ai)) > timedelta(hours=IDLE_HOURS)
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


async def _pre_land_conflict(group_id: int, char_id: int, last_seen_id: int) -> str:
    """A40（A37 批 3，审计 §4 批 3 · C19 / I11）：落库前复检「这段时间里群里是否已有真人发言」。

    本通道是后台型（``BACKGROUND_TYPES``），按设计**豁免速率闸**（每小时上限/最小间隔/日配额，
    见模块头 E12/E15 口径）；但豁免只许豁免速率，**不许豁免新事件冲突**（不变量 I11）：
    群历史是在 ``run_group_active`` 开头读的（``:149-156``），中间还要跑一次 LLM，
    这期间用户如果在群里说了话，这条 AI 冒泡就变成"插不进用户那句话的自言自语"。
    判据只用一个单调量：**本次读到的群历史末尾那条 id 之后，是否又落了 ``sender_type='user'`` 的消息**
    （用 id 不用时间戳，避开 created_at 的秒级精度与跨连接时钟差）。

    两档 flag 默认关＝一次查询都不发（逐字节旧行为）。影子档命中只打 INFO、照常落地；
    实拦档命中返回原因串，调用方**直接 return False 且不写任何行**——没落地就没有 ProactiveMessageLog，
    也就不存在"跳过却标记已发送"（不变量 I3；本通道的"消费标记"就是那两条 add 的行）。
    读失败＝照落（fail-open）并把读失败写进 INFO（不变量 I5，发送侧不收紧）。
    """
    from app.scheduling import scheduler as engine

    shadow, enforce = engine.gate3_flags()
    if not (shadow or enforce):
        return ""
    try:
        # 另开一个会话读：外层 db 事务在 LLM 期间一直挂着，同一连接再查会读到自己的旧快照（WAL 下看不到别人新提交）
        async with async_session_factory() as _db:
            hit = (await _db.execute(
                select(ChatGroupMessage.id)
                .where(
                    ChatGroupMessage.group_id == group_id,
                    ChatGroupMessage.sender_type == "user",
                    ChatGroupMessage.id > last_seen_id,
                )
                .limit(1)
            )).scalars().first()
    except Exception as e:
        _logger.info("Group active 闸③复检读失败照落 group=%s: %s", group_id, e)
        return ""
    if hit is None:
        return ""
    _logger.info("Group active 闸③%s reason=human_spoke group=%d char=%d last_seen=%d%s",
                 "命中" if enforce else "（影子）", group_id, char_id, last_seen_id,
                 "" if enforce else "·照落")
    return "human_spoke" if enforce else ""


async def run_group_active(char_id: int, group_id: int, user_id: int,
                           with_id: int | None = None) -> bool:
    """生成 2-4 轮双角色互聊并落库（发起者 + 搭档交替发言；无搭档时退化为单句冒泡）。

    A34 批3（2026-10-07，B.3.5）：生成仍是多轮，但**每 tick 每群只落 1 条**（`MAX_LANDS_PER_GROUP_TICK`），
    其余轮次 INFO 留痕后丢弃；日志口径从 rounds 改为 lands，让「生成了几轮」与「落地了几条」不再混在一起。
    """
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
            # A40 闸③现状锚：本次生成所依据的群历史末尾 id（recent 按 id 倒序取，[0] 即最新）
            _last_seen = recent[0].id if recent else 0

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
                if await _pre_land_conflict(group_id, char_id, _last_seen):
                    return False
                db.add(ChatGroupMessage(
                    group_id=group_id, sender_type="ai", character_id=char_id, content=text[:MAX_CHARS],
                ))
                meta = {"group_id": group_id}
                if with_id is not None:
                    meta["with_id"] = with_id
                db.add(ProactiveMessageLog(
                    character_id=char_id,
                    session_id=None,
                    message_type=GROUP_ACTIVE_TYPE,
                    content=text[:500],
                    extra_meta=json.dumps(meta, ensure_ascii=False),
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
            # A34 批3（B.3.5）：每 tick 每群最多落地 MAX_LANDS_PER_GROUP_TICK 条——
            # 互聊生成 2-4 轮，但只落发起者开口那一句，其余轮次 INFO 留痕后丢弃（不顺延到下个 tick）。
            if len(valid) > MAX_LANDS_PER_GROUP_TICK:
                dropped = valid[MAX_LANDS_PER_GROUP_TICK:]
                _logger.info(
                    "Group multi-chat trimmed char=%d group=%d keep=%d drop=%d dropped=[%s]",
                    char_id, group_id, MAX_LANDS_PER_GROUP_TICK, len(dropped),
                    " | ".join(f"{cid}:{c[:30]}" for cid, c in dropped),
                )
                valid = valid[:MAX_LANDS_PER_GROUP_TICK]
            if await _pre_land_conflict(group_id, char_id, _last_seen):
                return False
            for cid, content in valid:
                db.add(ChatGroupMessage(
                    group_id=group_id, sender_type="ai", character_id=cid, content=content,
                ))
                db.add(ProactiveMessageLog(
                    character_id=cid,
                    session_id=None,
                    message_type=GROUP_ACTIVE_TYPE,
                    content=content[:500],
                    extra_meta=json.dumps({"group_id": group_id, "with_id": with_id},
                                          ensure_ascii=False),
                ))
            await db.commit()
            _logger.info("Group multi-chat sent char=%d group=%d lands=%d", char_id, group_id, len(valid))
            return True
    except Exception as e:
        _logger.warning("run_group_active failed char=%d: %s", char_id, e)
        return False
