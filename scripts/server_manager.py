# -*- coding: utf-8 -*-
r"""
拥爱（AMBRACE）服务器统一管理脚本（诊断 + 修复 + 排查手册）
========================================================

【用法】（在项目根目录下执行）
  backend\.venv\Scripts\python.exe scripts\server_manager.py status    体检
  backend\.venv\Scripts\python.exe scripts\server_manager.py repair   一键修复（杀净 -> 启动唯一实例）
  backend\.venv\Scripts\python.exe scripts\server_manager.py start    启动
  backend\.venv\Scripts\python.exe scripts\server_manager.py stop     停止
  backend\.venv\Scripts\python.exe scripts\server_manager.py restart  重启

【常见问题排查表】
1. 手机连不上服务器（连接失败/超时）
   ① 运行 status，看「8000 监听」是否为空；
   ② 为空 -> 运行 repair（自动启动）；
   ③ 有监听但仍连不上 -> 检查防火墙（运行 scripts\open_firewall.bat）。

2. 出现「双拉起 / 双实例」（两个 uvicorn 或两个 watchdog）
   先澄清：backend\.venv\Scripts\pythonw.exe 是 venv 重定向 shim，每次启动会出现
   两个同名 pythonw 进程（shim + worker），这是正常假象，不是双实例。
   真双实例判定：status 输出的「8000 监听 PID」多于 1 个，
   或 watchdog.log 中短时间内出现多次 "Watchdog started"。
   处理：运行 repair。

3. 服务器活着但卡顿、风扇狂转
   多为记忆去重/补采全量扫描（见 docs/token-budget.md）或 LLM 高峰排队；
   运行 status 查看状态，必要时调整配置，不要反复重启。

4. 改了代码不生效
   uvicorn 未开 --reload，需重启：运行 restart。

5. 启动后立刻退出
   查看 backend\data\logs\server_stderr.log 尾部；常见原因：
   paused.flag 存在（先 repair）或 8766 锁被占（已有实例）。

【架构说明】
  启动链：server_manager -> pythonw uvicorn（监听 8000）+ pythonw watchdog.py（守护）
  锁：
    uvicorn  锁 = 127.0.0.1:8766（main.py 内绑定，重复实例自动退出）
    watchdog 锁 = 127.0.0.1:8765 + data\locks\watchdog.pid
  权威实例判定：以「监听 8000 的 PID」为准，不数进程数（避免 shim 假象）。

【硬性约束】
  图片二进制绝不传入 deepseek（只传 OCR/VLM 文字描述）。
  数据库时间 UTC naive，北京时间 = UTC+8。
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
BACKEND_DIR = os.path.join(BASE_DIR, "backend")
PYTHONW = os.path.join(BACKEND_DIR, ".venv", "Scripts", "pythonw.exe")
PYTHON = os.path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
WATCHDOG_PY = os.path.join(BASE_DIR, "scripts", "watchdog.py")
LOGS_DIR = os.path.join(BACKEND_DIR, "data", "logs")
STDERR_LOG = os.path.join(LOGS_DIR, "server_stderr.log")
STDOUT_LOG = os.path.join(LOGS_DIR, "server_stdout.log")
WATCHDOG_LOG = os.path.join(LOGS_DIR, "watchdog.log")
PAUSE_FLAG = os.path.join(BACKEND_DIR, "data", "paused.flag")
LOCKS_DIR = os.path.join(BACKEND_DIR, "data", "locks")
PORT = 8000
LOCK_PORT_UVICORN = 8766
LOCK_PORT_WATCHDOG = 8765
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    print(f"[{_now()}] {msg}")


def _ps(cmd: str) -> str:
    """运行 PowerShell 并返回 stdout（隐藏窗口，15s 超时）"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=15,
            creationflags=NO_WINDOW,
        )
        return (r.stdout or "").strip()
    except Exception:
        return ""


def get_port_pids(port: int) -> list:
    """返回监听指定端口的所有 PID（权威实例判定）"""
    out = _ps(
        "Get-NetTCPConnection -State Listen -LocalPort {0} -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty OwningProcess".format(port)
    )
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def get_cmdline_pids(keyword: str) -> list:
    """按命令行关键字匹配 pythonw/python 进程 PID"""
    esc = keyword.replace("'", "''")
    out = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
        "| Where-Object {{ $_.CommandLine -like '*{0}*' }} "
        "| Select-Object -ExpandProperty ProcessId".format(esc)
    )
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def kill_pids(pids) -> None:
    for pid in pids:
        _ps("Stop-Process -Id {0} -Force -ErrorAction SilentlyContinue".format(pid))


def http_ok(timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def read_tail(path: str, n: int = 15) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16000))
            data = f.read().decode("utf-8", errors="replace")
        lines = [l for l in data.splitlines() if l.strip()]
        return "\n".join(lines[-n:])
    except Exception:
        return "(无日志)"


def start_uvicorn() -> None:
    """启动唯一 uvicorn（pythonw 静默）"""
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(STDOUT_LOG, "a", encoding="utf-8") as fout:
        with open(STDERR_LOG, "a", encoding="utf-8") as ferr:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(PORT)],
                cwd=BACKEND_DIR,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=fout, stderr=ferr,
            )
    log("uvicorn 启动命令已发出（加载模型约需 30-60 秒）")


def start_watchdog() -> None:
    subprocess.Popen(
        [PYTHONW, WATCHDOG_PY],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
    )
    log("watchdog 启动命令已发出")


def wait_port(timeout: int = 90) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if http_ok():
            return True
        time.sleep(1.5)
    return False


def stop_all() -> None:
    """停止所有相关进程：先杀 watchdog（防拉起），再杀 uvicorn"""
    log("停止 watchdog（8765 锁 + watchdog.py 进程）...")
    kill_pids(get_port_pids(LOCK_PORT_WATCHDOG))
    kill_pids(get_cmdline_pids("watchdog.py"))
    log("停止 uvicorn（8000 监听 + 8766 锁）...")
    kill_pids(get_port_pids(PORT))
    kill_pids(get_port_pids(LOCK_PORT_UVICORN))
    # 写暂停标记，与桌面控制台兼容（防残留 watchdog 拉起）
    try:
        os.makedirs(os.path.dirname(PAUSE_FLAG), exist_ok=True)
        with open(PAUSE_FLAG, "w", encoding="utf-8") as f:
            f.write("stopped by server_manager " + _now())
    except Exception:
        pass
    time.sleep(1.5)


def clear_pause() -> None:
    try:
        if os.path.exists(PAUSE_FLAG):
            os.remove(PAUSE_FLAG)
    except Exception:
        pass


def cmd_status() -> int:
    print("=" * 60)
    print("拥爱（AMBRACE）服务器体检  ", _now())
    print("=" * 60)
    uvicorn_pids = get_port_pids(PORT)
    lock6 = get_port_pids(LOCK_PORT_UVICORN)
    wd_lock = get_port_pids(LOCK_PORT_WATCHDOG)
    wd_procs = get_cmdline_pids("watchdog.py")
    paused = os.path.exists(PAUSE_FLAG)
    ok = http_ok()

    print("8000 监听（权威 uvicorn）: {0}".format(uvicorn_pids or "无"))
    print("8766 uvicorn 锁        : {0}".format(lock6 or "无"))
    print("8765 watchdog 锁       : {0}".format(wd_lock or "无"))
    print("watchdog.py 进程       : {0}".format(wd_procs or "无"))
    print("HTTP 根路径            : {0}".format("正常" if ok else "无响应"))
    print("暂停标记 paused.flag   : {0}".format("存在（守护暂停）" if paused else "不存在"))

    print("\n-- 相关进程 --")
    procs = _ps(
        "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
        "| Where-Object { $_.CommandLine -match 'uvicorn|watchdog' } "
        "| Select-Object ProcessId, @{N='Cmd';E={$_.CommandLine}} | Format-Table -AutoSize | Out-String -Width 200"
    )
    print(procs if procs else "(无)")

    print("\n-- server_stderr.log 尾部 --")
    print(read_tail(STDERR_LOG))
    print("\n-- watchdog.log 尾部 --")
    print(read_tail(WATCHDOG_LOG))

    print("\n-- 结论 --")
    if len(uvicorn_pids) > 1:
        print("警告：检测到多个 8000 监听实例 -> 运行 repair 修复双实例")
    elif uvicorn_pids and ok:
        print("正常：服务器运行中（单实例）")
    elif uvicorn_pids and not ok:
        print("警告：8000 有监听但 HTTP 无响应 -> 运行 repair 重启")
    else:
        print("异常：服务器未运行 -> 运行 start（或 repair）")
    print("注意：venv pythonw 的 shim+worker 成对进程属正常现象，勿当作双实例。")
    return 0


def _ensure_started() -> None:
    start_uvicorn()
    if wait_port(90):
        log("服务器就绪")
    else:
        log("等待超时，请查看 server_stderr.log")
    start_watchdog()


def cmd_start() -> int:
    if get_port_pids(PORT):
        log("8000 已有监听实例，无需重复启动（如需强制重启用 restart/repair）")
        return 0
    stop_all()
    clear_pause()
    _ensure_started()
    return 0


def cmd_stop() -> int:
    stop_all()
    log("已停止所有服务器相关进程（watchdog + uvicorn）")
    return 0


def cmd_restart() -> int:
    stop_all()
    clear_pause()
    _ensure_started()
    return 0


def cmd_repair() -> int:
    log("开始一键修复：杀净所有相关进程 -> 启动唯一实例")
    stop_all()
    clear_pause()
    _ensure_started()
    time.sleep(3)
    cmd_status()
    return 0


def main() -> int:
    cmds = {
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
        "restart": cmd_restart,
        "repair": cmd_repair,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        print("可用命令：status / start / stop / restart / repair")
        return 1
    return cmds[sys.argv[1]]()


if __name__ == "__main__":
    # Windows 控制台默认 GBK，日志中可能有无法编码的字符（如替换符），
    # reconfigure 让 print 遇到时用 ? 代替而不是抛 UnicodeEncodeError 崩溃
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:
            pass
    sys.exit(main())