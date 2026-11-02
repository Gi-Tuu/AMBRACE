# -*- coding: utf-8 -*-
"""M2 2a 权威内核合同：隔离/代际/墓碑/grant/原子回滚。"""
from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, text

from app.models.base import Base
from app.models.memory import (
    Memory,
    MemoryReallowGrant,
    MemoryRestoreQuarantine,
    MemorySourceEvent,
    MemoryTombstone,
    MemoryWriteReceipt,
    SourceSeq,
)
from app.memory import source_events as se


TABLES = [SourceSeq.__table__, MemorySourceEvent.__table__, Memory.__table__,
          MemoryWriteReceipt.__table__, MemoryTombstone.__table__,
          MemoryReallowGrant.__table__, MemoryRestoreQuarantine.__table__]


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'm2.db'}", future=True)
    Base.metadata.create_all(eng, tables=TABLES)
    with eng.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO source_seq(id,seq) VALUES (1,0)"))
    yield eng
    eng.dispose()


def _source(engine, row=2001, owner=7, character=11, content="v1"):
    return se.issue_source_event(
        engine, kind="chat_message", source_row_id=row, owner_user_id=owner,
        character_id=character, event_at="2026-10-27T10:00:00+08:00", content=content)


def _write(engine, uid, memory_id=9001, owner=7, character=11, actor=7, idem="i1",
           grant=None, fail=None):
    return se.write_authoritative_memory(
        engine, event_uid=uid, memory_id=memory_id, owner_user_id=owner,
        character_id=character, actor_kind="authenticated_user", actor_user_id=actor,
        action="create", idempotency_key=idem, grant_token=grant,
        memory_values={"user_id": owner, "character_id": character,
                       "memory_type": "event", "content": "synthetic"},
        fail_before_commit=fail)


def _tomb(engine):
    e = _source(engine)
    assert _write(engine, e, idem="base") == "accepted"
    se.forget_memory(engine, memory_id=9001, owner_user_id=7, character_id=11)
    return e


def test_quarantined_write_is_refused_without_side_effects(engine):
    e = _source(engine)
    se.quarantine_restore(engine, event_uid=e, reason="backup_no_external")
    with engine.connect() as conn:
        assert not se.visible(conn, e)
    assert _write(engine, e, idem="q") == "refused_quarantined"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM memory_write_receipts")).scalar_one() == 0


def test_current_generation_invalidates_old_uid(engine):
    old = _source(engine, row=2001, content="gen1")
    new = _source(engine, row=2001, content="gen2")
    assert _write(engine, old, idem="old") == "refused_stale_generation"
    assert _write(engine, new, idem="new") == "accepted"


def test_reallowed_active_and_old_grant(engine):
    _tomb(engine)
    _source(engine, row=2001, content="v2")
    token = se.reallow_memory(engine, memory_id=9001, owner_user_id=7,
                              character_id=11, confirm_seq=se.current_seq(engine)
                              if hasattr(se, "current_seq") else 2)
    new = _source(engine, row=2002, content="new")
    assert _write(engine, new, idem="first", grant=token) == "accepted"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=9001")).scalar_one() == "reallowed_active"
    later = _source(engine, row=2003, content="later")
    assert _write(engine, later, idem="later") == "accepted"
    assert _write(engine, later, idem="oldgrant", grant=token) == "refused_bad_grant"


def test_retained_tombstone_checks_scope_without_live_memory(engine):
    _tomb(engine)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM memories WHERE id=9001"))
    attacker = _source(engine, row=3001, owner=8, character=12)
    assert _write(engine, attacker, owner=8, character=12, actor=8, idem="attacker") == "refused_tombstone_scope"


def test_active_update_rejects_foreign_unused_grant(engine):
    _tomb(engine)
    _source(engine, row=2001, content="v2")
    token = se.reallow_memory(engine, memory_id=9001, owner_user_id=7,
                              character_id=11, confirm_seq=se.current_seq(engine))
    new = _source(engine, row=2002, content="new")
    assert _write(engine, new, idem="first", grant=token) == "accepted"
    second = _source(engine, row=2002, content="second")
    assert _write(engine, second, memory_id=9002, idem="second") == "accepted"
    se.forget_memory(engine, memory_id=9002, owner_user_id=7, character_id=11)
    foreign = se.reallow_memory(engine, memory_id=9002, owner_user_id=7,
                                character_id=11, confirm_seq=se.current_seq(engine))
    later = _source(engine, row=2004, content="later")
    assert _write(engine, later, idem="foreign", grant=foreign) == "refused_bad_grant"
    assert _write(engine, later, idem="legit") == "accepted"


def test_failure_injection_rolls_back_tombstone_and_grant(engine):
    _tomb(engine)
    _source(engine, row=2001, content="v2")
    token = se.reallow_memory(engine, memory_id=9001, owner_user_id=7,
                              character_id=11, confirm_seq=se.current_seq(engine))
    new = _source(engine, row=2002, content="new")
    for point in ("grant", "memory", "receipt", "commit"):
        with pytest.raises(se.KernelError):
            _write(engine, new, idem="fail-" + point, grant=token, fail=point)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=9001")).scalar_one() == "reallowed"
            assert conn.execute(text("SELECT COUNT(*) FROM memory_write_receipts WHERE idempotency_key=:k"),
                               {"k": "fail-" + point}).scalar_one() == 0
            th = hashlib.sha256(token.encode()).hexdigest()
            assert conn.execute(text("SELECT used FROM memory_reallow_grants WHERE token_hash=:h"),
                               {"h": th}).scalar_one() == 0


def test_source_revoke_and_multiple_connections(engine):
    e = _source(engine)
    assert se.revoke_source_event(engine, event_uid=e, owner_user_id=7, character_id=11)
    assert _write(engine, e, idem="revoked") == "refused_missing_source"
    e2 = _source(engine, row=2002, content="new")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: _write(engine, e2, idem="race"), range(2)))
    assert sorted(results) == ["accepted", "duplicate"], results


def test_outer_transaction_rolls_back_message_event_and_seq(engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS fake_chat_messages(id INTEGER PRIMARY KEY, body TEXT)"))
    with engine.connect() as conn:
        conn.begin()
        conn.execute(text("INSERT INTO fake_chat_messages(id,body) VALUES (1,'msg')"))
        uid = se.issue_source_event_tx(
            conn, kind="chat_message", source_row_id=1, owner_user_id=7,
            character_id=11, event_at="2026-10-27T10:00:00+08:00", content="msg")
        conn.rollback()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM fake_chat_messages")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM memory_source_events WHERE event_uid=:u"),
                           {"u": uid}).scalar_one() == 0
        assert conn.execute(text("SELECT seq FROM source_seq WHERE id=1")).scalar_one() == 0


def test_write_tx_outer_rollback(engine):
    e = _source(engine)
    with engine.connect() as conn:
        conn.begin()
        assert se.write_authoritative_memory_tx(
            conn, event_uid=e, memory_id=9001, owner_user_id=7, character_id=11,
            actor_kind="authenticated_user", actor_user_id=7, action="create",
            idempotency_key="outer", memory_values={"memory_type":"event","content":"x"}) == "accepted"
        conn.rollback()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM memories")).scalar_one() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM memory_write_receipts")).scalar_one() == 0


def test_action_and_idempotency_scope(engine):
    e = _source(engine)
    assert _write(engine, e, idem="") == "refused_empty_idempotency_key"
    assert se.write_authoritative_memory(
        engine, event_uid=e, memory_id=9001, owner_user_id=7, character_id=11,
        actor_kind="authenticated_user", actor_user_id=7, action="bogus",
        idempotency_key="a", memory_values={"content": "x", "memory_type": "event"}) == "refused_bad_action"
    assert _write(engine, e, idem="a") == "accepted"
    assert se.write_authoritative_memory(
        engine, event_uid=e, memory_id=9001, owner_user_id=7, character_id=11,
        actor_kind="authenticated_user", actor_user_id=7, action="update",
        idempotency_key="a", memory_values={"content": "y"}) == "refused_idempotency_conflict"


def test_backup_restore_recovery_lock(tmp_path, engine):
    import sqlite3
    e = _source(engine)
    assert _write(engine, e, idem="base") == "accepted"
    seq_before = se.current_seq(engine)
    restored = tmp_path / "restored.db"
    src = sqlite3.connect(str(engine.url.database)); dst = sqlite3.connect(str(restored))
    src.backup(dst); dst.close(); src.close()
    r_engine = create_engine(f"sqlite:///{restored}", future=True)
    try:
        se.lock_recovery(r_engine, reason="restore_no_manifest")
        with r_engine.connect() as conn:
            assert se.recovery_locked(conn)
            assert not se.visible(conn, e)
        with pytest.raises(se.KernelError):
            _source(r_engine, row=2002, content="after_restore")
        assert se.current_seq(r_engine) == seq_before
    finally:
        r_engine.dispose()


def test_plaintext_not_stored_and_owner_constraints(engine):
    e = _source(engine, content="SYNTHETIC_PRIVATE_SENTINEL")
    assert _write(engine, e) == "accepted"
    with engine.connect() as conn:
        vals = []
        for table in ("memory_source_events", "memory_write_receipts", "memories"):
            for row in conn.execute(text("SELECT * FROM " + table)).mappings():
                vals.extend(str(v) for v in row.values() if isinstance(v, str))
        assert not any("SYNTHETIC_PRIVATE_SENTINEL" in v for v in vals)
    with pytest.raises(Exception):
        _source(engine, row=9, owner=0)
