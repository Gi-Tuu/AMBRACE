"""配额账本原子替换的 Windows 暂时占用：临时文件故障注入，不接生产。"""
import json

import pytest

from app.memory import rating_quota as rq


@pytest.fixture
def quota_path(tmp_path, monkeypatch):
    path = tmp_path / "quota.json"
    monkeypatch.setattr(rq, "_STATE_FILE", path)
    rq.add(13, 7)
    return path


def _denied(code):
    error = PermissionError("simulated Windows replace denial")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_replace_denial_preserves_each_increment(quota_path, monkeypatch, code):
    real_replace = rq.os.replace
    attempts = []
    sleeps = []

    def replace(src, dst):
        if dst == quota_path:
            attempts.append(1)
            if len(attempts) < 3:
                raise _denied(code)
        return real_replace(src, dst)

    monkeypatch.setattr(rq.os, "replace", replace)
    # Baseline has no sleep alias; raising=False permits the original regression to run.
    monkeypatch.setattr(rq, "sleep", sleeps.append, raising=False)
    assert rq.add(13, 1) == 8
    assert rq.used_today(13) == 8
    assert len(attempts) == 3
    assert sleeps == [0.05, 0.1]
    assert not quota_path.with_name(quota_path.name + ".tmp").exists()


@pytest.mark.parametrize("code,expected_attempts", [(5, 3), (13, 1), (None, 1)])
def test_permanent_or_other_denial_is_bounded_and_keeps_old_file(
    quota_path, monkeypatch, caplog, code, expected_attempts,
):
    attempts = []
    sleeps = []
    real_replace = rq.os.replace

    def replace(src, dst):
        if dst == quota_path:
            attempts.append(1)
            raise _denied(code)
        return real_replace(src, dst)

    monkeypatch.setattr(rq.os, "replace", replace)
    monkeypatch.setattr(rq, "sleep", sleeps.append, raising=False)
    old = quota_path.read_bytes()
    data = json.loads(old)
    data["counts"]["13"] = 8
    rq._save(data)
    assert quota_path.read_bytes() == old
    assert len(attempts) == expected_attempts
    assert len(sleeps) == expected_attempts - 1
    assert "Write rating quota failed" in caplog.text


def test_healthy_replace_does_not_sleep(quota_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr(rq, "sleep", sleeps.append)
    assert rq.add(13, 1) == 8
    assert rq.used_today(13) == 8
    assert sleeps == []
