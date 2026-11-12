"""拥爱服务器控制台 · 网页壳的桌面化三件套（W5-lite）。

1. 单实例：再点图标只把已有窗口叫到前面，不再开第二个。
2. 任务栏身份：AppUserModelID + 真图标，不再顶着 pythonw。
3. 记住窗口：关闭时存几何，启动时还原并夹回屏幕内。

状态文件放在 `%LOCALAPPDATA%\\AMBRACE\\console_web.json`：
**不进仓库、不进 backend/data、不碰 server_config.json 的任何键**。
"""
import ctypes
import ctypes.wintypes
import json
import os
import sys
import time

TITLE = "拥爱服务器控制台"
APP_ID = "AMBRACE.Console.Web"
MUTEX_NAME = "Local\\AMBRACEConsoleWeb"
MIN_W, MIN_H = 1150, 700          # 与旧壳同一把最小尺寸
DEFAULT_W, DEFAULT_H = 1360, 880  # 与旧壳默认窗口一致
ERROR_ALREADY_EXISTS = 183


def state_path():
    """可被环境变量覆盖（测试与多实例并存时用），默认落在用户本地数据目录。"""
    override = os.environ.get("AMBRACE_CONSOLE_STATE")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "AMBRACE", "console_web.json")


def load_state():
    try:
        with open(state_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(**kw):
    """读-合-写：状态文件坏了也不能把已有键丢掉。"""
    p = state_path()
    cur = load_state()
    cur.update(kw)
    cur["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


MINIMIZED_SENTINEL = -10000
"""最小化哨兵坐标的判据（Windows 最小化时会报 -32000／-21333 一类的假位置）。

    启动那一刻窗口还没摆好，moved 事件就会报这种假坐标；收进状态文件，
    「记住窗口」就废了——下次开窗被顶到左上角，而不是用户上次放的地方。
    比它更小的负坐标才当哨兵：多显示器 setups 里窗口真的可能在主屏左边几千像素。
"""


def _is_sentinel(v):
    try:
        return int(v) <= MINIMIZED_SENTINEL
    except (TypeError, ValueError):
        return False


def clamp_geometry(x, y, w, h, work_area):
    """还原窗口时：尺寸夹到最小值以上、屏幕以内；位置保证标题栏还在屏上。

    work_area = (left, top, right, bottom)，虚拟桌面去掉任务栏那圈。
    """
    wl, wt, wr, wb = work_area
    w = max(MIN_W, min(int(w or DEFAULT_W), max(MIN_W, wr - wl)))
    h = max(MIN_H, min(int(h or DEFAULT_H), max(MIN_H, wb - wt)))
    if x is None or y is None or _is_sentinel(x) or _is_sentinel(y):
        # 存着的哨兵坐标＝「没记住位置」，宁可居中，也不要开在左上角
        x = wl + max(0, (wr - wl - w) // 2)
        y = wt + max(0, (wb - wt - h) // 2)
    # 整窗不许探出屏幕：右/下边界最多顶到工作区边（窗口满屏时 x 就只能是 wl）
    # 上一版只夹了「左边在屏内」，于是 260 + 1706 宽的窗口被系统自己推到 -76 才不越界
    x = max(wl, min(int(x), wr - w))
    y = max(wt, min(int(y), wb - h))
    return {"x": int(x), "y": int(y), "width": w, "height": h}


def stored_geometry(state=None, work_area=None):
    state = state if state is not None else load_state()
    if work_area is None:
        try:
            work_area = logical_work_area()
        except Exception:  # noqa: BLE001  量不到屏幕就别认存过的坐标，宁可回默认窗口
            return {"x": 0, "y": 0, "width": DEFAULT_W, "height": DEFAULT_H}
    g = state.get("geometry") if isinstance(state.get("geometry"), dict) else {}
    return clamp_geometry(g.get("x"), g.get("y"), g.get("width"), g.get("height"), work_area)


def ensure_dpi_aware():
    """声明 DPI 感知，否则 GetSystemMetrics 回的是虚拟化后的假尺寸
    （150% 缩放下 2560 会被报成 1707），窗口位置就会夹错。"""
    ok = False
    try:
        # PER_MONITOR_AWARE_V2 = -4
        ok = bool(ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        try:
            ok = bool(ctypes.windll.user32.SetProcessDPIAware())
        except Exception:  # noqa: BLE001
            ok = False
    return ok


def virtual_work_area():
    """SM_XVIRTUALSCREEN/Y + CX/CY，再让出任务栏（SPI_GETWORKAREA 主屏）。"""
    u = ctypes.windll.user32
    left = u.GetSystemMetrics(76)
    top = u.GetSystemMetrics(77)
    wide = u.GetSystemMetrics(78)
    high = u.GetSystemMetrics(79)
    try:
        class Rect(ctypes.Structure):
            _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                        ("r", ctypes.c_long), ("b", ctypes.c_long)]
        rc = Rect()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0):
            return (min(left, rc.l), min(top, rc.t), max(left + wide, rc.r),
                    max(top + high, rc.b))
    except Exception:  # noqa: BLE001
        pass
    return (left, top, left + wide, top + high)


def dpi_scale():
    """主屏的 DPI 倍数（150% → 1.5）。取不到就按 1 算，别抛到外面。

    pywebview 的 x/y/width/height 是**逻辑**像素（物理 ÷ 倍数），
    Win32 量到的屏幕是**物理**像素；夹窗口必须两边同一单位，
    150% 屏上不换算就会把窗口夹到屏幕外。
    """
    try:
        u = ctypes.windll.user32
        dpi = 0
        try:
            dpi = int(u.GetDpiForWindow(u.GetDesktopWindow()) or 0)
        except Exception:  # noqa: BLE001
            dpi = 0
        if not dpi:
            dpi = int(u.GetDpiForSystem() or 0)
        return dpi / 96.0 if dpi > 0 else 1.0
    except Exception:  # noqa: BLE001
        return 1.0


def logical_work_area():
    """工作区换算成逻辑像素——与存进状态文件里的几何同一把尺。"""
    s = dpi_scale() or 1.0
    wl, wt, wr, wb = virtual_work_area()
    return (int(wl / s), int(wt / s), int(wr / s), int(wb / s))


def find_window(title=TITLE, pid=None):
    """按标题＋本进程找顶层窗口；拿不到就回 0。"""
    pid = pid if pid is not None else os.getpid()
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _l):
        wp = ctypes.wintypes.DWORD()
        # pointer() 而不是 byref()：out 参数写法等价，但测试替身写得到这个值
        u.GetWindowThreadProcessId(hwnd, ctypes.pointer(wp))
        if wp.value != pid or not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(int(hwnd))
        return True

    u.EnumWindows(cb, 0)
    return found[0] if found else 0


def focus_hwnd(hwnd):
    """把已存在的窗口叫到前面（最小化先还原）。"""
    if not hwnd:
        return False
    u = ctypes.windll.user32
    u.ShowWindow(int(hwnd), 9)        # SW_RESTORE
    u.SetForegroundWindow(int(hwnd))
    return True


def already_running():
    """命名互斥体：拿到句柄且不是 ERROR_ALREADY_EXISTS 才算第一个实例。"""
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, MUTEX_NAME)
    err = k32.GetLastError()
    if h and err != ERROR_ALREADY_EXISTS:
        _KEEP_MUTEX.append(h)         # 进程活着就别关，否则第二个实例照样进来
        return False
    return True


_KEEP_MUTEX = []


def set_app_id(app_id=APP_ID):
    """任务栏归到我们自己名下，不再显示 pythonw。"""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_window_icon(hwnd, ico_path):
    """pywebview 这版没有 icon 参数，直接发 WM_SETICON。"""
    if not hwnd or not ico_path or not os.path.exists(ico_path):
        return False
    u = ctypes.windll.user32
    LR_LOADFROMFILE, LR_DEFAULTSIZE, IMAGE_ICON = 0x0010, 0x0040, 1
    try:
        # 必须声明返回类型：默认按 c_int 取，64 位句柄会被截成一个看着像数的假句柄，
        # WM_SETICON 收到假句柄就当没设，任务栏还是 pythonw 那张
        u.LoadImageW.restype = ctypes.c_void_p
        hicon = u.LoadImageW(None, ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            return False
        for which in (0, 1, 2):        # ICON_SMALL / ICON / ICON_BIG
            u.SendMessageW(int(hwnd), 0x0080, which, ctypes.c_void_p(hicon))
        return True
    except Exception:  # noqa: BLE001
        return False


# ── 边缘缩放：frameless 把 FormBorderStyle 设成 None 时，WS_THICKFRAME 也一起被摘掉，
#    指针落在窗口边缘就没有可拖的命中区，窗口只能最大化/还原，不能自由改大小。
#    这里把样式位补回去，再发一次 WM_FRAMECHANGED 让系统按新样式重算非客户区。
#    最小尺寸仍由 WinForms 的 MinimumSize（WM_GETMINMAXINFO）管，拖不到 1150×700 以下。
GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


def _sig(fn, argtypes, restype):
    """给 API 声明签名（64 位句柄必须显式声明，否则会被截成 int）。
    测试替身是没有 argtypes 的普通函数——那种情况静默跳过，不影响逻辑。"""
    try:
        fn.argtypes = argtypes
        fn.restype = restype
    except (AttributeError, TypeError):
        pass
    return fn


def _user32():
    u = ctypes.windll.user32
    for name, args, ret in (
            ("GetWindowLongW", [ctypes.c_void_p, ctypes.c_int], ctypes.c_long),
            ("SetWindowLongW", [ctypes.c_void_p, ctypes.c_int, ctypes.c_long], ctypes.c_long),
            ("SetWindowPos", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, ctypes.c_uint], ctypes.c_bool),
            ("GetWindowRect", [ctypes.c_void_p, ctypes.POINTER(ctypes.wintypes.RECT)],
             ctypes.c_bool)):
        fn = getattr(u, name, None)      # 替身只实现自己关心的那几个，不许因为缺一个就整体炸
        if fn is not None:
            _sig(fn, args, ret)
    return u


def window_style(hwnd):
    """读窗口样式位；拿不到（无句柄/无 API）返回 0。"""
    if not hwnd:
        return 0
    try:
        return int(_user32().GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE))
    except Exception:  # noqa: BLE001
        return 0


# 补回 WS_THICKFRAME 之后，Win11 会给窗口画一圈 1px 的浅色边框＋圆角（实测：客户区比窗口
# 小 10px/边）。这圈线跟自绘页头不搭，用 DWM 的属性把它设成"不画"，缩放能力一点不减
# （命中测试看的是样式位，不是这圈颜色）。Win10 没有这个属性，失败就当没发生。
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_NONE = 0xFFFFFFFE


def hide_resize_border(hwnd):
    """让 DWM 不要给窗口描边（只影响观感）。返回是否设置成功。"""
    if not hwnd:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        _sig(dwm.DwmSetWindowAttribute,
             [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint], ctypes.c_long)
        val = ctypes.c_uint(DWMWA_COLOR_NONE)
        rc = dwm.DwmSetWindowAttribute(ctypes.c_void_p(int(hwnd)), DWMWA_BORDER_COLOR,
                                       ctypes.byref(val), ctypes.sizeof(val))
        return int(rc) == 0
    except Exception:  # noqa: BLE001
        return False


def enable_resize(hwnd):
    """把 WS_THICKFRAME 补回去，让指针在边缘能拖动改大小。

    返回"补完之后样式里确实有这一位"——不是"调用成功"。真窗口上如果 WinForms 又把它
    盖回去，这里会返回 False，界面上就别再宣称能缩放。
    """
    if not hwnd:
        return False
    try:
        u = _user32()
        style = int(u.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE))
        if not style & WS_THICKFRAME:
            u.SetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE, style | WS_THICKFRAME)
            u.SetWindowPos(ctypes.c_void_p(int(hwnd)), None, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                           | SWP_FRAMECHANGED)
    except Exception:  # noqa: BLE001
        return False
    ok = bool(window_style(hwnd) & WS_THICKFRAME)
    if ok:
        hide_resize_border(hwnd)      # 观感：别给自绘页头外面套一圈系统描边
    return ok


def drag_window(dx, dy):
    """按增量挪窗口（dx/dy 是逻辑像素，来自页面里指针的 screen 差）。

    为什么不用 pywebview 的 `window.move()`：
    ① 它在 WinForms 上把 `None` 传给 `SetWindowPos` 的 int 参数，某些组合直接抛
       （实测 A 组就抛 ArgumentError，而窗口恰好靠别的路子还能动，很容易误判）；
    ② 它挪的是缓存坐标，连续拖动会漂。
    这里每次读真实窗口矩形再挪，天然不漂；偏移自己按 DPI 换算成物理像素。
    """
    if not dx and not dy:
        return False
    hwnd = find_window()
    if not hwnd:
        return False
    try:
        u = _user32()
        r = ctypes.wintypes.RECT()
        if not u.GetWindowRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(r)):
            return False
        s = dpi_scale()
        u.SetWindowPos(ctypes.c_void_p(int(hwnd)), None,
                       int(r.left + round(dx * s)), int(r.top + round(dy * s)), 0, 0,
                       SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
        return True
    except Exception:  # noqa: BLE001
        return False


def handoff_or_stay(state=None, pid=None):
    """第二个实例：把已有窗口叫到前面并退场；叫不动（状态是陈的）就自己当主实例。"""
    state = state if state is not None else load_state()
    hwnd = int(state.get("hwnd") or 0)
    if not hwnd:
        return "primary"
    if find_window(TITLE, pid=state.get("pid")):
        return "handoff" if focus_hwnd(hwnd) else "primary"
    return "primary"



_last_remember = [0.0]
_geom = {}       # 最近一次知道的几何，事件一点点拼齐


def _geom_ok(g):
    """四件齐全且不小于最小尺寸，才算一条能用的几何。"""
    if not g or set(g) != {"x", "y", "width", "height"}:
        return False
    return int(g["width"]) >= MIN_W and int(g["height"]) >= MIN_H


def seed_geometry(geo):
    """启动时把「我们要求的位置」记下来当底座。

    moved 只给 x/y，resized 只给宽高——单靠一条事件拼不出完整几何，
    而在别的线程读窗口属性又要冒跨线程的险。
    """
    _geom.clear()
    for k in ("x", "y", "width", "height"):
        if isinstance(geo, dict) and geo.get(k) is not None:
            _geom[k] = int(geo[k])
    return dict(_geom)


def current_geometry(win):
    """直接问窗口取几何；取不到或不合尺寸就回 None，别把 0 当坐标存。

    最小化/未摆好时窗口会报哨兵坐标，那不是位置——采信它就等于毒掉「记住窗口」。
    """
    try:
        g = {"x": int(win.x), "y": int(win.y), "width": int(win.width), "height": int(win.height)}
    except (AttributeError, TypeError, ValueError):
        return None
    if _is_sentinel(g["x"]) or _is_sentinel(g["y"]):
        return None
    return g if _geom_ok(g) else None


def remember(win, force=False, patch=None):
    """存几何：拖动时最多 2 秒写一次，关窗那一下必须写。

    patch 是事件刚给的一半数（moved→x/y，resized→宽高）。尺寸低于最小值的多半是
    拖拽中途的临时值，整键丢掉——收了既写不进盘，还会把攒着的好底座一起带脏。
    """
    for k, v in (patch or {}).items():
        if v is None or k not in ("x", "y", "width", "height"):
            continue
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if (k == "width" and iv < MIN_W) or (k == "height" and iv < MIN_H):
            continue
        if k in ("x", "y") and _is_sentinel(iv):
            continue          # 最小化/未摆好时的假坐标，收了就把位置记坏
        _geom[k] = iv
    if not _geom_ok(_geom):
        asked = current_geometry(win)
        if asked:
            _geom.update(asked)
    if not _geom_ok(_geom):
        return False
    now = time.time()
    if not force and now - _last_remember[0] < 2.0:
        return False
    _last_remember[0] = now
    return save_state(geometry=dict(_geom))


def forget_hwnd():
    """退出时把 hwnd 抹掉，别让下一个实例去叫一个已经不存在的窗口。"""
    return save_state(hwnd=0, pid=0)
