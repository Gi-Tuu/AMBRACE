# -*- coding: utf-8 -*-
"""scripts/log_rotate.py「stdio 日志启动前轮转」单测（C1，2026-09-17）。

纯函数、零 DB、零网络，全部在 pytest tmp_path 内操作，**绝不触碰 backend/data/logs 真实日志**。
scripts/ 不是 Python 包，按既有测试惯例用 importlib 从磁盘按文件路径加载。
覆盖：未超阈值不动 / 超阈值滚动为 .1 / 反复触发只保留 keep 份且内容不串 /
目标不可写或被占用时静默返回空串且不抛异常 / 返回值含 basename。
"""
import importlib.util
import os
import stat
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ROTATE_PATH = _REPO / "scripts" / "log_rotate.py"
_MB = 1024 * 1024


def _load_log_rotate():
    spec = importlib.util.spec_from_file_location("_test_log_rotate_mod", str(_ROTATE_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


log_rotate = _load_log_rotate()


def test_missing_file_returns_empty(tmp_path):
    """文件不存在：返回空串，不抛异常"""
    assert log_rotate.rotate_stdio_log(str(tmp_path / "absent.log")) == ""


def test_below_threshold_untouched(tmp_path):
    """未超阈值：返回空串，文件原样不动，也不产生 .1"""
    p = tmp_path / "server_stderr.log"
    p.write_bytes(b"x" * (_MB - 1))
    assert log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2) == ""
    assert p.stat().st_size == _MB - 1
    assert not (tmp_path / "server_stderr.log.1").exists()


def test_over_threshold_rotates_to_dot1(tmp_path):
    """超阈值：原路径消失、内容完整搬到 .1，返回值含 basename 便于辨认"""
    p = tmp_path / "server_stderr.log"
    payload = b"a" * (_MB + 10)
    p.write_bytes(payload)
    msg = log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2)
    assert "server_stderr.log" in msg
    assert not p.exists()
    assert (tmp_path / "server_stderr.log.1").read_bytes() == payload


def test_repeat_rotation_keeps_keep_copies_and_content_not_mixed(tmp_path):
    """反复触发：.1 -> .2 依次滚动，只保留 keep 份，最老被删，且内容不串档"""
    p = tmp_path / "gateway_gw.log"
    for gen in range(1, 5):  # 触发 4 次，keep=2
        p.write_bytes(f"gen{gen}-".encode() + b"x" * _MB)
        assert log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2)

    names = sorted(x.name for x in tmp_path.glob("gateway_gw.log.*"))
    assert names == ["gateway_gw.log.1", "gateway_gw.log.2"]  # 只保留 keep 份，.3 不存在
    assert (tmp_path / "gateway_gw.log.1").read_bytes().startswith(b"gen4-")  # 最新
    assert (tmp_path / "gateway_gw.log.2").read_bytes().startswith(b"gen3-")  # 次新，未被串档


def test_replace_denied_returns_empty_without_raising(tmp_path, monkeypatch):
    """被占用/不可写（os.replace 抛 PermissionError）：返回空串、不抛异常，文件保持原样"""
    p = tmp_path / "server_stderr.log"
    payload = b"b" * (_MB + 10)
    p.write_bytes(payload)

    def _deny(*_args, **_kwargs):
        raise PermissionError(13, "文件被其他进程占用")

    monkeypatch.setattr(log_rotate.os, "replace", _deny)
    assert log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2) == ""
    assert p.read_bytes() == payload


def test_remove_oldest_denied_returns_empty_without_raising(tmp_path, monkeypatch):
    """归档满员时删除最老一份被拒（os.remove 抛 OSError）：同样静默返回空串"""
    p = tmp_path / "server_stderr.log"
    p.write_bytes(b"c" * (_MB + 10))
    (tmp_path / "server_stderr.log.2").write_bytes(b"old")

    def _deny(*_args, **_kwargs):
        raise PermissionError(13, "只读文件无法删除")

    monkeypatch.setattr(log_rotate.os, "remove", _deny)
    assert log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2) == ""
    assert p.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows 下独占句柄/只读属性才会真正阻止改名或删除")
def test_windows_occupied_and_readonly_target_returns_empty(tmp_path):
    """真机场景（Windows）：① 目标被独占打开 → 改名失败；② 最老备份只读 → 删除失败。
    两种情况都必须静默返回空串且不抛异常，保证启动流程不受影响。"""
    # ① 独占句柄（Python open 不含 FILE_SHARE_DELETE）
    p = tmp_path / "server_stderr.log"
    p.write_bytes(b"x" * (_MB + 1))
    with open(p, "rb"):
        assert log_rotate.rotate_stdio_log(str(p), max_mb=1, keep=2) == ""
    assert p.exists()

    # ② 归档满员且最老 .2 只读
    q = tmp_path / "server_stderr2.log"
    oldest = tmp_path / "server_stderr2.log.2"
    oldest.write_bytes(b"old")
    q.write_bytes(b"y" * (_MB + 1))
    os.chmod(str(oldest), stat.S_IREAD)
    try:
        assert log_rotate.rotate_stdio_log(str(q), max_mb=1, keep=2) == ""
        assert q.exists()
    finally:
        os.chmod(str(oldest), stat.S_IWRITE)
