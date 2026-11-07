"""W5-lite 的接线闸门：run() 必须真的把单实例/身份/记窗口串起来。

只假造 webview 与几个要碰 Win32 的函数，状态文件走临时目录——
写盘、去抖、退出抹句柄这三件事都让真代码跑，漏接一根线这条就红。
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

import console_app
import console_desktop as cd


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
        self.x, self.y, self.width, self.height = 300, 200, 1500, 900


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    """假 webview + 假 Win32 探针，返回 (日志, 状态文件路径)。"""
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
                      ("find_window", 424242), ("set_window_icon", True)):
        monkeypatch.setattr(cd, name, spy(name, ret))
    # 屏幕尺寸钉死：换台小屏机器跑，期望值不该跟着变
    monkeypatch.setattr(cd, "logical_work_area", lambda: (0, 0, 2560, 1600))

    fake = types.ModuleType("webview")

    def create_window(title, **kw):
        log.append(("create", title, kw))
        win = RecWin()
        log.append(("created", win))
        return win

    fake.create_window = create_window
    fake.start = lambda **kw: log.append(("start", kw))
    monkeypatch.setitem(sys.modules, "webview", fake)
    return log, tmp_path / "s.json"


def win_of(log):
    for row in log:
        if row[0] == "created":
            return row[1]
    raise AssertionError("没建窗口")


def test_run_wires_the_three_things(rig):
    log, _state = rig
    console_app.run("dark", check=False)
    names = [r[0] for r in log]
    # DPI 先声明（否则后面量到的屏幕是假的），身份在开窗前设好
    assert names.index("ensure_dpi_aware") < names.index("create") < names.index("start")
    assert names.index("set_app_id") < names.index("create")
    create = [r for r in log if r[0] == "create"][0]
    kw = create[2]
    assert create[1] == cd.TITLE, "标题就是 find_window 认人的凭据，不许改"
    assert (kw["width"], kw["height"]) == (cd.DEFAULT_W, cd.DEFAULT_H)
    assert (kw["x"], kw["y"]) == (600, 360), "没存过几何就居中开（2560×1600 屏）"
    assert kw["min_size"] == (cd.MIN_W, cd.MIN_H)
    assert kw["url"].startswith("file:///") and "#theme=dark" in kw["url"]
    w = win_of(log)
    assert {"shown", "moved", "resized", "closing"} <= set(vars(w.events))
    assert len(w.events.shown.items) == 1, "窗口亮起来要设图标、记句柄"
    assert len(w.events.closing.items) == 1, "关窗要落几何并抹掉句柄"
    assert len(w.events.moved.items) == 1 and len(w.events.resized.items) == 1


def test_shown_records_handle_and_real_icon(rig):
    log, state = rig
    console_app.run("dark", check=False)
    win_of(log).events.shown.fire()
    assert ("find_window",) in log
    icon = os.path.join(os.path.dirname(os.path.abspath(console_app.__file__)), "ambrace.ico")
    assert ("set_window_icon", 424242, icon) in log
    assert os.path.exists(icon), "图标文件没了＝任务栏又是 pythonw"
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["hwnd"] == 424242 and d["pid"] == os.getpid(), d


def test_events_remember_and_close_writes_once_more(rig):
    log, state = rig
    console_app.run("dark", check=False)
    w = win_of(log)
    w.events.resized.fire(1500, 900)          # 第一次事件：写得下
    g = json.loads(state.read_text(encoding="utf-8"))["geometry"]
    assert (g["width"], g["height"]) == (1500, 900), g
    assert g["height"] >= cd.MIN_H and g["width"] >= cd.MIN_W
    w.events.moved.fire(300, 200)             # 2 秒内：只并进底座，不写盘
    assert json.loads(state.read_text(encoding="utf-8"))["geometry"] == g, "去抖没生效就会狂写盘"
    w.events.closing.fire()                   # 关窗那一下必须写全
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["geometry"] == {"x": 300, "y": 200, "width": 1500, "height": 900}, d
    assert d["hwnd"] == 0 and d["pid"] == 0, "退出后别留死句柄给下一个实例"


def test_stored_geometry_is_used_at_startup(rig):
    """上次关窗的位置必须真的开在那里——否则「记住窗口」只是写在文件里好看。"""
    log, state = rig
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"geometry": {"x": 80, "y": 40, "width": 1400, "height": 900}}),
                     encoding="utf-8")
    console_app.run("dark", check=False)
    kw = [r for r in log if r[0] == "create"][0][2]
    assert (kw["x"], kw["y"], kw["width"], kw["height"]) == (80, 40, 1400, 900), kw


def test_offscreen_record_is_pulled_back_on_screen(rig):
    """拔过显示器留下的坐标不能把窗口开没：夹回工作区，标题栏必须在屏上。"""
    log, state = rig
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"geometry": {"x": 5000, "y": -300, "width": 1360,
                                              "height": 880}}), encoding="utf-8")
    console_app.run("dark", check=False)
    kw = [r for r in log if r[0] == "create"][0][2]
    assert (kw["x"], kw["y"]) == (2560 - 1360, 0), kw
    assert kw["x"] + kw["width"] <= 2560 and kw["y"] + kw["height"] <= 1600, kw


def test_second_instance_hands_off_and_never_opens_a_window(rig, monkeypatch):
    log, _state = rig
    monkeypatch.setattr(cd, "already_running", lambda: (log.append(("already",)), True)[1])
    monkeypatch.setattr(cd, "handoff_or_stay",
                        lambda: (log.append(("handoff",)), "handoff")[1])
    assert console_app.run("dark", check=False) == 0
    names = [r[0] for r in log]
    assert "already" in names and "handoff" in names
    assert "create" not in names and "start" not in names, "第二个实例只叫人，不开新窗口"


def test_stale_handoff_record_still_starts_the_app(rig, monkeypatch):
    """互斥体说有人在跑、但那个窗口叫不动（状态是陈的）→ 自己顶上，别拒绝启动。"""
    log, _state = rig
    monkeypatch.setattr(cd, "already_running", lambda: (log.append(("already",)), True)[1])
    monkeypatch.setattr(cd, "handoff_or_stay",
                        lambda: (log.append(("handoff",)), "primary")[1])
    console_app.run("dark", check=False)
    names = [r[0] for r in log]
    assert "create" in names and "start" in names
