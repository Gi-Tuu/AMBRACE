# -*- coding: utf-8 -*-
"""scripts/watchdog.py 的 G4 通用网关守护逻辑单测（配置驱动，不写死某进程）。

不真的拉起进程：用 importlib 加载 watchdog.py，monkeypatch 其探活/拉起函数验证
- 配置解析（master 开关 / enabled / command 缺失过滤）
- TCP 地址解析（probe dict / int / "host:port" / probe_port）
- monitor_gateways 防抖（不重复拉）
"""
import importlib.util
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
_WATCHDOG_PATH = _REPO / "scripts" / "watchdog.py"


def _load_watchdog():
    spec = importlib.util.spec_from_file_location("_test_watchdog_mod", str(_WATCHDOG_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load_watchdog()
# 模块一加载就把「真日志路径」记下来：下面那条 autouse 夹具会把 LOG 改到临时目录，
# 这个常量则是「测试一行都不许写进去」的那条生产路径，供不变式用例引用。
_PROD_LOG = watchdog.LOG


@pytest.fixture(autouse=True)
def _logs_stay_out_of_production(tmp_path, monkeypatch):
    """本文件的每个用例都把 watchdog 的日志写进自己的 tmp 目录。

    2026-10-04 实测：以前每跑一次这个文件，就往 `backend/data/logs/watchdog.log` 追加
    3 行**假故障**（「Gateway [OpenClaw] down detected, restarting...」等），
    而真端口 18789 一直在监听 ⇒ 排障时会被这些行骗。日志是运维现场，不是测试的草稿纸。
    """
    monkeypatch.setattr(watchdog, "LOG", str(tmp_path / "watchdog.log"))


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
        "name": "OpenClaw", "command": ["node", "x.js"], "cwd": "C:\\Users\\example",
        "probe": {"kind": "tcp", "port": 18789},
    })
    assert popped == []


def test_logs_never_reach_the_real_watchdog_log(tmp_path):
    """不变式：调用 `watchdog.log()` 只许写进本例 tmp，生产 `watchdog.log` 一个字节都不许涨。

    把上面那条 autouse 夹具拿掉，这条用例立刻红——它就是为这件事存在的
    （2026-10-04 之前没有这条，本文件每跑一次就往生产日志追加 3 行假故障）。
    """
    prod = Path(_PROD_LOG)
    before = prod.stat().st_size if prod.is_file() else 0
    watchdog.log("探针：这一行必须落在 tmp，不许进生产日志")
    after = prod.stat().st_size if prod.is_file() else 0
    assert after == before, "测试写进了生产 watchdog.log（autouse 夹具被拿掉了？）"
    assert "探针：这一行必须落在 tmp" in (tmp_path / "watchdog.log").read_text(encoding="utf-8")
