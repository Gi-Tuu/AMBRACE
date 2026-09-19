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
  （POSIX / Linux·macOS）backend/.venv/bin/python scripts/server_manager.py status|repair|start|stop|restart

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
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime

# C1（2026-09-17）：stdio 重定向日志的「启动前轮转」，实现与调用方同级（scripts/log_rotate.py）。
# P3-9（2026-09-2x）：跨平台拉起键 / venv 解释器路径 / 端口与命令行 PID 查询同样收敛到同级
# scripts/platform_util.py。
# 正常以「python <脚本绝对路径>」运行时 sys.path[0] 即 scripts/，同级导入可用；
# 兜底：被按文件路径加载（如单测 importlib 加载）时 scripts/ 不在 sys.path，补一次再导入。
try:
    from log_rotate import rotate_stdio_log
    import platform_util
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from log_rotate import rotate_stdio_log
    import platform_util

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
BACKEND_DIR = os.path.join(BASE_DIR, "backend")

# B6 补（2026-09-19）/ P3-9（2026-09-2x）：与 watchdog / server_controller 对齐——Windows 用
# venv/Scripts/pythonw.exe，POSIX 用 venv/bin/python（POSIX 无 pythonw，也没有 GUI 子系统概念）；
# 拉起键 Windows = creationflags、POSIX = start_new_session。统一由 platform_util 提供。
PYTHONW, PYTHON = platform_util.venv_python_paths(BACKEND_DIR)
WATCHDOG_PY = os.path.join(BASE_DIR, "scripts", "watchdog.py")
LOGS_DIR = os.path.join(BACKEND_DIR, "data", "logs")
STDERR_LOG = os.path.join(LOGS_DIR, "server_stderr.log")
STDOUT_LOG = os.path.join(LOGS_DIR, "server_stdout.log")
WATCHDOG_LOG = os.path.join(LOGS_DIR, "watchdog.log")
PAUSE_FLAG = os.path.join(BACKEND_DIR, "data", "paused.flag")
LOCKS_DIR = os.path.join(BACKEND_DIR, "data", "locks")
WATCHDOG_PID_FILE = os.path.join(LOCKS_DIR, "watchdog.pid")
PORT = 8000
# P2-2：单实例锁端口可配置（与 backend/app/main.py 同步，读环境变量 INSTANCE_LOCK_PORT，默认 8766）
LOCK_PORT_UVICORN = int(os.environ.get("INSTANCE_LOCK_PORT", "8766"))
LOCK_PORT_WATCHDOG = 8765
# 隐藏短命子进程（PowerShell）窗口；POSIX 上该属性不存在 -> 0。
# P3-9：拉起键里的 Windows-only 常量已收敛到 platform_util.popen_kwargs()；
# 本常量保持收敛前的原值（NO_WINDOW | CREATE_NEW_PROCESS_GROUP），行为逐位一致。
NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


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


def _sh(cmd: list) -> str:
    """运行 POSIX 命令并返回 stdout（15s 超时）；Windows 侧不使用（用 _ps）。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def get_port_pids(port: int) -> list:
    """返回监听指定端口的所有 PID（权威实例判定）：实现见 scripts/platform_util.port_pids()。"""
    return platform_util.port_pids(port)


def get_cmdline_pids(keyword: str) -> list:
    """按命令行关键字匹配进程 PID：实现见 scripts/platform_util.cmdline_pids()。"""
    return platform_util.cmdline_pids(keyword)


def kill_pids(pids, force: bool = False) -> None:
    """终止进程：Windows 用 Stop-Process -Force（等同强杀）；POSIX 默认 SIGTERM，force=True 时 SIGKILL。"""
    for pid in pids:
        if os.name == "nt":
            _ps("Stop-Process -Id {0} -Force -ErrorAction SilentlyContinue".format(pid))
        else:
            try:
                os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
            except Exception:
                pass


def http_ok(timeout: float = 1.5) -> bool:
    """纯 TCP 探活：仅判断端口可连（watchdog 用它做快速探测）。"""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def http_ready(timeout: float = 2.0) -> bool:
    """真实就绪判定：向 /api/v1/system/status 发 GET，返回 200 才算就绪。
    uvicorn 先绑 8000 再加载模型（bge-m3 约 30-60s），仅 TCP 连上不代表应用可用；
    用它做 start/restart/repair 的"等待就绪"，避免误判后提前拉起 watchdog。"""
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout) as s:
            s.sendall(b"GET /api/v1/system/status HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            data = s.recv(512)
            return b"200" in data[:16]
    except OSError:
        return False


def _log_read_path(path: str) -> str:
    """C1：启动前轮转后当前日志为空（或尚未重建）时优先读 .1。
    否则体检面板会显示一片空白，容易被误判成「服务启动失败」。"""
    alt = path + ".1"
    try:
        if os.path.exists(alt) and (not os.path.isfile(path) or os.path.getsize(path) == 0):
            return alt
    except OSError:
        pass
    return path


def read_tail(path: str, n: int = 15) -> str:
    try:
        with open(_log_read_path(path), "rb") as f:
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
    # C1：必须在打开重定向句柄之前轮转（Windows 上被占用的日志无法改名）
    _rot = rotate_stdio_log(STDERR_LOG, max_mb=10, keep=2) + rotate_stdio_log(STDOUT_LOG, max_mb=10, keep=2)
    if _rot:
        log(_rot)
    with open(STDOUT_LOG, "a", encoding="utf-8") as fout:
        with open(STDERR_LOG, "a", encoding="utf-8") as ferr:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(PORT)],
                cwd=BACKEND_DIR,
                stdout=fout, stderr=ferr,
                **platform_util.popen_kwargs(),
            )
    log("uvicorn 启动命令已发出（加载模型约需 30-60 秒）")


def start_watchdog() -> None:
    subprocess.Popen(
        [PYTHONW, WATCHDOG_PY],
        **platform_util.popen_kwargs(),
    )
    log("watchdog 启动命令已发出")


def wait_port(timeout: int = 90) -> bool:
    """等待 uvicorn 应用真正就绪（HTTP /api/v1/system/status 返回 200），而非仅端口可连。
    加载 bge-m3 约 30-60s；超时返回 False，但仍继续启动 watchdog（由 watchdog 自愈兜底）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if http_ready():
            return True
        time.sleep(1.5)
    return False


def clean_lock_files() -> None:
    """清理守护残留锁文件：watchdog 被强杀时本轮的 finally/clear_pid 不会执行，
    导致 data\\locks\\watchdog.pid 残留；stop/restart/repair 后必须主动删除，
    避免残留 pid 被误判为双实例或影响下一次启动。"""
    try:
        if os.path.exists(WATCHDOG_PID_FILE):
            os.remove(WATCHDOG_PID_FILE)
    except Exception:
        pass


def stop_all() -> None:
    """停止所有相关进程：先杀 watchdog（防拉起），再杀 uvicorn（含 launcher shim），最后清锁"""
    log("停止 watchdog（8765 锁 + watchdog.py 进程）...")
    kill_pids(get_port_pids(LOCK_PORT_WATCHDOG))
    kill_pids(get_cmdline_pids("watchdog.py"))
    log("停止 uvicorn（8000 监听 + 8766 锁 + uvicorn 进程）...")
    kill_pids(get_port_pids(PORT))
    kill_pids(get_port_pids(LOCK_PORT_UVICORN))
    # venv 的 pythonw.exe 是 launcher shim，会再起一个同名 worker（命令行相同）；
    # 端口锁定只命中 worker，这里再按命令行补杀 shim，确保杀净，避免残留 shim 被误判为双实例。
    kill_pids(get_cmdline_pids("uvicorn"))
    # 写暂停标记，与桌面控制台兼容（防残留 watchdog 拉起）
    try:
        os.makedirs(os.path.dirname(PAUSE_FLAG), exist_ok=True)
        with open(PAUSE_FLAG, "w", encoding="utf-8") as f:
            f.write("stopped by server_manager " + _now())
    except Exception:
        pass
    clean_lock_files()
    time.sleep(1.5)
    # 等 8000/8765/8766 三端口真正释放，避免 start 与残留实例竞态
    # POSIX：SIGTERM 属优雅终止，宽限后半程仍占端口的一律升级 SIGKILL（Windows 侧 Stop-Process -Force 已是强杀）
    for i in range(10):
        if not (get_port_pids(PORT) or get_port_pids(LOCK_PORT_UVICORN) or get_port_pids(LOCK_PORT_WATCHDOG)):
            break
        if os.name != "nt" and i >= 4:
            kill_pids(sorted(set(get_port_pids(PORT) + get_port_pids(LOCK_PORT_UVICORN)
                                 + get_port_pids(LOCK_PORT_WATCHDOG))), force=True)
        time.sleep(0.5)


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
    if os.name == "nt":
        procs = _ps(
            "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe' or Name='python.exe'\" "
            "| Where-Object { $_.CommandLine -match 'uvicorn|watchdog' } "
            "| Select-Object ProcessId, @{N='Cmd';E={$_.CommandLine}} | Format-Table -AutoSize | Out-String -Width 200"
        )
    else:
        procs = "\n".join(
            l for l in _sh(["ps", "-eo", "pid=,args="]).splitlines()
            if "uvicorn" in l or "watchdog" in l
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
    if os.name == "nt":
        print("注意：venv pythonw 的 shim+worker 成对进程属正常现象，勿当作双实例。")
    else:
        print("注意：POSIX 侧 uvicorn / watchdog 各只有一个进程，出现重复 PID 即为真双实例。")
    return 0


def _ensure_started() -> None:
    start_uvicorn()
    if wait_port(90):
        log("服务器就绪（HTTP /api/v1/system/status 200）")
    else:
        log("等待超时，请查看 server_stderr.log")
    start_watchdog()
    # 单实例复核：uvicorn 由 main.py 的 8766 锁保证唯一，watchdog 由 8765 锁保证唯一。
    # 此处仅信息性确认，不盲目重启，避免掩盖真正的竞态；如异常由后续 repair 兜底。
    u6 = get_port_pids(LOCK_PORT_UVICORN)
    wd = get_port_pids(LOCK_PORT_WATCHDOG)
    log("实例确认：uvicorn 锁(8766)={0}  watchdog 锁(8765)={1}".format(u6 or "无", wd or "无"))
    if len(u6) > 1 or len(wd) > 1:
        log("警告：检测到多重实例锁，请运行 repair 复查")


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