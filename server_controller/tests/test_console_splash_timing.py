"""11-07 第七轮反馈的闸门：开屏慢下来 + 去掉分段跳变。

用户原话：「动画不流畅，而且动画太快了」。

病根有三个，闸门逐条盯住：
  1. 2.9s 走完"蓄势→盖满→收束"三段，太赶；
  2. 关键帧动 left/top，每帧触发布局，越大越卡；
  3. 43%→47% 原地停一帧、外加整条共用一个缓动曲线，段与段之间节奏突变。

纪律：先红后绿；断言浏览器真正解析出来的关键帧，不是注释里的承诺。
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
APP = open(os.path.join(HERE, "console_app.py"), encoding="utf-8").read()


def _rule(sel):
    m = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", CSS)
    assert m, "找不到规则 %s" % sel
    return m.group(1)


def _journey():
    m = re.search(r"@keyframes planet-journey\s*\{(.*?)\n\}", CSS, re.S)
    assert m, "没有 planet-journey"
    return m.group(1)


def _split2(s):
    """按括号深度切 translate 的两个参数（calc(...) 里有逗号也不怕）。"""
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            return s[:i].strip(), s[i + 1:].strip()
    raise AssertionError("translate 只有一个参数：%s" % s)


def _stops():
    """[(百分比, x 原文, y 原文, 缩放)]，只认 translate(...) scale(...) 写法。"""
    out = []
    for m in re.finditer(r"([\d.]+)%\s*\{([^}]*)\}", _journey()):
        pct, body = float(m.group(1)), m.group(2)
        t = re.search(r"transform:\s*translate\((.*)\)\s*scale\(([\d.]+)\)", body)
        assert t, "关键帧 %g%% 不是 translate+scale：%s" % (pct, body)
        xs, ys = _split2(t.group(1))
        out.append((pct, xs, ys, float(t.group(2))))
    return out


def _pos(s):
    """从 '14.5vw' / '47vh' / 'calc(89vw - 74px)' 里取数值；纯 px 返回 None。"""
    m = re.search(r"(-?[\d.]+)(vw|vh)", s)
    return float(m.group(1)) if m else None


def _cover_index(st):
    """盖满那一刻：第一个到达最大缩放的帧（JS 的 SPLASH_COVER 对的是它）。"""
    mx = max(s[3] for s in st)
    return st.index(next(s for s in st if s[3] == mx))


def _arc_start(st):
    """收束起点：最后一个处于最大缩放的帧（盖满后的停顿也算在里面）。"""
    mx = max(s[3] for s in st)
    return max(i for i, s in enumerate(st) if s[3] == mx)


def _css_ms():
    m = re.search(r"planet-journey\s+([\d.]+)s", CSS)
    assert m, "没写 planet-journey 的时长"
    return int(round(float(m.group(1)) * 1000))


# ───── ① 太快 ─────
def test_journey_is_paced_out():
    ms = _css_ms()
    assert ms >= 4400, "整段旅程只有 %dms，还是太快（盖满后要留 2s 收束）" % ms
    assert ms <= 6200, "拖到 %dms 会让人以为卡住" % ms
    assert re.search(r"planet-journey\s+[\d.]+s\s+linear", CSS), \
        "整条必须 linear：段内再用缓动，多段串起来就是一顿一顿"


# ───── ② 不流畅：不许动 left/top ─────
def test_journey_animates_transform_only():
    blk = _journey()
    assert "left:" not in blk and "top:" not in blk, \
        "关键帧还在动 left/top，每帧触发布局，球越大越卡"
    assert blk.count("translate(") == blk.count("scale(") >= 14, \
        "关键帧太少，折线感就是卡顿感的来源"
    hero = _rule(".sp-hero")
    assert "left: 0" in hero and "top: 0" in hero, "球心没钉在视口原点"
    assert "margin: -64px 0 0 -64px" in hero, "128px 的球心没对齐原点，translate 会偏"
    assert "will-change: transform" in hero, "没给合成器提示，缩放会走重绘"


# ───── ③ 不许分段跳变 ─────
def test_scale_never_jumps_between_stops():
    st = _stops()
    assert len(st) >= 14, "只有 %d 个关键帧，必然顿" % len(st)
    for (p1, _, _, s1), (p2, _, _, s2) in zip(st, st[1:]):
        hi, lo = max(s1, s2), min(s1, s2)
        assert lo > 0 and hi / lo <= 2.1, \
            "%g%%→%g%% 一步放大 %.2f 倍，画面会突然弹一下" % (p1, p2, hi / lo)


def test_growth_is_monotone_and_has_no_dead_hold():
    st = _stops()
    for p, xs, _, _ in st:
        assert _pos(xs) is not None, \
            "关键帧 %g%% 的横坐标不是视口单位（球心钉在原点，位置只能写 vw）" % p
    k = _cover_index(st)
    grow = st[:k + 1]
    # 蓄势段允许一次原地（18% 那一下），但盖满之前的放大必须一路向上
    moving = [s for s in grow if s[3] > 1.01]
    for a, b in zip(moving, moving[1:]):
        assert b[3] >= a[3], "盖满途中 %g%%→%g%% 缩放回退了" % (a[0], b[0])
        assert _pos(b[1]) >= _pos(a[1]), "盖满途中没一路向右"
    holds = sum(1 for a, b in zip(st, st[1:]) if a[1:] == b[1:])
    assert holds <= 2, "整段原地停了 %d 次（只该有蓄势、盖满各一次）" % holds


# ───── ④ 收束段：单调、够密、真的是弧线 ─────
def test_arc_shrinks_monotonically_to_the_corner():
    st = _stops()
    arc = st[_arc_start(st):]
    assert len(arc) >= 6, "收束段只有 %d 个点，弧线退化成折线" % len(arc)
    for a, b in zip(arc, arc[1:]):
        assert b[3] < a[3], "收束段缩放没单调变小：%g→%g" % (a[3], b[3])
        assert _pos(b[1]) > _pos(a[1]), "没往右上角走（x）"
        ay, by = _pos(a[2]), _pos(b[2])
        if ay is not None and by is not None:
            assert by < ay, "没往右上角走（y）"


def test_arc_bulges_off_the_chord():
    st = _stops()
    arc = st[_arc_start(st):]
    # 末点 y 是 132px（页眉底线），拿来算弦会失真，改用 92.9% 那个点当弦的另一端
    end = next(i for i in range(len(arc) - 1, -1, -1) if _pos(arc[i][2]) is not None)
    x0, y0 = _pos(arc[0][1]), _pos(arc[0][2])
    x1, y1 = _pos(arc[end][1]), _pos(arc[end][2])
    devs = []
    for _, xs, ys, _ in arc[1:end]:
        x, y = _pos(xs), _pos(ys)
        devs.append((y0 + (x - x0) / (x1 - x0) * (y1 - y0)) - y)
    assert devs and max(devs) >= 1.5, "轨迹贴着弦走，是直线不是弧线"
    assert min(devs) >= -0.2, "弧线跑到弦下方了，应该先扬起再落向页眉"


# ───── ⑤ 落点必须正好是页眉那颗星球 ─────
def _hero_h():
    """页眉高度：字面量或 var(--hero-h) 都读得出来（CSS 里只许有一个真源）。"""
    m = re.search(r"flex:\s*0 0 (\d+)px", _rule(".hero"))
    if m:
        return int(m.group(1))
    assert "var(--hero-h)" in _rule(".hero"), "页眉高度既不是像素也不是 --hero-h"
    return int(re.search(r"--hero-h:\s*(\d+)px", CSS).group(1))


def _hero_center():
    """从 `.hero-planet` 与 `.hero` 的纸面值推出球心（相对视口右上角，CSS px）。"""
    hero = _rule(".hero-planet")
    w = int(re.search(r"width:\s*(\d+)px", hero).group(1))
    right = float(re.search(r"right:\s*(-?[\d.]+)px", hero).group(1))
    bottom = float(re.search(r"bottom:\s*(-?[\d.]+)px", hero).group(1))
    hh = _hero_h()
    return w, right + w / 2.0, hh - bottom - w / 2.0   # 距右边 / 距顶


def test_lands_exactly_on_the_header_planet():
    st = _stops()
    pct, xs, ys, sc = st[-1]
    assert pct == 100.0, "最后一帧不在 100%%"
    w, from_right, from_top = _hero_center()
    want_x = "calc(100vw-%gpx)" % round(from_right)
    assert re.sub(r"\s+", "", xs) == re.sub(r"\s+", "", want_x), \
        "落点跟页眉球的球心对不上：应为 %s（现在 %s），交接会横跳" % (want_x, xs)
    assert re.sub(r"\s+", "", ys) == "%gpx" % round(from_top), \
        "纵坐标应压在 %gpx（现在 %s）" % (round(from_top), ys)
    assert abs(sc - w / 128.0) < 0.01, "落点缩放 %.3f 与页眉球 %dpx/128px 对不上" % (sc, w)


def test_planet_sits_in_the_corner_third_off_screen():
    """用户定稿：右边约 1/3 不入镜，球心再往下一点。"""
    w, from_right, from_top = _hero_center()
    off = w / 2.0 - from_right                # 探出右边缘的宽度
    assert abs(off - w / 3.0) <= 6, "右边缘外露 %gpx，应约 1/3 球径（%gpx）" % (off, w / 3.0)
    hh = _hero_h()
    assert from_top > hh, "球心要低于页眉底边（%gpx），现在 %gpx" % (hh, from_top)
    assert from_top < hh + w / 2.0, "球心太低，球会整个沉到页眉下面去了"


# ───── ⑤b 月亮越大越黑，缩回来自动变亮 ─────
def test_moon_goes_dark_as_it_grows():
    m = re.search(r"@keyframes planet-night\s*\{(.*?)\n\}", CSS, re.S)
    assert m, "没有 planet-night：月亮不会随大小变暗"
    stops = {float(a): float(b)
             for a, b in re.findall(r"([\d.]+)%\s*\{\s*opacity:\s*([\d.]+)\s*;?\s*\}",
                                    m.group(1))}
    assert stops, m.group(1)
    peak = max(stops.values())
    assert peak >= 0.85, "盖满时只压暗到 %.2f，不是「接近全黑」" % peak
    at_peak = [k for k, v in stops.items() if v == peak][0]
    st = _stops()
    assert abs(at_peak - st[_cover_index(st)][0]) < 1.5, \
        "最黑那帧在 %g%%，应在盖满全屏那一帧 %g%%" % (at_peak, st[_cover_index(st)][0])
    assert stops[max(stops)] <= 0.02, "收尾没回亮：%g%% 还留着 %.2f" % (max(stops), stops[max(stops)])
    assert "html[data-theme=\"dark\"] .sp-hero::after" in CSS, "日落日（纸色档）不该被压暗"
    assert "html[data-theme=\"light\"] .sp-hero::after" not in CSS, \
        "给纸色档也写了压暗——太阳是光源，黑不了"


# ───── ⑨ 开场构图：过肩巨星球 / 中间带环小球 / 右侧星球 ─────
def test_opening_matches_the_sketch():
    """用户手绘图：左边是放大很大的星球（只露右缘，过肩镜头）、中间最小且带星环、右边一颗星球。"""
    st = _stops()
    p0 = st[0]
    assert p0[3] >= 10, "开场那颗球只有 %.1f 倍，不是「放大很大」" % p0[3]
    assert _pos(p0[1]) <= -15, "开场球心只到 %s，没退到画面外做「过肩」" % p0[1]

    a = _rule(".sp-orb-a")
    wa = int(re.search(r"width:\s*(\d+)px", a).group(1))
    wb = int(re.search(r"width:\s*(\d+)px", _rule(".sp-orb-b")).group(1))
    assert wa <= 130, "中间那颗 %dpx，应该是最小的一颗" % wa
    assert wb >= 240 and wb > wa, "右边那颗 %dpx，应该是一颗像样的星球" % wb
    assert ".sp-orb-a::after" in CSS and "border-radius: 50%" in _rule(".sp-orb-a::after"), \
        "中间那颗没有星环"


# ───── ⑩ 盖满后移除背景，只留那颗星球；底色是页面纸色，不是硬黑 ─────
def test_backdrop_drops_at_cover_leaving_only_the_planet():
    """用户定稿：「全屏遮盖后移除背景只保留左边的星球」。

    收束段不能是一块"硬黑底"——遮罩底色必须就是页面纸色，移除背景后剩下的
    那一层和落位后的页面同色，看不出是两块不同的背景。
    """
    sp = _rule("#splash")
    assert "var(--paper)" in sp, "遮罩底色不是页面纸色：%s" % sp
    assert "#0d0b0a" not in sp, "遮罩还是硬编码的近黑，收束段会多出一块黑幕"
    assert re.search(r'sp\.querySelector\("\.sp-stars"\);\s*\n\s*if \(stars\) stars\.remove\(\);',
                      HTML), "盖满后没移除背景（星空留着就不是「只剩那颗星球」）"
    assert "SPLASH_COVER" in HTML, "移除背景的时刻没跟常量挂钩"
    cov = float(re.search(r"SPLASH_COVER\s*=\s*([\d.]+)", HTML).group(1))
    st = _stops()
    assert abs(cov * 100 - st[_cover_index(st)][0]) < 0.6, \
        "JS 的盖满时刻 %s%% 与 CSS 关键帧 %g%% 对不上" % (round(cov * 100, 1),
                                                         st[_cover_index(st)][0])


# ───── ⑩b 星球落位时，内容区从下方顶上来 ─────
def test_cards_rise_into_place_when_the_planet_lands():
    """用户：「改为上升的卡片（仅仅启动星球到位时升起），到位再固定」。"""
    rise = _rule("#body.rise")
    assert "body-rise" in rise, "内容区没有上升动画：%s" % rise
    assert "both" in rise, "动画没用 both，落位后会回弹一下"
    kf = re.search(r"@keyframes body-rise\s*\{(.*?)\n\}", CSS, re.S)
    assert kf, "没有 body-rise 关键帧"
    body = kf.group(1)
    assert "translate3d(0," in body or "translateY(" in body, "上升动画里没有位移"
    m = re.search(r"translate3d\(0,\s*([\d.]+)px", body)
    assert m, "上升距离不是固定像素"
    assert float(m.group(1)) >= 20, "上升距离只有 %spx，等于没升" % m.group(1)
    assert "opacity" in body, "上升时没有渐显"
    assert "#body.rise { animation: none; }" in CSS, "reduced-motion 里没停上升"

    tail = HTML[HTML.index("SPLASH_MS"):]
    assert 'classList.add("rise")' in tail, "上升没跟落位挂钩"
    assert 'classList.remove("enter")' in tail, "翻页动画没让位，两个动画会打架"
    i = tail.index('classList.add("rise")')
    j = tail.index('"sp-out"')
    assert abs(i - j) < 400, "上升与遮罩消隐不在同一拍"


# ───── ⑪ 放大慢、缩小快 ─────
def test_growth_is_slower_than_the_shrink():
    st = _stops()
    cover = st[_cover_index(st)][0]
    total = _css_ms() / 1000.0
    grow = total * cover / 100.0
    shrink = total * (100 - cover) / 100.0
    assert grow >= 1.8, "放大只用了 %.2fs，用户要更慢" % grow
    assert shrink <= 2.3, "缩小用了 %.2fs，用户要更快" % shrink
    assert grow > shrink, "放大 %.2fs 仍比缩小 %.2fs 短" % (grow, shrink)


# ───── ⑤c 遮罩直到星球落位才打开 ─────
def test_curtain_opens_only_after_the_planet_lands():
    assert "sp-bare" not in CSS and "sp-bare" not in HTML, \
        "中途把遮罩透明化，下面的卡片会提前露出来（用户看图说跳帧）"
    out = _rule("#splash.sp-out")
    assert "opacity: 0" in out, "没有整层淡出"
    assert "transition" in _rule("#splash"), "淡出没有过渡，就是一帧闪掉"
    assert "SPLASH_FADE" in HTML and '"sp-out"' in HTML
    # 淡出必须排在落位之后（同一个 5s 定时器里），不能提前
    tail = HTML[HTML.index("SPLASH_MS"):]
    assert tail.index("SPLASH_MS") < tail.index('"sp-out"')


def test_handoff_swaps_without_a_blink():
    """淡出期间页眉那颗球必须已经"瞬间"在位，否则先消失再淡入＝闪一下。"""
    i = HTML.index('"sp-out"')
    near = HTML[max(0, i - 500):i + 200]
    assert "transition = \"none\"" in near, "交棒前没关掉 opacity 过渡，球会闪一下"
    assert "classList.add(\"ready\")" in near, "交棒前没把页眉球显示出来"


# ───── ⑥ 时间只有一个真源 ─────
def test_js_timeline_reads_the_css_duration():
    ms = _css_ms()
    m = re.search(r"SPLASH_MS\s*=\s*(\d+)", HTML)
    assert m, "JS 里的总时长没抽成常量，改 CSS 会与 JS 失步"
    assert int(m.group(1)) == ms, "CSS %dms / JS %sms 不同源" % (ms, m.group(1))
    assert "SPLASH_FADE" in HTML, "淡出时长没抽成常量"
    assert "}, 1250)" not in HTML and "}, 2900)" not in HTML, "还有写死的分段时长"


# ───── ⑧ 停了动画也得把人摆回画面里 ─────
def test_reduced_motion_still_parks_the_planet_on_screen():
    i = CSS.rindex("@media (prefers-reduced-motion: reduce)")
    assert CSS.count("@media (prefers-reduced-motion: reduce)") == 1, \
        "reduced-motion 块只许有一个（多个会让 css_block() 取错）"
    blk = CSS[i:]
    # 这条规则现在是一组选择器（.planet、.sp-hero、左下角设置星球），
    # 断言改成"这一组里有 planet 且整条是 animation:none"，不再钉死字面量
    assert re.search(r"\.planet[^{]*\{[^}]*animation:\s*none", blk), "开屏没在 reduced-motion 里停"
    m = re.search(r"\.sp-hero \{[^}]*transform:\s*translate\(([\d.]+)vw,\s*([\d.]+)vh\)", blk)
    assert m, "停动画后没把球摆回画面：负 margin 会把它甩到左上角外面"
    x, y = float(m.group(1)), float(m.group(2))
    assert 20 <= x <= 80 and 20 <= y <= 80, "兜底位置 (%gvw, %gvh) 不在画面中间一带" % (x, y)


# ───── ⑦ 浏览器真解析成什么样 ─────
@has_chrome
def test_gate_browser_sees_a_long_transform_only_journey(tmp_path):
    # file:// 下读不了外链样式表的 cssRules，改问 Web Animations：
    # 它给的是浏览器真正拿来跑的那一份关键帧。
    body = """
      const d = document.createElement('div');
      d.className = 'planet sp-hero';
      document.body.appendChild(d);
      const cs = getComputedStyle(d);
      out.anim = cs.animationDuration;
      out.timing = cs.animationTimingFunction;
      out.will = cs.willChange;
      const a = (d.getAnimations ? d.getAnimations() : [])
        .filter(function (x) { return x.animationName === 'planet-journey'; })[0];
      const kfs = a ? a.effect.getKeyframes() : [];
      out.stops = kfs.length;
      out.text = kfs.map(function (k) {
        return (k.computedOffset * 100).toFixed(1) + ':' + (k.transform || '-');
      }).join(' | ');
      out.props = kfs.map(function (k) { return Object.keys(k).join(','); }).join(' | ');
      // 布局位置必须是死的：等 900ms 再看，transform 变了而 offset 没变才算"只动合成器"
      const ol0 = d.offsetLeft, ot0 = d.offsetTop;
      await new Promise(function (r) { setTimeout(r, 900); });
      out.moved = d.offsetLeft !== ol0 || d.offsetTop !== ot0;
      out.mid = getComputedStyle(d).transform;
      d.remove();
    """
    got = _probe(tmp_path, "splashms", "dashboard", body)
    assert got.get("stops", -1) >= 14, "浏览器只拿到 %s 帧" % got.get("stops")
    assert "left" not in got["props"] and "top" not in got["props"], \
        "浏览器里确实在动 left/top：%s" % got["props"][:200]
    assert "5s" in got["anim"], "算出来的时长是 %s" % got["anim"]
    assert got["timing"].split(",")[0].strip() == "linear", got["timing"]
    assert got["will"] == "transform", "will-change 没生效：%s" % got["will"]
    assert "scale(26)" in got["text"], "盖满那一帧不见了：%s" % got["text"][:300]
    assert got["moved"] is False, "跑了 900ms 布局位置变了，说明还在动 left/top"
    assert got["mid"] not in ("none", ""), "900ms 时没有 transform，动画没跑"


def _probe(tmp_path, name, page, body):
    html = shell.build_shot_html(page, _bundle(), theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='spProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},300);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_sp_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocsp",
                            "--virtual-time-budget=6000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="spProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        got = json.loads(m.group(1).replace("&quot;", '"'))
        assert "error" not in got, got
        return got
    finally:
        if os.path.exists(f):
            os.remove(f)


def _bundle():
    b = {n: shell.api.call(n) for n in (
        "dashboard", "server_page", "log_page", "models_page", "flags_page",
        "audit_page", "accounts_page", "registration_page", "identity_page",
        "device_actions_page", "overview_page")}
    for k, v in shell.FIXTURE.items():
        b[k] = {"ok": True, "data": v}
    return b


# ───── ⑫ 开屏之前不许先闪一下白 ─────
def test_window_is_painted_in_theme_colour_not_white():
    """pywebview 的 background_color 默认值是 #FFFFFF：页面画出第一帧之前先闪一下白，
    开屏动画再漂亮也盖不住。这里钉住"底色跟主题走、且不是白"。"""
    m = re.search(r"background_color=[^\n]*", APP)
    assert m, "create_window 没传 background_color，WebView2 会先闪白"
    line = m.group(0).lower()
    assert "ffffff" not in line and '"#fff"' not in line, "底色还是白的：%s" % m.group(0)
    assert "#131110" in line and "#f4f0e6" in line, "底色没跟当前主题走：%s" % m.group(0)
