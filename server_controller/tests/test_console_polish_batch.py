"""第六批：导航重排 / 分组卡 X/Y / 保存=勾（星云→行星→超新星）/ 星环遮挡 /
主题圆形扩散与按压 / 微信卫星 / 底部星空 / 身份页可读性。
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


def _run(tmp_path, name, page, body, bundle, theme="dark"):
    html = shell.build_shot_html(page, bundle, theme=theme)
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='visProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},350);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_v6_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,900", "--temp-profile=ovu",
                            "--virtual-time-budget=5000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="visProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───── ① 导航顺序与改名 ─────
def test_gate_nav_order_and_model_page_rename():
    m = re.search(r"const NAV = \[(.*?)\];", HTML, re.S)
    assert m, "找不到 NAV"
    items = re.findall(r'\["(\w+)", "([^"]+)"\]', m.group(1))
    assert [k for k, _ in items] == ["dashboard", "server", "identity", "models",
                                     "accounts", "flags", "log", "audit"], items
    labels = dict(items)
    assert labels["models"] == "模型管理", "「默认模型」要改名「模型管理」"
    assert "默认模型" not in HTML, "旧名还在别处"
    # 标题表也要跟着改
    assert re.search(r'models: \["模型管理"', HTML)


# ───── ② 分组图标卡右上角＝「开着几个 / 一共几个」 ─────
@has_chrome
def test_gate_group_tiles_show_on_over_total(tmp_path):
    got = _run(tmp_path, "tiles", "flags", """
      const its = Array.from(document.querySelectorAll('.gtile i')).map(e => e.textContent);
      out.n = its.length;
      out.sample = its.slice(0, 4);
      out.bad = its.filter(t => !/^\\d+\\/\\d+$/.test(t));
      out.hasU = Array.from(document.querySelectorAll('.gtile i u')).length;
    """, {"flags_page": {"ok": True, "data": shell.FIXTURE["flags_page"]},
          "device_actions_page": {"ok": True, "data": shell.FIXTURE["device_actions_page"]}})
    assert got.get("n"), "分组卡没渲染"
    assert not got.get("bad"), "有卡片不是「开着/总数」口径：%s" % got.get("bad")
    assert got.get("hasU") == got.get("n"), "斜杠不是独立元素（样式会带下划线）"


# ───── ③ 保存＝勾，点下去有整套形变 ─────
def test_gate_no_text_save_buttons_left():
    assert ">保存<" not in HTML, "还有文字「保存」按钮"
    assert HTML.count("saveBtn(") >= 6, "保存按钮没统一成图标"
    for act in ("save-target", "server-limit-save", "mod-save", "flag-save",
                "reg-save", "set-save"):
        assert act in HTML, "丢了保存动作 %s" % act


def test_gate_save_button_has_the_three_stages():
    blk = re.search(r"const SAVE_ICON = ([\s\S]*?);\n", HTML)
    assert blk, "没有保存图标的结构定义"
    body = blk.group(1)
    for part in ("sv-plane", "sv-dust", "sv-orb", "sv-flash", "sv-tick3d"):
        assert part in body, "保存图标缺 %s 这一段" % part
    for kf in ("sv-plane", "sv-dust", "sv-orb", "sv-flash", "sv-tick3d"):
        assert "@keyframes " + kf in CSS, "缺 %s 的动画" % kf
    # 时序：行星先成形（58% 左右）再爆闪，勾最后弹起（60% 之后）
    assert re.search(r"\.savebtn\.saving \.sv-tick3d \{", CSS)


@has_chrome
def test_gate_save_button_animates_then_returns(tmp_path):
    got = _run(tmp_path, "save", "models", """
      const b = document.querySelector('#body .savebtn');
      out.has = !!b;
      out.flat = getComputedStyle(b.querySelector('.sv-plane')).opacity;
      b.click();
      await new Promise(r => setTimeout(r, 80));
      out.saving = b.classList.contains('saving');
      out.orbAnim = getComputedStyle(b.querySelector('.sv-orb')).animationName;
      await new Promise(r => setTimeout(r, 1700));
      out.after = b.classList.contains('saving');
      out.flat2 = getComputedStyle(b.querySelector('.sv-plane')).opacity;
    """, {"models_page": {"ok": True, "data": shell.FIXTURE["models_page"]}})
    assert got.get("has"), "模型页没有保存按钮"
    assert float(got.get("flat") or 0) > .5, "默认不是二维勾：%s" % got
    assert got.get("saving"), "点下去没进入形变：%s" % got
    assert got.get("orbAnim") == "sv-orb", "行星那一段没跑：%s" % got.get("orbAnim")
    assert not got.get("after"), "演完没回位：%s" % got


# ───── ④ 星环不许整圈压在球脸上 ─────
def test_gate_ring_is_occluded_by_the_planet():
    assert "rb-back" in HTML and "rb-front" in HTML, "星环没拆前后两截"
    back = re.search(r"\.rbtn \.rb-back \{([^}]*)\}", CSS)
    front = re.search(r"\.rbtn \.rb-front \{([^}]*)\}", CSS)
    ball = re.search(r"\.rbtn \.rb-ball \{([^}]*)\}", CSS)
    assert back and front and ball, "缺 rb-back / rb-front / rb-ball 的样式"
    zb = re.search(r"z-index:\s*(\d+)", ball.group(1))
    assert zb and int(zb.group(1)) > 0, "球没有压在后半环之上"
    assert "clip-path" in front.group(1), "前半环没有裁掉压在球外的那半"


# ───── ⑤ 主题：圆形扩散 + 按压 ─────
def test_gate_theme_flip_is_a_circular_reveal():
    assert "startViewTransition" in HTML, "换档没用 View Transitions"
    assert "clipPath" in HTML and "circle(0px at" in HTML, "没有从球心扩开的圆形"
    assert "::view-transition-old(root)" in CSS, "旧快照没关掉默认交叉淡入"
    body = HTML[HTML.index("async function toggleTheme"):]
    body = body[:body.index("\nconst ADMIN_PAGES")]
    # 兜底：不管过渡跑不跑得起来，主题都必须落地
    assert "setTimeout(applyOnce" in body, "过渡失败时主题会卡住"
    assert body.index("dataset.theme = next") < body.index('api("set_theme"'), \
        "主题得先落地再发请求"


def test_gate_theme_planet_presses_in():
    assert re.search(r"\.hero-planet:active \{[^}]*scale\(\.9", CSS, re.S), "点星球没有按压感"


# ───── ⑥ 微信卫星 + ⑦ 底部星空 ─────
def test_gate_wechat_satellite_is_the_channel_entry():
    m = re.search(r'<button class="wx-orb"[^>]*data-act="channel-login"', HTML, re.S)
    assert m, "左下角没有微信卫星入口"
    assert "@keyframes wx-float" in CSS and re.search(r"\.wx-orb \{[^}]*animation: wx-float", CSS, re.S), \
        "卫星没有上下浮动"
    assert '.wx-orb svg' in CSS, "卫星没画图标"


def test_gate_stardust_sits_under_the_planet():
    assert 'class="side-sky"' in HTML, "左下角没有星空层"
    assert re.search(r"html\[data-theme=\"dark\"\] \.side-sky \{ display: block; \}", CSS), \
        "星空只该在墨纸档出现"
    assert "@keyframes twinkle" in CSS, "星星不闪"
    zs = re.search(r"\.side-sky \{[^}]*z-index:\s*(\d+)", CSS)
    zp = re.search(r"\.set-orb \{[^}]*z-index:\s*(\d+)", CSS, re.S)
    assert zs and zp and int(zp.group(1)) > int(zs.group(1)), "球要压在星空前面"


# ───── ⑧ 身份页可读性 ─────
@has_chrome
def test_gate_identity_has_lead_lines_and_plain_titles(tmp_path):
    got = _run(tmp_path, "ident", "identity", """
      out.leads = document.querySelectorAll('.mod .lead').length;
      out.h2 = Array.from(document.querySelectorAll('.mod .sec h2')).map(e => e.textContent);
      out.body = document.getElementById('body').textContent;
      out.acts = Array.from(document.querySelectorAll('#body [data-act]')).map(e => e.dataset.act);
    """, {"identity_page": {"ok": True, "data": shell.FIXTURE["identity_page"]},
          "registration_page": {"ok": True, "data": shell.FIXTURE["registration_page"]}})
    assert got.get("leads", 0) >= 3, "每块都该有一句人话开场：%s" % got.get("leads")
    h2 = got.get("h2") or []
    assert not [t for t in h2 if "identity_enforce_mode" in t], \
        "小标题还糊着实现细节：%s" % h2
    assert "identity_enforce_mode" in HTML, "实现细节挪进 title 之后不许丢"
    for act in ("id-copy", "id-code", "id-rotate", "reg-save"):
        assert act in got.get("acts", []), "可读性重排后丢了动作 %s" % act
    # 渠道登录入口按用户要求从页内移除了，只剩左下角那颗卫星
    assert "channel-login" not in got.get("acts", []), "页内的渠道登录块应当已经移除"
    assert 'class="wx-orb"' in HTML and 'data-act="channel-login"' in HTML, "卫星入口没了"


# ───── ⑨ 纸色档（2026-11-08 晚）：太阳 / 白云 / 橙叶 / 金花 / 花苞开花 / 草坡 ─────
def test_gate_light_theme_is_a_day_scene_not_a_night_one():
    assert re.search(r'html\[data-theme="light"\] \.hero \.neb \{ display: block; \}', CSS), \
        "纸色档的云层没打开（.neb 默认 display:none，不打开就一条云规则都不生效）"
    assert re.search(r'html\[data-theme="light"\] \.hero \.neb canvas \{ display: none', CSS), \
        "纸色档还留着星空画布"
    assert re.search(r'html\[data-theme="light"\] \.hero \.neb \.mote \{[^}]*border-radius:\s*\d+% 0 \d+% 0',
                     CSS, re.S), "星点没换成叶子形状（对角圆角）"
    assert re.search(r'html\[data-theme="light"\] \.hero-planet::after \{[^}]*clip-path: path\(',
                     CSS, re.S), "太阳没有三角光芒"
    assert re.search(r'html\[data-theme="light"\] \.hero-planet \{ overflow: visible', CSS), \
        "太阳的瓣会被圆裁掉"
    assert "@keyframes sun-turn" in CSS and "sun-turn 70s" in CSS, "太阳不转"


def test_gate_light_theme_flower_shapes():
    # 开关：关=花苞（卵形）、开=开花（五瓣）
    assert re.search(r'html\[data-theme="light"\] \.sw i \.sw-face::after \{[^}]*62% 62% 38% 38%',
                     CSS, re.S), "开关关着的时候不是花苞"
    assert re.search(r'html\[data-theme="light"\] \.sw\.on i \.sw-face::before \{ opacity: 1',
                     CSS), "开关打开的时候不开花"
    # 滚条：金色五瓣花
    assert re.search(r'html\[data-theme="light"\] \.psb-core::before \{[^}]*box-shadow',
                     CSS, re.S), "滚条不是花"
    # 刷新/保存：开花
    assert "@keyframes bloom-petals" in CSS, "刷新没有开花"
    assert "@keyframes sv-bloom" in CSS, "保存的超新星没换成开花"


def test_gate_light_theme_grass_and_trees():
    # 左下角现在是"风滚草"（一团缠绕的草茎），不再是实心草坡
    assert re.search(r'html\[data-theme="light"\] \.set-orb b \{[^}]*conic-gradient', CSS, re.S), \
        "左下角不是缠绕的草茎"
    assert re.search(r'html\[data-theme="light"\] \.set-orb \{[^}]*animation: tumble-roll', CSS, re.S), \
        "风滚草不转"
    assert 'class="side-grove"' in HTML, "没有远树层"
    assert re.search(r'html\[data-theme="light"\] \.side-grove \{ display: block; \}', CSS), \
        "远树只在纸色档显示"
    # 树要有树干和树枝，不能只是一团树冠
    grove = re.search(r"\.side-grove i \{[^}]*\}", CSS, re.S)
    assert grove, "没有树冠规则"
    assert re.search(r"\.side-grove i::before \{[^}]*height: \d+px", CSS, re.S), "树没有树干"
    assert re.search(r"\.side-grove i::after \{[^}]*gradient\(", CSS, re.S), "树没有树枝"


@has_chrome
def test_gate_heatmap_inverts_between_themes(tmp_path):
    """墨纸档少＝暗、多＝亮；纸色档反着来：少＝浅、多＝深。"""
    heat = {"ok": True, "data": {"kpi": {}, "memStats": {}, "heatPeak": 100,
            "heat": [{"date": "2026-11-%02d" % (i + 1), "tokens": v, "future": False, "tasks": {}}
                     for i, v in enumerate([1, 50, 100, 5, 80, 20, 0])]}}
    body = """
      const fills = Array.from(document.querySelectorAll('rect[data-hm]'))
        .map(r => r.getAttribute('fill')).filter(f => f && f.indexOf('hsl') === 0);
      out.sample = fills.slice(0, 3);
    """
    import re as _re

    def lum(f):
        m = _re.search(r",(\d+)%,\s*(\d+)%\)", f)
        return int(m.group(2)) if m else -1

    light_got = _run(tmp_path, "heat_l", "dashboard", body, {"dashboard": heat}, theme="light")
    dark_got = _run(tmp_path, "heat_d", "dashboard", body, {"dashboard": heat}, theme="dark")
    ls, ds = light_got.get("sample") or [], dark_got.get("sample") or []
    assert len(ls) == 3 and len(ds) == 3, (ls, ds)
    # 纸色档：少（第 1 格）＝浅，多（第 3 格）＝深
    assert lum(ls[0]) > lum(ls[2]), "纸色档没反过来（少该浅、多该深）：%s" % ls
    # 墨纸档保持原样：少＝暗、多＝亮
    assert lum(ds[0]) < lum(ds[2]), "墨纸档被一起反了：%s" % ds


# ───── ⑩ 金色系列 + 开花的位置与不重叠 ─────
@has_chrome
def test_gate_bloom_is_centered_on_the_button(tmp_path):
    """「保存」的开花必须长在按钮正中，不能飘到左上角。"""
    got = _run(tmp_path, "bloompos", "models", """
      const b = document.querySelector('#body .savebtn');
      const br = b.getBoundingClientRect();
      b.click();
      await new Promise(r => setTimeout(r, 640));    // 开花那一段
      const f = b.querySelector('.sv-flash');
      const fr = f.getBoundingClientRect();
      out.dx = Math.abs(fr.left + fr.width / 2 - (br.left + br.width / 2));
      out.dy = Math.abs(fr.top + fr.height / 2 - (br.top + br.height / 2));
      out.op = getComputedStyle(f).opacity;
      out.anim = getComputedStyle(f).animationName;
    """, {"models_page": {"ok": True, "data": shell.FIXTURE["models_page"]}}, theme="light")
    assert got.get("anim") == "sv-bloom", "开花用的不是花形动画：%s" % got
    assert float(got.get("dx", 99)) <= 2.5 and float(got.get("dy", 99)) <= 2.5, \
        "开花的中心偏离按钮中心：%s" % got


@has_chrome
def test_gate_refresh_bud_shrinks_as_the_flower_opens(tmp_path):
    """刷新：花苞要缩成花心，花瓣从中心绽开，而不是"瓣压在花苞上"。"""
    got = _run(tmp_path, "bud", "accounts", """
      const b = document.querySelector('.rbtn');
      b.click();
      await new Promise(r => setTimeout(r, 420));
      const ball = b.querySelector('.rb-ball');
      out.scale = getComputedStyle(ball).transform;       // 应明显小于 1
      out.anim = getComputedStyle(ball).animationName;
      out.petalsAnim = getComputedStyle(ball, '::before').animationName;
      out.petalsOpacity = getComputedStyle(ball, '::before').opacity;
    """, {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}, theme="light")
    assert got.get("anim") == "bud-shrink", "花苞没有在收缩：%s" % got.get("anim")
    assert got.get("petalsAnim") == "bloom-petals", "花瓣没有展开：%s" % got.get("petalsAnim")


def test_gate_light_decor_is_a_gold_family_not_one_flat_color():
    """"金色系列"是一组色阶，不是把整片换成一个金。至少要有五个金色变量，而且要用上。"""
    for v in ("--g900", "--g700", "--g500", "--g400", "--g300", "--g200", "--g100"):
        assert re.search(re.escape(v) + r":\s*#[0-9a-f]{6}", CSS), "缺 %s" % v
    assert CSS.count("var(--g400)") >= 6, "装饰色没用上金色系列"
    # 环图的六档在纸色档也换成金色
    assert re.search(r'html\[data-theme="light"\]\s*\{[^}]*--ring1:\s*#8a5f14', CSS, re.S), \
        "纸色档环图还是砖红"


# ───── ⑩ 金色系列 + 开花的位置与不重叠 ─────
@has_chrome
def test_gate_bloom_is_centered_on_the_button(tmp_path):
    """「保存」的开花必须长在按钮正中，不能飘到左上角。"""
    got = _run(tmp_path, "bloompos", "models", """
      const b = document.querySelector('#body .savebtn');
      const br = b.getBoundingClientRect();
      b.click();
      await new Promise(r => setTimeout(r, 640));    // 开花那一段
      const f = b.querySelector('.sv-flash');
      const fr = f.getBoundingClientRect();
      out.dx = Math.abs(fr.left + fr.width / 2 - (br.left + br.width / 2));
      out.dy = Math.abs(fr.top + fr.height / 2 - (br.top + br.height / 2));
      out.op = getComputedStyle(f).opacity;
      out.anim = getComputedStyle(f).animationName;
    """, {"models_page": {"ok": True, "data": shell.FIXTURE["models_page"]}}, theme="light")
    assert got.get("anim") == "sv-bloom", "开花用的不是花形动画：%s" % got
    assert float(got.get("dx", 99)) <= 2.5 and float(got.get("dy", 99)) <= 2.5, \
        "开花的中心偏离按钮中心：%s" % got


@has_chrome
def test_gate_refresh_bud_shrinks_as_the_flower_opens(tmp_path):
    """刷新：花苞要缩成花心，花瓣从中心绽开，而不是"瓣压在花苞上"。"""
    got = _run(tmp_path, "bud", "accounts", """
      const b = document.querySelector('.rbtn');
      b.click();
      await new Promise(r => setTimeout(r, 420));
      const ball = b.querySelector('.rb-ball');
      out.scale = getComputedStyle(ball).transform;       // 应明显小于 1
      out.anim = getComputedStyle(ball).animationName;
      out.petalsAnim = getComputedStyle(ball, '::before').animationName;
      out.petalsOpacity = getComputedStyle(ball, '::before').opacity;
    """, {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}, theme="light")
    assert got.get("anim") == "bud-shrink", "花苞没有在收缩：%s" % got.get("anim")
    assert got.get("petalsAnim") == "bloom-petals", "花瓣没有展开：%s" % got.get("petalsAnim")


def test_gate_light_decor_is_a_gold_family_not_one_flat_color():
    """"金色系列"是一组色阶，不是把整片换成一个金。至少要有五个金色变量，而且要用上。"""
    for v in ("--g900", "--g700", "--g500", "--g400", "--g300", "--g200", "--g100"):
        assert re.search(re.escape(v) + r":\s*#[0-9a-f]{6}", CSS), "缺 %s" % v
    assert CSS.count("var(--g400)") >= 6, "装饰色没用上金色系列"
    # 环图的六档在纸色档也换成金色
    assert re.search(r'html\[data-theme="light"\]\s*\{[^}]*--ring1:\s*#8a5f14', CSS, re.S), \
        "纸色档环图还是砖红"


# ───── ⑪ 随机草 / 树的下绿上淡 / 落叶对标星点 / 太阳光芒留空隙 ─────
def test_gate_grass_is_a_scatter_not_a_comb():
    """草必须是一把随机的小草：逐片给角度/半径/高矮/粗细，而不是一条梳子。"""
    blades = re.findall(r'<i style="--a:[-\d.]+deg;--r:[-\d.]+px;--h:[-\d.]+px;--w:[-\d.]+px"></i>', HTML)
    assert len(blades) >= 12, "草叶太少（%d 片），不像草" % len(blades)
    angles = set(re.findall(r"--a:([-\d.]+)deg", " ".join(blades)))
    assert len(angles) == len(blades), "角度有重复，还是齐刷刷的"
    assert re.search(r"html\[data-theme=\"light\"\] \.set-orb i \{[^}]*var\(--a", CSS, re.S), \
        "草叶没接上随机角度"
    assert re.search(r"html\[data-theme=\"light\"\] \.set-orb i \{[^}]*translateY\(calc\(-1 \* var\(--r", CSS, re.S), \
        "草叶没按随机半径摆到坡外"
    assert "repeating-conic-gradient" not in CSS.split("/* ② 星点")[0].split("纸色档")[-1], \
        "梳子式草还没删干净"


def test_gate_tree_fades_from_green_at_the_bottom():
    blk = re.search(r"\.side-grove i \{[^}]*\}", CSS, re.S)
    assert blk, "没有树冠规则"
    assert "linear-gradient(to bottom" in blk.group(0), "树冠不是自下而上的渐变"
    body = blk.group(0)
    top = body.index("linear-gradient(to bottom")
    tail = body[top:]
    assert re.search(r"rgba\(96, 106, 58", tail), "树冠顶部不是原来的淡色"
    assert re.search(r"rgba\(1\d\d, 1\d\d, \d\d, \.\d+\)\s*100%", tail), \
        "树冠底部不是绿色（下绿上不变）"


def test_gate_leaves_follow_the_motes():
    """落叶必须用星点那一套：路径、节奏一样，旋转写进 transform 列表里。"""
    def kf(name):
        m = re.search(r"@keyframes " + name + r" \{(.*?)\n\}", CSS, re.S)
        assert m, "没有 @keyframes " + name
        return m.group(1)
    leaf, mote = kf("leaf-drift"), kf("mote-drift")
    # 落叶是**水平向左**飞（用户原话"往左飘"）：横向位移必须明显大于纵向，
    # 不能跟着星点那个斜率走——那看着就是往上飘。
    def end_xy(block):
        ms = re.findall(r"translate3d\((-?[\d.]+)px,\s*(-?[\d.]+)px", block)
        assert ms, "找不到位移"
        return float(ms[-1][0]), float(ms[-1][1])
    lx, ly = end_xy(leaf)
    mx, my = end_xy(mote)
    assert lx < 0 and ly < 0, "落叶应当向左飞：%s" % ((lx, ly),)
    assert abs(lx) > abs(ly) * 2, \
        "落叶不是往左飘（横向 %s 纵向 %s）" % (abs(lx), abs(ly))
    assert mx < 0 and my < 0, "星点应当向左上飘：%s" % ((mx, my),)
    assert "rotate(" in leaf, "叶尖没有转到行进方向"
    # 旋转必须写在 transform 列表里（translate 在前），不能单独用 rotate 属性——
    # 独立 rotate 属性会先于 transform 生效，把移动方向也一起转歪
    assert not re.search(r"\.hero \.neb \.mote \{[^}]*\brotate:", CSS, re.S), \
        "用了独立 rotate 属性，轨迹会被带歪"
    assert re.search(r"translate3d\([^)]*\) rotate\(", leaf), "旋转没跟在位移后面"


def test_gate_sun_rays_are_triangles_with_a_gap():
    """光芒要是 12 根独立三角，并且与盘面之间留一圈空隙。"""
    blk = re.search(r"html\[data-theme=\"light\"\] \.hero-planet::after \{[^}]*\}", CSS, re.S)
    assert blk, "没有光芒规则"
    body = blk.group(0)
    assert "clip-path: path(" in body, "光芒不是用 path() 画的（polygon 会连成实心星形）"
    assert body.count("M") >= 12, "三角瓣不足 12 根"
    # 根部半径 63px > 盘面边缘 55px，中间至少留 6px 空隙
    m = re.search(r"path\(\"M([\d.]+) ([\d.]+)", body)
    assert m, "path() 解析不了"
    import math
    r0 = math.hypot(float(m.group(1)) - 77, float(m.group(2)) - 77)
    assert r0 >= 60, "光芒根部太靠里，会插进盘面（%s）" % r0



# ───── ⑫ 风滚草 / 落叶顺时针 90° / 落叶加快 ─────
def test_gate_tumbleweed_is_a_ball_of_straw():
    """左下角要是一整团看得见的缠绕草茎，而不是薄薄一层。"""
    arcs = re.findall(r'<b style="--w:\d+px;--a:\d+deg;--o:[\d.]+"></b>', HTML)
    assert len(arcs) >= 10, "草茎太少（%d 道），不像一团草" % len(arcs)
    blk = re.search(r"html\[data-theme=\"light\"\] \.set-orb \{[^}]*tumble-roll[^}]*\}", CSS, re.S)
    assert blk, "没有纸色档的风滚草规则"
    # 整团要露出来：bottom 不能还是 -140px（那只有顶上薄薄一层）
    m = re.search(r"bottom:\s*(-?\d+)px", blk.group(0))
    assert m and int(m.group(1)) > -120, "草团还是沉在窗口底下，看不全：%s" % (m and m.group(1))


def test_gate_leaves_turned_90_degrees_clockwise_and_move_faster():
    """落叶：顺时针再转 90°（-73° → 17°），并且要真飞过去、不能原地淡掉。"""
    m = re.search(r"@keyframes leaf-drift \{(.*?)\n\}", CSS, re.S)
    assert m, "没有 leaf-drift"
    body = m.group(1)
    rot = re.search(r"rotate\((-?[\d.]+)deg\)", body)
    assert rot, "落叶没有固定朝向"
    assert 10 <= abs(float(rot.group(1))) <= 25, \
        "落叶朝向应是顺时针转 90° 之后的 17°，现在是 %s" % rot.group(1)
    # 位移要够长：原来只有 42px，看着像原地淡掉
    ms = re.findall(r"translate3d\((-?[\d.]+)px,\s*(-?[\d.]+)px", body)
    assert ms, "找不到落叶的位移"
    total = abs(float(ms[-1][0])) + abs(float(ms[-1][1]))
    assert total >= 150, "落叶飞得太短（%s），还是像原地淡掉" % total
    # 周期要压得住行内写的 18~29s
    assert re.search(r"animation-duration:\s*\d+s !important", CSS), \
        "落叶的周期没压短（行内 style 要 !important 才压得住）"


# ───── ⑫ 风滚草 / 落叶顺时针 90° / 落叶加快 ─────
