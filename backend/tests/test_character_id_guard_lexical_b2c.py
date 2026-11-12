import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from app.db.character_id_guard import ensure_client_safe_id_guard, is_capacity_error


def test_noop_triggers_are_repaired(tmp_path):
    engine=create_engine('sqlite:///'+(tmp_path/'noop.db').as_posix())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)')
            conn.exec_driver_sql('INSERT INTO ai_characters(id) VALUES(9007199254740991)')
            for name,event in [('ai_characters_client_safe_insert_after','INSERT'),('ai_characters_client_safe_update_after','UPDATE OF id')]:
                conn.exec_driver_sql(f'CREATE TRIGGER {name} AFTER {event} ON ai_characters WHEN NEW.id > 9007199254740991 BEGIN SELECT 1; END')
            info=ensure_client_safe_id_guard(conn)
            assert info['present'] is True
            import pytest
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql("INSERT INTO ai_characters(name) VALUES('automatic')")
    finally:
        engine.dispose()

def test_sql_parameter_cannot_contaminate_error_classifier():
    exc=IntegrityError('INSERT INTO ai_characters(name,user_id) VALUES (?,?)',('character_id_capacity_exhausted',999),sqlite3.IntegrityError('FOREIGN KEY constraint failed'))
    result=is_capacity_error(exc)
    assert result is False

def test_ineffective_check_gets_real_triggers(tmp_path):
    engine=create_engine('sqlite:///'+(tmp_path/'weak_check.db').as_posix())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, CONSTRAINT ck_ai_characters_client_safe_id CHECK (id <= 9007199254740991 OR 1))')
            conn.exec_driver_sql('INSERT INTO ai_characters(id) VALUES(9007199254740991)')
            info=ensure_client_safe_id_guard(conn)
            assert info['mode']=='triggers'
            import pytest
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql('INSERT INTO ai_characters DEFAULT VALUES')
    finally:
        engine.dispose()

def test_default_string_is_not_a_check(tmp_path):
    engine=create_engine('sqlite:///'+(tmp_path/'text_check.db').as_posix())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT DEFAULT 'constraint ck_ai_characters_client_safe_id check (id <= 9007199254740991)')")
            conn.exec_driver_sql('INSERT INTO ai_characters(id) VALUES(9007199254740991)')
            info=ensure_client_safe_id_guard(conn)
            assert info['mode']=='triggers'
            import pytest
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql('INSERT INTO ai_characters DEFAULT VALUES')
    finally:
        engine.dispose()

def test_quoted_raise_text_cannot_replace_actual_raise(tmp_path):
    from app.db.character_id_guard import _trigger_expected, verify_client_safe_id_guard
    import pytest
    engine=create_engine('sqlite:///'+(tmp_path/'quoted_raise.db').as_posix())
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE ai_characters(id INTEGER PRIMARY KEY AUTOINCREMENT)')
            conn.exec_driver_sql('INSERT INTO ai_characters(id) VALUES(9007199254740991)')
            for name,event in [('ai_characters_client_safe_insert_after','INSERT'),('ai_characters_client_safe_update_after','UPDATE OF id')]:
                sql=_trigger_expected(name,event).replace("SELECT RAISE(ABORT, 'character_id_capacity_exhausted')", 'SELECT "RAISE(ABORT, \'character_id_capacity_exhausted\')"')
                conn.exec_driver_sql(sql)
            assert verify_client_safe_id_guard(conn)['present'] is False
            assert ensure_client_safe_id_guard(conn)['present'] is True
            with pytest.raises(IntegrityError):
                conn.exec_driver_sql('INSERT INTO ai_characters DEFAULT VALUES')
    finally:
        engine.dispose()
