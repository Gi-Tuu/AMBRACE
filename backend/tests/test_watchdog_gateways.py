# -*- coding: utf-8 -*-
"""scripts/watchdog.py 的 G4 通用网关守护逻辑单测（配置驱动，不写死某进程）。

不真的拉起进程：用 importlib 加载 watchdog.py，monkeypatch 其探活/拉起函数验证
- 配置解析（master 开关 / enabled / command 缺失过滤）
- TCP 地址解析（probe dict / int / "host:port" / probe_port）
- monitor_gateways 防抖（不重复拉）
"""
import importlib.util
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_WATCHDOG_PATH = _REPO / "scripts" / "watchdog.py"


def _load_watchdog():
    spec = importlib.util.spec_from_file_location("_test_watchdog_mod", str(_WATCHDOG_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load_watchdog()


def _write_config(tmp_path, cfg):
    p = tmp_path / "server_config.json"
    import json
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_load_gateway_config_master_off(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "CONFIG", _write_config(tmp_path, {"gateway_watchdog_enabled": False}))
    assert watchdog._load_gateway_config() == []


def test_load_gateway_config_filters_enabled_and_command(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "CONFIG", _write_config(tmp_path, {
        "gateway_watchdog_enabled": True,
        "gateways": [
            {"name": "ok", "enabled": True, "command": ["node", "x.js"]},
            {"name": "disabled", "enabled": False, "command": ["node", "y.js"]},
            {"name": "no_cmd", "enabled": True, "command": []},
        ],
    }))
    gws = watchdog._load_gateway_config()
    assert [g["name"] for g in gws] == ["ok"]


def test_load_gateway_config_bad_config_safe(tmp_path, monkeypatch):
    """脏配置/文件缺失绝不抛（返回空 = 不守护），不影响主服务器守护。"""
    monkeypatch.setattr(watchdog, "CONFIG", str(tmp_path / "not_there.json"))
    assert watchdog._load_gateway_config() == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(watchdog, "CONFIG", str(bad))
    assert watchdog._load_gateway_config() == []


def test_gw_tcp_addr_supports_various_probe_forms():
    assert watchdog._gw_tcp_addr({"probe": {"kind": "tcp", "host": "127.0.0.1", "port": 18789}}) == ("127.0.0.1", 18789)
    assert watchdog._gw_tcp_addr({"probe": 18789}) == ("127.0.0.1", 18789)
    assert watchdog._gw_tcp_addr({"probe": "127.0.0.1:18789"}) == ("127.0.0.1", 18789)
    assert watchdog._gw_tcp_addr({"probe_port": 18789}) == ("127.0.0.1", 18789)
    assert watchdog._gw_tcp_addr({"probe": {"kind": "http", "url": "http://x"}}) == ("127.0.0.1", 0)


def test_monitor_gateways_debounce(tmp_path, monkeypatch):
    """防抖：同一周期内探活失败只拉一次（各自 grace 下限 30s）。"""
    monkeypatch.setattr(watchdog, "_gw_last_restart", {})
    calls = []

    def _fake_start(gw):
        calls.append(gw["name"])

    monkeypatch.setattr(watchdog, "_load_gateway_config", lambda: [
        {"name": "OpenClaw", "enabled": True, "command": ["node", "x.js"], "grace_sec": 30, "probe": {"kind": "tcp", "port": 18789}},
    ])
    monkeypatch.setattr(watchdog, "_gw_probe_ok", lambda gw: False)
    monkeypatch.setattr(watchdog, "_start_gateway", _fake_start)
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)

    watchdog.monitor_gateways()
    # 立即再查一次：仍在 grace 窗口内，不应重复拉
    watchdog.monitor_gateways()
    assert calls == ["OpenClaw"], calls


def test_monitor_gateways_skips_when_alive(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "_gw_last_restart", {})
    calls = []

    def _fake_start(gw):
        calls.append(gw["name"])

    monkeypatch.setattr(watchdog, "_load_gateway_config", lambda: [
        {"name": "OpenClaw", "enabled": True, "command": ["node", "x.js"]},
    ])
    monkeypatch.setattr(watchdog, "_gw_probe_ok", lambda gw: True)  # 存活
    monkeypatch.setattr(watchdog, "_start_gateway", _fake_start)
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)

    watchdog.monitor_gateways()
    assert calls == []


def test_start_gateway_respects_paused(monkeypatch):
    """暂停标记：停止服务器后 watchdog 不得反手拉起网关（_start_gateway 内部拦）。"""
    popped = []
    monkeypatch.setattr(watchdog, "is_paused", lambda: True)
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda *a, **k: popped.append((a, k)))
    watchdog._start_gateway({
        "name": "OpenClaw", "command": ["node", "x.js"], "cwd": "C:\\Users\\sheng",
        "probe": {"kind": "tcp", "port": 18789},
    })
    assert popped == []
