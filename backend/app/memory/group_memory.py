# -*- coding: utf-8 -*-
"""#72 群共享长期记忆（子库）：本地聚合写入 + 按群读取；不走向量、失败静默。

范式对齐 games/memory_bridge：群"共同经历"细节存 group_memories（一份/群），
角色主 memories 只滚动保留少量 group_summary 摘要指针。

本模块只提供读写原语（PR-B），不接 _save_group_memory 双轨（那属 PR-C）；
group_cognition_v2 总开关彼时在 agent/loop.py AGENT_FLAGS 登记，默认关——
此处仅读取该 flag（缺省 False），异常一律视为关（零行为变化）。
"""
from __future__ import annotations

import json

from datetime import datetime, timedelta
from sqlalchemy import func, or_, select

from app.db.database import async_session_factory
from app.models.chat import GroupCharCognition, GroupMemory
from app.utils.logger import get_logger
from app.utils.timeutil import beijing_day_start_utc

_logger = get_logger("memory.group_memory")

_SUMMARY_KEEP_PER_GROUP = 3      # 每角色每群主记忆只留最近 3 条 group_summary 指针
_LONGTERM_LIMIT = 6              # 群生成时注入的长期群记忆条数上限

# ── #72 PR-C 预算常量（2026-09-15，用户拍板 (a)）──
# 每（角色, 群）每日认知生成上限 / 单角色跨所有群每日总生成上限。本包只落常量，供 P3 消费；
# 计数沿用「按 (character_id, 北京时间当日) 数 DB 行」惯例（与 unfinished_topic/life_loop 同构）。
_CHAR_COG_PER_GROUP_DAILY = 4
_CHAR_COG_CROSS_GROUP_DAILY = 10

# ── #72 PR-C P5 群记忆日终合并收敛参数（2026-09-16）──
_COMPACT_KEEP_DAYS = 7          # 最近 7 天（北京时间自然日）原样保留，更旧的合并归档
_COMPACT_BATCH = 200            # 单群单次合并上限（防一次处理过久；>上限留待后续日终继续）
_COMPACT_SUMMARY_MAX = 600      # 摘要行 content 最大长度（宁可丢尾部也不写超长）


def group_cognition_on() -> bool:
    """总开关：默认关。读 AGENT_FLAGS 真值源，异常一律 False（零行为变化）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("group_cognition_v2", False))
    except Exception:
        return False


def group_memory_compact_on() -> bool:
    """#72 PR-C P5 日终合并总闸：默认关。读 AGENT_FLAGS 真值源，异常一律 False（零行为变化）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        return bool(AGENT_FLAGS.get("group_memory_compact", False))
    except Exception:
        return False


async def group_cognition_enabled_for(group_id: int) -> bool:
    """#72 PR-C 两级闸收敛点（P4，2026-09-15）：全局 flag 开 且 该群 cognition_enabled=1 才 True。

    所有激活点（生成调度 / 生成本身 / 私有注入 / P2 共享记忆注入）统一收敛到这里，
    避免各处分散判定导致非灰度群误触发。关 flag 时直接短路返回 False（零 DB 查询、零行为变化）。
    """
    if not group_cognition_on():
        return False
    try:
        async with async_session_factory() as db:
            from app.models.chat import ChatGroup
            enabled = (await db.execute(
                select(ChatGroup.cognition_enabled).where(ChatGroup.id == group_id)
            )).scalar_one_or_none()
        return bool(enabled)
    except Exception as e:
        _logger.warning("group_cognition_enabled_for failed group=%s: %s", group_id, e)
        return False


def _trace_group_cognition(route: str, character_id: int | None, detail: dict) -> None:
    """#72 PR-C 观测（P4，2026-09-15）：写 group_cognition_gen / group_cognition_inject 事件。

    复用 agent_trace_group / AgentTask trace 口径（trigger=group_chat）；只写不读、失败静默。
    """
    try:
        from app.agent import trace as _trace
        _trace.enqueue_task_log(
            task_id=_trace.new_task_id(),
            character_id=character_id,
            user_id=detail.get("user_id"),
            session_id=None,
            trigger="group_chat",
            route=(route or "unknown")[:30],
            steps_json=json.dumps(detail, ensure_ascii=False, default=str)[:1600],
            llm_calls=0,
            tool_calls=0,
            latency_ms=0,
            status="ok",
            error=None,
        )
    except Exception as e:
        _logger.warning("group_cognition trace failed route=%s: %s", route, e)


async def append_group_event(
    *, group_id: int, user_id: int, round_id: str | None,
    user_content: str, replies: list[dict], name_map: dict[int, str],
) -> None:
    """一轮群聊 → 1 条群共享事件（本地规则聚合，零 LLM）。

    replies: [{"character_id":..,"content":..}]（与 _generate_replies 返回同构）。
    开关关 / 无内容 / 异常 → 直接返回，不影响主链路。
    """
    if not group_cognition_on():
        return
    try:
        lines = []
        u = (user_content or "").strip()[:100]
        if u:
            lines.append(f"用户：{u}")
        for r in replies or []:
            cid = r.get("character_id")
            txt = (r.get("content") or "").strip()[:80]
            if cid and txt:
                lines.append(f"{name_map.get(cid, '角色')}：{txt}")
        if not lines:
            return
        async with async_session_factory() as db:
            db.add(GroupMemory(
                group_id=group_id, user_id=user_id, round_id=round_id,
                speaker_type="system", speaker_id=None,
                content="；".join(lines)[:600],
                epistemic_status="FACT", importance=40,
            ))
            await db.commit()
    except Exception as e:
        _logger.warning("append_group_event failed group=%s: %s", group_id, e)


async def recall_group_longterm(group_id: int, limit: int = _LONGTERM_LIMIT) -> list[str]:
    """取本群长期共同记忆（跨天，按时间倒序取若干条后正序展示）；非本群调用方拿不到。

    #72 PR-C P5（2026-09-16）：只取 is_archived=0 的活跃行，归档行（被日终合并收敛的旧事件）不再注入。
    """
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(GroupMemory)
                .where(
                    GroupMemory.group_id == group_id,
                    GroupMemory.is_archived == False,  # noqa: E712
                )
                .order_by(GroupMemory.id.desc()).limit(limit)
            )).scalars().all()
        return [f"[{str(r.created_at)[:10]}] {r.content}" for r in reversed(rows)]
    except Exception as e:
        _logger.warning("recall_group_longterm failed group=%s: %s", group_id, e)
        return []


async def compact_group_memories() -> dict:
    """#72 PR-C P5（2026-09-16）：群记忆日终合并收敛，防 group_memories 无声膨胀。

    - 逐群处理：取 is_archived=0 且 created_at < (北京时间今日00:00 - _COMPACT_KEEP_DAYS 天) 的行，
      按时间升序；单次合并上限 _COMPACT_BATCH（防一次处理过久）；
    - 把要合并的行拼成 1 条 system/FACT 摘要行（content 形如 [YYYY-MM-DD~YYYY-MM-DD] 事件1；事件2；…，
      总长截断到 _COMPACT_SUMMARY_MAX 内，宁可丢尾部也不写超长）；importance 取被合并行均值（无则 40）；
    - 被合并的旧行置 is_archived=1（留痕不删，绝不物理删）；
    - 幂等：旧行归档后重复执行不产生新摘要行；单次合并设有上限防过久；
    - 失败静默（只 warning），不阻塞调度循环；
    - 受 group_memory_compact 总闸（关=零行为变化，直接返回）。
    """
    if not group_memory_compact_on():
        return {"skipped": True, "groups": 0, "archived": 0, "summaries": 0}
    stats = {"skipped": False, "groups": 0, "archived": 0, "summaries": 0}
    try:
        cutoff = beijing_day_start_utc() - timedelta(days=_COMPACT_KEEP_DAYS)
        async with async_session_factory() as db:
            group_ids = (await db.execute(
                select(GroupMemory.group_id)
                .where(
                    GroupMemory.is_archived == False,  # noqa: E712
                    GroupMemory.created_at < cutoff,
                )
                .distinct()
            )).scalars().all()

        for gid in group_ids:
            try:
                res = await _compact_one_group(gid, cutoff)
                stats["groups"] += 1
                stats["archived"] += res["archived"]
                stats["summaries"] += res["summaries"]
            except Exception as e:
                _logger.warning("compact_group_memories group=%s failed: %s", gid, e)
    except Exception as e:
        _logger.warning("compact_group_memories failed: %s", e)
    return stats


async def _compact_one_group(group_id: int, cutoff: datetime) -> dict:
    """合并单个群的一批旧事件（≤_COMPACT_BATCH 条）：生成 1 条摘要 + 旧行归档。

    调用方负责按群异常隔离；本函数自身也只告警不抛，保证日终循环不被阻塞。
    """
    async with async_session_factory() as db:
        rows = (await db.execute(
            select(GroupMemory)
            .where(
                GroupMemory.group_id == group_id,
                GroupMemory.is_archived == False,  # noqa: E712
                GroupMemory.created_at < cutoff,
            )
            .order_by(GroupMemory.created_at.asc(), GroupMemory.id.asc())
            .limit(_COMPACT_BATCH)
        )).scalars().all()
        if not rows:
            return {"archived": 0, "summaries": 0}

        min_day = str(rows[0].created_at)[:10]
        max_day = str(rows[-1].created_at)[:10]
        prefix = f"[{min_day}~{max_day}] "

        body = "；".join(r.content for r in rows)
        # 截断到上限内，且优先保住日期范围前缀完整
        max_body = _COMPACT_SUMMARY_MAX - len(prefix)
        if len(body) > max_body:
            body = body[:max_body]
        summary_text = prefix + body

        imps = [r.importance for r in rows if r.importance is not None]
        avg_imp = (sum(imps) / len(imps)) if imps else 40.0

        db.add(GroupMemory(
            group_id=group_id, user_id=rows[0].user_id,
            round_id=None, speaker_type="system", speaker_id=None,
            content=summary_text, epistemic_status="FACT",
            importance=avg_imp, is_archived=False,
        ))
        # 旧行归档（留痕不删，绝不物理删）
        for r in rows:
            r.is_archived = True
        await db.commit()
        return {"archived": len(rows), "summaries": 1}


async def trim_group_summary_pointers(db, character_id: int, group_id: int,
                                      keep: int = _SUMMARY_KEEP_PER_GROUP) -> None:
    """每角色每群只留最近 keep 条 group_summary 指针，超出软删（is_archived=True，可追溯不物理删）。"""
    try:
        from app.models.memory import Memory
        rows = (await db.execute(
            select(Memory).where(
                Memory.character_id == character_id,
                Memory.source == "group",
                Memory.sub_type == "group_summary",
                Memory.group_id == group_id,
            ).order_by(Memory.id.desc())
        )).scalars().all()
        for old in rows[keep:]:
            old.is_archived = True
    except Exception as e:
        _logger.warning("trim_group_summary_pointers failed char=%s group=%s: %s", character_id, group_id, e)


# ── #72 PR-C 逐角色认知读写原语（P1，2026-09-15）──
# 与 PR-B 共享记忆原语并列：只做原子读写，不做注入、不调 LLM、失败静默只告警；
# 全部受 group_cognition_on() 总闸（关=零行为变化）。预算常量见模块顶部，本包不消费（供 P3）。

async def save_char_cognition(
    *, group_id: int, character_id: int, user_id: int, content: str,
    round_id: str | None = None, topic_key: str | None = None,
    cognition_type: str = "stance", importance: float = 40,
) -> bool:
    """写入一条角色认知（幂等：同 group_id + character_id + round_id/topic_key 不重复写）。

    返回 True=本次新写入；False=被总闸拦截 / 已存在（幂等跳过）/ 异常。
    """
    if not group_cognition_on():
        return False
    try:
        conds = []
        if round_id:
            conds.append(GroupCharCognition.round_id == round_id)
        if topic_key:
            conds.append(GroupCharCognition.topic_key == topic_key)
        async with async_session_factory() as db:
            if conds:
                existed = (await db.execute(
                    select(GroupCharCognition.id).where(
                        GroupCharCognition.group_id == group_id,
                        GroupCharCognition.character_id == character_id,
                        or_(*conds),
                    ).limit(1)
                )).scalar_one_or_none()
                if existed is not None:
                    return False  # 幂等跳过（同轮/同话题已写过）
            db.add(GroupCharCognition(
                group_id=group_id, user_id=user_id, character_id=character_id,
                round_id=round_id, topic_key=topic_key,
                cognition_type=cognition_type, content=content[:600],
                importance=importance,
            ))
            await db.commit()
        return True
    except Exception as e:
        _logger.warning("save_char_cognition failed group=%s char=%s: %s", group_id, character_id, e)
        return False


async def recall_char_cognition(character_id: int, group_id: int, limit: int = _LONGTERM_LIMIT) -> list[str]:
    """读该角色在该群的认知（默认过滤 is_archived）；非本角色/非本群调用方拿不到。

    返回按时间正序的认知内容文本列表（与 recall_group_longterm 同形态，便于 P3 私有注入）。
    """
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(GroupCharCognition)
                .where(
                    GroupCharCognition.group_id == group_id,
                    GroupCharCognition.character_id == character_id,
                    GroupCharCognition.is_archived == False,  # noqa: E712
                )
                .order_by(GroupCharCognition.id.desc()).limit(limit)
            )).scalars().all()
        return [r.content for r in reversed(rows)]
    except Exception as e:
        _logger.warning("recall_char_cognition failed char=%s group=%s: %s", character_id, group_id, e)
        return []


# ── #72 PR-C 预算计数（P3，2026-09-15）──
# 沿用「按 (character_id, 北京时间当日) 数 DB 行」惯例（与 unfinished_topic/life_loop 同构）。
# 计数起点 = beijing_day_start_utc()（北京时间当日 00:00 对应的 UTC naive），created_at 也是 UTC。

async def _count_char_cog_today(character_id: int, group_id: int) -> int:
    """该角色在本群今日的认知生成条数（北京自然日）。异常返回 0（保守，等同于达上限不生成）。"""
    try:
        start = beijing_day_start_utc()
        async with async_session_factory() as db:
            n = (await db.execute(
                select(func.count(GroupCharCognition.id)).where(
                    GroupCharCognition.character_id == character_id,
                    GroupCharCognition.group_id == group_id,
                    GroupCharCognition.created_at >= start,
                )
            )).scalar_one()
        return int(n or 0)
    except Exception as e:
        _logger.warning("count char cog today failed char=%s group=%s: %s", character_id, group_id, e)
        return 0


async def _count_char_cog_cross_today(character_id: int) -> int:
    """该角色跨所有群今日的认知生成总条数（北京自然日，跨群预算用）。异常返回 0。"""
    try:
        start = beijing_day_start_utc()
        async with async_session_factory() as db:
            n = (await db.execute(
                select(func.count(GroupCharCognition.id)).where(
                    GroupCharCognition.character_id == character_id,
                    GroupCharCognition.created_at >= start,
                )
            )).scalar_one()
        return int(n or 0)
    except Exception as e:
        _logger.warning("count char cog cross today failed char=%s: %s", character_id, e)
        return 0


async def _gen_one_cognition(
    *, group_name: str, character_id: int, character_name: str,
    user_id: int, user_content: str, char_reply: str,
) -> str:
    """单角色单轮认知生成（LLM，task=group_cognition，max_tokens<=160）。异常上抛由调用方静默。"""
    from app.agent.llm_client import chat_completion
    prompt = (
        f"你是群聊角色「{character_name}」。在群「{group_name}」里，用户说：「{(user_content or '')[:200]}」，"
        f"你回应了：「{(char_reply or '')[:200]}」。\n"
        "请只用第一人称写一句你本人对这件事的真实立场或看法（stance，不超过 80 字）："
        "用「我觉得/我认为/可能」等表达主观判断；不要重复上面的客观对话内容，也不要写既定事实；"
        "如果没什么特别看法，就写你当下的直观感受。只输出这一句话，不要任何前缀或解释。"
    )
    text = await chat_completion(
        messages=[{"role": "user", "content": prompt}],
        max_tokens=160, temperature=0.8, task="group_cognition",
        user_id=user_id, character_id=character_id,
    )
    if isinstance(text, tuple):
        text = text[0]
    return (text or "").strip()


async def generate_char_cognitions(
    *, group_id: int, user_id: int, user_content: str, replies: list[dict],
    name_map: dict[int, str], round_id: str | None = None, topic_key: str | None = None,
) -> None:
    """#72 PR-C P3（2026-09-15）：为本轮每个真实回应的角色各生成一条属于它自己的认知（stance）。

    - 两级闸（group_cognition_enabled_for）关 → 直接返回，不生成、不写、不注入；
    - 预算：每 (角色,群) 每日 ≤4、跨群每日 ≤10（命中即跳过并记 budget_hit trace，不报错）；
    - 幂等：复用 save_char_cognition 的 round_id/topic_key 去重（同轮/同话题不重复写）；
    - LLM 失败 → 静默降级（记 ok=False trace），绝不阻塞主回复链路；
    - 本函数本身由调用方经 spawn_background 异步调度（不阻塞主回复）。
    """
    if not await group_cognition_enabled_for(group_id):
        return
    try:
        # 群名（用于生成提示；查不到用默认名，不阻塞）
        group_name = "家庭群聊"
        try:
            async with async_session_factory() as db:
                from app.models.chat import ChatGroup
                g = (await db.execute(
                    select(ChatGroup.name).where(ChatGroup.id == group_id)
                )).scalar_one_or_none()
                if g:
                    group_name = g
        except Exception:
            pass
        # 仅给「真的说了话」的角色生成（沉默角色不耗预算）
        speakers = [r for r in (replies or []) if r.get("character_id") and (r.get("content") or "").strip()]
        for r in speakers:
            cid = int(r["character_id"])
            # 预算（关闸已 return，这里只查预算；先查跨群再查单群，命中任一即跳过）
            cross = await _count_char_cog_cross_today(cid)
            if cross >= _CHAR_COG_CROSS_GROUP_DAILY:
                _trace_group_cognition("group_cognition_gen", cid, {
                    "group_id": group_id, "user_id": user_id, "round_id": round_id,
                    "hit_budget": True, "tokens": 0, "ok": False, "source": "cross_group_cap",
                })
                continue
            per = await _count_char_cog_today(cid, group_id)
            if per >= _CHAR_COG_PER_GROUP_DAILY:
                _trace_group_cognition("group_cognition_gen", cid, {
                    "group_id": group_id, "user_id": user_id, "round_id": round_id,
                    "hit_budget": True, "tokens": 0, "ok": False, "source": "per_group_cap",
                })
                continue
            # 生成（LLM）
            try:
                content = await _gen_one_cognition(
                    group_name=group_name, character_id=cid,
                    character_name=name_map.get(cid, "角色"),
                    user_id=user_id, user_content=user_content, char_reply=r["content"],
                )
            except Exception as e:
                _logger.warning("gen char cognition failed char=%s group=%s: %s", cid, group_id, e)
                _trace_group_cognition("group_cognition_gen", cid, {
                    "group_id": group_id, "user_id": user_id, "round_id": round_id,
                    "hit_budget": False, "tokens": 0, "ok": False, "source": "llm_error",
                })
                continue
            if not content:
                _trace_group_cognition("group_cognition_gen", cid, {
                    "group_id": group_id, "user_id": user_id, "round_id": round_id,
                    "hit_budget": False, "tokens": 0, "ok": False, "source": "empty",
                })
                continue
            # 落库（save_char_cognition 自带总闸 + round_id/topic_key 幂等去重）
            ok = await save_char_cognition(
                group_id=group_id, character_id=cid, user_id=user_id,
                content=content, round_id=round_id, topic_key=topic_key,
                cognition_type="stance",
            )
            _trace_group_cognition("group_cognition_gen", cid, {
                "group_id": group_id, "user_id": user_id, "round_id": round_id,
                "hit_budget": False, "tokens": max(0, len(content) // 2),
                "ok": bool(ok), "source": "ok" if ok else "dedup",
            })
    except Exception as e:
        _logger.warning("generate_char_cognitions failed group=%s: %s", group_id, e)
