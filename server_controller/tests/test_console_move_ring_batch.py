"""第三批：窗口拖动改自己算 + 设置星球 + 仪表盘单个大环。

背景：补回 WS_THICKFRAME 之后，WebView2 的 app-region 原生拖动失效（实测拖不动）。
改成页头拖动条 → JS 增量 → 壳层 `drag_by` → 读真实窗口矩形再 SetWindowPos。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import types

import pytest

import console_app as shell
import console_desktop as cd

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")
HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
APP = open(shell.__file__, encoding="utf-8").read()


def _run(tmp_path, name, page, body, bundle):
    html = shell.build_shot_html(page, bundle, theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='visProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},400);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_v3_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,900", "--temp-profile=ovt",
                            "--virtual-time-budget=5000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="visProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


class FakeU32:
    """只认拖动要用的两个 API。"""

    def __init__(self, left=300, top=200):
        self.left, self.top = left, top
        self.calls = []

    @staticmethod
    def _h(x):
        return int(getattr(x, "value", x) or 0)

    def GetWindowRect(self, hwnd, ptr):
        self.calls.append(("rect", self._h(hwnd)))
        try:
            ptr._obj.left, ptr._obj.top = self.left, self.top
        except AttributeError:
            pass
        return True

    def SetWindowPos(self, hwnd, after, x, y, cx, cy, flags):
        self.calls.append(("pos", self._h(hwnd), x, y, flags))
        return True


def _wire(monkeypatch, u32):
    monkeypatch.setattr(cd.ctypes, "windll", types.SimpleNamespace(user32=u32))


# ───── ① 拖动：读真实矩形 + 按 DPI 换算 + 不动尺寸 ─────
def test_gate_drag_reads_the_live_rect_and_keeps_the_size(monkeypatch):
    u = FakeU32(left=300, top=200)
    _wire(monkeypatch, u)
    monkeypatch.setattr(cd, "find_window", lambda *a, **k: 4242)
    monkeypatch.setattr(cd, "dpi_scale", lambda: 2.0)
    assert cd.drag_window(10, 5) is True
    pos = [c for c in u.calls if c[0] == "pos"]
    assert pos, "没有挪窗口：%s" % u.calls
    assert pos[0][1] == 4242, "挪的不是这个窗口：%s" % (pos[0],)
    assert (pos[0][2], pos[0][3]) == (320, 210), "坐标没按 DPI 换算或没从真实矩形起算：%s" % (pos[0],)
    assert pos[0][4] & cd.SWP_NOSIZE, "拖动顺手改了尺寸"


def test_gate_drag_is_a_noop_without_movement_or_window(monkeypatch):
    u = FakeU32()
    _wire(monkeypatch, u)
    monkeypatch.setattr(cd, "find_window", lambda *a, **k: 0)
    assert cd.drag_window(10, 0) is False, "没有窗口也要返回 False"
    monkeypatch.setattr(cd, "find_window", lambda *a, **k: 7)
    assert cd.drag_window(0, 0) is False, "零位移不必动窗口"
    assert not [c for c in u.calls if c[0] == "pos"], "不该发 SetWindowPos"


def test_gate_drag_never_raises(monkeypatch):
    class Boom:
        def __getattr__(self, _n):
            raise OSError("没有这个 API")

    monkeypatch.setattr(cd.ctypes, "windll", types.SimpleNamespace(user32=Boom()))
    monkeypatch.setattr(cd, "find_window", lambda *a, **k: 9)
    assert cd.drag_window(3, 3) is False


def test_gate_shell_exposes_drag_by_and_turns_easy_drag_off():
    assert "def drag_by" in APP, "壳层没有 drag_by"
    assert 'if name == "drag_by":' in APP, "Api.call 没把 drag_by 路由过去"
    assert '"drag_by"' in APP, "drag_by 没进 expose 白名单"
    # easy_drag 走 window.move()，这条路在补边框后不可用，必须关掉
    assert re.search(r"easy_drag=False", APP), "easy_drag 没关（会和我们的拖动打架）"


def test_gate_webview_native_drag_marker_is_gone():
    assert 'class="dragbar"' in HTML and "pywebview-drag-region" not in HTML, \
        "还在指望 WebView2 的原生拖动"
    assert "bindDragBar" in HTML, "页头拖动条没绑事件"
    for ev in ("pointerdown", "pointermove"):
        assert ev in HTML, "拖动少了 %s" % ev
    assert '"drag_by"' in HTML, "前端没往壳层发 drag_by"


@has_chrome
def test_gate_dragstrip_gets_the_pointer(tmp_path):
    """拖动条要真能收到 pointerdown（不然 JS 再对也没用）。"""
    got = _run(tmp_path, "bar", "dashboard", """
      const bar = document.querySelector('.hero .dragbar');
      const r = bar.getBoundingClientRect();
      out.h = Math.round(r.height);
      out.cursor = getComputedStyle(bar).cursor;
      out.top = Math.round(r.top);
      const hit = document.elementFromPoint(Math.round(r.left + 300), Math.round(r.top + r.height / 2));
      out.hit = !!hit && (hit.classList.contains('dragbar') || hit.closest('.dragbar') !== null);
    """, {"dashboard": {"ok": True, "data": {"kpi": {}, "heat": [], "memStats": {}}}})
    assert got.get("cursor") == "move", "指针不是移动样式：%s" % got
    assert got.get("hit"), "拖动条被别的东西盖住了：%s" % got
    assert 4 <= got.get("h", 0) <= 12, "拖动条厚度不合理：%s" % got


def test_gate_splash_never_swallows_the_drag():
    """开屏是纯装饰。它要是拦指针，窗口刚打开那三秒页头就点不到，
    用户想先挪窗口时会以为"窗口不能动了"（实测过这一条）。"""
    assert re.search(r"#splash \{[^}]*pointer-events:\s*none", CSS, re.S), \
        "开屏层会挡住页头拖动条"


# ───── ② 设置：大星球只露一弧，且端口那行已移除 ─────
def test_gate_settings_planet_is_a_wide_cap():
    blk = re.search(r"\.set-orb \{([^}]*)\}", CSS, re.S)
    assert blk, "没有设置星球"
    body = blk.group(1)
    w = re.search(r"width:\s*(\d+)px", body)
    b = re.search(r"bottom:\s*(-?\d+)px", body)
    assert w and int(w.group(1)) >= 180, "星球太小（用户要的是一大个弧）：%s" % body[:100]
    assert b and int(b.group(1)) <= -100, "星球没沉到窗口下面（露出来的弧太多）：%s" % body[:100]
    assert re.search(r"\.set-orb:hover[^{]*\{[^}]*scale\(1\.0\d\)", CSS, re.S), "指上去没有微放大"
    assert 'id="sideFoot"' not in HTML, "端口那行还在"


# ───── ③ 仪表盘：单个大环，跟着左列选择走 ─────
def _dash():
    return {"kpi": {"server": "运行中", "serverCap": "PID 1 · 端口 8000", "health": "正常",
                    "chars": "14", "mems": "13,733", "tokens": "40,272,483"},
            "heatPeak": 100, "memSpark": [1, 3, 2, 5], "tokenSpark": [2, 5, 3, 8],
            "meta": {"target": "127.0.0.1:8000"},
            "memStats": {"kinds": [{"key": "event", "label": "近期事件", "count": 6},
                                   {"key": "insight", "label": "洞察", "count": 4}],
                         "roles": [{"id": 13, "name": "sam", "count": 4591,
                                    "kinds": [{"key": "insight", "label": "洞察", "count": 1549}]}]},
            "heat": [{"date": "2026-11-%02d" % (i + 1), "tokens": (i + 1) * 3, "future": False,
                      "tasks": {"主对话": (i + 1) * 2, "记忆处理": i + 1}} for i in range(12)]}


@has_chrome
def test_gate_ring_follows_the_selected_row(tmp_path):
    got = _run(tmp_path, "ring", "dashboard", """
      out.title0 = document.querySelector('.ring-t').textContent;
      out.rings = document.querySelectorAll('.ring').length;
      out.on = (document.querySelector('.kr.on') || {dataset:{}}).dataset.ring || '';
      out.inRing = !!document.querySelector('.ringmid svg');
      const mem = document.querySelector('.kr[data-ring="mem"]');
      mem.click();
      await new Promise(r => setTimeout(r, 180));
      out.title1 = document.querySelector('.ring-t').textContent;
      out.on1 = (document.querySelector('.kr.on') || {dataset:{}}).dataset.ring || '';
      out.leg = Array.from(document.querySelectorAll('.ringleg span')).map(e => e.textContent).slice(0, 2);
    """, {"dashboard": {"ok": True, "data": _dash()}})
    assert got.get("title0") == "Token 构成", "默认不是累计 Token 视图：%s" % got
    assert got.get("rings") == 1, "应当只有一个大环：%s" % got
    assert got.get("on") == "token", "默认没把累计 Token 那行标成选中：%s" % got
    assert got.get("inRing"), "环中间没有折线"
    assert got.get("title1") == "记忆构成", "点了记忆数没换环：%s" % got
    assert got.get("on1") == "mem", "选中态没跟着走：%s" % got
    assert any("近期事件" in x for x in got.get("leg") or []), "图例还是 Token 的：%s" % got.get("leg")


@has_chrome
def test_gate_ring_segment_tells_you_what_it_is(tmp_path):
    got = _run(tmp_path, "segtip", "dashboard", """
      const seg = document.querySelector('circle[data-seg]');
      out.has = !!seg;
      const r = seg.getBoundingClientRect();
      seg.dispatchEvent(new MouseEvent('mouseover', {bubbles: true,
        clientX: r.left + r.width / 2, clientY: r.top + 2}));
      await new Promise(res => setTimeout(res, 120));
      const tip = document.getElementById('hmtip');
      out.shown = !tip.hidden;
      out.text = tip.textContent;
    """, {"dashboard": {"ok": True, "data": _dash()}})
    assert got.get("has"), "环上没有可分段的元素"
    assert got.get("shown"), "指针停上去没浮出说明"
    t = got.get("text") or ""
    assert "主对话" in t or "记忆处理" in t, "浮层没说这是哪一类：%s" % t
    assert "%" in t, "浮层没给占比：%s" % t


def test_gate_left_column_uses_the_same_nebula():
    blk = re.search(r"\.kr\[data-ring\]:hover \{([^}]*)\}", CSS, re.S)
    assert blk, "左列没有悬停样式"
    assert "radial-gradient" in blk.group(1) and "row-nebula" in blk.group(1), \
        "左列没接上同款流动星云：%s" % blk.group(1)[:120]
    assert re.search(r"\.kr\.on \{[^}]*inset 2px 0 0", CSS, re.S), "选中的行没有左侧竖线"
