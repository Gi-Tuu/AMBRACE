# -*- coding: utf-8 -*-
"""M2 2b 欠账 ②③：感知来源端到端（真实 worker/extractor/save_memory）+ 备份携带 M2 权威表。

② 端到端：真实 chat 消息 → 提取队列 → extraction_worker.process_session → extractor
   （仅 llm_call 替身）→ save_memory → 感知打标 → persist_candidate 绑定 perception_batch。
③ 备份：`scripts/backup.py::_add_sqlite_backup` 打出来的库副本必须带走 5 张 M2 权威表与行。
"""
from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import zipfile
from pathlib import Path

from sqlalchemy import text
from app.utils.timeutil import now_naive_utc

ROOT = Path(__file__).resolve().parents[2]

SNAPSHOT = "微信：张三 明天下午三点开会 记得带材料 地点在会议室"
# 事实正文必须与快照有 >=3 段长词重合（打标判据），否则不会被判成感知来源
SYNTH = "EVENTS: 明天下午三点开会，记得带材料，地点在会议室\n"


def _fake_llm(calls):
    async def fake(*args, **kwargs):
        calls.append(1)
        return SYNTH
    return fake


async def _seed(u, c, s, content):
    from app.db.database import async_session_factory
    from app.application import chat_store

    async with async_session_factory() as db:
        await db.execute(text(
            "INSERT OR IGNORE INTO users(id,username,nickname,password_hash) "
            "VALUES (:u,:n,'n','x')"), {"u": u, "n": f"e2e_{u}"})
        await db.execute(text(
            "INSERT OR IGNORE INTO ai_characters(id,user_id,name,is_partner,talkativeness_locked,"
            "is_active,cognitive_loop_enabled,memory_v2_enabled) VALUES (:c,:u,'c',0,0,1,0,0)"),
            {"c": c, "u": u})
        await db.execute(text(
            "INSERT OR IGNORE INTO chat_sessions(id,user_id,character_id,is_active) "
            "VALUES (:s,:u,:c,1)"), {"s": s, "u": u, "c": c})
        await db.commit()
    mid, _ = await chat_store._persist_user_message(s, u, c, content, True)
    return mid


def _add_snapshot_sync(user_id: int, content: str) -> None:
    from app.db.session import engine
    from app.utils.timeutil import now_naive_utc

    with sqlite3.connect(engine.url.database) as db:
        db.execute("INSERT INTO phone_snapshots(user_id, source, content, created_at) "
                   "VALUES (?, 'accessibility', ?, ?)",
                   (user_id, content, now_naive_utc().strftime("%Y-%m-%d %H:%M:%S")))
        db.commit()


def test_perception_source_end_to_end(monkeypatch):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import extractor as ex
    from app.memory import extraction_worker as worker
    from app.db.database import async_session_factory

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)
    monkeypatch.setitem(AGENT_FLAGS, "perception_source_tag", True)
    calls: list[int] = []
    monkeypatch.setattr(ex, "llm_call", _fake_llm(calls))

    # 共享沙箱 ID 号段（2026-11-08 测试债整改）：915x/916x/917x 归本文件独占；
    # 勿与 ws 930x / mcp_stdio 940x / reallow 948x / reauth 950x / why_g5 955x 混用。
    uid, cid, sid = 9151, 9161, 9171

    async def run():
        msg = await _seed(uid, cid, sid, "明天下午三点开会，记得带材料，地点在会议室")
        _add_snapshot_sync(uid, SNAPSHOT)
        await ex.add_chat_memory_extraction(sid, cid, uid, "明天下午三点开会，记得带材料，地点在会议室",
                                            "synthetic ai", source_id=msg)
        await worker.process_session(sid, cid, uid)
        async with async_session_factory() as db:
            row = (await db.execute(text(
                "SELECT id,source,sub_type,source_event_uid FROM memories "
                "WHERE user_id=:u AND character_id=:c ORDER BY id DESC LIMIT 1"),
                {"u": uid, "c": cid})).mappings().first()
            kind = None
            if row and row["source_event_uid"]:
                kind = (await db.execute(text(
                    "SELECT kind FROM memory_source_events WHERE event_uid=:e"),
                    {"e": row["source_event_uid"]})).scalar_one()
            receipts = (await db.execute(text(
                "SELECT COUNT(*) FROM memory_write_receipts WHERE authoritative=1 "
                "AND owner_user_id=:u AND character_id=:c"),
                {"u": uid, "c": cid})).scalar_one()
        return row, kind, receipts

    row, kind, receipts = asyncio.run(run())
    assert row is not None, "端到端应从真实链路产出一条记忆"
    assert row["source"] == "perception", row
    assert row["source_event_uid"], "感知记忆必须绑定来源事件"
    assert kind == "perception_batch", kind
    assert receipts >= 1, "必须落权威 receipt"


def _load_backup_module():
    spec = importlib.util.spec_from_file_location(
        "backup_mod_debt", ROOT / "scripts" / "backup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backup_carries_m2_authority_tables(monkeypatch, tmp_path):
    from app.flags.agent_flags import AGENT_FLAGS
    from app.memory import source_events as se
    from app.db.database import async_session_factory
    from app.db.session import engine

    monkeypatch.setitem(AGENT_FLAGS, "memory_source_proof_v1", True)

    async def seed():
        db_path = engine.url.database
        from datetime import datetime, timezone

        async with async_session_factory() as db:
            # 用内核正规签发（会推进 source_seq 计数器）；手插会写歪计数器、污染后续用例
            await db.run_sync(lambda sd: se.issue_source_event(
                sd.connection(), kind="chat_message", source_row_id=990001,
                owner_user_id=9151, character_id=9161,
                event_at=(now_naive_utc().isoformat() + "+00:00"), content="backup-probe"))
            await db.commit()
            await db.execute(text(
                "INSERT INTO memory_tombstones(memory_id,owner_user_id,character_id,epoch,"
                "revoked_seq,confirm_seq,state,created_at) VALUES (9151,9151,9161,1,1,0,'closed',"
                "'2026-11-04 00:00:00')"))
            await db.execute(text(
                "INSERT INTO memory_restore_quarantine(event_uid,owner_user_id,character_id,reason,"
                "restored_at) VALUES ('mse-q-1',9151,9161,'synthetic','2026-11-04 00:00:00')"))
            await db.commit()
        return db_path

    db_path = asyncio.run(seed())
    tables = ("source_seq", "memory_source_events", "memory_tombstones",
              "memory_reallow_grants", "memory_restore_quarantine")
    with sqlite3.connect(db_path) as src:
        before = {t: src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    bmod = _load_backup_module()
    # 测试库在 %TEMP%（C:）而项目在 D:，_add_sqlite_backup 用 relpath(DB, SERVER_DIR) 会跨盘报错；
    # 这里把 SERVER_DIR 指到临时库同盘的上级，保持生产口径（同盘）不变。
    monkeypatch.setattr(bmod, "SERVER_DIR", str(Path(db_path).parents[1]))
    zpath = tmp_path / "backup.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        assert bmod._add_sqlite_backup(zf, str(db_path)) == 1
    with zipfile.ZipFile(zpath) as zf:
        name = zf.namelist()[0]
        restored = tmp_path / "restored.db"
        restored.write_bytes(zf.read(name))

    with sqlite3.connect(restored) as dst:
        after = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    assert after == before, f"备份/恢复后 M2 表行数不一致：{before} -> {after}"
    assert after["memory_source_events"] >= 1 and after["memory_tombstones"] >= 1
