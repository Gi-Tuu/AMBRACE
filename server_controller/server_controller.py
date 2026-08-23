# -*- coding: utf-8 -*-
"""拥爱（AMBRACE）服务器控制台 — 启动/停止/重启服务器（与 watchdog 协调，全程无终端弹窗）
UI v2（2026-08-23 控制台 UI 重做）：侧边栏导航 + 顶部状态栏 + 主页仪表盘（状态卡片）。
视觉齐平 flutter_app 的 Design Token 原子（AppColors/AppSpacing/AppRadius/AppShadow 思路），
所有既有功能/接口调用保持不变；新增仪表盘统计（角色数/记忆数/Token）经只读方式读取，不改生产数据。
"""
import json
import os
import queue
import shutil
import socket
import sqlite3
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

# ---- 视觉主题（对齐 flutter_app/lib/theme/tokens.dart 的 AppColors 原子）----
# 2026-08-23 打磨：为消除『糊』观感做对比度修正——正常文本 ≥4.5:1、图形控件 ≥3:1（WCAG）。
#   secondary #8E8E93(3.3:1)→#6E6E73(5.1:1)；accent #007AFF(4.0:1)→#0066CC(5.6:1 兼作主按钮底)；
#   三态语义色改用「文本安全」深变体：success #2E7D32(5.1:1)/warning #B25000(5.2:1)/error #D70015(5.4:1)。
COL_BG = "#F2F2F7"          # bgLight    主背景
COL_SIDEBAR = "#F2F2F7"     # 侧边栏背景（与主背景同色，靠高亮 + 右缘分隔线区分）
COL_CARD = "#FFFFFF"        # cardLight  卡片
COL_DIVIDER = "#ECECEF"     # dividerLight 分隔线（顶部栏下分隔）
COL_HAIRLINE = "#C6C6C8"    # hairline→border 卡片/按钮/输入框细边（稍加深，卡片层次更清晰）
COL_SURFACE_ALT = "#E9E9EB" # surfaceAlt 次级底色/悬停
COL_TEXT = "#1C1C1E"        # textPrimary
COL_TEXT_SEC = "#6E6E73"    # textSecondary（5.1:1，替代原 #8E8E93 3.3:1）
COL_TEXT_MUTED = "#636366"  # textMuted（5.9:1，状态栏/提示仍可读）
COL_ACCENT = "#0066CC"      # accent     品牌蓝（5.6:1，兼作主按钮底）
COL_SUCCESS = "#2E7D32"     # success（5.1:1，文本安全绿）
COL_WARNING = "#B25000"     # warning（5.2:1，文本安全琥珀）
COL_ERROR = "#D70015"       # error（5.4:1，文本安全红）
# 间距/圆角原子（AppSpacing / AppRadius）
SP_XXS, SP_XS, SP_SM, SP_MD, SP_LG, SP_XL = 4, 8, 12, 16, 24, 32
RD_SM, RD_MD, RD_LG = 8, 12, 16


def _font_family() -> str:
    """跨平台字体族：Windows Segoe UI / macOS Helvetica Neue / Linux Noto Sans（缺失时 Tk 会自动回退）"""
    if os.name == "nt":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return "Noto Sans"


FONT = _font_family()


# ---- 清晰矢量小图标（Canvas 原生几何，取代文本字形 ◆ ● ▶ 等，避免小字发糊/被替换为 emoji）----

def _make_icon(parent, size, kind, color, bg):
    """绘制 size×size 的矢量图标，返回带 `_paint_icon(color)` 可改色重绘的 Canvas。

    仅在无法用统一组件替代时用于高频/状态类图形（品牌菱形、导航、状态点）；
    按钮采用纯文字 + 主题配色，不引入 emoji / 任意 Unicode 图标字形。
    """
    c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)

    def paint(col):
        c.delete("all")
        s = float(size)
        if kind == "diamond":                                # 品牌菱形
            c.create_polygon(s*0.50, s*0.06, s*0.94, s*0.50, s*0.50, s*0.94, s*0.06, s*0.50,
                             fill=col, outline="")
        elif kind == "dot":                                  # 状态点（圆）
            c.create_oval(s*0.16, s*0.16, s*0.84, s*0.84, fill=col, outline="")
        elif kind == "dashboard":                            # 仪表盘：2×2 方块
            g = max(1, s*0.15); cell = (s - 3*g) / 2
            for i in (0, 1):
                for j in (0, 1):
                    x, y = g + i*(cell+g), g + j*(cell+g)
                    c.create_rectangle(x, y, x+cell, y+cell, fill=col, outline="")
        elif kind == "server":                               # 服务器控制：播放三角
            c.create_polygon(s*0.24, s*0.14, s*0.86, s*0.50, s*0.24, s*0.86, fill=col, outline="")
        elif kind == "log":                                  # 运行日志：三行
            lw = max(1, int(s*0.09)); g = max(1, s*0.24)
            for i in range(3):
                c.create_line(g, g*(i+0.5), s-g, g*(i+0.5), fill=col, width=lw)
        return c

    paint(color)
    c._paint_icon = paint
    return c


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


# ---- 仪表盘数据采集（只读，不写生产库）----

def _fetch_health() -> str:
    """服务健康：向 /api/v1/system/health 发一次只读请求；服务器未开则显示 —"""
    if not _check_alive():
        return "—"
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8000/api/v1/system/health", timeout=2) as r:
            return "正常" if r.status == 200 else "异常"
    except Exception:
        return "异常"


def _read_db_stats() -> dict:
    """只读读取 SQLite 统计：角色数 / 记忆数 / 累计 Token（mode=ro，不锁库不写数据；失败显示 —）"""
    stats = {"characters": None, "memories": None, "tokens": None}
    db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return stats
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return stats
    try:
        queries = {
            "characters": "SELECT COUNT(*) FROM ai_characters",
            "memories": "SELECT COUNT(*) FROM memories",
            "tokens": "SELECT COALESCE(SUM(total_tokens),0) FROM llm_usage",
        }
        for key, sql in queries.items():
            try:
                stats[key] = con.execute(sql).fetchone()[0]
            except Exception:
                pass
    finally:
        try:
            con.close()
        except Exception:
            pass
    return stats


def _fmt_int(v) -> str:
    """数字千分位格式化；空/异常显示 —"""
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return "—"


class ControllerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: "queue.Queue[tuple]" = queue.Queue()
        root.title("拥爱服务器控制台")  # 2026-08-18 用户拍板：去掉括号英文
        root.geometry("1000x640")  # 桌面软件观感：更宽，给侧边栏 + 仪表盘留空间
        root.minsize(840, 560)
        root.configure(bg=COL_BG)
        self._apply_window_icon()
        self._build_theme()

        # 布局：状态栏贴底 → 顶部状态栏 → 主体（侧边栏 + 内容）
        self._build_statusbar()
        self._build_header()
        self._build_body()

        self._last_log = ""
        self._last_state = None
        self._ollama_busy = False
        self._busy = False
        self._refreshing = False
        self._nav = "dashboard"

        self._select_page("dashboard")
        self._poll()          # 主线程轮询队列（唯一跨线程通信通道）
        self._do_refresh()    # 立即刷新一次
        self._schedule_refresh()

    def _apply_window_icon(self) -> None:
        """控制台窗口图标（品牌统一，2026-08-18）：Windows 优先 .ico（任务栏缩放清晰），
        其他平台/失败回退 PNG iconphoto（tk 8.6+ 支持 PNG）；图标文件缺失时静默跳过。
        """
        try:
            _ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ambrace.ico")
            if os.name == "nt" and os.path.isfile(_ico):
                self.root.iconbitmap(_ico)
        except Exception:
            pass
        try:
            _png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
            if os.path.isfile(_png):
                _img = tk.PhotoImage(file=_png)
                self.root.iconphoto(True, _img)
                self._icon_img = _img  # 保持引用防 GC
        except Exception:
            pass

    def _build_theme(self) -> None:
        """统一 ttk 主题（clam 保证跨平台可控配色）"""
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=COL_BG)
        style.configure("TCard.TFrame", background=COL_CARD)
        style.configure("TLabel", background=COL_BG, foreground=COL_TEXT, font=(FONT, 12))
        style.configure("CardTitle.TLabel", background=COL_CARD, foreground=COL_TEXT_SEC, font=(FONT, 12))
        style.configure(
            "Game.TButton", background=COL_ACCENT, foreground="#FFFFFF",
            borderwidth=0, focusthickness=0, padding=(SP_MD, SP_XS), font=(FONT, 12, "bold"),
        )
        style.map("Game.TButton",
                  background=[("active", "#0051A9"), ("pressed", "#004C9E"), ("disabled", COL_SURFACE_ALT)])
        style.configure(
            "Neutral.TButton", background=COL_CARD, foreground=COL_TEXT,
            borderwidth=1, relief="solid", bordercolor=COL_HAIRLINE,
            focusthickness=0, padding=(SP_MD, SP_XS), font=(FONT, 12),
        )
        style.map("Neutral.TButton",
                  background=[("active", COL_SURFACE_ALT), ("pressed", COL_SURFACE_ALT), ("disabled", COL_BG)])
        style.configure(
            "Danger.TButton", background=COL_CARD, foreground=COL_ERROR,
            borderwidth=1, relief="solid", bordercolor=COL_ERROR,
            focusthickness=0, padding=(SP_MD, SP_XS), font=(FONT, 12, "bold"),
        )
        style.map("Danger.TButton",
                  background=[("active", "#FBE9E7"), ("pressed", "#FBE9E7"), ("disabled", COL_BG)])
        style.configure("TCheckbutton", background=COL_CARD, foreground=COL_TEXT, font=(FONT, 12))
        style.map("TCheckbutton", background=[("active", COL_CARD)])
        style.configure("TEntry", fieldbackground="#FFFFFF", foreground=COL_TEXT,
                        bordercolor=COL_HAIRLINE, lightcolor=COL_HAIRLINE, darkcolor=COL_HAIRLINE,
                        insertcolor=COL_TEXT)

    # ── 顶部状态栏（品牌 + 服务器状态/端口/健康 + 设置入口）──
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=COL_CARD)
        header.pack(side="top", fill="x")

        brand = tk.Frame(header, bg=COL_CARD)
        brand.pack(side="left", padx=(SP_LG, 0), pady=SP_SM)
        _make_icon(brand, 18, "diamond", COL_ACCENT, COL_CARD).pack(side="left")
        tk.Label(brand, text="拥爱服务器控制台", fg=COL_TEXT, bg=COL_CARD,
                 font=(FONT, 15, "bold")).pack(side="left", padx=(SP_XS, 0))

        right = tk.Frame(header, bg=COL_CARD)
        right.pack(side="right", padx=SP_LG, pady=SP_SM)
        self.header_dot = _make_icon(right, 12, "dot", COL_TEXT_SEC, COL_CARD)
        self.header_dot.pack(side="left")
        self.header_status_label = tk.Label(right, text="检测中…", fg=COL_TEXT, bg=COL_CARD, font=(FONT, 12, "bold"))
        self.header_status_label.pack(side="left", padx=(SP_XS, SP_SM))
        self.header_port_label = tk.Label(right, text="端口 8000", fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 12))
        self.header_port_label.pack(side="left", padx=(0, SP_SM))
        self.header_health_label = tk.Label(right, text="健康 —", fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 12))
        self.header_health_label.pack(side="left", padx=(0, SP_MD))
        tk.Button(right, text="设置", bg=COL_CARD, fg=COL_ACCENT, activebackground=COL_CARD,
                  activeforeground=COL_ACCENT, relief="flat", bd=0, font=(FONT, 12, "bold"),
                  cursor="hand2", command=self.open_settings).pack(side="left")

        tk.Frame(self.root, height=1, bg=COL_DIVIDER).pack(side="top", fill="x")

    # ── 底栏（操作信息 + 刷新间隔）──
    def _build_statusbar(self) -> None:
        bar = tk.Frame(self.root, bg=COL_CARD, height=30)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, width=1, bg=COL_DIVIDER).pack(side="left", fill="y")
        self.msg = tk.Label(bar, text="就绪", fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 11))
        self.msg.pack(side="left", padx=SP_MD)
        self.refresh_label = tk.Label(bar, text=f"刷新间隔 {_get_refresh_ms() // 1000}s",
                                      fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 11))
        self.refresh_label.pack(side="right", padx=SP_LG)

    # ── 主体：侧边栏 + 内容页 ──
    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=COL_BG)
        body.pack(side="top", fill="both", expand=True)

        self._sidebar = tk.Frame(body, bg=COL_SIDEBAR, width=208)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        # 侧边栏右缘细线：与主内容区形成清晰分界，避免同底色下导航区被淹没
        tk.Frame(self._sidebar, width=1, bg=COL_HAIRLINE).pack(side="right", fill="y")

        self._content = tk.Frame(body, bg=COL_BG)
        self._content.pack(side="left", fill="both", expand=True)

        self._nav_widgets = {}
        nav_items = [
            ("dashboard", "仪表盘", "dashboard"),
            ("server", "服务器控制", "server"),
            ("log", "运行日志", "log"),
        ]
        for key, label, icon in nav_items:
            item = tk.Frame(self._sidebar, bg=COL_SIDEBAR, cursor="hand2")
            item.pack(fill="x", padx=SP_SM, pady=3)
            ic = _make_icon(item, 16, icon, COL_TEXT_SEC, COL_SIDEBAR)
            ic.pack(side="left", padx=(SP_MD, SP_SM), pady=SP_SM)
            lb = tk.Label(item, text=label, bg=COL_SIDEBAR, fg=COL_TEXT_SEC, font=(FONT, 13))
            lb.pack(side="left", pady=SP_SM)
            for wid in (item, ic, lb):
                wid.bind("<Button-1>", lambda e, k=key: self._select_page(k))
                wid.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                wid.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self._nav_widgets[key] = (item, ic, lb)

        self._pages = {
            "dashboard": self._build_dashboard_page(),
            "server": self._build_server_page(),
            "log": self._build_log_page(),
        }

    def _select_page(self, key: str) -> None:
        for k in self._pages:
            self._pages[k].pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for k, (item, ic, lb) in self._nav_widgets.items():
            if k == key:
                item.config(bg=COL_CARD, highlightthickness=1,
                            highlightbackground=COL_HAIRLINE, highlightcolor=COL_HAIRLINE)
                ic.config(bg=COL_CARD)
                ic._paint_icon(COL_ACCENT)
                lb.config(bg=COL_CARD, fg=COL_ACCENT, font=(FONT, 13, "bold"))
            else:
                item.config(bg=COL_SIDEBAR, highlightthickness=0)
                ic.config(bg=COL_SIDEBAR)
                ic._paint_icon(COL_TEXT_SEC)
                lb.config(bg=COL_SIDEBAR, fg=COL_TEXT_SEC, font=(FONT, 13))
        self._nav = key

    def _nav_hover(self, key: str, entering: bool) -> None:
        """导航项悬停态：已选中项不响应，未选中项轻微提亮以增强“可点”手感。"""
        if key == self._nav:
            return
        item, ic, lb = self._nav_widgets[key]
        if entering:
            item.config(bg=COL_SURFACE_ALT)
            ic.config(bg=COL_SURFACE_ALT)
            lb.config(bg=COL_SURFACE_ALT, fg=COL_TEXT)
        else:
            item.config(bg=COL_SIDEBAR)
            ic.config(bg=COL_SIDEBAR)
            lb.config(bg=COL_SIDEBAR, fg=COL_TEXT_SEC)

    # ── 卡片（标题 + 主值 + 副标题，内容垂直居中，避免“头重/大量留白”） ──
    def _make_card(self, parent, row, col, title, caption=""):
        card = tk.Frame(parent, bg=COL_CARD, highlightthickness=1, highlightbackground=COL_HAIRLINE,
                        highlightcolor=COL_HAIRLINE, bd=0)
        card.grid(row=row, column=col, sticky="nsew", padx=SP_XS, pady=SP_XS)
        inner = tk.Frame(card, bg=COL_CARD)
        inner.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        tk.Frame(inner, bg=COL_CARD).pack(fill="both", expand=True)   # 顶部弹性占位（垂直居中）
        tk.Label(inner, text=title, fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 12)).pack(anchor="w")
        value = tk.Label(inner, text="…", fg=COL_TEXT, bg=COL_CARD, font=(FONT, 22, "bold"))
        value.pack(anchor="w", pady=(SP_XS, 2))
        cap = tk.Label(inner, text=caption, fg=COL_TEXT_MUTED, bg=COL_CARD, font=(FONT, 11))
        cap.pack(anchor="w")
        tk.Frame(inner, bg=COL_CARD).pack(fill="both", expand=True)   # 底部弹性占位（垂直居中）
        return card, value, cap

    # ── 仪表盘页 ──
    def _build_dashboard_page(self) -> tk.Frame:
        page = tk.Frame(self._content, bg=COL_BG)
        pad = tk.Frame(page, bg=COL_BG)
        pad.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        tk.Label(pad, text="服务器概览", fg=COL_TEXT, bg=COL_BG, font=(FONT, 17, "bold")).pack(anchor="w")
        tk.Label(pad, text="运行状态、健康与数据规模一目了然", fg=COL_TEXT_SEC, bg=COL_BG,
                 font=(FONT, 12)).pack(anchor="w", pady=(2, SP_MD))

        grid = tk.Frame(pad, bg=COL_BG)
        grid.pack(fill="both", expand=True)
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="card")
        grid.rowconfigure(0, weight=1)
        grid.rowconfigure(1, weight=1)

        _, self.v_server, self.c_server = self._make_card(grid, 0, 0, "服务器状态")
        _, self.v_health, self.c_health = self._make_card(grid, 0, 1, "服务健康", "HTTP 探活")
        _, self.v_ollama, self.c_ollama = self._make_card(grid, 0, 2, OLLAMA_LABEL)
        _, self.v_chars, self.c_chars = self._make_card(grid, 1, 0, "角色数", "个角色")
        _, self.v_mems, self.c_mems = self._make_card(grid, 1, 1, "记忆数", "条记忆")
        _, self.v_tokens, self.c_tokens = self._make_card(grid, 1, 2, "累计 Token", "Token 累计")

        # 快捷操作（主题化按钮，不引入图标字形）
        actions = tk.Frame(pad, bg=COL_BG)
        actions.pack(fill="x", pady=(SP_LG, 0))
        self._dash_btn_start = ttk.Button(actions, text="启动服务器", style="Game.TButton", command=self.start_server)
        self._dash_btn_start.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_stop = ttk.Button(actions, text="停止服务器", style="Danger.TButton", command=self.stop_server)
        self._dash_btn_stop.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_restart = ttk.Button(actions, text="重启服务器", style="Neutral.TButton", command=self.restart_server)
        self._dash_btn_restart.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_log = ttk.Button(actions, text="打开日志文件", style="Neutral.TButton", command=self.open_log)
        self._dash_btn_log.pack(side="left", padx=(0, SP_XS))
        return page

    # ── 服务器控制页 ──
    def _build_server_page(self) -> tk.Frame:
        page = tk.Frame(self._content, bg=COL_BG)
        pad = tk.Frame(page, bg=COL_BG)
        pad.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)

        tk.Label(pad, text="服务器控制", fg=COL_TEXT, bg=COL_BG, font=(FONT, 17, "bold")).pack(anchor="w")
        tk.Label(pad, text="启动 / 停止 / 重启核心服务", fg=COL_TEXT_SEC, bg=COL_BG, font=(FONT, 12)).pack(anchor="w", pady=(2, SP_MD))

        card = tk.Frame(pad, bg=COL_CARD, highlightthickness=1, highlightbackground=COL_HAIRLINE, bd=0)
        card.pack(fill="x", pady=(0, SP_MD))
        inner = tk.Frame(card, bg=COL_CARD)
        inner.pack(fill="x", padx=SP_LG, pady=SP_LG)
        self.btn_start = ttk.Button(inner, text="启动服务器", style="Game.TButton", command=self.start_server)
        self.btn_start.pack(side="left", padx=(0, SP_XS))
        self.btn_stop = ttk.Button(inner, text="停止服务器", style="Danger.TButton", command=self.stop_server)
        self.btn_stop.pack(side="left", padx=(0, SP_XS))
        self.btn_restart = ttk.Button(inner, text="重启服务器", style="Neutral.TButton", command=self.restart_server)
        self.btn_restart.pack(side="left")
        self.server_pid_label = tk.Label(inner, text="", fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 12))
        self.server_pid_label.pack(side="left", padx=SP_MD)

        # Ollama 区块
        ollama_card = tk.Frame(pad, bg=COL_CARD, highlightthickness=1, highlightbackground=COL_HAIRLINE, bd=0)
        ollama_card.pack(fill="x")
        oin = tk.Frame(ollama_card, bg=COL_CARD)
        oin.pack(fill="x", padx=SP_LG, pady=SP_LG)
        self.ollama_dot = _make_icon(oin, 14, "dot", COL_TEXT_SEC, COL_CARD)
        self.ollama_dot.pack(side="left")
        tk.Label(oin, text=OLLAMA_LABEL, fg=COL_TEXT, bg=COL_CARD, font=(FONT, 13, "bold")).pack(side="left", padx=(SP_XS, 0))
        self.ollama_pid_label = tk.Label(oin, text="", fg=COL_TEXT_SEC, bg=COL_CARD, font=(FONT, 12))
        self.ollama_pid_label.pack(side="left", padx=(SP_XS, 0))
        self.btn_ollama_stop = ttk.Button(oin, text="停止 Ollama", style="Danger.TButton", command=self.stop_ollama)
        self.btn_ollama_stop.pack(side="right", padx=(0, SP_XS))
        self.btn_ollama_start = ttk.Button(oin, text="启动 Ollama", style="Game.TButton", command=self.start_ollama)
        self.btn_ollama_start.pack(side="right")

        vram_row = tk.Frame(ollama_card, bg=COL_CARD)
        vram_row.pack(fill="x", padx=SP_LG, pady=(0, SP_LG))
        self._ollama_low_vram = _load_low_vram()
        self.low_vram_var = tk.BooleanVar(value=self._ollama_low_vram)
        self.chk_low_vram = ttk.Checkbutton(
            vram_row, text="省显存模式（语言层走 CPU，显存 4.4→2.4GB，玩大型游戏时开启）",
            variable=self.low_vram_var, command=self.toggle_low_vram)
        self.chk_low_vram.pack(side="left")
        return page

    # ── 运行日志页 ──
    def _build_log_page(self) -> tk.Frame:
        page = tk.Frame(self._content, bg=COL_BG)
        pad = tk.Frame(page, bg=COL_BG)
        pad.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        head = tk.Frame(pad, bg=COL_BG)
        head.pack(fill="x", pady=(0, SP_SM))
        tk.Label(head, text="运行日志", fg=COL_TEXT, bg=COL_BG, font=(FONT, 17, "bold")).pack(side="left")
        self.log_open_btn = ttk.Button(head, text="打开日志文件", style="Neutral.TButton", command=self.open_log)
        self.log_open_btn.pack(side="right")

        self.log_box = scrolledtext.ScrolledText(pad, height=18, state="disabled",
                                                 bg="#FFFFFF", fg=COL_TEXT, insertbackground=COL_TEXT,
                                                 font=("Consolas", 10), relief="flat", bd=0,
                                                 highlightthickness=1, highlightbackground=COL_HAIRLINE,
                                                 highlightcolor=COL_HAIRLINE)
        self.log_box.pack(fill="both", expand=True)
        return page

    def _set_msg(self, text: str):
        self.msg.config(text=text)

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for b in (self.btn_start, self.btn_stop, self.btn_restart,
                  self._dash_btn_start, self._dash_btn_stop, self._dash_btn_restart):
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
                    _, alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats = item
                    self._apply_refresh(alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats)
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
        win.configure(bg=COL_BG)

        frame = tk.Frame(win, bg=COL_BG, padx=SP_LG, pady=SP_LG)
        frame.pack(fill="both", expand=True)

        tk.Label(frame, text="守护检测间隔（秒）", bg=COL_BG, fg=COL_TEXT, font=(FONT, 12)).grid(row=0, column=0, sticky="w", pady=(0, SP_XS))
        wd_var = tk.StringVar(value=str(cfg.get("watchdog_interval_sec", 120)))
        ttk.Entry(frame, textvariable=wd_var, width=14).grid(row=0, column=1, sticky="w", pady=(0, SP_XS))

        tk.Label(frame, text="界面刷新间隔（秒）", bg=COL_BG, fg=COL_TEXT, font=(FONT, 12)).grid(row=1, column=0, sticky="w", pady=(0, SP_MD))
        rf_var = tk.StringVar(value=str(int(cfg.get("controller_refresh_ms", 60000)) // 1000))
        ttk.Entry(frame, textvariable=rf_var, width=14).grid(row=1, column=1, sticky="w", pady=(0, SP_MD))

        tk.Label(frame, text="修改后立即生效：\n守护间隔下个检测周期生效，界面刷新即时生效。",
                 bg=COL_BG, fg=COL_TEXT_SEC, font=(FONT, 10)).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, SP_MD))

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
                self.refresh_label.config(text=f"刷新间隔 {rf}s")
                self._do_refresh()
            except ValueError as e:
                tk.messagebox.showerror("输入无效", str(e), parent=win)
            except Exception as e:
                tk.messagebox.showerror("保存失败", str(e), parent=win)

        ttk.Button(frame, text="保存", style="Game.TButton", command=save).grid(row=3, column=0, sticky="w")
        ttk.Button(frame, text="取消", style="Neutral.TButton", command=win.destroy).grid(row=3, column=1, sticky="w")

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
                health = _fetch_health()
                stats = _read_db_stats()
                self._q.put(("state", alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats))
            except Exception:
                pass
            finally:
                self._refreshing = False

        threading.Thread(target=job, daemon=True).start()

    # ── 应用刷新结果到各页面 ──
    def _update_log(self, log_text: str):
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", log_text)
        self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _apply_refresh(self, alive: bool, pid: int, paused: bool, log_text: str,
                       ollama_alive: bool, ollama_pid: int, health: str, stats: dict):
        # Ollama 状态：每次刷新更新（含 PID 变化），不依赖服务器状态
        if ollama_alive:
            self.ollama_dot._paint_icon(COL_SUCCESS)
            self.ollama_pid_label.config(text="运行中  (PID %d)" % ollama_pid)
            self.v_ollama.config(text="运行中", fg=COL_SUCCESS)
            self.c_ollama.config(text="PID %d" % ollama_pid)
        else:
            self.ollama_dot._paint_icon(COL_ERROR)
            self.ollama_pid_label.config(text="未运行")
            self.v_ollama.config(text="未运行", fg=COL_TEXT_SEC)
            self.c_ollama.config(text="端口 11434")

        # 顶部状态栏 + 仪表盘服务器卡
        if alive:
            self.header_dot._paint_icon(COL_SUCCESS)
            self.header_status_label.config(text="运行中")
            self.v_server.config(text="运行中", fg=COL_SUCCESS)
            self.c_server.config(text="PID %d · 端口 8000" % pid)
            self.server_pid_label.config(text="PID %d" % pid)
        else:
            self.header_dot._paint_icon(COL_ERROR)
            self.header_status_label.config(text="已停止")
            self.v_server.config(text="已停止", fg=COL_ERROR)
            self.c_server.config(text="端口 8000")
            self.server_pid_label.config(text="")
        self.header_port_label.config(text="端口 8000")
        self.header_health_label.config(text="健康 %s" % health,
                                        fg=COL_SUCCESS if health == "正常" else (COL_WARNING if health == "—" else COL_ERROR))
        self.v_health.config(text=health, fg=COL_SUCCESS if health == "正常" else (COL_WARNING if health == "—" else COL_ERROR))
        self.v_chars.config(text=_fmt_int(stats.get("characters")))
        self.v_mems.config(text=_fmt_int(stats.get("memories")))
        self.v_tokens.config(text=_fmt_int(stats.get("tokens")))

        # 守护暂停提示
        self._set_msg("守护已暂停（watchdog 不自动拉起）" if paused else "就绪")

        # 日志：仅在内容变化时更新（减少闪烁）
        if log_text != self._last_log:
            self._last_log = log_text
            self._update_log(log_text)


def main():
    root = tk.Tk()
    ControllerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
