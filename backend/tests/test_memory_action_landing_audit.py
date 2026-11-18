# -*- coding: utf-8 -*-
"""RC1 落库级判据（diagnostics 脚本）合同：合成临时库，不读真实数据。"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "diagnostics" / "memory_action_landing_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("landing_audit", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "audit.db"
    c = sqlite3.connect(p)
    c.executescript(
        """
        CREATE TABLE chat_sessions(id INTEGER PRIMARY KEY, user_id INT, character_id INT);
        CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, session_id INT, sender_type TEXT,
                                   content TEXT, created_at TEXT);
        CREATE TABLE memories(id INTEGER PRIMARY KEY, user_id INT, character_id INT, created_at TEXT);
        CREATE TABLE calendar_notes(id INTEGER PRIMARY KEY, character_id INT, created_at TEXT);
        CREATE TABLE memo_notes(id INTEGER PRIMARY KEY, character_id INT, created_at TEXT);
        INSERT INTO chat_sessions VALUES (1, 3, 13);
        """
    )
    c.commit()
    return p, c


def _msg(c, mid, content, at):
    c.execute("INSERT INTO chat_messages VALUES (?,1,'ai',?,?)", (mid, content, at))


def test_missing_and_landed_classes(db):
    p, c = db
    # 标记落库
    _msg(c, 1, "好，记下了【MEMO】明天交房租[/MEMO]", "2026-11-13 10:00:00")
    # 通道落库：10s 后有记忆
    _msg(c, 2, "好，我记住了，你女儿叫朵朵", "2026-11-13 11:00:00")
    c.execute("INSERT INTO memories VALUES (1,3,13,'2026-11-13 11:00:10')")
    # missing：说了记但窗口内无任何落库
    _msg(c, 3, "行，记牢了，周五带合同", "2026-11-13 12:00:00")
    # 非「说了记」措辞 → 不计入
    _msg(c, 4, "今天天气不错", "2026-11-13 13:00:00")
    # 会话外角色（同角色 13），日历落库
    _msg(c, 5, "记下了，提醒你下午开会", "2026-11-13 14:00:00")
    c.execute("INSERT INTO calendar_notes VALUES (1,13,'2026-11-13 14:00:30')")
    c.commit(); c.close()

    out = _load().audit(p, since="2026-11-13 00:00:00", window_s=120)
    assert out["total_said"] == 4
    assert out["marker_landed"] == 1
    assert out["channel_landed"] == 2
    assert out["missing"] == 1
    assert out["missing_samples"][0]["message_id"] == 3
    assert out["missing_ratio_pct"] == 25.0


def test_window_boundary_and_empty(db):
    p, c = db
    _msg(c, 1, "好记下了", "2026-11-13 10:00:00")
    # 落库在窗口外（>120s）
    c.execute("INSERT INTO memo_notes VALUES (1,13,'2026-11-13 10:05:00')")
    c.commit(); c.close()
    out = _load().audit(p, since="2026-11-13 00:00:00", window_s=120)
    assert out["total_said"] == 1
    assert out["missing"] == 1
    empty = _load().audit(p, since="2027-01-01 00:00:00", window_s=120)
    assert empty["total_said"] == 0 and empty["missing_ratio_pct"] == 0.0
