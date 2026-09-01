# -*- coding: utf-8 -*-
"""工作记忆服务（M3-a，2026-09-01）：turn 结束后异步评估/滚动覆盖 working_state 行。

docs/设计_M3工作记忆_20260901.md：
- 每 (user_id, character_id) 一条活跃行（判定=id 降序最新，不依赖 supersede 过滤——P0 修订）；
- 提取：LLM 输出"完整期望三桶"，服务端 diff（app/memory/working_state.py）；
- 证据门控：evidence_ids 统一 Memory.id，只认本轮新增且真实存在的记忆；
- 节流：每角色 30 分钟最多评估一次；diff 无变化不写；
- fail-open：任何异常静默，绝不阻塞回复；
- 写侧无条件标 superseded（与 events/facts.py 对齐，P1-2）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.agent import llm_client as _llm  # M3-a：模块级导入（无循环）；测试在 service 边界 patch
from app.memory import working_state as ws
from app.memory.observability import obs_event
from app.utils.logger import get_logger

_logger = get_logger("memory.working_state")

_THROTTLE = 1800  # 30 分钟（秒）
_MEM_TYPES_EXCLUDED_IN_PROMPT = 8  # 展示截断


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_latest(db, user_id: int, character_id: int):
    """该角色最新一条 working_state 行（P0：id 降序，不依赖状态过滤）。"""
    from app.models.memory import Memory
    res = await db.execute(
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.character_id == character_id,
            Memory.memory_type == "working_state",
        )
        .order_by(Memory.id.desc())
        .limit(1)
    )
    return res.scalar_one_or_none()


def _build_prompt(current: dict | None, user_text: str, ai_text: str,
                  evidence: list[tuple[int, str]]) -> str:
    cur_json = json.dumps(current, ensure_ascii=False) if current else "null（尚无工作记忆）"
    ev_lines = "\n".join(f"  - [{mid}] {summary}" for mid, summary in evidence[:_MEM_TYPES_EXCLUDED_IN_PROMPT]) \
        or "  （本轮没有新增记忆）"
    return (
        "你在维护一条角色的工作记忆（当前进行中的事/关系备注/未决问题）。\n"
        f"当前工作记忆：{cur_json}\n\n"
        f"本轮对话：\n用户说：{user_text[:300]}\nAI 回复：{ai_text[:300]}\n\n"
        f"本轮新增的长时记忆（id: 摘要，可作 evidence_ids 引用）：\n{ev_lines}\n\n"
        "请输出更新后的【完整】工作记忆 JSON（不是 diff），三桶结构：\n"
        '{"ongoing": [{"topic": "...", "detail": "...", "evidence_ids": [id]}], '
        '"relationship_notes": [{"note": "...", "evidence_ids": [id]}], '
        '"open_questions": [{"question": "...", "evidence_ids": [id]}]}\n'
        "规则：只保留仍然成立/进行中的条目（已完结的移除）；每条必须引用上面真实存在的记忆 id"
        "（没有相关记忆就引用本轮对话对应的新增记忆 id；完全无依据的条目不要输出）；"
        "每桶最多 3 条；没有变化的桶原样保留。只输出 JSON，不要多余文字。"
    )


async def maybe_evaluate_working_state(
    user_id: int, character_id: int, session_id: int | None,
    user_text: str, ai_text: str,
) -> None:
    """turn 结束后评估工作记忆（fail-open：任何异常只记日志）。"""
    try:
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("working_state_enabled", False):
            return
    except Exception:
        return

    try:
        from app.db.database import async_session_factory
        from app.models.memory import Memory

        async with async_session_factory() as db:
            latest = await get_latest(db, user_id, character_id)
            now = _now_naive()
            if latest is not None and latest.created_at is not None:
                if (now - latest.created_at).total_seconds() < _THROTTLE:
                    return  # 节流
            current = None
            if latest is not None and latest.content:
                try:
                    current = json.loads(latest.content)
                except Exception:
                    current = None

            # 本轮新增记忆（evidence 唯一来源；排除工作记忆自身）
            ev_rows = (await db.execute(
                select(Memory.id, Memory.title, Memory.content)
                .where(
                    Memory.character_id == character_id,
                    Memory.created_at >= _now_naive() - timedelta(minutes=10),  # M3-a：本轮新增记忆窗口
                    Memory.is_archived == False,  # noqa: E712
                    Memory.memory_type != "working_state",
                )
                .order_by(Memory.id.desc())
                .limit(20)
            )).fetchall()
            evidence = [(r[0], (r[1] or r[2] or "")[:80]) for r in ev_rows]
            valid_ids = {r[0] for r in ev_rows}

            prompt = _build_prompt(current, user_text, ai_text, evidence)
        # LLM 调用放 session 外（不占连接）
        text = await _llm.chat_completion(
            messages=[
                {"role": "system", "content": "你是一个输出 JSON 的助手，直接输出 JSON，不要多余文字。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=600,
            task="memory",
            user_id=user_id,
        )
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            desired = ws.validate_desired(json.loads(raw))
        except Exception:
            desired = None
        if desired is None:
            obs_event(character_id, "working_state_skipped", {"reason": "bad_json"})
            return

        new_content, stats = ws.apply_desired(current, desired, valid_ids, _now_naive().isoformat())
        if new_content is None:
            obs_event(character_id, "working_state_skipped", {"reason": "no_change", **stats})
            return

        async with async_session_factory() as db:
            latest = await get_latest(db, user_id, character_id)
            new_row = Memory(
                user_id=user_id, character_id=character_id,
                memory_type="working_state", content=json.dumps(new_content, ensure_ascii=False),
                scope="private", source="system", epistemic_status="FACT",
                importance=60.0, title=None,
            )
            db.add(new_row)
            await db.flush()
            # 写侧无条件标 superseded（P1-2：与 events/facts.py 对齐；读取侧不依赖它）
            if latest is not None and latest.status == "active":
                latest.status = "superseded"
                latest.superseded_by = new_row.id
                db.add(latest)
            await db.commit()
            obs_event(character_id, "working_state_updated", {**stats, "row_id": new_row.id})
        _logger.info("working_state updated char=%d stats=%s", character_id, stats)
    except Exception as e:
        _logger.warning("working_state evaluate failed char=%s: %s", character_id, e)
