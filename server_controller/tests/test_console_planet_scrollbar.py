"""星球滚条（工作台 .psb 移植）的行为闸门。

断言的是**跑起来的行为**：DOM 真的建出来、每个滚动容器真的挂上监听、
滚谁就跟谁、拖动真的改 scrollTop、尾迹真的按时收、卫星真的在转、
弹层真的接管滚条。不是读代码数字符串。

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


def _probe(body):
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try { %s }
    catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'psbProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, theme="dark", body=""):
    """探针页落在 web/ 里，相对路径的 d2.css 与星空图才解析得到；跑完即删。"""
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}
    html = shell.build_shot_html("accounts", bundle, theme=theme)
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_psb_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocpsb",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="psbProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来（页面没渲染？）"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 组件真建出来了：球体／尾迹／三颗卫星／层级 ─────────────

@has_chrome
def test_gate_planet_bar_is_built_with_its_parts(tmp_path):
    got = _run(tmp_path, "parts", "dark", """
      const p = document.getElementById('psb');
      out.exists = !!p;
      if (p) {
        out.core = p.querySelectorAll('.psb-core').length;
        out.tail = p.querySelectorAll('.psb-tail').length;
        out.satellites = p.querySelectorAll('.psb-core i').length;
        out.z = Number(getComputedStyle(p).zIndex);
        out.bg = getComputedStyle(p.querySelector('.psb-core')).backgroundImage.slice(0, 20);
      }
    """)
    assert got.get("exists") is True, got
    assert (got.get("core"), got.get("tail"), got.get("satellites")) == (1, 1, 3), got
    assert got.get("z", 0) >= 61, "星球被弹层（z-index 60）盖住了 %s" % got
    assert got.get("bg", "").startswith("radial-gradient"), "球体没用沿用的球面渐变 %s" % got


# ───────────── ② 每个滚动容器都挂上了监听 ─────────────

@has_chrome
def test_gate_every_scroll_container_is_bound(tmp_path):
    got = _run(tmp_path, "bound", "dark", """
      const nodes = document.querySelectorAll('#body, .nav, pre.log, .tblwrap, .ovl .box');
      out.count = nodes.length;
      out.flags = Array.from(nodes).map(n => n.dataset.psb || '');
    """)
    assert got.get("count", 0) >= 2, "页面里没找到滚动容器 %s" % got
    assert got.get("flags") and all(f == "1" for f in got["flags"]), \
        "有容器没挂上滚条监听 %s" % got


# ───────────── ③ 跟随：滚谁，星球就贴到谁的右边 ─────────────

@has_chrome
def test_gate_planet_follows_the_container_being_scrolled(tmp_path):
    got = _run(tmp_path, "follow", "dark", """
      const box = document.getElementById('body');
      box.insertAdjacentHTML('beforeend', '<div style="height:2400px;flex:none"></div>');
      PSB.bindAll();
      box.scrollTop = 260;
      box.dispatchEvent(new Event('scroll'));
      await new Promise(r => setTimeout(r, 60));
      const p = document.getElementById('psb'), r = box.getBoundingClientRect();
      out.off = p.classList.contains('off');
      out.top = parseInt(p.style.top, 10);
      out.right = parseInt(p.style.right, 10);
      out.wantTop = Math.round(r.top + 8);
      out.wantRight = Math.round(window.innerWidth - r.right + 2);
      out.transform = p.querySelector('.psb-core').style.transform;
    """)
    assert got.get("off") is False, "内容溢出了滚条却是隐藏态 %s" % got
    assert got.get("top") == got.get("wantTop") and got.get("right") == got.get("wantRight"), \
        "星球没贴到滚动容器右侧 %s" % got
    assert "translateY(" in got.get("transform", ""), "球体没跟着滚动位置走 %s" % got


# ───────────── ④ 速度尾迹：出现、到点自己收掉 ─────────────

@has_chrome
def test_gate_speed_tail_appears_and_retracts(tmp_path):
    got = _run(tmp_path, "tail", "dark", """
      const box = document.getElementById('body');
      box.insertAdjacentHTML('beforeend', '<div style="height:2400px;flex:none"></div>');
      PSB.bindAll();
      box.scrollTop = 300;
      box.dispatchEvent(new Event('scroll'));
      await new Promise(r => setTimeout(r, 40));
      const p = document.getElementById('psb');
      out.moving = p.classList.contains('moving');
      out.height = parseFloat(p.querySelector('.psb-tail').style.height);
      out.tailup = p.classList.contains('tailup');
      await new Promise(r => setTimeout(r, 340));
      out.movingAfter = p.classList.contains('moving');
      out.heightAfter = parseFloat(p.querySelector('.psb-tail').style.height);
    """)
    assert got.get("moving") is True and got.get("height", 0) > 0, "滚动时尾迹没出现 %s" % got
    assert got.get("tailup") is False, "向下滚尾巴却朝上 %s" % got
    assert got.get("movingAfter") is False and got.get("heightAfter") == 0, \
        "260ms 后尾迹没收回去 %s" % got


# ───────────── ⑤ 拖动：按住星球能改 scrollTop ─────────────

@has_chrome
def test_gate_dragging_the_planet_moves_the_content(tmp_path):
    got = _run(tmp_path, "drag", "dark", """
      const box = document.getElementById('body');
      box.insertAdjacentHTML('beforeend', '<div style="height:2400px;flex:none"></div>');
      PSB.bindAll();
      box.scrollTop = 0; box.dispatchEvent(new Event('scroll'));
      await new Promise(r => setTimeout(r, 30));
      const p = document.getElementById('psb'), r = p.getBoundingClientRect();
      out.before = box.scrollTop;
      const y = r.top + r.height * 0.8;
      p.dispatchEvent(new PointerEvent('pointerdown', {bubbles:true, button:0, clientY:y, pointerId:1}));
      p.dispatchEvent(new PointerEvent('pointermove', {bubbles:true, clientY:y, pointerId:1}));
      p.dispatchEvent(new PointerEvent('pointerup', {bubbles:true, pointerId:1}));
      out.after = box.scrollTop;
      out.drag = p.classList.contains('drag');
    """)
    assert got.get("after", 0) > got.get("before", 0), "拖动没带动内容 %s" % got
    assert got.get("drag") is False, "松手后还留在拖动态 %s" % got


# ───────────── ⑥ 弹层接管：滚条跟到弹层的 .box ─────────────

@has_chrome
def test_gate_overlay_takes_over_the_planet_bar(tmp_path):
    got = _run(tmp_path, "overlay", "dark", """
      const bodyBox = document.getElementById('body');
      bodyBox.insertAdjacentHTML('beforeend', '<div style="height:2400px;flex:none"></div>');
      PSB.bindAll();
      ovl('<div style="height:1600px">滚动我</div>');
      const b = document.querySelector('.ovl .box'), p = document.getElementById('psb');
      b.scrollTop = 200; b.dispatchEvent(new Event('scroll'));
      await new Promise(r => setTimeout(r, 60));
      out.bound = b.dataset.psb || '';
      out.right = parseInt(p.style.right, 10);
      out.want = Math.round(window.innerWidth - b.getBoundingClientRect().right + 2);
      closeOvl();
      await new Promise(r => setTimeout(r, 30));
      out.backToBody = parseInt(p.style.right, 10);
      out.wantBody = Math.round(window.innerWidth -
        document.getElementById('body').getBoundingClientRect().right + 2);
    """)
    assert got.get("bound") == "1", "弹层滚动容器没挂上滚条 %s" % got
    assert got.get("right") == got.get("want"), "弹层打开时星球没跟过去 %s" % got
    assert got.get("backToBody") == got.get("wantBody"), "关掉弹层后没跟回主区 %s" % got


# ───────────── ⑦ 卫星真的在转；减少动效时全停 ─────────────

@has_chrome
def test_gate_satellites_are_animating(tmp_path):
    got = _run(tmp_path, "orbit", "dark", """
      const p = document.getElementById('psb');
      out.counts = Array.from(p.querySelectorAll('.psb-core i'))
        .map(i => i.getAnimations().length);
    """)
    assert got.get("counts") and all(n >= 1 for n in got["counts"]), \
        "卫星没挂上绕转动画 %s" % got


def test_gate_reduced_motion_and_native_bar_are_handled():
    rm = re.search(r"@media\s*\(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\s*\}", CSS, re.S)
    assert rm and ".psb-core i" in rm.group(1) and "animation: none" in rm.group(1), \
        "系统要求减少动效时卫星还在转"
    assert re.search(r"::-webkit-scrollbar\s*\{\s*width:\s*0;\s*height:\s*0;\s*\}", CSS), \
        "原生滚动条没藏起来，会和星球滚条并排"
    assert "scrollbar-width: none" in CSS


# ───────────── ⑧ 两档都要看得见球体 ─────────────

@has_chrome
def test_gate_planet_is_visible_in_both_themes(tmp_path):
    for theme in ("dark", "light"):
        got = _run(tmp_path, "theme_" + theme, theme, """
          const core = document.querySelector('#psb .psb-core');
          out.bg = getComputedStyle(core).backgroundImage.slice(0, 20);
          out.border = getComputedStyle(core).borderTopWidth;
        """)
        assert got.get("bg", "").startswith("radial-gradient"), "%s 档球体没了 %s" % (theme, got)
        if theme == "light":
            assert got.get("border") != "0px", "纸色档球体没加细线，亮底上会糊 %s" % got
