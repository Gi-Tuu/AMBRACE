# -*- coding: utf-8 -*-
"""3.10 事件流水（P1，方案 §8.4）：domain_events 对账/回填脚本（幂等可重跑）。

按时间窗扫描 ``chat_sessions / chat_messages / ai_moments / moment_comments``，用与在线
**完全一致**的幂等键把缺失事件补齐；已存在的键自动跳过（UQ 冲突静默），重复执行安全。

**安全红线**（对齐 scripts/backfill_memory_chains.py）：本脚本**默认不触碰真实库**——
必须显式传 ``--db-url`` 指向临时库/显式参数库；未传直接报错退出，不回退生产库。

用法（在仓库根目录运行）：
    python scripts/backfill_domain_events.py --db-url "sqlite+aiosqlite:///D:/tmp/evt.db"
    python scripts/backfill_domain_events.py --db-url <url> --aggregate moment --since 2026-09-01
    python scripts/backfill_domain_events.py --db-url <url> --dry-run   # 只打印将补数量，不写
"""
import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# 让 backend 包可导入（脚本位于 <repo>/scripts/，取其父目录/backend）
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "backend"))


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="domain_events 对账/回填（3.10 P1，幂等可重跑）")
    ap.add_argument(
        "--db-url", required=True,
        help="显式数据库 URL（sqlite+aiosqlite:///绝对路径 等）。必须传入，缺省直接报错，不回退生产库。",
    )
    ap.add_argument(
        "--aggregate", default="all", choices=("all", "chat_session", "moment"),
        help="只回填指定聚合域（默认 all）",
    )
    ap.add_argument("--since", default=None, help="起始时间（YYYY-MM-DD 或完整时间戳）")
    ap.add_argument("--until", default=None, help="结束时间（同上）")
    ap.add_argument("--batch", type=int, default=200, help="每批扫描条数（默认 200）")
    ap.add_argument("--dry-run", action="store_true", help="只统计将补数量，不写库")
    return ap.parse_args()


def _evt(**kw) -> dict:
    """待补事件条目（kwargs → dict；便于 dry-run 与实补两段共用同一份结构）。"""
    return kw


def _parse_dt(value: str | None):
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    raise SystemExit(f"[backfill] 无法解析时间: {value}")


async def run(aggregate: str, since, until, batch: int, dry_run: bool) -> int:
    """回填主流程：返回将补（--dry-run）或实补（否则）的事件条数。"""
    from app.agent.loop import AGENT_FLAGS
    from app.db.database import async_session_factory
    from app.events.store import append_domain_event
    from app.events.types import EventType as _ET
    from app.models.chat import ChatMessage, ChatSession
    from app.models.domain_event import DomainEvent
    from app.models.life import AIMoment, MomentComment
    from sqlalchemy import select

    # 回填脚本强制开启事件写入（仅本进程生效，不改 runtime_flags 表）
    AGENT_FLAGS["domain_event_log_enabled"] = True

    async def _existing_keys(keys: list[str]) -> set[str]:
        found: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            async with async_session_factory() as db:
                rows = (await db.execute(
                    select(DomainEvent.idempotency_key).where(
                        DomainEvent.idempotency_key.in_(chunk)
                    )
                )).scalars().all()
            found.update(rows)
        return found

    missing: list[dict] = []   # 待补事件（append_domain_event 的关键字参数）

    # ── 聊天域 ──
    if aggregate in ("all", "chat_session"):
        async with async_session_factory() as db:
            q = select(ChatSession).order_by(ChatSession.id.asc())
            if since:
                q = q.where(ChatSession.created_at >= since)
            if until:
                q = q.where(ChatSession.created_at <= until)
            sessions = (await db.execute(q)).scalars().all()
        s_keys = {f"chat.session_created:chat_session:{s.id}" for s in sessions}
        s_have = await _existing_keys(sorted(s_keys))
        for s in sessions:
            key = f"chat.session_created:chat_session:{s.id}"
            if key in s_have:
                continue
            missing.append(_evt(
                event_type=_ET.CHAT_SESSION_CREATED.value, aggregate_type="chat_session",
                aggregate_id=s.id, entity_type="chat_session", entity_id=s.id,
                actor_type="user", actor_id=getattr(s, "user_id", None),
                payload={"character_id": getattr(s, "character_id", None), "backfilled": True},
                idempotency_key=key, origin="backfill",
            ))

        # 消息（user/ai 同键；AI 侧 route 不可考，统一标 backfill）
        offset = 0
        while True:
            async with async_session_factory() as db:
                q = (
                    select(ChatMessage)
                    .order_by(ChatMessage.id.asc())
                    .limit(batch).offset(offset)
                )
                if since:
                    q = q.where(ChatMessage.created_at >= since)
                if until:
                    q = q.where(ChatMessage.created_at <= until)
                msgs = (await db.execute(q)).scalars().all()
            if not msgs:
                break
            offset += len(msgs)
            m_keys = {f"chat.message_sent:chat_message:{m.id}" for m in msgs}
            m_have = await _existing_keys(sorted(m_keys))
            for m in msgs:
                key = f"chat.message_sent:chat_message:{m.id}"
                if key in m_have:
                    continue
                missing.append(_evt(
                    event_type=_ET.CHAT_MESSAGE_SENT.value, aggregate_type="chat_session",
                    aggregate_id=m.session_id, entity_type="chat_message", entity_id=m.id,
                    actor_type="user" if m.sender_type == "user" else "ai",
                    actor_id=None,
                    payload={"sender_type": m.sender_type, "route": "backfill",
                             "content": (m.content or "")[:200]},
                    idempotency_key=key, origin="backfill",
                ))

    # ── 朋友圈域 ──
    if aggregate in ("all", "moment"):
        async with async_session_factory() as db:
            q = select(AIMoment).order_by(AIMoment.id.asc())
            if since:
                q = q.where(AIMoment.created_at >= since)
            if until:
                q = q.where(AIMoment.created_at <= until)
            moments = (await db.execute(q)).scalars().all()
        mo_keys = {f"moment.published:ai_moment:{m.id}" for m in moments}
        mo_have = await _existing_keys(sorted(mo_keys))
        for m in moments:
            key = f"moment.published:ai_moment:{m.id}"
            if key in mo_have:
                continue
            is_user = (m.sender_type or "") == "user"
            missing.append(_evt(
                event_type=_ET.MOMENT_PUBLISHED.value, aggregate_type="moment",
                aggregate_id=m.id, entity_type="ai_moment", entity_id=m.id,
                actor_type="user" if is_user else "ai",
                actor_id=getattr(m, "user_id", None) if is_user else getattr(m, "character_id", None),
                payload={"sender_type": m.sender_type, "content": (m.content or "")[:200],
                         "backfilled": True},
                idempotency_key=key, origin="backfill",
            ))

        offset = 0
        while True:
            async with async_session_factory() as db:
                q = select(MomentComment).order_by(MomentComment.id.asc()).limit(batch).offset(offset)
                if since:
                    q = q.where(MomentComment.created_at >= since)
                if until:
                    q = q.where(MomentComment.created_at <= until)
                comments = (await db.execute(q)).scalars().all()
            if not comments:
                break
            offset += len(comments)
            c_keys = {f"moment.comment_added:moment_comment:{c.id}" for c in comments}
            c_have = await _existing_keys(sorted(c_keys))
            for c in comments:
                key = f"moment.comment_added:moment_comment:{c.id}"
                if key in c_have:
                    continue
                is_ai = (c.sender_type or "") == "ai"
                missing.append(_evt(
                    event_type=_ET.MOMENT_COMMENT_ADDED.value, aggregate_type="moment",
                    aggregate_id=c.moment_id, entity_type="moment_comment", entity_id=c.id,
                    actor_type="ai" if is_ai else "user",
                    actor_id=getattr(c, "sender_id", None) if is_ai else getattr(c, "user_id", None),
                    payload={"parent_id": c.parent_id, "round": "backfill",
                             "content": (c.content or "")[:200]},
                    idempotency_key=key, origin="backfill",
                ))

    print(f"[backfill] 待补事件 {len(missing)} 条" + ("（dry-run，不写库）" if dry_run else ""),
          flush=True)
    if dry_run:
        for item in missing[:20]:
            print(f"  - {item['event_type']} {item['idempotency_key']}")
        if len(missing) > 20:
            print(f"  ... 其余 {len(missing) - 20} 条省略")
        return len(missing)

    written = 0
    for item in missing:
        try:
            await append_domain_event(
                item["event_type"], item["aggregate_type"], item["aggregate_id"],
                entity_type=item["entity_type"], entity_id=item["entity_id"],
                actor_type=item["actor_type"], actor_id=item["actor_id"],
                payload=item["payload"], idempotency_key=item["idempotency_key"],
                origin=item["origin"],
            )
            written += 1
        except Exception as _e:  # noqa: BLE001 - 单条失败不阻断（幂等可重跑）
            print(f"[backfill] skip {item['idempotency_key']}: {_e}", file=sys.stderr)
    print(f"[backfill] 已补 {written}/{len(missing)} 条", flush=True)
    return written


def main() -> int:
    args = parse_args()
    # 先显式设置 DATABASE_URL，再导入任何 app 模块（config 在导入时读环境变量）
    os.environ["DATABASE_URL"] = args.db_url
    try:
        return asyncio.run(run(args.aggregate, _parse_dt(args.since), _parse_dt(args.until),
                               args.batch, args.dry_run))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
