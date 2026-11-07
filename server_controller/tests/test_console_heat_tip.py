"""热力图悬浮明细的行为闸门（③：把 token 任务构成加回来）。

数据链本来就是通的：console_core._read_token_heatmap 每行带 tasks，
console_api.dashboard 原样返回；缺的只是前端把 <title> 换成能列明细的浮层。

断言的是**跑起来的行为**：格子挂上索引、悬停真出明细、明细按用量降序、
浮层不越出视口、任务名是转义后的文本而不是活标签、离开就收起。
每条都要能变红：改坏一处实现，下面对应测试必须失败。
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

import console_app as shell

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")
HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()

DASH = {
    "kpi": {"server": "运行中", "health": "正常", "vlm": "—", "vlmCap": "",
            "chars": "1", "mems": "2", "tokens": "3"},
    "heat": [
        {"date": "2026-08-19", "tokens": 3441217, "future": False,
         "tasks": {"主对话": 2000000, "智能体思考": 1000000, "记忆处理": 441217}},
        {"date": "2026-08-20", "tokens": 0, "future": False, "tasks": {}},
        {"date": "2026-08-21", "tokens": 900, "future": False,
         "tasks": {"<script>bad()</script>": 900}},
    ],
    "heatPeak": 3441217,
    "memSpark": [], "tokenSpark": [],
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
    d.id = 'heatProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, theme="dark", body="", dash=None):
    bundle = {"dashboard": {"ok": True, "data": dash or DASH}}
    html = shell.build_shot_html("dashboard", bundle, theme=theme)
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_heat_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=oheat",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="heatProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来（页面没渲染？）"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 格子挂索引，原生 title 已退场（不许双弹）─────────────

@has_chrome
def test_gate_heat_cells_carry_index_and_no_native_title(tmp_path):
    got = _run(tmp_path, "cells", body="""
      const svg = document.querySelector('.heat svg');
      out.cells = svg.querySelectorAll('rect[data-hm]').length;
      out.titles = svg.querySelectorAll('title').length;
      out.labels = Array.from(svg.querySelectorAll('rect[data-hm]'))
        .slice(0, 2).map(r => r.getAttribute('aria-label') || '');
    """)
    assert got.get("cells", 0) >= 2, "格子没挂上明细索引 %s" % got
    assert got.get("titles") == 0, "原生 <title> 还在，会和自绘浮层双弹 %s" % got
    assert all("2026-08" in s and "tokens" in s for s in got.get("labels", [])), \
        "aria-label 没保留日期与总量，读屏会丢信息 %s" % got


# ───────────── ② 悬停真出任务构成，且按用量降序 ─────────────

@has_chrome
def test_gate_hover_shows_task_breakdown_sorted(tmp_path):
    got = _run(tmp_path, "hover", body="""
      const c = document.querySelector('.heat svg rect[data-hm="0"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX:220, clientY:300}));
      const t = document.getElementById('hmtip');
      out.hidden = t.hidden;
      out.text = t.textContent;
      out.rows = Array.from(t.querySelectorAll('.hml > div')).map(d => d.textContent);
    """)
    assert got.get("hidden") is False, "悬停了浮层还是隐藏态 %s" % got
    text = got.get("text", "")
    assert "2026-08-19" in text and "3,441,217" in text, "日期或总量没回到浮层 %s" % got
    assert "主对话" in text and "智能体思考" in text and "记忆处理" in text, \
        "任务构成明细没加回来 %s" % got
    assert "2,000,000" in text and "441,217" in text, "任务用量没列出 %s" % got
    rows = got.get("rows", [])
    assert len(rows) >= 3 and rows[0].find("主对话") < rows[0].find("2,000,000"), \
        "明细行格式不对 %s" % rows
    order = [next((n for n in ("主对话", "智能体思考", "记忆处理") if n in r), "") for r in rows[:3]]
    assert order == ["主对话", "智能体思考", "记忆处理"], "明细没按用量降序 %s" % order


# ───────────── ③ 浮层钳在视口内，不越界 ─────────────

@has_chrome
def test_gate_tooltip_is_clamped_into_the_viewport(tmp_path):
    got = _run(tmp_path, "clamp", body="""
      const c = document.querySelector('.heat svg rect[data-hm="0"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true,
        clientX: window.innerWidth - 2, clientY: window.innerHeight - 2}));
      const t = document.getElementById('hmtip'), r = t.getBoundingClientRect();
      out.left = Math.round(r.left); out.top = Math.round(r.top);
      out.right = Math.round(r.right); out.bottom = Math.round(r.bottom);
      out.vw = window.innerWidth; out.vh = window.innerHeight;
    """)
    assert got.get("right", 99999) <= got.get("vw", 0), "浮层右边越出视口 %s" % got
    assert got.get("bottom", 99999) <= got.get("vh", 0), "浮层下边越出视口 %s" % got
    assert got.get("left", -1) >= 0 and got.get("top", -1) >= 0, "浮层贴到负坐标 %s" % got


# ───────────── ④ 没明细的日子给兜底，不留空白 ─────────────

@has_chrome
def test_gate_empty_tasks_has_a_fallback(tmp_path):
    got = _run(tmp_path, "empty", body="""
      const c = document.querySelector('.heat svg rect[data-hm="1"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX:200, clientY:300}));
      out.text = document.getElementById('hmtip').textContent;
    """)
    assert "无任务构成" in got.get("text", ""), "零用量格子没给兜底文案 %s" % got


# ───────────── ⑤ 任务名走转义，不许变成活标签 ─────────────

@has_chrome
def test_gate_task_labels_are_escaped(tmp_path):
    got = _run(tmp_path, "escape", body="""
      const before = document.querySelectorAll('script').length;
      const c = document.querySelector('.heat svg rect[data-hm="2"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX:200, clientY:300}));
      const t = document.getElementById('hmtip');
      out.scriptsBefore = before;
      out.scriptsAfter = document.querySelectorAll('script').length;
      out.injected = t.querySelectorAll('script').length;
      out.text = t.textContent;
    """)
    assert got.get("scriptsAfter") == got.get("scriptsBefore"), \
        "任务名被当成 HTML 执行了 %s" % got
    assert got.get("injected") == 0, "浮层里混进了 script 标签 %s" % got
    # --dump-dom 会把 textContent 里的 < 序列化成 &lt;，这里读的是 DOM 源码而非解码值
    assert "&lt;script&gt;" in got.get("text", ""), "转义后应显示为纯文本 %s" % got


# ───────────── ⑥ 离开就收起，不常驻挡内容 ─────────────

@has_chrome
def test_gate_tip_is_hidden_until_hover_and_hides_on_leave(tmp_path):
    got = _run(tmp_path, "leave", body="""
      const t = document.getElementById('hmtip');
      out.before = t.hidden;
      const c = document.querySelector('.heat svg rect[data-hm="0"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX:200, clientY:300}));
      out.shown = !t.hidden;
      c.dispatchEvent(new MouseEvent('mouseout', {bubbles:true, relatedTarget: document.body,
        clientX:200, clientY:300}));
      out.after = t.hidden;
    """)
    assert got.get("before") is True, "浮层初始就该是隐藏态 %s" % got
    assert got.get("shown") is True, "悬停没显示 %s" % got
    assert got.get("after") is True, "移开没收起 %s" % got


# ───────────── ⑦ 纸面：样式存在且不吃点击 ─────────────

def test_gate_tip_style_is_flat_and_never_takes_clicks():
    assert ".hmtip" in CSS and "pointer-events: none" in CSS.split(".hmtip {", 1)[-1][:400], \
        "浮层没定义或会吃掉鼠标事件"
    assert "position: fixed" in CSS.split(".hmtip {", 1)[-1][:400], "浮层没跟着视口定位"
    assert 'id="hmtip"' in HTML and "hidden" in HTML, "页面里没有浮层容器"
    assert "HEAT_ROWS" in HTML and "tasks" in HTML, "明细数据没接上"
