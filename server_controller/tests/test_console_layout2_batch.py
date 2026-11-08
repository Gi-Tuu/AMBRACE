"""控制台第二批视觉/交互闸门（2026-11-08 下午 10 图反馈）。

覆盖：表格竖线留白与行星云 / 侧栏底线同高（在 r4 里）/ 端口不再 undefined /
日志易读版整行高亮+点开原文左对齐+去掉「原文」按钮 / 刷新图标与星球形变 /
设置星球 / 日志工具条两行且级别单档时隐藏 / 账号三读数与额度卡搬家 /
仪表盘合并状态卡+角色筛选+双环 / 星球滚条挂到 .logeasy / 副标题精简。
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
    f = os.path.join(WEB, "_v2_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,900", "--temp-profile=ovs",
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


def _dashboard_data():
    return {"kpi": {"server": "运行中", "serverCap": "PID 1 · 端口 8000", "health": "正常",
                    "chars": "14", "mems": "13,733", "tokens": "40,272,483",
                    "vlm": "qwen3.8-max", "vlmCap": "云端视觉 API"},
            "heatPeak": 100, "memSpark": [1, 3, 2, 5], "tokenSpark": [2, 5, 3, 8],
            "meta": {"target": "127.0.0.1:8000"},
            "memStats": {"kinds": [{"key": "event", "label": "近期事件", "count": 6},
                                   {"key": "insight", "label": "洞察", "count": 4}],
                         "roles": [{"id": 13, "name": "sam", "count": 4591,
                                    "kinds": [{"key": "insight", "label": "洞察", "count": 1549}]}]},
            "heat": [{"date": "2026-11-%02d" % (i + 1), "tokens": (i + 1) * 3, "future": False,
                      "tasks": {"主对话": (i + 1) * 2, "记忆处理": i + 1}} for i in range(12)]}


# ───── ① 表格首列：竖线不许贴字，但文字位置不许动 ─────
@has_chrome
def test_gate_table_rule_has_gutter_and_text_stays(tmp_path):
    got = _run(tmp_path, "gutter", "audit", """
      const wrap = document.querySelector('.tblwrap');
      const th = wrap.querySelector('th'), td = wrap.querySelector('td');
      const cs = getComputedStyle(td);
      out.padL = parseFloat(cs.paddingLeft);
      out.marginL = parseFloat(getComputedStyle(wrap).marginLeft);
      // 表头与单元格第一个字必须仍对齐（搬家前后都是同一列）
      out.sameX = Math.abs(th.getBoundingClientRect().left - td.getBoundingClientRect().left) < 1;
      out.wrapLeft = Math.round(wrap.getBoundingClientRect().left);
    """, {"audit_page": {"ok": True, "data": shell.FIXTURE["audit_page"]}})
    assert got.get("padL", 0) >= 10, "首列没留出给竖线的空白：%s" % got
    assert got.get("marginL", 0) <= -10, "容器没外扩，竖线会被裁掉：%s" % got
    assert got.get("sameX"), "表头和单元格的第一个字没对齐：%s" % got


# ───── ② 表格行接上同款流动星云 ─────
def test_gate_table_rows_share_the_row_nebula():
    blk = re.search(r"table\.tbl tbody tr:hover \{([^}]*)\}", CSS, re.S)
    assert blk, "表格行没有悬停样式"
    assert "radial-gradient" in blk.group(1) and "row-nebula" in blk.group(1), \
        "表格行没接上和「开关与权限」同款的流动星云：%s" % blk.group(1)[:120]


# ───── ③ 左下角那行「端口 N」已按用户要求移除，只留设置星球 ─────
def test_gate_the_port_line_is_gone():
    assert 'id="sideFoot"' not in HTML, "端口那行的容器还在"
    assert 'getElementById("sideFoot")' not in HTML, "还有代码往端口行里写东西"
    assert not re.search(r'"端口 " \+', HTML), "页面还在拼端口文案"
    assert 'class="set-orb"' in HTML, "设置星球丢了"


# ───── ④ 日志易读版：整行可点、原文在下方左对齐、没有「原文」按钮 ─────
@has_chrome
def test_gate_log_rows_open_raw_inline_and_left(tmp_path):
    log = {"text": "x", "empty": False, "hint": "", "paths": {}, "summary": "1 条",
           "sources": [{"key": "app", "label": "应用日志", "count": 1}],
           "levels": [{"key": "INFO", "label": "日常", "count": 1}],
           "rows": [{"source": "app", "level": "INFO", "time": "12:00:00", "module": "m",
                     "msg": "hi", "raw": "2026-11-08 12:00:00 | INFO | m | hi",
                     "cont": False, "easy": "示例"}]}
    got = _run(tmp_path, "logrow", "log", """
      const row = document.querySelector('.logrow');
      const raw = row.querySelector('.lograw');
      out.hasSummary = !!row.querySelector('summary');
      out.beforeOpen = raw.getBoundingClientRect().height;
      row.click();
      await new Promise(r => setTimeout(r, 120));
      const rr = raw.getBoundingClientRect();
      out.afterOpen = rr.height;
      out.align = getComputedStyle(raw).textAlign;
      out.below = rr.top > row.querySelector('.ez').getBoundingClientRect().top;
      out.cursor = getComputedStyle(row).cursor;
      const cs = getComputedStyle(row);
      out.hasNebula = /radial-gradient/.test(cs.backgroundImage) || cs.boxShadow !== 'none';
    """, {"log_page": {"ok": True, "data": log}})
    assert not got.get("hasSummary"), "「原文」按钮还在"
    assert got.get("beforeOpen") == 0, "原文默认就是展开的：%s" % got
    assert got.get("afterOpen", 0) > 0, "点一下没展开原文：%s" % got
    assert got.get("align") == "left", "原文不是左对齐：%s" % got.get("align")
    assert got.get("below"), "原文没落在消息下方：%s" % got
    assert got.get("cursor") == "pointer", "整行不像可点的：%s" % got.get("cursor")


def test_gate_log_row_hover_uses_the_same_highlight():
    blk = re.search(r"\.logrow:hover \{([^}]*)\}", CSS, re.S)
    assert blk, "易读版日志行没有悬停样式"
    assert "radial-gradient" in blk.group(1) and "inset 2px 0 0" in blk.group(1), \
        "日志行高亮与别的行不是同一套：%s" % blk.group(1)[:120]


# ───── ⑤ 刷新按钮：平面圆环 → 星球，点一下真的会变形 ─────
def test_gate_refresh_is_an_icon_not_a_label():
    # 注意用完整签名匹配：`const refreshBtnX` 也会包含 "const refreshBtn" 这个前缀
    assert re.search(r"const refreshBtn = act =>", HTML), "刷新没有做成图标按钮"
    assert "rb-plane" in HTML and "rb-orb" in HTML, "图标里没有平面弧与星球两层"
    assert HTML.count("refreshBtn('") >= 2, "还有「刷新」文字按钮没换"
    assert '>刷新<' not in HTML, "文本「刷新」按钮还在"


@has_chrome
def test_gate_refresh_morphs_into_a_planet_and_back(tmp_path):
    got = _run(tmp_path, "rbtn", "audit", """
      const b = document.querySelector('.rbtn');
      out.isIcon = !!b && !!b.querySelector('.rb-arc') && !!b.querySelector('.rb-orb');
      const plane = b.querySelector('.rb-plane'), orb = b.querySelector('.rb-orb');
      out.flat = getComputedStyle(plane).opacity;
      b.click();
      await new Promise(r => setTimeout(r, 60));
      out.busy = b.classList.contains('busy');
      out.orbOn = getComputedStyle(orb).opacity;      // 过渡开始，值应已朝 1 走
      out.spin = getComputedStyle(orb).animationName;
      await new Promise(r => setTimeout(r, 1200));
      out.after = b.classList.contains('busy');
    """, {"audit_page": {"ok": True, "data": shell.FIXTURE["audit_page"]}})
    assert got.get("isIcon"), "按钮里没有平面弧+星球两个图形：%s" % got
    assert float(got.get("flat") or 0) > 0.5, "默认不是平面的：%s" % got
    assert got.get("busy"), "点下去没进入变形状态：%s" % got
    assert got.get("spin") == "rb-spin", "星球没转起来：%s" % got.get("spin")
    assert not got.get("after"), "转完没弹回平面：%s" % got


# ───── ⑥ 设置：左下角一颗只露一角的星球，点了还能开设置 ─────
def test_gate_settings_is_a_corner_planet():
    blk = re.search(r"\.set-orb \{([^}]*)\}", CSS, re.S)
    assert blk, "没有设置星球"
    body = blk.group(1)
    assert re.search(r"left:\s*-\d", body) and re.search(r"bottom:\s*-\d", body), \
        "星球没有缩到角外面（露不出 1/4）：%s" % body[:120]
    assert "planet_moon.png" in body, "设置星球没有球面纹理"
    assert 'class="set-orb"' in HTML and 'data-act="settings"' in HTML.replace(
        'class="set-orb" data-act="settings"', 'data-act="settings"'), "设置星球没接上动作"
    assert re.search(r"\.set-orb:hover \{[^}]*scale\(1\.0\d\)", CSS, re.S), "指上去没有微放大"
    assert re.search(r"\.set-orb \.pl-shade \{[^}]*radial-gradient", CSS, re.S), \
        "背面没压黑（应当中间黑、边上一圈发光）"


# ───── ⑦ 日志工具条：两行三组，级别只有一档时不摆 ─────
@has_chrome
def test_gate_log_tools_two_rows_and_level_hidden_when_single(tmp_path):
    one = {"text": "x", "empty": False, "hint": "", "paths": {}, "summary": "",
           "sources": [{"key": "app", "label": "应用日志", "count": 80}],
           "levels": [{"key": "INFO", "label": "日常", "count": 80}], "rows": []}
    got = _run(tmp_path, "lt1", "log", """
      const rows = Array.from(document.querySelectorAll('.logtools .lt-row'));
      out.rows = rows.length;
      out.groups = rows.length ? rows[0].querySelectorAll('.lg').length : 0;
      out.hasLevel = /级别/.test(document.querySelector('.logtools').textContent);
      out.acts = Array.from(document.querySelectorAll('.lt-row.acts [data-act]')).map(e => e.dataset.act);
    """, {"log_page": {"ok": True, "data": one}})
    assert got.get("rows") == 2, "工具条没分成两行：%s" % got
    assert got.get("groups") == 2, "第一行只剩两组（视图/来源），级别该被隐藏：%s" % got
    assert not got.get("hasLevel"), "只有一档级别时还摆着级别筛选：%s" % got
    assert got.get("acts") and "autoscroll" in got.get("acts"), "第二行动作或自动滚动丢了：%s" % got

    two = dict(one, levels=[{"key": "INFO", "label": "日常", "count": 4},
                            {"key": "ERROR", "label": "出错", "count": 2}])
    got2 = _run(tmp_path, "lt2", "log", """
      out.groups = document.querySelectorAll('.lt-row')[0].querySelectorAll('.lg').length;
      out.hasLevel = /级别/.test(document.querySelector('.logtools').textContent);
    """, {"log_page": {"ok": True, "data": two}})
    assert got2.get("groups") == 3 and got2.get("hasLevel"), "两档级别时该显示级别筛选：%s" % got2


def test_gate_planet_scrollbar_reaches_the_easy_log():
    assert ".logeasy" in re.search(r'const SEL = "([^"]*)"', HTML).group(1), \
        "星球滚条没挂到易读版日志容器上"


# ───── ⑧ 账号页：三张读数搬来，额度卡搬走 ─────
@has_chrome
def test_gate_accounts_owns_the_three_readouts(tmp_path):
    ov = {"ok": True, "data": {"rows": [
        {"label": "账号总数", "value": "26"}, {"label": "已禁用账号", "value": "19"},
        {"label": "控制台管理员", "value": "3"}, {"label": "开启的开关", "value": "106"},
        {"label": "后端版本", "value": "3.5.6"}]}}
    got = _run(tmp_path, "ac3", "accounts", """
      const t = document.getElementById('body').textContent;
      out.text = t;
      out.ros = Array.from(document.querySelectorAll('.readout .ro i')).map(e => e.textContent);
      out.hasLimit = /默认额度/.test(t);
      out.icon = !!document.querySelector('.rbtn');
    """, {"overview_page": ov,
          "accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}})
    ros = got.get("ros") or []
    assert ros == ["账号总数", "已禁用账号", "控制台管理员"], "账号页三张读数不对：%s" % ros
    assert not got.get("hasLimit"), "默认额度卡还留在账号页"
    assert got.get("icon"), "账号页的刷新没换成图标"


@has_chrome
def test_gate_server_page_hosts_the_limit_card(tmp_path):
    srvd = {"state": {"alive": True, "remote": False, "pid": 1, "health": "正常", "paused": False},
            "target": {"host": "127.0.0.1", "port": 8000, "mode": "本机"},
            "rows": [],
            "server_limit": {"title": "服务器默认额度（0=未设置）", "value": "999,957（全局）",
                             "total_limit": 999957, "hint": "负数/非整数由后端拒绝（400）"}}
    got = _run(tmp_path, "sl2", "server", """
      const inp = document.getElementById('slVal');
      out.has = !!inp;
      const btn = document.querySelector('[data-act="server-limit-save"]');
      out.title = (document.querySelector('.mod .mh b') || {}).textContent || '';
      // 说明小字要贴在「保存」右边，不再单独占一行
      const hint = document.querySelector('.mod .field .hint');
      out.sameRow = !!hint && !!btn &&
        Math.abs(hint.getBoundingClientRect().top - btn.getBoundingClientRect().top) < 26 &&
        hint.getBoundingClientRect().left > btn.getBoundingClientRect().left;
      out.hintText = hint ? hint.textContent : '';
    """, {"server_page": {"ok": True, "data": srvd}})
    assert got.get("has"), "服务器页没有默认额度输入框：%s" % got
    assert "0=未设置" in (got.get("title") or ""), "标题括号里没精简成 0=未设置：%s" % got.get("title")
    assert got.get("sameRow"), "小字没搬到「保存」右边：%s" % got
    assert "400" in (got.get("hintText") or ""), "小字内容丢了：%s" % got.get("hintText")


# ───── ⑨ 仪表盘：状态合并、去图像理解、一个环跟着左列走 ─────
@has_chrome
def test_gate_dashboard_merged_status_and_ring(tmp_path):
    got = _run(tmp_path, "dash2", "dashboard", """
      const t = document.getElementById('body').textContent;
      out.text = t;
      out.kr = Array.from(document.querySelectorAll('.kr .k')).map(e => e.textContent);
      out.rings = document.querySelectorAll('.ring').length;
      out.title = (document.querySelector('.ring-t') || {}).textContent || '';
      out.select = !!document.getElementById('memRole');
      out.hasVlm = /图像理解/.test(t);
      out.hasHealthCard = /服务健康/.test(t);
      out.verRow = /后端版本/.test(t);
    """, {"dashboard": {"ok": True, "data": _dashboard_data()}})
    kr = got.get("kr") or []
    assert "当前健康（是否健康）" in kr and "当前状态（是否运行）" in kr, "状态与健康没合成一张：%s" % kr
    assert not got.get("hasHealthCard"), "「服务健康」这张卡还在"
    assert not got.get("hasVlm"), "图像理解卡还在"
    assert got.get("select"), "没有角色筛选下拉"
    assert got.get("rings") == 1, "应当只有一个大环：%s" % got.get("rings")
    assert got.get("title") == "Token 构成", "默认环不是累计 Token：%s" % got.get("title")
    assert got.get("verRow"), "后端版本没做成列表里的一项"


@has_chrome
def test_gate_dashboard_role_filter_changes_memory_count(tmp_path):
    got = _run(tmp_path, "dash3", "dashboard", """
      const num = () => Array.from(document.querySelectorAll('.kr .v.num'))
        .map(e => e.textContent)[0];
      out.all = num();
      const sel = document.getElementById('memRole');
      sel.value = '13';
      sel.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(r => setTimeout(r, 260));
      out.role = num();
      out.kept = document.getElementById('memRole') ? document.getElementById('memRole').value : '';
      out.tokenNote = /Token 按账号统计/.test(document.getElementById('body').textContent);
    """, {"dashboard": {"ok": True, "data": _dashboard_data()}})
    assert (got.get("all") or "").replace(",", "") == "13733", "全部角色时记忆数不对：%s" % got
    assert (got.get("role") or "").replace(",", "") == "4591", "换角色后记忆数没跟着变：%s" % got
    assert got.get("kept") == "13", "换完角色下拉被重置了：%s" % got
    assert got.get("tokenNote"), "选了角色也没说明 token 不分角色"


# ───── ⑩ 副标题：八页一句话说清，不再塞实现细节 ─────
def test_gate_subtitles_are_one_short_sentence():
    m = re.search(r"const TITLES = \{([^}]*)\}", HTML, re.S)
    assert m, "找不到 TITLES"
    pairs = {k: (n, s) for k, n, s in
             re.findall(r'(\w+): \["([^"]+)", "([^"]+)"\]', m.group(1))}
    assert len(pairs) == 8, "八页副标题不全：%s" % sorted(pairs)
    for key, (_name, sub) in pairs.items():
        assert 4 <= len(sub) <= 24, "%s 的副标题太长或太短（%d 字）：%s" % (key, len(sub), sub)
        assert "；" not in sub, "%s 的副标题还在用分号堆两件事：%s" % (key, sub)
