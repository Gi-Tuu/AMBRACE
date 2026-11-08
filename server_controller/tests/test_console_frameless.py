"""② 隐藏系统标题栏、自绘窗口按钮与细拖动条的行为闸门。

用户拍板：frameless，自绘最小化/最大化/关闭放页头右上，另留一条细拖动条，
接受阴影与贴边吸附退化。

断言的是跑起来的行为：开窗真的 frameless、三个动作真的进了 js_api 白名单、
按钮真渲染出来且带无障碍标签、拖动条带 pywebview 的 drag-region 标记且不与按钮抢点击、
点击真派发到窗口动作。每条都要能变红。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import types

import pytest

import console_app as shell

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")
HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
APP = open(os.path.join(HERE, "console_app.py"), encoding="utf-8").read()
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()

DASH = {
    "kpi": {"server": "运行中", "health": "正常", "vlm": "—", "vlmCap": "",
            "chars": "1", "mems": "2", "tokens": "3"},
    "heat": [{"date": "2026-11-07", "tokens": 100, "future": False, "tasks": {}}],
    "heatPeak": 100, "memSpark": [], "tokenSpark": [],
    "meta": {"at": "00:00:00", "next": "", "target": "—", "version": "—"},
}


def _probe(body):
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try { %s }
    catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'winProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, body):
    bundle = {"dashboard": {"ok": True, "data": DASH}}
    html = shell.build_shot_html("dashboard", bundle, theme="dark")
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_win_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocwin",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="winProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 开窗真的 frameless，窗口动作真的暴露给 JS ─────────────

def test_gate_window_is_frameless_and_actions_are_exposed():
    assert re.search(r"frameless\s*=\s*True", APP), "create_window 没开 frameless"
    a = shell.Api()
    names = set(a.expose())
    assert {"win_minimize", "win_maximize", "win_close"} <= names, \
        "三个窗口动作没进 js_api 白名单 %s" % sorted(names)
    assert "export_log_dialog" in names, "改 expose() 时把原有对话框弄丢了"


# ───────────── ② 三个动作真能驱动窗口（不靠猜方法名）─────────────

def test_gate_window_actions_call_the_real_window_methods(monkeypatch):
    import webview

    class Win:
        def __init__(self):
            self.calls = []

        def minimize(self):
            self.calls.append("minimize")

        def maximize(self):
            self.calls.append("maximize")

        def restore(self):
            self.calls.append("restore")

        def destroy(self):
            self.calls.append("destroy")

    w = Win()
    monkeypatch.setattr(webview, "windows", [w], raising=False)
    a = shell.Api()
    for name in ("win_minimize", "win_maximize", "win_close"):
        got = a.call(name)
        assert got["ok"] is True, "%s 失败：%s" % (name, got)
    assert w.calls == ["minimize", "maximize", "destroy"], "窗口方法调错了 %s" % w.calls
    got = a.call("win_maximize")
    assert got["ok"] is True and w.calls[-1] == "restore", "再点一次没还原 %s" % w.calls


# ───────────── ③ 细拖动条与三个按钮真渲染出来 ─────────────

@has_chrome
def test_gate_dragstrip_and_buttons_render(tmp_path):
    got = _run(tmp_path, "parts", """
      const bar = document.querySelector('.hero .dragbar');
      out.drag = !!bar && getComputedStyle(bar).cursor === 'move';
      out.dragHeight = bar ? Math.round(bar.getBoundingClientRect().height) : 0;
      out.btns = Array.from(document.querySelectorAll('.hero .winbtns button')).map(b => ({
        act: b.dataset.act, label: b.getAttribute('aria-label') || '',
        w: Math.round(b.getBoundingClientRect().width),
        h: Math.round(b.getBoundingClientRect().height) }));
      out.insideDrag = !!document.querySelector('.dragbar button');
    """)
    assert got.get("drag") is True, "细拖动条没了或者指针不是 move %s" % got
    assert 0 < got.get("dragHeight", 99) <= 12, "拖动条不是「细」条 %s" % got
    assert all(b["w"] >= 40 and b["h"] >= 24 for b in got.get("btns", [])), \
        "窗口按钮还是太小（返工要求放大） %s" % got.get("btns")
    assert got.get("insideDrag") is False, "按钮在拖动条里面会抢点击 %s" % got
    acts = [b["act"] for b in got.get("btns", [])]
    assert acts == ["win-min", "win-max", "win-close"], "按钮顺序或动作不对 %s" % acts
    assert all(b["label"] for b in got.get("btns", [])), "按钮缺无障碍标签 %s" % got


# ───────────── ④ 按钮点击真的派发到窗口动作 ─────────────

@has_chrome
def test_gate_button_click_dispatches_the_window_action(tmp_path):
    got = _run(tmp_path, "click", """
      const seen = [];
      const orig = window.pywebview;
      window.pywebview = {api: {call: function(n){ seen.push(n); return {ok:true,data:{}}; }}};
      window.__DATA__ = undefined;
      document.querySelector('[data-act="win-min"]').click();
      await new Promise(r => setTimeout(r, 60));
      window.pywebview = orig;
      out.seen = seen;
    """)
    assert "win_minimize" in got.get("seen", []), \
        "点最小化没派发到 win_minimize %s" % got


# ───────────── ⑤ 纸面：按钮样式、act 分支、退化说明都在 ─────────────

def test_gate_styles_and_dispatch_exist_on_paper():
    assert ".hero .dragbar" in CSS and ".hero .winbtns" in CSS, "自绘窗口控件没样式"
    # 拖动改由自己做：补了 WS_THICKFRAME 之后 WebView2 的原生 app-region 拖动失效，
    # 所以拖动条不再挂 pywebview 的标记，而是接到壳层的 drag_by
    assert 'class="dragbar"' in HTML, "拖动条不在"
    assert "pywebview-drag-region" not in HTML, "还在靠 pywebview 的原生拖动（补边框后失效）"
    assert "bindDragBar" in HTML and '"drag_by"' in HTML, "拖动没接到壳层"
    assert "def drag_by" in APP, "壳层没有 drag_by"
    for act in ('"win-min"', '"win-max"', '"win-close"'):
        assert act in HTML, "按钮 %s 没渲染" % act
    assert "win-min" in HTML and "win_minimize" in APP, "act() 没把按钮接到窗口动作"
