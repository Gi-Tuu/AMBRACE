import pytest
from sqlalchemy import inspect, text
from test_memory_source_event_v1 import engine as _engine_fixture, _source, _write
engine = _engine_fixture
from test_memory_source_event_migration import _run
from app.memory import source_events as se
from sqlalchemy import create_engine


def test_memory_values_cannot_override_authenticated_owner(engine):
    uid=_source(engine)
    result=se.write_authoritative_memory(engine,event_uid=uid,memory_id=9001,
        owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=7,
        action="create",idempotency_key="forged_owner",
        memory_values={"user_id":8,"character_id":12,"content":"synthetic","memory_type":"event"})
    with engine.connect() as c:
        row=c.execute(text("SELECT user_id,character_id FROM memories WHERE id=9001")).first()
        assert row is None or tuple(row)==(7,11),(result,tuple(row))


def test_update_values_cannot_move_primary_key(engine):
    uid=_source(engine)
    assert _write(engine,uid,idem="create")=="accepted"
    result=se.write_authoritative_memory(engine,event_uid=uid,memory_id=9001,
        owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=7,
        action="update",idempotency_key="move_id",
        memory_values={"id":9999,"content":"changed"})
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM memories WHERE id=9999")).scalar_one()==0,result
        assert c.execute(text("SELECT COUNT(*) FROM memories WHERE id=9001")).scalar_one()==1


def test_real_history_has_declared_source_foreign_keys(tmp_path):
    r=_run(tmp_path,"upgrade","head")
    assert r.returncode==0,r.stderr[-1000:]
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    try:
        for table in ("memories","memory_write_receipts"):
            fks=inspect(eng).get_foreign_keys(table)
            assert any(x["constrained_columns"]==["source_event_uid"] and
                       x["referred_table"]=="memory_source_events" for x in fks),(table,fks)
    finally: eng.dispose()


def test_metadata_restricts_source_seq_to_single_row(engine):
    with pytest.raises(Exception):
        with engine.begin() as c:c.execute(text("INSERT INTO source_seq(id,seq) VALUES (2,0)"))


def test_tx_wrong_actor_never_becomes_authoritative(engine):
    uid=_source(engine)
    with engine.begin() as conn:
        result=se.write_authoritative_memory_tx(conn,event_uid=uid,memory_id=9001,
            owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=888,
            action="create",idempotency_key="wrong_actor_tx",
            memory_values={"content":"synthetic","memory_type":"event"})
    with engine.connect() as conn:
        n=conn.execute(text("SELECT COUNT(*) FROM memory_write_receipts WHERE actor_user_id=888")).scalar_one()
    assert result.startswith("refused") and n==0,(result,n)


def test_tx_failure_is_not_swallowed_and_cannot_commit_partial_write(engine):
    uid=_source(engine)
    with pytest.raises(se.KernelError):
        with engine.begin() as conn:
            se.write_authoritative_memory_tx(conn,event_uid=uid,memory_id=9001,
                owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=7,
                action="create",idempotency_key="fail_tx",
                memory_values={"content":"synthetic","memory_type":"event"},
                fail_before_commit="memory")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM memories")).scalar_one()==0


def test_partial_column_without_fk_is_repaired_or_rejected(tmp_path):
    r=_run(tmp_path,"upgrade","d6f8a0b2c4e6")
    assert r.returncode==0,r.stderr[-800:]
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    with eng.begin() as c:
        c.execute(text("ALTER TABLE memories ADD COLUMN source_event_uid VARCHAR(64)"))
    eng.dispose()
    r=_run(tmp_path,"upgrade","head")
    if r.returncode != 0:
        return
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    try:
        assert any(x["constrained_columns"]==["source_event_uid"] and
                   x["referred_table"]=="memory_source_events"
                   for x in inspect(eng).get_foreign_keys("memories")), "stamped head with missing source FK"
    finally: eng.dispose()


def test_refused_tx_does_not_consume_grant_when_caller_commits(engine):
    uid=_source(engine)
    assert _write(engine,uid,idem="original")=="accepted"
    se.forget_memory(engine,memory_id=9001,owner_user_id=7,character_id=11)
    token=se.reallow_memory(engine,memory_id=9001,owner_user_id=7,character_id=11,
                           confirm_seq=se.current_seq(engine))
    fresh=_source(engine,row=2002,content="new")
    with engine.begin() as conn:
        result=se.write_authoritative_memory_tx(conn,event_uid=fresh,memory_id=9001,
            owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=7,
            action="update",idempotency_key="refused_empty_business",memory_values={},grant_token=token)
    assert result.startswith("refused"),result
    with engine.connect() as conn:
        used=conn.execute(text("SELECT used FROM memory_reallow_grants WHERE token_hash=:h"),
                         {"h":__import__("hashlib").sha256(token.encode()).hexdigest()}).scalar_one()
        state=conn.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=9001")).scalar_one()
        assert (used,state)==(0,"reallowed"),(result,used,state)


def test_same_check_name_wrong_definition_does_not_stamp(tmp_path):
    r=_run(tmp_path,"upgrade","d6f8a0b2c4e6")
    assert r.returncode==0,r.stderr[-800:]
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE source_seq(id INTEGER NOT NULL PRIMARY KEY,seq INTEGER NOT NULL,CONSTRAINT ck_source_seq_single_row CHECK(id > 0))"))
    eng.dispose()
    r=_run(tmp_path,"upgrade","head")
    if r.returncode != 0:
        return
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    try:
        with pytest.raises(Exception):
            with eng.begin() as c:c.execute(text("INSERT INTO source_seq(id,seq) VALUES (2,0)"))
    finally:eng.dispose()


def test_caught_exception_cannot_commit_grant_or_tombstone_side_effects(engine):
    uid=_source(engine)
    assert _write(engine,uid,idem="before")=="accepted"
    se.forget_memory(engine,memory_id=9001,owner_user_id=7,character_id=11)
    token=se.reallow_memory(engine,memory_id=9001,owner_user_id=7,character_id=11,
                           confirm_seq=se.current_seq(engine))
    fresh=_source(engine,row=2002,content="new")
    with engine.begin() as c:
        with pytest.raises(se.KernelError):
            se.write_authoritative_memory_tx(c,event_uid=fresh,memory_id=9001,
                owner_user_id=7,character_id=11,actor_kind="authenticated_user",actor_user_id=7,
                action="update",idempotency_key="caught_error",memory_values={"content":"updated"},
                grant_token=token,fail_before_commit="grant")
    with engine.connect() as c:
        used=c.execute(text("SELECT used FROM memory_reallow_grants WHERE token_hash=:h"),
                       {"h":__import__("hashlib").sha256(token.encode()).hexdigest()}).scalar_one()
        state=c.execute(text("SELECT state FROM memory_tombstones WHERE memory_id=9001")).scalar_one()
        assert (used,state)==(0,"reallowed"),(used,state)


@pytest.mark.parametrize("point",["columns","indexes"])
def test_remaining_real_migration_fault_points_resume(tmp_path,point):
    import os,sys,subprocess
    r=_run(tmp_path,"upgrade","d6f8a0b2c4e6")
    assert r.returncode==0,r.stderr[-800:]
    env=os.environ.copy();env["DATABASE_URL"]=f"sqlite:///{tmp_path / 'history.db'}";env["M2_2A_FAULT"]=point
    r=subprocess.run([sys.executable,"-m","alembic","-c","alembic.ini","upgrade","head"],
        cwd=__import__("pathlib").Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,encoding="utf-8",timeout=180)
    assert r.returncode != 0
    r=_run(tmp_path,"upgrade","head")
    assert r.returncode==0,r.stderr[-1600:]
    eng=create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    try:
        with eng.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="c5e7a9b1d3f6"
        chat_cols = {c["name"] for c in inspect(eng).get_columns("chat_messages")}
        assert "source_event_uid" in chat_cols
        chat_indexes = {i["name"]: i["column_names"] for i in inspect(eng).get_indexes("chat_messages")}
        assert chat_indexes["idx_chat_source_event_uid"] == ["source_event_uid"]
        assert any(x["constrained_columns"]==["source_event_uid"] for x in inspect(eng).get_foreign_keys("memories"))
    finally:eng.dispose()


def test_recovery_lock_rejects_authoritative_write(engine):
    uid=_source(engine)
    assert _write(engine,uid,idem="first")=="accepted"
    se.lock_recovery(engine)
    assert _write(engine,uid,idem="after_restore")=="refused_recovery_locked"
    with engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM memory_write_receipts WHERE idempotency_key='after_restore'")).scalar_one()==0


def test_source_uid_supports_deployment_python_without_uuid7(engine,monkeypatch):
    import uuid
    monkeypatch.delattr(se.uuid,"uuid7",raising=False)
    event=_source(engine)
    assert uuid.UUID(event.removeprefix("mse-")).version==4
    assert _write(engine,event,idem="python312")=="accepted"
