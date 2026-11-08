"""W5b 主题切换入口：墨纸 ↔ 纸色。

三条线都要钉住：
① 偏好只写新壳自己的桌面状态文件，**server_config.json 一个字节都不许动**（W0 红线）；
② 启动优先级 命令行 > 新壳记的 > 旧壳配置；
③ 点一下立刻换皮（反馈长在动作发生的地方），记不住就用按钮自己的虚线说话。
"""
from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

import console_api as api
import console_app as shell
import console_desktop as cd
import console_core as sc  # W6-A：桩/断言目标改为共享真源（纯标准库）

CHROME = r"C:/Program Files/Google/Chrome/Application/chrome.exe"
has_chrome = pytest.mark.skipif(not os.path.exists(CHROME), reason="这台机器没有 Chrome")
HERE = os.path.dirname(os.path.abspath(shell.__file__))
WEB = os.path.join(HERE, "web")
CSS = open(os.path.join(WEB, "d2.css"), encoding="utf-8").read()
HTML = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """状态文件与旧壳配置都改指临时目录：测试绝不碰他机器上的真偏好。"""
    state = tmp_path / "console_web.json"
    monkeypatch.setenv("AMBRACE_CONSOLE_STATE", str(state))
    cfg = tmp_path / "server_config.json"
    cfg.write_text(json.dumps({"controller_theme": "aurora", "watchdog_interval_sec": 45},
                              ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG", str(cfg))
    monkeypatch.setattr(shell, "CONFIG", str(cfg))
    return state, cfg


# ─────────────────────────── ① 写哪儿 ───────────────────────────

def test_set_theme_writes_only_the_desktop_state_file(sandbox):
    state, cfg = sandbox
    before = cfg.read_bytes()
    got = api.set_theme("light")
    assert got["ok"] is True, got
    assert got["data"]["theme"] == "light" and got["data"]["shown"] == "纸色", got
    assert json.loads(state.read_text(encoding="utf-8"))["theme"] == "light"
    assert cfg.read_bytes() == before, "server_config.json 被写了：旧壳的主题会被我替用户改掉"
    assert "backend" not in got["data"]["path"].lower(), got["data"]


def test_set_theme_rejects_a_third_palette(sandbox):
    state, _cfg = sandbox
    for bad in ("aurora", "", None, "DARK", "light "):
        got = api.set_theme(bad)
        assert got["ok"] is False, "%r 竟然收下了：%s" % (bad, got)
        assert got["code"] == -1 and "两档" in got["error"], got
    assert "theme" not in json.loads(state.read_text(encoding="utf-8")) if state.exists() else True


def test_state_file_keeps_the_geometry_when_theme_changes(sandbox):
    """读-合-写：记主题不能把窗口几何与句柄一起冲掉（那是 W5 刚交付的家当）。"""
    state, _cfg = sandbox
    cd.save_state(geometry={"x": 10, "y": 20, "width": 1360, "height": 880}, hwnd=999, pid=7)
    api.set_theme("light")
    d = json.loads(state.read_text(encoding="utf-8"))
    assert d["theme"] == "light" and d["hwnd"] == 999 and d["geometry"]["width"] == 1360, d


def test_get_theme_reports_what_is_remembered(sandbox):
    state, _cfg = sandbox
    got = api.get_theme()
    assert got["ok"] and got["data"]["theme"] == "" and got["data"]["shown"] == "未记住", got
    api.set_theme("dark")
    assert api.get_theme()["data"]["theme"] == "dark"
    state.write_text(json.dumps({"theme": "sepia"}), encoding="utf-8")
    assert api.get_theme()["data"]["theme"] == "", "认不得的值要当没记过"


def test_call_reaches_set_theme_through_the_whitelist(sandbox):
    assert api.call("set_theme", "light")["ok"] is True
    assert api.call("write_server_config", "dark")["ok"] is False, "白名单外的名字不许被点"


# ─────────────────────────── ② 启动读哪个 ───────────────────────────

def test_preferred_theme_priority_state_over_old_config(sandbox, monkeypatch):
    state, cfg = sandbox
    cfg.write_text(json.dumps({"controller_theme": "light"}), encoding="utf-8")
    assert shell.preferred_theme() == "light", "新壳没记过时要回落到旧壳的配置"
    cd.save_state(theme="dark")
    assert shell.preferred_theme() == "dark", "新壳记的必须压过旧壳配置"
    state.write_text(json.dumps({"theme": "乱码"}), encoding="utf-8")
    assert shell.preferred_theme() == "light", "认不得的值不能带进启动，要退回上一档来源"


def test_cli_theme_beats_the_remembered_one(sandbox, monkeypatch):
    seen = {}
    monkeypatch.setattr(shell, "shot", lambda out, theme, *a, **k: seen.update(t=theme) or 0)
    cd.save_state(theme="light")
    monkeypatch.setattr(shell.sys, "argv", ["console_app.py", "--theme", "dark",
                                            "--shot", "x.png"])
    shell.main()
    assert seen["t"] == "dark", "命令行必须压过状态文件"


# ─────────────────────────── ③ 界面上的入口 ───────────────────────────

def test_theme_planet_sits_in_the_top_right_corner():
    # 11-07 返工：左下角的方形图标按钮退役，改成页眉右上角那颗会形变的星球
    hero = HTML[HTML.index('<div class="hero">'):HTML.index('<div class="body"')]
    assert 'id="themePlanet"' in hero and 'class="planet hero-planet"' in hero, \
        "主题星球没放在页眉右上角"
    assert 'aria-label="切换主题"' in hero
    assert '<div class="pl-shade"></div>' in hero, "星球没有球面光照层"
    assert "build_planet.py" in CSS or "planet_moon.png" in CSS, "星球没用仿真表面纹理"
    assert 'class="tbtn"' not in HTML and 'id="tbtn"' not in HTML, "左下角旧按钮没删干净"
    assert 'getElementById("themePlanet")' in HTML, "按钮没挂上点击"
    assert 'id="splash"' in HTML and "bootSplash" in HTML, "首次开屏没接上"


def test_planet_morphs_moon_to_sun_by_palette():
    assert 'html[data-theme="light"] .planet { background-image: url("planet_sun.png");' in CSS, \
        "纸色档没换成太阳纹理"
    assert "planet_moon.png" in CSS and "@keyframes planet-spin" in CSS, \
        "墨纸档月亮纹理与自转动效没写"
    assert "@keyframes planet-journey" in CSS, "开屏没有弧线收束轨迹"
    assert "sp-out" in CSS and "sp.remove()" in HTML, \
        "落位后没有整层淡出、只剩星球"


def test_failure_state_does_not_burn_an_accent():
    bad = re.search(r"\.hero-planet\.bad \{([^}]*)\}", CSS)
    assert bad, "记不住主题时星球没有失败态"
    assert "dashed" in bad.group(1), bad.group(1)


def test_theme_word_no_longer_duplicated_in_the_foot():
    """图标已经说了状态，页脚再写一遍「· 墨纸」就是两处说同一件事。"""
    assert 'foot + " · " +' not in HTML
    # 左下角那行「端口 N」用户要求去掉，只留设置星球；这里钉住"没有再写出任何页脚文案"
    assert 'id="sideFoot"' not in HTML, "左下角那行端口文案还挂在页面上"
    assert HTML.count("syncThemeBtn()") >= 3, "换档后文案要跟着走"


def test_switching_applies_before_the_write_is_confirmed():
    body = HTML[HTML.index("async function toggleTheme"):]
    body = body[:body.index("\nconst ADMIN_PAGES")]
    assert body.index("dataset.theme = next") < body.index('await api("set_theme"'), \
        "先等壳层写完再换皮：点一下要等一个来回才变色"


PROBE = """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const b = document.getElementById('themePlanet');
    const face = document.querySelector('.hero-planet');
    const tex = () => getComputedStyle(face).backgroundImage;
    const snap = () => ({theme: document.documentElement.dataset.theme,
                         moon: tex().indexOf('planet_moon') >= 0 ? '1' : '0',
                         sun: tex().indexOf('planet_sun') >= 0 ? '1' : '0',
                         sky: getComputedStyle(document.getElementById('sky')).display});
    const before = snap();
    b.click();
    const mid = document.documentElement.dataset.theme;   // 还没等壳层回话
    await new Promise(r => setTimeout(r, 500));
    const after = snap();
    const d = document.createElement('div');
    d.id = 'themeProbe';
    d.textContent = JSON.stringify({before: before, mid: mid, after: after,
                                    bad: b.classList.contains('bad'), title: b.title});
    document.body.appendChild(d);
  }, 250);
});
</script>
</body>"""


def theme_probe(tmp_path, extra=None, theme="dark"):
    bundle = {"dashboard": api.call("dashboard")}
    if extra is not None:
        bundle["set_theme"] = extra
    html = shell.build_shot_html("dashboard", bundle, theme=theme).replace("</body>", PROBE, 1)
    f = os.path.join(WEB, "_theme_probe.html")
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocgate",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
    finally:
        os.remove(f)
    assert r.returncode == 0, r.stderr[-300:]
    m = re.search(r'id="themeProbe">(.*?)</div>', r.stdout, re.S)
    assert m, "探针没跑起来（页面没渲染？）"
    return json.loads(m.group(1))


@has_chrome
def test_click_flips_the_palette_even_when_the_shell_cannot_record(tmp_path):
    """没有壳层回话（出图模式没这页数据）也要立刻换皮，并把「没记住」说在按钮上。"""
    got = theme_probe(tmp_path, extra=None, theme="dark")
    assert got["before"]["theme"] == "dark"
    assert float(got["before"]["moon"]) > .5 and float(got["before"]["sun"]) < .1, \
        "墨纸档没用月亮纹理 %s" % got["before"]
    assert float(got["before"]["moon"]) > .5 and float(got["before"]["sun"]) < .1, \
        "墨纸档没用月亮纹理 %s" % got["before"]
    assert 'html[data-theme="light"] .planet { background-image: url("planet_sun.png");' in CSS
    assert "planet_moon.png" in CSS
    assert got["before"]["sky"] == "block", "墨纸档星空要在"
    assert got["mid"] == "light", "点下去没立刻换皮"
    assert got["after"]["theme"] == "light" and got["bad"] is True, got
    assert float(got["after"]["sun"]) > .9 and float(got["after"]["moon"]) < .1, \
        "切到纸色后没换成太阳纹理 %s" % got["after"]
    assert got["after"]["sky"] == "none", "切到纸色后星空还跟着，违背拍板第③项"
    assert "没记住" in got["title"], got["title"]


@has_chrome
def test_click_records_when_the_shell_answers(tmp_path):
    got = theme_probe(tmp_path, extra={"ok": True, "data": {"theme": "light"}}, theme="dark")
    assert got["after"]["theme"] == "light" and got["bad"] is False, got
    assert "没记住" not in got["title"], got["title"]
