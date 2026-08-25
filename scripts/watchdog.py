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
# pythonw.exe：GUI 子系统，启动 uvicorn 永远无控制台窗口（完全静默）
PYTHONW = os.path.join(SERVER_DIR, ".venv", "Scripts", "pythonw.exe")
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
