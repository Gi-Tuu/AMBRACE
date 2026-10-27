"""周期台账原子替换与退避恢复：仅临时文件，不接真实状态文件。"""
import ctypes
import json
import os
from datetime import datetime, timedelta

import pytest

from app.scheduling import periodic_state as pst


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    path = tmp_path / "periodic_state.json"
    monkeypatch.setattr(pst, "_STATE_FILE", path)
    for name in ("_LOCAL_STAMPS", "_LOCAL_RETRY_AT", "_LOCAL_COUNTERS", "_LOCAL_MARKS"):
        monkeypatch.setattr(pst, name, {})
    monkeypatch.setattr(pst, "_LOCAL_STREAKS", {})
    return path


def _denied():
    error = PermissionError("controlled file replacement denial")
    error.winerror = 5
    return error


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_windows_handle_without_delete_share_recovers(isolated_state, monkeypatch):
    """实际Win32读句柄不共享删除权限；第一次替换被拒，释放后应重试成功。"""
    from ctypes import wintypes
    isolated_state.write_text(json.dumps({"other": {"count": 7}}), encoding="utf-8")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ; FILE_SHARE_READ | FILE_SHARE_WRITE，故意无FILE_SHARE_DELETE。
    handle = create(str(isolated_state), 0x80000000, 3, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    released = False
    pauses = []
    def release_on_retry(delay):
        nonlocal released
        pauses.append(delay)
        assert close(handle)
        released = True
    monkeypatch.setattr(pst.time, "sleep", release_on_retry)
    try:
        now = datetime(2026, 10, 23, 0, 0)
        pst.mark_failed("job", timedelta(hours=6), when=now)
    finally:
        if not released:
            close(handle)
    assert len(pauses) == 1, "真实文件共享冲突必须触发重试，不能只绕过写盘"
    actual = json.loads(isolated_state.read_text(encoding="utf-8"))
    assert actual["job"]["fail_streak"] == 1
    assert actual["other"] == {"count": 7}
    assert not isolated_state.with_suffix(".json.tmp").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows replacement retry")
def test_persistent_denial_keeps_backoff_then_recovers(isolated_state, monkeypatch, caplog):
    interval = timedelta(hours=6)
    now = datetime(2026, 10, 23, 0, 0)
    pst.mark_done("job", when=now-interval)
    before = isolated_state.read_bytes()
    original = pst.os.replace
    calls = []
    pauses = []
    def deny(*args):
        calls.append(args)
        raise _denied()
    monkeypatch.setattr(pst.os, "replace", deny)
    monkeypatch.setattr(pst.time, "sleep", pauses.append)
    for streak, minutes in ((1, 15), (2, 30), (3, 60), (4, 360)):
        pst.mark_failed("job", interval, when=now)
        assert pst.fail_streak("job") == streak
        assert pst.last_done("job") == now - interval + timedelta(minutes=minutes)
        assert not pst.is_due("job", interval, now=now+timedelta(minutes=minutes)-timedelta(seconds=1))
        assert pst.is_due("job", interval, now=now+timedelta(minutes=minutes))
    assert isolated_state.read_bytes() == before
    assert len(calls) == 16 and len(pauses) == 12
    assert sum(pauses) == pytest.approx(0.4)
    assert sum("Write periodic state failed" in r.message for r in caplog.records) == 4
    monkeypatch.setattr(pst.os, "replace", original)
    pst.mark_done("job", when=now+interval)
    assert pst.fail_streak("job") == 0
    assert pst.last_done("job") == now+interval
    assert "job" not in pst._LOCAL_STREAKS
    assert "job" not in pst._LOCAL_STAMPS
    assert json.loads(isolated_state.read_text(encoding="utf-8"))["job"]["fail_streak"] == 0


def test_non_windows_access_error_is_not_retried(isolated_state, monkeypatch):
    calls = []
    pauses = []
    def deny(*args):
        calls.append(args)
        raise PermissionError("not a Windows sharing failure")
    monkeypatch.setattr(pst.os, "replace", deny)
    monkeypatch.setattr(pst.time, "sleep", pauses.append)
    pst.mark_failed("job", timedelta(hours=6), when=datetime(2026,10,23))
    assert len(calls) == 1 and pauses == []
    assert pst.fail_streak("job") == 1


def test_daily_failure_preserves_gate_and_success_clears_it(isolated_state, monkeypatch):
    now = datetime(2026, 10, 23, 15, 0)
    monkeypatch.setattr(pst, "app_tz_offset_hours", lambda: 8)
    original = pst.os.replace
    def deny(*args):
        raise PermissionError("persistent disk denial")
    monkeypatch.setattr(pst.os, "replace", deny)
    pst.mark_failed_daily("daily", when=now)
    assert pst.last_done("daily") is None
    assert pst.fail_streak("daily") == 1
    assert pst.next_retry_at("daily") == now+timedelta(minutes=15)
    assert not pst.is_daily_due("daily", 23, now=now+timedelta(minutes=14))
    pst.mark_failed_daily("daily", when=now+timedelta(minutes=15))
    assert pst.fail_streak("daily") == 2
    assert pst.next_retry_at("daily") == now+timedelta(minutes=45)
    monkeypatch.setattr(pst.os, "replace", original)
    pst.mark_done("daily", when=now+timedelta(hours=1))
    assert pst.fail_streak("daily") == 0
    assert pst.next_retry_at("daily") is None
    assert not pst.is_daily_due("daily", 23, now=now+timedelta(hours=2))
