"""11-07 第二轮反馈的行为闸门（② 百分比 / ③ 去状态字 / ⑤ 刷新不闪 / ⑥ 形变与拖尾时机 / ⑦ 分组 / ⑧ 竖线动效）。

第 ⑤⑥ 条同源：paint() 以前每次刷新都整页重建 DOM，导致入场动画重播、
开关元素被换掉让 CSS 过渡无从触发。通解是 setBody（渲染结果没变就不碰 DOM）+
开关只切 class 不重建。这里断言的是跑起来的行为，不是读代码凑字符串。
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

DASH = {
    "kpi": {"server": "运行中", "health": "正常", "vlm": "—", "vlmCap": "",
            "chars": "1", "mems": "2", "tokens": "3"},
    "heat": [{"date": "2026-08-19", "tokens": 3441217, "future": False,
              "tasks": {"主对话": 2000000, "智能体思考": 1000000, "记忆处理": 441217}}],
    "heatPeak": 3441217, "memSpark": [], "tokenSpark": [],
    "meta": {"at": "00:00:00", "next": "", "target": "—", "version": "—"},
}


def _bundle():
    b = {n: shell.api.call(n) for n in (
        "dashboard", "server_page", "log_page", "models_page", "flags_page",
        "audit_page", "accounts_page", "registration_page", "identity_page",
        "device_actions_page", "overview_page")}
    for k, v in shell.FIXTURE.items():
        b[k] = {"ok": True, "data": v}
    b["dashboard"] = {"ok": True, "data": DASH}
    # 出图模式的 api() 直接从 __DATA__ 取信封；给切换动作一个成功回包，
    # 否则产品会按「写后端失败」正确地回滚，测的就不是形变了。
    b["set_flag"] = {"ok": True, "data": {}}
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
    d.id = 'fbProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, name, page, body):
    html = shell.build_shot_html(page, _bundle(), theme="dark")
    html = html.replace("</body>", _probe(body), 1)
    f = os.path.join(WEB, "_fb_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocfb",
                            "--virtual-time-budget=3000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
        m = re.search(r'id="fbProbe">(.*?)</div>', dom, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───────────── ⑤ 刷新不闪：结果没变就一个字节都不碰 DOM ─────────────

def test_gate_refresh_keeps_the_same_dom_nodes():
    assert "function setBody(html)" in HTML
    assert "if (bodySig[page] === html) return false;" in HTML, \
        "没有「渲染结果未变就不碰 DOM」的短路"
    assert re.search(r"\} else \{\s*box\.classList\.remove\(\"enter\"\);", HTML), \
        "刷新时没移除 enter，入场动画仍会重播"


@has_chrome
def test_gate_second_paint_does_not_rebuild_cards(tmp_path):
    got = _run(tmp_path, "repaint", "dashboard", """
      const card = document.querySelector('#body .card');
      const before = card;
      paint(); paint();                       // 模拟两次 20s 定时刷新
      out.same = document.querySelector('#body .card') === before;
      out.enter = document.getElementById('body').classList.contains('enter');
    """)
    assert got.get("same") is True, "刷新把卡片 DOM 重建了，会闪一下 %s" % got
    assert got.get("enter") is False, "刷新还挂着 enter，会重播入场动画 %s" % got


# ───────────── ⑥ 切开关只切 class，形变过渡才跑得起来 ─────────────

@has_chrome
def test_gate_toggle_morphs_in_place_without_rebuild(tmp_path):
    got = _run(tmp_path, "toggle", "flags", """
      const sw = document.querySelector('button.sw[data-act="flag-toggle"]');
      const before = sw;
      const wasOn = sw.classList.contains('on');
      sw.click();
      await new Promise(r => setTimeout(r, 40));
      out.same = document.querySelector('button.sw[data-act="flag-toggle"]') === before;
      out.flipped = sw.classList.contains('on') !== wasOn;
      out.morph = sw.classList.contains('morph');
    """)
    assert got.get("same") is True, "切换开关把 DOM 重建了，transition 不会触发 %s" % got
    assert got.get("flipped") is True, "开关状态没就地翻转 %s" % got
    assert got.get("morph") is True, "切换瞬间没挂上一次性拖尾 %s" % got


def test_gate_tail_only_runs_during_morph():
    assert ".sw.morph .sw-tail { animation: tail-sweep" in CSS
    assert ".sw.on i .sw-tail" not in CSS, "开态还有常驻拖尾"
    assert "@keyframes tail-sweep" in CSS and "@keyframes dust-float" in CSS


# ───────────── ② 明细每行右边有占额百分比 ─────────────

@has_chrome
def test_gate_tooltip_rows_show_share_percent(tmp_path):
    got = _run(tmp_path, "tip", "dashboard", """
      const c = document.querySelector('.heat svg rect[data-hm="0"]');
      c.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX:220, clientY:300}));
      const t = document.getElementById('hmtip');
      out.pcts = Array.from(t.querySelectorAll('.hml em')).map(e => e.textContent);
      out.rows = t.querySelectorAll('.hml > div').length;
    """)
    assert got.get("rows", 0) >= 3 and len(got.get("pcts", [])) == got["rows"], \
        "明细行没都配上百分比 %s" % got
    assert all(re.fullmatch(r"\d+\.\d%", p) for p in got["pcts"]), \
        "百分比格式不对 %s" % got


# ───────────── ⑦ 分组标签条：点一组只看一组 ─────────────

@has_chrome
def test_gate_flag_group_chips_filter_the_list(tmp_path):
    got = _run(tmp_path, "chips", "flags", """
      const chips = Array.from(document.querySelectorAll('.gtiles .gtile'));
      out.chips = chips.map(b => b.textContent.trim());
      out.tiles = chips.length;
      out.groupsBefore = document.querySelectorAll('.grp').length;
      if (chips.length > 1) chips[1].click();
      await new Promise(r => setTimeout(r, 60));
      out.groupsAfter = document.querySelectorAll('.grp').length;
      out.active = Array.from(document.querySelectorAll('.gtiles .gtile.on')).map(b => b.textContent.trim());
    """)
    assert len(got.get("chips", [])) >= 3, "分组图标卡片没渲染 %s" % got
    assert got.get("tiles", 0) >= 3, "没有做成桌面图标式分类卡片 %s" % got
    assert got.get("groupsBefore", 0) >= 2, "默认没铺开全部分组 %s" % got
    assert got.get("groupsAfter") == 1, "点分组后没有只显示那一组 %s" % got
    assert len(got.get("active", [])) == 1, "选中态没切换 %s" % got


# ───────────── ③ 状态字移除 ／ ⑧ 竖线动效 ─────────────

def test_gate_hint_removed_and_hover_has_sweep_animation():
    assert 'id="heroNext"' not in HTML, "被要求移除的那段字还在 DOM 里"
    assert "进页时拉一次" not in HTML, "状态字还在 JS 里赋值"
    assert "@keyframes row-sweep" in CSS and "transform: scaleX(0)" in CSS, \
        "竖线没有左到右展开动效"
    assert "@keyframes dust-float" in CSS, "没有星尘浮现&下沉"
    assert ".frow:hover::before" in CSS and "animation: row-sweep" in CSS
    assert "width: 3px" in CSS.split(".frow::before,", 1)[-1][:200], "竖线没加粗"
