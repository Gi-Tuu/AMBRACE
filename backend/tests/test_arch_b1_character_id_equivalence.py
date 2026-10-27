# -*- coding: utf-8 -*-
"""B1 行为等价：同一合成输入的水位/拒绝语义/序列修复一致（钉具体值与文案）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.db.character_id_guard import CLIENT_SAFE_MAX_ID
from app.db.character_id_references import scan_character_id_watermark as scan_refs
from app.db.migrate import (
    repair_ai_character_sequence,
    scan_character_id_watermark as scan_migrate,
)

DDL = """
CREATE TABLE ai_characters (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, name TEXT);
CREATE TABLE lorebook_entries (id INTEGER PRIMARY KEY, character_id INTEGER);
CREATE TABLE memories (id INTEGER PRIMARY KEY, speaker_id INTEGER, speaker_type TEXT);
"""


@pytest.fixture()
def conn(tmp_path):
    eng = create_engine("sqlite:///" + (tmp_path / "eq.db").as_posix())
    c = eng.connect()
    for stmt in DDL.strip().split(";"):
        if stmt.strip():
            c.exec_driver_sql(stmt)
    c.commit()
    try:
        yield c
    finally:
        c.close()
        eng.dispose()


def _ins(conn, sql):
    conn.exec_driver_sql(sql)
    conn.commit()


def test_同一输入_两入口完全一致(conn):
    _ins(conn, "INSERT INTO ai_characters(id,user_id,name) VALUES(101,1,'A')")
    _ins(conn, "INSERT INTO lorebook_entries(id,character_id) VALUES(1,150)")
    a = scan_migrate(conn)
    b = scan_refs(conn)
    assert a == b
    assert a["high"] == 150


def test_多态判别语义不变(conn):
    _ins(conn, "INSERT INTO ai_characters(id,user_id,name) VALUES(103,1,'A')")
    _ins(conn, "INSERT INTO memories(id,speaker_id,speaker_type) VALUES(1,7,'character')")
    _ins(conn, "INSERT INTO memories(id,speaker_id,speaker_type) VALUES(2,99,'user')")
    assert scan_migrate(conn)["high"] == 103


@pytest.mark.parametrize(
    "sql,needle",
    [
        ("INSERT INTO lorebook_entries(id,character_id) VALUES(1,'abc')", "non-integer"),
        ("INSERT INTO lorebook_entries(id,character_id) VALUES(1,-3)", "negative"),
        ("INSERT INTO memories(id,speaker_id,speaker_type) VALUES(1,42,'alien')", "unknown"),
    ],
)
def test_拒绝语义与文案不变(conn, sql, needle):
    _ins(conn, sql)
    with pytest.raises(RuntimeError) as e1:
        scan_migrate(conn)
    assert needle in str(e1.value)
    with pytest.raises(RuntimeError) as e2:
        scan_refs(conn)
    assert str(e2.value) == str(e1.value)


def test_引用零值允许_超上限拒绝(conn):
    _ins(conn, "INSERT INTO lorebook_entries(id,character_id) VALUES(1,0)")
    assert scan_migrate(conn)["high"] == 0
    _ins(
        conn,
        "INSERT INTO ai_characters(id,user_id,name) VALUES(%d,1,'X')" % (CLIENT_SAFE_MAX_ID + 5),
    )
    with pytest.raises(RuntimeError) as e:
        scan_migrate(conn)
    msg = str(e.value)
    assert ("client-safe" in msg) or ("sequence exhausted" in msg)


def test_序列修复仍在migrate且幂等(conn):
    _ins(conn, "INSERT INTO ai_characters(id,user_id,name) VALUES(105,1,'A')")
    repair_ai_character_sequence(conn)
    conn.commit()
    s1 = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()[0]
    repair_ai_character_sequence(conn)
    conn.commit()
    s2 = conn.exec_driver_sql(
        "SELECT seq FROM sqlite_sequence WHERE name='ai_characters'").fetchone()[0]
    assert int(s1) >= 105 and s1 == s2


# ── 与**开工 HEAD 的冻结实现**做真对照（不是同函数别名）─────────────────
def _baseline_scan():
    """从 git 只读导出开工 HEAD 的旧实现，在测试内隔离 exec，得到**独立**的 scan 函数。

    理由（派单 §1）：`migrate.scan` 现在只是 `references.scan` 的别名，二者相等不能证明
    "改前改后等价"；必须拿改前的源码跑同一输入做 A/B。
    """
    import os
    import subprocess

    repo = Path(__file__).resolve().parents[2]
    src = subprocess.run(
        ["git", "show", "17a0246ef8a489c94a1ef2293c08db05ef85cf4d:backend/app/db/migrate.py"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=True).stdout
    head = src.index("_ROLE_REF_SPECS: dict")
    tail = src.index("def repair_ai_character_sequence(conn) -> dict:")
    block = src[head:tail]
    ns: dict = {}
    exec("from dataclasses import dataclass\n"
         "CLIENT_SAFE_MAX_ID = (1 << 53) - 1\nINT64_MAX = (1 << 63) - 1\n"
         + block, ns)
    return ns["scan_character_id_watermark"]


@pytest.fixture(scope="module")
def baseline():
    try:
        return _baseline_scan()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"无法导出开工 HEAD 的冻结实现：{type(e).__name__}: {e}")


@pytest.mark.parametrize("case", ["ok", "non_integer", "negative", "over_max", "poly_unknown"])
def test_冻结HEAD实现与当前实现逐项对照(conn, baseline, case):
    """同一合成输入：水位 / 拒绝类型 / 拒绝文案必须完全一致。"""
    if case == "ok":
        _ins(conn, "INSERT INTO ai_characters(id,user_id,name) VALUES(101,1,'A')")
        _ins(conn, "INSERT INTO lorebook_entries(id,character_id) VALUES(1,150)")
        assert scan_refs(conn) == baseline(conn)
    elif case == "non_integer":
        _ins(conn, "INSERT INTO lorebook_entries(id,character_id) VALUES(1,'abc')")
    elif case == "negative":
        _ins(conn, "INSERT INTO lorebook_entries(id,character_id) VALUES(1,-3)")
    elif case == "over_max":
        _ins(conn, "INSERT INTO ai_characters(id,user_id,name) VALUES(%d,1,'X')" % (CLIENT_SAFE_MAX_ID + 5))
    else:
        _ins(conn, "INSERT INTO memories(id,speaker_id,speaker_type) VALUES(1,42,'alien')")

    if case != "ok":
        with pytest.raises(RuntimeError) as new_err:
            scan_refs(conn)
        with pytest.raises(RuntimeError) as old_err:
            baseline(conn)
        assert str(new_err.value) == str(old_err.value), "拒绝文案与旧实现不一致"
