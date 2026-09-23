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
import webbrowser
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

# C1（2026-09-17）：stdio 日志的「启动前轮转」，实现位于 backend 的兄弟目录 scripts/log_rotate.py。
# 注意：控制台以 server_controller/ 为 sys.path[0]（与 server_manager/watchdog 以 scripts/ 为
# sys.path[0] 不同），直接同级 import 会 ImportError，故先把 scripts/ 显式加入 sys.path。
# 坑：本文件 SERVER_DIR 指 backend/（scripts/ 在项目根下），必须用 PROJECT_ROOT 拼，不能用 SERVER_DIR。
# P3-9（2026-09-2x）：跨平台拉起键 / venv 解释器路径也收敛到同级 scripts/platform_util.py，
# 与 log_rotate 共用这次 sys.path 注入。
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from log_rotate import rotate_stdio_log  # noqa: E402
import platform_util  # noqa: E402
# V1 设计系统基座（同目录模块）：字号阶梯 / 间距栅格 / 图标与照片渲染器
import console_ui as CUI  # noqa: E402

# B6/P3-9：Windows 用 .venv/Scripts/pythonw.exe（GUI 子系统、无控制台窗口）+ python.exe，
# POSIX 用 .venv/bin/python（无 pythonw，两个入口同路径）；拉起键 Windows = creationflags、
# POSIX = start_new_session。统一由 platform_util 提供（原先 server_controller 各写一份）。
PYTHONW, PYTHON = platform_util.venv_python_paths(SERVER_DIR)
PAUSE_FLAG = os.path.join(SERVER_DIR, "data", "paused.flag")
APP_LOG = os.path.join(SERVER_DIR, "data", "logs", "app.log")
STDERR_LOG = os.path.join(SERVER_DIR, "data", "logs", "server_stderr.log")
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
# 隐藏短命子进程（PowerShell）窗口；POSIX 上该属性不存在 -> 0。
# P3-9 复核修正（2026-09-19，Codex）：保留 CREATE_NEW_PROCESS_GROUP 位，与收敛前逐位一致。
NO_WINDOW = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


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


# 字体族单一真源在 console_ui.FONT_UI。这里曾经是 "Segoe UI"：中文靠系统回退字体渲染，
# 量宽用 Segoe 的回退、绘制用另一个字体，按钮文字宽度会算不准（V3 统一后不再有两套族名）
FONT = CUI.FONT_UI


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
        # 圆角半径也按 DPI 走：卡片整体变大后还留 16px 的角，看起来像没做完
        self.radius = CUI.px(radius or theme.radius)
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
        h = self.inner.winfo_reqheight() + 2 * self.pad
        mgr = self.winfo_manager()   # grid 出来的卡片没有 pack_info，直接问会抛 TclError
        if mgr == "pack" and "x" in str(self.pack_info().get("fill", "")).lower():
            # 横向被 pack 拉满 → 宽度由容器决定，这里只按内容长高，
            # 否则卡片会缩成"内容请求宽"，整行工具条就塌了
            w = self.winfo_reqwidth()
        else:
            w = self.inner.winfo_reqwidth() + 2 * self.pad
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
        # height / 内边距都是 100% 基准值，这里统一换成物理像素（字体 Tk 已经自己缩过了，
        # 按钮框不跟着缩就会出现"字撑破按钮"）
        self._h = CUI.px(height)
        self._fs = font_size
        self._bold = bold
        # 根据文字自动计算宽度
        if width is None:
            import tkinter.font as tkfont
            fn = tkfont.Font(family=FONT, size=font_size, weight="bold" if bold else "normal")
            width = fn.measure(text) + CUI.px(32)  # 左右各 16（基准）内边距
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, height=self._h,
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
        # 宽高都是 100% 基准值：标签字号 Tk 会自动缩放，分段控件的框不跟着缩就会被字撑破
        self._w0, self._h = CUI.px(width), CUI.px(height)
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0,
                         width=self._w0, height=self._h)
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
                             font=CUI.f("micro", i == self.idx))


# ═══════════════════════════════════════════════════════════════
# 矢量图标
# ═══════════════════════════════════════════════════════════════

def _make_icon(parent, size, kind, color, bg):
    """图标工厂（V1）：Lucide 纯白 PNG → 按主题染色 → 缓存 PhotoImage。

    契约与旧版逐字保持一致（返回 Canvas、带 _paint_icon(color)、可 config(bg=...)），
    所以 _select_page / _nav_hover 等调用方一行都不用改。
    画布尺寸用物理像素（CUI.px），否则高分屏下 21px 的图会被 16px 的画布裁掉。
    未跑 build_icons.py（素材缺失）时退回旧手绘几何，不会渲染成空白。
    """
    name = CUI.ICON_MAP.get(kind, kind)
    box = CUI.px(size)
    c = tk.Canvas(parent, width=box, height=box, bg=bg, highlightthickness=0, bd=0)

    def paint(col):
        c.delete("all")
        ph = CUI.ICONS.get(name, size, col)
        if ph is not None:
            c._icon_ph = ph  # 持有引用，防 Tk 图像被 Python 侧 GC
            c.create_image(box / 2, box / 2, image=ph, anchor="center")
            return c
        _paint_icon_fallback(c, size, kind, col)
        return c

    paint(color)
    c._paint_icon = paint
    return c


def _paint_icon_fallback(c, size, kind, col):
    """无图标素材时的几何兜底（V1 之前的画法，仅保证不空白）。"""
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
        subprocess.Popen([PYTHON, MANAGER_PY, cmd], **platform_util.popen_kwargs())
    except Exception as e:
        raise RuntimeError("执行失败: {0}".format(e))


def _start_uvicorn():
    try:
        os.makedirs(os.path.dirname(STDERR_LOG), exist_ok=True)
        # C1：打开重定向句柄之前轮转（控制台侧无 log()，直接打印即可）
        _rot = rotate_stdio_log(STDERR_LOG, max_mb=10, keep=2)
        if _rot:
            print(_rot)
        with open(STDERR_LOG, "a", encoding="utf-8") as f:
            subprocess.Popen(
                [PYTHONW, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", str(TARGET_PORT)],
                cwd=SERVER_DIR, stdout=f, stderr=subprocess.STDOUT, **platform_util.popen_kwargs())
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
        subprocess.Popen([exe, "serve"], env=env, **platform_util.popen_kwargs())
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


def _log_read_path(path: str) -> str:
    """C1：启动前轮转后当前日志为空（或尚未重建）时优先读 .1。
    否则控制台日志区会显示一片空白，容易被误判成「服务启动失败」。"""
    alt = path + ".1"
    try:
        if os.path.exists(alt) and (not os.path.isfile(path) or os.path.getsize(path) == 0):
            return alt
    except OSError:
        pass
    return path


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
    # C1：server_stderr.log 刚被轮转过（当前为空）时回退读 .1，避免日志区空白
    parts = [p for p in (tail(APP_LOG), tail(_log_read_path(STDERR_LOG))) if p.strip()]
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


def _fmt_ago(sec) -> str:
    """秒数 →「X 单位前」人读格式（不足 1 分钟用秒，其余用分/时）。"""
    try:
        s = max(0, int(sec))
    except Exception:
        return "—"
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def _fetch_liveness() -> dict:
    """读取运行期活性（/api/v1/system/liveness），返回 {ok, stalled, summary, level}。

    - ok=False：端点不可用/网络失败 → 下游回落现状（summary 空，不改健康卡语义）。
    - level：0=正常(绿)、1=停滞预警(黄，如 scheduler 心跳超阈值)、2=停滞(红，端点 reported stalled)。
    - summary 例：「调度 12s 前 · 微信桥 3s 前」；微信桥取启用绑定最近 in/out 的最小间隔。
    """
    if not _check_alive():
        return {"ok": False, "stalled": False, "summary": "", "level": 0}
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(_target_base() + "/api/v1/system/liveness", timeout=2.5) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "stalled": False, "summary": "", "level": 0}
    stalled = bool(data.get("stalled"))
    loops = data.get("loops") or {}
    sched = loops.get("scheduler") or {}
    sched_secs = sched.get("seconds_since_heartbeat")
    sched_stalled = bool(sched.get("stalled"))
    ch = data.get("channels") or {}
    wx = ch.get("wechat_ilink") or {}
    wx_secs = None
    for b in (wx.get("bindings") or []):
        if not b.get("enabled"):
            continue
        for v in (b.get("seconds_since_inbound"), b.get("seconds_since_outbound")):
            if v is not None and (wx_secs is None or v < wx_secs):
                wx_secs = v
    parts = []
    if sched_secs is not None:
        parts.append("调度 %s 前" % _fmt_ago(sched_secs))
    if wx_secs is not None:
        parts.append("微信桥 %s 前" % _fmt_ago(wx_secs))
    level = 2 if stalled else (1 if sched_stalled else 0)
    return {"ok": True, "stalled": stalled, "summary": " · ".join(parts), "level": level}


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
# Token 热力图 / 任务占比（近 26 周；HEATMAP_WEEKS=26，与标题一致）
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
# 管理面 HTTP 客户端（账号独立 P2，2026-09-19）
#
# 契约 §0 铁律：控制台只调 HTTP API，禁止直连 DB；管理逻辑全部落在后端服务层。
# 统一前缀 /api/v1/admin/server，登录走 POST /api/v1/auth/login 拿 JWT，
# 缓存在 backend/data/console_token.json（该文件含凭据，不入 git）。
#
# 线程约定：以下函数一律在后台线程里被调用（见 ControllerApp._run_admin），
# 任何网络/解析异常都在本层收敛成 AdminApiError，绝不向外抛到 Tk 主循环。
# ═══════════════════════════════════════════════════════════════

CONSOLE_TOKEN_FILE = os.path.join(SERVER_DIR, "data", "console_token.json")
ADMIN_API_PREFIX = "/api/v1/admin/server"
AUTH_LOGIN_PATH = "/api/v1/auth/login"
ADMIN_TIMEOUT = 6.0
# 模态键顺序（契约 §1.2：key ∈ llm|image|vlm|speech|multimodal），仅用于展示排序
MODALITY_ORDER = ("llm", "multimodal", "image", "vlm", "speech")
ACCOUNT_LLM_MODES = ("own", "default_allowed", "blocked")
# 表格里显示中文、行操作菜单也用这一份文案（旧版直接抛 own/default_allowed/blocked 裸值）
LLM_MODE_TEXT = {"own": "仅用自己的配置", "default_allowed": "可用服务器默认",
                 "blocked": "禁用服务器默认"}
# 注册策略（契约 §1.4：mode ∈ open|invite_only|closed；文案仅展示，拦截行为以后端为准）
REGISTRATION_MODES = (("open", "开放", "任何人都能注册新账号（现状默认）"),
                      ("invite_only", "仅邀请码", "注册需要邀请码"),
                      ("closed", "关闭", "注册端点直接返回 403"))
REGISTRATION_MODE_LABELS = {m: label for m, label, _desc in REGISTRATION_MODES}
# 概览字段（契约 §1.6，只读展示；后端缺哪个字段就显示 —，控制台不做任何推算）
OVERVIEW_FIELDS = (("accounts", "账号总数"),
                  ("disabled", "已禁用账号"),
                  ("server_admins", "控制台管理员"),
                  ("flags_on", "开启的开关"),
                  ("version", "后端版本"))
# A8（2026-09-20）LLM 额度：source → 中文来源标签（控制台是内部工具，文案不做 i18n）
LLM_LIMIT_SOURCE_LABELS = {"user": "账号覆盖", "global": "全局", "unset": "未设置"}

# X7-M4e-2 行动通道页（控制台）：三条开关的键名 / 中文名 / 「读不到这一行」时的缺省方向。
# 缺省方向照抄后端 app/device/actions.py（device_actions_enabled 与 device_actions_plugin_enabled
# 缺行＝关、device_actions_force_dry_run 缺行＝开）；生效值一律以后端返回为准，控制台不做推算。
DEVICE_ACTION_SWITCHES = (("global", "全局行动开关", "关"),
                          ("plugin_enabled", "插件行动通道", "关"),
                          ("force_dry_run", "插件强制干跑", "开"))
DEVICE_ACTION_LABELS = {key: label for key, label, _default in DEVICE_ACTION_SWITCHES}
# 两份名单：页面区块键 → 契约字段名 / 中文名词（§1：targets 用 target，plugins 用 plugin）
DEVICE_ACTION_FIELDS = {"targets": "target", "plugins": "plugin"}
DEVICE_ACTION_NOUNS = {"targets": "包名", "plugins": "插件名"}
DEVICE_ACTION_SECTIONS = (("targets", "目标白名单", "应用包名，如 com.example.app"),
                          ("plugins", "插件灰度名单", "插件名，如 browser_mcp"))


def _llm_limit_text(row) -> str:
    """账号额度展示：『生效值（来源）』；无生效额度显示「未设置」。

    只做展示，不做任何推算：生效值/来源一律取后端给的 llm_total_limit / llm_total_limit_source。
    """
    src = str(row.get("llm_total_limit_source") or "")
    val = row.get("llm_total_limit")
    if src == "unset" or not val:
        return "未设置"
    return "%s（%s）" % (val, LLM_LIMIT_SOURCE_LABELS.get(src, src))


class AdminApiError(Exception):
    """管理面请求失败。

    code：0=网络不可达/响应不可解析，401/403=登录态或权限问题，
    404=后端接口未就绪（另一端在并行开发），其余为后端原样返回的状态码。
    """

    def __init__(self, message, code=0, path=""):
        Exception.__init__(self, message)
        self.message = message
        self.code = code
        self.path = path


_CONSOLE_SESSION = {}


def _console_token_read() -> dict:
    """读取缓存登录态 {token, username, user_id}；文件缺失/损坏时返回空 dict。"""
    try:
        with open(CONSOLE_TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("token"):
            return data
    except Exception:
        pass
    return {}


def _console_session() -> dict:
    """当前会话（内存优先，首次访问时从 console_token.json 恢复）。"""
    global _CONSOLE_SESSION
    if not _CONSOLE_SESSION:
        _CONSOLE_SESSION = _console_token_read()
    return _CONSOLE_SESSION


def _console_login_state() -> str:
    """已登录用户名；未登录返回空串。"""
    s = _console_session()
    return str(s.get("username") or "") if s.get("token") else ""


def _console_set_session(token: str, username: str = "", user_id=None) -> None:
    global _CONSOLE_SESSION
    _CONSOLE_SESSION = {"token": token, "username": username, "user_id": user_id}
    payload = dict(_CONSOLE_SESSION)
    payload["saved_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(CONSOLE_TOKEN_FILE), exist_ok=True)
        with open(CONSOLE_TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # 落盘失败只意味着下次启动要重新登录，不影响本次会话
        _safe_traceback()


def _console_clear_session() -> None:
    global _CONSOLE_SESSION
    _CONSOLE_SESSION = {}
    try:
        if os.path.exists(CONSOLE_TOKEN_FILE):
            os.remove(CONSOLE_TOKEN_FILE)
    except Exception:
        _safe_traceback()


def _http_json(method: str, path: str, body=None, token: str = "", timeout: float = ADMIN_TIMEOUT):
    """发一个 JSON HTTP 请求，返回 (status, parsed)。网络失败抛 AdminApiError(code=0)。

    非 2xx 不抛（HTTPError 的响应体也要读出来，后端可读提示在 detail 里）；
    响应体不是 JSON 时返回空 dict，由上层按状态码给文案。
    """
    import urllib.error
    import urllib.request
    url = _target_base() + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + str(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = int(getattr(r, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        status = int(e.code or 0)
        try:
            raw = e.read()
        except Exception:
            raw = b""
    except Exception as e:
        raise AdminApiError("无法连接后端 %s（%s）" % (url, e), code=0, path=path)
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        parsed = {}
    return status, parsed


def _err_text(parsed, default: str = "") -> str:
    """从 FastAPI 响应里取可读 detail（detail 可能是 str 或 dict）。"""
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:200]
        if isinstance(detail, dict):
            for k in ("message", "detail", "msg"):
                v = detail.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:200]
    return default


def _admin_request(method: str, sub: str, body=None) -> dict:
    """管理面统一请求（自动带 Bearer）。成功返回 dict，失败抛 AdminApiError。

    语义化状态码（前端不做业务规则，只翻译后端返回）：
    - 无 token → 401（不发请求）
    - 401 → 清会话（token 过期/失效）
    - 403 → 保留会话：可能是非 server_admin，也可能是开关被服务器锁定
    - 404/405 → 接口未就绪（后端另一路在并行开发）
    """
    path = ADMIN_API_PREFIX + sub
    token = str(_console_session().get("token") or "")
    if not token:
        raise AdminApiError("未登录或权限不足，请重新登录", code=401, path=path)
    status, data = _http_json(method, path, body=body, token=token)
    if status == 401:
        _console_clear_session()
        raise AdminApiError("未登录或权限不足，请重新登录", code=401, path=path)
    if status == 403:
        raise AdminApiError(_err_text(data, "未登录或权限不足，请重新登录"), code=403, path=path)
    if status in (404, 405):
        raise AdminApiError("接口未就绪：%s %s" % (method, path), code=404, path=path)
    if not (200 <= status < 300):
        raise AdminApiError("后端返回 %d：%s" % (status, _err_text(data, str(data)[:200])),
                            code=status, path=path)
    return data if isinstance(data, dict) else {"data": data}


def _admin_login(username: str, password: str) -> dict:
    """POST /api/v1/auth/login，成功则缓存 JWT 到 console_token.json。"""
    status, data = _http_json("POST", AUTH_LOGIN_PATH,
                              body={"username": username, "password": password})
    if status in (401, 403):
        raise AdminApiError(_err_text(data, "用户名或密码不正确"), code=status, path=AUTH_LOGIN_PATH)
    if status == 429:
        raise AdminApiError(_err_text(data, "尝试次数过多，请稍后再试"), code=status, path=AUTH_LOGIN_PATH)
    if not (200 <= status < 300):
        raise AdminApiError("登录失败（后端返回 %d）：%s" % (status, _err_text(data, str(data)[:200])),
                            code=status, path=AUTH_LOGIN_PATH)
    token = str(data.get("access_token") or "") if isinstance(data, dict) else ""
    if not token:
        raise AdminApiError("登录响应缺少 access_token", code=0, path=AUTH_LOGIN_PATH)
    _console_set_session(token, str(data.get("username") or username), data.get("user_id"))
    return data


def _sorted_modalities(rows) -> list:
    """按契约模态顺序排序，未知键排在后面（后端新增模态不需要改前端）。"""
    def _rank(r):
        k = str(r.get("key") or "")
        return (MODALITY_ORDER.index(k), k) if k in MODALITY_ORDER else (99, k)
    try:
        return sorted([r for r in (rows or []) if isinstance(r, dict)], key=_rank)
    except Exception:
        return list(rows or [])


def _fmt_audit_val(v, limit: int = 46) -> str:
    """审计「前/后值」摘要：dict/list 压成 k=v; k=v，密钥类字段打码，超长截断。"""
    if v is None or v == "" or v == {} or v == []:
        return "—"
    s = ""
    try:
        if isinstance(v, dict):
            parts = []
            for k, val in v.items():
                lk = str(k).lower()
                if any(t in lk for t in ("api_key", "token", "password", "secret")):
                    val = "***" if val else "—"
                parts.append("%s=%s" % (k, val))
            s = "; ".join(parts)
        elif isinstance(v, (list, tuple)):
            s = "; ".join(str(x) for x in v)
        else:
            s = str(v)
    except Exception:
        s = str(v)
    s = s.replace("\n", " ").strip()
    if not s:
        return "—"
    return s[:limit] + "…" if len(s) > limit else s


def _clear_frame(frame) -> None:
    """清空容器内子控件（列表重渲染用，主线程调用）。"""
    try:
        for child in frame.winfo_children():
            child.destroy()
    except Exception:
        _safe_traceback()


def _fmt_dt(v) -> str:
    """后端 UTC naive 时间串 → 北京时间展示（只截断到分钟，解析失败原样显示）。"""
    s = str(v or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "").replace("/", "-"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except Exception:
        return s.replace("T", " ")[:16]


def _registration_mode_text(mode) -> str:
    """mode → 「开放（open）」；未知值原样显示，未返回时给联调提示（不做业务推断）。"""
    m = str(mode or "").strip()
    if not m:
        return "后端未返回 mode"
    label = REGISTRATION_MODE_LABELS.get(m)
    return "%s（%s）" % (label, m) if label else "未知策略（%s）" % m


# ═══════════════════════════════════════════════════════════════
# 主应用
# ═══════════════════════════════════════════════════════════════

class ControllerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._q: "queue.Queue[tuple]" = queue.Queue()
        # 管理面（账号独立 P2）：页内控件登记表 + 请求串行闸门
        self._admin_meta = {}
        self._admin_busy = False
        self._admin_login_tip = None
        self._admin_login_pwd_var = None

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
        # V1：把 DPI 缩放与等宽族名交给设计系统（间距与图标按物理像素出图，Tk 不会自动缩放图片）
        CUI.set_scale(scale)
        CUI.init_fonts(root)
        # 间距常量按 DPI 重绑：SP_* 是 100% 下的基准值，而 Tk 只自动缩放字号、
        # 不缩放 padx/pady——150% 机器上不重绑就是"字大间距小"，整页挤成一团
        global SP_XXS, SP_XS, SP_SM, SP_MD, SP_LG, SP_XL
        SP_XXS, SP_XS, SP_SM, SP_MD, SP_LG, SP_XL = (
            CUI.sp("xxs"), CUI.sp("xs"), CUI.sp("sm"),
            CUI.sp("md"), CUI.sp("lg"), CUI.sp("xl"))
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
        header = tk.Frame(self.root, bg=t.sidebar, height=CUI.px(52))
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        brand = tk.Frame(header, bg=t.sidebar)
        brand.pack(side="left", padx=(SP_LG, 0), pady=SP_SM)
        _make_icon(brand, 18, "diamond", t.accent_glow, t.sidebar).pack(side="left")
        tk.Label(brand, text="拥爱服务器控制台", fg=t.text, bg=t.sidebar,
                 font=CUI.f("h2", True)).pack(side="left", padx=(SP_XS, 0))

        right = tk.Frame(header, bg=t.sidebar)
        right.pack(side="right", padx=SP_LG, pady=SP_SM)
        self.header_dot = _make_icon(right, 10, "dot", t.text_muted, t.sidebar)
        self.header_dot.pack(side="left")
        self.header_status_label = tk.Label(right, text="检测中…", fg=t.text, bg=t.sidebar, font=CUI.f("title", True))
        self.header_status_label.pack(side="left", padx=(SP_XS, SP_SM))
        self.header_port_label = tk.Label(
            right,
            text=("目标 %s:%d" % (TARGET_HOST, TARGET_PORT)) if _is_remote()
                 else ("端口 %d" % TARGET_PORT),
            fg=t.text_sec, bg=t.sidebar, font=CUI.f("body"))
        self.header_port_label.pack(side="left", padx=(0, SP_SM))
        self.header_health_label = tk.Label(right, text="健康 —", fg=t.text_sec, bg=t.sidebar, font=CUI.f("body"))
        self.header_health_label.pack(side="left", padx=(0, SP_MD))
        tk.Button(right, text="刷新", bg=t.sidebar, fg=t.accent_glow, activebackground=t.sidebar,
                  activeforeground=t.accent_glow, relief="flat", bd=0, font=CUI.f("body", True),
                  cursor="hand2", command=self._do_refresh).pack(side="left", padx=(0, SP_SM))
        tk.Button(right, text="渠道登录", bg=t.sidebar, fg=t.accent_glow, activebackground=t.sidebar,
                  activeforeground=t.accent_glow, relief="flat", bd=0, font=CUI.f("body", True),
                  cursor="hand2", command=self._open_channel_login).pack(side="left", padx=(0, SP_SM))
        tk.Button(right, text="设置", bg=t.sidebar, fg=t.accent_glow, activebackground=t.sidebar,
                  activeforeground=t.accent_glow, relief="flat", bd=0, font=CUI.f("body", True),
                  cursor="hand2", command=self.open_settings).pack(side="left")

        tk.Frame(self.root, height=1, bg=t.divider).pack(side="top", fill="x")

    def _open_channel_login(self) -> None:
        """打开本地渠道登录页（仅 127.0.0.1 可访问；P2 扫码绑定下放手机，2026-09-12）。"""
        self._set_msg(f"已在浏览器打开渠道登录页 http://127.0.0.1:{TARGET_PORT}/channel-login")
        threading.Thread(
            target=lambda: webbrowser.open(f"http://127.0.0.1:{TARGET_PORT}/channel-login"),
            daemon=True,
        ).start()

    def _build_statusbar(self) -> None:
        t = self.theme
        bar = tk.Frame(self.root, bg=t.sidebar, height=CUI.px(28))
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        tk.Frame(bar, width=1, bg=t.divider).pack(side="left", fill="y")
        self.msg = tk.Label(bar, text="就绪", fg=t.text_sec, bg=t.sidebar, font=CUI.f("caption"))
        self.msg.pack(side="left", padx=SP_MD)
        self.refresh_label = tk.Label(bar, text=f"刷新间隔 {_get_refresh_ms() // 1000}s",
                                      fg=t.text_muted, bg=t.sidebar, font=CUI.f("caption"))
        self.refresh_label.pack(side="right", padx=(0, SP_SM))
        self.last_refresh_label = tk.Label(bar, text="上次刷新 —", fg=t.text_muted, bg=t.sidebar, font=CUI.f("caption"))
        self.last_refresh_label.pack(side="right", padx=(0, SP_LG))

    def _build_body(self) -> None:
        t = self.theme
        body = tk.Frame(self.root, bg=t.bg)
        body.pack(side="top", fill="both", expand=True)

        sb_w = max(208, CUI.px(160))   # 侧栏宽：按 160 逻辑像素给，100% 下仍是 208 不变
        self._sidebar = tk.Frame(body, bg=t.sidebar, width=sb_w)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        tk.Frame(self._sidebar, width=1, bg=t.divider).pack(side="right", fill="y")

        self._content = tk.Frame(body, bg=t.bg)
        self._content.pack(side="left", fill="both", expand=True)

        self._nav_widgets = {}
        self._admin_meta = {}
        self._admin_login_tip = None
        # V1：9 个入口各给一枚专属图标（旧版 5 个形状复用，dashboard×3 / dot×4，等于没有图标）
        nav_items = [
            ("dashboard", "仪表盘", "layout-dashboard"),
            ("server", "服务器控制", "server"),
            ("log", "运行日志", "scroll-text"),
            ("models", "默认模型", "brain"),
            ("accounts", "账号管理", "users"),
            ("registration", "注册策略", "ticket"),
            ("flags", "开关与权限", "toggle-right"),
            ("device_actions", "行动通道", "check-circle-2"),
            ("audit", "审计", "clipboard-list"),
            ("overview", "概览", "gauge"),
        ]
        for key, label, icon in nav_items:
            item = tk.Frame(self._sidebar, bg=t.sidebar, cursor="hand2")
            item.pack(fill="x", padx=SP_SM, pady=2)
            bar = tk.Frame(item, width=3, bg=t.sidebar)
            bar.pack(side="left", fill="y")
            ic = _make_icon(item, 16, icon, t.text_sec, t.sidebar)
            ic.pack(side="left", padx=(SP_MD, SP_SM), pady=SP_SM)
            lb = tk.Label(item, text=label, bg=t.sidebar, fg=t.text_sec, font=CUI.f("title"))
            lb.pack(side="left", pady=SP_SM)
            for wid in (item, ic, lb, bar):
                wid.bind("<Button-1>", lambda e, k=key: self._select_page(k))
                wid.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                wid.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self._nav_widgets[key] = (item, ic, lb, bar)

        # 侧栏底部品牌画框（照片槽位）：后建、贴底，窗口矮的时候先挤它而不是挤导航
        plaque = CUI.photo_label(self._sidebar, t, "photo_sidebar_brand.jpg",
                                 sb_w - 1, CUI.px(110), bg=t.sidebar)
        if plaque is not None:
            plaque.pack(side="bottom", pady=(0, CUI.sp("sm")))

        self._pages = {
            "dashboard": self._build_dashboard_page(),
            "server": self._build_server_page(),
            "log": self._build_log_page(),
            "models": self._build_models_page(),
            "accounts": self._build_accounts_page(),
            "registration": self._build_registration_page(),
            "flags": self._build_flags_page(),
            "device_actions": self._build_device_actions_page(),
            "audit": self._build_audit_page(),
            "overview": self._build_overview_page(),
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
                lb.config(bg=t.accent_dim, fg=t.text, font=CUI.f("title", True))
            else:
                item.config(bg=t.sidebar)
                bar.config(bg=t.sidebar)
                ic.config(bg=t.sidebar)
                ic._paint_icon(t.text_sec)
                lb.config(bg=t.sidebar, fg=t.text_sec, font=CUI.f("title"))
        self._nav = key
        if key == "log":
            self._refresh_log_tick()
        else:
            self._stop_log_timer()
        self._maybe_load_admin_page(key)

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
        tk.Label(inner, text=title, fg=t.text_sec, bg=t.card, font=CUI.f("body")).pack(anchor="w")
        # KPI 数值走等宽族：数字宽度固定，卡片之间不再跳位
        value = tk.Label(inner, text="—", fg=t.text, bg=t.card,
                         font=CUI.f("num_xl", bold=True, num=True))
        value.pack(anchor="w", pady=(SP_XS, 2))
        cap = tk.Label(inner, text=caption, fg=t.text_muted, bg=t.card, font=CUI.f("caption"))
        cap.pack(anchor="w")
        tk.Frame(inner, bg=t.card).pack(fill="both", expand=True)
        return card, value, cap

    def _wrap_card(self, parent, height=None, fit=False, **pack_kw):
        """返回一个圆角卡片及其 inner Frame，供自由布局使用。

        `fit=True` 让卡片按内容长高——单行工具条一律走它：固定高度在 150% DPI 下
        必然把按钮裁掉（按钮框本身已经按 DPI 放大，卡片高度却是 100% 时代写死的）。
        `height=` 仍按 DPI 换算，只用于确实需要占位的块（如热力图卡）。
        """
        t = self.theme
        if fit:
            card = RoundedCard(parent, t, pad=3, fit_inner=True)
        elif height:
            card = RoundedCard(parent, t, pad=3, height=CUI.px(height))
        else:
            card = RoundedCard(parent, t, pad=3)
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
        tk.Label(pad, text="服务器概览", fg=t.text, bg=t.bg, font=CUI.f("h1", True)).pack(anchor="w")
        tk.Label(pad, text="运行状态、健康与数据规模一目了然", fg=t.text_muted, bg=t.bg,
                 font=CUI.f("body")).pack(anchor="w", pady=(2, SP_MD))

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

        # Token 活动卡：近 26 周热力图（每日/每周/累计），hover 查看当天用量与占比
        heat_card = RoundedCard(grid, t, pad=3, height=CUI.px(330))
        heat_card.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=3, pady=3)
        heat_inner = heat_card.inner
        heat_inner.config(padx=SP_LG, pady=SP_XS)
        heat_head = tk.Frame(heat_inner, bg=t.card)
        heat_head.pack(fill="x")
        tk.Label(heat_head, text="Token 活动（近 26 周 · 悬停看当日任务构成）",
                 fg=t.text_sec, bg=t.card,
                 font=CUI.f("body")).pack(side="left")
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
                                     height=CUI.px(168))
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

        tk.Label(pad, text="服务器控制", fg=t.text, bg=t.bg, font=CUI.f("h1", True)).pack(anchor="w")
        tk.Label(pad, text="启动 / 停止 / 重启核心服务", fg=t.text_muted, bg=t.bg,
                 font=CUI.f("body")).pack(anchor="w", pady=(2, SP_MD))

        _, inner = self._wrap_card(pad, fit=True, fill="x", pady=(0, SP_MD))
        inner.config(pady=SP_XS)
        btn_row = tk.Frame(inner, bg=t.card)
        btn_row.pack(fill="x")
        self.btn_start = RoundedButton(btn_row, t, "启动服务器", command=self.start_server, variant="primary")
        self.btn_start.pack(side="left", padx=(0, SP_XS))
        self.btn_stop = RoundedButton(btn_row, t, "停止服务器", command=self.stop_server, variant="danger")
        self.btn_stop.pack(side="left", padx=(0, SP_XS))
        self.btn_restart = RoundedButton(btn_row, t, "重启服务器", command=self.restart_server, variant="neutral")
        self.btn_restart.pack(side="left")
        self.server_pid_label = tk.Label(btn_row, text="", fg=t.text_sec, bg=t.card, font=CUI.f("title"))
        self.server_pid_label.pack(side="left", padx=SP_MD)

        # 图像理解服务（Ollama）：压缩为单行，与上方开关同一密度，不再独占整块
        _, ol = self._wrap_card(pad, fit=True, fill="x", pady=(0, SP_SM))
        ol.config(pady=SP_XS)
        ol_row = tk.Frame(ol, bg=t.card)
        ol_row.pack(fill="x")
        self.ollama_dot = _make_icon(ol_row, 14, "dot", t.text_muted, t.card)
        self.ollama_dot.pack(side="left")
        tk.Label(ol_row, text=OLLAMA_LABEL, fg=t.text, bg=t.card,
                 font=CUI.f("title", True)).pack(side="left", padx=(SP_XS, 0))
        self.ollama_pid_label = tk.Label(ol_row, text="", fg=t.text_sec, bg=t.card,
                                         font=CUI.f("body"))
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
        _, tgt = self._wrap_card(pad, fit=True, fill="x", pady=(0, SP_SM))
        tgt.config(pady=SP_XS)
        tgt_row1 = tk.Frame(tgt, bg=t.card)
        tgt_row1.pack(fill="x")
        tk.Label(tgt_row1, text="控制台监控目标", width=14, anchor="w", fg=t.text, bg=t.card,
                 font=CUI.f("title", True)).pack(side="left")
        tk.Label(tgt_row1, text="主机", fg=t.text_muted, bg=t.card, font=CUI.f("caption")).pack(side="left")
        self.target_host_var = tk.StringVar(value=TARGET_HOST)
        ttk.Entry(tgt_row1, textvariable=self.target_host_var, width=18).pack(side="left", padx=(4, SP_XS))
        tk.Label(tgt_row1, text="端口", fg=t.text_muted, bg=t.card, font=CUI.f("caption")).pack(side="left")
        self.target_port_var = tk.StringVar(value=str(TARGET_PORT))
        ttk.Entry(tgt_row1, textvariable=self.target_port_var, width=7).pack(side="left", padx=(4, SP_SM))
        RoundedButton(tgt_row1, t, "保存切换", command=self._save_target,
                      variant="primary", height=30, font_size=10).pack(side="left", padx=(0, SP_XS))
        RoundedButton(tgt_row1, t, "恢复本机", command=self._reset_target,
                      variant="neutral", height=30, font_size=10).pack(side="left")
        tgt_row2 = tk.Frame(tgt, bg=t.card)
        tgt_row2.pack(fill="x", pady=(SP_XS, 0))
        self.target_mode_label = tk.Label(tgt_row2, text="", anchor="w", fg=t.text_sec,
                                          bg=t.card, font=CUI.f("caption"))
        self.target_mode_label.pack(side="left")
        self._refresh_target_ui()

        # 服务器地址（从仪表盘迁到这里：本机 / 局域网 / Tailscale，可一键复制）
        _, addr_inner = self._wrap_card(pad, fit=True, fill="x")
        addr_inner.config(pady=SP_SM)
        addr_head = tk.Frame(addr_inner, bg=t.card)
        addr_head.pack(fill="x")
        tk.Label(addr_head, text="服务器地址（手机端「设置 → 服务器地址」填写）",
                 fg=t.text_sec, bg=t.card, font=CUI.f("body")).pack(side="left")
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
                     bg=t.card, font=CUI.f("caption")).pack(side="left")
            _v = tk.Label(_row, text="探测中…", anchor="w", fg=t.text,
                          bg=t.card, font=CUI.f("body", True))
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
        tk.Label(head, text="运行日志", fg=t.text, bg=t.bg, font=CUI.f("h1", True)).pack(side="left")
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
        # 空态画框：一行日志都还没有时居中压在文本区上（黑箱子里孤零零一行灰字很难看），
        # 首行内容到达即撤。用 place 而不是 pack——不能把 log_box 挤下去。
        self.log_empty = None
        self._log_empty_shown = None
        if CUI.PHOTOS.has("photo_empty_log.jpg"):
            holder = tk.Frame(pad, bg=t.log_bg)
            tile = CUI.photo_label(holder, t, "photo_empty_log.jpg", CUI.px(150), CUI.px(150))
            if tile is not None:
                tile.pack()
                tk.Label(holder, text="还没有日志写入（app.log / server_stderr.log）",
                         fg=t.text_sec, bg=t.log_bg, font=CUI.f("caption")
                         ).pack(pady=(CUI.sp("sm"), 0))
                self.log_empty = holder
        self._sync_log_empty(False)
        return page

    def _sync_log_empty(self, has_text: bool) -> None:
        """日志空态画框的显隐（状态相同则不动，避免每次刷新都重画一遍）。"""
        holder = getattr(self, "log_empty", None)
        if holder is None:
            return
        if self._log_empty_shown == (not has_text):
            return
        self._log_empty_shown = not has_text
        if self._log_empty_shown:
            holder.place(relx=0.5, rely=0.5, anchor="center")
        else:
            holder.place_forget()

    # ── 管理面（账号独立 P2）：公共骨架 ──

    def _make_admin_page(self, title: str, subtitle: str):
        """管理页容器：与仪表盘/服务器页同一纵向滚动范式，返回 (page, pad)。"""
        t = self.theme
        page = tk.Frame(self._content, bg=t.bg)
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

        def _on_leave(e, c=_scroll):
            c.unbind_all("<MouseWheel>")
        _scroll.bind("<Enter>", _on_enter)
        _scroll.bind("<Leave>", _on_leave)

        tk.Label(pad, text=title, fg=t.text, bg=t.bg, font=CUI.f("h1", True)).pack(anchor="w")
        tk.Label(pad, text=subtitle, fg=t.text_muted, bg=t.bg,
                 font=CUI.f("body")).pack(anchor="w", pady=(2, SP_MD))
        return page, pad

    def _admin_card(self, parent, **pack_kw):
        """自适应高度圆角卡片（列表内容长短不定，不钉死高度以免裁切）。"""
        t = self.theme
        card = RoundedCard(parent, t, pad=3, fit_inner=True)
        card.pack(**pack_kw)
        inner = card.inner
        inner.config(padx=SP_LG, pady=SP_SM)
        return card, inner

    def _admin_login_bar(self, pad, refresh_cb):
        """每页统一的登录态条（当前账号 + 刷新 + 登录/退出）+ 本页结果提示行。"""
        t = self.theme
        _, bar = self._admin_card(pad, fill="x", pady=(0, SP_SM))
        row = tk.Frame(bar, bg=t.card)
        row.pack(fill="x")
        who = _console_login_state()
        dot = _make_icon(row, 10, "dot", t.success if who else t.error, t.card)
        dot.pack(side="left")
        lab = tk.Label(row, text=("已登录：%s" % who) if who else "未登录",
                       fg=t.text if who else t.error, bg=t.card, font=CUI.f("body", True))
        lab.pack(side="left", padx=(SP_XS, 0))
        RoundedButton(row, t, "刷新", command=refresh_cb, variant="primary",
                      height=28, font_size=10).pack(side="right")
        RoundedButton(row, t, "退出", command=self._admin_logout, variant="neutral",
                      height=28, font_size=10).pack(side="right", padx=(0, SP_XS))
        RoundedButton(row, t, "登录", command=self._open_admin_login, variant="neutral",
                      height=28, font_size=10).pack(side="right", padx=(0, SP_XS))
        hint = tk.Label(bar, text="", anchor="w", justify="left", fg=t.text_muted,
                        bg=t.card, font=CUI.f("caption"))
        hint.pack(fill="x", pady=(SP_XS, 0))
        return lab, dot, hint

    def _set_admin_status(self, key: str, text: str, kind: str = "info") -> None:
        """页内结果提示（只做显示，不做业务判断）。kind: info/ok/warn/err/pending。"""
        meta = self._admin_meta.get(key)
        if not meta:
            return
        t = self.theme
        color = {"ok": t.success, "warn": t.warning, "err": t.error,
                 "pending": t.accent_glow}.get(kind, t.text_muted)
        try:
            meta["status"].config(text=text, fg=color)
        except Exception:
            _safe_traceback()

    def _admin_note(self, key: str, text: str, clear: bool = True,
                    illo: str = "", fg=None) -> None:
        """整页占位提示（接口未就绪 / 未登录时把请求路径显示出来，方便联调）。

        clear=False 时保留 body 已有内容（如额度卡），只追加提示行。
        illo 给素材名时走「照片画框 + 居中说明」的空态版式；素材没到货或解码失败时
        退回下面那条纯文字提示——**版式与上一版逐字一致**，不会留一个空框。
        """
        meta = self._admin_meta.get(key)
        if not meta:
            return
        t = self.theme
        body = meta["body"]
        if clear:
            _clear_frame(body)
        if illo:
            tile = CUI.photo_label(body, t, illo, CUI.px(150), CUI.px(150))
            if tile is not None:
                # 左对齐而不是居中：管理页 body 在横向可滚的画布里，表格列宽合计会把
                # 内框撑得比视口宽，居中反而把画框推到视口右侧之外
                tile.pack(anchor="w", padx=SP_LG, pady=(CUI.sp("xl"), CUI.sp("md")))
                # wraplength 是必需的：这些提示是一整句长文案，不折行会把 body 的
                # 请求宽度撑到比视口还宽，于是"居中"的画框被顶到右边、文字掉到折叠线下
                tk.Label(body, text=text, anchor="w", justify="left",
                         fg=fg or t.text_sec, bg=t.bg, font=CUI.f("body"),
                         wraplength=CUI.px(520)
                         ).pack(anchor="w", padx=SP_LG, pady=(0, CUI.sp("xl")))
                return
        tk.Label(body, text=text, anchor="w", justify="left", fg=fg or t.warning,
                 bg=t.surface_alt, font=CUI.f("body"), padx=SP_MD, pady=SP_MD).pack(fill="x")

    def _refresh_admin_login_bars(self) -> None:
        who = _console_login_state()
        t = self.theme
        for meta in self._admin_meta.values():
            try:
                meta["login_label"].config(
                    text=("已登录：%s" % who) if who else "未登录",
                    fg=t.text if who else t.error)
                meta["login_dot"]._paint_icon(t.success if who else t.error)
            except Exception:
                _safe_traceback()

    def _reload_admin_page(self, key: str) -> None:
        meta = self._admin_meta.get(key)
        if meta:
            try:
                meta["loader"]()
            except Exception as e:
                _safe_traceback()
                self._set_msg("刷新失败: %s" % e)

    def _maybe_load_admin_page(self, key: str) -> None:
        """管理页首次进入才拉数据（避免启动即打后端；也不给未登录用户报错刷屏）。"""
        meta = self._admin_meta.get(key)
        if meta is None or meta.get("loaded"):
            return
        meta["loaded"] = True
        self.root.after(150, meta["loader"])

    # ── 管理面：后台请求 + 主线程回投 ──

    def _run_admin(self, title: str, work, on_ok=None, page_key: str = "") -> None:
        """后台线程执行管理面请求，结果经 _poll 回投主线程（Tk 主线程绝不阻塞）。"""
        if self._admin_busy:
            self._set_msg("管理面请求处理中，请稍候…")
            return
        self._admin_busy = True
        self._set_msg("%s…" % title)

        def job():
            try:
                payload = work()
            except AdminApiError as e:
                self._q.put(("admin_err", e.message, e, page_key))
            except Exception as e:
                _safe_traceback()
                self._q.put(("admin_err", "请求异常: %s" % e, None, page_key))
            else:
                self._q.put(("admin_ok", payload, on_ok))
            finally:
                self._q.put(("admin_done", None, None))

        threading.Thread(target=job, daemon=True).start()

    def _on_admin_error(self, text: str, err, page_key: str = "") -> None:
        """错误落到页面上：登录态问题给重新登录入口，404 显示接口路径。"""
        code = getattr(err, "code", 0) if err is not None else 0
        path = getattr(err, "path", "") if err is not None else ""
        tip = getattr(self, "_admin_login_tip", None)
        if tip is not None:
            try:
                if tip.winfo_exists():
                    tip.config(text=text)
                    var = getattr(self, "_admin_login_pwd_var", None)
                    if var is not None:
                        var.set("")
            except Exception:
                pass
        if code == 401:
            text = "未登录或权限不足，请重新登录"
        if page_key:
            self._set_admin_status(page_key, "%s（%s）" % (text, path) if path else text, "err")
            if code in (401, 404, 0):
                # 三种"这一页现在没有数据"的时刻各给一枚画框：没登录 / 接口没上线 / 后端不通
                _illo = {401: "photo_locked_gate.jpg",
                         404: "photo_empty_disconnected.jpg"}.get(code, "photo_offline.jpg")
                self._admin_note(page_key, "%s\n请求路径：%s %s" % (
                    text, path, "（点右上角「登录」后重试）" if code == 401
                    else "（后端接口可能尚未上线）" if code == 404 else "（先启动后端再刷新）"),
                    illo=_illo, fg=self.theme.error)
        else:
            self._set_msg(text)
        self._refresh_admin_login_bars()
        self._set_msg(text[:120])

    # ── 管理面：登录 / 退出 ──

    def _open_admin_login(self) -> None:
        t = self.theme
        self._style_ttk()
        win = tk.Toplevel(self.root)
        win.title("控制台管理登录")
        win.resizable(False, False)
        win.transient(self.root)
        win.configure(bg=t.bg)
        card = RoundedCard(win, t, pad=3, fit_inner=True)
        card.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        f = card.inner
        f.config(padx=SP_LG, pady=SP_LG)
        tk.Label(f, text="服务器管理登录", fg=t.text, bg=t.card,
                 font=CUI.f("h2", True)).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(f, text="目标 %s（凭据缓存在 backend/data/console_token.json）" % _target_base(),
                 fg=t.text_muted, bg=t.card, font=CUI.f("caption")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, SP_MD))
        tk.Label(f, text="用户名", fg=t.text_sec, bg=t.card, font=CUI.f("body")).grid(
            row=2, column=0, sticky="w", padx=(0, SP_SM))
        user_var = tk.StringVar(value=_console_login_state())
        ttk.Entry(f, textvariable=user_var, width=24).grid(row=2, column=1, sticky="w")
        tk.Label(f, text="密码", fg=t.text_sec, bg=t.card, font=CUI.f("body")).grid(
            row=3, column=0, sticky="w", padx=(0, SP_SM), pady=(SP_XS, 0))
        pwd_var = tk.StringVar()
        self._admin_login_pwd_var = pwd_var
        pwd_entry = ttk.Entry(f, textvariable=pwd_var, show="*", width=24)
        pwd_entry.grid(row=3, column=1, sticky="w", pady=(SP_XS, 0))
        tip = tk.Label(f, text="", anchor="w", justify="left", fg=t.error, bg=t.card,
                       font=CUI.f("caption"), wraplength=320)
        tip.grid(row=4, column=0, columnspan=2, sticky="w", pady=(SP_SM, 0))
        self._admin_login_tip = tip

        def submit():
            uname = user_var.get().strip()
            pwd = pwd_var.get()
            if self._admin_busy:
                tip.config(text="上一个管理面请求还在处理中，请稍候再登录")
                return
            tip.config(text="")

            def ok(_data):
                self._admin_login_tip = None
                try:
                    win.destroy()
                except Exception:
                    pass
                self._refresh_admin_login_bars()
                self._set_msg("登录成功：%s" % uname)
                # 其余管理页回到「待加载」：切过去自动重拉，不停留在旧的未登录提示
                for k, m in self._admin_meta.items():
                    m["loaded"] = False
                cur = self._admin_meta.get(self._nav)
                if cur is not None:
                    cur["loaded"] = True
                    self._reload_admin_page(self._nav)

            self._run_admin("登录中", lambda: _admin_login(uname, pwd), ok, self._nav)

        btns = tk.Frame(f, bg=t.card)
        btns.grid(row=5, column=0, columnspan=2, sticky="w", pady=(SP_MD, 0))
        RoundedButton(btns, t, "登录", command=submit, variant="primary",
                      height=32, font_size=11).pack(side="left", padx=(0, SP_XS))
        RoundedButton(btns, t, "取消", command=win.destroy, variant="neutral",
                      height=32, font_size=11).pack(side="left")

        def on_close():
            self._admin_login_tip = None
            try:
                win.destroy()
            except Exception:
                pass
        win.protocol("WM_DELETE_WINDOW", on_close)
        pwd_entry.bind("<Return>", lambda e: submit())
        pwd_entry.focus_set()
        win.grab_set()

    def _admin_logout(self) -> None:
        _console_clear_session()
        self._refresh_admin_login_bars()
        for key in list(self._admin_meta):
            self._set_admin_status(key, "已退出登录", "warn")
        self._set_msg("已退出登录（本地 token 缓存已清除）")

    # ── 默认模型页 ──

    def _build_models_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "默认模型", "服务器级各模态默认配置（无自有配置的账号回落到这里；api_key 不回显明文）")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_modalities)
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["models"] = {"status": hint, "login_label": login_label,
                                      "login_dot": login_dot, "body": body,
                                      "path": ADMIN_API_PREFIX + "/modalities",
                                      "loader": self._load_modalities}
        return page

    def _load_modalities(self) -> None:
        def ok(data):
            rows = _sorted_modalities(data.get("modalities"))
            self._render_modalities(rows)
            self._set_admin_status(
                "models",
                "共 %d 个模态（GET %s）" % (len(rows), ADMIN_API_PREFIX + "/modalities"), "ok")

        self._set_admin_status("models", "加载中… GET %s" % (ADMIN_API_PREFIX + "/modalities"),
                               "pending")
        self._run_admin("读取默认模型", lambda: _admin_request("GET", "/modalities"), ok, "models")

    def _render_modalities(self, rows) -> None:
        meta = self._admin_meta["models"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        if not rows:
            self._admin_note("models", "后端未返回任何模态（GET %s）" % meta["path"])
            return
        for r in rows:
            key = str(r.get("key") or "")
            _, card = self._admin_card(body, fill="x", pady=(0, SP_SM))
            head = tk.Frame(card, bg=t.card)
            head.pack(fill="x")
            _make_icon(head, 10, "dot", t.success if r.get("enabled") else t.text_muted,
                       t.card).pack(side="left")
            tk.Label(head, text="%s（%s）" % (r.get("label") or key, key),
                     fg=t.text, bg=t.card, font=CUI.f("title", True)).pack(side="left", padx=(SP_XS, 0))
            tk.Label(head, text="provider %s · 日限额 %s" % (r.get("provider") or "—",
                                                             r.get("daily_limit") or "—"),
                     fg=t.text_muted, bg=t.card, font=CUI.f("caption")).pack(side="right")
            form = tk.Frame(card, bg=t.card)
            form.pack(fill="x", pady=(SP_XS, 0))
            en_var = tk.BooleanVar(value=bool(r.get("enabled")))
            ttk.Checkbutton(form, text="启用", variable=en_var).pack(side="left")
            fields = {}
            for fname, flabel, fwidth in (("model", "模型", 20), ("base_url", "Base URL", 24)):
                tk.Label(form, text=flabel, fg=t.text_muted, bg=t.card,
                         font=CUI.f("caption")).pack(side="left", padx=(SP_MD, 4))
                var = tk.StringVar(value=str(r.get(fname) or ""))
                ttk.Entry(form, textvariable=var, width=fwidth).pack(side="left")
                fields[fname] = var
            tk.Label(form, text="api_key", fg=t.text_muted, bg=t.card,
                     font=CUI.f("caption")).pack(side="left", padx=(SP_MD, 4))
            ak_var = tk.StringVar()
            ttk.Entry(form, textvariable=ak_var, width=16).pack(side="left")
            fields["api_key"] = ak_var
            tk.Label(form, text="留空＝不修改（当前 %s）" % ("已配置" if r.get("has_api_key") else "未配置"),
                     fg=t.text_muted, bg=t.card, font=CUI.f("caption")).pack(side="left", padx=(4, 0))
            RoundedButton(form, t, "保存", variant="primary", height=28, font_size=10,
                          command=lambda k=key, f=fields, ev=en_var: self._save_modality(k, f, ev)
                          ).pack(side="right")
            RoundedButton(form, t, "清空密钥", variant="neutral", height=28, font_size=10,
                          command=lambda k=key: self._clear_modality_key(k)
                          ).pack(side="right", padx=(0, SP_XS))

    def _save_modality(self, key: str, fields: dict, en_var) -> None:
        body = {"enabled": bool(en_var.get()),
                "model": fields["model"].get().strip(),
                "base_url": fields["base_url"].get().strip()}
        ak = fields["api_key"].get().strip()
        if ak:
            body["api_key"] = ak

        def ok(_data):
            self._set_msg("已保存默认模型：%s" % key)
            self._load_modalities()

        self._run_admin("保存 %s" % key,
                        lambda: _admin_request("PUT", "/modalities/%s" % key, body), ok, "models")

    def _clear_modality_key(self, key: str) -> None:
        def ok(_data):
            self._set_msg("已清空 %s 的 api_key" % key)
            self._load_modalities()

        self._run_admin("清空 %s 密钥" % key,
                        lambda: _admin_request("PUT", "/modalities/%s" % key, {"api_key": ""}),
                        ok, "models")

    # ── 账号管理页 ──

    def _build_accounts_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "账号管理", "跨家庭账号：禁用/启用、授予/取消控制台管理员、设置 llm_mode（护栏以后端为准）")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_accounts)
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["accounts"] = {"status": hint, "login_label": login_label,
                                        "login_dot": login_dot, "body": body,
                                        "path": ADMIN_API_PREFIX + "/accounts",
                                        "loader": self._load_accounts}
        return page

    def _load_accounts(self) -> None:
        def ok(data):
            payload = data if isinstance(data, dict) else {}
            rows = [r for r in (payload.get("accounts") or []) if isinstance(r, dict)]
            limit = payload.get("limit")
            self._render_accounts(rows, limit if isinstance(limit, dict) else None)
            self._set_admin_status("accounts", "共 %d 个账号（GET %s）"
                                   % (len(rows), ADMIN_API_PREFIX + "/accounts"), "ok")

        def work():
            # A8：顺带读「服务器默认额度」；该接口未就绪（404/未登录）时降级为不显示，不影响账号表
            # 注意：_admin_request 返回的已经是信封 dict（{"accounts": [...]}），不能再包一层
            out = _admin_request("GET", "/accounts")
            try:
                out["limit"] = _admin_request("GET", "/llm-limit")
            except AdminApiError:
                out["limit"] = None
            return out

        self._set_admin_status("accounts", "加载中… GET %s/accounts" % ADMIN_API_PREFIX, "pending")
        self._run_admin("读取账号", work, ok, "accounts")

    def _render_accounts(self, rows, limit=None) -> None:
        meta = self._admin_meta["accounts"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        self._render_server_llm_limit(limit)
        if not rows:
            self._admin_note(
                "accounts",
                "接口 200 但 accounts 为空（GET %s）：请确认账号数据是否存在，"
                "以及响应结构是否变更（信封字段是否仍为 accounts）" % meta["path"],
                clear=False, illo="photo_empty_accounts.jpg", fg=t.text_sec)
            return
        cols = (
            {"label": "ID", "weight": 0, "min": 46},
            {"label": "用户名", "weight": 2, "min": 110},
            {"label": "昵称", "weight": 2, "min": 100},
            {"label": "主账号", "weight": 1, "min": 78},
            {"label": "控制台", "weight": 1, "min": 78},
            {"label": "状态", "weight": 2, "min": 124},
            {"label": "LLM 额度", "weight": 2, "min": 130},
            {"label": "LLM 模式", "weight": 2, "min": 140},
            {"label": "操作", "weight": 0, "min": 56},
        )
        _, card = self._admin_card(body, fill="x")
        table = CUI.DataTable(card, t, cols)
        for idx, r in enumerate(rows):
            uid = r.get("id")
            disabled = bool(r.get("disabled_at"))
            base = t.card if idx % 2 == 0 else CUI.mix(t.card, t.card_hover, 0.55)
            row = table.add_row(_hex_mix(base, t.error, 0.14) if disabled else base)
            row.text(0, str(uid), num=True, fg=t.text)
            row.text(1, str(r.get("username") or ""), fg=t.text)
            nick = str(r.get("nickname") or "")
            row.text(2, nick, fg=t.text_sec, tip=nick)
            row.text(3, "是" if r.get("is_admin") else "否", fg=t.text_sec)
            row.text(4, "是" if r.get("server_admin") else "否",
                     fg=t.accent_glow if r.get("server_admin") else t.text_muted,
                     bold=bool(r.get("server_admin")))
            row.text(5, ("禁用中 %s" % _fmt_dt(r.get("disabled_at"))) if disabled else "正常",
                     fg=t.error if disabled else t.success)
            row.text(6, _llm_limit_text(r), num=True, fg=t.text)
            mode = str(r.get("llm_mode") or "")
            row.text(7, LLM_MODE_TEXT.get(mode, mode or "—"),
                     fg=t.error if mode == "blocked" else t.text_sec)
            # 操作收进 ⋯ 菜单：旧版每行 1 个下拉 + 5 个按钮，25 行＝125 个按钮，
            # 是全站噪音最大的一屏；动作一个没少，只是不再常驻。
            more = _make_icon(row.cell(8), 18, "more-horizontal", t.text_sec, row.bg)
            more.config(cursor="hand2")
            more.bind("<Button-1>", lambda e, rr=r: self._open_account_menu(e, rr))
            more.pack(side="left")

    def _open_account_menu(self, event, r) -> None:
        """账号行操作菜单（替代旧的常驻下拉 + 5 个按钮）。"""
        t = self.theme
        uid = r.get("id")
        attrs = dict(tearoff=0, bg=t.surface_alt, fg=t.text, bd=0, relief="flat",
                     activebackground=t.accent_dim, activeforeground=t.text,
                     font=CUI.f("body"))
        m = tk.Menu(self.root, **attrs)
        m.add_command(label="取消控制台管理员" if r.get("server_admin") else "设为控制台管理员",
                      command=lambda i=uid, en=not bool(r.get("server_admin")):
                      self._set_account_server_admin(i, en))
        m.add_command(label="恢复账号" if r.get("disabled_at") else "禁用账号",
                      command=lambda i=uid, d=not bool(r.get("disabled_at")):
                      self._set_account_disabled(i, d))
        m.add_separator()
        cur = str(r.get("llm_mode") or "default_allowed")
        sub = tk.Menu(m, **attrs)
        for mode in ACCOUNT_LLM_MODES:
            sub.add_command(
                label="%s（当前）" % LLM_MODE_TEXT[mode] if mode == cur else LLM_MODE_TEXT[mode],
                command=lambda i=uid, mm=mode: self._set_account_llm_mode(
                    i, tk.StringVar(value=mm)))
        m.add_cascade(label="LLM 模式", menu=sub)
        m.add_command(label="设置额度覆盖…",
                      command=lambda i=uid, o=r.get("llm_total_limit_own"):
                      self._open_llm_limit_dialog(i, o))
        if r.get("llm_total_limit_own") is not None:
            m.add_command(label="清除额度覆盖",
                          command=lambda i=uid: self._clear_account_llm_limit(i))
        m.tk_popup(event.x_root, event.y_root)
        m.grab_release()

    def _set_account_disabled(self, uid, disabled: bool) -> None:
        def ok(_data):
            self._set_msg("账号 %s 已%s" % (uid, "禁用" if disabled else "启用"))
            self._load_accounts()

        self._run_admin("更新账号 %s" % uid,
                        lambda: _admin_request("PUT", "/accounts/%s/disabled" % uid,
                                               {"disabled": bool(disabled)}),
                        ok, "accounts")

    def _set_account_server_admin(self, uid, enabled: bool) -> None:
        def ok(_data):
            self._set_msg("账号 %s %s控制台管理员" % (uid, "已授予" if enabled else "已取消"))
            self._load_accounts()

        self._run_admin("更新账号 %s" % uid,
                        lambda: _admin_request("PUT", "/accounts/%s/server-admin" % uid,
                                               {"enabled": bool(enabled)}),
                        ok, "accounts")

    def _set_account_llm_mode(self, uid, mode_var) -> None:
        mode = str(mode_var.get() or "")

        def ok(_data):
            self._set_msg("账号 %s llm_mode=%s" % (uid, mode))
            self._load_accounts()

        self._run_admin("更新账号 %s" % uid,
                        lambda: _admin_request("PUT", "/accounts/%s/llm-mode" % uid,
                                               {"llm_mode": mode}),
                        ok, "accounts")

    # ── LLM 额度（A8：按账号覆盖 + 服务器默认）──

    def _render_server_llm_limit(self, limit) -> None:
        """服务器默认额度卡片（账号管理页顶部）：只读展示 + 可编辑保存。

        接口未就绪/未登录（limit 为 None）时只给提示，不影响下方账号表。
        """
        body = self._admin_meta["accounts"]["body"]
        t = self.theme
        _, card = self._admin_card(body, fill="x", pady=(0, SP_SM))
        tk.Label(card, text="服务器默认额度（账号无覆盖时回落到这里；0=未设置）", fg=t.text_sec,
                 bg=t.card, font=CUI.f("caption")).pack(anchor="w")
        if not isinstance(limit, dict):
            tk.Label(card, text="未读取到（GET %s/llm-limit）" % ADMIN_API_PREFIX,
                     fg=t.warning, bg=t.card, font=CUI.f("body")).pack(anchor="w", pady=(0, SP_XS))
            return
        val = limit.get("total_limit")
        src = str(limit.get("source") or "")
        tk.Label(card, text="%s（%s）" % (val, LLM_LIMIT_SOURCE_LABELS.get(src, src)),
                 fg=t.text, bg=t.card, font=CUI.f("h2", True)).pack(anchor="w", pady=(0, SP_SM))
        row = tk.Frame(card, bg=t.card)
        row.pack(fill="x")
        var = tk.StringVar(value="" if val is None else str(val))
        ttk.Entry(row, textvariable=var, width=14).pack(side="left")
        RoundedButton(row, t, "保存", variant="primary", height=28, font_size=10,
                      command=lambda v=var: self._save_server_llm_limit(v)
                      ).pack(side="left", padx=(SP_XS, 0))
        tk.Label(row, text="负数/非整数由后端拒绝（400）", fg=t.text_muted, bg=t.card,
                 font=CUI.f("caption")).pack(side="left", padx=(SP_MD, 0))

    def _save_server_llm_limit(self, var) -> None:
        raw = str(var.get() or "").strip()
        if not raw:
            self._set_admin_status("accounts", "先填写额度再保存（0 表示未设置）", "warn")
            return
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self._set_admin_status("accounts", "额度必须是整数", "warn")
            return
        if value < 0:
            self._set_admin_status("accounts", "额度不能为负数", "warn")
            return

        def ok(_data):
            self._set_msg("已保存服务器默认额度：%d" % value)
            self._load_accounts()

        self._run_admin("保存服务器默认额度",
                        lambda: _admin_request("PUT", "/llm-limit", {"total_limit": value}),
                        ok, "accounts")

    def _open_llm_limit_dialog(self, uid, own) -> None:
        """单账号额度输入弹窗（提交走 _run_admin 后台线程，Tk 主线程不阻塞）。"""
        t = self.theme
        self._style_ttk()
        win = tk.Toplevel(self.root)
        win.title("设置账号 %s 的 LLM 额度" % uid)
        win.resizable(False, False)
        win.transient(self.root)
        win.configure(bg=t.bg)
        card = RoundedCard(win, t, pad=3, fit_inner=True)
        card.pack(fill="both", expand=True, padx=SP_LG, pady=SP_LG)
        f = card.inner
        f.config(padx=SP_LG, pady=SP_LG)
        tk.Label(f, text="账号 %s 的 LLM 额度" % uid, fg=t.text, bg=t.card,
                 font=CUI.f("h2", True)).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(f, text="保存=设账号覆盖；点「清除覆盖」= 删除覆盖回落服务器默认（0 表示额度为 0）",
                 fg=t.text_muted, bg=t.card, font=CUI.f("caption")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, SP_MD))
        tk.Label(f, text="额度", fg=t.text_sec, bg=t.card, font=CUI.f("body")).grid(
            row=2, column=0, sticky="w", padx=(0, SP_SM))
        var = tk.StringVar(value="" if own is None else str(own))
        entry = ttk.Entry(f, textvariable=var, width=18)
        entry.grid(row=2, column=1, sticky="w")
        tip = tk.Label(f, text="", anchor="w", justify="left", fg=t.error, bg=t.card,
                       font=CUI.f("caption"), wraplength=320)
        tip.grid(row=3, column=0, columnspan=2, sticky="w", pady=(SP_SM, 0))

        def close():
            try:
                win.destroy()
            except Exception:
                pass

        def submit():
            raw = str(var.get() or "").strip()
            if not raw:
                tip.config(text="请输入额度（0 表示额度为 0）")
                return
            try:
                value = int(raw)
            except (TypeError, ValueError):
                tip.config(text="额度必须是整数")
                return
            if value < 0:
                tip.config(text="额度不能为负数")
                return
            tip.config(text="")

            def ok(_data):
                close()
                self._set_msg("账号 %s 额度已设为 %d" % (uid, value))
                self._load_accounts()

            self._run_admin("设置账号 %s 额度" % uid,
                            lambda: _admin_request("PUT", "/accounts/%s/llm-limit" % uid,
                                                   {"total_limit": value}),
                            ok, "accounts")

        btns = tk.Frame(f, bg=t.card)
        btns.grid(row=4, column=0, columnspan=2, sticky="w", pady=(SP_MD, 0))
        RoundedButton(btns, t, "保存", command=submit, variant="primary",
                      height=32, font_size=11).pack(side="left", padx=(0, SP_XS))
        RoundedButton(btns, t, "清除覆盖", variant="neutral", height=32, font_size=11,
                      command=lambda: (close(), self._clear_account_llm_limit(uid))
                      ).pack(side="left", padx=(0, SP_XS))
        RoundedButton(btns, t, "取消", command=close, variant="neutral",
                      height=32, font_size=11).pack(side="left")
        win.protocol("WM_DELETE_WINDOW", close)
        entry.bind("<Return>", lambda e: submit())
        entry.focus_set()
        win.grab_set()

    def _clear_account_llm_limit(self, uid) -> None:
        def ok(_data):
            self._set_msg("账号 %s 已清除额度覆盖（回落服务器默认）" % uid)
            self._load_accounts()

        self._run_admin("清除账号 %s 额度覆盖" % uid,
                        lambda: _admin_request("PUT", "/accounts/%s/llm-limit" % uid,
                                               {"total_limit": None}),
                        ok, "accounts")

    # ── 开关与权限页 ──

    # ── 开关与权限页（V2 重做）─────────────────────────────────────
    # 旧版三宗罪：81 行平铺无分组、243 个原生黑框复选框、81 个常驻「保存」按钮，
    # 而且 title/desc 恒为 null（管理端点不返回中文元数据），整页只有裸键名。
    # 现在：分组折叠 + 搜索/筛选 + 自绘 Switch + 只有改动过的行才出现「保存」。
    # 写库语义一字未改（仍是每行一次 PUT 三个字段），只是把噪音收掉。

    def _build_flags_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "开关与权限", "功能开关当前值 + 允许用户自助 + 服务器锁定（锁定行高亮，用户侧写该开关一律 403）")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_flags)
        # 工具条只建一次：_render_flags 会反复重建列表，搜索框若建在列表里，
        # 每敲一个字都会被清掉焦点。
        bar = tk.Frame(pad, bg=t.bg)
        bar.pack(fill="x", pady=(0, CUI.sp("sm")))
        self._flags_rows = []
        self._flags_open = set()
        self._flags_dirty = {}
        self._flags_search = tk.StringVar()
        self._flags_filter = tk.StringVar(value="all")
        ent = tk.Entry(bar, textvariable=self._flags_search, width=20, bg=t.entry_bg,
                       fg=t.text, insertbackground=t.text, relief="flat",
                       highlightthickness=1, highlightbackground=t.hairline,
                       highlightcolor=t.accent, font=CUI.f("body"))
        ent.pack(side="left", ipady=CUI.sp("xxs"))
        ent.bind("<KeyRelease>", lambda _e: self._render_flags())
        CUI.Tooltip(ent, lambda: "按键名 / 中文名 / 说明搜索")
        Segmented(bar, t, [("all", "全部"), ("diff", "与默认不同"),
                           ("locked", "仅锁定"), ("on", "仅开启")],
                  lambda v: (self._flags_filter.set(v), self._render_flags()),
                  width=CUI.px(292), height=CUI.px(26)).pack(side="left", padx=(CUI.sp("md"), 0))
        RoundedButton(bar, t, "展开全部", variant="neutral", height=CUI.px(26), font_size=10,
                      command=self._flags_expand_all).pack(side="left", padx=(CUI.sp("md"), 0))
        RoundedButton(bar, t, "收起全部", variant="neutral", height=CUI.px(26), font_size=10,
                      command=self._flags_collapse_all).pack(side="left", padx=(CUI.sp("xs"), 0))
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["flags"] = {"status": hint, "login_label": login_label,
                                     "login_dot": login_dot, "body": body,
                                     "path": ADMIN_API_PREFIX + "/flags",
                                     "loader": self._load_flags}
        return page

    def _load_flags(self) -> None:
        def ok(data):
            rows = [r for r in (data.get("flags") or []) if isinstance(r, dict)]
            self._flags_rows = rows
            self._flags_dirty = {}
            self._render_flags()
            self._set_admin_status("flags", "共 %d 个开关（策略 %s/flags ＋ 展示元数据 /system/feature-flags）"
                                   % (len(rows), ADMIN_API_PREFIX), "ok")

        def work():
            # 中文标题/分组/是否常用/scope/source 只在 GET /api/v1/system/feature-flags 有，
            # 管理面 /flags 的 title/desc 恒为 null → 两端口按键名在前端合并，不改后端。
            admin = _admin_request("GET", "/flags")
            pub_map = {}
            try:
                token = str(_console_session().get("token") or "")
                status, pub = _http_json("GET", "/api/v1/system/feature-flags", token=token)
                if 200 <= status < 300 and isinstance(pub, dict):
                    for r in (pub.get("flags") or []):
                        if isinstance(r, dict) and r.get("key"):
                            pub_map[str(r["key"])] = r
            except Exception:
                _safe_traceback()
            rows = []
            for r in (admin.get("flags") or []):
                if not isinstance(r, dict):
                    continue
                pub = pub_map.get(str(r.get("key") or ""), {})
                cm = pub.get("meta") if isinstance(pub.get("meta"), dict) else {}
                merged = dict(r)
                merged["title"] = cm.get("title") or r.get("title")
                merged["desc"] = cm.get("desc") or r.get("desc")
                merged["group"] = str(cm.get("group") or "other")
                merged["group_order"] = cm.get("group_order", 99)
                merged["order"] = cm.get("order", 9999)
                merged["visible"] = bool(cm.get("visible"))
                merged["source"] = pub.get("source")
                merged["scope"] = pub.get("scope")
                merged["user_enabled"] = pub.get("user_enabled")
                rows.append(merged)
            return {"flags": rows}

        self._set_admin_status("flags", "加载中… GET %s/flags" % ADMIN_API_PREFIX, "pending")
        self._run_admin("读取开关", work, ok, "flags")

    def _flags_visible_rows(self) -> list:
        q = str(self._flags_search.get() or "").strip().lower()
        mode = str(self._flags_filter.get() or "all")
        out = []
        for r in self._flags_rows:
            if mode == "diff" and str(r.get("source") or "") != "db":
                continue
            if mode == "locked" and not r.get("server_locked"):
                continue
            if mode == "on" and not r.get("enabled"):
                continue
            if q:
                hay = " ".join([str(r.get("key") or ""), str(r.get("title") or ""),
                                str(r.get("desc") or "")]).lower()
                if q not in hay:
                    continue
            out.append(r)
        return out

    def _flags_expand_all(self) -> None:
        self._flags_open = {str(r.get("group") or "other") for r in self._flags_rows}
        self._render_flags()

    def _flags_collapse_all(self) -> None:
        self._flags_open = set()
        self._render_flags()

    def _flags_toggle_group(self, gid: str) -> None:
        if gid in self._flags_open:
            self._flags_open.discard(gid)
        else:
            self._flags_open.add(gid)
        self._render_flags()

    def _render_flags(self) -> None:
        meta = self._admin_meta["flags"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        if not self._flags_rows:
            self._admin_note("flags", "后端未返回任何开关（GET %s）" % meta["path"])
            return
        rows = self._flags_visible_rows()
        if not rows:
            self._admin_note("flags", "没有匹配的开关（搜索「%s」/ 筛选「%s」）"
                             % (self._flags_search.get() or "—", self._flags_filter.get()),
                             clear=False)
            return
        filtering = bool(str(self._flags_search.get() or "").strip()) or \
            str(self._flags_filter.get() or "all") != "all"
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(str(r.get("group") or "other"), []).append(r)
        ordered = sorted(groups.items(), key=lambda kv: (
            min(int(x.get("group_order", 99)) for x in kv[1]),
            min(int(x.get("order", 9999)) for x in kv[1])))
        # 「常用」置顶且始终展开（后端 meta.visible 决定，与 App 开关页同一真源）
        hot = [r for r in rows if r.get("visible")]
        if hot and not filtering:
            self._flags_group_card(body, "常用", len(hot), True, t, hot)
        for gid, grows in ordered:
            if filtering:
                self._flags_group_card(body, CUI.GROUP_LABELS.get(gid, gid), len(grows), True,
                                       t, sorted(grows, key=lambda x: int(x.get("order", 9999))))
                continue
            if hot and not filtering:
                grows = [r for r in grows if not r.get("visible")]
                if not grows:
                    continue
            open_it = gid in self._flags_open
            head = tk.Frame(body, bg=t.surface_alt, cursor="hand2")
            head.pack(fill="x", pady=(CUI.sp("xs"), 0))
            chev = _make_icon(head, 14, "chevron-right" if not open_it else "chevron-down",
                              t.accent if open_it else t.text_sec, t.surface_alt)
            chev.pack(side="left", padx=(CUI.sp("sm"), CUI.sp("xs")), pady=7)
            tk.Label(head, text=CUI.GROUP_LABELS.get(gid, gid), bg=t.surface_alt, fg=t.text,
                     font=CUI.f("h2")).pack(side="left")
            tk.Label(head, text=str(len(grows)), bg=t.surface_alt, fg=t.text_muted,
                     font=CUI.f("caption")).pack(side="left", padx=(CUI.sp("xs"), 0))
            for wid in (head, chev):
                wid.bind("<Button-1>", lambda _e, g=gid: self._flags_toggle_group(g))
            if open_it:
                self._flags_group_card(body, "", len(grows), True, t,
                                       sorted(grows, key=lambda x: int(x.get("order", 9999))),
                                       bare=True)

    def _flags_group_card(self, body, label: str, count: int, _open: bool, t,
                          rows: list, bare: bool = False) -> None:
        """一个分组卡：表头 + 数据行。bare=True 时不再画组名（折叠头已在外面画过）。"""
        if not bare:
            head = tk.Frame(body, bg=t.surface_alt)
            head.pack(fill="x", pady=(CUI.sp("xs"), 0))
            tk.Label(head, text=label, bg=t.surface_alt, fg=t.text,
                     font=CUI.f("h2")).pack(side="left", padx=CUI.sp("sm"), pady=7)
            tk.Label(head, text=str(count), bg=t.surface_alt, fg=t.text_muted,
                     font=CUI.f("caption")).pack(side="left")
        _, card = self._admin_card(body, fill="x")
        card.grid_columnconfigure(0, weight=1)
        for ci, name in ((1, "当前值"), (2, "允许自助"), (3, "服务器锁定")):
            tk.Label(card, text=name, anchor="w", fg=t.text_muted, bg=t.card,
                     font=CUI.f("caption", True)).grid(
                row=0, column=ci, sticky="w", padx=(0, CUI.sp("md")), pady=(0, 4))
        tk.Frame(card, bg=t.hairline, height=1).grid(row=1, column=0, columnspan=5,
                                                     sticky="ew", pady=(0, CUI.sp("xxs")))
        for ri, r in enumerate(rows, start=2):
            self._flag_row(card, r, ri, t)

    def _flag_row(self, card, r, ri: int, t) -> None:
        key = str(r.get("key") or "")
        locked = bool(r.get("server_locked"))
        zebra = t.card if ri % 2 == 0 else _hex_mix(t.card, t.card_hover, 0.55)
        row_bg = _hex_mix(t.card, t.warning, 0.18) if locked else zebra
        title = str(r.get("title") or "").strip()
        desc = str(r.get("desc") or "").strip()
        name_f = tk.Frame(card, bg=row_bg)
        name_f.grid(row=ri, column=0, sticky="w", pady=3)
        tk.Label(name_f, text=key, anchor="w", bg=row_bg,
                 fg=t.warning if locked else t.text,
                 font=CUI.f("body", bool(locked))).pack(side="left")
        if title:
            tk.Label(name_f, text=title, anchor="w", bg=row_bg, fg=t.text_sec,
                     font=CUI.f("caption")).pack(side="left", padx=(CUI.sp("sm"), 0))
        if desc:
            lb = tk.Label(name_f, text=desc if len(desc) <= 30 else desc[:29] + "…",
                          anchor="w", bg=row_bg, fg=t.text_muted, font=CUI.f("caption"))
            lb.pack(side="left", padx=(CUI.sp("sm"), 0))
            CUI.Tooltip(lb, lambda d=desc: d)
        if str(r.get("source") or "") == "db":
            tk.Label(name_f, text="DB覆盖", bg=_hex_mix(row_bg, t.accent, 0.16),
                     fg=t.accent_glow, font=CUI.f("micro"),
                     padx=6, pady=1).pack(side="left", padx=(CUI.sp("sm"), 0))
        if str(r.get("scope") or "") == "user":
            tk.Label(name_f, text="按账号", bg=_hex_mix(row_bg, t.accent, 0.16),
                     fg=t.accent_glow, font=CUI.f("micro"),
                     padx=6, pady=1).pack(side="left", padx=(CUI.sp("xs"), 0))
        vars_ = (tk.BooleanVar(value=bool(r.get("enabled"))),
                 tk.BooleanVar(value=bool(r.get("self_service"))),
                 tk.BooleanVar(value=locked))
        # 三列都可编辑：控制台是服务器管理员面，锁不锁都要能改（用户侧的 403 由后端裁决）
        for ci, var in enumerate(vars_, start=1):
            CUI.Switch(card, t, variable=var, bg=row_bg,
                       command=lambda k=key, vs=vars_: self._flag_mark_dirty(k, vs)
                       ).grid(row=ri, column=ci, sticky="w", padx=(0, CUI.sp("md")))
        slot = tk.Frame(card, bg=row_bg)
        slot.grid(row=ri, column=4, sticky="w")
        self._flags_dirty.setdefault(key, {"vars": vars_, "slot": slot, "dirty": False})

    def _flag_mark_dirty(self, key: str, vars_) -> None:
        """只有改动过的行才长出「保存」按钮——干净行不显示，81 行的视觉噪音就没了。"""
        ent = self._flags_dirty.get(key)
        if not ent or ent["dirty"]:
            return
        ent["dirty"] = True
        RoundedButton(ent["slot"], self.theme, "保存", variant="primary",
                      height=CUI.px(24), font_size=9,
                      command=lambda k=key, vs=vars_: self._save_flag(k, vs)
                      ).pack(side="left")

    def _save_flag(self, key: str, vars_) -> None:
        body = {"enabled": bool(vars_[0].get()), "self_service": bool(vars_[1].get()),
                "server_locked": bool(vars_[2].get())}

        def ok(_data):
            self._set_msg("已保存开关 %s" % key)
            self._load_flags()

        self._run_admin("更新开关 %s" % key,
                        lambda: _admin_request("PUT", "/flags/%s" % key, body), ok, "flags")

    # ── 审计页 ──

    def _build_audit_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "审计", "管理面写动作留痕（时间/操作者/动作/目标/前后值摘要），按 created_at 倒序")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_audit)
        _, filter_bar = self._admin_card(pad, fill="x", pady=(0, SP_SM))
        frow = tk.Frame(filter_bar, bg=t.card)
        frow.pack(fill="x")
        tk.Label(frow, text="最近条数", fg=t.text_sec, bg=t.card,
                 font=CUI.f("body")).pack(side="left")
        self._audit_limit_var = tk.StringVar(value="100")
        ttk.Entry(frow, textvariable=self._audit_limit_var, width=8).pack(side="left", padx=(SP_XS, 0))
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["audit"] = {"status": hint, "login_label": login_label,
                                     "login_dot": login_dot, "body": body,
                                     "path": ADMIN_API_PREFIX + "/audit",
                                     "loader": self._load_audit}
        return page

    def _load_audit(self) -> None:
        try:
            limit = int(str(self._audit_limit_var.get()).strip() or 100)
        except Exception:
            limit = 100
        sub = "/audit?limit=%d" % limit

        def ok(data):
            rows = [r for r in (data.get("entries") or []) if isinstance(r, dict)]
            self._render_audit(rows, limit)
            self._set_admin_status("audit", "共 %d 条（GET %s/audit?limit=%d）"
                                   % (len(rows), ADMIN_API_PREFIX, limit), "ok")

        self._set_admin_status("audit", "加载中… GET %s/audit?limit=%d" % (ADMIN_API_PREFIX, limit),
                               "pending")
        self._run_admin("读取审计", lambda: _admin_request("GET", sub), ok, "audit")

    def _render_audit(self, rows, limit: int) -> None:
        meta = self._admin_meta["audit"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        if not rows:
            self._admin_note("audit", "暂无审计记录（GET %s/audit?limit=%d）" % (ADMIN_API_PREFIX, limit),
                             illo="photo_empty_audit.jpg", fg=t.text_sec)
            return
        cols = (
            {"label": "时间", "weight": 0, "min": 150},
            {"label": "操作者", "weight": 2, "min": 110},
            {"label": "动作", "weight": 3, "min": 170},
            {"label": "目标", "weight": 2, "min": 130},
            {"label": "前值", "weight": 3, "min": 150},
            {"label": "后值", "weight": 3, "min": 150},
        )
        _, card = self._admin_card(body, fill="x")
        table = CUI.DataTable(card, t, cols)
        for r in rows:
            row = table.add_row()
            row.text(0, _fmt_dt(r.get("created_at")), num=True, fg=t.text)
            row.text(1, str(r.get("actor_username") or r.get("actor_user_id") or "—"),
                     fg=t.text)
            row.text(2, str(r.get("action") or "—"), fg=t.text)
            tgt = str(r.get("target") or "—")
            row.text(3, tgt, fg=t.text_sec, tip=tgt)
            before = _fmt_audit_val(r.get("before"))
            after = _fmt_audit_val(r.get("after"))
            row.text(4, before, fg=t.text_muted, tip=before)
            row.text(5, after, fg=t.text_sec, tip=after)

    # ── 注册策略页 ──

    def _build_registration_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "注册策略", "开放 / 仅邀请码 / 关闭（保存后自动回读确认；注册拦截与提示文案一律以后端为准）")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_registration)
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["registration"] = {"status": hint, "login_label": login_label,
                                            "login_dot": login_dot, "body": body,
                                            "path": ADMIN_API_PREFIX + "/registration",
                                            "loader": self._load_registration}
        return page

    def _load_registration(self) -> None:
        path = ADMIN_API_PREFIX + "/registration"

        def ok(data):
            mode = str(data.get("mode") or "")
            self._render_registration(mode)
            self._set_admin_status("registration", "当前 %s（GET %s）"
                                   % (_registration_mode_text(mode), path), "ok")

        self._set_admin_status("registration", "加载中… GET %s" % path, "pending")
        self._run_admin("读取注册策略", lambda: _admin_request("GET", "/registration"),
                        ok, "registration")

    def _render_registration(self, mode: str) -> None:
        meta = self._admin_meta["registration"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        _, card = self._admin_card(body, fill="x")
        tk.Label(card, text="当前注册策略", fg=t.text_sec, bg=t.card,
                 font=CUI.f("caption")).pack(anchor="w")
        tk.Label(card, text=_registration_mode_text(mode), fg=t.text, bg=t.card,
                 font=CUI.f("h2", True)).pack(anchor="w", pady=(0, SP_SM))
        mode_var = tk.StringVar(value=mode)
        for m, label, desc in REGISTRATION_MODES:
            row = tk.Frame(card, bg=t.card)
            row.pack(fill="x", pady=1)
            tk.Radiobutton(row, text="%s（%s）" % (label, m), value=m, variable=mode_var,
                           bg=t.card, fg=t.text, activebackground=t.card,
                           activeforeground=t.text, selectcolor=t.entry_bg,
                           font=CUI.f("body")).pack(side="left")
            tk.Label(row, text=desc, fg=t.text_muted, bg=t.card,
                     font=CUI.f("caption")).pack(side="left", padx=(SP_MD, 0))
            if m == mode:
                tk.Label(row, text="← 生效中", fg=t.accent_glow, bg=t.card,
                         font=CUI.f("caption")).pack(side="right")
        act = tk.Frame(card, bg=t.card)
        act.pack(fill="x", pady=(SP_SM, 0))
        RoundedButton(act, t, "保存", variant="primary", height=28, font_size=10,
                      command=lambda v=mode_var: self._save_registration(v)
                      ).pack(side="left")
        RoundedButton(act, t, "放弃修改并重读", variant="neutral", height=28, font_size=10,
                      command=self._load_registration).pack(side="left", padx=(SP_XS, 0))
        tk.Label(act, text="403/401 时点右上角「登录」后重试", fg=t.text_muted, bg=t.card,
                 font=CUI.f("caption")).pack(side="right")

    def _save_registration(self, mode_var) -> None:
        mode = str(mode_var.get() or "")
        if not mode:
            self._set_admin_status("registration", "先选择一个策略再保存", "warn")
            return

        def ok(_data):
            self._set_msg("已保存注册策略：%s" % mode)
            self._load_registration()  # 保存后回读，页面显示以后端返回值为准

        self._run_admin("保存注册策略",
                        lambda: _admin_request("PUT", "/registration", {"mode": mode}),
                        ok, "registration")

    # ── 概览页 ──

    def _build_overview_page(self) -> tk.Frame:
        t = self.theme
        page, pad = self._make_admin_page(
            "概览", "账号数/禁用数/控制台管理员/开启开关/后端版本（只读，控制台不做任何推算）")
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_overview)
        # 品牌横幅（照片槽位）：素材没到货时整块不建，页面与上一版逐字一致。
        # 构图是"左上表盘 + 右侧大片空黑"，所以走 panel 模式（贴左 + 右缘渐隐），
        # 文案压在右侧空区上；cover 成横条会把表盘裁掉。
        if CUI.PHOTOS.has("photo_about_hero.jpg"):
            hero = CUI.PhotoBanner(pad, t, "photo_about_hero.jpg", CUI.px(150), mode="panel")
            hero.pack(fill="x", pady=(0, CUI.sp("sm")))
            hbg = CUI.photo_bg(t)
            tk.Label(hero, text="拥爱 · 服务器控制台", fg=CUI.photo_fg(t), bg=hbg,
                     font=CUI.f("h1", True)).place(x=CUI.px(330), rely=0.34, anchor="w")
            tk.Label(hero, text="为 AI 陪伴服务写的运维仪表盘",
                     fg=CUI.mix(CUI.photo_fg(t), hbg, 0.38), bg=hbg,
                     font=CUI.f("caption")).place(x=CUI.px(330), rely=0.63, anchor="w")
        _, tools = self._admin_card(pad, fill="x", pady=(0, SP_SM))
        trow = tk.Frame(tools, bg=t.card)
        trow.pack(fill="x")
        tk.Label(trow, text="快速判断服务器现状；未登录或接口未就绪时只提示，不影响其他页签",
                 fg=t.text_muted, bg=t.card, font=CUI.f("caption")).pack(side="left")
        RoundedButton(trow, t, "刷新", command=self._load_overview, variant="primary",
                      height=28, font_size=10).pack(side="right")
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")
        self._admin_meta["overview"] = {"status": hint, "login_label": login_label,
                                        "login_dot": login_dot, "body": body,
                                        "path": ADMIN_API_PREFIX + "/overview",
                                        "loader": self._load_overview}
        return page

    def _load_overview(self) -> None:
        path = ADMIN_API_PREFIX + "/overview"

        def ok(data):
            shown = self._render_overview(data)
            self._set_admin_status("overview", "已读取 %d 项（GET %s）" % (shown, path), "ok")

        self._set_admin_status("overview", "加载中… GET %s" % path, "pending")
        self._run_admin("读取概览", lambda: _admin_request("GET", "/overview"), ok, "overview")

    def _render_overview(self, data) -> int:
        """按契约字段顺序渲染，后端多给的字段附在后面；返回展示条数。"""
        meta = self._admin_meta["overview"]
        body = meta["body"]
        t = self.theme
        _clear_frame(body)
        data = data if isinstance(data, dict) else {}
        rows = [(label, data.get(key)) for key, label in OVERVIEW_FIELDS]
        known = {key for key, _label in OVERVIEW_FIELDS}
        rows += [(key, data.get(key)) for key in sorted(data) if key not in known]
        if all(v is None for _label, v in rows):
            self._admin_note("overview", "后端未返回任何概览字段（GET %s）" % meta["path"])
            return 0
        table = CUI.DataTable(body, t, [
            {"label": "指标", "weight": 2, "min": 150},
            {"label": "值", "weight": 3, "min": 220},
        ])
        for label, val in rows:
            missing = val is None
            row = table.add_row()
            row.text(0, str(label), "body", fg=t.text)
            # 数值列走等宽族（宽度不随数字位数跳位）；缺值退到正文字号并用弱化色
            row.text(1, "—" if missing else str(val),
                     "num" if not missing else "body",
                     fg=t.text_muted if missing else t.text,
                     bold=not missing, num=not missing)
        return len(rows)

    # ── 行动通道（X7-M4e-2：三条开关 + 两份名单，一律走管理面 HTTP，不直连数据库）──

    def _build_device_actions_page(self) -> tk.Frame:
        """三段式：三条开关 / 目标白名单 / 插件灰度名单（控件只建一次，重绘只换列表行）。"""
        t = self.theme
        page, pad = self._make_admin_page(
            "行动通道",
            "三条开关 + 目标白名单 + 插件灰度名单（全部经 %s/device-actions 读写，控制台不碰数据库）"
            % ADMIN_API_PREFIX)
        login_label, login_dot, hint = self._admin_login_bar(pad, self._load_device_actions)
        self._da_snapshot = {}
        self._da_rows = {"targets": [], "plugins": []}
        self._da_entry = {}
        self._da_hint = {}
        self._da_hint_default = {}
        self._da_cap = {}
        self._da_list_host = {}
        self._da_switch = {}
        # 整页提示位：未登录 / 接口未就绪时由 _admin_note 占用，放最上面才不会被三段挤出视口
        body = tk.Frame(pad, bg=t.bg)
        body.pack(fill="x")

        _, sw_card = self._admin_card(pad, fill="x", pady=(SP_SM, SP_SM))
        tk.Label(sw_card, text="三条开关", anchor="w", fg=t.text_sec, bg=t.card,
                 font=CUI.f("caption", True)).pack(fill="x")
        for key, label, default_text in DEVICE_ACTION_SWITCHES:
            row = tk.Frame(sw_card, bg=t.card)
            row.pack(fill="x", pady=CUI.sp("xxs"))
            left = tk.Frame(row, bg=t.card)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=label, anchor="w", fg=t.text, bg=t.card,
                     font=CUI.f("body", True)).pack(anchor="w")
            sub = tk.Label(left, text="未读取（GET %s/device-actions）" % ADMIN_API_PREFIX,
                           anchor="w", fg=t.text_muted, bg=t.card, font=CUI.f("caption"))
            sub.pack(anchor="w")
            var = tk.BooleanVar(value=False)
            sw = CUI.Switch(row, t, variable=var, bg=t.card,
                            command=lambda k=key, v=var: self._save_device_action_switch(k, v))
            sw.pack(side="right", padx=(SP_SM, 0))
            self._da_switch[key] = {"var": var, "sw": sw, "sub": sub,
                                    "default": default_text, "present": None}

        for which, title, sample in DEVICE_ACTION_SECTIONS:
            self._da_list_section(which, pad, title, sample)

        self._admin_meta["device_actions"] = {"status": hint, "login_label": login_label,
                                              "login_dot": login_dot, "body": body,
                                              "path": ADMIN_API_PREFIX + "/device-actions",
                                              "loader": self._load_device_actions}
        return page

    def _da_list_section(self, which: str, parent, title: str, sample: str) -> None:
        """一份名单的区块卡：标题行（租户/上限）+ 输入行 + 提示行 + 列表行容器。"""
        t = self.theme
        _, card = self._admin_card(parent, fill="x", pady=(0, SP_SM))
        head = tk.Frame(card, bg=t.card)
        head.pack(fill="x")
        tk.Label(head, text=title, anchor="w", fg=t.text, bg=t.card,
                 font=CUI.f("h2", True)).pack(side="left")
        cap = tk.Label(head, text="", fg=t.text_muted, bg=t.card, font=CUI.f("caption"))
        cap.pack(side="right")
        row = tk.Frame(card, bg=t.card)
        row.pack(fill="x", pady=(SP_XS, 0))
        var = tk.StringVar()
        ent = tk.Entry(row, textvariable=var, bg=t.entry_bg, fg=t.text,
                       insertbackground=t.text, relief="flat", highlightthickness=1,
                       highlightbackground=t.hairline, highlightcolor=t.accent,
                       font=CUI.f("body"))
        ent.pack(side="left", fill="x", expand=True, ipady=CUI.sp("xxs"))
        ent.bind("<Return>", lambda _e, w=which: self._add_device_action_item(w))
        RoundedButton(row, t, "添加", variant="primary", height=28, font_size=10,
                      command=lambda w=which: self._add_device_action_item(w)
                      ).pack(side="left", padx=(SP_XS, 0))
        hint_text = "提示：%s（回车或点「添加」提交，名单以服务端返回为准）" % sample
        hint = tk.Label(card, text=hint_text, anchor="w", justify="left", fg=t.text_muted,
                        bg=t.card, font=CUI.f("caption"))
        hint.pack(fill="x", pady=(2, 0))
        CUI.Tooltip(ent, lambda s=sample: s)
        tk.Frame(card, bg=t.hairline, height=1).pack(fill="x", pady=(SP_XS, 0))
        lst = tk.Frame(card, bg=t.card)
        lst.pack(fill="x")
        self._da_entry[which] = var
        self._da_hint[which] = hint
        self._da_hint_default[which] = hint_text
        self._da_cap[which] = cap
        self._da_list_host[which] = lst

    def _da_note(self, which: str, text: str = "", kind: str = "info") -> None:
        """区块内提示行（后端 reason 原样落在这里，不弹栈也不吞掉）；text 空则复位成默认提示。"""
        lab = self._da_hint.get(which)
        if lab is None:
            return
        t = self.theme
        color = {"ok": t.success, "warn": t.warning, "err": t.error}.get(kind, t.text_muted)
        try:
            lab.config(text=text or self._da_hint_default.get(which, ""), fg=color)
        except Exception:
            _safe_traceback()

    def _render_device_action_list(self, which: str) -> None:
        t = self.theme
        host = self._da_list_host.get(which)
        if host is None:
            return
        _clear_frame(host)
        names = self._da_rows.get(which) or []
        if not names:
            tk.Label(host, text="名单为空（后端返回 0 条）", anchor="w", fg=t.text_muted,
                     bg=t.card, font=CUI.f("caption")).pack(fill="x", pady=CUI.sp("xxs"))
            return
        for i, name in enumerate(names):
            zebra = t.card if i % 2 == 0 else _hex_mix(t.card, t.card_hover, 0.55)
            row = tk.Frame(host, bg=zebra)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=name, anchor="w", fg=t.text, bg=zebra,
                     font=CUI.f("body")).pack(side="left", fill="x", expand=True)
            RoundedButton(row, t, "删除", variant="neutral", height=24, font_size=9,
                          command=lambda w=which, n=name: self._del_device_action_item(w, n)
                          ).pack(side="right")

    @staticmethod
    def _da_names(value) -> list:
        """契约里两份名单是字符串数组；万一后端给的是对象也照字面显示，绝不静默丢条目。"""
        out = []
        for item in (value or []):
            text = (item if isinstance(item, str) else str(item)).strip()
            if text and text not in out:
                out.append(text)
        return out

    def _apply_device_actions(self, data: dict) -> None:
        """整包落地：名单 + 上限/租户标题行 + 三条开关（全部取后端字段，缺就显示缺）。"""
        self._da_snapshot = data
        self._da_rows["targets"] = self._da_names(data.get("targets"))
        self._da_rows["plugins"] = self._da_names(data.get("plugins"))
        self._apply_device_action_switches(data.get("switches"))
        limits = data.get("limits") if isinstance(data.get("limits"), dict) else {}
        tenant = data.get("tenant_id")
        for which, limit_key in (("targets", "targets_max"), ("plugins", "plugins_max")):
            cap = limits.get(limit_key)
            try:
                self._da_cap[which].config(text="租户＝%s ｜ %s" % (
                    "—" if tenant is None else tenant,
                    "上限未知" if cap is None else "最多 %s 个" % cap))
            except Exception:
                _safe_traceback()
            self._da_note(which)
            self._render_device_action_list(which)

    def _apply_device_action_switches(self, switches) -> None:
        sw = switches if isinstance(switches, dict) else {}
        present = sw.get("rows_present")
        present = present if isinstance(present, dict) else None
        t = self.theme
        for key, _label, default_text in DEVICE_ACTION_SWITCHES:
            ent = self._da_switch.get(key)
            if ent is None:
                continue
            if present is not None:
                ent["present"] = bool(present.get(key))
            on_text = "开" if bool(sw.get(key)) else "关"
            if ent["present"] is None:
                sub = "后端生效值：%s（是否显式设置：后端未返回）" % on_text
            elif ent["present"]:
                sub = "已显式设置（后端生效值：%s）" % on_text
            else:
                sub = "未显式设置（默认：%s）" % default_text
            try:
                ent["var"].set(bool(sw.get(key)))
                ent["sw"]._draw()
                ent["sub"].config(text=sub,
                                  fg=t.text_sec if ent["present"] else t.text_muted)
            except Exception:
                _safe_traceback()

    def _load_device_actions(self) -> None:
        path = ADMIN_API_PREFIX + "/device-actions"

        def ok(data):
            self._apply_device_actions(data if isinstance(data, dict) else {})
            self._set_admin_status("device_actions", "已读取（GET %s）" % path, "ok")

        self._set_admin_status("device_actions", "加载中… GET %s" % path, "pending")
        self._run_admin("读取行动通道", lambda: _admin_request("GET", "/device-actions"),
                        ok, "device_actions")

    def _save_device_action_switch(self, key: str, var) -> None:
        label = DEVICE_ACTION_LABELS.get(key, key)
        wanted = bool(var.get())
        ent = self._da_switch.get(key) or {}
        snapshot = self._da_snapshot.get("switches") if isinstance(self._da_snapshot, dict) else None
        # 滑块先退回上一次读到的服务器值，只由回包/回读落地：这一页是危险动作的总闸，
        # 后端还没答应之前界面不能先替它说「已关」（写失败时最容易误导人的一刻）。
        var.set(bool((snapshot or {}).get(key)))
        try:
            if ent.get("sw") is not None:
                ent["sw"]._draw()
        except Exception:
            _safe_traceback()

        def ok(data):
            payload = data if isinstance(data, dict) else {}
            if isinstance(payload.get("switches"), dict):
                self._apply_device_action_switches(payload["switches"])
            else:
                self._load_device_actions()  # 回包没带 switches 就整页回读，不拿本地值充数
            if payload.get("ok") is False:
                self._set_admin_status("device_actions", "后端拒绝修改开关 %s：%s"
                                       % (key, payload.get("reason")), "err")
                return
            self._set_admin_status("device_actions", "已保存开关 %s＝%s（PUT %s/switches）"
                                   % (label, "开" if wanted else "关",
                                      ADMIN_API_PREFIX + "/device-actions"), "ok")
            self._set_msg("已保存开关 %s=%s" % (label, "开" if wanted else "关"))

        self._run_admin("保存开关 %s" % label,
                        lambda: _admin_request("PUT", "/device-actions/switches",
                                               {"key": key, "enabled": wanted}),
                        ok, "device_actions")

    def _replace_da_rows(self, which: str, payload: dict) -> None:
        """POST/DELETE 的回包都带最新整份名单（含删空时的 []），照它刷新，不再自行增删。"""
        if isinstance(payload.get(which), list):
            self._da_rows[which] = self._da_names(payload[which])
        self._render_device_action_list(which)

    def _add_device_action_item(self, which: str) -> None:
        noun = DEVICE_ACTION_NOUNS.get(which, which)
        raw = str(self._da_entry[which].get() or "").strip()
        if not raw:
            self._da_note(which, "先在输入框里填写%s再点添加" % noun, "warn")
            return

        def ok(data):
            payload = data if isinstance(data, dict) else {}
            self._replace_da_rows(which, payload)  # 先落地回包名单，条数才不是加之前的旧值
            if payload.get("ok") is False:
                self._da_note(which, "添加失败：%s"
                              % str(payload.get("reason") or "后端未返回 reason"), "err")
            else:
                self._da_note(which, "已添加 %s（当前 %d 条）"
                              % (raw, len(self._da_rows[which])), "ok")
                self._da_entry[which].set("")

        self._run_admin("添加%s" % noun,
                        lambda: _admin_request("POST", "/device-actions/%s" % which,
                                               {DEVICE_ACTION_FIELDS[which]: raw}),
                        ok, "device_actions")

    def _del_device_action_item(self, which: str, name: str) -> None:
        noun = DEVICE_ACTION_NOUNS.get(which, which)

        def ok(data):
            payload = data if isinstance(data, dict) else {}
            if payload.get("ok") is False:
                self._da_note(which, "删除失败：%s"
                              % str(payload.get("reason") or "后端未返回 reason"), "err")
            else:
                self._da_note(which, "已删除 %s（移除 %s 条）"
                              % (name, payload.get("removed")), "ok")
            self._replace_da_rows(which, payload)

        self._run_admin("删除%s" % noun,
                        lambda: _admin_request("DELETE", "/device-actions/%s" % which,
                                               {DEVICE_ACTION_FIELDS[which]: name}),
                        ok, "device_actions")

    # ── ttk 主题（Checkbutton / Entry 仍用 ttk）──

    def _style_ttk(self):
        t = self.theme
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCheckbutton", background=t.card, foreground=t.text, font=CUI.f("body"))
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
                elif kind == "admin_ok":
                    # 先释放串行闸门再回投：回调里常接「保存后回读」（_load_xxx），
                    # 若等 admin_done 才解锁，这次回读会被 _run_admin 当成重复请求挡掉。
                    self._admin_busy = False
                    try:
                        if item[2]:
                            item[2](item[1])
                    except Exception as e:
                        _safe_traceback()
                        self._set_msg("管理面渲染异常: %s" % e)
                elif kind == "admin_err":
                    self._admin_busy = False
                    try:
                        self._on_admin_error(item[1], item[2], item[3] if len(item) > 3 else "")
                    except Exception:
                        _safe_traceback()
                elif kind == "admin_done":
                    self._admin_busy = False
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
        # C1：当前文件刚被轮转为空时优先用系统查看器打开 .1，避免打开到空白文件
        _path = _log_read_path(STDERR_LOG)
        if not os.path.exists(_path):
            return
        if os.name == "nt":
            os.startfile(_path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", _path])
        else:
            subprocess.Popen(["xdg-open", _path])

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
        tk.Label(frame, text="界面主题", bg=t.card, fg=t.text, font=CUI.f("body", True)).grid(
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

        tk.Label(frame, text="守护检测间隔（秒）", bg=t.card, fg=t.text, font=CUI.f("body")).grid(
            row=2, column=0, sticky="w", pady=(0, SP_XS), padx=(0, SP_LG))
        wd_var = tk.StringVar(value=str(cfg.get("watchdog_interval_sec", 120)))
        ttk.Entry(frame, textvariable=wd_var, width=14).grid(row=2, column=1, sticky="w", pady=(0, SP_XS))

        tk.Label(frame, text="界面刷新间隔（秒）", bg=t.card, fg=t.text, font=CUI.f("body")).grid(
            row=3, column=0, sticky="w", pady=(0, SP_MD), padx=(0, SP_LG))
        rf_var = tk.StringVar(value=str(int(cfg.get("controller_refresh_ms", DEFAULT_REFRESH_MS)) // 1000))
        ttk.Entry(frame, textvariable=rf_var, width=14).grid(row=3, column=1, sticky="w", pady=(0, SP_MD))

        tk.Label(frame, text="修改后立即生效：\n守护间隔下个检测周期生效，界面刷新即时生效。",
                 bg=t.card, fg=t.text_sec, font=CUI.f("caption")).grid(
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
                # C3（2026-09-06）：健康卡副标题接运行期活性（/liveness）；后台线程容错，
                # 端点不可用返回默认 dict，_apply_refresh 回落现状（不改健康卡语义）。
                stats["liveness"] = _safe(_fetch_liveness, {"ok": False, "stalled": False, "summary": "", "level": 0})
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
        self._sync_log_empty(False)
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
        # 0 档不能等于"看不见"：用 hairline 而不是 surface_alt，空格子才读得出"当天没有量"
        return [t.hairline,
                _hex_mix(t.card, t.accent, .30),
                _hex_mix(t.card, t.accent, .55),
                _hex_mix(t.card, t.accent, .78),
                t.accent]

    def _draw_heat_legend(self, c, t, w, colors) -> None:
        """色阶图例：画在画布右上角，跟着 `_draw_heatmap` 一起重绘（换主题不会留旧色）。

        不写图例的话没人知道 0 档是"当天没有量"，而不是"这块还没画"。
        """
        s = CUI.px(9)
        step = s + CUI.px(3)
        pad_txt = CUI.px(20)
        x = w - CUI.px(8) - (pad_txt * 2 + step * 5)
        y = CUI.px(11)
        c.create_text(x, y, anchor="w", text="少", fill=t.text_muted, font=CUI.f("nano"))
        x0 = x + pad_txt
        for i in range(5):
            c.create_rectangle(x0 + i * step, y - s / 2, x0 + i * step + s, y + s / 2,
                               fill=colors[i], outline=t.hairline)
        c.create_text(x0 + step * 5 + CUI.px(4), y, anchor="w", text="多",
                      fill=t.text_muted, font=CUI.f("nano"))

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
            c.create_text(w / 2, h / 2, text="暂无数据", fill=t.text_muted, font=CUI.f("body"))
            return
        self._heat_retry = 0
        vals = _heat_series(days, self._heat_mode)
        levels = _heat_levels(vals)
        self._heat_series_vals, self._heat_levels = vals, levels
        # 轴位一律按 DPI 给：星期标签是两个字，左槽 30px 在 150% 下会把"周一"切成"号一"
        left, top, bottom = CUI.px(46), CUI.px(26), CUI.px(26)
        ncols = math.ceil(len(days) / 7)
        avail_w = w - left - CUI.px(6)
        # 格子同时受列宽/行高约束，设下限保证可见、设上限避免过大；17 列天然偏窄，左对齐
        cell = max(CUI.px(9), min(avail_w / ncols, (h - top - bottom) / 7, CUI.px(30)))
        gap = max(CUI.px(2), cell * .12)
        size = cell - gap
        x0 = left
        colors = self._heat_cell_colors()
        self._draw_heat_legend(c, t, w, colors)
        show_week = cell >= CUI.px(11)
        if show_week:
            for r, name in ((0, "周一"), (2, "周三"), (4, "周五")):
                c.create_text(x0 - 6, top + r * cell + cell / 2, anchor="e",
                              text=name, fill=t.text_muted, font=CUI.f("nano"))
        prev_month = None
        for i, d in enumerate(days):
            col, row = i // 7, i % 7
            x = x0 + col * cell + gap / 2
            y = top + row * cell + gap / 2
            mon = int(d["date"][5:7])
            if row == 0 and mon != prev_month:
                c.create_text(x, h - CUI.px(10), anchor="w", text=f"{mon}月",
                              fill=t.text_muted, font=CUI.f("nano"))
                prev_month = mon
            pts = _rr_points(x, y, x + size, y + size, 3)
            if d["future"]:
                c.create_polygon(pts, smooth=True, splinesteps=8, fill="",
                                 outline=t.hairline, tags=("cell", str(i)))
            else:
                # 空格子也描一圈淡边：不描的话"没有数据"和"没画出来"在界面上长得一样
                c.create_polygon(pts, smooth=True, splinesteps=8,
                                 fill=colors[levels[i]], outline=t.hairline,
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
                           font=CUI.f("micro"), padx=8, pady=5, bd=0)
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
        self._sync_log_empty(bool(log_text.strip()))

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
        # C3（2026-09-06）：健康卡副标题接 /liveness——显示「调度 Xs 前 / 微信桥 Xs 前」，停滞变色。
        liv = stats.get("liveness")
        if isinstance(liv, dict) and liv.get("ok"):
            lvl = liv.get("level", 0)
            live_fg = t.success if lvl == 0 else (t.warning if lvl == 1 else t.error)
            self.c_health.config(text=liv["summary"] if liv.get("summary") else "HTTP 探活")
            if lvl in (1, 2):  # 停滞/预警 → 健康值与顶栏标题一并转告警色
                self.v_health.config(fg=live_fg)
                self.header_health_label.config(fg=live_fg)
        else:
            # 端点不可用 → 回落默认副标题（保持「正常运行」语义）
            self.c_health.config(text="HTTP 探活")
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
