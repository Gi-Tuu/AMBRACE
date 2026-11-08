"""控制台视觉批闸门（2026-11-08 中午 12 图反馈）。

覆盖：星空交界渐隐 / 流星覆盖左侧 / 高亮条统一左侧竖线 / 开关中心与文字边距 /
滚动容器不再硬切文字 / 表格列左对齐+操作列居中 / 删重复文案 /
热力图连续渐变 / 服务器身份 7→3。
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
      const d=document.createElement('div');d.id='visProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},350);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_vis_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ovis",
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


# ───── ① 星空交界渐隐（左缘不再是硬切）─────
def test_gate_hero_left_edge_has_fade():
    i = CSS.index(".hero { position: relative")
    seg = CSS[i:i + 900]
    assert "linear-gradient(to right" in seg and "hero::before" in seg, \
        "页眉左缘没有渐隐遮罩：%s" % seg[:200]


# ───── ② 流星覆盖左侧 ─────
def test_gate_meteors_cover_left_half():
    sx = re.findall(r"--sx:([\d.]+)%", HTML)
    assert len(sx) >= 4, "流星没有各自的水平起点：%s" % sx
    assert sum(1 for x in sx if float(x) < 50) >= 2, "左半边还是没有流星：%s" % sx
    assert "left: var(--sx" in CSS, "流星起点没接到 CSS 变量"


# ───── ③ 高亮条统一：不再有整行深色 hover 背景 ─────
def test_gate_highlight_is_only_left_rule():
    assert not re.search(r"table\.tbl tr:hover td\s*\{[^}]*background", CSS, re.S), \
        "整行深色高亮条还在"
    assert "tr:hover td:first-child" in CSS and "inset 2px 0" in CSS, \
        "表格行没换成左侧强调竖线"


# ───── ④ 开关中心在行水平中线、文字上下边距对称 ─────
@has_chrome
def test_gate_switch_center_on_row_midline(tmp_path):
    got = _run(tmp_path, "sw", "flags", """
      const row = document.querySelector('.frow');
      const sw = row.querySelector('.sw i'), ft = row.querySelector('.ft');
      const R = e => e.getBoundingClientRect(), cy = r => r.top + r.height/2;
      out.align = getComputedStyle(row).alignItems;
      out.diff = Math.round(cy(R(sw)) - cy(R(row)));
      out.pad = getComputedStyle(row).paddingTop + '/' + getComputedStyle(row).paddingBottom;
    """, {"flags_page": {"ok": True, "data": shell.FIXTURE["flags_page"]},
          "device_actions_page": {"ok": True, "data": shell.FIXTURE["device_actions_page"]}})
    assert got.get("align") == "center", "行没改成 center 对齐：%s" % got.get("align")
    assert abs(got.get("diff", 99)) <= 2, "开关中心不在行中线：%s" % got.get("diff")
    p = (got.get("pad") or "").split("/")
    assert len(p) == 2 and p[0] == p[1], "上下边距不对称：%s" % got.get("pad")


# ───── ⑤ 滚动容器不再把字硬切在半行 ─────
def test_gate_scroll_containers_have_bottom_cushion():
    # 必须落在 .tblwrap 自己这一块里：写成 -webkit-mask-image 或写在别处都不算
    m = re.search(r"\.tblwrap \{[^}]*\}", CSS, re.S)
    assert m, "找不到表格滚动容器 .tblwrap"
    blk = m.group(0)
    assert "padding-bottom" in blk, "表格滚动容器底部没有留白：%s" % blk
    # 光有属性不够，值必须真留出一行的余量（改成 0 就等于没有）
    m2 = re.search(r"padding-bottom:\s*(\d+)px", blk)
    assert m2 and int(m2.group(1)) >= 12, "底部留白太小，仍会贴边切字：%s" % blk
    assert re.search(r"(?<!-)mask-image: linear-gradient", blk), \
        "滚动容器底边只有 -webkit- 前缀，标准属性缺失：%s" % blk


# ───── ⑥ 数值列左对齐、操作列居中 ─────
def test_gate_table_columns_align():
    assert re.search(r"table\.tbl th\.num, table\.tbl td\.num \{[^}]*text-align: left", CSS, re.S), \
        "数值列还是右对齐"
    assert re.search(r"th\.act[^{]*\{[^}]*text-align: center", CSS, re.S), \
        "操作列没居中"


# ───── ⑦ 模型卡片名字与标签同一行、下对齐；保存右边不再重复 ─────
@has_chrome
def test_gate_model_cap_inline_and_no_duplicate_hint(tmp_path):
    got = _run(tmp_path, "mod", "models", """
      const c = document.querySelector('.mods .mod');
      const R = e => e.getBoundingClientRect();
      const b = c.querySelector('.mh b'), cap = c.querySelector('.mh .cap');
      out.sameLine = Math.abs(R(b).top - R(cap).top) < 12;
      out.gap = Math.round(R(cap).left - R(b).right);
      out.footHints = Array.from(c.querySelectorAll('.foot .hint')).map(e => e.textContent);
      out.keyHint = (c.querySelector('[data-f="api_key"]')||{}).placeholder || '';
    """, {"models_page": {"ok": True, "data": shell.FIXTURE["models_page"]}})
    assert got.get("sameLine"), "provider 标签掉到第二行了：%s" % got
    assert got.get("footHints") == [], "保存右边还有重复的提示文案：%s" % got.get("footHints")
    assert "留空" in (got.get("keyHint") or ""), "API Key 的占位说明也丢了：%s" % got.get("keyHint")


# ───── ⑧ 热力图：一整条连续颜色 ─────
@has_chrome
def test_gate_heatmap_is_continuous_gradient(tmp_path):
    got = _run(tmp_path, "heat", "dashboard", """
      const rs = Array.from(document.querySelectorAll('rect[data-hm]'));
      out.fills = Array.from(new Set(rs.map(r => r.getAttribute('fill'))));
      const svg = document.querySelector('.heat svg');
      out.grad = svg ? svg.querySelectorAll('linearGradient').length : 0;
      out.legend = svg ? svg.textContent : '';
    """, {"dashboard": {"ok": True, "data": {
        "kpi": {}, "heat": [{"date": "2026-10-%02d" % (i + 1), "tokens": t, "future": False}
                            for i, t in enumerate([0, 5, 12, 20, 33, 40, 55, 66, 70, 82, 91, 100])],
        "heatPeak": 100, "memSpark": [], "tokenSpark": [], "meta": {}}}})
    assert len(got.get("fills", [])) >= 8, "颜色不是连续的（档位太少）：%s" % got.get("fills")
    assert got.get("grad", 0) >= 1, "图例不是连续渐变条：%s" % got.get("grad")
    assert "低" not in (got.get("legend") or "") and "峰" not in (got.get("legend") or ""), \
        "图例还在写离散档位：%s" % got.get("legend")


# ───── ⑨ 服务器身份 7→3 ─────
@has_chrome
def test_gate_identity_has_three_cards(tmp_path):
    import copy
    idt = copy.deepcopy(shell.FIXTURE["identity_page"])
    reg = shell.FIXTURE["registration_page"]
    got = _run(tmp_path, "id", "identity", """
      const cards = Array.from(document.querySelectorAll('.mod'));
      out.n = cards.length;
      out.titles = cards.map(c => (c.querySelector('.mh b')||{}).textContent || '');
      out.acts = Array.from(document.querySelectorAll('[data-act]')).map(e => e.dataset.act);
      out.text = document.getElementById('body').textContent;
    """, {"identity_page": {"ok": True, "data": idt},
          "registration_page": {"ok": True, "data": reg}})
    assert got.get("n") == 3, "卡片没合成 3 块：%s" % got.get("n")
    t = " ".join(got.get("titles", []))
    assert "服务器" in t and "接入" in t and "轮换" in t, "三块主题不对：%s" % got.get("titles")
    for act in ("channel-login", "id-copy", "id-code", "id-rotate", "reg-save"):
        assert act in got.get("acts", []), "合并后丢了动作 %s" % act
    for word in ("校验档位", "部署面", "配对码", "注册策略"):
        assert word in (got.get("text") or ""), "合并后丢了内容 %s" % word
