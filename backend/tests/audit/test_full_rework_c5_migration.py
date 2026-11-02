# -*- coding: utf-8 -*-
"""P1 返工9 回归：真实历史链 b3 → c5 必须补 chat_messages.source_event_uid。

这是本批全量失败暴露的缺口：旧库/旧测试停在 b3，ORM 已带 source_event_uid 时写入失败。
本用例用真实 Alembic（不 create_all）证明 c5 迁移存在、可升级、可重复。
"""
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import settings

BACKEND = Path(__file__).resolve().parents[2]
B3 = "b3d9e1f5a7c2"
C5 = "c5e7a9b1d3f6"


def _cfg():
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def test_b3_to_c5_adds_chat_source_event_uid(tmp_path, monkeypatch):
    db = tmp_path / "p1_c5.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{db}")
    cfg = _cfg()
    command.upgrade(cfg, B3)
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(chat_messages)")}
        assert "source_event_uid" not in cols
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == B3
    finally:
        con.close()

    command.upgrade(cfg, C5)
    con = sqlite3.connect(str(db))
    try:
        info = {r[1]: r for r in con.execute("PRAGMA table_info(chat_messages)")}
        assert "source_event_uid" in info
        # PRAGMA: (cid, name, type, notnull, dflt, pk)，必须可空、非主键。
        assert info["source_event_uid"][3] == 0 and info["source_event_uid"][5] == 0
        idx = {r[1] for r in con.execute("PRAGMA index_list(chat_messages)")}
        assert "idx_chat_source_event_uid" in idx
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == C5
    finally:
        con.close()

    # 幂等重放：真实升级到同一 head 不报错、不丢列。
    command.upgrade(cfg, C5)
