"""C 批闸门：热力图三档 / 远程禁按钮 / 服务器默认额度可保存 / 开关标脏后保存。

四条都是「界面上能点的东西没有如实反映状态」这一类，所以全部用真渲染 + 真点击来量：
不是读代码猜，而是点下去之后看发了什么请求、界面说了什么。

纪律：先红后绿；每条都要能变红。
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


def _dash():
    """自己造一份热力图数据：12 个非零且互不相同的值。

    连续色阶会给出 12 种透明度，三档只会给出 3 种——这样才能把两者区分开。
    """
    toks = [0, 5, 12, 20, 33, 40, 55, 66, 70, 82, 91, 100, 100, 0, 0]
    rows = [{"date": "2026-10-%02d" % (i + 1), "tokens": t, "future": False}
            for i, t in enumerate(toks)]
    return {"kpi": {}, "heat": rows, "heatPeak": 100, "memSpark": [], "tokenSpark": [],
            "meta": {"target": "x"}}


def _server(remote):
    return {"state": {"alive": True, "remote": remote, "pid": 4242,
                      "health": "正常", "paused": False},
            "target": {"host": "127.0.0.1", "port": 8000, "mode": "本机"},
            "rows": []}


def _run(tmp_path, name, page, body, bundle):
    html = shell.build_shot_html(page, bundle, theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='cProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},250);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_c_probe_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=ocb",
                            "--virtual-time-budget=6000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="cProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# ───── C5 热力图三档 ─────
@has_chrome
def test_gate_heatmap_uses_three_tiers(tmp_path):
    """用户改口：要一整条连贯颜色，不要离散档位 —— 颜色数应接近非零格子数。"""
    got = _run(tmp_path, "heat", "dashboard", """
      const rs = Array.from(document.querySelectorAll('rect[data-hm]'));
      const fills = rs.map(r => r.getAttribute('fill')).filter(f => f && f !== 'none');
      out.rects = rs.length;
      out.colors = Array.from(new Set(fills)).length;
      out.nonzero = fills.length;
      const svg = document.querySelector('.heat svg');
      out.grad = svg ? svg.querySelectorAll('linearGradient').length : 0;
    """, {"dashboard": {"ok": True, "data": _dash()}})
    assert got.get("rects", 0) >= 10, "热力图没画出来：%s" % got
    assert got["colors"] >= 8, "颜色又退回离散档位了：%s" % got
    assert got["colors"] >= got["nonzero"] - 1, "颜色没跟着用量连续变化：%s" % got
    assert got.get("grad", 0) >= 1, "图例不是连续色带：%s" % got


# ───── C7 远程禁按钮 ─────
@has_chrome
def test_gate_remote_host_disables_server_buttons(tmp_path):
    """连的是远程主机时，本机的启停按钮点不动——现在它们照样是亮的。"""
    remote = _run(tmp_path, "srvr", "server", """
      const bs = Array.from(document.querySelectorAll(
        '[data-act="start"],[data-act="stop"],[data-act="restart"]'));
      out.n = bs.length;
      out.disabled = bs.map(b => b.disabled);
      out.tip = bs.map(b => b.getAttribute('title') || '');
    """, {"server_page": {"ok": True, "data": _server(True)}})
    assert remote.get("n") == 3, "启停按钮没找到：%s" % remote
    assert all(remote["disabled"]), "远程主机上按钮still能点：%s" % remote
    assert any("远程" in t for t in remote["tip"]), "没告诉用户为什么点不动：%s" % remote

    local = _run(tmp_path, "srvl", "server", """
      const bs = Array.from(document.querySelectorAll(
        '[data-act="start"],[data-act="stop"],[data-act="restart"]'));
      out.disabled = bs.map(b => b.disabled);
    """, {"server_page": {"ok": True, "data": _server(False)}})
    assert local.get("disabled") == [False, False, False], "本机不该禁用：%s" % local


# ───── C2 服务器默认额度可保存 ─────
@has_chrome
def test_gate_server_default_limit_is_saveable(tmp_path):
    """额度卡原来只有只读的一行值，没有改的入口；这一批整卡从账号管理搬到服务器控制。"""
    srvd = _server(False)
    srvd["server_limit"] = dict(shell.FIXTURE["accounts_page"]["server_limit"], total_limit=999957)
    got = _run(tmp_path, "sl", "server", """
      const inp = document.getElementById('slVal');
      out.hasInput = !!inp;
      out.value = inp ? inp.value : '';
      const calls = [];
      const orig = window.api;
      window.api = function (n) {
        calls.push(Array.prototype.slice.call(arguments));
        return orig.apply(null, arguments);
      };
      const btn = document.querySelector('[data-act="server-limit-save"]');
      out.hasBtn = !!btn;
      if (inp && btn) {
        inp.value = '500';
        btn.click();
        await new Promise(r => setTimeout(r, 300));
      }
      out.calls = calls.filter(c => c[0] === 'set_server_limit').map(c => c.slice(0, 2));
    """, {"server_page": {"ok": True, "data": srvd}})
    assert got.get("hasInput"), "没有可编辑的默认额度输入框：%s" % got
    assert str(got.get("value")) == "999957", "输入框没带上当前值：%s" % got
    assert got.get("hasBtn"), "没有保存按钮：%s" % got
    assert got.get("calls") == [["set_server_limit", 500]], "保存没把新值发出去：%s" % got


# ───── C3 开关先攒、再保存 ─────
@has_chrome
def test_gate_flags_are_staged_then_saved(tmp_path):
    """现在点一下开关就落库，手滑没有反悔的机会。改成先攒着、点保存才写。"""
    got = _run(tmp_path, "flags", "flags", """
      window.__DATA__.set_flag = {ok: true, data: {}};   // 出图模式没有写接口，这里补一个成功的
      const calls = [];
      const orig = window.api;
      window.api = function (n) {
        calls.push(n);
        return orig.apply(null, arguments);
      };
      const sw = document.querySelector('.grp .frow .sw');
      out.hasSwitch = !!sw;
      sw.click();
      await new Promise(r => setTimeout(r, 150));
      const bar = () => document.getElementById('flagDirtyBar');
      out.afterClickWrites = calls.filter(c => c === 'set_flag').length;
      out.afterClickBar = bar() ? bar().textContent : '';
      const save = document.querySelector('[data-act="flag-save"]');
      out.hasSave = !!save;
      if (save) {
        save.click();
        await new Promise(r => setTimeout(r, 500));
      }
      out.afterSaveWrites = calls.filter(c => c === 'set_flag').length;
      out.afterSaveBar = bar() ? bar().textContent : '';
    """, {"flags_page": {"ok": True, "data": shell.FIXTURE["flags_page"]},
          "device_actions_page": {"ok": True, "data": shell.FIXTURE["device_actions_page"]}})
    assert got.get("hasSwitch"), "开关没找到：%s" % got
    assert got["afterClickWrites"] == 0, "点一下就直接写后端了（没攒改动）：%s" % got
    assert "未保存" in got.get("afterClickBar", ""), "没有未保存提示：%s" % got
    assert got.get("hasSave"), "没有保存按钮：%s" % got
    assert got["afterSaveWrites"] >= 1, "点保存却没写后端：%s" % got
    assert "未保存" not in got.get("afterSaveBar", ""), "保存后还挂着未保存：%s" % got


def test_gate_auto_refresh_does_not_eat_staged_flags():
    """20 秒自动刷新会把攒着的改动冲掉——这条守的就是它。"""
    i = HTML.index("function armTimer")
    tail = HTML[i:i + 900]
    assert "flagDirty" in tail, "自动刷新没避让未保存的开关改动：会静默丢改动"


# ───── C1 设置对话框（watchdog_interval_sec / controller_refresh_ms）─────
def test_gate_server_settings_read_and_save(tmp_path, monkeypatch):
    """旧壳设置对话框的两个键：读得到、存得下、越界拒绝、别的键一个都不许动。"""
    import console_api as ca
    import console_core as sc

    cfg = tmp_path / "server_config.json"
    cfg.write_text(json.dumps({"watchdog_interval_sec": 120,
                               "controller_refresh_ms": 20000,
                               "api_key": "KEEP-ME"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sc, "CONFIG", str(cfg))

    got = ca.call("server_settings")
    assert got["ok"], got
    d = got["data"]
    assert d["watchdog_interval_sec"] == 120 and d["controller_refresh_sec"] == 20, d
    assert d["wd_range"] == [15, 3600] and d["rf_range"] == [5, 600], d

    r = ca.call("save_server_settings", 60, 5)
    assert r["ok"], r
    saved = json.loads(cfg.read_text(encoding="utf-8"))
    assert saved["watchdog_interval_sec"] == 60, saved
    assert saved["controller_refresh_ms"] == 5000, saved
    assert saved["api_key"] == "KEEP-ME", "把别的键写没了：%s" % saved

    for wd, rf in ((10, 5), (4000, 5), (60, 2), (60, 900), ("x", 5)):
        assert not ca.call("save_server_settings", wd, rf)["ok"], "越界没拦住：%s/%s" % (wd, rf)
    assert json.loads(cfg.read_text(encoding="utf-8"))["watchdog_interval_sec"] == 60, "被拒的写入落盘了"


@has_chrome
def test_gate_settings_dialog_opens_fills_and_saves(tmp_path):
    """侧栏那颗「设置」点开要有两个输入框，回填当前值，保存要把新值发出去。"""
    got = _run(tmp_path, "set", "dashboard", """
      const btn = document.querySelector('[data-act="settings"]');
      out.hasBtn = !!btn;
      const calls = [];
      const orig = window.api;
      window.api = function (n) {
        calls.push(Array.prototype.slice.call(arguments));
        return orig.apply(null, arguments);
      };
      if (btn) {
        btn.click();
        await new Promise(r => setTimeout(r, 120));
      }
      const wd = document.getElementById('setWd'), rf = document.getElementById('setRf');
      out.hasFields = !!wd && !!rf;
      out.wd = wd ? wd.value : '';
      out.rf = rf ? rf.value : '';
      const save = document.querySelector('[data-act="set-save"]');
      out.hasSave = !!save;
      if (wd && rf && save) {
        wd.value = '60'; rf.value = '5';
        save.click();
        await new Promise(r => setTimeout(r, 300));
      }
      out.calls = calls.filter(c => c[0] === 'save_server_settings')
                       .map(c => c.slice(0, 3));
      out.stillOpen = !!document.getElementById('setWd');
    """, {"server_settings": {"ok": True, "data": {
        "watchdog_interval_sec": 120, "controller_refresh_sec": 20,
        "wd_range": [15, 3600], "rf_range": [5, 600], "path": "backend/data/server_config.json",
        "msg": ""}},
        "save_server_settings": {"ok": True, "data": {"watchdog_interval_sec": 60,
                                                     "controller_refresh_sec": 5,
                                                     "msg": "已保存：守护 60s / 刷新 5s"}},
        "dashboard": {"ok": True, "data": _dash()}})
    assert got.get("hasBtn"), "侧栏没有「设置」入口：%s" % got
    assert got.get("hasFields"), "对话框里没有两个输入框：%s" % got
    assert str(got.get("wd")) == "120" and str(got.get("rf")) == "20", "没回填当前值：%s" % got
    assert got.get("hasSave"), "没有保存按钮：%s" % got
    assert got.get("calls") == [["save_server_settings", 60, 5]], "保存没把新值发出去：%s" % got
    assert got.get("stillOpen") is False, "保存后对话框没关：%s" % got


def test_gate_refresh_interval_comes_from_settings():
    """界面刷新间隔要能改：定时器不许再写死 20000。"""
    i = HTML.index("function armTimer")
    tail = HTML[i:i + 900]
    assert "refreshMs" in tail or "REFRESH_MS" in tail, "刷新间隔还是写死的：%s" % tail[:200]


# ───── C4 渠道登录按钮（后端 /channel-login 是仅本机的扫码页）─────
def test_gate_channel_login_opens_the_local_page(monkeypatch):
    """后端那个二维码页只有 127.0.0.1 能访问，控制台按钮要开的就是它。"""
    import console_api as ca
    import console_core as sc

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u) or True)
    monkeypatch.setattr(sc, "TARGET_HOST", "127.0.0.1")
    monkeypatch.setattr(sc, "TARGET_PORT", 8000)
    r = ca.call("open_channel_login")
    assert r["ok"], r
    assert opened == ["http://127.0.0.1:8000/channel-login"], opened

    # 连的是远程主机时不能开本机 127.0.0.1（那页根本不在本机）
    monkeypatch.setattr(sc, "TARGET_HOST", "10.0.0.9")
    opened.clear()
    bad = ca.call("open_channel_login")
    assert not bad["ok"], "远程主机不该去开本机页面：%s" % bad
    assert opened == [], "远程主机还是把浏览器打开了：%s" % opened


@has_chrome
def test_gate_channel_login_entry_is_the_sidebar_satellite(tmp_path):
    """页内那块渠道登录已按用户要求移除，入口只剩左下角那颗微信卫星。"""
    got = _run(tmp_path, "chl", "identity", """
      // 入口只有一处：左下角的卫星
      const btn = document.querySelector('.side [data-act="channel-login"]');
      out.hasOrb = !!btn;
      out.hasBtn = !!document.querySelector('#body [data-act="channel-login"]');
      out.text = btn ? btn.textContent : '';
      const calls = [];
      const orig = window.api;
      window.api = function (n) {
        calls.push(n);
        return orig.apply(null, arguments);
      };
      if (btn) { btn.click(); await new Promise(r => setTimeout(r, 200)); }
      out.calls = calls.filter(c => c === 'open_channel_login');
    """, {"identity_page": {"ok": True, "data": shell.FIXTURE["identity_page"]},
          "registration_page": {"ok": True, "data": shell.FIXTURE["registration_page"]},
          "open_channel_login": {"ok": True, "data": {"msg": "已在本机浏览器打开扫码页",
                                                      "url": "http://127.0.0.1:8000/channel-login"}}})
    assert got.get("hasOrb"), "左下角没有微信卫星入口：%s" % got
    assert not got.get("hasBtn"), "页内的渠道登录块应当已移除：%s" % got
    assert got.get("calls") == ["open_channel_login"], "点卫星没打开扫码页：%s" % got


# ───── C6 错误码分流 ─────
@has_chrome
def test_gate_error_codes_are_told_apart(tmp_path):
    """401/403/404/429/5xx/连不上，六种说法必须不一样——不能一律「读取失败」。"""
    got = _run(tmp_path, "err", "dashboard", """
      out.c403 = fail({ok: false, code: 403, error: "forbidden"});
      out.c404 = fail({ok: false, code: 404, error: "nf"});
      out.c429 = fail({ok: false, code: 429, error: "slow down"});
      out.c500 = fail({ok: false, code: 500, error: "boom"});
      out.net  = fail({ok: false, code: -1, error: "connect refused"});
      out.hints = {
        admin: /管理员/.test(out.c403),
        missing: /后端没有|没有这个接口|对不上/.test(out.c404),
        limit: /频繁|限流|稍后/.test(out.c429),
        server: /后端/.test(out.c500),
        net: /连不上|连接/.test(out.net),
      };
    """, {"dashboard": {"ok": True, "data": _dash()}})
    h = got.get("hints") or {}
    assert h.get("admin"), "403 没说是权限问题：%s" % got.get("c403")
    assert h.get("missing"), "404 没说是接口对不上：%s" % got.get("c404")
    assert h.get("limit"), "429 没说是限流：%s" % got.get("c429")
    assert h.get("server"), "5xx 没说是后端错：%s" % got.get("c500")
    assert h.get("net"), "连不上没给连不上的说法：%s" % got.get("net")
    assert len({got.get("c403"), got.get("c404"), got.get("c429")}) == 3, "几种码的文案还是一样的"


def test_gate_429_pauses_auto_refresh():
    """被限流还按每秒/每 20 秒打过去只会更糟：429 之后要让自动刷新歇一会儿。

    断言的是**语句**不是字面量 —— 只 grep "429" 的话，注释里那几个字也能骗过去。
    """
    i = HTML.index("async function loadPage")
    tail = HTML[i:i + 1600]
    assert re.search(r"if \(r\.code === 429\) backoffUntil = Date\.now\(\)", tail), \
        "loadPage 没在 429 时设退避：%s" % tail[:300]
    j = HTML.index("function armTimer")
    assert re.search(r"Date\.now\(\) < backoffUntil", HTML[j:j + 900]), "定时器没避让限流退避"


# ───── C4b 扫码页的相对路径（二维码出不来的根因）─────
def test_gate_channel_login_page_fetches_resolve_under_its_own_path():
    """页面在 `/channel-login`（没有尾斜杠）打开时，`./api/wechat/qrcode` 会被解析成
    `/api/wechat/qrcode` → 404，于是二维码永远出不来。

    这里用浏览器同一套解析规则（urljoin）算一遍，不用起服务、不用联网。
    """
    import importlib.util
    import pathlib
    from urllib.parse import urljoin

    path = pathlib.Path(HERE).parent / "backend" / "app" / "api" / "channel_login.py"
    assert path.is_file(), "找不到扫码页源码：%s" % path
    spec = importlib.util.spec_from_file_location("_chl_page", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    html = mod._PAGE_HTML
    urls = re.findall(r'fetch\("([^"]+)"', html)
    assert len(urls) >= 2, "没找到 fetch 调用：%s" % html[:200]
    base = "http://127.0.0.1:8000/channel-login"
    for u in urls:
        got = urljoin(base, u)
        assert got.startswith(base + "/"), (
            "页面在 %s 打开时，%s 会被解析成 %s → 404（二维码出不来就是它）" % (base, u, got))


# ───── C6b 动作级提示也走同一张错误表 ─────
def test_gate_action_toasts_use_the_same_error_table():
    """整页错误分流了，动作按钮的 toast 也要分流：不许再一律「后端原话（code N）」。"""
    assert "function errBrief" in HTML, "没有一行版的分流文案"
    left = HTML.count('code " + r.code')
    assert left == 1, "还有 %d 处动作在用旧格式（只该剩 fail() 里那一处）" % left
    for act in ("flag-save", "server-limit-save", "set-save", "login"):
        i = HTML.index('a === "%s"' % act)
        assert "errBrief" in HTML[i:i + 900], "%s 还在用旧格式" % act
