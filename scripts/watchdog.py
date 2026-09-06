# -*- coding: utf-8 -*-
"""拥爱（AMBRACE）服务器守护进程 — 每 60 秒检查端口，挂了自动拉起（后台运行，不占窗口）"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/ 上一级
SERVER_DIR = os.path.join(_PROJECT_ROOT, "backend")
# B6（2026-09-06）：PYTHONW 按 os.name 分支——Windows 用 venv/Scripts/pythonw.exe（GUI 子系统、
# 无控制台窗口），POSIX 用 venv/bin/python。与 server_controller 的 _VENV_BIN 对齐，不再硬编码 Windows。
_VENV_BIN = "Scripts" if os.name == "nt" else "bin"
PYTHONW = os.path.join(SERVER_DIR, ".venv", _VENV_BIN,
                       "pythonw.exe" if os.name == "nt" else "python")
LOG = os.path.join(SERVER_DIR, "data", "logs", "watchdog.log")
LOCK_PORT = 8765  # 单实例锁端口
LOCKS_DIR = os.path.join(SERVER_DIR, "data", "locks")
PID_FILE = os.path.join(LOCKS_DIR, "watchdog.pid")
RESTART_GRACE_SEC = 120  # 拉起宽限期：uvicorn 启动慢（加载模型）时防重复拉起
_last_restart = 0.0
PAUSE_FLAG = os.path.join(SERVER_DIR, "data", "paused.flag")  # 存在时暂停自动拉起（由控制台软件控制）
CONFIG = os.path.join(SERVER_DIR, "data", "server_config.json")  # 控制台可修改的运行时配置
DEFAULT_INTERVAL = 120  # 默认检测间隔（秒）

# 每日备份：watchdog 运行期间每天备份一次源码+数据库（防文件损坏/误清空）
_last_backup_day = ""

# G4：通用外部网关守护（配置驱动，不写死具体进程）。读 backend/data/server_config.json 的
# gateway_watchdog_enabled + gateways[]（每项 {name, command:[...], cwd, probe|probe_port, enabled, grace_sec}）。
# 探活失败 → 拉起；尊重 paused.flag；每网关独立 grace 防抖；进程已在跑则不重复拉。
GATEWAY_RESTART_GRACE = 30          # 网关拉起后的全局最小间隔（秒），防崩溃循环狂拉
_gw_last_restart = {}               # name -> ts


def run_daily_backup() -> None:
    """每天首次进入新日期时执行一次备份；失败只记日志，不影响守护"""
    global _last_backup_day
    today = time.strftime("%Y%m%d")
    if _last_backup_day == today:
        return
    _last_backup_day = today
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import backup
        log(backup.do_backup())
    except Exception as e:
        log(f"Daily backup failed: {e}")

# 保持锁套接字引用，防止函数返回后被垃圾回收导致锁失效
_lock_socket = None


def acquire_lock() -> bool:
    """单实例锁：绑定本地端口，已有一个 watchdog 则返回 False"""
    global _lock_socket
    try:
        lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
        _lock_socket = lock
        return True
    except OSError:
        return False


def write_pid() -> None:
    """记录本 watchdog 进程 PID（供 server_manager 诊断/清理）"""
    try:
        os.makedirs(LOCKS_DIR, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def clear_pid() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def log(msg: str):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")
    except Exception:
        pass


def is_paused() -> bool:
    """暂停标记：控制台软件关闭服务器时创建，避免 watchdog 自动拉起"""
    return os.path.exists(PAUSE_FLAG)


def get_interval() -> int:
    """每次循环动态读取检测间隔（秒），控制台改配置后下个周期即生效"""
    try:
        import json
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        v = int(cfg.get("watchdog_interval_sec", DEFAULT_INTERVAL))
        return max(15, min(v, 3600))
    except Exception:
        return DEFAULT_INTERVAL


def port_listening(port: int, timeout: float = 1.0) -> bool:
    """纯 TCP 端口探测：毫秒级，不触发任何应用逻辑"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def start_server():
    if is_paused():
        log("Paused flag exists, skip restart")
        return
    # 端口已被占用 → 说明已有服务器在跑（如刚重启启动中），不要重复拉起
    if port_listening(8000):
        log("Port 8000 already listening, skip restart")
        return
    # 8766 被占 → 另一个 uvicorn 正在启动（已拿 app 单实例锁、尚未绑 8000）。
    # 若此刻再拉起，会与该实例竞态（虽会被 8766 锁挡住，但会产生多余 shim+worker 假象）。
    # 此处跳过本次，等下一个检测周期（避免"start 命令与 watchdog 自愈同时拉起"的竞态）。
    if port_listening(8766):
        log("Uvicorn lock port 8766 already bound, skip restart")
        return
    log("Server down detected, restarting...")
    try:
        with open(os.path.join(SERVER_DIR, "data", "logs", "server_stderr.log"), "a", encoding="utf-8") as f:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                cwd=SERVER_DIR,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=f,
                stderr=subprocess.STDOUT,
            )
        log("Restart command issued")
    except Exception as e:
        log(f"Restart failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# G4：通用外部网关守护（配置驱动）
# 默认配置示例（OpenClaw 网关；可替换/追加任意本地或外部进程）：
#   {
#     "gateway_watchdog_enabled": true,
#     "gateways": [
#       {"name": "OpenClaw Gateway", "enabled": true,
#        "command": ["C:\\Program Files\\nodejs\\node.exe",
#                    "C:\\Users\\sheng\\AppData\\Roaming\\npm\\node_modules\\openclaw\\dist\\index.js",
#                    "gateway", "--port", "18789"],
#        "cwd": "C:\\Users\\sheng",
#        "probe": {"kind": "tcp", "host": "127.0.0.1", "port": 18789},
#        "grace_sec": 30}
#     ]
#   }
# ─────────────────────────────────────────────────────────────────────────────


def _load_gateway_config():
    """读取通用网关配置；任何异常都返回「不守护」，绝不因配置脏数据影响主服务器守护。"""
    try:
        import json
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not cfg.get("gateway_watchdog_enabled", False):
            return []
        gws = cfg.get("gateways", [])
        return [g for g in gws if g.get("enabled") and g.get("command")]
    except Exception as e:
        log(f"Gateway config load failed: {e}")
        return []


def _gw_tcp_addr(gw):
    """从配置解析 TCP 探活地址 (host, port)：支持 probe dict / probe int / probe "host:port" / probe_port。"""
    probe = gw.get("probe")
    if isinstance(probe, dict):
        return probe.get("host", "127.0.0.1"), int(probe.get("port", 0))
    if isinstance(probe, int):
        return "127.0.0.1", probe
    if isinstance(probe, str) and ":" in probe:
        h, p = probe.rsplit(":", 1)
        return (h or "127.0.0.1"), int(p)
    port = gw.get("probe_port")
    if port:
        return "127.0.0.1", int(port)
    return "127.0.0.1", 0


def _gw_probe_ok(gw):
    """网关探活（与主服务器 8000 探测同口径的 TCP 探活，另备 http/process 兜底）。"""
    probe = gw.get("probe") or {}
    kind = probe.get("kind", "tcp") if isinstance(probe, dict) else "tcp"
    try:
        if kind == "tcp":
            host, port = _gw_tcp_addr(gw)
            return port > 0 and port_listening(port, timeout=1.0)
        if kind == "http":
            import urllib.request
            url = probe.get("url", "")
            with urllib.request.urlopen(url, timeout=3) as r:
                return 200 <= r.status < 400
        if kind == "process":
            kw = (probe.get("keyword") or "").lower()
            if not kw:
                return True
            try:
                import psutil
                for p in psutil.process_iter(["cmdline"]):
                    cl = " ".join(p.info.get("cmdline") or []).lower()
                    if kw in cl:
                        return True
                return False
            except ImportError:
                return True  # 无 psutil 时不做误杀：视为存活，交由 tcp/http 主探活
    except Exception:
        return False
    return False


def _start_gateway(gw):
    """拉起一个网关：隐藏窗口（Windows）、输出重定向到 data/logs/、尊重 paused.flag。"""
    if is_paused():
        return
    name = gw.get("name", "gateway")
    try:
        args = [str(a) for a in (gw.get("command") or [])]
        if not args:
            return
        kw = {"cwd": gw.get("cwd") or SERVER_DIR}
        log_path = os.path.join(SERVER_DIR, "data", "logs", f"gateway_{name}.log")
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            kw["stdin"] = subprocess.DEVNULL
            kw["stdout"] = open(log_path, "a", encoding="utf-8")
            kw["stderr"] = subprocess.STDOUT
        else:
            kw["start_new_session"] = True
        subprocess.Popen(args, **kw)
        log(f"Gateway [{name}] (re)started: {' '.join(args)}")
    except Exception as e:
        log(f"Gateway [{name}] start failed: {e}")


def monitor_gateways():
    """每个检测周期调用：对启用的网关探活，挂了且超过各自 grace 则拉起（防抖不重复拉）。"""
    now = time.time()
    for gw in _load_gateway_config():
        name = gw.get("name", "gateway")
        if _gw_probe_ok(gw):
            continue
        grace = int(gw.get("grace_sec", GATEWAY_RESTART_GRACE))
        last = _gw_last_restart.get(name, 0.0)
        if now - last < max(grace, GATEWAY_RESTART_GRACE):
            continue
        _gw_last_restart[name] = now
        log(f"Gateway [{name}] down detected, restarting...")
        _start_gateway(gw)


def main():
    if not acquire_lock():
        log("Another watchdog already running, exiting")
        sys.exit(0)
    write_pid()
    log("Watchdog started (single instance, TCP probe + pid)")
    global _last_restart
    _last_restart = time.time()  # 启动即进入宽限期，避免启动慢时误判掉线
    try:
        while True:
            try:
                run_daily_backup()
                monitor_gateways()
                if not port_listening(8000):
                    if not is_paused():
                        now = time.time()
                        if now - _last_restart < RESTART_GRACE_SEC:
                            log(f"Skip restart, within grace period ({RESTART_GRACE_SEC}s)")
                        else:
                            start_server()
                            _last_restart = now
            except Exception as e:
                log(f"Check error: {e}")
            time.sleep(get_interval())
    finally:
        clear_pid()


if __name__ == "__main__":
    main()
