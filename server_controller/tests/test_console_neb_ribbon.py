"""① 页眉 Canvas 星云引擎的行为闸门（11-07 返工后口径）。

用户否定了「满屏低透明度线条（像细菌）」，要的是工作台 `createNebula`
那种：柔和成团的星云缓缓流动 + 星尘光点沿轨道漂移／绕转。

约束：光点只落右 58%（左 40% 标题静区），画布固定亮度、
不与 W4 呼吸层共用动画，出图钉帧时不启 rAF（两帧可比）。
每条都要能变红。
"""
from __future__ import annotations

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
    d.id = 'nebProbe';
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
    f = os.path.join(WEB, "_neb_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocneb",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="nebProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ① 画布真渲染、光点数量够、真的画了一帧 ─────────────

@has_chrome
def test_gate_nebula_canvas_draws_stardust(tmp_path):
    got = _run(tmp_path, "canvas", """
      const cv = document.getElementById('nebCanvas');
      out.exists = !!cv;
      out.ctx = !!(cv && cv.getContext('2d'));
      out.parts = typeof Nebula !== 'undefined' ? Nebula.parts() : 0;
      out.frames = typeof Nebula !== 'undefined' ? Nebula.frames() : 0;
      out.w = cv ? cv.width : 0;
      out.ribbons = document.querySelectorAll('.ribbons, .rb').length;
    """)
    assert got.get("exists") is True and got.get("ctx") is True, "没有 Canvas 星云层 %s" % got
    assert got.get("parts", 0) >= 50, "星尘光点太少 %s" % got
    assert got.get("frames", 0) >= 1, "画布一帧都没画 %s" % got
    assert got.get("w", 0) > 0, "画布尺寸没 fit %s" % got
    assert got.get("ribbons") == 0, "被否定的丝带线条还在 %s" % got


# ───────────── ② 丝带方案彻底退场，画布不与呼吸层共用动画 ─────────────

def test_gate_no_ribbons_and_no_second_breathing_clock():
    assert "ribbons" not in HTML and "ribbon-flow" not in CSS, "丝带 DOM/CSS 没删干净"
    assert "class=\"neb-canvas\"" in HTML, "Canvas 没接进 .neb"
    canvas_rule = CSS.split(".hero .neb canvas {", 1)[-1].split("}", 1)[0]
    assert "animation" not in canvas_rule, \
        "画布又挂了 sky-breathe，会让标题静区跟着起伏 %s" % canvas_rule
    assert CSS.count("animation: sky-breathe") == 1, "呼吸层动画被挂到了别的层上"
    rm = re.search(r"@media\s*\(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\s*\}", CSS, re.S)
    assert rm and ".hero .neb canvas" in rm.group(1), "减少动效时没给画布兜底"


# ───────────── ③ 光点只落右 58%，标题静区与出图钉帧都不被污染 ─────────────

def test_gate_stardust_drifts_right_to_left_with_lit_cores():
    assert "vx: -(0.012" in HTML and "p.x < -20" in HTML, "星尘没有从右往左飘"
    assert "cl.x + cl.r < 0" in HTML, "星云团没有同向回卷"
    assert "W * 0.06" in HTML, "左侧那一片没铺星尘"
    assert "const X0 = W * 0.42" in HTML and "if (px >= X0)" in HTML, \
        "高亮核心没限制在右半（会破坏 W4 标题静区两帧一致）"
    assert "skyPhase" in HTML and "window.__SKY__" in HTML, \
        "高亮核心没随呼吸相位起伏（低帧会被洗白）"
    assert "rgba(255, 246, 224" in HTML, "没有高亮星点核心，只剩毛球状光晕"
    assert "window.__SKY__ === undefined" in HTML, \
        "出图钉帧还在跑 rAF，两帧截图内容会不一致"
    assert 'document.documentElement.dataset.sky === "paused"' in HTML, \
        "页面隐藏时画布没停"


# ───────────── ④ 真渲染钉两帧：W4 呼吸基线不许被洗白 ─────────────

@has_chrome
def test_gate_nebula_does_not_wash_out_the_breathing_baseline(tmp_path):
    def read(sky):
        p = str(tmp_path / ("neb_%s.png" % str(sky).replace(".", "_")))
        assert shell.shot(p, "dark", "dashboard", fixture=True, sky=sky) == 0
        im = Image.open(p).convert("L")
        x0, top, bot = 196, 84, 128
        w = im.size[0] - x0
        data = list(im.crop((x0 + int(w * .55), top, x0 + w, bot)).tobytes())
        return sum(1 for v in data if v >= 70), max(data)

    lo, hi = read(0.18), read(0.72)
    assert lo[0] <= 24, "星云把低帧基线洗白了：%d 个亮像素" % lo[0]
    assert hi[0] >= 30 and hi[0] >= lo[0] * 3, \
        "呼吸层没落到屏幕上 %d→%d" % (lo[0], hi[0])
    assert hi[1] >= lo[1] + 25, "峰值只动了 %d→%d" % (lo[1], hi[1])
