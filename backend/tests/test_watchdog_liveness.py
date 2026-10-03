# -*- coding: utf-8 -*-
"""scripts/watchdog.py 的 HB-2 判据升级单测（HTTP /liveness 优先、TCP probe 兜底）。

不真起服务、不真拉进程：importlib 加载 watchdog.py，monkeypatch urllib / port_pids /
subprocess.run / start_server，验证
- probe_liveness 各分支（200 健康 / stalled / 非 200 / 非 JSON / 404 回退 / 异常超时）
- 僵死需「连续 N 次」成立才动手、宽限期内不动手、恢复即清零
- restart_hung_server 尊重 paused.flag、杀完监听者后复用既有 start_server()
"""
import importlib.util
import io
import json
import time
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_WATCHDOG_PATH = _REPO / "scripts" / "watchdog.py"


def _load_watchdog():
    spec = importlib.util.spec_from_file_location("_test_wd_liveness_mod", str(_WATCHDOG_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load_watchdog()
# 模块一加载就记下「真日志路径」：autouse 夹具会把 LOG 挪进 tmp，这个常量给不变式用例用。
_PROD_LOG = watchdog.LOG


@pytest.fixture(autouse=True)
def _logs_stay_out_of_production(tmp_path, monkeypatch):
    """本文件的每个用例都把 watchdog 的日志写进自己的 tmp 目录。

    2026-10-04 实测：以前每跑一次就往 `backend/data/logs/watchdog.log` 追加
    「Paused flag exists, skip hang restart」——而当时**并不存在** paused.flag，
    是用例把 `is_paused` 桩成了 True。运维日志里出现测试编造的因果，比没日志更坏。
    """
    monkeypatch.setattr(watchdog, "LOG", str(tmp_path / "watchdog.log"))


class _Resp(io.BytesIO):
    """urlopen 返回体的最小替身（raw BytesIO 无 context manager 协议，补上）。"""

    def __init__(self, status, payload):
        super().__init__(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        self._status = status

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_urlopen(monkeypatch, status=None, payload=None, exc=None):
    import urllib.request

    def _open(url, timeout=None):
        if exc is not None:
            raise exc
        return _Resp(status, payload)

    monkeypatch.setattr(urllib.request, "urlopen", _open)


# ── 1. probe_liveness 分支 ────────────────────────────────────────────────────

def test_probe_liveness_ok(monkeypatch):
    _fake_urlopen(monkeypatch, 200, {"status": "alive", "stalled": False})
    ok, note = watchdog.probe_liveness()
    assert ok is True and note == "ok"


def test_probe_liveness_stalled_is_unhealthy(monkeypatch):
    _fake_urlopen(monkeypatch, 200, {"status": "alive", "stalled": True})
    ok, note = watchdog.probe_liveness()
    assert ok is False and "stalled=true" in note


def test_probe_liveness_non_200(monkeypatch):
    _fake_urlopen(monkeypatch, 500, {"detail": "boom"})
    ok, note = watchdog.probe_liveness()
    assert ok is False and "HTTP 500" in note


def test_probe_liveness_non_json(monkeypatch):
    _fake_urlopen(monkeypatch, 200, b"<html>proxy interlude</html>")
    ok, note = watchdog.probe_liveness()
    assert ok is False and "JSON" in note


def test_probe_liveness_missing_route_falls_back_to_tcp(monkeypatch):
    """404/405（旧构建尚无该路由）不得判僵死——回退 TCP 判据视为健康，避免误杀正常服务。"""
    for code in (404, 405):
        _fake_urlopen(monkeypatch, code, {"detail": "Not Found"})
        ok, note = watchdog.probe_liveness()
        assert ok is True, (code, note)
        assert "回退 TCP" in note, (code, note)


def test_probe_liveness_timeout_counts_as_hang(monkeypatch):
    """事件循环被阻塞时 TCP 仍握手成功，但 HTTP 会超时 ⇒ 必须判不健康（本次升级的动机）。"""
    _fake_urlopen(monkeypatch, exc=TimeoutError("timed out"))
    ok, note = watchdog.probe_liveness()
    assert ok is False and "TimeoutError" in note


# ── 2. check_app_liveness：连续确认 + 宽限期 ──────────────────────────────────

def _prep(monkeypatch, healthy_seq, restart_calls):
    monkeypatch.setattr(watchdog, "probe_liveness", lambda *a, **k: (healthy_seq.pop(0), "stub"))
    monkeypatch.setattr(watchdog, "log", lambda msg: None)
    monkeypatch.setattr(watchdog, "restart_hung_server", lambda: restart_calls.append(1))
    monkeypatch.setattr(watchdog, "_hang_streak", 0)
    monkeypatch.setattr(watchdog, "_last_restart", 0.0)


def test_hang_needs_consecutive_confirmations(monkeypatch):
    calls = []
    n = watchdog.LIVENESS_HANG_CONFIRMATIONS
    _prep(monkeypatch, [False] * n, calls)

    for _ in range(n - 1):
        watchdog.check_app_liveness()
    assert calls == [], "未达连续次数不得动手"

    watchdog.check_app_liveness()
    assert calls == [1], "达到连续次数应按僵死处理"


def test_hang_streak_reset_by_healthy_probe(monkeypatch):
    calls = []
    _prep(monkeypatch, [False, False, True, False, False], calls)
    for _ in range(5):
        watchdog.check_app_liveness()
    assert calls == [], "中途恢复健康应清零计数，不得累加误杀"
    assert watchdog._hang_streak == 2


def test_hang_restart_respects_grace_period(monkeypatch):
    calls = []
    n = watchdog.LIVENESS_HANG_CONFIRMATIONS
    _prep(monkeypatch, [False] * n, calls)
    monkeypatch.setattr(watchdog, "_last_restart", time.time())  # 刚拉起过，仍在宽限期
    for _ in range(n):
        watchdog.check_app_liveness()
    assert calls == [], "宽限期内（含启动加载期）不得按僵死误杀"


# ── 3. restart_hung_server：杀监听者后复用既有拉起流程 ────────────────────────

def test_restart_hung_server_skips_when_paused(monkeypatch):
    started, killed = [], []
    monkeypatch.setattr(watchdog, "is_paused", lambda: True)
    monkeypatch.setattr(watchdog, "start_server", lambda: started.append(1))
    monkeypatch.setattr(watchdog.platform_util, "port_pids", lambda p: killed.append(p) or [4242])
    watchdog.restart_hung_server()
    assert started == [] and killed == [], "paused.flag 必须优先，不探测不拉起"


def test_restart_hung_server_kills_listener_then_reuses_start_server(monkeypatch):
    started, killed, logs = [], [], []
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)
    monkeypatch.setattr(watchdog, "log", lambda m: logs.append(str(m)))
    monkeypatch.setattr(watchdog, "port_listening", lambda port, timeout=1.0: False)
    monkeypatch.setattr(watchdog.platform_util, "port_pids", lambda p: [4242])
    monkeypatch.setattr(watchdog.os, "name", "nt")  # 固定走 Windows 分支（Linux/macOS 用 os.kill）
    monkeypatch.setattr(watchdog.subprocess, "run", lambda cmd, **kw: killed.append(cmd))
    monkeypatch.setattr(watchdog, "start_server", lambda: started.append(1))

    watchdog.restart_hung_server()

    assert len(killed) == 1 and "4242" in killed[0], killed
    assert started == [1], "处置完必须走既有 start_server() 拉起流程"
    assert watchdog._hang_streak == 0
    assert any("App hang confirmed" in m for m in logs), logs


def test_restart_hung_server_posix_uses_sigterm(monkeypatch):
    """POSIX 分支不得碰 Windows 专有常量/命令（历史教训：AttributeError 被吞＝只探测不自愈）。"""
    started, signals, logs = [], [], []
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)
    monkeypatch.setattr(watchdog, "log", lambda m: logs.append(str(m)))
    monkeypatch.setattr(watchdog, "port_listening", lambda port, timeout=1.0: False)
    monkeypatch.setattr(watchdog.platform_util, "port_pids", lambda p: [4242])
    monkeypatch.setattr(watchdog.os, "name", "posix")
    monkeypatch.setattr(watchdog.os, "kill", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(watchdog, "start_server", lambda: started.append(1))

    watchdog.restart_hung_server()

    assert signals == [watchdog.signal.SIGTERM], signals
    assert started == [1]


def test_restart_hung_server_without_listener_defers(monkeypatch):
    """判据与动作之间端口已没人监听（进程刚退）⇒ 不动作，交下一周期 TCP 路径处理。"""
    started = []
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)
    monkeypatch.setattr(watchdog, "log", lambda m: None)
    monkeypatch.setattr(watchdog.platform_util, "port_pids", lambda p: [])
    monkeypatch.setattr(watchdog, "start_server", lambda: started.append(1))
    watchdog.restart_hung_server()
    assert started == []


def test_logs_never_reach_the_real_watchdog_log(tmp_path):
    """不变式：`watchdog.log()` 只许写进本例 tmp，生产 `watchdog.log` 一个字节都不许涨。

    拿掉上面的 autouse 夹具，这条立刻红。（与 test_watchdog_gateways.py 里同名用例是一对，
    两个文件都曾经往生产日志写假故障。）
    """
    prod = Path(_PROD_LOG)
    before = prod.stat().st_size if prod.is_file() else 0
    watchdog.log("探针：这一行必须落在 tmp，不许进生产日志")
    after = prod.stat().st_size if prod.is_file() else 0
    assert after == before, "测试写进了生产 watchdog.log（autouse 夹具被拿掉了？）"
    assert "探针：这一行必须落在 tmp" in (tmp_path / "watchdog.log").read_text(encoding="utf-8")
