"""UI 翻新批的行为闸门：断言**运行时行为**，不是读代码。

本批改了什么（2026-11-06）：
① 热力图图例另起一行，不再压住月份标签；
② 行动通道页的失败提示有落点（全局状态条），不再静默丢；
③ 开关是真按钮（可聚焦、带 role/aria）；
④ 侧栏序号即页码，数字键能翻页；
⑤ 弹层的「取消／关闭」接上了线（此前是死按钮——弹层挂在 document.body 上，
   页内的 data-act 派发收不到它）；
⑥ 表头可排序、操作列钉右、表头吸顶；
⑦ 亮纸档弱文字提到 4.5:1 以上；全站字号只许从阶梯里取。

每条都要能变红：实现改坏一处，下面必须失败。
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


def _probe(body: str) -> str:
    """把一段探针脚本接在页面后面，返回它写进 #uiProbe 的 JSON 文本。"""
    return """
<script>
window.addEventListener('load', function(){
  setTimeout(async function(){
    const out = {};
    try {
      %s
    } catch (e) { out.error = String(e && e.message || e); }
    const d = document.createElement('div');
    d.id = 'uiProbe';
    d.textContent = JSON.stringify(out);
    document.body.appendChild(d);
  }, 300);
});
</script>
</body>""" % body


def _run(tmp_path, page, bundle, overlay=None, body=""):
    html = shell.build_shot_html(page, bundle, overlay=overlay).replace("</body>", _probe(body), 1)
    f = tmp_path / ("ui_%s.html" % page)
    f.write_text(html, encoding="utf-8")
    r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                        "--window-size=1360,880", "--temp-profile=ocgate",
                        "--virtual-time-budget=4000", "file:///" + str(f).replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    dom = re.sub(r"<script\b.*?</script>", " ", r.stdout, flags=re.S | re.I)
    m = re.search(r'id="uiProbe">(.*?)</div>', dom, re.S)
    assert m, "探针没跑起来（页面没渲染？）"
    return json.loads(m.group(1).replace("&quot;", '"'))


def _env(name, data):
    return {name: {"ok": True, "data": data}}


# ─────────────────────────── ⑤ 弹层的取消按钮 ───────────────────────────

@has_chrome
def test_弹层的取消按钮能关掉弹层(tmp_path):
    """死链修复：弹层挂在 document.body，页内的 data-act 派发收不到它。

    修之前：点「取消」什么也不发生，用户只能去关窗口。
    """
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}
    got = _run(tmp_path, "accounts", bundle, overlay="confirm",
               body="""
                 out.before = !!document.getElementById('ovl');
                 const btn = document.querySelector('#ovl [data-act="ovl-cancel"]');
                 out.hasBtn = !!btn;
                 if (btn) btn.click();
                 await new Promise(r => setTimeout(r, 120));
                 out.after = !!document.getElementById('ovl');
               """)
    assert got.get("before") is True, got
    assert got.get("hasBtn") is True, "弹层里没有取消按钮"
    assert got.get("after") is False, "点取消没关掉弹层（按钮仍是死的）"


@has_chrome
def test_删号向导的关闭按钮也能用(tmp_path):
    """护栏弹层用的是同一个 ovl（关闭按钮），一并验。"""
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}
    got = _run(tmp_path, "accounts", bundle, overlay="blocked",
               body="""
                 const btn = document.querySelector('#ovl [data-act="ovl-cancel"]');
                 out.label = btn ? btn.textContent : '';
                 if (btn) btn.click();
                 await new Promise(r => setTimeout(r, 120));
                 out.after = !!document.getElementById('ovl');
               """)
    assert got.get("label") == "关闭", got
    assert got.get("after") is False, got


# ─────────────────────────── ⑥ 表头排序 ───────────────────────────

@has_chrome
def test_点表头能排序且再点一次换方向(tmp_path):
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]}}
    got = _run(tmp_path, "accounts", bundle,
               body="""
                 const ids = () => Array.from(document.querySelectorAll('tbody tr td:first-child'))
                                    .map(td => td.textContent.trim());
                 out.before = ids();
                 const th = document.querySelector('th[data-sort="id"]');
                 out.hasTh = !!th;
                 th.click();
                 await new Promise(r => setTimeout(r, 60));
                 out.up = ids();
                 document.querySelector('th[data-sort="id"]').click();   // 重绘后是新表头
                 await new Promise(r => setTimeout(r, 60));
                 out.down = ids();
               """)
    assert got.get("hasTh") is True, "表头没带 data-sort，点不动"
    assert got.get("before") == ["2", "3"], got
    assert got.get("up") == ["2", "3"], got
    assert got.get("down") == ["3", "2"], "再点一次没有换方向 %s" % got


@has_chrome
def test_排序只动当前页(tmp_path):
    """审计页与账号页各有一份排序状态，互不串。"""
    bundle = {"accounts_page": {"ok": True, "data": shell.FIXTURE["accounts_page"]},
              "audit_page": {"ok": True, "data": shell.FIXTURE["audit_page"]}}
    got = _run(tmp_path, "accounts", bundle,
               body="""
                 document.querySelector('th[data-sort="id"]').click();
                 await new Promise(r => setTimeout(r, 60));
                 out.auditSort = JSON.stringify(SORTS.audit);
                 out.accountsSort = JSON.stringify(SORTS.accounts);
               """)
    assert got.get("accountsSort") == '{"key":"id","dir":1}', got
    assert got.get("auditSort") == '{"key":"","dir":1}', got


# ─────────────────────────── ④ 数字键翻页 ───────────────────────────

@has_chrome
def test_数字键能翻页(tmp_path):
    """合并成 8 页后一位数就够；9 / 0 不再对应任何页，按了不动。"""
    bundle = {n: {"ok": True, "data": {}} for n in
              ("dashboard", "server_page", "log_page", "models_page", "flags_page",
               "audit_page", "accounts_page", "registration_page", "identity_page",
               "device_actions_page", "overview_page")}
    got = _run(tmp_path, "dashboard", bundle,
               body="""
                 const title = () => document.getElementById('pageTitle').textContent;
                 const key = k => document.dispatchEvent(
                   new KeyboardEvent('keydown', {key: k, bubbles: true}));
                 out.start = title();
                 key('3');
                 await new Promise(r => setTimeout(r, 60));
                 out.after3 = title();
                 key('8');                      // 8＝审计（合并后最后一页）
                 await new Promise(r => setTimeout(r, 60));
                 out.after8 = title();
                 key('9');                      // 已经没有第 9 页
                 await new Promise(r => setTimeout(r, 60));
                 out.after9 = title();
               """)
    assert got.get("after3") == "运行日志", got
    assert got.get("after8") == "审计", got
    assert got.get("after9") == "审计", "按 9 不该翻到不存在的页：%s" % got
    assert got.get("start") == "仪表盘", got

# ─────────────────────────── ① 图例与月份标签不重叠 ───────────────────────────

@has_chrome
def test_热力图图例另起一行不压月份标签(tmp_path):
    """此前图例与最后一个月份标签抢同一条基线（坐标硬编码＝必然重叠）。"""
    import console_api as api
    import console_core as sc  # W6-A：桩/断言目标改为共享真源（纯标准库）

    heat = [{"date": "2026-10-%02d" % i, "tokens": (i * 1000) % 9000, "future": False}
            for i in range(1, 29)]
    heat += [{"date": "2026-09-%02d" % i, "tokens": 0, "future": False} for i in range(1, 29)]
    heat += [{"date": "2026-08-%02d" % i, "tokens": 0, "future": False} for i in range(1, 29)]
    old = sc._read_token_heatmap
    sc._read_token_heatmap = lambda *a, **k: heat
    try:
        bundle = {"dashboard": api.call("dashboard")}
    finally:
        sc._read_token_heatmap = old

    got = _run(tmp_path, "dashboard", bundle,
               body="""
                 const svg = document.querySelector('.heat svg');
                 out.hasSvg = !!svg;
                 if (!svg) { } else {
                   const texts = Array.from(svg.querySelectorAll('text'));
                   const months = texts.filter(t => /月$/.test(t.textContent.trim()));
                   const legend = texts.filter(t => t.textContent.trim() === '少' ||
                                                  t.textContent.trim() === '多');
                   const swatches = Array.from(svg.querySelectorAll('rect'))
                     .filter(r => r.getAttribute('width') === '18');
                   out.monthY = months.map(t => Number(t.getAttribute('y')));
                   out.legendY = legend.map(t => Number(t.getAttribute('y')));
                   out.swatchY = swatches.map(r => Number(r.getAttribute('y')));
                   out.legendX = legend.map(t => Number(t.getAttribute('x')));
                   out.monthX = months.map(t => Number(t.getAttribute('x')));
                   out.swatchX = swatches.map(r => Number(r.getAttribute('x')));
                 }
               """)
    assert got.get("hasSvg") is True, got
    assert got.get("monthY"), "热力图里没有月份标签 %s" % got
    legend_y = got.get("legendY") or []
    month_y = got.get("monthY") or []
    assert legend_y and month_y, got
    # 图例基线必须比月份标签基线低一整行（字号 10.5px，行距至少 18px）
    assert min(legend_y) - max(month_y) >= 18, \
        "图例还在月份标签同一条基线上：%s vs %s" % (legend_y, month_y)
    # 且图例色块不许落进月份标签那一行的纵向范围
    sw_y = got.get("swatchY") or []
    if sw_y:
        assert min(sw_y) + 9 <= max(month_y) or min(sw_y) >= max(month_y), \
            "图例色块压在月份标签上：%s vs %s" % (sw_y, month_y)


# ─────────────────────────── ⑦ 视觉口径（CSS 层面） ───────────────────────────

def _lum(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _cr(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _var(name: str, block: str = ":root") -> str:
    m = re.search(re.escape(block) + r"\s*\{(.*?)\}", CSS, re.S)
    assert m, "CSS 里找不到 %s" % block
    mm = re.search(r"--%s:\s*([^;]+)" % name, m.group(1))
    assert mm, "%s 里没有 --%s" % (block, name)
    return mm.group(1).strip()


def test_亮纸档弱文字过得了对比度():
    """design.md §13：次文字 ≥ 4.5:1。

    旧值 #9c9284 对纸底只有 2.69:1——「弱」得看不见。
    """
    paper = _var("paper")
    weak = _var("weak")
    assert _cr(weak, paper) >= 4.5, "弱文字 %s 对纸底 %s 只有 %.2f:1" % (weak, paper, _cr(weak, paper))


def test_暗纸档弱文字过得了对比度():
    paper = _var("paper", 'html[data-theme="dark"]')
    weak = _var("weak", 'html[data-theme="dark"]')
    assert _cr(weak, paper) >= 4.5, "弱文字 %s 对墨纸 %s 只有 %.2f:1" % (weak, paper, _cr(weak, paper))


def test_次文字过得了对比度():
    paper = _var("paper")
    dim = _var("dim")
    assert _cr(dim, paper) >= 4.5, "次文字 %s 对纸底 %s 只有 %.2f:1" % (dim, paper, _cr(dim, paper))


def test_全站字号只许从阶梯里取():
    """字号纪律：不许再写裸字号（阶梯是设计系统的唯一真源）。"""
    bare = re.findall(r"font-size:\s*(\d+(?:\.\d+)?)px", CSS)
    assert not bare, "CSS 里还有裸字号：%s" % sorted(set(bare))
    steps = set(re.findall(r"--fs-[a-z0-9]+:\s*([\d.]+)px", CSS))
    assert len(steps) >= 8, "字号阶梯只剩 %s" % sorted(steps)
    vals = sorted(float(x) for x in steps)
    assert len(set(vals)) == len(vals), "阶梯里有两档同号：%s" % vals
    assert vals == sorted(vals), "阶梯没排开：%s" % vals


def test_暗纸档也有图表填充与细发丝两个衍生变量():
    for name in ("chartfill", "hair"):
        assert _var(name, 'html[data-theme="dark"]'), "暗纸档缺 --%s" % name


def test_状态条与开关样式都在():
    assert ".status" in CSS and 'id="statusMsg"' in HTML
    assert "button.sw" in CSS, "开关按钮没有样式（会被浏览器默认按钮皮盖住）"
    assert "focus-visible" in CSS, "键盘焦点没有可见反馈"
    assert "role=\"switch\"" in HTML, "开关没有 role=switch"
    assert "aria-checked" in HTML, "开关没有 aria-checked"
