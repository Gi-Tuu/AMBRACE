"""⑤⑥ 日期与时间合并、时钟真走秒的行为闸门。

原来日期卡在侧栏（.db 的 dDay/dDate），时钟在页头（.clock 的 heroTime/heroNext），
两处分散且字体不同；更关键的是 heroTime 只在 paint() 时赋值，靠 3s/20s 刷新才跳。

断言的是跑起来的行为：四元素真在同一个组件里、格式对、
1 秒 tick 真把时间改回来、呼吸层静区覆盖新组件且在磁盘图上真的没有亮星。
每条都要能变红。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess

import pytest

from PIL import Image

import console_app as shell

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")
HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_starfield", os.path.join(WEB, "build_starfield.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

DASH = {
    "kpi": {"server": "运行中", "health": "正常", "vlm": "—", "vlmCap": "",
            "chars": "1", "mems": "2", "tokens": "3"},
    "heat": [{"date": "2026-11-07", "tokens": 100, "future": False, "tasks": {"主对话": 100}}],
    "heatPeak": 100, "memSpark": [], "tokenSpark": [],
    "meta": {"at": "00:00:00", "next": "", "target": "—", "version": "—"},
}


def _probe(body, wait=300):
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try { %s }
    catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'clockProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, %d);
});
</script>
</body>""" % (body, wait)


def _run(tmp_path, name, body, theme="dark", budget=4000):
    bundle = {"dashboard": {"ok": True, "data": DASH}}
    html = shell.build_shot_html("dashboard", bundle, theme=theme)
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_clock_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=oclock",
                            "--virtual-time-budget=%d" % budget,
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="clockProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 四个元素真在同一个组件里，侧栏日期卡退场 ─────────────

@has_chrome
def test_gate_date_and_time_live_in_one_clock(tmp_path):
    got = _run(tmp_path, "merge", """
      const c = document.querySelector('.side .clock');
      out.clock = !!c;
      out.ids = ['dDay','dDate','heroTime'].map(k => !!(c && c.querySelector('#'+k)));
      out.sameColumn = !!(c && c.querySelector('.ct #dDate') && c.querySelector('.ct #heroTime'));
      out.noHint = !document.getElementById('heroNext');
      out.sideDate = !!document.querySelector('.side .db');
      // 返工：时间要在左侧导航栏「上方」，不是页头右上
      const nav = document.getElementById('nav');
      out.inSide = !!(c && c.closest('.side'));
      out.beforeNav = !!(c && nav &&
        (c.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING));
      out.heroClock = !!document.querySelector('.hero .clock');
    """)
    assert got.get("clock") is True, "侧栏没有时钟组件 %s" % got
    assert got.get("ids") == [True, True, True], "日/月周/时间没合并进同一个组件 %s" % got
    assert got.get("sameColumn") is True, "月+周 与 时间 没在日的右边同一列上下两行 %s" % got
    assert got.get("noHint") is True, "被要求移除的状态字还在 %s" % got
    assert got.get("sideDate") is False, "侧栏还留着重复的日期卡 %s" % got
    assert got.get("inSide") is True and got.get("beforeNav") is True, \
        "时钟没放在左侧导航栏上方 %s" % got
    assert got.get("heroClock") is False, "页头右上还留着重复的时钟 %s" % got


# ───────────── ② 日期／星期／时间格式都对 ─────────────

@has_chrome
def test_gate_clock_text_formats(tmp_path):
    got = _run(tmp_path, "format", """
      out.day = document.getElementById('dDay').textContent;
      out.date = document.getElementById('dDate').textContent;
      out.time = document.getElementById('heroTime').textContent;
    """)
    assert re.fullmatch(r"\d{2}", got.get("day", "")), "大号日期不是两位 %s" % got
    assert "月" in got.get("date", "") and "周" in got.get("date", ""), \
        "月份／星期没显示 %s" % got
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", got.get("time", "")), \
        "时间不是 HH:MM:SS %s" % got
    assert "进页时拉一次" not in HTML, "被要求移除的那段字还留在页面里"


# ───────────── ③ 1 秒 tick 真把时间改回来（哨兵法，不赌系统时钟）─────────────

@has_chrome
def test_gate_clock_really_ticks_every_second(tmp_path):
    got = _run(tmp_path, "tick", """
      const el = document.getElementById('heroTime');
      el.textContent = 'SENTINEL';
      out.before = el.textContent;
      await new Promise(r => setTimeout(r, 1300));
      out.after = el.textContent;
    """, budget=5000)
    assert got.get("before") == "SENTINEL", "哨兵没塞进去 %s" % got
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", got.get("after", "")), \
        "1 秒 tick 没把时间改回来（时钟还是只在翻页时跳） %s" % got


# ───────────── ④ 静区扩到能罩住新组件，且磁盘呼吸层真的挖空 ─────────────

def test_gate_twinkle_layer_leaves_the_new_clock_area_dark():
    sf = _builder()
    x0, y0, x1, y1 = sf.CLOCK_BOX
    assert x0 <= 2260 and (x1 - x0) >= 400 and (y1 - y0) >= 110, \
        "静区没随合并后变宽的组件向左扩 %s" % (sf.CLOCK_BOX,)
    im = Image.open(os.path.join(WEB, "star_tw.png")).convert("RGBA")
    box = im.crop((x0, y0, x1 + 1, y1 + 1))
    alpha = box.tobytes()[3::4]
    assert max(alpha) == 0, "磁盘呼吸层在时钟静区里还有亮星（忘了重生成 star_tw.png）"


# ───────────── ⑤ 纸面：1s tick 与合并样式都在 ─────────────

def test_gate_tick_and_merged_styles_exist_on_paper():
    assert HTML.count("new Date().toTimeString()") >= 2, \
        "没有独立的 1 秒 tick（时间只在 paint() 里赋值）"
    assert "CLOCK_TICK_MS = 1000" in HTML, "没找到 1 秒周期的独立 tick"
    assert ".side .clock .cd" in CSS and ".side .clock .ct" in CSS, "侧栏时钟两栏样式没写"
    assert "CLOCK_BOX" in open(os.path.join(WEB, "build_starfield.py"), encoding="utf-8").read()
