"""11-07 第四轮反馈的闸门：两个 bug + 开屏 + 星球按钮 + 尾迹/流星/星点/分组/排版。

纪律：断言跑起来的行为与纸面约束，每条都要能变红。
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
CORE = open(os.path.join(HERE, "console_core.py"), encoding="utf-8").read()


def _bundle():
    b = {n: shell.api.call(n) for n in (
        "dashboard", "server_page", "log_page", "models_page", "flags_page",
        "audit_page", "accounts_page", "registration_page", "identity_page",
        "device_actions_page", "overview_page")}
    for k, v in shell.FIXTURE.items():
        b[k] = {"ok": True, "data": v}
    return b


def _run(tmp_path, name, page, body):
    html = shell.build_shot_html(page, _bundle(), theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='r4Probe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},300);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_r4_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocr4",
                            "--virtual-time-budget=6000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="r4Probe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───── 严重问题 1：pythonw 下不许弹控制台 ─────
def test_gate_no_console_window_on_probe():
    assert CORE.count("creationflags=NO_WINDOW") >= 3, \
        "ipconfig / tailscale 少了 NO_WINDOW，进服务器控制页会闪终端"


# ───── 严重问题 2：从日志页切走后不许被拉回来 ─────
def test_gate_navigation_is_pinned_to_its_page():
    assert "if (p === page) paint();" in HTML, "失败分支会重绘当前页"
    assert "armedFor !== page" in HTML and "loadPage(armedFor)" in HTML, \
        "定时器没绑页键，旧页刷新会污染新页"
    assert "const target = page;" in HTML and "loadPage(target)" in HTML


@has_chrome
def test_gate_leaving_log_stays_on_the_new_page(tmp_path):
    got = _run(tmp_path, "nav", "log", """
      const btn = document.querySelector('#nav button[data-p="server"]');
      btn.click();
      const mid = {page: page, title: document.getElementById('pageTitle').textContent};
      await new Promise(r => setTimeout(r, 3600));   // 跨过日志页 3s 定时器
      out.mid = mid;
      out.page = page; out.title = document.getElementById('pageTitle').textContent;
      out.hasLog = !!document.querySelector('#body pre.log');
    """)
    assert got.get("mid", {}).get("page") == "server", got
    assert got.get("page") == "server" and got.get("title") == "服务器控制", got
    assert got.get("hasLog") is False, "切走后又变回运行日志了 %s" % got


# ───── 开屏动画 + 右上角星球 ─────
def test_gate_first_run_splash_and_corner_planet_on_paper():
    assert 'id="splash"' in HTML and "bootSplash" in HTML
    assert "ambrace_splash_seen" in HTML, "只有第一次进入才该覆盖"
    assert "window.__DATA__" in HTML and "sp.remove()" in HTML, "出图模式要跳过开屏"
    assert "@keyframes planet-journey" in CSS and "@keyframes planet-spin" in CSS, \
        "开屏弧线轨迹 / 星球自转没写"
    assert HTML.count('class="planet sp-orb') >= 2 and 'class="planet sp-hero"' in HTML, \
        "开场不是星空 + 三个球"
    assert "sp-out" in CSS and ".sp-stars" in CSS, "遮罩/星空层没接上"
    assert "SPLASH_MS" in HTML and "SPLASH_FADE" in HTML, \
        "开屏时长没抽成常量：写死毫秒会跟 CSS 关键帧失步"
    assert 'id="themePlanet"' in HTML and 'class="planet hero-planet"' in HTML
    assert "hero-planet.ready" in CSS, "最终星球没有交棒显示"
    assert re.search(r"\.hero-planet \{[^}]*right: -?\d+px", CSS), "最终星球没塞进窗口右上角"
    assert 'id="tbtn"' not in HTML and 'class="tbtn"' not in HTML
    assert 'html[data-theme="light"] .planet { background-image: url("planet_sun.png");' in CSS, \
        "纸色档星球没换成太阳纹理"
    assert "planet_moon.png" in CSS and "build_planet.py" in CSS, "没接上程序化星球纹理"

@has_chrome
def test_gate_splash_is_skipped_in_shot_mode(tmp_path):
    got = _run(tmp_path, "splash", "dashboard", """
      out.splash = !!document.getElementById('splash');
      out.planet = !!document.getElementById('themePlanet');
    """)
    assert got.get("splash") is False, "出图被开屏遮住了 %s" % got
    assert got.get("planet") is True, "右上角常驻星球不见了 %s" % got


# ───── 尾迹 / 流星 / 常亮星点 / 分组卡片 / 排版 ─────
def test_gate_tail_meteor_and_tiles_paper_contracts():
    assert "height: 20px; width: 0;" in CSS.split(".sw i .sw-tail {", 1)[-1][:160], \
        "尾迹宽度不等于球体直径"
    assert ".sw { --tail: 207, 199, 187; }" in CSS and ".sw.on { --tail: 255, 180, 137; }" in CSS, \
        "月亮/太阳尾迹颜色没分别对应"
    assert HTML.count('class="shoot"') >= 6, "流星数量太少"
    assert "--sw:" in HTML and "translate3d(-340px, 0, 0)" in CSS, \
        "流星大小不随机 / 拖尾方向与轨迹不符"
    assert "keep: Math.random() < 0.12" in HTML and "p.keep ?" in HTML, \
        "变暗时没保留少量常亮星点"
    assert ".gtiles" in CSS and "class=\"gtiles stagger\"" in HTML, "分组没做成图标卡片"
    # 明细数字改成固定列 grid 后仍要向右竖对齐（口径不变，写法换了）
    assert re.search(r"\.hmtip \.hml > div \{[^}]*grid-template-columns:[^}]*82px", CSS, re.S), \
        "明细数字没有固定列右对齐"
    assert "text-align: right" in CSS, "明细数字没右对齐"
    # 侧栏分隔线必须跟页眉底线同高：hero 是 border-box，那 1px 底边算在 --hero-h 里面，
    # 所以侧栏这条线得画在 --hero-h - 1 上（写死 132 就会矮一整像素，用户截图里能看见）
    assert re.search(r"--hero-h:\s*132px", CSS), "页眉高度变量丢了"
    assert re.search(r"\.side::before \{[^}]*top:\s*calc\(var\(--hero-h\)\s*-\s*1px\)", CSS, re.S), \
        "侧栏分隔线没与页眉底线对齐"


# ───── ⑨ 分组图标卡：计数不许压标签，长名不许顶动大字 ─────
@has_chrome
def test_gate_group_tiles_do_not_overlap_or_shift(tmp_path):
    """用户截图：有些字和数字重叠；名字太长把大字顶上去。

    病根：`.gtile i` 用 `bottom: 26px` 绝对定位，正好落在标签那一行；
    标签又没有行数上限——长名换行后整卡变高，`justify-content:center` 把大字顶上去。
    这里用真几何量：计数与标签不许相交、标签区高度一致、卡片高度一致、大字距顶一致。
    """
    got = _run(tmp_path, "tiles", "flags", """
      const tiles = Array.from(document.querySelectorAll('.gtile'));
      // 真后端里就有 9 个字的组名（编纂知识与前瞻意图），直接塞进最后一张卡
      tiles[tiles.length - 1].querySelector('span').textContent = '编纂知识与前瞻意图';
      await new Promise(r => setTimeout(r, 40));   // 无头 + 虚拟时间下 rAF 不一定推进
      const R = e => e.getBoundingClientRect();
      const hit = (a, b) => !(a.right <= b.left || b.right <= a.left ||
                              a.bottom <= b.top || b.bottom <= a.top);
      const sn = tiles.map(t => R(t.querySelector('span')));
      const nu = tiles.map(t => R(t.querySelector('i')));
      const bg = tiles.map(t => R(t.querySelector('b')));
      const tl = tiles.map(t => R(t));
      out.n = tiles.length;
      out.overlap = sn.filter((s, k) => hit(s, nu[k])).length;
      out.labelH = sn.map(r => Math.round(r.height));
      out.tileH = tl.map(r => Math.round(r.height));
      out.bigTop = bg.map((r, k) => Math.round(r.top - tl[k].top));
      out.overlapBig = nu.filter((n, k) => hit(n, bg[k])).length;
    """)
    assert got.get("n", 0) >= 4, "分组卡没渲染出来：%s" % got
    assert got["overlap"] == 0, "计数压在标签上了：%s" % got
    assert got.get("overlapBig") == 0, "计数压在大字上了：%s" % got
    assert len(set(got["labelH"])) == 1, "标签区高度没锁死（长名换行会改高度）：%s" % got["labelH"]
    assert len(set(got["tileH"])) == 1, "长名字把卡片撑高了：%s" % got["tileH"]
    assert len(set(got["bigTop"])) == 1, "大字被顶动了：%s" % got["bigTop"]


# ───── ⑨ 分组图标卡：计数不许压标签，长名不许顶动大字 ─────
@has_chrome
def test_gate_group_tiles_do_not_overlap_or_shift(tmp_path):
    """用户截图：有些字和数字重叠；名字太长把大字顶上去。

    病根：`.gtile i` 用 `bottom: 26px` 绝对定位，正好落在标签那一行；
    标签又没有行数上限——长名换行后整卡变高，`justify-content:center` 把大字顶上去。
    这里用真几何量：计数与标签不许相交、标签区高度一致、卡片高度一致、大字距顶一致。
    """
    got = _run(tmp_path, "tiles", "flags", """
      const tiles = Array.from(document.querySelectorAll('.gtile'));
      // 真后端里就有 9 个字的组名（编纂知识与前瞻意图），直接塞进最后一张卡
      tiles[tiles.length - 1].querySelector('span').textContent = '编纂知识与前瞻意图';
      await new Promise(r => setTimeout(r, 40));   // 无头 + 虚拟时间下 rAF 不一定推进
      const R = e => e.getBoundingClientRect();
      const hit = (a, b) => !(a.right <= b.left || b.right <= a.left ||
                              a.bottom <= b.top || b.bottom <= a.top);
      const sn = tiles.map(t => R(t.querySelector('span')));
      const nu = tiles.map(t => R(t.querySelector('i')));
      const bg = tiles.map(t => R(t.querySelector('b')));
      const tl = tiles.map(t => R(t));
      out.n = tiles.length;
      out.overlap = sn.filter((s, k) => hit(s, nu[k])).length;
      out.labelH = sn.map(r => Math.round(r.height));
      out.tileH = tl.map(r => Math.round(r.height));
      out.bigTop = bg.map((r, k) => Math.round(r.top - tl[k].top));
      out.overlapBig = nu.filter((n, k) => hit(n, bg[k])).length;
    """)
    assert got.get("n", 0) >= 4, "分组卡没渲染出来：%s" % got
    assert got["overlap"] == 0, "计数压在标签上了：%s" % got
    assert got.get("overlapBig") == 0, "计数压在大字上了：%s" % got
    assert len(set(got["labelH"])) == 1, "标签区高度没锁死（长名换行会改高度）：%s" % got["labelH"]
    assert len(set(got["tileH"])) == 1, "长名字把卡片撑高了：%s" % got["tileH"]
    assert len(set(got["bigTop"])) == 1, "大字被顶动了：%s" % got["bigTop"]
