# -*- coding: utf-8 -*-
"""跨平台拉起链单测（2026-09-19 建；P3-9 收尾：三处重复实现收敛到 scripts/platform_util.py）。

背景：watchdog.py 的服务器自愈路径与整条 server_manager.py 曾硬写
subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS（Windows-only 常量）
以及 venv/Scripts/pythonw.exe，在 POSIX 上分别是 AttributeError 与路径不存在：
- watchdog 侧表现：端口探测正常，但每次拉起都失败 = 只探测不自愈；
- server_manager 侧表现：status 靠 Get-NetTCPConnection 解析 PID，POSIX 上恒为空 -> 误报「服务器未运行」。

2026-09-19 的 1daa660d 只把平台分支补到了三处（watchdog / server_manager / server_controller），
于是同一套判断有了三份副本。现统一收敛到 scripts/platform_util.py（唯一平台分支来源）。

本文件只做纯函数 / monkeypatch 级校验（不真的起进程、不写真实日志、不碰生产库）：
1. 两条平台的拉起键 popen_kwargs() 取值正确（POSIX 必须是 start_new_session）；
2. venv 解释器路径按平台解析正确（两条显式分支 + 缺省按当前平台）；
3. POSIX 的端口 PID 解析（psutil -> lsof -> ss）与命令行 PID 解析（ps，排除自身）分支可用；
4. 三个调用点必须委托 platform_util，且各自不再保留重复实现；
5. 棘轮守卫：Windows-only 常量只允许出现在 scripts/platform_util.py 的 popen_kwargs() 内。
"""
import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"

# 与 watchdog/server_manager 同一种导入方式（先入 scripts/ 再 import），保证模块实例一致。
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import platform_util  # noqa: E402

_WINDOWS_ONLY = ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS")
_SPAWN_CALL_SITES = (
    "scripts/watchdog.py",
    "scripts/server_manager.py",
    "server_controller/server_controller.py",
)


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, str(_SCRIPTS / filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


watchdog = _load("_test_wd_cross_platform", "watchdog.py")
server_manager = _load("_test_sm_cross_platform", "server_manager.py")


# ── 1. 拉起键（脱离父进程）：唯一来源 platform_util.popen_kwargs() ──────────────
def test_popen_kwargs_posix_uses_new_session(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert platform_util.popen_kwargs() == {"start_new_session": True}


def test_popen_kwargs_windows_keeps_creationflags(monkeypatch):
    """Windows 行为必须一字不变：仍是 CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS。"""
    expected = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    if not expected:
        pytest.skip("Windows-only 常量在本机不可用")
    monkeypatch.setattr(os, "name", "nt")
    assert platform_util.popen_kwargs() == {"creationflags": expected}


def test_watchdog_start_server_posix_spawns_detached(tmp_path, monkeypatch):
    """自愈路径在 POSIX 上必须真的把 uvicorn 拉起来（此前 AttributeError 被 except 吞掉 = 不自愈）。"""
    (tmp_path / "data" / "logs").mkdir(parents=True)          # 先建目录：勿在 os.name 被改后动 pathlib
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(watchdog, "SERVER_DIR", str(tmp_path))
    monkeypatch.setattr(watchdog, "is_paused", lambda: False)
    monkeypatch.setattr(watchdog, "port_listening", lambda port: False)
    monkeypatch.setattr(watchdog, "rotate_stdio_log", lambda *a, **k: "")
    monkeypatch.setattr(watchdog, "log", lambda msg: None)
    calls = []
    monkeypatch.setattr(watchdog.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    watchdog.start_server()

    assert len(calls) == 1, calls
    args, kwargs = calls[0]
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs
    assert "uvicorn" in args[0] and "8000" in args[0]


def test_server_manager_start_watchdog_posix_spawns_detached(monkeypatch):
    """server_manager 的拉起路径同样走共享实现：POSIX 不再硬写 Windows-only creationflags。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(server_manager, "log", lambda msg: None)
    calls = []
    monkeypatch.setattr(server_manager.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))

    server_manager.start_watchdog()

    assert len(calls) == 1, calls
    args, kwargs = calls[0]
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs
    assert os.path.basename(args[0][1]) == "watchdog.py"


# ── 2. venv 解释器路径 ───────────────────────────────────────────────────────
def test_venv_python_paths_windows():
    pyw, py = platform_util.venv_python_paths("repo", True)
    assert pyw.endswith(os.path.join(".venv", "Scripts", "pythonw.exe"))
    assert py.endswith(os.path.join(".venv", "Scripts", "python.exe"))


def test_venv_python_paths_posix():
    pyw, py = platform_util.venv_python_paths("repo", False)
    assert pyw == py
    assert pyw.endswith(os.path.join(".venv", "bin", "python"))


def test_venv_python_paths_defaults_to_current_os(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    pyw, py = platform_util.venv_python_paths("repo")
    assert pyw == py
    assert pyw.endswith(os.path.join(".venv", "bin", "python"))


# ── 3. PID 解析：nt（PowerShell/WMI）与 POSIX（psutil -> lsof -> ss / ps） ──────
def test_windows_port_pids_parses_powershell(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    seen = []
    monkeypatch.setattr(platform_util, "_ps", lambda cmd: seen.append(cmd) or "1234\n\n5678\n")
    assert platform_util.port_pids(8000) == [1234, 5678]
    assert "Get-NetTCPConnection" in seen[0]


def test_windows_cmdline_pids_parses_wmi(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    seen = []
    monkeypatch.setattr(platform_util, "_ps", lambda cmd: seen.append(cmd) or "4321")
    assert platform_util.cmdline_pids("uvicorn") == [4321]
    assert "Win32_Process" in seen[0]


def test_posix_port_pids_prefers_psutil(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform_util, "_psutil_port_pids", lambda port: [4321])
    monkeypatch.setattr(platform_util, "_sh", lambda cmd: pytest.fail("psutil 可用时不应回退外部命令"))
    assert platform_util.port_pids(8000) == [4321]


def test_posix_port_pids_parses_lsof(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform_util, "_psutil_port_pids", lambda port: [])
    monkeypatch.setattr(platform_util, "_sh", lambda cmd: "1234\n5678" if cmd[:2] == ["lsof", "-t"] else "")
    assert platform_util.port_pids(8000) == [1234, 5678]


def test_posix_port_pids_falls_back_to_ss(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(platform_util, "_psutil_port_pids", lambda port: [])
    ss_line = 'LISTEN 0 5 0.0.0.0:8000 0.0.0.0:* users:(("python",pid=4321,fd=7))'
    monkeypatch.setattr(platform_util, "_sh", lambda cmd: "" if cmd[:2] == ["lsof", "-t"] else ss_line)
    assert platform_util.port_pids(8000) == [4321]


def test_posix_cmdline_pids_parses_ps(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    out = "\n".join([
        "  101 /usr/bin/python3 scripts/watchdog.py",
        "  102 /usr/bin/python3 -m uvicorn app.main:app",
        "  103 /usr/bin/python3 scripts/backup.py",
    ])
    monkeypatch.setattr(platform_util, "_sh", lambda cmd: out)
    assert platform_util.cmdline_pids("watchdog.py") == [101]
    assert platform_util.cmdline_pids("uvicorn") == [102]
    assert platform_util.cmdline_pids("nothing-matches") == []


def test_posix_cmdline_pids_excludes_self(monkeypatch):
    """排除自身 PID：否则 server_manager 的 stop 会把「正在执行 stop 的自己」也列进待杀名单。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "getpid", lambda: 101)
    out = "\n".join([
        "  101 /usr/bin/python3 scripts/watchdog.py",
        "  102 /usr/bin/python3 scripts/watchdog.py",
    ])
    monkeypatch.setattr(platform_util, "_sh", lambda cmd: out)
    assert platform_util.cmdline_pids("watchdog.py") == [102]


# ── 4. 收敛守卫：三个调用点必须委托 platform_util ─────────────────────────────
@pytest.mark.parametrize("relpath", _SPAWN_CALL_SITES)
def test_spawn_call_sites_delegate_to_platform_util(relpath):
    src = (_REPO / relpath).read_text(encoding="utf-8")
    tree = ast.parse(src)
    local_defs = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_popen_kwargs" not in local_defs, f"{relpath}: 仍保留本地 _popen_kwargs() 重复实现"
    assert "_venv_python_paths" not in local_defs, f"{relpath}: 仍保留本地 _venv_python_paths() 重复实现"
    assert "platform_util.popen_kwargs()" in src, f"{relpath}: 未改走 platform_util.popen_kwargs()"
    assert "platform_util.venv_python_paths(" in src, f"{relpath}: 未改走 platform_util.venv_python_paths()"


def test_server_manager_pid_queries_delegate():
    src = (_SCRIPTS / "server_manager.py").read_text(encoding="utf-8")
    assert "platform_util.port_pids(" in src
    assert "platform_util.cmdline_pids(" in src


# ── 5. 棘轮守卫（防「漏改一处」复发） ───────────────────────────────────────
_RATCHET_FILES = ("scripts/platform_util.py",) + _SPAWN_CALL_SITES


@pytest.mark.parametrize("relpath", _RATCHET_FILES)
def test_windows_only_flags_only_inside_platform_util_popen_kwargs(relpath):
    """Windows-only 常量只允许出现在 scripts/platform_util.py 的 popen_kwargs() 内（AST 行号判定）。

    判定规则（2026-09-19 Codex 复核收窄）：
    - `subprocess.CREATE_NEW_PROCESS_GROUP` 式**裸属性访问** = Windows-only，必须只出现在
      `popen_kwargs()` 内（POSIX 上 AttributeError）；
    - `getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)` 式**字符串常量**属 POSIX 安全写法
      （POSIX 取到 0），**不在棘轮范围内** —— 否则会误伤 NO_WINDOW 这类「隐藏窗口」常量
      （server_manager / server_controller 收敛前就是该写法，必须逐位保留）。
    """
    tree = ast.parse((_REPO / relpath).read_text(encoding="utf-8"))
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "popen_kwargs":
            allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    # getattr(subprocess, "<常量>", 0) 的常量行号 —— POSIX 安全，豁免
    getattr_arg_lines = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"):
            getattr_arg_lines.update(a.lineno for a in node.args)
    offenders = sorted({
        node.lineno
        for node in ast.walk(tree)
        if (isinstance(node, ast.Attribute) and node.attr in _WINDOWS_ONLY)
        or (isinstance(node, ast.Constant) and node.value in _WINDOWS_ONLY
            and node.lineno not in getattr_arg_lines)
    } - allowed)
    assert not offenders, (
        f"{relpath}: Windows-only 常量出现在 {offenders} 行"
        "（只允许出现在 scripts/platform_util.py 的 popen_kwargs() 内）"
    )
