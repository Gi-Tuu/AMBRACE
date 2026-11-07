"""W5-lite 桌面化三件套的测试：单实例、任务栏身份、记住窗口。

全部脱机跑：ctypes 换成假对象，不真开窗口、不真发 Win32 调用。
"""
from __future__ import annotations

import json
import os

import pytest

import console_desktop as cd


@pytest.fixture()
def state_file(tmp_path, monkeypatch):
    p = tmp_path / "AMBRACE" / "console_web.json"
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(p))
    cd._last_remember[0] = 0.0
    cd._geom.clear()          # 底座是模块级的，不清就会串到下一条用例
    return p


class FakeFunc:
    """可贴 restype 的假 API：真的 ctypes 函数对象也允许设 restype。"""

    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *a, **k):
        return self._fn(*a, **k)


class FakeUser32:
    def __init__(self, metrics=None, windows=None, dpi_ok=True, pid=4242, dpi=0):
        self.metrics = metrics or {}
        # windows：{句柄: 标题}；给了句柄就认为它是本进程的那个控制台窗口
        self.windows = windows if windows is not None else {}
        self.dpi_ok = dpi_ok
        self.pid = pid
        self.dpi = dpi
        self.sent = []
        self.restored = []
        self.fore = []
        self.LoadImageW = FakeFunc(self._load)

    def _load(self, *_a, **_k):
        return 777

    def GetSystemMetrics(self, idx):
        return self.metrics.get(idx, 0)

    def SystemParametersInfoW(self, *_a):
        return False

    def GetDesktopWindow(self):
        return 1

    def GetDpiForWindow(self, _h):
        return self.dpi

    def GetDpiForSystem(self):
        return self.dpi

    def SetProcessDpiAwarenessContext(self, _ctx):
        return self.dpi_ok

    def SetProcessDPIAware(self):
        return True

    def IsWindowVisible(self, _h):
        return True

    def GetWindowThreadProcessId(self, _h, pidptr):
        pidptr.contents.value = self.pid

    def GetWindowTextLengthW(self, h):
        return len(self.windows.get(int(h), ""))

    def GetWindowTextW(self, h, buf, _n):
        buf.value = self.windows.get(int(h), "")

    def EnumWindows(self, cb, _l):
        for h in self.windows:
            cb(h, None)
        return True

    def ShowWindow(self, h, cmd):
        self.restored.append((h, cmd))
        return True

    def SetForegroundWindow(self, h):
        self.fore.append(h)
        return True

    def SendMessageW(self, h, msg, wparam, lparam):
        self.sent.append((h, msg, wparam, lparam))
        return 0


class FakeShell32:
    def __init__(self, ok=True):
        self.ok = ok
        self.ids = []

    def SetCurrentProcessExplicitAppUserModelID(self, aid):
        self.ids.append(aid)
        if not self.ok:
            raise OSError("没有这个 API")
        return 0


class FakeKernel32:
    def __init__(self, err=0):
        self.err = err
        self.names = []
        self.CreateMutexW = FakeFunc(self._create)

    def _create(self, _s, _i, name):
        self.names.append(name)
        return 0 if self.err == 183 else 555

    def GetLastError(self):
        return self.err


def fake_windll(user32=None, shell32=None, kernel32=None):
    class W:
        pass
    w = W()
    w.user32 = user32 or FakeUser32()
    w.shell32 = shell32 or FakeShell32()
    w.kernel32 = kernel32 or FakeKernel32()
    return w


# ═════════════════ 状态文件的位置 ═════════════════
def test_state_path_is_not_in_the_repo_or_backend_data(state_file):
    p = cd.state_path()
    assert p == os.path.abspath(str(state_file)), p
    low = p.lower()
    assert not low.startswith(r"d:\ambrace"), "状态文件不许落进仓库"
    assert "backend" not in low and "server_config" not in low, "不许碰后端配置"


def test_state_path_default_lives_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("AMBRACE_CONSOLE_STATE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    p = cd.state_path()
    assert p == str(tmp_path / "AMBRACE" / "console_web.json"), p


def test_load_state_survives_garbage(state_file):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{不是 json", encoding="utf-8")
    assert cd.load_state() == {}
    state_file.write_text("[1,2]", encoding="utf-8")
    assert cd.load_state() == {}, "顶层不是 dict 也要当空，别把列表带进下游"
    state_file.unlink()
    assert cd.load_state() == {}


def test_save_state_merges_and_is_atomic(state_file):
    assert cd.save_state(hwnd=111, pid=1) is True
    assert cd.save_state(geometry={"x": 5}) is True
    d = json.loads(state_file.read_text(encoding="utf-8"))
    assert d["hwnd"] == 111 and d["pid"] == 1 and d["geometry"] == {"x": 5}, d
    assert d["saved_at"], "要留时间戳，否则看不出是不是陈的"
    assert not (state_file.parent / "console_web.json.tmp").exists()


# ═════════════════ 几何夹取 ═════════════════
def test_clamp_geometry_rules():
    wa = (0, 0, 2560, 1600)
    assert cd.clamp_geometry(-9000, -9000, 1360, 880, wa) == {"x": 0, "y": 0, "width": 1360,
                                                              "height": 880}
    small = cd.clamp_geometry(100, 100, 300, 200, wa)
    assert (small["width"], small["height"]) == (cd.MIN_W, cd.MIN_H), small
    big = cd.clamp_geometry(100, 100, 99999, 99999, wa)
    assert (big["width"], big["height"]) == (2560, 1600), big
    # 跑到屏幕外面的记录要整窗拉回来：右/下边不许越过工作区边界
    off = cd.clamp_geometry(99999, 99999, 1360, 880, wa)
    assert (off["x"], off["y"]) == (2560 - 1360, 1600 - 880), off
    full = cd.clamp_geometry(260, 210, 1706, 1066, (0, 0, 1706, 1066))
    assert full == {"x": 0, "y": 0, "width": 1706, "height": 1066}, \
        "满屏尺寸还想留 260 的偏移，系统会自己把窗口推到 -76（实跑踩过）"
    mid = cd.clamp_geometry(None, None, None, None, (0, 0, 2560, 1600))
    assert mid == {"x": 600, "y": 360, "width": 1360, "height": 880}, mid


def test_stored_geometry_uses_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps({"geometry": {"x": 200, "y": 120,
                                                              "width": 1400, "height": 900}}),
                                     encoding="utf-8")
    g = cd.stored_geometry(work_area=(0, 0, 2560, 1600))
    assert g == {"x": 200, "y": 120, "width": 1400, "height": 900}, g


# ═════════════════ DPI 与身份 ═════════════════
def test_ensure_dpi_aware_falls_back(monkeypatch):
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(FakeUser32(dpi_ok=False)))
    assert cd.ensure_dpi_aware() is True      # 主 API 不行也要走 SetProcessDPIAware
    class Boom:
        def __getattr__(self, _n):
            raise OSError("没有这个 API")
    monkeypatch.setattr(cd.ctypes, "windll", Boom())
    assert cd.ensure_dpi_aware() is False, "没有 API 也不能抛到外面"


def test_work_area_is_physical_after_dpi_claim(monkeypatch):
    u = FakeUser32(metrics={76: 0, 77: 0, 78: 2560, 79: 1600})
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(u))
    assert cd.virtual_work_area() == (0, 0, 2560, 1600)


def test_logical_work_area_divides_by_dpi(monkeypatch):
    """窗口坐标是逻辑像素（pywebview 自己就除过倍数），夹取必须同单位。"""
    m = {76: 0, 77: 0, 78: 2560, 79: 1600}
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(FakeUser32(metrics=m, dpi=144)))
    assert cd.dpi_scale() == 1.5, cd.dpi_scale()
    assert cd.logical_work_area() == (0, 0, 1706, 1066), cd.logical_work_area()
    # 150% 屏上不换算的话，物理坐标 2400 会被当成还在屏内，窗口就飞出去了
    assert cd.clamp_geometry(2400, 100, 1360, 880, cd.logical_work_area())["x"] == 1706 - 1360


def test_dpi_scale_survives_missing_api(monkeypatch):
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(FakeUser32(dpi=0)))
    assert cd.dpi_scale() == 1.0, "老系统/拿不到就按 1 算"

    class Boom:
        def __getattr__(self, _n):
            raise OSError("没有这个 API")

    monkeypatch.setattr(cd.ctypes, "windll", Boom())
    assert cd.dpi_scale() == 1.0


def test_stored_geometry_falls_back_when_screen_unmeasurable(tmp_path, monkeypatch):
    """量不到屏幕也不能抛——那会把窗口启动一起带走，宁可退回默认尺寸。"""
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps({"geometry": {"x": 8000, "y": 8000,
                                                              "width": 4000, "height": 3000}}),
                                     encoding="utf-8")

    class Boom:
        def __getattr__(self, _n):
            raise OSError("没有这个 API")

    monkeypatch.setattr(cd.ctypes, "windll", Boom())
    g = cd.stored_geometry()
    assert g == {"x": 0, "y": 0, "width": cd.DEFAULT_W, "height": cd.DEFAULT_H}, g


def test_app_id_is_set_before_any_window(monkeypatch):
    sh = FakeShell32()
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(shell32=sh))
    assert cd.set_app_id() is True
    assert sh.ids == [cd.APP_ID], sh.ids
    bad = FakeShell32(ok=False)
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(shell32=bad))
    assert cd.set_app_id() is False, "老 Windows 没这个 API 也不能崩"


def test_set_window_icon_sends_three_messages(monkeypatch, tmp_path):
    import ctypes
    ico = tmp_path / "a.ico"
    ico.write_bytes(b"\x00\x01")
    u = FakeUser32()
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(u))
    assert cd.set_window_icon(1234, str(ico)) is True
    assert [s[1] for s in u.sent] == [0x0080, 0x0080, 0x0080], u.sent
    assert sorted(s[2] for s in u.sent) == [0, 1, 2], "小/大/任务栏三种都得发"
    assert u.LoadImageW.restype == ctypes.c_void_p, \
        "不声明返回类型，64 位句柄会被截成假句柄，图标就等于没设（实跑踩过）"
    assert all(isinstance(s[3], ctypes.c_void_p) and s[3].value == 777 for s in u.sent), u.sent
    assert cd.set_window_icon(0, str(ico)) is False
    assert cd.set_window_icon(1234, str(tmp_path / "不存在.ico")) is False


# ═════════════════ 单实例 ═════════════════
def test_already_running_first_then_second(monkeypatch):
    cd._KEEP_MUTEX.clear()
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(kernel32=FakeKernel32(err=0)))
    assert cd.already_running() is False, "第一个实例不该被判成重复"
    assert cd._KEEP_MUTEX, "互斥体句柄必须留着，进程一退出第二个实例就能挤进来"
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(kernel32=FakeKernel32(err=183)))
    assert cd.already_running() is True


def test_find_window_needs_same_pid_and_exact_title(monkeypatch):
    monkeypatch.setattr(cd.ctypes, "windll",
                        fake_windll(FakeUser32(windows={999: cd.TITLE}, pid=4242)))
    assert cd.find_window(pid=4242) == 999
    assert cd.find_window(pid=1) == 0, "别人进程里同名的窗口不算"
    monkeypatch.setattr(cd.ctypes, "windll",
                        fake_windll(FakeUser32(windows={999: cd.TITLE + " - 副本"}, pid=4242)))
    assert cd.find_window(pid=4242) == 0, "标题差一点也不能认错窗口"


def test_handoff_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(tmp_path / "s.json"))
    assert cd.handoff_or_stay({"hwnd": 0}) == "primary", "没记录就直接当主实例"
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(FakeUser32(windows={})))
    assert cd.handoff_or_stay({"hwnd": 999, "pid": 4242}) == "primary", "叫不到活窗口就别退场"
    u2 = FakeUser32(windows={999: cd.TITLE}, pid=4242)
    monkeypatch.setattr(cd.ctypes, "windll", fake_windll(u2))
    assert cd.handoff_or_stay({"hwnd": 999, "pid": 4242}) == "handoff"
    assert u2.fore == [999] and u2.restored == [(999, 9)], "先还原再前台"


def test_forget_hwnd_clears_the_handle(state_file):
    cd.save_state(hwnd=555, pid=7)
    cd.forget_hwnd()
    d = json.loads(state_file.read_text(encoding="utf-8"))
    assert d["hwnd"] == 0 and d["pid"] == 0, "退出后别留一个死句柄给下一个实例"


# ═════════════════ 记几何 ═════════════════
class FakeWin:
    def __init__(self, x=100, y=100, width=1360, height=880):
        self.x, self.y, self.width, self.height = x, y, width, height


def test_current_geometry_refuses_garbage():
    assert cd.current_geometry(FakeWin()) == {"x": 100, "y": 100, "width": 1360, "height": 880}
    assert cd.current_geometry(FakeWin(width=300)) is None, "小于最小值的不存"
    assert cd.current_geometry(FakeWin(height=100)) is None
    assert cd.current_geometry(FakeWin(width=1360, height=880, x=None)) is None
    assert cd.current_geometry(object()) is None, "取不到就当没取到，别写 0 进文件"


def test_remember_debounce_and_force(state_file):
    w = FakeWin()
    assert cd.remember(w, force=True) is True
    assert json.loads(state_file.read_text(encoding="utf-8"))["geometry"]["width"] == 1360
    assert cd.remember(w) is False, "拖动时每 2 秒最多写一次"
    cd._last_remember[0] -= 5
    assert cd.remember(w) is True


def test_remember_refuses_bad_geometry(state_file):
    """坏几何既不能写盘，也不能把攒着的好底座带脏。"""
    assert cd.remember(FakeWin(width=1)) is False
    assert not state_file.exists(), state_file
    cd.seed_geometry({"x": 10, "y": 20, "width": 1360, "height": 880})
    assert cd.remember(object(), force=True, patch={"width": 1}) is True
    g = json.loads(state_file.read_text(encoding="utf-8"))["geometry"]
    assert g["width"] == 1360, "拖拽中途的小尺寸丢掉，别覆盖好值"
    assert cd.remember(object(), force=True, patch={"x": "不是数"}) is True
    assert json.loads(state_file.read_text(encoding="utf-8"))["geometry"] == g, "垃圾值同理"
    assert cd.remember(object(), force=True, patch={"x": 210}) is True
    assert json.loads(state_file.read_text(encoding="utf-8"))["geometry"] == \
        {"x": 210, "y": 20, "width": 1360, "height": 880}


def test_events_merge_into_one_geometry(state_file):
    """moved 只给 x/y、resized 只给宽高：底座＋两次事件才拼出一条完整几何。"""
    cd.seed_geometry({"x": 100, "y": 100, "width": 1360, "height": 880})
    assert cd.remember(object(), patch={"width": 1500, "height": 900}) is True
    assert cd.remember(object(), force=True, patch={"x": 40, "y": 60}) is True
    g = json.loads(state_file.read_text(encoding="utf-8"))["geometry"]
    assert g == {"x": 40, "y": 60, "width": 1500, "height": 900}, g
    # 没给底座、又问不到窗口时，半条几何宁可不写
    cd._geom.clear()
    cd._last_remember[0] -= 5
    assert cd.remember(object(), force=True, patch={"x": 7}) is False


# ═════════════════ 最小化哨兵坐标（2026-11-06）═════════════════

def test_最小化哨兵坐标不会写进状态文件(state_file):
    """窗口还没摆好时 moved 事件会报 -21333 一类的假坐标（Windows 最小化哨兵）。

    把它收进状态文件＝「记住窗口」被毒掉：下次开窗总被顶到左上角，
    而不是用户上次放的地方（实机踩过：存的是 -21333，开窗跑到 0,0）。
    """
    cd.seed_geometry({"x": 168, "y": 92, "width": 1360, "height": 880})
    assert cd.remember(object(), force=True, patch={"x": -21333, "y": -21333}) is True
    g = json.loads(state_file.read_text(encoding="utf-8"))["geometry"]
    assert g["x"] == 168 and g["y"] == 92, g


def test_问窗口时遇到哨兵坐标就不采信():
    w = FakeWin(x=-21333, y=-21333)
    assert cd.current_geometry(w) is None, "哨兵坐标不是位置，别写进文件"
    assert cd.current_geometry(FakeWin(x=-9000, y=10)) is not None, \
        "多显示器上真在主屏左边的负坐标不是哨兵，别误伤"


def test_存着的哨兵坐标会居中打开(tmp_path, monkeypatch):
    """旧版本已经被哨兵坐标污染过的状态文件：当作「没记住位置」，居中开。"""
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps({"geometry": {"x": -21333, "y": -21333,
                                                              "width": 1360, "height": 880}}),
                                     encoding="utf-8")
    g = cd.stored_geometry(work_area=(0, 0, 2560, 1600))
    assert (g["x"], g["y"]) == (600, 360), g
