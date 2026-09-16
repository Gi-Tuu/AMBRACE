# -*- coding: utf-8 -*-
"""#72 PR-C P1+P2：群聊认知升级数据层 + 共享记忆接线测试（2026-09-15）。

纪律：全程用 pytest 会话临时库（conftest 已把 DATABASE_URL 指向 tmp_path 沙箱），不碰真实库；
迁移自测用独立临时 SQLite 文件（subprocess 跑 alembic），同样不碰真实库。
"""
import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: F401  (monkeypatch fixture 由 pytest 注入，保留以明确依赖)

# ────────────── flag 默认关零影响 ──────────────

def test_flag_default_off():
    from app.agent import loop as _loop
    from app.memory import group_memory as gm
    assert _loop.AGENT_FLAGS.get("group_cognition_v2", False) is False
    assert gm.group_cognition_on() is False


def test_budget_constants():
    from app.memory import group_memory as gm
    # 用户拍板 (a)：每（角色,群）每日 4 / 跨群 10
    assert gm._CHAR_COG_PER_GROUP_DAILY == 4
    assert gm._CHAR_COG_CROSS_GROUP_DAILY == 10


# ────────────── 写入/读取幂等 + 角色隔离 + 归档过滤（flag 开）──────────────

async def _clear():
    from app.db.database import async_session_factory
    from app.models.chat import GroupCharCognition
    from sqlalchemy import delete
    async with async_session_factory() as db:
        await db.execute(delete(GroupCharCognition))
        await db.commit()


async def _ensure_parents():
    """插入 FK 父行（users/ai_characters/chat_groups），使 group_char_cognitions 写入不触发外键约束。"""
    from app.db.database import async_session_factory
    from app.models.user import User
    from app.models.character import AICharacter
    from app.models.chat import ChatGroup
    async with async_session_factory() as db:
        await db.merge(User(id=4, username="t4", nickname="T4"))
        await db.merge(AICharacter(id=11, user_id=4, name="C11"))
        await db.merge(AICharacter(id=12, user_id=4, name="C12"))
        await db.merge(ChatGroup(id=1, user_id=4))
        await db.commit()


async def _count():
    from app.db.database import async_session_factory
    from app.models.chat import GroupCharCognition
    from sqlalchemy import func, select
    async with async_session_factory() as db:
        return (await db.execute(select(func.count()).select_from(GroupCharCognition))).scalar_one()


def test_save_idempotent_and_recall_scoped(monkeypatch):
    """同 group+char+round 重复写幂等（仅 1 行）；recall 仅返回本角色认知、不串他人。"""
    from app.agent import loop as _loop
    from app.memory import group_memory as gm
    monkeypatch.setitem(_loop.AGENT_FLAGS, "group_cognition_v2", True)
    asyncio.run(_ensure_parents())
    asyncio.run(_clear())

    async def run():
        w1 = await gm.save_char_cognition(group_id=1, character_id=11, user_id=4, content="A stance", round_id="r1")
        w2 = await gm.save_char_cognition(group_id=1, character_id=11, user_id=4, content="A again", round_id="r1")
        w3 = await gm.save_char_cognition(group_id=1, character_id=12, user_id=4, content="B stance", round_id="r1")
        assert w1 is True and w2 is False and w3 is True  # 同轮同角色幂等跳过
        assert await _count() == 2  # char11 仅 1 条 + char12 1 条
        rec11 = await gm.recall_char_cognition(character_id=11, group_id=1)
        rec12 = await gm.recall_char_cognition(character_id=12, group_id=1)
        assert rec11 == ["A stance"]
        assert rec12 == ["B stance"]
    asyncio.run(run())


def test_recall_filters_archived(monkeypatch):
    """recall_char_cognition 默认过滤 is_archived。"""
    from app.agent import loop as _loop
    from app.memory import group_memory as gm
    from app.db.database import async_session_factory
    from app.models.chat import GroupCharCognition
    monkeypatch.setitem(_loop.AGENT_FLAGS, "group_cognition_v2", True)
    asyncio.run(_ensure_parents())
    asyncio.run(_clear())

    async def run():
        async with async_session_factory() as db:
            db.add(GroupCharCognition(group_id=1, user_id=4, character_id=11, content="old", is_archived=True))
            db.add(GroupCharCognition(group_id=1, user_id=4, character_id=11, content="live"))
            await db.commit()
        assert await gm.recall_char_cognition(character_id=11, group_id=1) == ["live"]
    asyncio.run(run())


def test_gate_off_no_write(monkeypatch):
    """flag 关时 save 不写库、recall 返回空（与现状逐字节一致）。"""
    from app.agent import loop as _loop
    from app.memory import group_memory as gm
    monkeypatch.setitem(_loop.AGENT_FLAGS, "group_cognition_v2", False)
    asyncio.run(_ensure_parents())
    asyncio.run(_clear())

    async def run():
        w = await gm.save_char_cognition(group_id=1, character_id=11, user_id=4, content="x", round_id="r9")
        assert w is False
        assert await gm.recall_char_cognition(character_id=11, group_id=1) == []
        assert await _count() == 0
    asyncio.run(run())


# ────────────── 迁移自测：空库 upgrade head + downgrade + 单头（不碰真实库）──────────────

def test_migration_upgrade_head_and_downgrade_single_head(tmp_path):
    backend = Path(__file__).resolve().parent.parent  # backend/
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db.as_posix()}"
    env = dict(__import__("os").environ)
    env["DATABASE_URL"] = url

    # 1) 空库 upgrade head（整链重放，含本迁移 f3c4d5e6f7a1）
    r1 = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                        cwd=str(backend), env=env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stderr

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "group_char_cognitions" in tables
    assert "memory_write_receipts" in tables  # #70 M3 迁移已并入同一链
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_groups)")}
    assert "cognition_enabled" in cols
    # 索引存在
    idxs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_gcc_group_char" in idxs and "idx_gcc_round" in idxs
    conn.close()

    # 2) 单头校验
    r2 = subprocess.run([sys.executable, "-m", "alembic", "heads"],
                        cwd=str(backend), env=env, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    # 单头校验（不硬编码具体 head：后续新增迁移时本用例无需改）
    head_lines = [ln for ln in r2.stdout.splitlines() if "(head)" in ln]
    assert len(head_lines) == 1, r2.stdout
    assert "f2b3c4d5e6f7 (head)" not in r2.stdout

    # 3) downgrade 到父节点 → 表与列干净移除（可回退）
    r3 = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "f2b3c4d5e6f7"],
                        cwd=str(backend), env=env, capture_output=True, text=True)
    assert r3.returncode == 0, r3.stderr

    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "group_char_cognitions" not in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_groups)")}
    assert "cognition_enabled" not in cols
    conn.close()
