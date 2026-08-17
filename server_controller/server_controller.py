# -*- coding: utf-8 -*-
"""拥爱（AMBRACE）服务器控制台 — 启动/停止/重启服务器（与 watchdog 协调，全程无终端弹窗）"""
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（server_controller/ 的上级）
SERVER_DIR = os.path.join(PROJECT_ROOT, "backend")
_VENV_BIN = "Scripts" if os.name == "nt" else "bin"
PYTHONW = os.path.join(SERVER_DIR, ".venv", _VENV_BIN, "pythonw.exe" if os.name == "nt" else "python")
PAUSE_FLAG = os.path.join(SERVER_DIR, "data", "paused.flag")
STDERR_LOG = os.path.join(SERVER_DIR, "data", "logs", "server_stderr.log")
PYTHON = os.path.join(SERVER_DIR, ".venv", _VENV_BIN, "python.exe" if os.name == "nt" else "python")
MANAGER_PY = os.path.join(PROJECT_ROOT, "scripts", "server_manager.py")
CONFIG = os.path.join(SERVER_DIR, "data", "server_config.json")  # 与 watchdog 共享的运行时配置
DEFAULT_REFRESH_MS = 60000  # 默认界面刷新周期（毫秒）
POLL_MS = 200
LOG_TAIL = 40
# ---- 图像理解服务（Ollama，未来后端控制台可把每个服务抽象为 {name, port, start, stop} 列表）----
OLLAMA_PORT = 11434
OLLAMA_LABEL = "图像理解服务（Ollama）"
# Windows 隐藏所有子进程的控制台窗口（PowerShell 是控制台程序，不设会在刷新时闪窗）；Linux/macOS 用 start_new_session
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def _load_config() -> dict:
    """读取与 watchdog 共享的运行时配置（server_config.json）"""
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_ollama() -> str:
    """定位 Ollama 可执行文件：配置(ollama_exe) > 环境变量 OLLAMA_EXE > PATH > Windows 常见安装路径"""
    for cand in (_load_config().get("ollama_exe"), os.environ.get("OLLAMA_EXE")):
        if cand and os.path.isfile(cand):
            return cand
    which = shutil.which("ollama")
    if which:
        return which
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        for cand in (
                     os.path.join(local, "Programs", "Ollama", "ollama.exe") if local else "",
                     os.path.join("C:", os.sep, "Program Files", "Ollama", "ollama.exe"),
                     os.path.join("C:", os.sep, "Program Files (x86)", "Ollama", "ollama.exe")):
            if cand and os.path.isfile(cand):
                return cand
    return ""


def _ollama_models_dir() -> str:
    """Ollama 模型目录：配置(ollama_models_dir) > 环境变量 OLLAMA_MODELS > Windows 旧路径（存在才注入，防环境变量继承丢失）"""
    for cand in (_load_config().get("ollama_models_dir"), os.environ.get("OLLAMA_MODELS"),
                 ""):
        if cand and os.path.isdir(cand):
            return cand
    return ""


def _popen_kwargs() -> dict:
    """Windows：隐藏控制台窗口并脱离父进程；Linux/macOS：新会话后台运行（等效 nohup）"""
    if os.name == "nt":
        return {"creationflags": NO_WINDOW | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def _get_refresh_ms() -> int:
    """读取界面刷新间隔（毫秒），默认 60 秒；控制台设置后立即生效"""
    try:
        import json
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        v = int(cfg.get("controller_refresh_ms", DEFAULT_REFRESH_MS))
        return max(5000, min(v, 600000))
    except Exception:
        return DEFAULT_REFRESH_MS


def _check_alive() -> bool:
    """纯 TCP 端口探测（毫秒级，不触发应用逻辑）"""
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            return True
    except OSError:
        return False


def _port_pid(port: int) -> int:
    """查询监听指定端口的进程 PID（优先 psutil；未安装时 Windows 用 PowerShell、其他平台用 lsof 降级）"""
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return conn.pid or 0
        return 0
    except ImportError:
        pass
    except Exception:
        return 0
    if os.name == "nt":
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$c = Get-NetTCPConnection -State Listen -LocalPort %d -ErrorAction SilentlyContinue; " % port,
                 "if ($c) { $c.OwningProcess } else { 0 }"],
                capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
            )
            out = (ps.stdout or "").strip().splitlines()
            return int(out[0]) if out and out[0].strip().isdigit() else 0
        except Exception:
            return 0
    try:
        ps = subprocess.run(["lsof", "-t", "-i:%d" % port], capture_output=True, text=True, timeout=5)
        out = (ps.stdout or "").strip().splitlines()
        return int(out[0]) if out and out[0].strip().isdigit() else 0
    except Exception:
        return 0


def _get_pid() -> int:
    """查询监听 8000 的进程 PID"""
    return _port_pid(8000)


def _run_manager(cmd: str) -> None:
    """统一走 server_manager（清理残留 + 启动唯一实例），隐藏窗口"""
    try:
        subprocess.Popen([PYTHON, MANAGER_PY, cmd], **_popen_kwargs())
    except Exception as e:
        raise RuntimeError("执行失败: {0}".format(e))

def _start_uvicorn():
    """静默启动 uvicorn（Windows 用 pythonw；Linux/macOS 用新会话后台运行，日志重定向到 server_stderr.log）"""
    try:
        os.makedirs(os.path.dirname(STDERR_LOG), exist_ok=True)
        with open(STDERR_LOG, "a", encoding="utf-8") as f:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
                cwd=SERVER_DIR,
                stdout=f,
                stderr=subprocess.STDOUT,
                **_popen_kwargs(),
            )
    except Exception as e:
        raise RuntimeError(f"启动失败: {e}")


def _ollama_alive() -> bool:
    """Ollama 服务 TCP 探活（端口 11434，毫秒级）"""
    try:
        with socket.create_connection(("127.0.0.1", OLLAMA_PORT), timeout=1):
            return True
    except OSError:
        return False


def _get_ollama_pid() -> int:
    """查询监听 11434 的进程 PID"""
    return _port_pid(OLLAMA_PORT)


def _start_ollama(low_vram: bool = False) -> None:
    """静默启动 Ollama serve（low_vram=True 时语言层走 CPU，显存约 4.4GB->2.4GB）"""
    exe = _find_ollama()
    if not exe:
        raise RuntimeError("未找到 Ollama：请在 server_config.json 配置 ollama_exe（或安装 Ollama 并加入 PATH）后重试")
    try:
        env = dict(os.environ)
        models_dir = _ollama_models_dir()
        if models_dir:
            env["OLLAMA_MODELS"] = models_dir
        if low_vram:
            env["LLAMA_ARG_N_GPU_LAYERS"] = "0"
        subprocess.Popen([exe, "serve"], env=env, **_popen_kwargs())
    except Exception as e:
        raise RuntimeError(f"Ollama 启动失败: {e}")


def _load_low_vram() -> bool:
    """读取省显存模式开关（持久化在 server_config.json，与 watchdog 共享）"""
    try:
        import json
        with open(CONFIG, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("ollama_low_vram", False))
    except Exception:
        return False


def _kill_tree(proc, killed: set) -> None:
    """递归终止进程及其子进程（防推理子进程变孤儿继续占显存）"""
    try:
        pid = proc.pid
        if pid in killed:
            return
        killed.add(pid)
        for child in proc.children(recursive=True):
            try:
                child.kill()
            except Exception:
                pass
        proc.kill()
    except Exception:
        pass


def _stop_ollama() -> None:
    """停止 Ollama（主进程进程树 + 残留 llama-server 推理子进程；优先 psutil，未安装时按平台降级）"""
    try:
        import psutil
        killed: set = set()
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == OLLAMA_PORT and conn.pid:
                try:
                    _kill_tree(psutil.Process(conn.pid), killed)
                except Exception:
                    pass
        for proc in psutil.process_iter(["pid", "name"]):
            name = (proc.info.get("name") or "").lower()
            if name in ("llama-server", "llama-server.exe") and proc.info.get("pid") not in killed:
                _kill_tree(proc, killed)
        return
    except ImportError:
        pass
    except Exception as e:
        raise RuntimeError(f"Ollama 停止失败: {e}")
    # 降级（无 psutil）
    if os.name == "nt":
        try:
            ollama_root = os.path.dirname(_find_ollama())
            script_parts = [
                "$ErrorActionPreference='SilentlyContinue'; ",
                "$p = Get-NetTCPConnection -State Listen -LocalPort %d | " % OLLAMA_PORT,
                "Select-Object -ExpandProperty OwningProcess; ",
                "if ($p) { taskkill /T /F /PID $p | Out-Null }; ",
                # 仅杀主进程时 llama-server 推理子进程会变孤儿继续占显存，需一并清理
                "Get-CimInstance Win32_Process | ",
                "Where-Object { $_.Name -eq 'llama-server.exe' -and ",
                "$_.ExecutablePath -like '" + ollama_root + "*' } | ",
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ]
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "".join(script_parts)],
                capture_output=True, timeout=15, creationflags=NO_WINDOW,
            )
        except Exception as e:
            raise RuntimeError(f"Ollama 停止失败: {e}")
    else:
        try:
            lsof = subprocess.run(["lsof", "-t", "-i:%d" % OLLAMA_PORT], capture_output=True, text=True, timeout=5)
            for pid in (lsof.stdout or "").split():
                subprocess.run(["kill", "-9", pid], timeout=5)
            subprocess.run(["pkill", "-9", "-f", "llama-server"], timeout=5)
        except Exception as e:
            raise RuntimeError(f"Ollama 停止失败: {e}")



def _tail_log():
    """读取日志尾部"""
    try:
        if not os.path.exists(STDERR_LOG):
            return ""
        with open(STDERR_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 12000))
            data = f.read().decode("utf-8", errors="replace")
        return "\n".join(data.splitlines()[-LOG_TAIL:])
    except Exception:
        return ""


class ControllerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: "queue.Queue[tuple]" = queue.Queue()
        root.title("拥爱（AMBRACE）服务器控制台")
        root.geometry("680x580")
        root.minsize(520, 460)

        top = ttk.Frame(root, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        self.status_dot = tk.Label(top, text="\u25cf", font=("Segoe UI", 20), fg="grey")
        self.status_dot.pack(side="left")
        self.status_label = tk.Label(top, text="检测中…", font=("Segoe UI", 12))
        self.status_label.pack(side="left", padx=10)
        self.paused_label = tk.Label(top, text="", font=("Segoe UI", 9), fg="#e65100")
        self.paused_label.pack(side="left")

        btns = ttk.Frame(root, padding=(12, 4))
        btns.pack(fill="x")
        self.btn_start = ttk.Button(btns, text="\u25b6 启动服务器", command=self.start_server)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(btns, text="\u25a0 停止服务器", command=self.stop_server)
        self.btn_stop.pack(side="left", padx=4)
        self.btn_restart = ttk.Button(btns, text="\u21bb 重启服务器", command=self.restart_server)
        self.btn_restart.pack(side="left", padx=4)
        ttk.Button(btns, text="打开日志", command=self.open_log).pack(side="left", padx=4)
        ttk.Button(btns, text="设置", command=self.open_settings).pack(side="left", padx=4)

        ollama_row = ttk.Frame(root, padding=(12, 2))
        ollama_row.pack(fill="x")
        self.ollama_dot = tk.Label(ollama_row, text="\u25cf", font=("Segoe UI", 20), fg="grey")
        self.ollama_dot.pack(side="left")
        ttk.Label(ollama_row, text=OLLAMA_LABEL, font=("Segoe UI", 12)).pack(side="left", padx=8)
        self.ollama_pid_label = tk.Label(ollama_row, text="", font=("Segoe UI", 9), fg="#666")
        self.ollama_pid_label.pack(side="left")
        self.btn_ollama_stop = ttk.Button(ollama_row, text="\u25a0 停止 Ollama", command=self.stop_ollama)
        self.btn_ollama_stop.pack(side="right", padx=4)
        self.btn_ollama_start = ttk.Button(ollama_row, text="\u25b6 启动 Ollama", command=self.start_ollama)
        self.btn_ollama_start.pack(side="right", padx=4)

        self._ollama_low_vram = _load_low_vram()
        vram_row = ttk.Frame(root, padding=(14, 2))
        vram_row.pack(fill="x")
        self.low_vram_var = tk.BooleanVar(value=self._ollama_low_vram)
        self.chk_low_vram = ttk.Checkbutton(
            vram_row, text="省显存模式（语言层走 CPU，显存 4.4→2.4GB，玩大型游戏时开启）",
            variable=self.low_vram_var, command=self.toggle_low_vram)
        self.chk_low_vram.pack(side="left")
        self.low_vram_label = tk.Label(vram_row, text="", font=("Segoe UI", 9), fg="#888")
        self.low_vram_label.pack(side="left", padx=8)

        self.msg = tk.Label(root, text="", fg="#666", font=("Segoe UI", 9))
        self.msg.pack(anchor="w", padx=16)

        self.log_box = scrolledtext.ScrolledText(root, height=18, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(4, 10))

        self._last_log = ""
        self._last_state = None
        self._ollama_busy = False
        self._busy = False
        self._refreshing = False
        self._poll()          # 主线程轮询队列（唯一跨线程通信通道）
        self._do_refresh()    # 立即刷新一次
        self._schedule_refresh()

    def _set_msg(self, text: str):
        self.msg.config(text=text)

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_start, self.btn_stop, self.btn_restart):
            b.config(state=state)

    def _set_ollama_busy(self, busy: bool):
        self._ollama_busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_ollama_start, self.btn_ollama_stop):
            b.config(state=state)

    # ── 主线程轮询：处理后台线程投递的结果 ──
    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "state":
                    _, alive, pid, paused, log_text, ollama_alive, ollama_pid = item
                    self._apply_refresh(alive, pid, paused, log_text, ollama_alive, ollama_pid)
                elif kind == "msg":
                    self._set_msg(item[1])
                elif kind == "busy":
                    self._set_busy(False)
                elif kind == "busy_ollama":
                    self._set_ollama_busy(False)
                elif kind == "refresh":
                    self._do_refresh()
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    # ── 操作（按钮）：耗时部分放后台线程，结果经队列回传 ──
    def _run_action(self, title: str, fn):
        if self._busy:
            return
        self._set_busy(True)
        self._set_msg(f"{title}…")

        def job():
            try:
                msg = fn()
                self._q.put(("msg", msg))
            except Exception as e:
                self._q.put(("msg", f"操作失败: {e}"))
            finally:
                self._q.put(("busy", False))
                self._q.put(("refresh", None))

        threading.Thread(target=job, daemon=True).start()

    def start_server(self):
        def fn():
            if os.name == "nt":
                _run_manager("start")
                return "启动指令已发出（server_manager），服务器约 30-60 秒就绪"
            _start_uvicorn()
            return "已启动 uvicorn（后台），服务器约 30-60 秒就绪"
        self._run_action("正在启动服务器", fn)

    def stop_server(self):
        def fn():
            if os.name == "nt":
                _run_manager("stop")
                return "已停止（server_manager 已清理 uvicorn + watchdog）"
            pid = _port_pid(8000)
            if pid:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
                return f"已停止（PID {pid}）"
            return "未检测到运行中的服务器（端口 8000 无监听）"
        self._run_action("正在停止服务器", fn)

    def restart_server(self):
        def fn():
            if os.name == "nt":
                _run_manager("restart")
                return "重启指令已发出（server_manager），服务器约 30-60 秒就绪"
            pid = _port_pid(8000)
            if pid:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
            _start_uvicorn()
            return "已重启 uvicorn（后台）"
        self._run_action("正在重启服务器", fn)

    def _run_ollama_action(self, title: str, fn):
        if self._ollama_busy:
            return
        self._set_ollama_busy(True)
        self._set_msg(f"{title}…")

        def job():
            try:
                self._q.put(("msg", fn()))
            except Exception as e:
                self._q.put(("msg", f"操作失败: {e}"))
            finally:
                self._q.put(("busy_ollama", False))
                self._q.put(("refresh", None))

        threading.Thread(target=job, daemon=True).start()

    def start_ollama(self):
        def fn():
            _start_ollama(self._ollama_low_vram)
            mode = "（省显存模式）" if self._ollama_low_vram else "（全速模式）"
            return "Ollama 启动指令已发出，模型加载约需 5-10 秒" + mode
        self._run_ollama_action("正在启动 Ollama", fn)

    def toggle_low_vram(self):
        """切换省显存模式：保存配置；Ollama 运行中则自动重启生效"""
        import json
        on = self.low_vram_var.get()
        self._ollama_low_vram = on
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        cfg["ollama_low_vram"] = on
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if _ollama_alive():
            self._run_ollama_action("正在切换 Ollama 模式", self._restart_ollama_with_mode)
        else:
            self._set_msg("已保存：下次启动 Ollama 时生效" + ("（省显存模式）" if on else "（全速模式）"))

    def _restart_ollama_with_mode(self):
        _stop_ollama()
        time.sleep(2)
        _start_ollama(self._ollama_low_vram)
        if self._ollama_low_vram:
            return "已切换省显存模式：显存占用降至约 2.4GB（图片理解略慢，可忽略）"
        return "已恢复全速模式：显存占用约 4.4GB，图片理解最快"

    def stop_ollama(self):
        def fn():
            _stop_ollama()
            return "Ollama 已停止"
        self._run_ollama_action("正在停止 Ollama", fn)

    def open_log(self):
        if not os.path.exists(STDERR_LOG):
            return
        if os.name == "nt":
            os.startfile(STDERR_LOG)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", STDERR_LOG])
        else:
            subprocess.Popen(["xdg-open", STDERR_LOG])

    def open_settings(self):
        """设置窗口：自定义 watchdog 检测间隔与控制台刷新间隔（写入共享配置）"""
        try:
            import json
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.resizable(False, False)
        win.transient(self.root)

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="守护检测间隔（秒）").grid(row=0, column=0, sticky="w", pady=(0, 4))
        wd_var = tk.StringVar(value=str(cfg.get("watchdog_interval_sec", 120)))
        ttk.Entry(frame, textvariable=wd_var, width=14).grid(row=0, column=1, sticky="w", pady=(0, 4))

        ttk.Label(frame, text="界面刷新间隔（秒）").grid(row=1, column=0, sticky="w", pady=(0, 12))
        rf_var = tk.StringVar(value=str(int(cfg.get("controller_refresh_ms", 60000)) // 1000))
        ttk.Entry(frame, textvariable=rf_var, width=14).grid(row=1, column=1, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="修改后立即生效：\n守护间隔下个检测周期生效，界面刷新即时生效。",
                  foreground="#888", font=("Segoe UI", 9)).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 12))

        def save():
            try:
                wd = int(wd_var.get().strip())
                rf = int(rf_var.get().strip())
                if not (15 <= wd <= 3600):
                    raise ValueError("守护间隔需在 15-3600 秒之间")
                if not (5 <= rf <= 600):
                    raise ValueError("刷新间隔需在 5-600 秒之间")
                with open(CONFIG, "r", encoding="utf-8") as f:
                    old_cfg = json.load(f)
                cfg_new = dict(old_cfg)
                cfg_new["watchdog_interval_sec"] = wd
                cfg_new["controller_refresh_ms"] = rf * 1000
                with open(CONFIG, "w", encoding="utf-8") as f:
                    json.dump(cfg_new, f, ensure_ascii=False, indent=2)
                win.destroy()
                self._set_msg(f"已保存：守护 {wd}s / 刷新 {rf}s")
                self._do_refresh()
            except ValueError as e:
                tk.messagebox.showerror("输入无效", str(e), parent=win)
            except Exception as e:
                tk.messagebox.showerror("保存失败", str(e), parent=win)

        ttk.Button(frame, text="保存", command=save).grid(row=3, column=0, sticky="w")
        ttk.Button(frame, text="取消", command=win.destroy).grid(row=3, column=1, sticky="w")

        win.grab_set()

    # ── 定时刷新：后台线程采集，主线程应用，杜绝卡顿/闪窗 ──
    def _schedule_refresh(self):
        self.root.after(_get_refresh_ms(), self._schedule_refresh)
        self._do_refresh()

    def _do_refresh(self):
        if self._refreshing:
            return
        self._refreshing = True

        def job():
            try:
                alive = _check_alive()
                pid = _get_pid() if alive else 0
                paused = os.path.exists(PAUSE_FLAG)
                log_text = _tail_log()
                ollama_alive = _ollama_alive()
                ollama_pid = _get_ollama_pid() if ollama_alive else 0
                self._q.put(("state", alive, pid, paused, log_text, ollama_alive, ollama_pid))
            except Exception:
                pass
            finally:
                self._refreshing = False

        threading.Thread(target=job, daemon=True).start()

    def _apply_refresh(self, alive: bool, pid: int, paused: bool, log_text: str,
                       ollama_alive: bool, ollama_pid: int):
        # Ollama 状态：每次刷新更新（含 PID 变化），不依赖服务器状态
        if ollama_alive:
            self.ollama_dot.config(fg="#2e7d32")
            self.ollama_pid_label.config(text=f"运行中  (PID {ollama_pid})")
        else:
            self.ollama_dot.config(fg="#c62828")
            self.ollama_pid_label.config(text="未运行")
        state = (alive, paused, ollama_alive)
        # 状态未变化时，仅当日志有新内容才刷新日志框；UI 状态标签不变
        if state == self._last_state:
            if log_text != self._last_log:
                self._last_log = log_text
                self.log_box.config(state="normal")
                self.log_box.delete("1.0", "end")
                self.log_box.insert("1.0", log_text)
                self.log_box.see("end")
                self.log_box.config(state="disabled")
            return
        self._last_state = state
        if alive:
            self.status_dot.config(fg="#2e7d32")
            self.status_label.config(text=f"运行中  (PID {pid})")
        else:
            self.status_dot.config(fg="#c62828")
            self.status_label.config(text="已停止")
        self.paused_label.config(text="守护暂停中" if paused else "")
        if log_text != self._last_log:
            self._last_log = log_text
            self.log_box.config(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", log_text)
            self.log_box.see("end")
            self.log_box.config(state="disabled")


def main():
    root = tk.Tk()
    ControllerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
