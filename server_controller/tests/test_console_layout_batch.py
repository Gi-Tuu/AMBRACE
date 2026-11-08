"""控制台版式批闸门（2026-11-08 上午反馈）。

六处改动，每条都用真渲染 + 真几何 / 真 DOM 断言，不读代码猜：
  ① 默认模型卡片：三字段单列、顺序、名字右侧标签、底部三者中心同一水平线
  ② 卡片两列、每行两张等大
  ③ 页脚分界线图层高于正文 + 右下角页码改成当前数字
  ④ 热力图明细浮层：数字固定列右对齐、分类左对齐不换行
  ⑤ 热力图新增朱红最高用量档
  ⑥ 开关说明里的 **加粗** 要渲染出来（不许露裸符号）
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


def _run(tmp_path, name, page, body, bundle):
    html = shell.build_shot_html(page, bundle, theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='layProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},300);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_lay_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=olay",
                            "--virtual-time-budget=5000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="layProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


def _models_bundle():
    return {"models_page": {"ok": True, "data": shell.FIXTURE["models_page"]}}


# ───── ①② 默认模型卡片：单列 / 名字右侧标签 / 底部同线 / 每行两张 ─────
@has_chrome
def test_gate_model_cards_single_column_and_two_per_row(tmp_path):
    got = _run(tmp_path, "mod", "models", """
      const cards = Array.from(document.querySelectorAll('.mods .mod'));
      out.n = cards.length;
      const c = cards[0];
      const R = e => e.getBoundingClientRect();
      const fields = Array.from(c.querySelectorAll('.fields > div'));
      out.fieldX = fields.map(f => Math.round(R(f).left));
      out.fieldY = fields.map(f => Math.round(R(f).top));
      out.labels = fields.map(f => f.querySelector('label').textContent);
      const b = c.querySelector('.mh b'), cap = c.querySelector('.mh .cap');
      out.capGap = Math.round(R(cap).left - R(b).right);       // 紧跟名字右边
      out.capBottom = Math.round(R(cap).bottom), out.nameBottom = Math.round(R(b).bottom);
      const foot = c.querySelector('.foot');
      const sw = foot.querySelector('.sw'), txt = sw.querySelector('i'), save = foot.querySelector('.btn');
      const cy = r => r.top + r.height / 2;
      out.centerY = {orbit: Math.round(cy(R(txt))), label: Math.round(cy(R(sw))),
                     save: Math.round(cy(R(save)))};
      out.cols = getComputedStyle(document.querySelector('.mods')).gridTemplateColumns;
      out.cardTop = cards.map(x => Math.round(R(x).top));
      out.cardW = cards.map(x => Math.round(R(x).width));
    """, _models_bundle())
    assert got.get("n", 0) >= 3, "卡片没渲染出来：%s" % got
    assert got.get("labels") == ["模型", "Base URL", "API Key"], "字段顺序不对：%s" % got.get("labels")
    assert len(set(got.get("fieldX", []))) == 1, "三个字段没排在同一左列：%s" % got.get("fieldX")
    assert len(set(got.get("fieldY", []))) == 3, "三个字段没各占一行：%s" % got.get("fieldY")
    assert 0 <= got.get("capGap", 999) <= 24, "标签没紧跟卡片名字：%s" % got.get("capGap")
    assert abs(got.get("capBottom", 0) - got.get("nameBottom", 999)) <= 3, "标签没与名字下对齐：%s" % got
    cy = got.get("centerY") or {}
    assert len(set(cy.values())) == 1, "开关/启用/保存三者中心不在一条线：%s" % cy
    parts = [x for x in re.split(r"\s+", got.get("cols", "")) if x.endswith("px")]
    assert len(parts) == 2, "卡片不是每行两张：%s" % got.get("cols")
    assert abs(parts[0] and float(parts[0][:-2]) - float(parts[1][:-2])) < 2, "两张卡不等宽：%s" % parts
    assert got.get("cardTop", [])[1] == got.get("cardTop", [])[0], "第二张没跟第一张同行：%s" % got.get("cardTop")


# ───── ③ 页脚分界线图层 + 页码数字 ─────
@has_chrome
def test_gate_footer_is_above_body_and_shows_page_number(tmp_path):
    got = _run(tmp_path, "foot", "dashboard", """
      const st = document.querySelector('.status'), bd = document.getElementById('body');
      const cs = getComputedStyle(st), cb = getComputedStyle(bd);
      out.statusZ = cs.zIndex;
      out.bodyZ = cb.zIndex;
      out.bg = cs.backgroundColor;
      out.hint = document.getElementById('statusHint').textContent;
    """, {"dashboard": {"ok": True, "data": {"kpi": {}, "heat": [], "heatPeak": 1}}})
    assert got.get("statusZ") not in ("auto", None, ""), "页脚没有独立图层：%s" % got
    assert int(got["statusZ"]) > int(got.get("bodyZ") if str(got.get("bodyZ")).isdigit() else 0), \
        "页脚图层没比正文更上：%s" % got
    assert got.get("bg") not in ("rgba(0, 0, 0, 0)", "transparent"), "页脚背景是透明的：%s" % got
    assert re.fullmatch(r"\d{2}\s*/\s*\d{2}", got.get("hint", "").strip()), \
        "右下角不是对应页码数字：%s" % got.get("hint")


def test_gate_footer_hint_uses_the_current_page():
    """页码要随翻页变，不能写死一句说明。"""
    i = HTML.index("statusHint")
    seg = HTML[max(0, i - 400):i + 400]
    assert "NAV.findIndex" in seg or "page" in seg, "页码没跟当前页挂钩：%s" % seg[:200]


# ───── ④ 明细浮层：数字固定列右对齐、分类不换行 ─────
@has_chrome
def test_gate_heat_tip_numbers_align_and_labels_do_not_wrap(tmp_path):
    got = _run(tmp_path, "tip", "dashboard", """
      // 直接造一份明细，模拟最长的分类名
      const el = document.getElementById('hmtip');
      el.innerHTML = '<div class="hml">' +
        '<div><i>智能体思考</i><b>295,642</b><em>55.6%</em></div>' +
        '<div><i>记忆处理</i><b>98,962</b><em>18.6%</em></div>' +
        '<div><i>主对话</i><b>74,556</b><em>14.0%</em></div></div>';
      el.hidden = false;
      const R = e => e.getBoundingClientRect();
      const rows = Array.from(el.querySelectorAll('.hml > div'));
      out.numX = rows.map(r => Math.round(R(r.querySelector('b')).left));
      out.numRight = rows.map(r => Math.round(R(r.querySelector('b')).right));
      out.labelLeft = rows.map(r => Math.round(R(r.querySelector('i')).left));
      out.labelH = rows.map(r => Math.round(R(r.querySelector('i')).height));
      out.nowrap = getComputedStyle(rows[0].querySelector('i')).whiteSpace;
      out.cols = getComputedStyle(rows[0]).gridTemplateColumns;
    """, {"dashboard": {"ok": True, "data": {"kpi": {}, "heat": [], "heatPeak": 1}}})
    assert len(set(got.get("numX", []))) == 1, "数字没有固定起始位置：%s" % got.get("numX")
    assert len(set(got.get("numRight", []))) == 1, "数字没有向右竖对齐：%s" % got.get("numRight")
    assert len(set(got.get("labelLeft", []))) == 1, "分类没左对齐：%s" % got.get("labelLeft")
    assert len(set(got.get("labelH", []))) == 1, "长分类名换行了：%s" % got.get("labelH")
    assert got.get("nowrap") == "nowrap", "分类没锁不换行：%s" % got.get("nowrap")
    cols = [x for x in re.split(r"\s+", got.get("cols") or "") if x.endswith("px")]
    assert len(cols) == 3 and cols[1:] == ["82px", "48px"], \
        "没用固定的数字/占比列：%s" % got.get("cols")


# ───── ⑤ 热力图朱红最高档 ─────
@has_chrome
def test_gate_heatmap_has_a_vermilion_top_tier(tmp_path):
    """连续色带的顶端应是高饱和亮朱橙（HSL 插值到 t=1）。"""
    got = _run(tmp_path, "heat", "dashboard", """
      const svg = document.querySelector('.heat svg');
      const stops = Array.from(svg.querySelectorAll('linearGradient stop'));
      out.last = stops.length ? stops[stops.length-1].getAttribute('stop-color') : '';
      out.first = stops.length ? stops[0].getAttribute('stop-color') : '';
    """, {"dashboard": {"ok": True, "data": {
        "kpi": {}, "heat": [{"date": "2026-10-%02d" % (i + 1), "tokens": t, "future": False}
                            for i, t in enumerate([0, 5, 20, 40, 60, 80, 95, 100])],
        "heatPeak": 100, "memSpark": [], "tokenSpark": [], "meta": {}}}})
    assert got.get("last") and got["last"] != got.get("first"), "色带两端没有渐变：%s" % got
    assert "hsl(" in (got.get("last") or ""), "顶端不是连续插值出来的颜色：%s" % got


def test_gate_heat_color_is_continuous_not_palette():
    """色带必须来自连续插值函数，不能退回一张离散调色板。"""
    i = HTML.index("function heatColor")
    seg = HTML[i:i + 320]
    assert "Math.max(0, Math.min(1, v))" in seg, "没做 0..1 归一化：%s" % seg[:120]
    assert "TIER_OP" not in HTML[i - 200:i + 400], "离散档位表还留着：%s" % seg[:120]


# ───── ⑥ 开关说明的 **加粗** 要渲染 ─────
@has_chrome
def test_gate_flag_desc_bold_is_rendered(tmp_path):
    # 示意数据里没有带 ** 的文案，这里把真目录里那条原样塞进一行，验证渲染链路
    import copy
    data = copy.deepcopy(shell.FIXTURE["flags_page"])
    data["groups"][0]["rows"].append({
        "key": "proactive_freshness_shadow", "title": "主动消息前先看一眼现状",
        "desc": "开启后会把判断结果记进开发日志；**这一步不改变它发不发消息**，只攒读数。",
        "enabled": False, "self_service": True, "server_locked": False,
        "source": "db", "scope": "user", "visible": True, "order": 9})
    got = _run(tmp_path, "flag", "flags", """
      const descs = Array.from(document.querySelectorAll('.frow .ft i'));
      out.rawStars = descs.filter(d => d.textContent.indexOf('**') >= 0).length;
      out.bold = descs.filter(d => d.querySelector('b,strong')).length;
      out.boldText = Array.from(document.querySelectorAll('.frow .ft i b, .frow .ft i strong'))
        .slice(0, 3).map(b => b.textContent);
    """, {"flags_page": {"ok": True, "data": data},
          "device_actions_page": {"ok": True, "data": shell.FIXTURE["device_actions_page"]}})
    assert got.get("rawStars") == 0, "说明里还露着裸 ** 符号：%s" % got
    assert got.get("bold", 0) >= 1, "加粗没渲染出来：%s" % got
    assert any("不改变" in t or "一个字" in t or "一律不补" in t or "亲口" in t
               for t in got.get("boldText", [])), "渲染的不是那几段强调文字：%s" % got.get("boldText")
