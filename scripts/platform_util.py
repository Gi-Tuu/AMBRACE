# -*- coding: utf-8 -*-
"""跨平台进程工具（P3-9 收尾：收敛三处重复的「跨平台拉起 / 探测」实现）。

2026-09-19 的 1daa660d 修掉了 watchdog / server_manager 在 POSIX 上的断裂，但同一套平台判断
散成了三份（watchdog.py、server_manager.py、server_controller.py 各一份 `_popen_kwargs()`，
外加各自的 venv 解释器路径与 PID 查询）。本模块是它们的唯一来源：

- ``popen_kwargs()``     拉起后台进程的「脱离父进程」键（Windows creationflags / POSIX 新会话）；
- ``venv_python_paths()`` venv 内 pythonw/python 的绝对路径（纯函数，便于双平台单测）；
- ``port_pids()``        监听某端口的 PID（nt: PowerShell；POSIX: psutil → lsof → ss）；
- ``cmdline_pids()``     命令行含关键字的 PID（nt: WMI；POSIX: ps）。

原则：不引入新依赖，psutil 只做「有就用」的可选路径，缺失/报错一律回退到 lsof/ss；
所有外部命令与解析都走宽 except，失败返回空表/空串，绝不向上抛。

棘轮：Windows-only 常量 ``CREATE_NEW_PROCESS_GROUP`` / ``DETACHED_PROCESS`` 只允许出现在本文件
``popen_kwargs()`` 内（``backend/tests/test_cross_platform_spawn.py`` 有 AST 守卫钉死）。
"""
import os
import re
import subprocess

_PS_TIMEOUT = 15  # 与 server_manager 原 _ps 一致
# 隐藏短命子进程窗口（POSIX 上该属性不存在 -> 0）。
# P3-9 复核修正（2026-09-19，Codex）：保留 CREATE_NEW_PROCESS_GROUP 位——收敛前三处 `_ps`
# 都是 `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`，收敛不得顺手改掉「独立进程组」这一位。
_NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
              | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def popen_kwargs() -> dict:
    """后台拉起的「脱离父进程」键。

    Windows：``creationflags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS``（新进程组 + 脱离控制台，
    与 watchdog / server_manager / server_controller 原先各自实现完全一致）；
    POSIX：``start_new_session=True``（脱离父会话，避免被父进程的终端信号带走）。
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def venv_python_paths(backend_dir: str, is_windows=None) -> tuple:
    """venv 内 pythonw / python 的绝对路径（纯函数，便于单测两条分支）。

    Windows：``.venv/Scripts/pythonw.exe``（GUI 子系统、无控制台窗口）+ ``python.exe``；
    POSIX  ：``.venv/bin/python``（无 pythonw，两个入口同路径）。

    ``is_windows`` 缺省按当前平台（``os.name == "nt"``）判断。
    """
    win = (os.name == "nt") if is_windows is None else bool(is_windows)
    base = os.path.join(backend_dir, ".venv", "Scripts" if win else "bin")
    if win:
        return os.path.join(base, "pythonw.exe"), os.path.join(base, "python.exe")
    py = os.path.join(base, "python")
    return py, py


def _ps(cmd: str) -> str:
    """运行 PowerShell 并返回 stdout（隐藏窗口、15s 超时）；任何异常返回空串。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=_PS_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _sh(cmd: list) -> str:
    """运行 POSIX 命令并返回 stdout（15s 超时）；Windows 侧不使用（用 _ps）。任何异常返回空串。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_PS_TIMEOUT)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _digit_lines(out: str) -> list:
    """逐行取纯数字（PowerShell/WMI 的 PID 输出），去重排序。"""
    pids = []
    for line in (out or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def _psutil_port_pids(port: int) -> list:
    """POSIX 首选：psutil（可选依赖，只做「有就用」）。未安装或查询失败（macOS 权限等）返回空表，
    由 :func:`port_pids` 回退到 lsof / ss。"""
    try:
        import psutil
    except Exception:
        return []
    try:
        listen = getattr(psutil, "CONN_LISTEN", "LISTEN")  # psutil 7 起 CONN_* 常量已弃用
        pids = set()
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == listen and conn.pid:
                pids.add(int(conn.pid))
        return sorted(pids)
    except Exception:
        return []


def port_pids(port: int) -> list:
    """返回监听指定端口的所有 PID（权威实例判定）。

    nt   ：``Get-NetTCPConnection -State Listen``（沿用 server_manager 的 _ps 思路，但不依赖别的脚本）；
    POSIX：psutil（有就用）→ ``lsof -t -n -i:PORT -sTCP:LISTEN`` → ``ss -H -ltnp "sport = :PORT"``
    （``pid=(\\d+)``），三级回退，与 server_controller 的 POSIX 端口判定同源。
    """
    if os.name == "nt":
        out = _ps(
            "Get-NetTCPConnection -State Listen -LocalPort {0} -ErrorAction SilentlyContinue "
            "| Select-Object -ExpandProperty OwningProcess".format(port)
        )
        return _digit_lines(out)
    pids = _psutil_port_pids(port)
    if pids:
        return pids
    out = _sh(["lsof", "-t", "-n", "-i:%d" % port, "-sTCP:LISTEN"])
    if out:
        return _digit_lines(out)
    return sorted({int(m) for m in re.findall(r"pid=(\d+)", _sh(["ss", "-H", "-ltnp", "sport = :%d" % port]))})


def cmdline_pids(keyword: str) -> list:
    """按命令行关键字匹配进程 PID。

    nt   ：WMI ``Win32_Process`` 的 CommandLine（沿用 server_manager 原实现）；
    POSIX：``ps -eo pid=,args=`` 解析并排除自身 PID（避免把调用者自己杀掉）。
    """
    if os.name != "nt":
        me = os.getpid()
        pids = []
        for line in _sh(["ps", "-eo", "pid=,args="]).splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid == me or keyword not in parts[1]:
                continue
            pids.append(pid)
        return sorted(set(pids))
    esc = keyword.replace("'", "''")
    out = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
        "| Where-Object {{ $_.CommandLine -like '*{0}*' }} "
        "| Select-Object -ExpandProperty ProcessId".format(esc)
    )
    return _digit_lines(out)
