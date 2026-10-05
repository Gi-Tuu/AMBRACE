"""A30-②：`extract_cal_note` / `parse_actions` 的评测基准日入参守卫（2026-10-05 立）。

要解决的坑：日历备注标记支持「今天/明天/后天」与省略日期，换算基准一直是**本机北京时钟**。
评测集（0-4）每道题带 `time_anchor`（如 2026-03-10），若 J1 直接拿生产解析结果判日期，
同一道题**今天绿、后天红**——与本项目反复出现的「测试依赖本机环境」同族（码页、绝对路径、
平台专属分支），这是第四种：**跟着日历走的断言**。

口径选择（用户 10-05 拍为②）：不动生产语义，加一个缺省为 None 的关键字参数。
所以本文件最重要的一条是**默认值必须逐字节维持旧行为**——生产调用点没传参，
它们的输出不能有任何变化。畸形锚点则必须炸（静默退回本机时钟＝尺子跟着日历走还看着正常）。
"""
import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.agent.actions import AgentAction, extract_cal_note, parse_actions

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def _bj_now() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


# ── 1. 默认值＝旧行为（逐字节） ─────────────────────────────────────────
@pytest.mark.parametrize("raw,expect_offset", [("今天去接孩子", 0), ("明天去接孩子", 1), ("后天去接孩子", 2)])
def test_不传基准日时相对词仍按本机北京时间(raw, expect_offset):
    got = extract_cal_note(f"[CAL_NOTE]{raw}[/CAL_NOTE]")
    assert got is not None
    assert got[0] == (_bj_now() + timedelta(days=expect_offset)).isoformat(), got
    assert extract_cal_note(f"[CAL_NOTE]{raw}[/CAL_NOTE]", base_date=None) == got, \
        "显式传 None 与不传必须完全等价（否则生产调用点的等价性没被证明）"


def test_不传基准日时省略日期等于今天_与旧实现同式():
    got = extract_cal_note("【CAL_NOTE】提醒喝水【/CAL_NOTE】")
    assert got is not None and got[0] == _bj_now().isoformat(), got


def test_绝对日期头不受基准日影响():
    """绝对日期本来就不需要换算：两种基准必须给出同一结果（防我把手改歪）。"""
    for base in (None, "2026-03-10"):
        assert extract_cal_note("[CAL_NOTE]2026-03-15 接孩子[/CAL_NOTE]", base_date=base) == \
            ("2026-03-15", "接孩子")


# ── 2. 传锚点时按锚点换算 ───────────────────────────────────────────────
@pytest.mark.parametrize("word,off", [("今天", 0), ("明天", 1), ("后天", 2)])
def test_相对词按锚点换算(word, off):
    anchor = date(2026, 3, 10)
    got = extract_cal_note(f"[CAL_NOTE]{word}接孩子[/CAL_NOTE]", base_date="2026-03-10")
    assert got is not None
    assert got[0] == (anchor + timedelta(days=off)).isoformat(), got
    assert got[1] == "接孩子", got


def test_省略日期时锚点当天():
    assert extract_cal_note("【CAL_NOTE】交周报【/CAL_NOTE】", base_date="2026-03-10")[0] == "2026-03-10"


def test_阳性对照_同一句明天在两个基准下必然不同():
    """少了这条，「参数根本没生效」也能骗过上面所有用例。

    锚点由本机日期**倒推 37 天**而不是写死常量：写死的话，跑到锚点那天这条会自己变红——
    而那正是本文件要消灭的病，守卫自己不能跟着日历走。
    """
    far = _bj_now() - timedelta(days=37)
    a = extract_cal_note("[CAL_NOTE]明天接孩子[/CAL_NOTE]", base_date=far.isoformat())[0]
    b = extract_cal_note("[CAL_NOTE]明天接孩子[/CAL_NOTE]")[0]              # 本机时钟
    assert a == (far + timedelta(days=1)).isoformat(), (far, a)
    assert a != b, "锚点与本机时钟给出同一个日期 ⇒ base_date 没生效"


def test_畸形基准日必须炸不能静默退回本机():
    """非日期串一律 ValueError：评测传了坏锚点却静默退回本机时钟＝分数跟着日历走。"""
    for bad in ("明天", "20260310", "2026-03-10x", "", "  ", "2026-13-01", "2026-02-30"):
        with pytest.raises(ValueError, match="base_date"):
            extract_cal_note("[CAL_NOTE]明天接孩子[/CAL_NOTE]", base_date=bad)


def test_月日不补零也能解析_这是实际行为不是漏洞():
    """`strptime` 对 `2026-3-10` 是**宽松**的（我第一版把它当畸形值，断言写错了）。

    宽松只影响输入写法，不影响结果确定性：它照样解析出 2026-03-10，不会退回本机时钟。
    写在这里是为了让下一个读代码的人别把它当 bug 改掉。
    """
    assert extract_cal_note("[CAL_NOTE]明天接孩子[/CAL_NOTE]", base_date="2026-3-10")[0] == "2026-03-11"


# ── 3. parse_actions 透传 ───────────────────────────────────────────────
def test_parse_actions把基准日透传给日历备注():
    far = _bj_now() - timedelta(days=37)
    acts = parse_actions("好。[CAL_NOTE]明天 接孩子[/CAL_NOTE]", base_date=far.isoformat())
    cal = [a for a in acts if a.action_type == "CAL_NOTE"]
    assert cal and cal[0].payload["date"] == (far + timedelta(days=1)).isoformat(), cal
    default_acts = parse_actions("好。[CAL_NOTE]明天 接孩子[/CAL_NOTE]")
    assert default_acts[0].payload["date"] == (_bj_now() + timedelta(days=1)).isoformat(), \
        "不传基准日时必须仍按本机时钟给出明天"


# ── ④ 零行为变化守卫：生产码里不许有人偷偷传基准日 ───────────────────────
def test_生产调用点一律不传基准日():
    """`base_date` 只该由评测传。生产里一旦出现，就等于把「今天」的定义从时钟改成常量。

    用 AST 核（不靠 grep 数文本）：`app/` 下任何 `extract_cal_note(...)` / `parse_actions(...)`
    调用都不得带 `base_date` 关键字。
    **例外＝定义它自己的模块** `app/agent/actions.py`：`parse_actions` 要把参数透传给
    `extract_cal_note`，那是这条链的实现本身，不是生产调用点（第一版没排它，自己咬了自己一次）。
    """
    self_module = (APP_DIR / "agent" / "actions.py").resolve()
    offenders = []
    for py in APP_DIR.rglob("*.py"):
        if py.resolve() == self_module:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            if name in ("extract_cal_note", "parse_actions") and \
                    any(k.arg == "base_date" for k in node.keywords):
                offenders.append(f"{py.relative_to(APP_DIR.parent)}:{node.lineno}")
    assert not offenders, f"生产码传了 base_date（这个参数只归评测用）：{offenders}"


def test_生产调用点仍按单参数位置调用_加参不会打断它们():
    """`base_date` 是 keyword-only：既有生产调用（`extract_cal_note(text)`）不受影响。"""
    callers = []
    for py in APP_DIR.rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        if "extract_cal_note(" in src or "parse_actions(" in src:
            callers.append(py.name)
    assert callers, "生产里已经没人调这两个函数了？那这条守卫要改成别的锚"
    # 单参数位置调用必须照常工作
    assert extract_cal_note("[CAL_NOTE]2026-03-15 接孩子[/CAL_NOTE]") == ("2026-03-15", "接孩子")
    assert isinstance(parse_actions("[MEMO]朵朵[/MEMO]"), list)
