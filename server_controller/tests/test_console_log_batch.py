"""日志页结构化批闸门（2026-11-08）。

需求：分来源 + 级别筛选 + 面向普通用户的「易读版」视图。
硬约束：**不编造日志内容**。易读版只翻译结构（时间/级别/模块/消息四段）与已知术语，
消息原文一字不改地保留，堆栈默认折叠。
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


def _run(tmp_path, name, body, bundle):
    html = shell.build_shot_html("log", bundle, theme="dark")
    probe = """<script>window.addEventListener('load',function(){setTimeout(async function(){
      const out={}; try{ %s }catch(e){out.error=String(e&&e.message||e);}
      const d=document.createElement('div');d.id='logProbe';d.textContent=JSON.stringify(out);
      document.body.appendChild(d);},300);});</script></body>""" % body
    html = html.replace("</body>", probe, 1)
    f = os.path.join(WEB, "_log_%s.html" % name)
    with open(f, "w", encoding="utf-8") as h:
        h.write(html)
    try:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                            "--window-size=1360,880", "--temp-profile=olog",
                            "--virtual-time-budget=5000",
                            "file:///" + f.replace("\\", "/")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stderr[-300:]
        m = re.search(r'id="logProbe">(.*?)</div>', r.stdout, re.S)
        assert m, "探针没跑起来"
        return json.loads(m.group(1).replace("&quot;", '"'))
    finally:
        if os.path.exists(f):
            os.remove(f)


# 造一份确定性的示意数据：两来源、四级别、一条带堆栈的 ERROR
def _log_data():
    return {
        "text": "（原始全文，供导出）", "empty": False,
        "hint": "还没有日志写入（app.log / server_stderr.log）",
        "paths": {"app": "D:/app.log", "stderr": "D:/server_stderr.log"},
        "sources": [{"key": "app", "label": "应用日志", "count": 3},
                    {"key": "stderr", "label": "服务器错误输出", "count": 2}],
        "levels": [{"key": "ERROR", "label": "出错", "count": 1},
                   {"key": "WARNING", "label": "提醒", "count": 1},
                   {"key": "INFO", "label": "日常", "count": 3}],
        "summary": "最近 5 行：1 条出错、1 条提醒、3 条日常记录",
        "rows": [
            {"source": "app", "level": "INFO", "time": "11:35:32", "module": "scheduler.arbiter",
             "msg": "Proactive msg char=11 skipped: inactive char", "raw": "FULL-RAW-1", "cont": False,
             "easy": "定时任务 · 主动消息跳过（角色长时间没互动）"},
            {"source": "app", "level": "ERROR", "time": "11:35:33", "module": "asyncio",
             "msg": "Task exception was never retrieved", "raw": "FULL-RAW-2", "cont": False,
             "easy": "异步任务 · 有一个任务抛了异常没被处理"},
            {"source": "app", "level": "TRACE", "time": "", "module": "",
             "msg": "  File \"events.py\", line 94, in _run_once", "raw": "FULL-RAW-3", "cont": True,
             "easy": ""},
            {"source": "app", "level": "WARNING", "time": "11:35:34", "module": "llm.client",
             "msg": "retry 1/3 after timeout", "raw": "FULL-RAW-4", "cont": False,
             "easy": "模型调用 · 超时后重试（第 1 次）"},
            {"source": "stderr", "level": "INFO", "time": "11:35:32", "module": "",
             "msg": "Memory review char=6 skipped", "raw": "FULL-RAW-5", "cont": False,
             "easy": "记忆整理跳过（当日额度已用完）"},
        ],
    }


# ───── ① 后端结构化：来源 / 级别 / 续行 ─────
def test_gate_log_page_returns_structured_rows(monkeypatch):
    import console_api as api
    import console_core as sc

    parts = {
        "app": "\n".join([
            "2026-11-08 11:35:32 | INFO    | scheduler.arbiter | Proactive msg skipped",
            "2026-11-08 11:35:33 | ERROR   | asyncio | Task exception was never retrieved",
            "Traceback (most recent call last):",
            '  File "events.py", line 94, in _run_once',
            "2026-11-08 11:35:34 | WARNING | llm.client | retry 1/3",
        ]),
        "stderr": "\n".join([
            "11:35:32 | INFO | Memory review skipped",
            "11:35:33 | ERROR | RuntimeError: boom",
        ]),
    }
    monkeypatch.setattr(sc, "_tail_log_parts", lambda *a, **k: parts)
    monkeypatch.setattr(sc, "_tail_log", lambda *a, **k: "\n".join(parts.values()))

    d = api.call("log_page")["data"]
    rows = d["rows"]
    assert len(rows) == 7, "行数不对：%s" % rows
    assert {r["source"] for r in rows} == {"app", "stderr"}, "没分来源：%s" % rows
    assert [r["level"] for r in rows].count("ERROR") == 2, "级别没解析出来：%s" % rows
    assert rows[2]["cont"] is True, "Traceback 首行没标续行：%s" % rows[2]
    assert rows[3]["cont"] is True, "缩进的 File 行没标续行：%s" % rows[3]
    assert rows[1]["module"] == "asyncio", "模块没拆出来：%s" % rows[1]
    assert rows[5]["time"] == "11:35:32", "stderr 的时间没拆出来：%s" % rows[5]
    assert rows[0]["msg"] == "Proactive msg skipped", "消息被改写了：%s" % rows[0]
    assert {x["key"] for x in d["levels"]} >= {"ERROR", "WARNING", "INFO"}
    assert {x["key"] for x in d["sources"]} == {"app", "stderr"}
    assert "出错" in d["summary"], "没有人话摘要：%s" % d["summary"]


# ───── ② 易读版：中文级别 / 来源分组 / 堆栈折叠 / 原文保留 ─────
@has_chrome
def test_gate_easy_view_has_chinese_levels_and_folds_trace(tmp_path):
    got = _run(tmp_path, "easy", """
      const v = document.querySelector('[data-act="log-view"][data-v="easy"]');
      if (v) { v.click(); await new Promise(r => setTimeout(r, 120)); }
      const rows = Array.from(document.querySelectorAll('.logrow'));
      out.n = rows.length;
      out.levels = rows.map(r => (r.querySelector('.lv')||{}).textContent || '');
      out.sources = Array.from(document.querySelectorAll('.logsrc')).map(e => e.textContent);
      out.trace = document.querySelectorAll('.logrow.trace').length;
      out.folded = document.querySelectorAll('details.logstack').length;
      out.raws = Array.from(document.querySelectorAll('.logrow')).map(r => r.dataset.raw || '');
      out.easy = rows.map(r => (r.querySelector('.ez')||{}).textContent || '');
    """, {"log_page": {"ok": True, "data": _log_data()}})
    assert got.get("n", 0) >= 4, "易读版没渲染：%s" % got
    assert any("出错" in x for x in got.get("levels", [])), "级别没翻成人话：%s" % got.get("levels")
    assert len(got.get("sources", [])) >= 2, "没按来源分组：%s" % got.get("sources")
    assert got.get("folded", 0) >= 1, "堆栈没折叠：%s" % got
    assert any("FULL-RAW" in x for x in got.get("raws", [])), "原文没保留：%s" % got.get("raws")
    assert got.get("trace", 0) == 0, "续行被当成独立一条了：%s" % got.get("trace")


# ───── ③ 级别筛选 ─────
@has_chrome
def test_gate_level_filter_keeps_only_that_level(tmp_path):
    got = _run(tmp_path, "lv", """
      const v = document.querySelector('[data-act="log-view"][data-v="easy"]');
      if (v) { v.click(); await new Promise(r => setTimeout(r, 100)); }
      const b = document.querySelector('[data-act="log-level"][data-k="ERROR"]');
      if (b) { b.click(); await new Promise(r => setTimeout(r, 120)); }
      out.rows = Array.from(document.querySelectorAll('.logrow')).map(r => r.dataset.level);
      out.active = (document.querySelector('[data-act="log-level"].on')||{}).dataset || {};
    """, {"log_page": {"ok": True, "data": _log_data()}})
    assert got.get("rows") and set(got["rows"]) == {"ERROR"}, "级别筛选没生效：%s" % got


# ───── ④ 来源筛选 ─────
@has_chrome
def test_gate_source_filter_keeps_only_that_source(tmp_path):
    got = _run(tmp_path, "src", """
      const v = document.querySelector('[data-act="log-view"][data-v="easy"]');
      if (v) { v.click(); await new Promise(r => setTimeout(r, 100)); }
      const b = document.querySelector('[data-act="log-source"][data-k="stderr"]');
      if (b) { b.click(); await new Promise(r => setTimeout(r, 120)); }
      out.rows = Array.from(document.querySelectorAll('.logrow')).map(r => r.dataset.source);
    """, {"log_page": {"ok": True, "data": _log_data()}})
    assert got.get("rows") and set(got["rows"]) == {"stderr"}, "来源筛选没生效：%s" % got


# ───── ⑤ 原始视图仍在，副标题不再写死「各 40 行」 ─────
@has_chrome
def test_gate_raw_view_still_available(tmp_path):
    got = _run(tmp_path, "raw", """
      const v = document.querySelector('[data-act="log-view"][data-v="raw"]');
      if (v) { v.click(); await new Promise(r => setTimeout(r, 120)); }
      out.hasPre = !!document.getElementById('logBox');
      out.preText = (document.getElementById('logBox')||{}).textContent || '';
    """, {"log_page": {"ok": True, "data": _log_data()}})
    assert got.get("hasPre"), "开发者的原始视图没了：%s" % got


def test_gate_log_subtitle_describes_the_new_capabilities():
    i = HTML.index('log: ["运行日志"')
    seg = HTML[i:i + 160]
    assert "末尾各 40 行" not in seg, "副标题还在写死 40 行：%s" % seg
    assert "来源" in seg or "筛选" in seg or "易读" in seg, "副标题没说明新能力：%s" % seg


# ───── ⑥ 不编造：中文旁注只复述已知句式、数字全部取自原文 ─────
def test_gate_easy_hint_never_invents_facts():
    import console_api as api
    h = api._easy_hint("INFO", "scheduler.memory_review",
                       "Memory review char=6 skipped: nostalgia daily limit")
    assert "6" in h and "回忆整理" in h and "额度已用完" in h, h
    assert "·" not in h, "还在词对词硬拼：%s" % h          # 不许再出现拼词分隔符
    assert "（scheduler）" not in h, "模块名重复写进旁注了：%s" % h

    # 未知句式：留空，由前端显示原文，绝不猜一句中文
    assert api._easy_hint("INFO", "misc", "totally unknown payload xyz=42") == ""
    # 出错级别才带「出错：」前缀
    assert api._easy_hint("ERROR", "asyncio", "Task exception was never retrieved").startswith("出错：")
    # 数字必须来自原文，不能自己编一个
    h2 = api._easy_hint("INFO", "scheduler.arbiter", "Proactive msg char=9318 skipped: inactive char")
    assert "9318" in h2 and "9319" not in h2, h2
