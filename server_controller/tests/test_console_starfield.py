"""W4 星空低频呼吸的闸门。

分两层：
* 纸面断言（不起浏览器）——呼吸层只动透明度、只在墨纸档、页面看不见就停、
  亮星位置与底板逐像素对齐、时钟与标题底下不许闪；
* 真渲染断言（无头 Chrome 出图）——把呼吸钉在两帧上量亮度，
  「变亮」这件事必须量得出来，而不是我嘴上说它动了。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess

import pytest

import console_api as api
import console_app as shell

HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")


def dashboard_bundle():
    """仪表盘没有示意数据，直接取真读数——呼吸测的是像素，不是数字。"""
    return {"dashboard": api.call("dashboard")}


def load_builder():
    spec = importlib.util.spec_from_file_location("build_starfield",
                                                  os.path.join(WEB, "build_starfield.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def css_block(selector):
    """取某条规则的声明体（简化：按选择器起点到下一个 `}` 收尾，允许跨行）。"""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS, re.S)
    assert m, "CSS 里找不到 %s" % selector
    return m.group(1)


def keyframes(name):
    m = re.search(r"@keyframes\s+" + re.escape(name) + r"\s*\{(.*?)\n\}", CSS, re.S)
    assert m, "没有 @keyframes %s" % name
    return m.group(1)


# ─────────────────────────── 素材：底板不许被呼吸改动 ───────────────────────────

def test_base_plate_is_byte_identical_to_the_generator():
    """底板 PNG 必须与生成器现在的输出逐字节一致——呼吸只许叠层，不许动底子。"""
    b = load_builder()
    base, tw = b.build()
    import io
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    assert hashlib.sha256(buf.getvalue()).hexdigest() == \
        hashlib.sha256(open(os.path.join(WEB, "star_dark.png"), "rb").read()).hexdigest(), \
        "star_dark.png 与生成器对不上：要么改了种子/画法没重出图，要么呼吸动了底板"
    buf2 = io.BytesIO()
    tw.save(buf2, format="PNG")
    assert hashlib.sha256(buf2.getvalue()).hexdigest() == \
        hashlib.sha256(open(os.path.join(WEB, "star_tw.png"), "rb").read()).hexdigest()


def test_twinkle_layer_is_a_subset_of_the_base_stars():
    """呼吸层的每个亮点都得长在一颗真星上（晕允许差一像素）——错位就成了两层星。"""
    b = load_builder()
    base, tw = b.build()
    px, tp = base.load(), tw.load()
    w, h = base.size

    def lit_base(x, y):
        r, g, bl = px[x, y]
        return r > 60 and g > 55 and bl > 45

    lit = off = 0
    for y in range(h):
        for x in range(w):
            if tp[x, y][3] <= 0:
                continue
            lit += 1
            if not any(lit_base(min(w - 1, max(0, x + dx)), min(h - 1, max(0, y + dy)))
                       for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
                off += 1
    assert lit > 2000, "呼吸层几乎是空的（%d 个点）" % lit
    assert off == 0, "%d 个亮点在底板附近找不到对应的星" % off


def test_twinkle_never_touches_the_title_or_the_clock():
    """左侧 40% 是标题静区，右上是时钟——呼吸不许在这两块底下闪。"""
    b = load_builder()
    _, tw = b.build()
    tp = tw.load()
    w, h = tw.size
    for y in range(h):
        for x in range(int(w * 0.40)):
            assert tp[x, y][3] == 0, "左静区 (%d,%d) 有亮星" % (x, y)
    x0, y0, x1, y1 = b.CLOCK_BOX
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            assert tp[x, y][3] == 0, "时钟静区 (%d,%d) 有亮星" % (x, y)


def test_twinkle_uses_only_the_existing_ink():
    """不许为呼吸引入新颜色：亮星层的颜色必须就是 STAR。"""
    b = load_builder()
    _, tw = b.build()
    want = tuple(int(v, 16) for v in re.findall("..", b.STAR[1:]))
    got = {c[1][:3] for c in tw.getcolors(maxcolors=1 << 20) if c[1][3] > 0}
    assert got == {want}, "亮星层出现了新颜色 %s" % got


# ─────────────────────────── 纸面：CSS / HTML / JS ───────────────────────────

def test_breathing_is_low_frequency_and_opacity_only():
    """低频＝单程 ≥10 秒；只动 opacity——动 transform/filter/background 都要重绘或重排。"""
    kf = keyframes("sky-breathe")
    props = set(re.findall(r"([a-z-]+)\s*:", kf))
    assert props == {"opacity"}, "呼吸动了 %s，只许 opacity" % props
    lo = float(re.search(r"from\s*\{\s*opacity:\s*([0-9.]+)", kf).group(1))
    hi = float(re.search(r"to\s*\{\s*opacity:\s*([0-9.]+)", kf).group(1))
    assert lo < hi, "呼吸的两端反了"
    assert hi - lo >= 0.3, "起伏只有 %.2f，量不出呼吸" % (hi - lo)
    rule = css_block('html[data-theme="dark"] .hero .sky')
    dur = float(re.search(r"animation:\s*sky-breathe\s+(\d+(?:\.\d+)?)s", rule).group(1))
    assert dur >= 10, "周期 %.1fs 不算低频，会像闪烁" % dur
    assert "alternate" in rule and "infinite" in rule, "呼吸要来回，不要跳回起点"


def test_breathing_is_dark_only_and_pauses_when_hidden():
    """亮纸档没有星空；页面看不见（最小化/隐藏）必须整条停下，不给合成器留常驻活。"""
    assert "display: none" in css_block(".hero .sky"), "默认态（亮纸档）不许出现呼吸层"
    assert "animation" in css_block('html[data-theme="dark"] .hero .sky')
    paused = css_block('html[data-sky="paused"] .hero .sky')
    assert "animation-play-state: paused" in paused
    assert 'document.hidden' in HTML and "visibilitychange" in HTML
    assert re.search(r'dataset\.sky\s*=\s*document\.hidden\s\?\s*"paused"\s:\s*"run"', HTML)
    rm = re.search(r"@media\s*\(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\s*\}", CSS, re.S)
    assert rm and ".sky" in rm.group(1) and "animation: none" in rm.group(1), \
        "系统要求减少动效时没有静止兜底"


def test_breathing_costs_no_js_frames():
    """W4 呼吸层不许有 rAF/定时器：靠纯 CSS。

    页眉另有独立的 Canvas 星云流动层（用户要的星尘光点），它有自己的时钟；
    这条只管呼吸层那一段 JS，别把全文的 rAF 都算到呼吸头上。
    """
    sky_js = HTML[HTML.index("W4 星空呼吸"):HTML.index("const NAV")]
    assert "setInterval" not in sky_js and "requestAnimationFrame" not in sky_js
    assert "sky-breathe" not in sky_js, "JS 里再驱动一遍动画＝两套时钟"
    # 呼吸层仍是唯一挂 sky-breathe 的元素
    assert CSS.count("animation: sky-breathe") == 1, "sky-breathe 被挂到了别的层上"


def test_sky_layer_is_behind_the_text_and_takes_no_clicks():
    """星要压在字底下，而且不许吃掉点击（页眉上有标题与时钟）。"""
    hero = HTML[HTML.index('<div class="hero">'):HTML.index('<div class="body"')]
    # 日期时间已于 11-07 返工移到侧栏，页头只剩标题；时钟位置由 clock_merge 闸门管。
    assert hero.index('class="sky"') < hero.index('class="ht"'), \
        "呼吸层排到了文字后面，会盖住标题"
    assert 'aria-hidden="true"' in hero
    assert "pointer-events: none" in css_block(".hero .sky")
    assert "z-index" not in css_block(".hero .sky"), \
        "呼吸层靠 DOM 顺序压在字底下，抬 z-index 就会盖住标题"
    assert "isolation: isolate" in css_block(".hero"), \
        "screen 混合要封在页眉里，不然会跟整页背景混"


def test_no_backdrop_filter_anywhere():
    """这台机器的老规矩：backdrop-filter 会拖慢整个桌面，星空不许把它带回来。"""
    assert "backdrop-filter" not in CSS


@has_chrome
def test_pinned_frames_actually_differ_on_screen(tmp_path):
    """钉两帧出图：页眉里那条无字带要量得出变亮——「动了」得是量出来的，不是我说的。

    取 y 84..128：标题(30..78)与时钟(32..73)都在这条带子上方，
    否则秒针一跳就把亮度差污染掉（第一版就栽在这儿）。
    """
    from PIL import Image

    def strip(sky):
        p = str(tmp_path / ("sky_%s.png" % str(sky).replace(".", "_")))
        assert shell.shot(p, "dark", "dashboard", fixture=True, sky=sky) == 0
        return Image.open(p).convert("L")

    lo, hi = strip(0.18), strip(0.72)
    assert lo.size == hi.size
    x0 = 196                                     # 侧栏宽度：页眉从这儿开始
    TOP, BOT = 84, 128                           # 标题(30..78)与时钟(32..73)都在这条带子上方，
    # 否则秒针一跳就把读数污染掉（第一版就栽在这儿）

    def col(im, fx0, fx1):
        w = lo.size[0] - x0
        return list(im.crop((x0 + int(w * fx0), TOP, x0 + int(w * fx1), BOT)).tobytes())

    def peaks(data, cut=70):
        """返回 (够亮的像素数, 峰值)。呼吸是几百颗星变亮、不是整片洗白，
        所以量亮点数——均值会被几万个纸底像素摊平（第一版就被摊平过）。"""
        return sum(1 for v in data if v >= cut), max(data)

    right_lo, right_hi = peaks(col(lo, 0.55, 1.0)), peaks(col(hi, 0.55, 1.0))
    assert right_hi[0] >= 30 and right_hi[0] >= right_lo[0] * 3, \
        "右半页眉亮星数 %d→%d：呼吸没落到屏幕上" % (right_lo[0], right_hi[0])
    assert right_hi[1] >= right_lo[1] + 25, \
        "峰值只动了 %d→%d" % (right_lo[1], right_hi[1])
    left_lo, left_hi = peaks(col(lo, 0.0, 0.34)), peaks(col(hi, 0.0, 0.34))
    assert left_lo == left_hi, "标题那一列跟着起伏了 %s→%s" % (left_lo, left_hi)


@has_chrome
def test_light_theme_has_no_breathing(tmp_path):
    """亮纸档：呼吸层根本不该在屏幕上，两帧之间那条带子必须逐字节相同。"""
    from PIL import Image

    def grab(sky):
        p = str(tmp_path / ("light_%s.png" % str(sky).replace(".", "_")))
        assert shell.shot(p, "light", "dashboard", fixture=True, sky=sky) == 0
        return Image.open(p).crop((196, 84, 1360, 128)).tobytes()

    assert grab(0.18) == grab(0.72), "亮纸档的页眉也在起伏"


@has_chrome
def test_dom_keeps_the_layer_and_its_pin(tmp_path):
    """渲染后的 DOM 里呼吸层还在、且被钉到了指定帧（钉不住就说明动画压过了内联样式）。"""
    html = shell.build_shot_html("dashboard", dashboard_bundle(), theme="dark", sky=0.55)
    f = tmp_path / "sky.html"
    f.write_text(html, encoding="utf-8")
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                        "--window-size=1360,880", "--temp-profile=ocgate",
                        "--virtual-time-budget=4000", "file:///" + str(f).replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=90)
    assert r.returncode == 0, r.stderr[-300:]
    dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
    m = re.search(r'<div class="sky"[^>]*>', dom)
    assert m, "渲染后没有 .sky"
    style = re.search(r'style="([^"]*)"', m.group(0))
    assert style, m.group(0)
    assert "opacity: 0.55" in style.group(1), style.group(1)
    # Chrome 把 animation:none 序列化成一长串，名字位上必须是 none
    assert re.search(r"animation:[^;]*\bnone\b", style.group(1)), \
        "钉帧没关掉动画：%s" % style.group(1)
    assert "sky-breathe" not in style.group(1)


def chrome(args, src):
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--window-size=1360,880", "--temp-profile=ocgate"] + args +
                       ["file:///" + str(src).replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    return r


def render_in_web(name, html):
    """页面必须落在 web/ 里：d2.css 与两张星空图是按相对路径找的，
    放临时目录会渲成一张白图（第一版就这么被骗过一次）。"""
    f = os.path.join(WEB, name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    return f


PROBE = """
<script>
window.addEventListener('load', function(){
  setTimeout(function(){
    const el = document.getElementById('sky');
    const as = (document.getAnimations() || []).filter(a => a.animationName === 'sky-breathe');
    const vals = [];
    for (const t of [0, 3500, 7000, 10500, 14000, 28000]) {
      as.forEach(a => { try { a.currentTime = t; } catch (e) {} });
      vals.push(getComputedStyle(el).opacity);
    }
    const d = document.createElement('div');
    d.id = 'skyProbe';
    d.textContent = JSON.stringify({n: as.length, state: as.map(a => a.playState), vals: vals});
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>"""


@has_chrome
def test_animation_is_attached_and_sweeps_the_range(tmp_path):
    """把动画挂在时间轴上采样：呼吸必须真的在跑，且走的是 ease-in-out 的那条弧。

    无头截图不吃合成器的动画时钟（两个虚拟时间截出来一模一样），
    所以这里用 currentTime 采样计算样式——量的是「它会扫过整个区间」，
    区间两端的实际亮度由 test_pinned_frames_actually_differ_on_screen 负责。
    """
    html = shell.build_shot_html("dashboard", dashboard_bundle(), theme="dark")
    html = html.replace("</body>", PROBE, 1)
    src = render_in_web("_sky_probe.html", html)
    try:
        dom = chrome(["--dump-dom", "--virtual-time-budget=2500"], src).stdout
    finally:
        os.remove(src)
    m = re.search(r'id="skyProbe">(.*?)</div>', dom, re.S)
    assert m, "探针没跑起来（页面没渲染？）"
    got = json.loads(m.group(1))
    assert got["n"] == 1, "挂在呼吸层上的动画有 %d 条" % got["n"]
    assert got["state"] == ["running"], got["state"]
    v = [float(x) for x in got["vals"]]
    assert abs(v[0] - 0.18) < 0.01, "起点不在 .18：%s" % v
    assert abs(v[4] - 0.72) < 0.01, "14 秒那一下没到峰值 .72：%s" % v
    assert abs(v[5] - 0.18) < 0.01, \
        "28 秒（一个来回）没回到 .18，alternate 没生效：%s" % v
    assert v[0] < v[1] < v[2] < v[3] < v[4], "扫过区间却是平的：%s" % v
    assert abs(v[2] - 0.45) < 0.015, "半程该在区间中点 .45：%s" % v
    # 缓入缓出：前 1/4 走得比匀速慢（.25 vs 匀速的 .315），后 1/4 走得比匀速快（.65 vs .585）
    assert v[1] < 0.285, "前 1/4 就到 %.4f，是匀速不是缓入：%s" % (v[1], v)
    assert v[3] > 0.60, "后 1/4 才到 %.4f，是匀速不是缓出：%s" % (v[3], v)


@has_chrome
def test_paused_attribute_is_attached_when_hidden(tmp_path):
    """把 document.hidden 假成 true 再渲染：<html> 上要出现 data-sky="paused"。"""
    html = shell.build_shot_html("dashboard", dashboard_bundle(), theme="dark")
    html = html.replace("<script>",
                        "<script>Object.defineProperty(document,'hidden',{get:function(){return true}});</script>\n<script>",
                        1)
    src = render_in_web("_sky_hidden.html", html)
    try:
        dom = chrome(["--dump-dom", "--virtual-time-budget=2500"], src).stdout
    finally:
        os.remove(src)
    assert 'data-sky="paused"' in dom, dom[:400]


# ───────────── ⑥ 横幅上不许有竖着的"疏密台阶" ─────────────

def test_gate_band_brightness_has_no_vertical_step():
    """40% 那条一刀切会留下明显的疏密断层（用户看图指出"衔接突兀"）。

    旧写法是 `a = randint(130,255) if x>40% else randint(28,86)`：星点数量一样，
    亮度整整跳一档，落在眼里就是"左边一片淡雾、右边一片亮星，中间一条线"。
    这里按 24 条竖带量最亮像素，任何相邻两带的落差都不许超过 30。
    """
    from PIL import ImageStat
    from PIL import ImageStat
    b = load_builder()
    base, _tw = b.build()
    im = base.convert("L")
    w, h = im.size
    bands = 24
    tops = [ImageStat.Stat(im.crop((i * w // bands, 0, (i + 1) * w // bands, h))).extrema[0][1]
            for i in range(bands)]
    jumps = [abs(tops[i + 1] - tops[i]) for i in range(bands - 1)]
    assert max(jumps) <= 30, "横幅有一条竖着的亮度台阶：带峰值 %s" % tops


def test_gate_twinkle_layer_fades_in_instead_of_stopping_at_a_line():
    """呼吸层也不许在一条竖线上断掉：静区边界要按概率淡入。"""
    b = load_builder()
    _base, tw = b.build()
    a = tw.split()[3]
    w, h = tw.size
    bands = 24
    cnt = [sum(1 for v in a.crop((i * w // bands, 0, (i + 1) * w // bands, h)).tobytes()
               if v) for i in range(bands)]
    for i in range(bands - 1):
        assert not (cnt[i] == 0 and cnt[i + 1] > 60), \
            "呼吸层在第 %d 条带上从 0 直接跳到 %d 个点（一条硬边）" % (i, cnt[i + 1])
    assert cnt[0] == 0, "标题静区里还有呼吸亮星"
    assert max(cnt) > 150, "整条呼吸层几乎空了：%s" % cnt
