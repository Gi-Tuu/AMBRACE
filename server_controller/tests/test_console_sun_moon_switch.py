"""① 星球开关的行为闸门（11-07 返工后口径）。

用户否定了「两个 SVG 图标交叉淡入」，要的是：椭圆轨道里**一颗球**，
关＝月牙、开＝太阳，切换靠**遮罩形变**（不是两张图淡入），带速度拖尾、
三颗绕转卫星，尺寸放大、有工作台 .psb 星球滚条的球面渐变与光晕。

断言跑起来的行为：每个 .sw 真挂上星球结构、两态形状真不同、
过渡在 260ms 内、无障碍不丢。每条都要能变红。
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
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()


def css_block(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.S)
    assert m, "CSS 里找不到 %s" % selector
    return m.group(1)


def _bundle():
    b = {n: shell.api.call(n) for n in (
        "dashboard", "server_page", "log_page", "models_page", "flags_page",
        "audit_page", "accounts_page", "registration_page", "identity_page",
        "device_actions_page", "overview_page")}
    for k, v in shell.FIXTURE.items():
        b[k] = {"ok": True, "data": v}
    return b


def _probe(body):
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try { %s }
    catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'swProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, page, body):
    html = shell.build_shot_html(page, _bundle(), theme="dark")
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_sw_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocsw",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="swProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 每个开关都是一颗星球（跨 3 页）─────────────

@has_chrome
@pytest.mark.parametrize("page", ["log", "flags"])
def test_gate_every_switch_is_a_planet_in_an_oval(tmp_path, page):
    got = _run(tmp_path, "parts_" + page, page, """
      const sws = Array.from(document.querySelectorAll('.sw'));
      const i0 = sws[0] && sws[0].querySelector('i');
      const cs = i0 ? getComputedStyle(i0) : null;
      out.count = sws.length;
      out.planets = sws.filter(s => s.querySelectorAll('i > .sw-planet').length === 1).length;
      out.tails = sws.filter(s => s.querySelectorAll('i > .sw-tail').length === 1).length;
      out.satellites = sws.filter(s => s.querySelectorAll('.sw-planet > i').length === 3).length;
      out.oldIcons = sws.filter(s => s.querySelector('.sw-sun, .sw-moon')).length;
      out.w = cs ? Math.round(parseFloat(cs.width)) : 0;
      out.h = cs ? Math.round(parseFloat(cs.height)) : 0;
      out.radius = cs ? cs.borderRadius : '';
    """)
    assert got.get("count", 0) >= 1, "这一页没渲染出开关 %s" % got
    assert got.get("planets") == got.get("count") and got.get("tails") == got.get("count"), \
        "有开关没换成星球＋拖尾 %s" % got
    assert got.get("satellites") == got.get("count"), "三颗绕转卫星没挂齐 %s" % got
    assert got.get("oldIcons") == 0, "旧的双 SVG 图标还在（用户否定了图标方案） %s" % got
    assert got.get("w", 0) >= 50 and got.get("h", 0) >= 26, \
        "开关没放大到星球尺寸 %sx%s %s" % (got.get("w"), got.get("h"), got)
    assert "999px" in got.get("radius", ""), "轨道不是椭圆（胶囊形） %s" % got


# ───────────── ② 开关两态靠遮罩形变，不是两张图淡入 ─────────────

@has_chrome
def test_gate_on_off_is_a_shape_morph(tmp_path):
    got = _run(tmp_path, "morph", "flags", """
      const off = document.querySelector('.sw:not(.on) i'), on = document.querySelector('.sw.on i');
      const g = (el, pseudo) => el ? getComputedStyle(el, pseudo) : null;
      out.hasBoth = !!(off && on);
      if (off && on) {
        out.offPlanet = g(off.querySelector('.sw-planet'), null).transform;
        out.onPlanet = g(on.querySelector('.sw-planet'), null).transform;
        out.offShade = g(off.querySelector('.sw-face'), '::after').transform;
        out.onShade = g(on.querySelector('.sw-face'), '::after').transform;
        out.offDay = g(off.querySelector('.sw-face'), '::before').opacity;
        out.onDay = g(on.querySelector('.sw-face'), '::before').opacity;
        out.tailAnim = getComputedStyle(off.querySelector('.sw-tail')).animationName;
        out.tailWidth = getComputedStyle(off.querySelector('.sw-tail')).width;
      }
    """)
    assert got.get("hasBoth") is True, "这一页没有开/关两种状态 %s" % got
    assert got.get("offPlanet") != got.get("onPlanet"), "球体没在椭圆轨道里滑动 %s" % got
    assert got.get("offShade") != got.get("onShade"), \
        "月牙遮罩没形变（这不是形变动画） %s" % got
    assert float(got.get("offDay", 0)) < .1 and float(got.get("onDay", 0)) > .9, \
        "日面没在开态亮起 %s" % got
    assert got.get("tailAnim") == "none" and got.get("tailWidth") == "0px", \
        "拖尾不该常驻；只在切换瞬间扫一次 %s" % got
    assert ".sw.morph .sw-tail" in CSS and "@keyframes tail-sweep" in CSS, \
        "没有切换瞬间的一次性拖尾动画"


# ───────────── ③ 过渡流畅但不超 260ms ─────────────

def test_gate_morph_transition_is_within_budget():
    block = css_block(".sw i .sw-planet")
    dur = re.findall(r"\.(\d+)s", block)
    assert dur and max(float("0." + d) for d in dur) <= 0.26, \
        "形变过渡超了设计正本 260ms 上限 %s" % block
    assert "transform" in block and "border-radius" in block, \
        "形变没动 transform / border-radius %s" % block
    assert "transition" in css_block(".sw i .sw-face::after"), "月牙遮罩没有过渡"
    assert "overflow: hidden" in css_block(".sw i .sw-face"), \
        "遮罩没裁在球面里，会凸出一个暗色圆形"
    assert ".sw.on i .sw-tail" not in CSS, "开态还有常驻拖尾"
    assert "swOrbit" in CSS, "卫星没绕转"


# ───────────── ④ 无障碍与锁定态没被改造弄丢 ─────────────

@has_chrome
def test_gate_switch_semantics_survive_the_redesign(tmp_path):
    got = _run(tmp_path, "a11y", "flags", """
      const sws = Array.from(document.querySelectorAll('button.sw'));
      out.total = sws.length;
      out.checked = sws.filter(s => s.hasAttribute('aria-checked')).length;
      out.role = sws.filter(s => s.getAttribute('role') === 'switch').length;
      out.lockedDisabled = Array.from(document.querySelectorAll('.frow.locked button.sw'))
        .filter(s => s.disabled).length;
      out.lockedTotal = document.querySelectorAll('.frow.locked button.sw').length;
    """)
    assert got.get("total", 0) >= 2 and got.get("checked") == got.get("total"), \
        "aria-checked 丢了 %s" % got
    assert got.get("role") == got.get("total"), "role=switch 丢了 %s" % got
    assert got.get("lockedTotal", 0) >= 1 and got.get("lockedDisabled") == got.get("lockedTotal"), \
        "服务器锁定行没保持不可点 %s" % got


# ───────────── ⑤ 纸面：统一装扮、星球要素、减少动效 ─────────────

def test_gate_dresser_and_reduced_motion_rules_exist_on_paper():
    assert "dressSwitches" in HTML and "SW_ICON" in HTML, "没有统一的开关装扮入口"
    assert re.search(r"dressSwitches\(document\.getElementById\(\"body\"\)\)", HTML), \
        "装扮没接进 paint()，翻页重建 DOM 后会退化回空 <i>"
    assert 'class="sw-planet"' in HTML and 'class="sw-tail"' in HTML, "SW_ICON 没带星球与拖尾"
    assert "radial-gradient(circle at 34% 32%" in CSS, "球面渐变没沿用 .psb 口径"
    assert "@keyframes swOrbit" in CSS, "卫星绕转没写"
    assert ".sw-sun" not in CSS and ".sw-moon" not in CSS, "旧双图标规则还在"
    rm = re.search(r"@media\s*\(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\s*\}", CSS, re.S)
    assert rm and ".sw-planet > i" in rm.group(1), "减少动效时卫星还在转"
