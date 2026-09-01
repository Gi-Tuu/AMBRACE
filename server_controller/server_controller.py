"""拥爱（AMBRACE）服务器控制台 — 启动/停止/重启服务器（与 watchdog 协调，全程无终端弹窗）
UI v4（2026-08-28）：多主题（暗色/亮色）+ 圆润柔和设计 + 高 DPI 修复。
所有既有功能/接口调用保持不变；仪表盘统计经只读方式读取，不改生产数据。
"""
import ctypes
import json
import math
import os
import queue
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog


def _safe_traceback():
    """pythonw 下 sys.stderr 可能为 None，traceback.print_exc 会二次崩溃；安全降级。"""
    try:
        import traceback
        traceback.print_exc()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# DPI / 窗口
# ═══════════════════════════════════════════════════════════════

def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_titlebar_theme(hwnd, dark: bool):
    """Windows 10/11 标题栏明暗（DWMWA_USE_IMMERSIVE_DARK_MODE=20）。"""
    try:
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(ctypes.c_int(1 if dark else 0)), ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 路径 / 常量
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_DIR = os.path.join(PROJECT_ROOT, "backend")
_VENV_BIN = "Scripts" if os.name == "nt" else "bin"
PYTHONW = os.path.join(SERVER_DIR, ".venv", _VENV_BIN, "pythonw.exe" if os.name == "nt" else "python")
PAUSE_FLAG = os.path.join(SERVER_DIR, "data", "paused.flag")
APP_LOG = os.path.join(SERVER_DIR, "data", "logs", "app.log")
STDERR_LOG = os.path.join(SERVER_DIR, "data", "logs", "server_stderr.log")
PYTHON = os.path.join(SERVER_DIR, ".venv", _VENV_BIN, "python.exe" if os.name == "nt" else "python")
MANAGER_PY = os.path.join(PROJECT_ROOT, "scripts", "server_manager.py")
CONFIG = os.path.join(SERVER_DIR, "data", "server_config.json")
DEFAULT_REFRESH_MS = 20000
CATCHUP_MS = 2000
CATCHUP_N = 10
LOG_FOLLOW_MS = 3000
POLL_MS = 200
LOG_TAIL = 40
OLLAMA_PORT = 11434
OLLAMA_LABEL = "图像理解服务（Ollama）"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


# ═══════════════════════════════════════════════════════════════
# 主题
# ═══════════════════════════════════════════════════════════════

class Theme:
    """一套完整的配色 token。所有 UI 颜色均从此读取，切换主题只需换实例。"""
    def __init__(self, name: str, label: str, dark: bool, c: dict):
        self.name = name
        self.label = label
        self.dark = dark
        for k, v in c.items():
            setattr(self, k, v)


THEMES = {
    "aurora": Theme("aurora", "极光", True, {
        "bg":             "#05070F",
        "sidebar":        "#070B16",
        "card":           "#0D1322",
        "card_hover":     "#121A2E",
        "divider":        "#101726",
        "hairline":       "#1B2438",
        "surface_alt":    "#0F1626",
        "text":           "#EAF0F9",
        "text_sec":       "#9AA7BD",
        "text_muted":     "#67728A",
        "accent":         "#5EEAD4",
        "accent_dim":     "#0E2B29",
        "accent_glow":    "#7CF0DD",
        "success":        "#34D399",
        "warning":        "#FBBF24",
        "error":          "#F87171",
        "log_bg":         "#070B12",
        "log_fg":         "#C9D1D9",
        "card_topline":   "#14213A",
        "btn_neutral_bg": "#131C33",
        "btn_neutral_fg": "#D5DCEA",
        "btn_neutral_hv": "#1A2542",
        "btn_danger_bg":  "#3B1818",
        "btn_danger_fg":  "#FCA5A5",
        "btn_danger_hv":  "#4C1F1F",
        "entry_bg":       "#0B1120",
        "pulse_hi":       "#5EEAD4",
        "pulse_lo":       "#0E3B36",
        "radius":         16,
    }),
    "dark": Theme("dark", "暗色", True, {
        "bg":             "#0F1117",
        "sidebar":        "#12141B",
        "card":           "#181B24",
        "card_hover":     "#1F2330",
        "divider":        "#1E2230",
        "hairline":       "#282D3E",
        "surface_alt":    "#1C2030",
        "text":           "#E8EAF0",
        "text_sec":       "#8B90A0",
        "text_muted":     "#5A6072",
        "accent":         "#3B82F6",
        "accent_dim":     "#1E3A5F",
        "accent_glow":    "#60A5FA",
        "success":        "#34D399",
        "warning":        "#FBBF24",
        "error":          "#F87171",
        "log_bg":         "#0B0D12",
        "log_fg":         "#C9D1D9",
        "card_topline":   "#252A38",
        "btn_neutral_bg": "#222636",
        "btn_neutral_fg": "#D8DCE8",
        "btn_neutral_hv": "#2C3148",
        "btn_danger_bg":  "#3B1818",
        "btn_danger_fg":  "#FCA5A5",
        "btn_danger_hv":  "#4C1F1F",
        "entry_bg":       "#1A1D28",
        "pulse_hi":       "#34D399",
        "pulse_lo":       "#0F3D2E",
        "radius":         14,
    }),
    "light": Theme("light", "亮色", False, {
        "bg":             "#F0F0F5",
        "sidebar":        "#FFFFFF",
        "card":           "#FFFFFF",
        "card_hover":     "#F5F5FA",
        "divider":        "#E5E5EC",
        "hairline":       "#D8D8E0",
        "surface_alt":    "#EDEDF3",
        "text":           "#1C1C1E",
        "text_sec":       "#6B7080",
        "text_muted":     "#9A9EAA",
        "accent":         "#0071E3",
        "accent_dim":    "#E8F1FE",
        "accent_glow":    "#0077ED",
        "success":        "#2EA043",
        "warning":        "#B25000",
        "error":          "#D72638",
        "log_bg":         "#FAFAFC",
        "log_fg":         "#2C2C2E",
        "card_topline":   "#F0F0F5",
        "btn_neutral_bg": "#F0F0F5",
        "btn_neutral_fg": "#3C3C43",
        "btn_neutral_hv": "#E5E5EC",
        "btn_danger_bg":  "#FFF5F5",
        "btn_danger_fg":  "#D72638",
        "btn_danger_hv":  "#FFE8E8",
        "entry_bg":       "#FFFFFF",
        "pulse_hi":       "#2EA043",
        "pulse_lo":       "#DCEFE2",
        "radius":         14,
    }),
}

SP_XXS, SP_XS, SP_SM, SP_MD, SP_LG, SP_XL = 4, 8, 12, 16, 24, 32


def _font_family() -> str:
    if os.name == "nt":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "Helvetica Neue"
    return "Noto Sans"


FONT = _font_family()


# ═══════════════════════════════════════════════════════════════
# 圆润组件
# ═══════════════════════════════════════════════════════════════

def _rr_points(x1, y1, x2, y2, r):
    """返回 smooth=True 时近似圆角矩形的多边形点列。"""
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundedCard(tk.Canvas):
    """圆角卡片：Canvas 绘制圆角底 + 内嵌 Frame（inner）承载子控件。"""
    def __init__(self, parent, theme: Theme, radius=None, pad=4, fit_inner=False, **kw):
        self.theme = theme
        self.radius = radius or theme.radius
        self.pad = pad
        self.fit_inner = fit_inner
        super().__init__(parent, bg=theme.bg, highlightthickness=0, bd=0, **kw)
        self.inner = tk.Frame(self, bg=theme.card)
        self._win = None
        self.bind("<Configure>", self._on_cfg)
        if fit_inner:
            # 不钉死卡片尺寸时随 inner 自然需求尺寸生长（与 DPI 缩放无关），
            # 避免固定高度不足时 Tk packer 把后续子控件判为装不下而取消映射
            self.inner.bind("<Configure>", lambda e: self._fit_to_inner())

    def _fit_to_inner(self):
        if not self.fit_inner:
            return
        w = self.inner.winfo_reqwidth() + 2 * self.pad
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if w > 1 and (self.winfo_reqwidth() != w or self.winfo_reqheight() != h):
            self.config(width=w, height=h)

    def _on_cfg(self, e):
        self.delete("bg")
        w, h = e.width, e.height
        if w < 10 or h < 10:
            return
        pts = _rr_points(1, 1, w - 1, h - 1, self.radius)
        self.create_polygon(pts, smooth=True, splinesteps=24,
                            fill=self.theme.card, outline=self.theme.hairline, width=1, tags="bg")
        self.tag_lower("bg")
        iw, ih = w - 2 * self.pad, h - 2 * self.pad
        if self._win is None:
            self._win = self.create_window(w // 2, h // 2, window=self.inner,
                                           width=iw, height=ih, anchor="center")
        else:
            self.coords(self._win, w // 2, h // 2)
            self.itemconfig(self._win, width=iw, height=ih)


class RoundedButton(tk.Canvas):
    """圆角按钮：Canvas 绘制，支持 hover/press/disabled 三态。"""
    def __init__(self, parent, theme: Theme, text, command=None,
                 variant="primary", width=None, height=36, font_size=11, bold=True):
        self.theme = theme
        self.command = command
        self.variant = variant
        self._enabled = True
        self._hover = False
        self._press = False
        self._text = text
        self._h = height
        self._fs = font_size
        self._bold = bold
        # 根据文字自动计算宽度
        if width is None:
            import tkinter.font as tkfont
            fn = tkfont.Font(family=FONT, size=font_size, weight="bold" if bold else "normal")
            width = fn.measure(text) + 32  # 左右各 16px 内边距
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, height=height,
                         width=width)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _colors(self):
        t = self.theme
        if not self._enabled:
            return t.surface_alt, t.text_muted, t.surface_alt
        if self.variant == "primary":
            bg = t.accent_glow if (self._hover or self._press) else t.accent
            return bg, "#FFFFFF", bg
        if self.variant == "danger":
            bg = t.btn_danger_hv if (self._hover or self._press) else t.btn_danger_bg
            return bg, t.btn_danger_fg, bg
        # neutral
        bg = t.btn_neutral_hv if (self._hover or self._press) else t.btn_neutral_bg
        return bg, t.btn_neutral_fg, bg

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            w = 120
        bg, fg, _ = self._colors()
        r = min(10, h // 2)
        pts = _rr_points(1, 1, w - 1, h - 1, r)
        self.create_polygon(pts, smooth=True, splinesteps=20, fill=bg, outline="", tags="btn")
        fn = (FONT, self._fs, "bold") if self._bold else (FONT, self._fs)
        self.create_text(w // 2, h // 2, text=self._text, fill=fg, font=fn, tags="btn")

    def _on_enter(self, e):
        self._hover = True
        self._draw()

    def _on_leave(self, e):
        self._hover = False
        self._press = False
        self._draw()

    def _on_press(self, e):
        if self._enabled:
            self._press = True
            self._draw()

    def _on_release(self, e):
        if self._enabled and self._press:
            self._press = False
            self._draw()
            if self.command:
                self.command()

    def config_state(self, enabled: bool):
        self._enabled = enabled
        self._draw()

    # 兼容 ttk.Button 的 state() 调用
    def state(self, states=None):
        if states is None:
            return ("disabled",) if not self._enabled else ("!disabled",)
        if "disabled" in states:
            self._enabled = False
        elif "!disabled" in states or "normal" in states:
            self._enabled = True
        self._draw()


class Segmented(tk.Canvas):
    """分段选择器（每日/每周/累计）：胶囊底 + 高亮滑块，配色随当前 Theme。"""
    def __init__(self, parent, theme: "Theme", options, callback, width=156, height=26):
        self.theme = theme
        self.opts = options
        self.callback = callback
        self.idx = 0
        self._w0, self._h = width, height
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0,
                         width=width, height=height)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)

    def set_index(self, i: int, fire: bool = True):
        i = max(0, min(len(self.opts) - 1, i))
        if i == self.idx:
            return
        self.idx = i
        self._draw()
        if fire and self.callback:
            self.callback(self.opts[i][0])

    def _click(self, e):
        seg = self.winfo_width() / len(self.opts)
        self.set_index(int(e.x // seg))

    def _draw(self):
        self.delete("all")
        t = self.theme
        w, h = self.winfo_width(), self._h
        if w < 10:
            w = self._w0
        n = len(self.opts)
        seg = w / n
        self.create_polygon(_rr_points(1, 1, w - 1, h - 1, h / 2), smooth=True,
                            splinesteps=20, fill=t.btn_neutral_bg, outline="")
        for i, (_, label) in enumerate(self.opts):
            x0, x1 = i * seg, (i + 1) * seg
            if i == self.idx:
                self.create_polygon(_rr_points(x0 + 2, 2, x1 - 2, h - 2, (h - 4) / 2),
                                    smooth=True, splinesteps=20, fill=t.accent, outline="")
                fg = "#FFFFFF"
            else:
                fg = t.text_sec
            self.create_text((x0 + x1) / 2, h / 2, text=label, fill=fg,
                             font=(FONT, 9, "bold" if i == self.idx else "normal"))


# ═══════════════════════════════════════════════════════════════
# 矢量图标
# ═══════════════════════════════════════════════════════════════

def _make_icon(parent, size, kind, color, bg):
    c = tk.Canvas(parent, width=size, height=size, bg=bg, highlightthickness=0, bd=0)

    def paint(col):
        c.delete("all")
        s = float(size)
        if kind == "diamond":
            c.create_polygon(s*0.50, s*0.06, s*0.94, s*0.50, s*0.50, s*0.94, s*0.06, s*0.50,
                             fill=col, outline="")
        elif kind == "dot":
            c.create_oval(s*0.16, s*0.16, s*0.84, s*0.84, fill=col, outline="")
        elif kind == "dashboard":
            g = max(1, s*0.15); cell = (s - 3*g) / 2
            for i in (0, 1):
                for j in (0, 1):
                    x, y = g + i*(cell+g), g + j*(cell+g)
                    c.create_rectangle(x, y, x+cell, y+cell, fill=col, outline="")
        elif kind == "server":
            c.create_polygon(s*0.24, s*0.14, s*0.86, s*0.50, s*0.24, s*0.86, fill=col, outline="")
        elif kind == "log":
            lw = max(1, int(s*0.09)); g = max(1, s*0.24)
            for i in range(3):
                c.create_line(g, g*(i+0.5), s-g, g*(i+0.5), fill=col, width=lw)
        return c

    paint(color)
    c._paint_icon = paint
    return c


# ═══════════════════════════════════════════════════════════════
# 业务逻辑（进程管理 / 探活 / 数据采集，与 UI 无关）
# ═══════════════════════════════════════════════════════════════

def _load_config() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_ollama() -> str:
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
    for cand in (_load_config().get("ollama_models_dir"), os.environ.get("OLLAMA_MODELS"), ""):
        if cand and os.path.isdir(cand):
            return cand
    return ""


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": NO_WINDOW | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


def _get_refresh_ms() -> int:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        v = int(cfg.get("controller_refresh_ms", DEFAULT_REFRESH_MS))
        return max(5000, min(v, 600000))
    except Exception:
        return DEFAULT_REFRESH_MS


# ── 控制台监控目标（可配置本机/远程后端地址，2026-09-01）──

_DEFAULT_TARGET_HOST = "127.0.0.1"
_DEFAULT_TARGET_PORT = 8000


def _normalize_endpoint(host, port=None):
    """归一化主机/端口：允许粘贴整段 URL，拆出 host 与 port。"""
    h = str(host or "").strip().replace("https://", "").replace("http://", "")
    h = h.rstrip("/").split("/")[0]
    if h.count(":") == 1 and h.rsplit(":", 1)[-1].isdigit():
        h, pp = h.rsplit(":", 1)
        if port in (None, ""):
            port = pp
    try:
        p = int(port)
    except Exception:
        p = _DEFAULT_TARGET_PORT
    if not (1 <= p <= 65535):
        p = _DEFAULT_TARGET_PORT
    return (h or _DEFAULT_TARGET_HOST), p


def _load_target():
    cfg = _load_config()
    return _normalize_endpoint(cfg.get("controller_target_host") or _DEFAULT_TARGET_HOST,
                               cfg.get("controller_target_port", _DEFAULT_TARGET_PORT))


TARGET_HOST, TARGET_PORT = _load_target()


def set_server_target(host: str, port: int):
    """运行时切换监控目标（保存配置后调用，立即生效）。"""
    global TARGET_HOST, TARGET_PORT
    TARGET_HOST = str(host or _DEFAULT_TARGET_HOST).strip()
    TARGET_PORT = int(port)


def _local_ip_set() -> set:
    ips = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
    try:
        import psutil
        for addrs in psutil.net_if_addrs().values():
            for ad in addrs:
                ips.add((ad.address or "").split("%")[0].lower())
    except Exception:
        pass
    return ips


def _is_remote() -> bool:
    """监控目标是否为另一台机器：远程只做状态监控，不做进程管理/本地库读取。"""
    return TARGET_HOST.strip().lower() not in _local_ip_set()


def _target_base() -> str:
    return "http://%s:%d" % (TARGET_HOST, TARGET_PORT)


def _check_alive() -> bool:
    try:
        with socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=1.5):
            return True
    except OSError:
        return False


def _port_pid(port: int) -> int:
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
                capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
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


# 服务监听端口（与 uvicorn --port、各处探活保持一致）
SERVER_PORT = 8000

# 识别代理/VPN 虚拟网卡（Clash TUN、Tailscale、虚拟机等），挑真实局域网 IP 时跳过
_TUN_NAME_HINTS = ("tailscale", "wintun", "clash", "singbox", "tun", "tap",
                   "hyper-v", "vethernet", "vmware", "virtualbox", "docker", "loopback")


def _is_lan_private(ip: str) -> bool:
    """是否为标准家用/企业局域网私网地址（10/8、172.16-31、192.168/16）。"""
    try:
        parts = ip.split(".")
        a, b = int(parts[0]), int(parts[1])
    except Exception:
        return False
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def _looks_tun(ip: str) -> bool:
    """198.18/198.19 基准保留段（Clash/sing-box fake-ip）、100.64/10 CGNAT（Tailscale）。"""
    try:
        a, b = int(ip.split(".")[0]), int(ip.split(".")[1])
    except Exception:
        return False
    if a in (198,) and b in (18, 19):
        return True
    if a == 100 and 64 <= b <= 127:
        return True
    return False


def _psutil_lan_ip() -> str:
    try:
        import psutil
    except Exception:
        return ""
    found = []
    try:
        for name, addrs in psutil.net_if_addrs().items():
            low = name.lower()
            if any(k in low for k in _TUN_NAME_HINTS):
                continue
            for ad in addrs:
                fam = getattr(ad, "family", None)
                if fam is not None and getattr(fam, "name", "") == "AF_INET" \
                        and _is_lan_private(ad.address):
                    found.append(ad.address)
    except Exception:
        return ""
    return found[0] if found else ""


def _ipconfig_lan_ip() -> str:
    if os.name != "nt":
        return ""
    try:
        import re
        proc = subprocess.run(["ipconfig"], capture_output=True, timeout=5)
        raw = proc.stdout or b""
        out = ""
        for enc in ("gbk", "utf-8", "mbcs"):  # 中文 Windows ipconfig 为 GBK
            try:
                out = raw.decode(enc, errors="ignore")
                break
            except Exception:
                continue
        ips = re.findall(r"IPv4[^:]*?:\s*([0-9.]+)", out)
        for ip in ips:
            if _is_lan_private(ip):
                return ip
    except Exception:
        return ""
    return ""


def _get_lan_ip() -> str:
    """真实局域网 IPv4。依次：psutil 网卡 → UDP 默认路由 → 主机名解析 → ipconfig；
    全程优先标准私网地址，避开 Clash/Tailscale 等 TUN 虚拟网卡；都没有再退回 UDP 结果。"""
    ip = _psutil_lan_ip()
    if ip:
        return ip
    udp = ""
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sk.connect(("8.8.8.8", 80))
            udp = sk.getsockname()[0]
        finally:
            sk.close()
    except Exception:
        udp = ""
    if _is_lan_private(udp):
        return udp
    try:
        for t in socket.getaddrinfo(socket.gethostname(), None):
            if t[0] == socket.AF_INET and _is_lan_private(t[4][0]):
                return t[4][0]
    except Exception:
        pass
    ip2 = _ipconfig_lan_ip()
    if ip2:
        return ip2
    return udp


def _get_tailscale_ip() -> str:
    """Tailscale IPv4（跨网络组网用）。未安装/未登录返回空串。"""
    try:
        out = subprocess.run(["tailscale", "ip", "-4"],
                             capture_output=True, text=True, timeout=3)
        lines = (out.stdout or "").strip().splitlines()
        return lines[0].strip() if lines else ""
    except Exception:
        return ""


def _get_pid() -> int:
    if _is_remote():
        return 0  # 远程主机的进程 PID 无法在本机取得
    return _port_pid(TARGET_PORT)


def _run_manager(cmd: str) -> None:
    try:
        subprocess.Popen([PYTHON, MANAGER_PY, cmd], **_popen_kwargs())
    except Exception as e:
        raise RuntimeError("执行失败: {0}".format(e))


def _start_uvicorn():
    try:
        os.makedirs(os.path.dirname(STDERR_LOG), exist_ok=True)
        with open(STDERR_LOG, "a", encoding="utf-8") as f:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(TARGET_PORT)],
                cwd=SERVER_DIR, stdout=f, stderr=subprocess.STDOUT, **_popen_kwargs())
    except Exception as e:
        raise RuntimeError(f"启动失败: {e}")


def _ollama_alive() -> bool:
    # 本机模式探本机 Ollama；远程模式尝试探目标主机同端口（远程 Ollama 需绑 0.0.0.0）
    host = TARGET_HOST if _is_remote() else "127.0.0.1"
    try:
        with socket.create_connection((host, OLLAMA_PORT), timeout=1):
            return True
    except OSError:
        return False


def _get_ollama_pid() -> int:
    if _is_remote():
        return 0
    return _port_pid(OLLAMA_PORT)


def _start_ollama(low_vram: bool = False) -> None:
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
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("ollama_low_vram", False))
    except Exception:
        return False


def _kill_tree(proc, killed: set) -> None:
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
    if os.name == "nt":
        try:
            ollama_root = os.path.dirname(_find_ollama())
            script_parts = [
                "$ErrorActionPreference='SilentlyContinue'; ",
                "$p = Get-NetTCPConnection -State Listen -LocalPort %d | " % OLLAMA_PORT,
                "Select-Object -ExpandProperty OwningProcess; ",
                "if ($p) { taskkill /T /F /PID $p | Out-Null }; ",
                "Get-CimInstance Win32_Process | ",
                "Where-Object { $_.Name -eq 'llama-server.exe' -and ",
                "$_.ExecutablePath -like '" + ollama_root + "*' } | ",
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
            ]
            subprocess.run(["powershell", "-NoProfile", "-Command", "".join(script_parts)],
                           capture_output=True, timeout=15, creationflags=NO_WINDOW)
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
    def tail(path, size=12000):
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                n = f.tell()
                f.seek(max(0, n - size))
                data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-LOG_TAIL:])
        except Exception:
            return ""
    parts = [p for p in (tail(APP_LOG), tail(STDERR_LOG)) if p.strip()]
    return "\n".join(parts)


def _fetch_health() -> str:
    if not _check_alive():
        return "—"
    try:
        import urllib.request
        with urllib.request.urlopen(_target_base() + "/api/v1/system/health", timeout=2.5) as r:
            return "正常" if r.status == 200 else "异常"
    except Exception:
        return "异常"


def _fmt_compact(v) -> str:
    try:
        v = int(v)
    except Exception:
        return "0"
    if v >= 10 ** 6:
        x = v / 10 ** 6
        return f"{x:.1f}M" if x != int(x) else f"{int(x)}M"
    if v >= 10 ** 3:
        x = v / 10 ** 3
        return f"{x:.1f}k" if x != int(x) else f"{int(x)}k"
    return str(v)


def _fmt_short_date(date_str: str) -> str:
    try:
        y, m, d = str(date_str).split("-")
        return f"{int(m)}/{int(d)}"
    except Exception:
        return str(date_str)


def _aggregate_token_trend(rows, days=7, tz_name="Asia/Shanghai", today=None) -> list:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone(timedelta(hours=8))
    ref = today or datetime.now(tz)
    today_date = ref.astimezone(tz).date() if getattr(ref, "tzinfo", None) else ref.date()
    start = today_date - timedelta(days=days - 1)
    buckets = {}
    for i in range(days):
        buckets[(start + timedelta(days=i)).isoformat()] = 0
    for created_at, tokens in rows:
        if not created_at:
            continue
        try:
            s = str(created_at).strip().replace("Z", "+00:00")
            if "T" not in s and " " in s:
                s = s.replace(" ", "T", 1)
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            day = dt.astimezone(tz).date().isoformat()
            if day in buckets:
                buckets[day] += int(tokens or 0)
        except Exception:
            continue
    return [{"date": d, "tokens": buckets[d]} for d in sorted(buckets)]


def _read_token_trend(days=7, db=None, today=None) -> list:
    if db is None:
        db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    if not os.path.isfile(db):
        return []
    try:
        con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT created_at, COALESCE(total_tokens, 0) FROM llm_usage WHERE created_at IS NOT NULL"
        ).fetchall()
    except Exception:
        return []
    finally:
        try:
            con.close()
        except Exception:
            pass
    return _aggregate_token_trend(rows, days=days, today=today)

# ═══════════════════════════════════════════════════════════════
# Token 热力图 / 任务占比（近 17 周）
# ═══════════════════════════════════════════════════════════════
HEATMAP_WEEKS = 26
WEEK_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TASK_LABELS = {
    "message": "主对话", "chat": "智能体思考", "memory": "记忆处理",
    "status": "状态评估", "card": "织库卡片", "review": "主动复习",
    "diary": "日记生成", "game": "游戏对局", "emotion": "情绪关怀",
    "reflection": "每日反思", "life_tick": "AI 生活", "life_loop": "AI 生活",
    "life_regression": "AI 生活", "life_share": "AI 生活",
    "timeline": "时间线", "plugin_ai": "其他", "eval_100": "其他",
    "": "历史未分类",
}


def _hex_mix(c1: str, c2: str, f: float) -> str:
    def _hx(c):
        c = c.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a, b = _hx(c1), _hx(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


def _read_token_heatmap(weeks=HEATMAP_WEEKS, db=None, today=None):
    """近 N 周按天用量：周一列对齐、定长 weeks*7，末尾为本周未来占位日（future=True）。"""
    if _is_remote():
        return []  # 远程模式不读本地库，热力图显示「暂无数据」
    if db is None:
        db = os.path.join(SERVER_DIR, "data", "sqlite", "ai_companion.db")
    try:
        tz = ZoneInfo("Asia/Shanghai")
    except Exception:
        tz = timezone(timedelta(hours=8))
    ref = today or datetime.now(tz)
    today_date = ref.astimezone(tz).date() if getattr(ref, "tzinfo", None) else ref.date()
    this_mon = today_date - timedelta(days=today_date.weekday())
    start = this_mon - timedelta(weeks=weeks - 1)
    n = weeks * 7
    day_list = [start + timedelta(days=i) for i in range(n)]
    buckets = {d.isoformat(): 0 for d in day_list}
    tasks_by_day = {}
    if os.path.isfile(db):
        try:
            con = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=2)
            rows = con.execute(
                "SELECT created_at, COALESCE(task,''), COALESCE(total_tokens,0) FROM llm_usage "
                "WHERE created_at IS NOT NULL").fetchall()
            con.close()
        except Exception:
            rows = []
        for created_at, task, tokens in rows:
            try:
                s = str(created_at).strip().replace("Z", "+00:00")
                if "T" not in s and " " in s:
                    s = s.replace(" ", "T", 1)
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                k = dt.astimezone(tz).date().isoformat()
                if k in buckets:
                    buckets[k] += int(tokens or 0)
                    # 每日任务构成（展示名聚合），供热力图悬浮窗使用
                    label = TASK_LABELS.get((task or "").strip(), "其他")
                    day_t = tasks_by_day.setdefault(k, {})
                    day_t[label] = day_t.get(label, 0) + int(tokens or 0)
            except Exception:
                continue
    return [{"date": d.isoformat(), "tokens": buckets[d.isoformat()],
             "tasks": tasks_by_day.get(d.isoformat(), {}),
             "future": d > today_date} for d in day_list]


def _fold_topn(items, n=6):
    """最多 n 行：前 n-1 大 + 唯一「其他」（合并既有其他与剩余项），其他置末位。"""
    other = sum(x["tokens"] for x in items if x["label"] == "其他")
    rest = [x for x in items if x["label"] != "其他"]
    head = rest[:max(1, n - 1)]
    other += sum(x["tokens"] for x in rest[max(1, n - 1):])
    out = list(head)
    if other > 0:
        out.append({"label": "其他", "tokens": other, "calls": 0})
    return out


def _heat_series(days, mode="day"):
    """按模式返回每格数值：day 当天 / week 当周合计 / cum 截至当日累计。"""
    n = len(days)
    vals = [0] * n
    if mode == "week":
        for col in range(n // 7):
            s = sum(days[col * 7 + r]["tokens"] for r in range(7))
            for r in range(7):
                vals[col * 7 + r] = s
    elif mode == "cum":
        run = 0
        for i, d in enumerate(days):
            run += d["tokens"]
            vals[i] = run
    else:
        vals = [d["tokens"] for d in days]
    return vals


def _heat_levels(vals):
    """非零值按 50/75/90 分位映射 0..4 档色阶（避免被个别超大日压扁层次）。"""
    nz = sorted(v for v in vals if v > 0)
    levels = [0] * len(vals)
    if not nz:
        return levels

    def _q(p):
        return nz[min(len(nz) - 1, int(p * len(nz)))]
    t1, t2, t3 = _q(.5), _q(.75), _q(.9)
    for i, v in enumerate(vals):
        if v <= 0:
            levels[i] = 0
        elif v < t1:
            levels[i] = 1
        elif v < t2:
            levels[i] = 2
        elif v < t3:
            levels[i] = 3
        else:
            levels[i] = 4
    return levels



def _read_db_stats() -> dict:
    stats = {"characters": None, "memories": None, "tokens": None}
    if _is_remote():
        return stats  # 远程主机的本地 SQLite 不可达，保持「—」
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
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except Exception:
        return "—"


# ═══════════════════════════════════════════════════════════════
# 主应用
# ═══════════════════════════════════════════════════════════════

class ControllerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: "queue.Queue[tuple]" = queue.Queue()

        # 加载主题
        cfg0 = _load_config()
        theme_name = cfg0.get("controller_theme", "aurora")
        if theme_name not in THEMES:
            theme_name = "aurora"
        self.theme = THEMES[theme_name]

        root.title("拥爱服务器控制台")
        try:
            dpi = root.winfo_fpixels("1i")
            scale = dpi / 96.0
        except Exception:
            scale = 1.0
        w = int(1360 * scale)
        h = int(880 * scale)
        root.geometry(f"{w}x{h}")
        root.minsize(int(1150 * scale), int(700 * scale))
        root.configure(bg=self.theme.bg)
        self._scale = scale
        self._apply_window_icon()
        self._apply_titlebar()

        self._build_ui()

        self._last_log = ""
        self._last_state = None
        self._ollama_busy = False
        self._busy = False
        self._refreshing = False
        self._nav = "dashboard"
        self._alive = False
        self._ollama_alive = False
        self._trend = []
        self._last_refresh_ts = 0.0
        self._catchup_left = 0
        self._catchup_target = None
        self._catchup_after_id = None
        self._log_after_id = None

        self._select_page("dashboard")
        self._poll()
        self._do_refresh()
        self._schedule_refresh()
        self._pulse_phase = 0
        self._pulse_tick()

    # ── 主题 / 窗口 ──

    def _apply_titlebar(self):
        if os.name == "nt":
            self.root.update_idletasks()
            try:
                # Tk 在 Windows 上 winfo_id() 返回客户区 HWND，GetParent 取顶层窗口；
                # 两个都尝试设置，兼容不同 Tk 版本。
                hwnds = []
                try:
                    h = ctypes.windll.user32.GetParent(self.root.winfo_id())
                    if h:
                        hwnds.append(h)
                except Exception:
                    pass
                hwnds.append(self.root.winfo_id())
                for h in hwnds:
                    set_titlebar_theme(h, self.theme.dark)
            except Exception:
                pass

    def _apply_window_icon(self) -> None:
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
                self._icon_img = _img
        except Exception:
            pass

    def switch_theme(self, name: str):
        if name not in THEMES or name == self.theme.name:
            return
        self.theme = THEMES[name]
        # 持久化
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        cfg["controller_theme"] = name
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self._rebuild_ui()

    def _rebuild_ui(self):
        """销毁全部控件后重建（主题切换）。"""
        for child in self.root.winfo_children():
            child.destroy()
        self.root.configure(bg=self.theme.bg)
        self._apply_titlebar()
        self._build_ui()
        self._select_page(self._nav)
        self._do_refresh()

    # ── UI 构建 ──

    def _build_ui(self):
        self._style_ttk()
        t = self.theme
        if t.name == "aurora":
            # Aurora 极光条：窗口最顶缘 2px 三色渐变带（teal / sky / violet）
            strip = tk.Frame(self.root, height=2)
            strip.pack(side="top", fill="x")
            strip.pack_propagate(False)
            for color in (t.accent, "#7DD3FC", "#A78BFA"):
                seg = tk.Frame(strip, bg=color, height=2)
                seg.pack(side="left", fill="both", expand=True)
                seg.pack_propagate(False)
        self._build_statusbar()
        self._build_header()
        self._build_body()

    def _build_header(self) -> None:
        t = self.theme
        header = tk.Frame(self.root, bg=t.sidebar, height=52)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=t.sidebar)
        brand.pack(side="left", padx=(SP_LG, 0), pady=SP_SM)
        _make_icon(brand, 18, "diamond", t.accent_glow, t.sidebar).pack(side="left")
        tk.Label(brand, text="拥爱服务器控制台", fg=t.text, bg=t.sidebar,
                 font=(FONT, 14, "bold")).pack(side="left", padx=(SP_XS, 0))

        right = tk.Frame(header, bg=t.sidebar)
        right.pack(side="right", padx=SP_LG, pady=SP_SM)
        self.header_dot = _make_icon(right, 10, "dot", t.text_muted, t.sidebar)
        self.header_dot.pack(side="left")
        self.header_status_label = tk.Label(right, text="检测中…", fg=t.text, bg=t.sidebar, font=(FONT, 12, "bold"))
        self.header_status_label.pack(side="left", padx=(SP_XS, SP_SM))
        self.header_port_label = tk.Label(
            right,
            text=("目标 %s:%d" % (TARGET_HOST, TARGET_PORT)) if _is_remote()
                 else ("端口 %d" % TARGET_PORT),
            fg=t.text_sec, bg=t.sidebar, font=(FONT, 11))
        self.header_port_label.pack(side="left", padx=(0, SP_SM))
        self.header_health_label = tk.Label(right, text="健康 —", fg=t.text_sec, bg=t.sidebar, font=(FONT, 11))
        self.header_health_label.pack(side="left", padx=(0, SP_MD))
        tk.Button(right, text="刷新", bg=t.sidebar, fg=t.accent_glow, activebackground=t.sidebar,
                  activeforeground=t.accent_glow, relief="flat", bd=0, font=(FONT, 11, "bold"),
                  cursor="hand2", command=self._do_refresh).pack(side="left", padx=(0, SP_SM))
        tk.Button(right, text="设置", bg=t.sidebar, fg=t.accent_glow, activebackground=t.sidebar,
                  activeforeground=t.accent_glow, relief="flat", bd=0, font=(FONT, 11, "bold"),
                  cursor="hand2", command=self.open_settings).pack(side="left")

        tk.Frame(self.root, height=1, bg=t.divider).pack(side="top", fill="x")

    def _build_statusbar(self) -> None:
        t = self.theme
        bar = tk.Frame(self.root, bg=t.sidebar, height=28)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, width=1, bg=t.divider).pack(side="left", fill="y")
        self.msg = tk.Label(bar, text="就绪", fg=t.text_sec, bg=t.sidebar, font=(FONT, 10))
        self.msg.pack(side="left", padx=SP_MD)
        self.refresh_label = tk.Label(bar, text=f"刷新间隔 {_get_refresh_ms() // 1000}s",
                                      fg=t.text_muted, bg=t.sidebar, font=(FONT, 10))
        self.refresh_label.pack(side="right", padx=(0, SP_SM))
        self.last_refresh_label = tk.Label(bar, text="上次刷新 —", fg=t.text_muted, bg=t.sidebar, font=(FONT, 10))
        self.last_refresh_label.pack(side="right", padx=(0, SP_LG))

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.root, bg=t.bg)
        body.pack(side="top", fill="both", expand=True)

        self._sidebar = tk.Frame(body, bg=t.sidebar, width=208)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        tk.Frame(self._sidebar, width=1, bg=t.divider).pack(side="right", fill="y")

        self._content = tk.Frame(body, bg=t.bg)
        self._content.pack(side="left", fill="both", expand=True)

        self._nav_widgets = {}
        nav_items = [
            ("dashboard", "仪表盘", "dashboard"),
            ("server", "服务器控制", "server"),
            ("log", "运行日志", "log"),
        ]
        for key, label, icon in nav_items:
            item = tk.Frame(self._sidebar, bg=t.sidebar, cursor="hand2")
            item.pack(fill="x", padx=SP_SM, pady=2)
            bar = tk.Frame(item, width=3, bg=t.sidebar)
            bar.pack(side="left", fill="y")
            ic = _make_icon(item, 16, icon, t.text_sec, t.sidebar)
            ic.pack(side="left", padx=(SP_MD, SP_SM), pady=SP_SM)
            lb = tk.Label(item, text=label, bg=t.sidebar, fg=t.text_sec, font=(FONT, 12))
            lb.pack(side="left", pady=SP_SM)
            for wid in (item, ic, lb, bar):
                wid.bind("<Button-1>", lambda e, k=key: self._select_page(k))
                wid.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                wid.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self._nav_widgets[key] = (item, ic, lb, bar)

        self._pages = {
            "dashboard": self._build_dashboard_page(),
            "server": self._build_server_page(),
            "log": self._build_log_page(),
        }

    def _select_page(self, key: str) -> None:
        t = self.theme
        for k in self._pages:
            self._pages[k].pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        for k, (item, ic, lb, bar) in self._nav_widgets.items():
            if k == key:
                item.config(bg=t.accent_dim)
                bar.config(bg=t.accent)
                ic.config(bg=t.accent_dim)
                ic._paint_icon(t.accent_glow)
                lb.config(bg=t.accent_dim, fg=t.text, font=(FONT, 12, "bold"))
            else:
                item.config(bg=t.sidebar)
                bar.config(bg=t.sidebar)
                ic.config(bg=t.sidebar)
                ic._paint_icon(t.text_sec)
                lb.config(bg=t.sidebar, fg=t.text_sec, font=(FONT, 12))
        self._nav = key
        if key == "log":
            self._refresh_log_tick()
        else:
            self._stop_log_timer()

    def _nav_hover(self, key: str, entering: bool) -> None:
        if key == self._nav:
            return
        t = self.theme
        item, ic, lb, bar = self._nav_widgets[key]
        if entering:
            item.config(bg=t.card_hover)
            ic.config(bg=t.card_hover)
            lb.config(bg=t.card_hover, fg=t.text)
        else:
            item.config(bg=t.sidebar)
            ic.config(bg=t.sidebar)
            lb.config(bg=t.sidebar, fg=t.text_sec)

    # ── 卡片工厂 ──

    def _make_card(self, parent, row, col, title, caption="", height=None):
        t = self.theme
        # KPI 卡默认随内容自适应：标题+22号数值+说明在高 DPI 下自然高度约 140px，
        # 钉死 72px 会让 Tk packer 把数值/说明判为装不下而取消映射（界面只剩标题）
        kw = dict(pad=3, fit_inner=height is None)
        if height is not None:
            kw["height"] = height
        card = RoundedCard(parent, t, **kw)
        card.grid(row=row, column=col, sticky="nsew", padx=SP_XS, pady=SP_XS)
        inner = card.inner
        inner.config(padx=SP_LG, pady=SP_MD)
        tk.Frame(inner, bg=t.card).pack(fill="both", expand=True)
        tk.Label(inner, text=title, fg=t.text_sec, bg=t.card, font=(FONT, 11)).pack(anchor="w")
        value = tk.Label(inner, text="—", fg=t.text, bg=t.card, font=(FONT, 22, "bold"))
        value.pack(anchor="w", pady=(SP_XS, 2))
        cap = tk.Label(inner, text=caption, fg=t.text_muted, bg=t.card, font=(FONT, 10))
        cap.pack(anchor="w")
        tk.Frame(inner, bg=t.card).pack(fill="both", expand=True)
        return card, value, cap

    def _wrap_card(self, parent, height=None, **pack_kw):
        """返回一个圆角卡片及其 inner Frame，供自由布局使用。height 显式固定高度。"""
        t = self.theme
        card = RoundedCard(parent, t, pad=3, height=height) if height else RoundedCard(parent, t, pad=3)
        card.pack(**pack_kw)
        inner = card.inner
        inner.config(padx=SP_LG, pady=SP_LG)
        return card, inner

    # ── 仪表盘 ──

    def _build_dashboard_page(self) -> tk.Frame:
        t = self.theme
        page = tk.Frame(self._content, bg=t.bg)
        # 纵向滚动容器：保证任何窗口高度下卡片都按自然高度排布，热力图不再被压没
        _scroll = tk.Canvas(page, bg=t.bg, highlightthickness=0, bd=0)
        _sb = ttk.Scrollbar(page, orient="vertical", command=_scroll.yview)
        _scroll.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _scroll.pack(side="left", fill="both", expand=True)
        pad = tk.Frame(_scroll, bg=t.bg)
        _pad_win = _scroll.create_window((SP_LG, SP_SM), window=pad, anchor="nw")
        pad.bind("<Configure>",
                 lambda e: _scroll.configure(scrollregion=_scroll.bbox("all")))

        def _fit_width(e, c=_scroll, w=_pad_win):
            c.itemconfig(w, width=max(1, e.width - 2 * SP_LG))
        _scroll.bind("<Configure>", _fit_width)

        def _on_enter(e, c=_scroll):
            c.bind_all("<MouseWheel>",
                       lambda ev: c.yview_scroll(int(-ev.delta / 120), "units"))

        def _on_leave(e):
            _scroll.unbind_all("<MouseWheel>")
        _scroll.bind("<Enter>", _on_enter)
        _scroll.bind("<Leave>", _on_leave)
        tk.Label(pad, text="服务器概览", fg=t.text, bg=t.bg, font=(FONT, 16, "bold")).pack(anchor="w")
        tk.Label(pad, text="运行状态、健康与数据规模一目了然", fg=t.text_muted, bg=t.bg,
                 font=(FONT, 11)).pack(anchor="w", pady=(2, SP_MD))

        # 6 张 KPI 卡 2×3（自然高度）；热力图与占比卡按舒适高度排布，整页可滚动
        grid = tk.Frame(pad, bg=t.bg)
        grid.pack(fill="x")
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="kpi")
        grid.rowconfigure(2, minsize=330)

        _, self.v_server, self.c_server = self._make_card(grid, 0, 0, "服务器状态")
        _, self.v_health, self.c_health = self._make_card(grid, 0, 1, "服务健康", "HTTP 探活")
        _, self.v_ollama, self.c_ollama = self._make_card(grid, 0, 2, OLLAMA_LABEL)
        _, self.v_chars, self.c_chars = self._make_card(grid, 1, 0, "角色数", "个角色")
        _, self.v_mems, self.c_mems = self._make_card(grid, 1, 1, "记忆数", "条记忆")
        _, self.v_tokens, self.c_tokens = self._make_card(grid, 1, 2, "累计 Token", "Token 累计")

        # Token 活动卡：近 17 周热力图（每日/每周/累计），hover 查看当天用量与占比
        heat_card = RoundedCard(grid, t, pad=3, height=330)
        heat_card.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=3, pady=3)
        heat_inner = heat_card.inner
        heat_inner.config(padx=SP_LG, pady=SP_XS)
        heat_head = tk.Frame(heat_inner, bg=t.card)
        heat_head.pack(fill="x")
        tk.Label(heat_head, text="Token 活动（近 26 周 · 悬停看当日任务构成）",
                 fg=t.text_sec, bg=t.card,
                 font=(FONT, 11)).pack(side="left")
        self._heat_mode = "day"
        self._heat_days = []
        self._heat_series_vals = []
        self._heat_levels = []
        self._heat_cells = {}
        self._heat_tip = None
        self.heat_seg = Segmented(
            heat_head, t, [("day", "每日"), ("week", "每周"), ("cum", "累计")],
            self._set_heat_mode, width=156, height=26)
        self.heat_seg.pack(side="right")
        self.heat_canvas = tk.Canvas(heat_inner, bg=t.card, highlightthickness=0, bd=0,
                                     height=168)
        self.heat_canvas.pack(fill="both", expand=True, pady=(2, 0))
        self.heat_canvas.bind("<Configure>", lambda e: self._draw_heatmap())
        self.heat_canvas.bind("<Motion>", self._on_heat_motion)
        self.heat_canvas.bind("<Leave>", lambda e: self._hide_heat_tip())

        # 快捷操作（自然排在末尾）
        actions = tk.Frame(pad, bg=t.bg)
        actions.pack(fill="x", pady=(SP_SM, 0))
        self._dash_btn_start = RoundedButton(actions, t, "启动服务器", command=self.start_server, variant="primary")
        self._dash_btn_start.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_stop = RoundedButton(actions, t, "停止服务器", command=self.stop_server, variant="danger")
        self._dash_btn_stop.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_restart = RoundedButton(actions, t, "重启服务器", command=self.restart_server, variant="neutral")
        self._dash_btn_restart.pack(side="left", padx=(0, SP_XS))
        self._dash_btn_log = RoundedButton(actions, t, "打开日志文件", command=self.open_log, variant="neutral")
        self._dash_btn_log.pack(side="left", padx=(0, SP_XS))

        return page

    # ── 服务器地址 ──

    def _probe_addresses(self):
        """后台探测本机/局域网/Tailscale 地址，避免 subprocess 卡住 UI。"""
        if not hasattr(self, "_addr_value"):
            return
        for _v in self._addr_value.values():
            try:
                _v.config(text="探测中…")
            except Exception:
                pass

        def job():
            if _is_remote():
                url = _target_base()
                view = {"local": (url, url + "    （当前监控目标·远程主机）"),
                        "lan": ("", "远程模式下不探测本机局域网地址"),
                        "ts": ("", "远程模式下不探测 Tailscale")}
                self._q.put(("addrs", view))
                return
            lan_ip = _get_lan_ip()
            ts_ip = _get_tailscale_ip()
            local_url = "http://127.0.0.1:%d" % TARGET_PORT
            if lan_ip:
                lan_url = "http://%s:%d" % (lan_ip, TARGET_PORT)
                lan_show = lan_url + ("    （疑似代理/TUN 虚拟网卡，手机若连不上请关闭系统代理后重新探测）"
                                      if _looks_tun(lan_ip) else "")
            else:
                lan_url = ""
                lan_show = "未探测到局域网 IP（请确认本机已连网）"
            if ts_ip:
                ts_url = "http://%s:%d" % (ts_ip, TARGET_PORT)
                ts_show = ts_url
            else:
                ts_url = ""
                ts_show = "未安装/未登录 Tailscale（跨网访问时使用）"
            view = {"local": (local_url, local_url + "    （服务器本机自测）"),
                    "lan": (lan_url, lan_show),
                    "ts": (ts_url, ts_show)}
            # 经统一队列回到主线程渲染（Tk 跨线程调用不安全，与本文件其它后台任务一致）
            self._q.put(("addrs", view))

        threading.Thread(target=job, daemon=True).start()

    def _render_addresses(self, view: dict):
        """view: {key: (纯净URL, 展示文本)}；URL 供复制，展示文本可带提示。"""
        if not hasattr(self, "_addr_value"):
            return
        for key, (url, show) in view.items():
            self._addresses[key] = url
            if key in self._addr_value:
                self._addr_value[key].config(text=show)

    def _copy_address(self, key: str):
        val = getattr(self, "_addresses", {}).get(key, "")
        if not isinstance(val, str) or not val.startswith("http"):
            self._set_msg("该地址暂不可用，无法复制")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(val)
        self._set_msg("已复制服务器地址：%s" % val)

    # ── 服务器控制页 ──

    def _build_server_page(self) -> tk.Frame:
        t = self.theme
        page = tk.Frame(self._content, bg=t.bg)
        # 纵向滚动容器：保证任何窗口高度下卡片都按自然高度排布，热力图不再被压没
        _scroll = tk.Canvas(page, bg=t.bg, highlightthickness=0, bd=0)
        _sb = ttk.Scrollbar(page, orient="vertical", command=_scroll.yview)
        _scroll.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _scroll.pack(side="left", fill="both", expand=True)
        pad = tk.Frame(_scroll, bg=t.bg)
        _pad_win = _scroll.create_window((SP_LG, SP_SM), window=pad, anchor="nw")
        pad.bind("<Configure>",
                 lambda e: _scroll.configure(scrollregion=_scroll.bbox("all")))

        def _fit_width(e, c=_scroll, w=_pad_win):
            c.itemconfig(w, width=max(1, e.width - 2 * SP_LG))
        _scroll.bind("<Configure>", _fit_width)

        def _on_enter(e, c=_scroll):
            c.bind_all("<MouseWheel>",
                       lambda ev: c.yview_scroll(int(-ev.delta / 120), "units"))

        def _on_leave(e):
            _scroll.unbind_all("<MouseWheel>")
        _scroll.bind("<Enter>", _on_enter)
        _scroll.bind("<Leave>", _on_leave)

        tk.Label(pad, text="服务器控制", fg=t.text, bg=t.bg, font=(FONT, 16, "bold")).pack(anchor="w")
        tk.Label(pad, text="启动 / 停止 / 重启核心服务", fg=t.text_muted, bg=t.bg,
                 font=(FONT, 11)).pack(anchor="w", pady=(2, SP_MD))

        _, inner = self._wrap_card(pad, height=52, fill="x", pady=(0, SP_MD))
        inner.config(pady=SP_XS)
        btn_row = tk.Frame(inner, bg=t.card)
        btn_row.pack(fill="x")
        self.btn_start = RoundedButton(btn_row, t, "启动服务器", command=self.start_server, variant="primary")
        self.btn_start.pack(side="left", padx=(0, SP_XS))
        self.btn_stop = RoundedButton(btn_row, t, "停止服务器", command=self.stop_server, variant="danger")
        self.btn_stop.pack(side="left", padx=(0, SP_XS))
        self.btn_restart = RoundedButton(btn_row, t, "重启服务器", command=self.restart_server, variant="neutral")
        self.btn_restart.pack(side="left")
        self.server_pid_label = tk.Label(btn_row, text="", fg=t.text_sec, bg=t.card, font=(FONT, 12))
        self.server_pid_label.pack(side="left", padx=SP_MD)

        # 图像理解服务（Ollama）：压缩为单行，与上方开关同一密度，不再独占整块
        _, ol = self._wrap_card(pad, height=46, fill="x", pady=(0, SP_SM))
        ol.config(pady=SP_XS)
        ol_row = tk.Frame(ol, bg=t.card)
        ol_row.pack(fill="x")
        self.ollama_dot = _make_icon(ol_row, 14, "dot", t.text_muted, t.card)
        self.ollama_dot.pack(side="left")
        tk.Label(ol_row, text=OLLAMA_LABEL, fg=t.text, bg=t.card,
                 font=(FONT, 12, "bold")).pack(side="left", padx=(SP_XS, 0))
        self.ollama_pid_label = tk.Label(ol_row, text="", fg=t.text_sec, bg=t.card,
                                         font=(FONT, 11))
        self.ollama_pid_label.pack(side="left", padx=(SP_SM, 0))
        self._ollama_low_vram = _load_low_vram()
        self.low_vram_var = tk.BooleanVar(value=self._ollama_low_vram)
        self.chk_low_vram = ttk.Checkbutton(
            ol_row, text="省显存模式", variable=self.low_vram_var,
            command=self.toggle_low_vram)
        self.chk_low_vram.pack(side="right", padx=(SP_SM, SP_LG))
        self.btn_ollama_stop = RoundedButton(ol_row, t, "停止", command=self.stop_ollama,
                                             variant="danger", height=30, font_size=10)
        self.btn_ollama_stop.pack(side="right", padx=(SP_XS, 0))
        self.btn_ollama_start = RoundedButton(ol_row, t, "启动", command=self.start_ollama,
                                              variant="primary", height=30, font_size=10)
        self.btn_ollama_start.pack(side="right")

        # 控制台监控目标：可指向本机或另一台远程后端（远程仅监控状态）
        _, tgt = self._wrap_card(pad, height=96, fill="x", pady=(0, SP_SM))
        tgt.config(pady=SP_XS)
        tgt_row1 = tk.Frame(tgt, bg=t.card)
        tgt_row1.pack(fill="x")
        tk.Label(tgt_row1, text="控制台监控目标", width=14, anchor="w", fg=t.text, bg=t.card,
                 font=(FONT, 12, "bold")).pack(side="left")
        tk.Label(tgt_row1, text="主机", fg=t.text_muted, bg=t.card, font=(FONT, 10)).pack(side="left")
        self.target_host_var = tk.StringVar(value=TARGET_HOST)
        ttk.Entry(tgt_row1, textvariable=self.target_host_var, width=18).pack(side="left", padx=(4, SP_XS))
        tk.Label(tgt_row1, text="端口", fg=t.text_muted, bg=t.card, font=(FONT, 10)).pack(side="left")
        self.target_port_var = tk.StringVar(value=str(TARGET_PORT))
        ttk.Entry(tgt_row1, textvariable=self.target_port_var, width=7).pack(side="left", padx=(4, SP_SM))
        RoundedButton(tgt_row1, t, "保存切换", command=self._save_target,
                      variant="primary", height=30, font_size=10).pack(side="left", padx=(0, SP_XS))
        RoundedButton(tgt_row1, t, "恢复本机", command=self._reset_target,
                      variant="neutral", height=30, font_size=10).pack(side="left")
        tgt_row2 = tk.Frame(tgt, bg=t.card)
        tgt_row2.pack(fill="x", pady=(SP_XS, 0))
        self.target_mode_label = tk.Label(tgt_row2, text="", anchor="w", fg=t.text_sec,
                                          bg=t.card, font=(FONT, 10))
        self.target_mode_label.pack(side="left")
        self._refresh_target_ui()

        # 服务器地址（从仪表盘迁到这里：本机 / 局域网 / Tailscale，可一键复制）
        _, addr_inner = self._wrap_card(pad, height=164, fill="x")
        addr_inner.config(pady=SP_SM)
        addr_head = tk.Frame(addr_inner, bg=t.card)
        addr_head.pack(fill="x")
        tk.Label(addr_head, text="服务器地址（手机端「设置 → 服务器地址」填写）",
                 fg=t.text_sec, bg=t.card, font=(FONT, 11)).pack(side="left")
        RoundedButton(addr_head, t, "重新探测", command=self._probe_addresses,
                      variant="neutral", height=28, font_size=10).pack(side="right")
        self._addr_value = {}
        for _key, _label in (("local", "本机访问"),
                             ("lan", "局域网（同 Wi-Fi）"),
                             ("ts", "Tailscale 跨网")):
            _row = tk.Frame(addr_inner, bg=t.card)
            _row.pack(fill="x", pady=1)
            # 先 pack 右侧按钮预留位置，避免长地址把「复制」挤出可视区
            RoundedButton(_row, t, "复制", command=lambda k=_key: self._copy_address(k),
                          variant="neutral", height=26, font_size=9).pack(side="right")
            tk.Label(_row, text=_label, width=16, anchor="w", fg=t.text_muted,
                     bg=t.card, font=(FONT, 10)).pack(side="left")
            _v = tk.Label(_row, text="探测中…", anchor="w", fg=t.text,
                          bg=t.card, font=(FONT, 11, "bold"))
            _v.pack(side="left", fill="x", expand=True)
            self._addr_value[_key] = _v
        # _addresses 只存纯净 URL（供复制），展示文本可带提示
        self._addresses = {"local": "", "lan": "", "ts": ""}
        # 进入控制台即探测一次服务器地址（后台线程，不卡 UI）
        self.root.after(300, self._probe_addresses)
        return page

    # ── 日志页 ──

    def _build_log_page(self) -> tk.Frame:
        t = self.theme
        page = tk.Frame(self._content, bg=t.bg)
        pad = tk.Frame(page, bg=t.bg)
        pad.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        head = tk.Frame(pad, bg=t.bg)
        head.pack(fill="x", pady=(0, SP_SM))
        tk.Label(head, text="运行日志", fg=t.text, bg=t.bg, font=(FONT, 16, "bold")).pack(side="left")
        self.log_export_btn = RoundedButton(head, t, "导出日志", command=self.export_log, variant="neutral", height=32, font_size=10)
        self.log_export_btn.pack(side="right", padx=(SP_XS, 0))
        self.log_clear_btn = RoundedButton(head, t, "清空显示", command=self.clear_log_display, variant="neutral", height=32, font_size=10)
        self.log_clear_btn.pack(side="right", padx=(SP_XS, 0))
        self.log_open_btn = RoundedButton(head, t, "打开日志文件", command=self.open_log, variant="neutral", height=32, font_size=10)
        self.log_open_btn.pack(side="right")
        self.log_autoscroll_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(head, text="自动滚动", variable=self.log_autoscroll_var,
                        command=self._on_log_autoscroll).pack(side="right", padx=(0, SP_SM))

        self.log_box = scrolledtext.ScrolledText(pad, height=18, state="disabled",
                                                 bg=t.log_bg, fg=t.log_fg, insertbackground=t.accent_glow,
                                                 font=("Consolas", 10), relief="flat", bd=0,
                                                 highlightthickness=1, highlightbackground=t.hairline,
                                                 highlightcolor=t.hairline,
                                                 selectbackground=t.accent_dim, selectforeground=t.text)
        self.log_box.pack(fill="both", expand=True)
        return page

    # ── ttk 主题（Checkbutton / Entry 仍用 ttk）──

    def _style_ttk(self):
        t = self.theme
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCheckbutton", background=t.card, foreground=t.text, font=(FONT, 11))
        style.map("TCheckbutton", background=[("active", t.card)])
        style.configure("TEntry", fieldbackground=t.entry_bg, foreground=t.text,
                        bordercolor=t.hairline, lightcolor=t.hairline, darkcolor=t.hairline,
                        insertcolor=t.text)

    # ── 状态 / 消息 ──

    def _set_msg(self, text: str):
        self.msg.config(text=text)

    def _sync_button_states(self) -> None:
        alive, ollama = self._alive, self._ollama_alive
        remote = _is_remote()
        start_en = not (self._busy or alive or remote)
        stop_en = not (self._busy or not alive or remote)
        restart_en = not (self._busy or remote)
        for b in (self.btn_start, self._dash_btn_start):
            b.config_state(start_en)
        for b in (self.btn_stop, self._dash_btn_stop):
            b.config_state(stop_en)
        for b in (self.btn_restart, self._dash_btn_restart):
            b.config_state(restart_en)
        self.btn_ollama_start.config_state(not (self._ollama_busy or ollama or _is_remote()))
        self.btn_ollama_stop.config_state(not (self._ollama_busy or not ollama or _is_remote()))

    def _set_busy(self, busy: bool):
        self._busy = busy
        self._sync_button_states()

    def _set_ollama_busy(self, busy: bool):
        self._ollama_busy = busy
        self._sync_button_states()

    # ── 主线程轮询 ──

    def _poll(self):
        try:
            while True:
                item = self._q.get_nowait()
                kind = item[0]
                if kind == "state":
                    _, alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats = item
                    try:
                        self._apply_refresh(alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats)
                    except Exception as e:
                        _safe_traceback()
                        self._set_msg("仪表盘刷新异常: %s" % e)
                elif kind == "msg":
                    self._set_msg(item[1])
                elif kind == "busy":
                    self._set_busy(False)
                elif kind == "busy_ollama":
                    self._set_ollama_busy(False)
                elif kind == "refresh":
                    self._do_refresh()
                elif kind == "addrs":
                    try:
                        self._render_addresses(item[1])
                    except Exception:
                        _safe_traceback()
                elif kind == "catchup":
                    self._start_catchup(item[1])
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    # ── 操作 ──

    def _run_action(self, title: str, fn, catchup_target=None):
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
                if catchup_target:
                    self._q.put(("catchup", catchup_target))

        threading.Thread(target=job, daemon=True).start()

    def start_server(self):
        if _is_remote():
            self._set_msg("远程监控模式下不能从本机启动远程服务器")
            return
        def fn():
            if os.name == "nt":
                _run_manager("start")
                return "启动指令已发出（server_manager），服务器约 30-60 秒就绪"
            _start_uvicorn()
            return "已启动 uvicorn（后台），服务器约 30-60 秒就绪"
        self._run_action("正在启动服务器", fn, "running")

    def stop_server(self):
        if _is_remote():
            self._set_msg("远程监控模式下不能从本机停止远程服务器")
            return
        def fn():
            if os.name == "nt":
                _run_manager("stop")
                return "已停止（server_manager 已清理 uvicorn + watchdog）"
            pid = _port_pid(TARGET_PORT)
            if pid:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
                return f"已停止（PID {pid}）"
            return "未检测到运行中的服务器（端口 %d 无监听）" % TARGET_PORT
        self._run_action("正在停止服务器", fn, "stopped")

    def restart_server(self):
        if _is_remote():
            self._set_msg("远程监控模式下不能从本机重启远程服务器")
            return
        def fn():
            if os.name == "nt":
                _run_manager("restart")
                return "重启指令已发出（server_manager），服务器约 30-60 秒就绪"
            pid = _port_pid(TARGET_PORT)
            if pid:
                import signal as _sig
                os.kill(pid, _sig.SIGTERM)
            _start_uvicorn()
            return "已重启 uvicorn（后台）"
        self._run_action("正在重启服务器", fn, "running")

    def _run_ollama_action(self, title: str, fn, catchup_target=None):
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
                if catchup_target:
                    self._q.put(("catchup", catchup_target))

        threading.Thread(target=job, daemon=True).start()

    def start_ollama(self):
        if _is_remote():
            self._set_msg("远程监控模式下不能从本机启停远程 Ollama")
            return
        def fn():
            _start_ollama(self._ollama_low_vram)
            mode = "（省显存模式）" if self._ollama_low_vram else "（全速模式）"
            return "Ollama 启动指令已发出，模型加载约需 5-10 秒" + mode
        self._run_ollama_action("正在启动 Ollama", fn, "ollama_running")

    def toggle_low_vram(self):
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
            self._run_ollama_action("正在切换 Ollama 模式", self._restart_ollama_with_mode, "ollama_running")
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
        if _is_remote():
            self._set_msg("远程监控模式下不能从本机启停远程 Ollama")
            return
        def fn():
            _stop_ollama()
            return "Ollama 已停止"
        self._run_ollama_action("正在停止 Ollama", fn, "ollama_stopped")

    def open_log(self):
        if not os.path.exists(STDERR_LOG):
            return
        if os.name == "nt":
            os.startfile(STDERR_LOG)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", STDERR_LOG])
        else:
            subprocess.Popen(["xdg-open", STDERR_LOG])

    # ── 监控目标切换 ──

    def _persist_target(self, host: str, port: int) -> None:
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        cfg["controller_target_host"] = host
        cfg["controller_target_port"] = port
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    def _save_target(self):
        try:
            host, port = _normalize_endpoint(self.target_host_var.get(),
                                              self.target_port_var.get())
        except Exception as e:
            self._set_msg("地址无效：%s" % e)
            return
        self._persist_target(host, port)
        set_server_target(host, port)
        self.target_host_var.set(host)
        self.target_port_var.set(str(port))
        self._refresh_target_ui()
        mode = "远程仅监控" if _is_remote() else "本机完整模式"
        note = ""
        if not _is_remote() and port != _DEFAULT_TARGET_PORT:
            note = "；注意本机服务固定监听 8000，自定义端口只适用于远程监控"
        self._set_msg("监控目标已切换为 %s:%d（%s）%s" % (host, port, mode, note))
        self._do_refresh()

    def _reset_target(self):
        self.target_host_var.set(_DEFAULT_TARGET_HOST)
        self.target_port_var.set(str(_DEFAULT_TARGET_PORT))
        self._save_target()

    def _refresh_target_ui(self):
        if not hasattr(self, "target_mode_label"):
            return
        if _is_remote():
            self.target_mode_label.config(
                text="当前：远程监控模式 — 仅显示在线/健康；进程启停、本地数据库统计与热力图不可用")
        else:
            warn = "" if TARGET_PORT == _DEFAULT_TARGET_PORT else "（注意：本机服务固定监听 8000，改端口仅用于远程监控）"
            self.target_mode_label.config(
                text="当前：本机模式 — 可启停服务、读取本地数据库统计与 Token 热力图" + warn)

    # ── 设置弹窗 ──

    def open_settings(self):
        t = self.theme
        self._style_ttk()
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        win = tk.Toplevel(self.root)
        win.title("设置")
        win.resizable(False, False)
        win.transient(self.root)
        win.configure(bg=t.bg)

        outer = tk.Frame(win, bg=t.bg, padx=SP_LG, pady=SP_LG)
        outer.pack(fill="both", expand=True)
        card = RoundedCard(outer, t, pad=3)
        card.pack(fill="both", expand=True)
        frame = card.inner
        frame.config(padx=SP_LG, pady=SP_LG)

        # 主题切换
        tk.Label(frame, text="界面主题", bg=t.card, fg=t.text, font=(FONT, 11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, SP_XS), padx=(0, SP_LG))
        theme_var = tk.StringVar(value=self.theme.label)
        theme_names = [th.label for th in THEMES.values()]
        theme_menu = ttk.Combobox(frame, textvariable=theme_var, values=theme_names,
                                  state="readonly", width=12)
        theme_menu.grid(row=0, column=1, sticky="w", pady=(0, SP_XS))

        def on_theme_change(e=None):
            for k, th in THEMES.items():
                if th.label == theme_var.get():
                    self.switch_theme(k)
                    win.destroy()
                    return
        theme_menu.bind("<<ComboboxSelected>>", on_theme_change)

        # 分隔
        tk.Frame(frame, bg=t.divider, height=1).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=SP_SM)

        tk.Label(frame, text="守护检测间隔（秒）", bg=t.card, fg=t.text, font=(FONT, 11)).grid(
            row=2, column=0, sticky="w", pady=(0, SP_XS), padx=(0, SP_LG))
        wd_var = tk.StringVar(value=str(cfg.get("watchdog_interval_sec", 120)))
        ttk.Entry(frame, textvariable=wd_var, width=14).grid(row=2, column=1, sticky="w", pady=(0, SP_XS))

        tk.Label(frame, text="界面刷新间隔（秒）", bg=t.card, fg=t.text, font=(FONT, 11)).grid(
            row=3, column=0, sticky="w", pady=(0, SP_MD), padx=(0, SP_LG))
        rf_var = tk.StringVar(value=str(int(cfg.get("controller_refresh_ms", DEFAULT_REFRESH_MS)) // 1000))
        ttk.Entry(frame, textvariable=rf_var, width=14).grid(row=3, column=1, sticky="w", pady=(0, SP_MD))

        tk.Label(frame, text="修改后立即生效：\n守护间隔下个检测周期生效，界面刷新即时生效。",
                 bg=t.card, fg=t.text_sec, font=(FONT, 10)).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, SP_MD))

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

        btn_row = tk.Frame(frame, bg=t.card)
        btn_row.grid(row=5, column=0, columnspan=2, sticky="w")
        RoundedButton(btn_row, t, "保存", command=save, variant="primary", height=32, font_size=11).pack(side="left", padx=(0, SP_XS))
        RoundedButton(btn_row, t, "取消", command=win.destroy, variant="neutral", height=32, font_size=11).pack(side="left")

        win.grab_set()

    # ── 定时刷新 ──

    def _schedule_refresh(self):
        self.root.after(_get_refresh_ms(), self._schedule_refresh)
        self._do_refresh()

    def _do_refresh(self) -> bool:
        if self._refreshing:
            return False
        self._refreshing = True
        self._last_refresh_ts = time.time()

        def _safe(fn, default):
            try:
                return fn()
            except Exception:
                _safe_traceback()
                return default

        def job():
            errors = []
            try:
                alive = _safe(_check_alive, False)
                pid = _safe(lambda: _get_pid() if alive else 0, 0)
                paused = os.path.exists(PAUSE_FLAG)
                log_text = _safe(_tail_log, "")
                ollama_alive = _safe(_ollama_alive, False)
                ollama_pid = _safe(lambda: _get_ollama_pid() if ollama_alive else 0, 0)
                health = _safe(_fetch_health, "—")
                stats = _safe(_read_db_stats, {"characters": None, "memories": None, "tokens": None})
                stats["heat"] = _safe(lambda: _read_token_heatmap(HEATMAP_WEEKS), [])
                self._q.put(("state", alive, pid, paused, log_text, ollama_alive, ollama_pid, health, stats))
            except Exception as e:
                _safe_traceback()
                errors.append(str(e))
            finally:
                self._refreshing = False
            if errors:
                self._q.put(("msg", "刷新异常: %s" % errors[0]))

        threading.Thread(target=job, daemon=True).start()
        return True

    def _start_catchup(self, target) -> None:
        if self._catchup_after_id is not None:
            try:
                self.root.after_cancel(self._catchup_after_id)
            except Exception:
                pass
        self._catchup_after_id = None
        self._catchup_left = CATCHUP_N
        self._catchup_target = target
        self._catchup_after_id = self.root.after(CATCHUP_MS, self._catchup_tick)

    def _catchup_tick(self) -> None:
        self._catchup_after_id = None
        if self._catchup_left <= 0:
            self._catchup_target = None
            return
        started = self._do_refresh()
        if started:
            self._catchup_left -= 1
        if self._catchup_target_reached():
            self._catchup_left = 0
            self._catchup_target = None
            return
        if self._catchup_left > 0:
            self._catchup_after_id = self.root.after(CATCHUP_MS, self._catchup_tick)

    def _catchup_target_reached(self) -> bool:
        t = self._catchup_target
        if not t:
            return False
        if t == "running":
            return _check_alive()
        if t == "stopped":
            return not _check_alive()
        if t == "ollama_running":
            return _ollama_alive()
        if t == "ollama_stopped":
            return not _ollama_alive()
        return False

    # ── 日志跟随 ──

    def _refresh_log_tick(self) -> None:
        if self._nav != "log":
            return
        log_text = _tail_log()
        if log_text != self._last_log:
            self._last_log = log_text
            self._update_log(log_text)
        self._log_after_id = self.root.after(LOG_FOLLOW_MS, self._refresh_log_tick)

    def _stop_log_timer(self) -> None:
        if self._log_after_id is not None:
            try:
                self.root.after_cancel(self._log_after_id)
            except Exception:
                pass
        self._log_after_id = None

    def _on_log_autoscroll(self) -> None:
        if self.log_autoscroll_var.get():
            self._update_log(self._last_log, force_scroll=True)

    def export_log(self) -> None:
        default_name = "ambrace-log-%s.txt" % datetime.now().strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出日志", defaultextension=".txt",
            initialfile=default_name, filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_tail_log())
            self._set_msg("日志已导出：%s" % path)
        except Exception as e:
            self._set_msg("导出失败：%s" % e)

    def clear_log_display(self) -> None:
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.config(state="disabled")
        self._set_msg("已清空显示（下次刷新恢复）")

    def _update_last_refresh(self) -> None:
        if not hasattr(self, "last_refresh_label"):
            return
        elapsed = time.time() - self._last_refresh_ts
        if self._last_refresh_ts <= 0 or elapsed < 2:
            text = "上次刷新 刚刚"
        elif elapsed < 60:
            text = "上次刷新 %d秒前" % int(elapsed)
        else:
            text = "上次刷新 %d分前" % int(elapsed // 60)
        self.last_refresh_label.config(text=text)

    # ── 趋势图 ──

    def _set_heat_mode(self, mode: str) -> None:
        self._heat_mode = mode
        self._hide_heat_tip()
        self._draw_heatmap()

    def _heat_cell_colors(self):
        t = self.theme
        return [t.surface_alt,
                _hex_mix(t.card, t.accent, .30),
                _hex_mix(t.card, t.accent, .55),
                _hex_mix(t.card, t.accent, .78),
                t.accent]

    def _draw_heatmap(self) -> None:
        if not hasattr(self, "heat_canvas"):
            return
        t = self.theme
        c = self.heat_canvas
        c.delete("all")
        self._heat_cells = {}
        days = self._heat_days or []
        # 必须使用物理尺寸：画布尚未映射（≈1px）时不硬画，否则格子会画到可视区外而“消失”
        w, h = c.winfo_width(), c.winfo_height()
        if w < 60 or h < 40:
            if days and getattr(self, "_heat_retry", 0) < 30:
                self._heat_retry = getattr(self, "_heat_retry", 0) + 1
                self.root.after(120, self._draw_heatmap)
            return
        if not days:
            c.create_text(w / 2, h / 2, text="暂无数据", fill=t.text_muted, font=(FONT, 11))
            return
        self._heat_retry = 0
        vals = _heat_series(days, self._heat_mode)
        levels = _heat_levels(vals)
        self._heat_series_vals, self._heat_levels = vals, levels
        left, top, bottom = 30, 8, 22
        ncols = math.ceil(len(days) / 7)
        avail_w = w - left - 6
        # 格子同时受列宽/行高约束，设下限保证可见、设上限避免过大；17 列天然偏窄，左对齐
        cell = max(9, min(avail_w / ncols, (h - top - bottom) / 7, 36))
        gap = max(2, cell * .12)
        size = cell - gap
        x0 = left
        colors = self._heat_cell_colors()
        show_week = cell >= 11
        if show_week:
            for r, name in ((0, "周一"), (2, "周三"), (4, "周五")):
                c.create_text(x0 - 6, top + r * cell + cell / 2, anchor="e",
                              text=name, fill=t.text_muted, font=(FONT, 8))
        prev_month = None
        for i, d in enumerate(days):
            col, row = i // 7, i % 7
            x = x0 + col * cell + gap / 2
            y = top + row * cell + gap / 2
            mon = int(d["date"][5:7])
            if row == 0 and mon != prev_month:
                c.create_text(x, h - 8, anchor="w", text=f"{mon}月",
                              fill=t.text_muted, font=(FONT, 8))
                prev_month = mon
            pts = _rr_points(x, y, x + size, y + size, 3)
            if d["future"]:
                c.create_polygon(pts, smooth=True, splinesteps=8, fill="",
                                 outline=t.hairline, tags=("cell", str(i)))
            else:
                c.create_polygon(pts, smooth=True, splinesteps=8,
                                 fill=colors[levels[i]], outline="",
                                 tags=("cell", str(i)))
            self._heat_cells[i] = (x, y, size)

    def _on_heat_motion(self, e) -> None:
        c = self.heat_canvas
        cur = c.find_withtag("current")
        idx = None
        if cur:
            for tg in c.gettags(cur[0]):
                if tg.isdigit():
                    idx = int(tg)
                    break
        if idx is None or not self._heat_days:
            self._hide_heat_tip()
            return
        self._show_heat_tip(idx, e.x_root, e.y_root)

    def _heat_tip_text(self, idx: int) -> str:
        d = self._heat_days[idx]
        vals = self._heat_series_vals or _heat_series(self._heat_days, self._heat_mode)
        v = vals[idx] if idx < len(vals) else 0
        period_total = sum(x["tokens"] for x in self._heat_days) or 1
        peak = max((x["tokens"] for x in self._heat_days), default=0) or 1
        y, m, dd = int(d["date"][:4]), int(d["date"][5:7]), int(d["date"][8:10])
        wd = WEEK_CN[date(y, m, dd).weekday()]
        if self._heat_mode == "week":
            head = f"{m}月第 {idx // 7 + 1} 周 · 本周合计"
        elif self._heat_mode == "cum":
            head = f"{m}/{dd} {wd} · 截至当日累计"
        else:
            head = f"{m}/{dd} {wd}"
        if d["future"]:
            return f"{head}\n（未到）"
        lines = [head, f"{v:,} Token"]
        if self._heat_mode != "cum" and v > 0:
            lines.append(f"占区间 {v / period_total * 100:.1f}% · 峰值的 {v / peak * 100:.0f}%")
        # Aurora：当日/当周任务类型构成（top5），由悬浮窗承载原占比卡
        pool = {}
        if self._heat_mode == "week":
            for r in range(7):
                j = (idx // 7) * 7 + r
                if j < len(self._heat_days):
                    for lb, tv in (self._heat_days[j].get("tasks") or {}).items():
                        pool[lb] = pool.get(lb, 0) + tv
        elif self._heat_mode == "day":
            pool = dict(d.get("tasks") or {})
        if pool:
            bk = _fold_topn(sorted(
                [{"label": k, "tokens": v} for k, v in pool.items()],
                key=lambda x: x["tokens"], reverse=True), 5)
            pt = sum(x["tokens"] for x in bk) or 1
            seg = "  ".join(f"{x['label']} {x['tokens'] / pt * 100:.0f}%"
                            for x in bk if x["tokens"] > 0)
            if seg:
                lines.append(seg)
        return "\n".join(lines)

    def _show_heat_tip(self, idx, x_root, y_root) -> None:
        text = self._heat_tip_text(idx)
        if self._heat_tip is None:
            tip = tk.Toplevel(self.root)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            lab = tk.Label(tip, text=text, justify="left", bg="#20242F", fg="#E8EAF0",
                           font=(FONT, 9), padx=8, pady=5, bd=0)
            lab.pack()
            self._heat_tip = (tip, lab)
        tip, lab = self._heat_tip
        lab.config(text=text)
        tip.geometry(f"+{x_root + 14}+{y_root + 14}")
        tip.deiconify()

    def _hide_heat_tip(self) -> None:
        if self._heat_tip is not None:
            try:
                self._heat_tip[0].withdraw()
            except Exception:
                pass

    # ── 应用刷新 ──

    def _update_log(self, log_text: str, force_scroll=None) -> None:
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", log_text)
        if force_scroll or self.log_autoscroll_var.get():
            self.log_box.see("end")
        self.log_box.config(state="disabled")

    def _apply_refresh(self, alive: bool, pid: int, paused: bool, log_text: str,
                       ollama_alive: bool, ollama_pid: int, health: str, stats: dict):
        t = self.theme
        self._alive = alive
        self._ollama_alive = ollama_alive
        self._sync_button_states()
        self._update_last_refresh()
        self._heat_days = stats.get("heat") or []
        try:
            self._draw_heatmap()
        except Exception:
            _safe_traceback()
        if ollama_alive:
            self.ollama_dot._paint_icon(t.success)
            self.ollama_pid_label.config(
                text="运行中（远程主机）" if _is_remote() else "运行中  (PID %d)" % ollama_pid)
            self.v_ollama.config(text="运行中", fg=t.success)
            self.c_ollama.config(
                text=("远程 %s" % TARGET_HOST) if _is_remote() else ("PID %d" % ollama_pid))
        else:
            self.ollama_dot._paint_icon(t.error)
            self.ollama_pid_label.config(text="未运行")
            self.v_ollama.config(text="未运行", fg=t.text_sec)
            self.c_ollama.config(text="端口 11434")

        if alive:
            self.header_status_label.config(text="运行中")
            self.v_server.config(text="运行中", fg=t.success)
            self.c_server.config(
                text=("远程 %s:%d" % (TARGET_HOST, TARGET_PORT)) if _is_remote()
                     else ("PID %d · 端口 %d" % (pid, TARGET_PORT)))
            self.server_pid_label.config(
                text="远程主机" if _is_remote() else "PID %d" % pid)
        else:
            self.header_status_label.config(text="已停止")
            self.v_server.config(text="已停止", fg=t.error)
            self.c_server.config(text="端口 %d" % TARGET_PORT)
            self.server_pid_label.config(text="")
        self.header_port_label.config(
            text=("目标 %s:%d" % (TARGET_HOST, TARGET_PORT)) if _is_remote()
                 else ("端口 %d" % TARGET_PORT))
        health_fg = t.success if health == "正常" else (t.warning if health == "—" else t.error)
        self.header_health_label.config(text="健康 %s" % health, fg=health_fg)
        self.v_health.config(text=health, fg=health_fg)
        self.v_chars.config(text=_fmt_int(stats.get("characters")))
        self.v_mems.config(text=_fmt_int(stats.get("memories")))
        self.v_tokens.config(text=_fmt_int(stats.get("tokens")))
        if _is_remote():
            self.c_chars.config(text="远程不统计")
            self.c_mems.config(text="远程不统计")
            self.c_tokens.config(text="远程不统计")
        else:
            self.c_chars.config(text="个角色")
            self.c_mems.config(text="条记忆")
            self.c_tokens.config(text="Token 累计")

        self._set_msg("守护已暂停（watchdog 不自动拉起）" if paused else "就绪")

        if log_text != self._last_log:
            self._last_log = log_text
            self._update_log(log_text)

    # ── 状态灯呼吸 ──

    def _pulse_tick(self):
        self._pulse_phase = (self._pulse_phase + 1) % 60
        if self._alive:
            t = self.theme
            ratio = (math.sin(self._pulse_phase / 60.0 * 2 * math.pi) + 1) / 2
            # 呼吸脉冲取主题 token（Aurora=teal 呼吸；dark/light=绿色系）
            hi = getattr(self.theme, "pulse_hi", "#34D399")
            lo = getattr(self.theme, "pulse_lo", "#0F3D2E")
            hr, hg, hb = int(hi[1:3], 16), int(hi[3:5], 16), int(hi[5:7], 16)
            lr, lg, lb = int(lo[1:3], 16), int(lo[3:5], 16), int(lo[5:7], 16)
            r = int(lr + (hr - lr) * ratio)
            g = int(lg + (hg - lg) * ratio)
            b = int(lb + (hb - lb) * ratio)
            try:
                self.header_dot._paint_icon(f"#{r:02x}{g:02x}{b:02x}")
            except Exception:
                pass
        else:
            try:
                self.header_dot._paint_icon(self.theme.error)
            except Exception:
                pass
        self.root.after(80, self._pulse_tick)


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    try:
        dpi = root.winfo_fpixels("1i")
        root.tk.call("tk", "scaling", dpi / 72.0)
    except Exception:
        pass
    ControllerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
