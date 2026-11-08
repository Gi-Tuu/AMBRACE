"""窗口边缘缩放（路线 1：补回 WS_THICKFRAME）的闸门。

背景：pywebview 的 frameless 把 WinForms 的 FormBorderStyle 设成 None 时，
WS_THICKFRAME 一并被摘掉——指针落在窗口边缘没有命中区，窗口只能最大化/还原。
这里补回样式位并发 WM_FRAMECHANGED，最小尺寸仍由 MinimumSize 管。

两条要求：
① win32 层：样式位真的补上了、真发了 FRAMECHANGED、返回值说的是"补完之后真有这一位"；
② 接线层：窗口亮起来（shown）时真的调了，而且用的是 find_window 拿到的那个句柄。
"""
from __future__ import annotations

import re
import sys
import types

import pytest

import console_app
import console_desktop as cd

SRC = open(cd.__file__, encoding="utf-8").read()
APP = open(console_app.__file__, encoding="utf-8").read()


class FakeU32:
    """最小的 user32 替身：只认 GetWindowLongW / SetWindowLongW / SetWindowPos。

    真 API 收到的是 c_void_p，替身这边要自己拆出整数值（直接 int(c_void_p) 会抛）。
    """

    def __init__(self, style=0x00C00000, keep=True):
        self.style = style
        self.keep = keep          # False＝SetWindowLongW 写不进去（模拟被 WinForms 盖回）
        self.calls = []

    @staticmethod
    def _h(x):
        return int(getattr(x, "value", x) or 0)

    def GetWindowLongW(self, hwnd, idx):
        self.calls.append(("get", self._h(hwnd), idx))
        return self.style

    def SetWindowLongW(self, hwnd, idx, val):
        self.calls.append(("set", self._h(hwnd), idx, val))
        if self.keep:
            self.style = val
        return 0

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("pos", self._h(hwnd), flags))
        return True

    def GetWindowRect(self, hwnd, rect_ptr):
        self.calls.append(("rect", self._h(hwnd)))
        try:
            rect_ptr._obj.left, rect_ptr._obj.top = 100, 200
        except AttributeError:
            pass
        return True


def _wire(monkeypatch, u32, dwm=None):
    """把 windll 换成替身；不给 dwm 就模拟"这台机器没有 dwmapi"。"""
    ns = {"user32": u32}
    if dwm is not None:
        ns["dwmapi"] = dwm
    monkeypatch.setattr(cd.ctypes, "windll", types.SimpleNamespace(**ns))
    return u32


# ───── ① win32 层 ─────
def test_gate_constants_are_the_windows_ones():
    assert cd.GWL_STYLE == -16, "GWL_STYLE 取错了位"
    assert cd.WS_THICKFRAME == 0x00040000, "WS_THICKFRAME 值不对"
    assert cd.SWP_FRAMECHANGED == 0x0020 and cd.SWP_NOSIZE == 0x0001, "SWP 标志位不对"


def test_gate_enable_resize_sets_the_bit_and_redraws_the_frame(monkeypatch):
    u = _wire(monkeypatch, FakeU32(style=0x00C00000))     # 有 CAPTION，没有 THICKFRAME
    assert cd.enable_resize(1234) is True
    sets = [c for c in u.calls if c[0] == "set"]
    assert sets and sets[0][1] == 1234, "没往这个句柄上写样式：%s" % u.calls
    assert sets[0][3] & cd.WS_THICKFRAME, "写回去的样式里没有 WS_THICKFRAME：%s" % sets[0]
    assert sets[0][3] & 0x00C00000, "其它样式位被冲掉了：%s" % hex(sets[0][3])
    pos = [c for c in u.calls if c[0] == "pos"]
    assert pos and pos[0][2] & cd.SWP_FRAMECHANGED, "没让系统重算非客户区（FRAMECHANGED）"
    assert pos[0][2] & cd.SWP_NOSIZE and pos[0][2] & cd.SWP_NOMOVE, "改样式不该顺手改大小/位置"


def test_gate_enable_resize_is_idempotent(monkeypatch):
    u = _wire(monkeypatch, FakeU32(style=cd.WS_THICKFRAME | 0x00C00000))
    assert cd.enable_resize(7) is True
    assert not [c for c in u.calls if c[0] == "set"], "已经有这一位还去写一次：%s" % u.calls
    assert not [c for c in u.calls if c[0] == "pos"], "已经有这一位还发 FRAMECHANGED"


def test_gate_enable_resize_reports_failure_honestly(monkeypatch):
    """WinForms 若把样式盖回去，必须返回 False——不许报成功。"""
    _wire(monkeypatch, FakeU32(style=0x00C00000, keep=False))
    assert cd.enable_resize(9) is False, "样式没写进去却返回了 True"


def test_gate_enable_resize_never_raises(monkeypatch):
    class Boom:
        def __getattr__(self, _n):
            raise OSError("没有这个 API")

    monkeypatch.setattr(cd.ctypes, "windll", types.SimpleNamespace(user32=Boom()))
    assert cd.enable_resize(11) is False, "没有 API 也不能抛到外面"
    assert cd.enable_resize(0) is False, "没有句柄就是 False"


def test_gate_window_style_reads_what_the_setter_wrote(monkeypatch):
    _wire(monkeypatch, FakeU32(style=0))
    cd.enable_resize(3)
    assert cd.window_style(3) & cd.WS_THICKFRAME, "读回的样式里没有这一位"
    assert cd.window_style(0) == 0, "没有句柄要回 0，不许抛异常"


# ── 观感：补了边框之后 Win11 会描一圈浅色线，要把它设成"不画" ──
class FakeDwm:
    def __init__(self, rc=0, boom=False):
        self.calls = []
        self.rc = rc
        self.boom = boom

    def DwmSetWindowAttribute(self, hwnd, attr, ptr, size):
        if self.boom:
            raise OSError("没有这个属性")
        self.calls.append((int(getattr(hwnd, "value", hwnd) or 0), attr, size))
        # byref(x) 传进来的是 CArgObject，底下那个 c_uint 在 ._obj 上
        inner = getattr(ptr, "_obj", None)
        self.value = getattr(inner, "value", None)
        return self.rc


def test_gate_resize_border_is_hidden_with_dwm(monkeypatch):
    dwm = FakeDwm()
    _wire(monkeypatch, FakeU32(style=0x00C00000), dwm)
    assert cd.enable_resize(88) is True
    assert dwm.calls, "没有去关掉 DWM 描边"
    assert dwm.calls[0][1] == cd.DWMWA_BORDER_COLOR == 34, "属性号不对：%s" % (dwm.calls[0],)
    assert dwm.value == cd.DWMWA_COLOR_NONE == 0xFFFFFFFE, \
        "传的颜色不是「不画」：%s" % hex(dwm.value)


def test_gate_resize_still_works_when_dwm_says_no(monkeypatch):
    """Win10 没有 34 号属性：关不掉描边也不许影响缩放。"""
    dwm = FakeDwm(boom=True)
    _wire(monkeypatch, FakeU32(style=0), dwm)
    assert cd.enable_resize(99) is True, "dwmapi 抛异常把缩放也带崩了"
    assert cd.hide_resize_border(0) is False


# ───── ② 接线层：窗口亮起来时真的调了 ─────
class RecEvent:
    def __init__(self):
        self.items = []

    def __iadd__(self, fn):
        self.items.append(fn)
        return self

    def fire(self, *a):
        return [fn(*a) for fn in self.items]


class RecWin:
    def __init__(self):
        self.events = types.SimpleNamespace(shown=RecEvent(), moved=RecEvent(),
                                            resized=RecEvent(), closing=RecEvent())
        self.x, self.y, self.width, self.height = 100, 100, 1360, 880


def test_gate_on_shown_enables_resize_on_the_real_hwnd(tmp_path, monkeypatch):
    log = []
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(tmp_path / "s.json"))
    cd._geom.clear()
    cd._last_remember[0] = 0.0

    def spy(name, ret=None):
        def f(*a, **k):
            log.append((name,) + tuple(a))
            return ret
        return f

    for name, ret in (("ensure_dpi_aware", True), ("set_app_id", True),
                      ("already_running", False), ("handoff_or_stay", "primary"),
                      ("find_window", 424242), ("set_window_icon", True),
                      ("enable_resize", True)):
        monkeypatch.setattr(cd, name, spy(name, ret))
    monkeypatch.setattr(cd, "logical_work_area", lambda: (0, 0, 2560, 1600))

    fake = types.ModuleType("webview")
    win = RecWin()

    def create_window(title, **kw):
        log.append(("create", title, kw))
        return win

    fake.create_window = create_window
    fake.start = lambda **kw: log.append(("start", kw))
    monkeypatch.setitem(sys.modules, "webview", fake)

    console_app.run("dark", check=False)
    win.events.shown.fire()
    hits = [r for r in log if r[0] == "enable_resize"]
    assert hits, "窗口亮起来时没调 enable_resize：%s" % [r[0] for r in log]
    assert hits[0][1] == 424242, "用的不是 find_window 拿到的句柄：%s" % (hits[0],)
    # 顺序：拿到句柄 → 设图标 → 补缩放（记句柄之前）
    names = [r[0] for r in log if r[0] in ("find_window", "set_window_icon", "enable_resize")]
    assert names[:3] == ["find_window", "set_window_icon", "enable_resize"], names


def test_gate_resize_is_documented_next_to_the_frameless_flag():
    """frameless 那一行旁边要留一句话，免得以后有人把它删了不知道会失去什么。"""
    m = re.search(r"frameless=True,[^\n]*\n(?:[^\n]*\n){0,3}", APP)
    assert m, "找不到 frameless=True 那段"
    assert "enable_resize" in APP, "console_app 里没有调用 enable_resize"
    assert re.search(r"cd\.enable_resize\(hwnd\)", APP), "调用点没用 hwnd"
