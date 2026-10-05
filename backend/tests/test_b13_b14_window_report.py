"""B13/B14 判效脚本的算术守卫（`scripts/diagnostics/b13_b14_window_report.py`，只读脚本）。

为什么一个"打印用"的脚本要配单测：10-07 / 10-08 到点就是拿它的输出判效，
**数字算错了没人会发现**（乘子计数、跨轮一致率、按共识写回会动几条）。
所以把「聚合」与「打印」拆开（`collect_b13` / `collect_b14` 返回 dict），这里用临时库钉死算术。

纪律：全程临时库（`tmp_path`）＋自建最小 schema，绝不连生产库；
脚本自身只用 `mode=ro`，本测试连的是它接进来的临时文件。
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "b13_b14_window_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_winrep_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wr = _load()

SINCE = "2026-01-01 00:00:00"


def _db(tmp_path) -> sqlite3.Connection:
    p = tmp_path / "win.db"
    con = sqlite3.connect(str(p))
    con.execute(
        "CREATE TABLE agent_task_logs (id INTEGER PRIMARY KEY, character_id INTEGER,"
        " trigger TEXT, route TEXT, steps_json TEXT, status TEXT, created_at TEXT)"
    )
    return con


def _ins(con, route, char, payload, at="2026-10-05 03:00:00", raw=None):
    con.execute(
        "INSERT INTO agent_task_logs (character_id, trigger, route, steps_json, status, created_at)"
        " VALUES (?, 'memory_obs', ?, ?, 'ok', ?)",
        (char, route, raw if raw is not None else json.dumps(payload), at),
    )


# ─────────────────────────── B13 ───────────────────────────
def test_B13_乘子分档与触闸计数按条算(tmp_path):
    con = _db(tmp_path)
    for _ in range(3):
        _ins(con, wr.DRIVE_ROUTE, 13, {"multiplier": 1.0, "bias": 0.0, "drive_key": "longing"})
    _ins(con, wr.DRIVE_ROUTE, 11, {"multiplier": 1.1, "bias": 0.1})     # 性格偏置把乘子顶到 1.1
    _ins(con, wr.DRIVE_ROUTE, 12, {"multiplier": 0.8, "bias": -0.1})    # 贴总闸下沿
    s = wr.collect_b13(con, SINCE)
    assert s["n"] == 5 and s["identity"] == 3, s
    assert s["floor_hit"] == 1 and s["ceil_hit"] == 0, s   # 1.1 ≠ 上沿 1.25，不算贴闸
    assert s["bias"].get(0.1) == 1 and s["bias"].get(-0.1) == 1, s
    con.close()


def test_B13_坏JSON单独计数不被当成没有信号(tmp_path):
    """截断/坏字节行如果静默跳过，「触顶 0 次」就可能是「样本丢了」而不是「真的没触顶」。"""
    con = _db(tmp_path)
    _ins(con, wr.DRIVE_ROUTE, 13, {"multiplier": 1.0, "bias": 0.0})
    _ins(con, wr.DRIVE_ROUTE, 13, None, raw='{"multiplier": 0.8, "bias"')   # 半截 JSON
    s = wr.collect_b13(con, SINCE)
    assert s["n"] == 2 and s["bad_json"] == 1, s
    assert sum(s["mult"].values()) == 1, "坏行没被计入 bad_json 就等于丢样本"
    con.close()


def test_B13_按角色给偏置构成_不给条数比例(tmp_path):
    """偏置是角色固有属性：同一角色重复几千次不能当成几千次独立观测。"""
    con = _db(tmp_path)
    for _ in range(40):
        _ins(con, wr.DRIVE_ROUTE, 11, {"multiplier": 1.1, "bias": 0.1})
    for _ in range(40):
        _ins(con, wr.DRIVE_ROUTE, 13, {"multiplier": 1.0, "bias": 0.0})
    s = wr.collect_b13(con, SINCE)
    assert set(s["char_bias"]) == {11, 13}
    assert s["char_bias"][11][0.1] == 40 and s["char_bias"][13][0.0] == 40
    con.close()


def test_B13_窗口外的行不进来(tmp_path):
    con = _db(tmp_path)
    _ins(con, wr.DRIVE_ROUTE, 13, {"multiplier": 1.0, "bias": 0.0}, at="2025-12-01 00:00:00")
    _ins(con, wr.DRIVE_ROUTE, 13, {"multiplier": 1.0, "bias": 0.0}, at=SINCE)
    assert wr.collect_b13(con, SINCE)["n"] == 1     # 边界（等于起算点）算在内
    con.close()


# ─────────────────────────── B14 ───────────────────────────
def _rounds_batch(con, rounds, consensus, char=13):
    _ins(con, wr.RATING_ROUTE, char, {"rounds": rounds, "consensus": consensus,
                                      "vote3_rounds_used": len(rounds), "candidate_count": len(rounds[0])})


def test_B14_一致率与分歧率算得对(tmp_path):
    con = _db(tmp_path)
    # 4 条记忆：前 3 条三轮同星；第 4 条 3/5/4 且共识≠第 1 轮
    _rounds_batch(con, [{"1": 3, "2": 4, "3": 5, "4": 3},
                        {"1": 3, "2": 4, "3": 5, "4": 5},
                        {"1": 3, "2": 4, "3": 5, "4": 4}],
                  {"1": 3, "2": 4, "3": 5, "4": 4})
    s = wr.collect_b14(con, SINCE)
    assert s["batches"] == 1 and s["mem_total"] == 4, s
    assert s["agree_all"] == 3 and s["diverge"] == 1, s
    assert s["flip_items"] == 1 and s["flip_batches"] == 1, s     # 按共识写回会动 1 条、1 批
    assert abs(s["star_gap_sum"] - 1.0) < 1e-9, s                 # 3 → 4 差一星
    con.close()


def test_B14_只有一轮的批次不进入分母(tmp_path):
    """干跑中途失败只留一轮时，「跨轮一致」无从谈起——算进去会把一致率虚抬。"""
    con = _db(tmp_path)
    _rounds_batch(con, [{"1": 3}], {"1": 3})
    s = wr.collect_b14(con, SINCE)
    assert s["batches"] == 1 and s["mem_total"] == 0, s
    con.close()


def test_B14_截断的留痕进unparsable而不是静默丢(tmp_path):
    con = _db(tmp_path)
    _ins(con, wr.RATING_ROUTE, 13, None, raw='{"rounds": [{"1": 3}, {"1": 4}], "consensu')
    _ins(con, wr.RATING_ROUTE, 13, {"outcome": "rated", "stars": {"1": 3}})   # 没干跑的普通批次
    s = wr.collect_b14(con, SINCE)
    assert s["unparsable"] == 1 and s["batches"] == 0, s
    con.close()


def test_B14_贴上限的批次要报数_防候选翻倍后悄悄截断(tmp_path):
    con = _db(tmp_path)
    big = json.dumps({"rounds": [{"%d" % i: 3 for i in range(200)}] * 3, "consensus": {}})
    assert len(big) >= wr.TRACE_STEPS_MAX - 5, "样本没造到接近上限，测不出这个报警"
    _ins(con, wr.RATING_ROUTE, 13, None, raw=big)
    s = wr.collect_b14(con, SINCE)
    assert s["near_cap"] == 1, s
    con.close()


# ─────────────────────────── 公共小工具 ───────────────────────────
def test_百分比与置信区间的零分母不炸(tmp_path):
    assert wr._pct(0, 0) == "-"
    assert wr._wilson_ci(0, 0) == "-"
    lo, hi = wr._wilson_ci(33, 40).replace("[", "").replace("]", "").replace("%", "").split(", ")
    assert float(lo) <= 82.5 <= float(hi), wr._wilson_ci(33, 40)
    assert wr._wilson_ci(0, 40).startswith("[0.0%"), wr._wilson_ci(0, 40)


def test_打印函数不抛且空窗口也出得来(tmp_path, capsys):
    con = _db(tmp_path)
    s13 = wr.report_b13(con, SINCE)
    s14 = wr.report_b14(con, SINCE)
    out = capsys.readouterr().out
    assert s13["n"] == 0 and s14["n_rows"] == 0
    assert "无样本" in out and "无留痕" in out
    con.close()
