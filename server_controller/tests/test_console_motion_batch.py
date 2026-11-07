"""动效与质感批的行为闸门（2026-11-06 收口）。

收口的是此前留在工作区的未提交 WIP：错落入场、流动星云与粒子、
翻页／弹层动效、侧栏竖线、滚动条与过渡。

修掉的唯一红灯：两片渐变光斑 + screen 加光把「呼吸层钉帧量亮度」的
低帧基线洗白（亮像素 10 → 57，3 倍判据过不了）。现在用纯色低透明度
云层，低帧回到 16、高帧 81（5 倍），并让暂停开关管住整条流动层。

纪律（server_controller/AGENTS.md §4）：断言运行时行为，别只读代码；
每条闸门都要能变红——改坏一处实现，对应测试必须失败。
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


def css_block(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.S)
    assert m, "CSS 里找不到 %s" % selector
    return m.group(1)


def own_block(selector):
    """取"选择器就是它自己"的那条规则体。

    css_block 用 re.search 找第一个子串匹配，`.hero .neb .shoot` 会先撞上
    `html[data-sky="paused"] .hero .neb .shoot`（暂停块），取到的不是本体。
    """
    for m in re.finditer(r"([^{}]+)\{", CSS):
        if m.group(1).strip() != selector:
            continue
        start, depth, i = m.end(), 1, m.end()
        while i < len(CSS) and depth:
            if CSS[i] == "{":
                depth += 1
            elif CSS[i] == "}":
                depth -= 1
            i += 1
        return CSS[start:i - 1]
    raise AssertionError("找不到 %s 自己的规则体" % selector)


def _probe(body):
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try { %s }
    catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'motionProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, theme="dark", body=""):
    """探针页必须落在 web/ 里：d2.css 与两张星空图按相对路径找，
    放临时目录会渲成白图（W4 闸门踩过）。跑完立刻删，不留文件。"""
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}
    html = shell.build_shot_html("accounts", bundle, theme=theme)
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_motion_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocmotion",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="motionProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来（页面没渲染？）"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ─────────────────── ① 流动层只在墨纸档，且不吃点击 ───────────────────

@has_chrome
def test_gate_neb_is_ink_only_and_never_takes_clicks(tmp_path):
    dark = _run(tmp_path, "ink", "dark", """
      const neb = document.querySelector('.hero .neb');
      out.display = getComputedStyle(neb).display;
      out.hits = getComputedStyle(neb).pointerEvents;
      out.motes = document.querySelectorAll('.hero .neb .mote').length;
      out.clouds = document.querySelectorAll('.hero .neb .cloud').length;
      out.shoot = document.querySelectorAll('.hero .neb .shoot').length;
    """)
    assert dark.get("display") == "block", dark
    assert dark.get("hits") == "none", "页眉上的点击被流动层吃掉了"
    assert dark.get("motes") >= 10 and dark.get("clouds") == 2 and dark.get("shoot") >= 5, dark

    light = _run(tmp_path, "paper", "light", """
      out.display = getComputedStyle(document.querySelector('.hero .neb')).display;
    """)
    assert light.get("display") == "none", "亮纸档也出现了流动层 %s" % light


# ─────────────── ② 暂停开关管住整条流动层（不只是呼吸层） ───────────────

@has_chrome
def test_gate_pause_switch_stops_the_whole_flow_layer(tmp_path):
    got = _run(tmp_path, "paused", "dark", """
      const pick = () => Array.from(document.querySelectorAll(
          '.hero .neb .cloud, .hero .neb .mote, .hero .neb .shoot'))
        .flatMap(el => el.getAnimations().map(a => a.playState));
      document.documentElement.dataset.sky = 'paused';
      await new Promise(r => setTimeout(r, 80));
      out.paused = pick();
      document.documentElement.dataset.sky = 'run';
      await new Promise(r => setTimeout(r, 80));
      out.running = pick();
    """)
    assert got.get("paused"), "流动层上一条动画都没挂上 %s" % got
    assert set(got["paused"]) == {"paused"}, "暂停后还有动画在跑 %s" % got
    assert got.get("running") and "running" in got["running"], "恢复后没重新跑起来 %s" % got


def test_gate_pause_rule_names_both_layers():
    """纸面兜底：暂停规则要同时点名呼吸层与流动层的三类元素。"""
    blocks = re.findall(r'(html\[data-sky="paused"\][^{]*)\{([^}]*)\}', CSS, re.S)
    assert len(blocks) >= 2, "暂停规则只剩一条（呼吸层与流动层要一起停）"
    selectors = "\n".join(s for s, _ in blocks)
    assert all("animation-play-state: paused" in decl for _, decl in blocks)
    for sel in (".hero .neb .cloud", ".hero .neb .mote", ".hero .neb .shoot"):
        assert sel in selectors, "暂停规则漏了 %s" % sel


# ─────────────── ③ 真渲染：流动层不许洗白呼吸层的低帧 ───────────────

@has_chrome
def test_gate_neb_does_not_wash_out_the_breathing_baseline(tmp_path):
    """钉两帧出图量亮度：把 screen 加光／渐变光斑放回来，这条必须变红。"""
    from PIL import Image

    def read(sky):
        p = str(tmp_path / ("neb_%s.png" % str(sky).replace(".", "_")))
        assert shell.shot(p, "dark", "dashboard", fixture=True, sky=sky) == 0
        im = Image.open(p).convert("L")
        x0, top, bot = 196, 84, 128
        w = im.size[0] - x0
        data = list(im.crop((x0 + int(w * .55), top, x0 + w, bot)).tobytes())
        return sum(1 for v in data if v >= 70), max(data)

    lo, hi = read(0.18), read(0.72)
    assert lo[0] <= 24, "流动层把低帧基线洗白了：%d 个亮像素" % lo[0]
    assert hi[0] >= lo[0] * 3 and hi[1] >= lo[1] + 25, \
        "呼吸层落不到屏幕上：%s → %s" % (lo, hi)


def test_gate_cloud_has_no_gradient_and_no_screen_blend():
    """页眉流动层不许用渐变光斑／加光混合；云层透明度上限 .05（实测 .04/.03 才过闸门）。

    范围只管页眉流动层：全站滚动条球体滑块的 radial-gradient 是**沿用工作台
    「星球滚条」既有组件**（工作台 ui/index.html 的 .psb），不是本批新增装饰，
    不构成设计口径冲突；云层的静态柔化同理，去掉会露出硬边圆斑（11-06 目检）。
    """
    for sel in (".hero .neb .c1", ".hero .neb .c2"):
        block = css_block(sel)
        assert "gradient" not in block, "%s 又用回渐变了" % sel
    assert "mix-blend-mode" not in css_block(".hero .neb .cloud"), "cloud 又用回加光混合了"
    for sel in (".hero .neb .c1", ".hero .neb .c2"):
        m = re.search(r"rgba\([^)]*,\s*\.(\d+)\)", css_block(sel))
        assert m and int(m.group(1)) <= 5, "%s 透明度 %s 过高" % (sel, m and m.group(1))


def test_gate_meteor_head_is_at_the_leading_end():
    """用户指出「流星的头尾反了」。

    流星从右侧 86% 处出发、沿自身轴线向左下飞（`translate3d(-340px,0,0)`），
    所以**前进端是元素的左端**，亮头必须落在左端。
    `linear-gradient(to right, 亮, 暗)` 的第一个色标在左端＝前进端；
    写成 `to left` 就落在右端＝尾端，头尾正好反过来。
    """
    block = own_block(".hero .neb .shoot")
    m = re.search(r"linear-gradient\(to (left|right),\s*(rgba?\([^)]*\))\s*,\s*(rgba?\([^)]*\))\)",
                  block)
    assert m, "流星的渐变写法变了：%s" % block
    assert "translate3d(-340px" in CSS, "流星位移改了，闸门要跟着改"

    def lum(c):
        if c.startswith("#"):
            r, g, b = (int(c[i:i + 2], 16) for i in (1, 3, 5))
        else:
            nums = re.findall(r"[\d.]+", c)
            r, g, b = (int(float(x)) for x in nums[:3])
        return 0.299 * r + 0.587 * g + 0.114 * b

    assert lum(m.group(2)) > lum(m.group(3)), \
        "第一色标（%s）不比尾端（%s）亮" % (m.group(2), m.group(3))
    assert m.group(1) == "right", \
        "渐变方向是 to %s：亮头落在尾端、尾端顶在前面（用户说的头尾反了）" % m.group(1)


# ─────────────── ④ 减少动效与错落入场的边界 ───────────────

def test_gate_reduced_motion_stops_every_new_animation():
    rm = re.search(r"@media\s*\(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\s*\}", CSS, re.S)
    assert rm, "没有减少动效的兜底块"
    block = rm.group(1)
    for rule in (".hero .neb .cloud", ".hero .neb .mote", ".hero .neb .shoot",
                 ".stagger", ".ovl", ".menu"):
        assert rule in block, "减少动效时漏了 %s" % rule
    assert block.count("animation: none") >= 4, "关闭动画的声明不够"


def test_gate_stagger_is_bounded_to_eight_and_covers_list_pages():
    delays = re.findall(
        r"#body\.enter \.stagger > \*:nth-child\((\d+)\) \{ animation-delay: \.(\d+)s; \}", CSS)
    assert [int(n) for n, _ in delays] == list(range(1, 9)), "错落延迟不是 1..8：%s" % delays
    assert [int(d) for _, d in delays] == [2, 4, 6, 8, 10, 12, 14, 16], "步长不是 18ms：%s" % delays
    assert HTML.count("stagger") >= 5, "带错落入场的容器太少"
    assert "#body.enter { animation: fade .18s" in CSS, "翻页容器没走 180ms 淡入"
    assert ".ovl .box { animation: rise .18s" in CSS, "弹层没走 180ms 上移"
