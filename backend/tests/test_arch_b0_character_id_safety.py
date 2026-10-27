# -*- coding: utf-8 -*-
"""B0 架构验收：角色 ID 水位/引用/上限规则的**隔离**守卫（2026-10-22）。

只读产品代码，全部用 pytest tmp_path 里的合成 SQLite；不连任何真实库。
覆盖：高水位扫描、已删除角色的残留引用、类型/负数/零/NULL 边界、多态判别值、
未登记 character_id（必须拒绝而不是清洗）、客户端上限、序列修复幂等、守卫 trigger。
"""
from __future__ import annotations


import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from app.db.character_id_guard import (
    CLIENT_SAFE_MAX_ID,
    CharacterIdCapacityExceeded,
    ensure_client_safe_id_guard,
    verify_client_safe_id_guard,
)
from app.db.migrate import (
    _reject_unknown_columns,
    _role_ref_columns,
    repair_ai_character_sequence,
    scan_character_id_watermark,
)

# 合成最小 schema：只建规则需要用到的表，不引用产品 models（避免拉起全部业务表）
DDL = """
CREATE TABLE ai_characters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT
);
CREATE TABLE memories (
  id INTEGER PRIMARY KEY,
  character_id INTEGER,
  speaker_id INTEGER,
  speaker_type TEXT
);
CREATE TABLE lorebook_entries (id INTEGER PRIMARY KEY, character_id INTEGER);
CREATE TABLE agent_tasks (id INTEGER PRIMARY KEY, character_id INTEGER);
CREATE TABLE plugin_ext (id INTEGER PRIMARY KEY, character_id INTEGER);  -- 未登记引用
CREATE TABLE ambiguous_ext (id INTEGER PRIMARY KEY, sender_id INTEGER);    -- 未登记多态列
"""


@pytest.fixture()
def conn(tmp_path):
    eng = create_engine(f"sqlite:///{(tmp_path / 'b0.db').as_posix()}")
    c = eng.connect()
    try:
        for stmt in DDL.strip().split(";"):
            if stmt.strip():
                c.exec_driver_sql(stmt)
        c.commit()
        yield c
    finally:
        c.close()
        eng.dispose()


def _ins(conn, table, **cols):
    keys = ", ".join(f'"{k}"' for k in cols)
    vals = ", ".join("NULL" if v is None else (f"'{v}'" if isinstance(v, str) else str(v))
                    for v in cols.values())
    conn.exec_driver_sql(f"INSERT INTO {table} ({keys}) VALUES ({vals})")
    conn.commit()


# ── 4.2 水位与引用 ────────────────────────────────────────────────────────
def test_空库扫描无异常且水位为0(conn):
    scan = scan_character_id_watermark(conn)
    assert scan["high"] == 0, scan


def test_已删除角色的残留引用参与水位且不被删除(conn):
    _ins(conn, "ai_characters", id=5, user_id=1, name="A")
    _ins(conn, "lorebook_entries", id=1, character_id=9)   # 角色 9 已不存在
    scan = scan_character_id_watermark(conn)
    assert scan["high"] == 9, scan
    rows = conn.exec_driver_sql("SELECT COUNT(*) FROM lorebook_entries").fetchone()[0]
    assert rows == 1, "残留引用不得被清洗/重绑"


def test_引用列非整数被拒绝(conn):
    _ins(conn, "lorebook_entries", id=1, character_id="abc")
    with pytest.raises(RuntimeError) as ei:
        scan_character_id_watermark(conn)
    assert "non-integer" in str(ei.value)


def test_引用列负数被拒绝(conn):
    _ins(conn, "lorebook_entries", id=1, character_id=-3)
    with pytest.raises(RuntimeError) as ei:
        scan_character_id_watermark(conn)
    assert "negative" in str(ei.value)


def test_引用列零值允许作哨兵(conn):
    _ins(conn, "lorebook_entries", id=1, character_id=0)
    scan = scan_character_id_watermark(conn)      # 不抛
    assert scan["high"] == 0


def test_引用列NULL允许(conn):
    _ins(conn, "lorebook_entries", id=1, character_id=None)
    scan = scan_character_id_watermark(conn)      # NULL 不构成引用
    assert scan["high"] == 0


def test_多态引用_角色判别计入水位_用户判别不计入(conn):
    _ins(conn, "memories", id=1, speaker_id=7, speaker_type="character")
    _ins(conn, "memories", id=2, speaker_id=99, speaker_type="user")
    scan = scan_character_id_watermark(conn)
    assert scan["high"] == 7, scan


def test_多态引用_未知判别值带正id被拒绝(conn):
    _ins(conn, "memories", id=1, speaker_id=42, speaker_type="alien")
    with pytest.raises(RuntimeError) as ei:
        scan_character_id_watermark(conn)
    assert "unknown" in str(ei.value)


def test_未登记character_id列有数据时拒绝猜测且不动数据(conn):
    """未登记的 character_id 列**有值** ⇒ 写前拒绝（不猜/不洗/不重绑）。"""
    _ins(conn, "plugin_ext", id=1, character_id=4)
    known, unknown_cid, unknown_amb = _role_ref_columns(conn)
    assert ("plugin_ext", "character_id") in unknown_cid
    assert ("ambiguous_ext", "sender_id") in unknown_amb
    with pytest.raises(RuntimeError) as ei:
        scan_character_id_watermark(conn)
    assert "refusing to guess/clean/rebind" in str(ei.value)
    rows = conn.exec_driver_sql("SELECT COUNT(*) FROM plugin_ext").fetchone()[0]
    assert rows == 1, "拒绝时不得删除/改型/重绑数据"
    val = conn.exec_driver_sql("SELECT character_id FROM plugin_ext").fetchone()[0]
    assert int(val) == 4, "原值必须保留"


def test_未登记character_id列无数据时仅报告不拦截(conn):
    known, unknown_cid, unknown_amb = _role_ref_columns(conn)
    assert ("plugin_ext", "character_id") in unknown_cid
    scan = scan_character_id_watermark(conn)
    assert any("plugin_ext" in r for r in scan["unknown_columns"]), scan["unknown_columns"]
    assert _reject_unknown_columns is not None


def test_超过客户端上限被拒绝(conn):
    _ins(conn, "ai_characters", id=CLIENT_SAFE_MAX_ID + 10, user_id=1, name="X")
    with pytest.raises(RuntimeError) as ei:
        scan_character_id_watermark(conn)
    assert "client-safe" in str(ei.value) or "sequence exhausted" in str(ei.value)


def test_等于客户端上限可以通过(conn):
    _ins(conn, "ai_characters", id=CLIENT_SAFE_MAX_ID, user_id=1, name="X")
    scan = scan_character_id_watermark(conn)
    assert scan["high"] == CLIENT_SAFE_MAX_ID


# ── 序列修复 ─────────────────────────────────────────────────────────────
def test_序列修复只改sqlite_sequence不改schema(conn):
    _ins(conn, "ai_characters", id=5, user_id=1, name="A")
    _ins(conn, "ai_characters", id=11, user_id=1, name="B")
    ddl_before = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'").fetchone()[0]
    out = repair_ai_character_sequence(conn)
    conn.commit()
    ddl_after = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_characters'").fetchone()[0]
    assert ddl_after == ddl_before, "不得改 schema"
    seq = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()
    assert seq is not None and int(seq[0]) >= 11, seq
    assert out is not None


def test_序列修复重复执行幂等(conn):
    _ins(conn, "ai_characters", id=7, user_id=1, name="A")
    repair_ai_character_sequence(conn)
    conn.commit()
    s1 = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()
    repair_ai_character_sequence(conn)
    conn.commit()
    s2 = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()
    assert s1 == s2, (s1, s2)


# ── 客户端范围守卫 ────────────────────────────────────────────────────────
def test_守卫安装后可拦截超上限id且校验一致(conn):
    ensure_client_safe_id_guard(conn)
    conn.commit()
    verify = verify_client_safe_id_guard(conn)
    assert verify.get("present") is True, verify
    assert verify.get("triggers"), verify
    with pytest.raises(SAIntegrityError):
        conn.exec_driver_sql("INSERT INTO ai_characters (id, user_id, name) VALUES (?,?,?)",
                             (CLIENT_SAFE_MAX_ID + 1, 1, "X"))
    conn.rollback()


def test_安装守卫在超上限数据上拒绝而不是放行(conn):
    _ins(conn, "ai_characters", id=CLIENT_SAFE_MAX_ID + 5, user_id=1, name="X")
    with pytest.raises(Exception) as ei:
        ensure_client_safe_id_guard(conn)
    msg = str(ei.value)
    assert "client-safe" in msg or "max=" in msg or isinstance(ei.value, CharacterIdCapacityExceeded), msg
