"""Synthetic permanent ledger failures: accounting stays consistent without claiming durability."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.memory import rating_quota as rq

DAY = datetime(2026, 10, 24, 10, tzinfo=timezone(timedelta(hours=8)))


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    monkeypatch.setattr(rq, "_STATE_FILE", path)
    monkeypatch.setattr(rq, "_PENDING", {}, raising=False)
    monkeypatch.setattr(rq, "sleep", lambda _: None)
    rq.add(13, 3, DAY)
    real = rq.os.replace
    def deny(src, dst):
        if dst == path:
            exc = PermissionError("synthetic permanent refusal")
            exc.winerror = 5
            raise exc
        return real(src, dst)
    monkeypatch.setattr(rq.os, "replace", deny)
    return path, real


def test_failed_save_preserves_accounting_and_exposes_not_durable(ledger, caplog):
    path, _ = ledger
    assert rq.add(13, 2, DAY) == 5
    assert rq.used_today(13, DAY) == 5
    assert rq.add(13, 4, DAY) == 9
    assert rq.used_today(13, DAY) == 9
    assert rq.diff_observed({13: 9, 14: 2}, DAY) == {14: (0, 2)}
    assert json.loads(path.read_text())["counts"] == {"13": 3}
    assert not rq.is_persisted(DAY)
    assert "not durable" in caplog.text
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_recovery_flushes_all_characters_once_without_new_charge(ledger, monkeypatch):
    path, real = ledger
    assert rq.add(13, 2, DAY) == 5
    assert rq.add(14, 4, DAY) == 4
    assert not rq.flush_pending(DAY)
    monkeypatch.setattr(rq.os, "replace", real)
    assert rq.flush_pending(DAY)
    assert rq.is_persisted(DAY)
    assert json.loads(path.read_text())["counts"] == {"13": 5, "14": 4}
    assert rq.add(13, 1, DAY) == 6
    assert rq.flush_pending(DAY)
    assert rq.used_today(14, DAY) == 4


def test_pending_day_rolls_at_beijing_midnight_not_utc_midnight(ledger):
    assert rq.add(13, 2, DAY) == 5
    before = datetime(2026, 10, 24, 15, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=1)
    assert rq.used_today(13, before) == 5
    assert rq.used_today(13, after) == 0
    assert rq.add(13, 1, after) == 1
    assert rq.used_today(13, after) == 1


def test_another_ledger_path_does_not_inherit_pending_counts(ledger, monkeypatch, tmp_path):
    path, _ = ledger
    rq.add(13, 2, DAY)
    monkeypatch.setattr(rq, "_STATE_FILE", tmp_path / "other.json")
    assert rq.used_today(13, DAY) == 0
    assert rq.add(13, 1, DAY) == 1
    monkeypatch.setattr(rq, "_STATE_FILE", path)
    assert rq.used_today(13, DAY) == 5


def test_same_process_concurrent_failure_does_not_lose_charges(ledger, monkeypatch):
    with ThreadPoolExecutor(max_workers=5) as pool:
        counts = list(pool.map(lambda _: rq.add(13, 1, DAY), range(12)))
    assert sorted(counts) == list(range(4, 16))
    assert rq.used_today(13, DAY) == 15
    monkeypatch.setattr(rq, "_today_key", lambda now=None: "2026-10-24")
    from app.memory.ai_rating import _daily_rated
    assert asyncio.run(_daily_rated(None, 13)) == 15


def test_failed_write_survives_missing_or_corrupt_old_file_in_process(ledger):
    path, _ = ledger
    rq.add(13, 2, DAY)
    path.write_text("corrupt")
    assert rq.used_today(13, DAY) == 5
    assert rq.add(13, 1, DAY) == 6
    path.unlink()
    assert rq.used_today(13, DAY) == 6


# Reuse only temporary DB/model/trace fixtures; no live model or production data.
from test_ai_rating_trace import rating_db as rating_db, llm as llm, events as events


def test_actual_rating_batch_keeps_committed_charge_and_recovers_before_quota_skip(
    rating_db, llm, events, monkeypatch,
):
    from test_ai_rating_trace import _seed, _mem, _run, _char_rows, _mem_state
    from app.memory import ai_rating as ar
    monkeypatch.setattr(rq, "_PENDING", {})
    _seed(rating_db, _mem(101, "合成设备需要定期检验"))
    rq.add(13, ar.AI_RATING_MAX_PER_CHAR - 1)
    real = rq.os.replace
    path = rq._STATE_FILE
    def deny(src, dst):
        if dst == path:
            raise PermissionError("synthetic persistent refusal")
        return real(src, dst)
    monkeypatch.setattr(rq.os, "replace", deny)
    assert _run() == 1
    assert _mem_state(rating_db, 101)[3] is True
    assert _char_rows(events)[0]["quota_persisted"] is False
    assert rq.used_today(13) == ar.AI_RATING_MAX_PER_CHAR
    _seed(rating_db, _mem(102, "合成设备还需要登记编号"))
    events.clear()
    calls = len(llm.prompts)
    assert _run() == 0
    assert len(llm.prompts) == calls
    assert _char_rows(events)[0]["outcome"] == "quota_full"
    assert _char_rows(events)[0]["quota_persisted"] is False
    assert _mem_state(rating_db, 102)[3] is False
    monkeypatch.setattr(rq.os, "replace", real)
    assert _run() == 0  # Batch entry retries disk even though quota still blocks models.
    assert rq.is_persisted()
    assert json.loads(path.read_text())["counts"]["13"] == ar.AI_RATING_MAX_PER_CHAR
    assert len(llm.prompts) == calls
    assert _mem_state(rating_db, 102)[3] is False


def test_temporary_file_write_failure_also_preserves_consumption(ledger, monkeypatch):
    path, _ = ledger
    real = type(path).write_text
    def fail_tmp(self, *args, **kwargs):
        if self == path.with_name(path.name + ".tmp"):
            raise OSError("synthetic full volume")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(type(path), "write_text", fail_tmp)
    assert rq.add(13, 2, DAY) == 5
    assert rq.used_today(13, DAY) == 5
    assert not rq.is_persisted(DAY)
    assert not rq.flush_pending(DAY)


def test_no_pending_batch_flush_is_readonly(ledger, monkeypatch):
    path, _ = ledger
    before = path.read_bytes()
    def unexpected(data):
        raise AssertionError("no pending charge must not write a file")
    monkeypatch.setattr(rq, "_save", unexpected)
    assert rq.is_persisted(DAY)
    assert rq.flush_pending(DAY)
    assert path.read_bytes() == before
