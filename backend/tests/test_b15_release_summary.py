# -*- coding: utf-8 -*-
"""B15 判效读数（`summarize_b15`）守卫——纯函数，全部用合成留痕，不碰库不调模型。

写这批断言时的自律：每条都要能给出**反例**（⑫），并保证「样本不足」永远不被读成「通过」。
"""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "b13_b14_window_report.py"

_spec = importlib.util.spec_from_file_location("_b13145_under_test", str(SCRIPT))
wr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wr)


def _open(level_before: float, level_after: float, *, drive: str = "curiosity", ratio=None):
    return {"kind": "open", "drive": drive, "level_before": level_before,
            "level_after": level_after, "ratio": 0.45 if ratio is None else ratio}


def _full(level_before: float, level_after: float, *, msg_id=9001, drive: str = "longing"):
    return {"kind": "full", "drive": drive, "level_before": level_before,
            "level_after": level_after, "ratio": 1.0, "attributed_msg_id": msg_id}


def _many_open(n: int):
    return [_open(0.8, 0.5, drive="d%d" % (i % 5)) for i in range(n)]


def _many_full(n: int):
    return [_full(0.9, 0.0, msg_id=7000 + i, drive="f%d" % (i % 5)) for i in range(n)]


# ───────────── ① 方向判据：能给出反例才算有牙 ─────────────
def test_开口释放水位没降就是缺陷_不能被算成正常():
    s = wr.summarize_b15([_open(0.5, 0.5), _open(0.5, 0.7)])
    assert s["n_open"] == 2 and s["open_ok"] == 0 and len(s["open_bad"]) == 2, s
    # 只有两条 ⇒ 先报样本不足，但坏行必须已经被点名（否则到点了也看不出问题）
    assert "样本不足" in s["verdict"]
    assert s["open_bad"][0]["before"] == 0.5 and s["open_bad"][1]["after"] == 0.7


def test_全额释放未清零必须进反例():
    s = wr.summarize_b15([{"kind": "full", "drive": "x", "level_before": 0.9,
                           "level_after": 0.3, "ratio": 1.0, "attributed_msg_id": 1}])
    assert s["full_ok"] == 0 and len(s["full_bad"]) == 1, s


def test_全额释放缺归属同样算反例():
    s = wr.summarize_b15([_full(0.9, 0.0, msg_id=None)])
    assert s["full_ok"] == 0 and len(s["full_bad"]) == 1 and s["full_missing_attr"] == 1, s


def test_两档都齐且方向对才给正面结论():
    rows = _many_open(12) + _many_full(10)
    s = wr.summarize_b15(rows)
    assert s["n"] == 22 and s["enough"] is True, s
    assert s["open_ok"] == 12 and s["full_ok"] == 10 and not s["open_bad"] and not s["full_bad"]
    assert "方向成立" in s["verdict"], s["verdict"]


def test_样本不足永远不许读成通过():
    s = wr.summarize_b15(_many_open(19))          # 差一条到阈值
    assert s["enough"] is False and "样本不足" in s["verdict"], s
    s2 = wr.summarize_b15(_many_open(20))         # 到阈值，但只有开口一档
    assert s2["enough"] is True and "只攒到一档" in s2["verdict"], s2["verdict"]


def test_有缺陷优先于只攒一档():
    rows = _many_open(15) + [_open(0.4, 0.4)] + _many_full(4)
    s = wr.summarize_b15(rows)
    assert len(s["open_bad"]) == 1 and "有缺陷" in s["verdict"], s["verdict"]


def test_坏载荷计入_bad_rows_不污染两档计数():
    s = wr.summarize_b15([{"kind": "open", "drive": "x", "level_before": None, "level_after": None},
                          {"kind": "weird"}, _open(0.6, 0.2)])
    assert s["bad_rows"] == 2 and s["n_open"] == 1 and s["open_ok"] == 1, s


def test_按_drive_统计与触发点计数不吃样本数():
    s = wr.summarize_b15(_many_open(5) + _many_full(5))
    assert s["n"] == 10 and s["by_drive"] and sum(s["by_drive"].values()) == 10, s


# ───────────── ② 算术必须是纯函数（⑨）＋ collect 只读 ─────────────
def test_summarize是纯函数_不许碰库和时间():
    src = inspect.getsource(wr.summarize_b15)
    for banned in ("sqlite", "conn", "utcnow", "now(", "datetime", "print("):
        assert banned not in src, "summarize_b15 里出现 %s ⇒ 算术不再可离线复算" % banned


def test_collect_b15_只读_不改库():
    src = inspect.getsource(wr.collect_b15)
    assert "select" in src.lower()
    for banned in ("insert", "update ", "delete ", "commit"):
        assert banned not in src.lower(), "collect_b15 里出现写操作 %s" % banned


def test_阈值与台账一致():
    """20 这个数字同时写在 docs/plans.md 的 B15 行里；改一处必须改两处，别留静默分歧。"""
    assert wr.RELEASE_MIN_SAMPLES == 20
    plan = (REPO / "docs" / "plans.md")
    if not plan.exists():                       # 脱敏快照没有 docs/（CI 跑裁剪树）⇒ 跳过但要能自证跳过
        import pytest
        pytest.skip("裁剪树里没有 docs/plans.md")
    text = plan.read_text(encoding="utf-8-sig")
    row = [l for l in text.split("\n") if l.startswith("| B15 |")]
    assert row, "plans 里找不到 B15 行"
    assert "≥20" in row[0] or "**≥20" in row[0], "B15 行的阈值表述变了，脚本与台账要对齐"
