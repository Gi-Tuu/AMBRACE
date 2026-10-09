# -*- coding: utf-8 -*-
"""A39 影子读数脚本的守卫：尺子要先被量过，才准它去量生产。

最要防的一件事（本仓第四次撞同族）：**拿自己造的假行测自己的解析器**——那样正则怎么写都绿。
所以正对照的日志行由**生产侧同一个 `shadow_mark()`** 与 `freshness.py` 里那条 `_logger.info`
的格式串现生成；格式一分叉，这几条立刻红。
"""
import ast
import importlib.util
import io
import logging
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "freshness_shadow_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_frreport_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rpt = _load()


def _prod_format() -> str:
    """从 `freshness.py` 的 AST 里取那条 `_logger.info` 的格式串（字面量，不 eval 任何东西）。"""
    src = (REPO / "backend" / "app" / "scheduling" / "freshness.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "info"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and "A39 闸② channel=" in node.args[0].value):
            return node.args[0].value
    raise AssertionError("日志格式串没找到＝生产侧改过写法，这套读数已经读不到东西了")


def _prod_line(channel: str, verdict: str, reason: str, gate: bool, **extra) -> str:
    """按**生产同款**生成一行：格式串从源码 AST 现取，不在测试里重打一遍。"""
    from app.domain.proactivity.freshness import shadow_mark

    return ("2026-10-09 12:00:00 | INFO    | scheduler.freshness | "
            + (_prod_format() % (channel, "" if gate else "（影子）",
                                 shadow_mark(verdict, reason, extra or None))))


def test_正对照走生产格式():
    line = _prod_line("life_regression", "cancel", "依据的事实已失效", gate=False)
    rows = rpt.parse_lines([line])
    assert len(rows) == 1, f"生产格式的行解析不出来＝尺子与日志已分叉：{line}"
    r = rows[0]
    assert (r["channel"], r["verdict"], r["reason"], r["mode"]) == \
        ("life_regression", "cancel", "依据的事实已失效", "影子"), r
    assert r["day"] == "2026-10-09"


def test_实拦档与影子档必须分得开():
    rows = rpt.parse_lines([_prod_line("moment_comment", "keep", "现状未变", gate=True)])
    assert len(rows) == 1 and rows[0]["mode"] == "实拦", rows
    # 反向钉：不是闸②的行一条都不许算进来
    assert rpt.parse_lines(["2026-10-09 01:00:00 | INFO | other | 无关行"]) == []
    assert rpt.parse_lines(["A39 闸② channel=x（影子） 手滑没打中括号"]) == []


def test_extra字段要进读数不能只报总数():
    rows = rpt.parse_lines([_prod_line("moment_comment", "keep", "现状未变", gate=True,
                                       **{"快照": 3, "现状": 7})])
    assert rows[0]["extras"] == {"快照": "3", "现状": "7"}, rows[0]


def test_summarize必须是纯函数():
    """不读库、不读时钟——窗口由 CLI 层算好传进来（否则判效会跟着本机日历漂）。"""
    fn = next(n for n in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8")))
              if isinstance(n, ast.FunctionDef) and n.name == "summarize")
    src = ast.unparse(fn)
    assert "datetime" not in src and "sqlite" not in src and "open(" not in src, src[:220]
    assert "utcnow" not in src and "time()" not in src


def test_分档统计与cancel率算得对():
    rows = rpt.parse_lines([
        _prod_line("timer_general", "cancel", "等待结果已兑现", gate=False),
        _prod_line("timer_general", "keep", "现状未变", gate=False),
        _prod_line("timer_general", "keep", "现状未变", gate=False),
    ])
    s = rpt.summarize(rows, days=5, min_samples=1)
    d = s["channels"]["timer_general"]
    assert d["n"] == 3 and d["cancel"] == 1 and d["keep"] == 2
    assert abs(d["cancel_rate"] - 1 / 3) < 1e-9, d
    assert d["reasons"] == {"等待结果已兑现": 1}, d
    assert d["shadow"] == 3 and d["gate"] == 0


def test_零读数必须拒绝下结论():
    s = rpt.summarize([], days=5, min_samples=8)
    text = rpt.report(s, [])
    assert s["total"] == 0 and s["channels"] == {}
    assert "别据此下结论" in text, text
    assert "实闸若开" not in text, "一条读数都没有却给了方向＝尺子自己在编话"


def test_样本不足时只报数不给建议():
    rows = rpt.parse_lines([_prod_line("unfinished_topic", "cancel", "用户已接着说过（旧话题不必复述）",
                                       gate=False)])
    s = rpt.summarize(rows, days=5, min_samples=8)
    assert s["channels"]["unfinished_topic"]["enough"] is False
    assert "实闸若开" not in rpt.report(s, []), "样本不足还给拨闸建议"
    # 反向钉：够数了就一定要给方向，否则这条判读永远不出声
    s2 = rpt.summarize(rows * 8, days=5, min_samples=8)
    assert s2["channels"]["unfinished_topic"]["enough"] is True
    assert "实闸若开" in rpt.report(s2, []), "够了样本数却不判读＝这套读数永远不会有用"


def test_延迟触发留痕单独一档且不归影子闸():
    rows = rpt.parse_lines([_prod_line("life_regression", "keep", "现状未变", gate=False)])
    delay = [{"rule": "anger_high", "char": "13"}]
    text = rpt.report(rpt.summarize(rows, 5, 1), delay)
    assert "通道 3" in text and "anger_high" in text
    assert "不归影子闸管" in rpt.report(rpt.summarize([], 5, 1), []), "0 次时要把口径说清"


def test_表外档位必须冒出来():
    rows = rpt.parse_lines([_prod_line("moment_comment", "sideways", "有人新加了一档", gate=False)])
    s = rpt.summarize(rows, 5, 1)
    assert s["unknown_verdicts"] == ["sideways"], s
    assert "先修尺子" in rpt.report(s, [])
