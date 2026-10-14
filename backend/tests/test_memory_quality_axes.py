# -*- coding: utf-8 -*-
"""A30 五轴离线报表（`scripts/diagnostics/memory_quality_axes.py`）单测——**纯合成 tmp_path 数据**。

覆盖派单点名的八件事：三跑稳／不稳、缺轴、字段错、豁免与未豁免、源指纹不一致／丢跑、
旧逐字读数保留、输入不被改且无 LLM/网络、零样本 unknown。
10-14 补的完整性逐跑化：三跑缺任一趟 fp＝整题不可比、任一趟动作↔载荷配不上对＝该跑 unknown
不进分母、截断载荷只作下界、S5 坏行不默认 0 条、`--map-out` 私有派生物拒绝写进仓库。

刻意不做的事：不碰生产留底（真实衍生题集不进本夹具）、不连库、不打网络。
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts" / "diagnostics"
MODULE_PATH = os.environ.get("A30_AXES_SCRIPT") or str(SCRIPT_DIR / "memory_quality_axes.py")

sys.path.insert(0, str(SCRIPT_DIR))
import memory_action_eval as ruler                                    # noqa: E402

_spec = importlib.util.spec_from_file_location("memory_quality_axes", MODULE_PATH)
ax = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ax)          # 变异测试经 A30_AXES_SCRIPT 指向内存外副本，判据实现仍咬原文件


def sig(types=(), payloads=(), chan=(), dates=(), exempt=(), ok=True):
    """用**尺子自己的** `run_sig_text` 渲染逐跑指纹（自己拼串＝口径漂移）。"""
    return ruler.run_sig_text({"pass": ok, "actions_seen": list(types), "dates_seen": list(dates),
                               "exemptions": list(exempt), "payloads": list(payloads),
                               "mem_channel": list(chan)})


def run(types=(), payloads=(), chan=(), dates=(), exempt=(), ok=True, err="", fp="fp-stable"):
    return {"types": list(types), "payloads": list(payloads), "chan": list(chan),
            "dates": list(dates), "exempt": list(exempt), "ok": ok, "err": err, "fp": fp,
            "sig": sig(types, payloads, chan, dates, exempt, ok)}


def make_row(cid, runs, n_runs=None, dup_other=False):
    """把合成跑折成生成侧留底的形状（字段名与 `--emit-rows` 逐字同名）。"""
    first, others = runs[0], runs[1:]
    sigs = [r["sig"] for r in runs]
    row = {"cid": cid, "judge": "J1", "pass": first["ok"], "err": first["err"],
           "actions_seen": first["types"], "payloads": first["payloads"],
           "mem_channel": first["chan"], "dates_seen": first["dates"],
           "exemptions": first["exempt"], "reply_text": "首跑正文",
           "sig": first["sig"], "n_runs": n_runs if n_runs is not None else len(runs),
           "pass_all": all(r["ok"] for r in runs),
           "consistent": len({ruler.mask_clock(s) for s in sigs}) == 1,
           "err_spread": "/".join(sorted({r["err"] or "过" for r in runs})),
           "prompt_fp_stable": first["fp"], "fp_spread": sorted({r["fp"] for r in runs}),
           "other_runs": [{"pass": o["ok"], "err": o["err"],
                           "actions": ",".join(o["types"]) or "无",
                           "dates": ",".join(o["dates"]) or "无日期",
                           "chan": " ‖ ".join(o["chan"]) or "无",
                           "payloads": " ‖ ".join(o["payloads"]) or "无",
                           "prompt_fp_stable": o["fp"], "sig": o["sig"]} for o in others]}
    if dup_other:
        row["other_runs"] = row["other_runs"][:1]
    return row


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return str(path)


def dataset_entry(cid, expect, anchor=None):
    return {"cid": cid, "judge": "J1", "expect": expect, "time_anchor": anchor or {}}


def build(tmp_path, rows, dataset=None, shadow=None, want_runs=3):
    rows_path = write_jsonl(tmp_path / "rows.jsonl", rows)
    ds_path = str(write_jsonl(tmp_path / "cases.jsonl", dataset)) if dataset else ""
    legs = [("合成腿", ax.load_jsonl(rows_path),
             ax.load_expect(ds_path), str(shadow) if shadow else "", want_runs)]
    report = ax.build_report(legs)
    return report, ax.render(report)


# ─────────────────────────── S1 结构稳定性 ───────────────────────────
def test_三跑稳_S1_两列都过(tmp_path):
    r1 = make_row("j1x", [run(("MEMO",), ["买菜"]), run(("MEMO",), ["买菜"]), run(("MEMO",), ["买菜"])])
    report, text = build(tmp_path, [r1])
    s1 = report["legs"][0]["axes"][0]
    assert (s1["cells"][0]["num"], s1["cells"][0]["den"]) == (1, 1)
    assert (s1["cells"][1]["num"], s1["cells"][1]["den"]) == (1, 1)
    assert s1["cells"][2]["num"] == 0
    assert "Q01" in text and "j1x" not in text            # 匿名题号，不回显原 cid


def test_条数在抖_S1_两列都不过(tmp_path):
    r = make_row("j1x", [run(("MEMO",), ["买菜"]), run(("MEMO", "CAL_NOTE"), ["买菜", "周一"]),
                         run(("MEMO",), ["买菜"])])
    s1 = build(tmp_path, [r])[0]["legs"][0]["axes"][0]
    assert s1["cells"][0]["num"] == 0 and s1["cells"][0]["den"] == 1
    assert s1["cells"][1]["num"] == 0


def test_条数同但片段不同必须单列(tmp_path):
    """交叉格＝新信息：条数一致但内容换了，两列必须分别报出来，不许被合成一个数。"""
    r = make_row("j1x", [run(("MEMO",), ["买菜"]), run(("MEMO",), ["买鱼"]), run(("MEMO",), ["买菜"])])
    s1 = build(tmp_path, [r])[0]["legs"][0]["axes"][0]
    assert s1["cells"][0]["num"] == 1                     # 条数一致
    assert s1["cells"][1]["num"] == 0                     # 片段集合不一致
    assert s1["cells"][2]["num"] == 1                     # 交叉格点名
    assert s1["cells"][0]["den"] == s1["cells"][2]["den"]


def test_S1_不合成总分(tmp_path):
    r = make_row("j1x", [run(("MEMO",), ["a"]), run(("MEMO",), ["a"]), run(("MEMO",), ["a"])])
    report = build(tmp_path, [r])[0]
    for leg in report["legs"]:
        for a in leg["axes"]:
            assert not any(("总分" in c["name"] or "综合" in c["name"]) for c in a["cells"])
            assert all(c["den"] == 0 or c["basis"] for c in a["cells"])   # 每个读数都带口径依据
        axes = {a["axis"]: a for a in leg["axes"]}
        assert len(axes["S1"]["cells"]) == 3 and len(axes["S4"]["cells"]) == 4   # 多列各自出数


# ─────────────────────────── S2 关键片段 ───────────────────────────
def test_S2_命中与未命中逐条报依据(tmp_path):
    rows = [make_row("j1a", [run(("MEMO",), ["用户住城西"])] * 3),
            make_row("j1b", [run(("MEMO",), ["用户住城南"])] * 3)]
    ds = [dataset_entry("j1a", {"action_type": "MEMO", "field_match": {"*": "城西"}}),
          dataset_entry("j1b", {"action_type": "MEMO", "field_match": {"*": "城西"}})]
    s2 = build(tmp_path, rows, dataset=ds)[0]["legs"][0]["axes"][1]
    assert (s2["cells"][0]["num"], s2["cells"][0]["den"]) == (3, 6)     # 三跑命中 3／期望 6
    by_q = {c["q"]: c for c in s2["detail"]}
    assert by_q["Q01"]["hit"] == 3 and by_q["Q02"]["hit"] == 0
    assert all(p["basis"] for c in s2["detail"] for p in c["runs"])     # 逐条依据不套百分比


def test_S2_缺判据时明确unknown不进分母(tmp_path):
    """题面没给 field_match／没给 dataset ⇒ 该轴未可算，绝不把"没标注"读成 0 命中或成功。"""
    rows = [make_row("j1a", [run(("MEMO",), ["用户住城西"])] * 3)]
    r_no = build(tmp_path, rows)[0]["legs"][0]["axes"][1]               # 无 dataset
    assert r_no["state"] == ax.STATE_UNKNOWN and r_no["cells"][0]["den"] == 0
    ds = [dataset_entry("j1a", {"action_type": "MEMO"})]               # 有题面无 field_match
    r_fm = build(tmp_path, rows, dataset=ds)[0]["legs"][0]["axes"][1]
    assert r_fm["state"] == ax.STATE_UNKNOWN and r_fm["cells"][0]["den"] == 0
    assert "field_match" in " ".join(r_fm["unknown"])


def test_S2_无产出的跑归S3不折成片段错(tmp_path):
    rows = [make_row("j1a", [run(ok=False, err="E3"), run(ok=False, err="E3"), run(ok=False, err="E3")])]
    ds = [dataset_entry("j1a", {"action_type": "MEMO", "field_match": {"*": "城西"}})]
    s2 = build(tmp_path, rows, dataset=ds)[0]["legs"][0]["axes"][1]
    assert s2["cells"][0]["den"] == 0
    assert sum(1 for p in s2["detail"][0]["runs"] if "归 S3" in p["basis"]) == 3
    assert s2["detail"][0]["hit"] == 0 and s2["detail"][0]["want"] == 0


# ─────────────────────────── S3 / S4 ───────────────────────────
def test_S3_沿用留底错因(tmp_path):
    rows = [make_row("j1a", [run(("MEMO",), ["a"]),
                             run(ok=False, err="E3"), run(ok=False, err="E3")]),
            make_row("j1b", [run(("MEMO",), ["a"])] * 3)]
    axes = build(tmp_path, rows)[0]["legs"][0]["axes"]
    s3 = axes[2]
    assert (s3["cells"][0]["num"], s3["cells"][0]["den"]) == (2, 6)
    assert s3["detail"][0]["E3"] == 2 and s3["detail"][1]["E3"] == 0


def test_S4_豁免与未豁免分列(tmp_path):
    """同一禁用片段：写在"已搬离"短句里＝豁免放行；写成现状＝违规。豁免前后必须两列。"""
    exp = {"action_type": "MEMO", "field_match": {"*": "城西"}, "forbidden": ["城北老小区"]}
    rows = [make_row("j1ok", [run(("MEMO",), ["已搬离城北老小区，现居城西"])] * 3),
            make_row("j1bad", [run(("MEMO",), ["用户仍住城北老小区"])] * 3)]
    ds = [dataset_entry("j1ok", exp), dataset_entry("j1bad", exp)]
    s4 = build(tmp_path, rows, dataset=ds)[0]["legs"][0]["axes"][3]
    cells = {c["name"]: c for c in s4["cells"]}
    assert cells["E4 跑数／有效判定跑数"]["den"] == 6
    before = cells["禁用片段·豁免前／可判跑数"]
    after = cells["禁用片段·豁免后／可判跑数"]
    ok_case = [c for c in s4["detail"] if c["q"] == "Q01"][0]
    bad_case = [c for c in s4["detail"] if c["q"] == "Q02"][0]
    assert "豁免前 3／豁免后 0／放行 3" in ok_case["禁用片段复算"]        # 只被放行，不算违规
    assert "豁免前 3／豁免后 3／放行 0" in bad_case["禁用片段复算"]       # 未豁免
    assert before["num"] == after["num"] + cells["豁免放行条数"]["num"]


def test_S4_题面未标forbidden时不适用(tmp_path):
    rows = [make_row("j1a", [run(("MEMO",), ["城西"])] * 3)]
    ds = [dataset_entry("j1a", {"action_type": "MEMO", "field_match": {"*": "城西"}})]
    s4 = build(tmp_path, rows, dataset=ds)[0]["legs"][0]["axes"][3]
    assert s4["detail"][0]["禁用片段复算"].startswith("题面未标 forbidden")
    assert s4["cells"][1]["den"] == 0 and s4["cells"][1]["state"] == ax.STATE_UNKNOWN


def test_截断载荷只作下界不许读作全篇无违规(tmp_path):
    """载荷在生成侧就被截到 70 字：后半句里的旧值**看不见**，所以 0 违规只能是下界。"""
    cut = "用" * ax.PAYLOAD_TRUNC                                  # 恰好触到截断位
    exp = {"action_type": "MEMO", "field_match": {"*": "城西"}, "forbidden": ["城北老小区"]}
    rows = [make_row("j1a", [run(("MEMO",), [cut])] * 3)]
    report, text = build(tmp_path, rows, dataset=[dataset_entry("j1a", exp)])
    axes = report["legs"][0]["axes"]
    s4 = axes[3]
    cell = {c["name"]: c for c in s4["cells"]}["禁用片段·豁免后／可判跑数"]
    assert cell["num"] == 0                                        # 没扫到＝报 0
    assert "下界" in cell["basis"] and "截断" in cell["basis"]        # 但这一格写清它是下界
    assert "下界" in {c["name"]: c for c in s4["cells"]}["禁用片段·豁免前／可判跑数"]["basis"]
    detail = s4["detail"][0]["禁用片段复算"]
    assert detail.startswith("截断下界") and "不得读作该题无违规" in detail
    assert "下界" in axes[1]["cells"][0]["basis"]                  # S2 命中同理
    assert "触到生成期 70 字截断位" in text                          # 逐条依据点名到跑
    assert "下界" in text


def test_未触截断位的不被标成下界(tmp_path):
    """反向守卫：没截断的载荷不该被无端打上"下界"，否则这条纪律就成了万能挡箭牌。"""
    exp = {"action_type": "MEMO", "field_match": {"*": "城西"}, "forbidden": ["城北老小区"]}
    rows = [make_row("j1a", [run(("MEMO",), ["用户住城西"])] * 3)]
    axes = build(tmp_path, rows, dataset=[dataset_entry("j1a", exp)])[0]["legs"][0]["axes"]
    assert "下界" not in axes[3]["cells"][1]["basis"]
    assert axes[3]["detail"][0]["禁用片段复算"].startswith("豁免前")


# ─────────────────────────── 完整性：拒收／不可比 ───────────────────────────
def test_源指纹不一致显示不可比(tmp_path):
    runs = [run(("MEMO",), ["a"], fp="fp-1"), run(("MEMO",), ["a"], fp="fp-2"),
            run(("MEMO",), ["a"], fp="fp-2")]
    r = build(tmp_path, [make_row("j1a", runs)])[0]["legs"][0]
    assert r["n_comparable"] == 0
    assert "输入指纹不一致" in r["axes"][0]["unknown"][0]
    assert r["axes"][0]["cells"][0]["den"] == 0          # 不可比题不进分母（不偷算样本）


def test_丢跑与重复cid拒收(tmp_path):
    good = make_row("j1a", [run(("MEMO",), ["a"])] * 3)
    other = make_row("j1c", [run(("MEMO",), ["b"])] * 3)
    lost = make_row("j1b", [run(("MEMO",), ["a"])] * 3, dup_other=True)   # n_runs=3 只剩一条 other
    dup = copy.deepcopy(good)                                             # 同 cid 两行＝分母会被灌水
    axes = build(tmp_path, [good, lost, dup, other])[0]["legs"][0]["axes"]
    s1 = axes[0]
    assert s1["cells"][0]["den"] == 1                                     # 只算没被拒收的那题
    assert [c["q"] for c in s1["detail"]] == ["Q03"]                       # 被拒收的题不进逐条依据
    reasons = " ".join(s1["unknown"])
    assert "跑数不足" in reasons and "重复 cid" in reasons
    assert s1["cells"][0]["num"] == s1["cells"][0]["den"]                 # 拒收的题没被偷算进分子


def test_指纹字段错段数不可解(tmp_path):
    r = make_row("j1a", [run(("MEMO",), ["a"])] * 3)
    r["sig"] = "过｜MEMO｜无日期｜无豁免｜只有四段"          # 段数与 `run_sig_text` 脱钩
    leg = build(tmp_path, [r])[0]["legs"][0]
    assert leg["n_comparable"] == 0
    assert "指纹段数不可解" in leg["axes"][0]["unknown"][0]


# ─────────── 完整性逐跑化（10-14 小尾：没记录≠0 必须真守住） ───────────
def test_逐跑指纹缺一即不可比(tmp_path):
    """三跑只留了首跑 fp ⇒ 旧写法"只收非空 fp"会判可比（set 里就 1 个元素）。

    这一维存在的理由就是回答"第 2、3 跑到底吃了什么输入"，缺记＝答不出＝整题不可比；
    但旧逐字读数**照旧并列**，只进 caveat 那一格（不许被新判定抹掉）。
    """
    runs = [run(("MEMO",), ["a"], fp="fp-1"), run(("MEMO",), ["a"], fp=""), run(("MEMO",), ["a"], fp="")]
    leg = build(tmp_path, [make_row("j1a", runs)])[0]["legs"][0]
    assert leg["n_comparable"] == 0
    assert "逐跑输入指纹不全" in leg["axes"][0]["unknown"][0]
    assert leg["axes"][0]["cells"][0]["den"] == 0                  # 不进分母（不偷算样本）
    lg = {c["name"]: c for c in leg["axes"][5]["cells"]}
    assert lg["每一跑都过／题数"]["den"] == 1                      # 旧读数仍列这一题
    assert lg["每一跑都过／题数"]["num"] == 1                      # 旧值逐字保留
    assert lg["其中三跑输入同源／题数"]["num"] == 0                 # 只在这一格记 caveat


def test_fp与留底声明矛盾时不猜实值(tmp_path):
    """`fp_spread`／`input_identical` 是生成侧对同一维的第二份记录，两者对不上＝自相矛盾。"""
    r = make_row("j1a", [run(("MEMO",), ["a"], fp="fp-A")] * 3)
    r["fp_spread"] = ["fp-A", "fp-B"]                             # 留底说有两份，逐跑只读出一份
    leg = build(tmp_path, [r])[0]["legs"][0]
    assert leg["n_comparable"] == 0
    assert "fp_spread" in leg["axes"][0]["unknown"][0]
    assert leg["axes"][5]["cells"][0]["den"] == 1                 # 旧值不因此被抹掉

    r2 = make_row("j1b", [run(("MEMO",), ["a"], fp="fp-A")] * 3)
    r2["input_identical"] = False                                 # 逐跑 fp 全等却说不同源
    leg2 = build(tmp_path, [r2])[0]["legs"][0]
    assert leg2["n_comparable"] == 0
    assert "input_identical" in leg2["axes"][0]["unknown"][0]


def test_二三跑动作与载荷对不上则该跑unknown不进分母(tmp_path):
    """旧写法只核首跑⇒第 2/3 跑的"2 个动作配 1 段载荷"会被放过，S1/S3 就拿配不成对的跑算数。"""
    bad = run(("MEMO", "CAL_NOTE"), ["只有一条载荷"])              # 动作数 2／载荷数 1
    good = run(("MEMO",), ["只有一条载荷"])
    leg = build(tmp_path, [make_row("j1a", [good, bad, good])])[0]["legs"][0]
    assert leg["n_comparable"] == 1                                # 牵连只到那一趟，整题仍可算
    s1 = leg["axes"][0]
    assert s1["detail"][0]["counts"] == "1/1"                      # 坏跑没混进条数比对
    assert "第2跑" in s1["unknown"][-1] and "不进分母" in s1["unknown"][-1]
    assert leg["axes"][2]["detail"][0]["runs"] == 2                 # S3 有效判定跑数＝2 而非 3
    assert leg["axes"][5]["cells"][0]["den"] == 1                  # 旧读数照常列出


def test_首跑指纹与显式字段不符也剔出分母(tmp_path):
    """首跑既有显式字段又有指纹，两者对不上＝留底自相矛盾⇒这一趟判不了（不挑一个信）。"""
    good = run(("MEMO",), ["a"])
    r = make_row("j1a", [good, good, good])
    r["actions_seen"] = ["MEMO", "CAL_NOTE"]                       # 显式字段 2 个动作
    r["payloads"] = ["a", "b"]                                     # 载荷也 2 段＝不触发首跑那条旧硬伤
    leg = build(tmp_path, [r])[0]["legs"][0]
    assert leg["n_comparable"] == 1
    assert leg["axes"][0]["detail"][0]["counts"] == "1/1"          # 错位的首跑不参与条数比对
    assert "actions_seen" in leg["axes"][0]["unknown"][-1]


def test_正确同源三跑才出可比读数(tmp_path):
    """反向守卫：逐跑 fp 齐且全等＋映射配得上对 ⇒ 该算的照算，不许把守门变成一律拒收。"""
    r = make_row("j1a", [run(("MEMO",), ["a"], fp="fp-A")] * 3)
    r["input_identical"] = True
    r["fp_spread"] = ["fp-A"]
    leg = build(tmp_path, [r])[0]["legs"][0]
    assert leg["n_comparable"] == 1
    assert leg["axes"][0]["cells"][0]["den"] == 1
    assert leg["axes"][0]["unknown"] == []
    assert leg["axes"][5]["cells"][3]["num"] == 1                  # 同源 caveat 格＝1


def test_n_runs不可解释时标unknown不猜(tmp_path):
    r = make_row("j1a", [run(("MEMO",), ["a"])] * 3)
    r["n_runs"] = "三跑"                                           # 外部产物里的非数值形态
    leg = build(tmp_path, [r])[0]["legs"][0]
    assert leg["n_comparable"] == 0
    assert "不可解释" in leg["axes"][0]["unknown"][0]


# ─────────────────────────── 辅轴：旧读数原样并列 ───────────────────────────
def test_旧逐字读数保留且与新轴并列(tmp_path):
    """新轴说"结构稳"，旧轴说"逐字不一致"——两行必须同时在场，新轴不覆盖旧轴。"""
    rows = [make_row("j1a", [run(("MEMO",), ["买菜"])] * 3),
            make_row("j1b", [run(("MEMO",), ["甲"]), run(("CAL_NOTE",), ["甲"]),
                            run(("MEMO",), ["甲"])])]
    report, text = build(tmp_path, rows)
    axes = report["legs"][0]["axes"]
    s1, legacy = axes[0], axes[5]
    assert (s1["cells"][0]["num"], s1["cells"][0]["den"]) == (2, 2)       # 条数两题都稳
    assert (s1["cells"][1]["num"], s1["cells"][1]["den"]) == (2, 2)       # 片段集合也稳
    lg = {c["name"]: c for c in legacy["cells"]}
    assert lg["每一跑都过／题数"]["num"] == 2
    assert lg["三跑逐字一致／题数"]["num"] == 1                           # 旧口径仍不一致（动作名换了）
    assert lg["认证／题数"]["num"] == 1
    assert "辅轴·逐字严格认证" in text and "每一跑都过" in text


def test_旧值与留底逐字相等不被重算覆盖(tmp_path):
    r = make_row("j1a", [run(("MEMO",), ["a"], ok=False, err="E3")] * 3)
    r["pass_all"] = True                                  # 留底里存的历史认证读数（与新复算不同源）
    r["consistent"] = True
    legacy = build(tmp_path, [r])[0]["legs"][0]["axes"][5]
    assert legacy["cells"][2]["num"] == 1                 # 认证沿用留底存的 `pass_all/consistent`
    assert legacy["detail"][0]["err_spread"] == "E3"      # 逐跑错因原样带出，不改写


# ─────────────────────────── S5 补记条数 ───────────────────────────
def test_S5_只数条不取正文(tmp_path):
    log = tmp_path / "app.log"
    log.write_text(
        "INFO A30 兜底 route=memory_fallback char=12 made=无 skipped=no_marker\n"
        "INFO A30 兜底 route=memory_fallback char=12 made=CAL_NOTE:明天开会 ‖ MEMO:带伞 skipped=无\n"
        "无关行\n", encoding="utf-8")
    report, text = build(tmp_path, [make_row("j1a", [run(("MEMO",), ["a"])] * 3)], shadow=log)
    s5 = report["legs"][0]["axes"][4]
    cells = {c["name"]: c for c in s5["cells"]}
    assert (cells["made 非空次数／调用次数"]["num"], cells["made 非空次数／调用次数"]["den"]) == (1, 2)
    assert cells["补记条目总数"]["num"] == 2
    assert "no_marker" in text                             # 原因名可以报
    for secret in ("明天开会", "带伞"):                     # 正文一律不进报表
        assert secret not in text


def test_S5_无输入即未可算(tmp_path):
    s5 = build(tmp_path, [make_row("j1a", [run(("MEMO",), ["a"])] * 3)])[0]["legs"][0]["axes"][4]
    assert s5["state"] == ax.STATE_UNKNOWN
    assert s5["unknown"] == ["未给影子留痕输入"]


def test_S5_坏行缺字段不默认补了0条(tmp_path):
    """`made=` 读不出／字段边界有歧义的行**不进调用数**：旧写法 calls 先加一再 continue，
    等于把"读不出"稀释成"这一次没补记"，分母被灌水、读数被压低。"""
    log = tmp_path / "app.log"
    log.write_text(
        "INFO A30 兜底 route=memory_fallback char=12 made=无 skipped=无\n"
        "INFO A30 兜底 route=memory_fallback char=12 skipped=no_field\n"                    # 缺 made=
        "INFO A30 兜底 route=memory_fallback char=12 made=CAL_NOTE:带伞 ‖ MEMO:拿药 skipped=无\n"
        "INFO A30 兜底 route=memory_fallback char=12 made=用户说 skipped=无 skipped=x\n",    # 正文撞了字段串
        encoding="utf-8")
    s5 = build(tmp_path, [make_row("j1a", [run(("MEMO",), ["a"])] * 3)], shadow=log)[0]["legs"][0]["axes"][4]
    cells = {c["name"]: c for c in s5["cells"]}
    assert cells["made 非空次数／调用次数"]["den"] == 2         # 只数读得出来的两次
    assert cells["made 非空次数／调用次数"]["num"] == 1
    assert cells["补记条目总数"]["num"] == 2
    assert s5["detail"][0]["不可解析行数"] == 2
    assert "没记录≠补了 0 条" in " ".join(s5["unknown"])
    assert "未计入" in cells["补记条目总数"]["basis"]


def test_S5_整份留痕读不出时不出一条像样的读数(tmp_path):
    """全是坏行 ⇒ 调用数 0 ⇒ 该轴 unknown，绝不报"0 条补记"这种假干净。"""
    log = tmp_path / "bad.log"
    log.write_text("INFO A30 兜底 route=memory_fallback char=12 skipped=无\n" * 2, encoding="utf-8")
    s5 = build(tmp_path, [make_row("j1a", [run(("MEMO",), ["a"])] * 3)], shadow=log)[0]["legs"][0]["axes"][4]
    assert s5["state"] == ax.STATE_UNKNOWN
    assert max(c["den"] for c in s5["cells"]) == 0
    assert "没记录≠补了 0 条" in " ".join(s5["unknown"])


# ─────────────────────────── 零样本／零副作用 ───────────────────────────
def test_零样本全轴unknown(tmp_path):
    """单跑档（无三跑）＋无 dataset ⇒ 没有一轴能出数；不许报"0 个错所以没问题"。"""
    leg = build(tmp_path, [make_row("j1a", [run(("MEMO",), ["a"])])])[0]["legs"][0]
    assert leg["n_comparable"] == 0
    states = {a["axis"]: (a["state"], max([c["den"] for c in a["cells"]] or [0])) for a in leg["axes"]}
    assert states["S1"] == (ax.STATE_UNKNOWN, 0)
    assert states["S2"][1] == 0 and states["S4"][1] == 0
    assert states["LEGACY"][0] == ax.STATE_UNKNOWN        # 分母 0 ⇒ 旧读数格也是 unknown


def test_输入产物不被改写(tmp_path):
    rows = [make_row("j1a", [run(("MEMO",), ["城西"])] * 3)]
    before = copy.deepcopy(rows)
    loaded = ax.load_jsonl(write_jsonl(tmp_path / "rows.jsonl", rows))
    assert loaded == before
    ax.build_report([("腿", loaded, {}, "", 3)])
    assert loaded == before                                # 复算只读，不写回输入


def test_干净进程里零LLM零网络(tmp_path):
    """隔离解释器证明：import 本工具不拉起 `app.*`、不碰 LLM 入口、不引网络库、不自动跑 CLI。"""
    code = ("import sys; sys.path[:0] = [%r, %r]; import memory_quality_axes as ax;"
            "bad=sorted(m for m in sys.modules if m.startswith('app.') or m in ('httpx','requests','aiohttp'));"
            "print('BAD[' + ','.join(bad) + ']')" % (str(Path(MODULE_PATH).parent), str(SCRIPT_DIR)))
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        timeout=180,
    )
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "BAD[]", out.stdout


def test_报表不泄漏载荷正文(tmp_path):
    rows = [make_row("j1a", [run(("MEMO",), ["只有本题才有的独特句子"])] * 3)]
    ds = [dataset_entry("j1a", {"action_type": "MEMO", "field_match": {"*": "独特"}})]
    text = build(tmp_path, rows, dataset=ds)[1]
    assert "只有本题才有的独特句子" not in text
    assert "首跑正文" not in text


def test_CLI落盘并返回零(tmp_path, capsys):
    rows = [make_row("j1a", [run(("MEMO",), ["a"])] * 3)]
    rows_path = write_jsonl(tmp_path / "rows.jsonl", rows)
    out = tmp_path / "report.md"
    assert ax.main(["--rows", rows_path, "--out", str(out)]) == 0
    assert out.is_file() and "五轴" in out.read_text(encoding="utf-8")
    printed = capsys.readouterr().out
    assert "Q01" in printed and "j1a" not in printed


def test_没有rows输入就拒绝出数(tmp_path):
    with pytest.raises(SystemExit):
        ax.main([])
    with pytest.raises(SystemExit):
        ax.load_jsonl(str(tmp_path / "不存在.jsonl"))
    empty = tmp_path / "空.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        ax.load_jsonl(str(empty))


def test_map_out私有映射拒绝写进仓库(tmp_path):
    """匿名题号↔原 cid 能反查真实题面＝带原数据的派生物，落进仓库迟早跟着公开快照漏出去。

    本题顺带把本机事实钉住：项目要求的 `--basetemp` 就落在仓库里，所以 `tmp_path` **也算仓库内**，
    仓库外那条路必须另找目录验。
    """
    rows = [make_row("j1a", [run(("MEMO",), ["a"])] * 3)]
    rows_path = write_jsonl(tmp_path / "rows.jsonl", rows)
    inside = Path(ax.REPO_ROOT) / "axes_map_leak.json"
    with pytest.raises(SystemExit) as ei:
        ax.main(["--rows", rows_path, "--map-out", str(inside)])
    assert "私有" in str(ei.value) and "仓库" in str(ei.value)
    assert not inside.exists()                                    # 拒收发生在写盘之前
    with tempfile.TemporaryDirectory() as td:                     # 仓库外才放行
        outside = Path(td) / "map.json"
        assert ax.main(["--rows", rows_path, "--map-out", str(outside)]) == 0
        assert json.loads(outside.read_text(encoding="utf-8"))["rows/Q01"] == "j1a"   # 腿名＝文件名 stem
    assert "--map-out" in build(tmp_path, rows)[1]                 # 报表也写明这条纪律


def test_源码与报表不带作者机路径(tmp_path):
    """docstring 里的样例路径是**作者机器**目录，公开面扫描（check_public_leak）就是为它设的。"""
    src = Path(MODULE_PATH).read_text(encoding="utf-8")
    forbidden_workspace = "Codex" + "-Projects"  # 同一禁止词判据，不在公开源码嵌入完整作者目录名
    assert forbidden_workspace not in src and "D:\\" not in src and "D:/" not in src
    assert "<评测留底.jsonl>" in src
    rows = [make_row("j1a", [run(("MEMO",), ["a"])] * 3)]
    report_text = build(tmp_path, rows)[1]
    assert forbidden_workspace not in report_text


def test_min_samples只标不足不做准入(tmp_path):
    """阈值未批准 ⇒ `--min-samples` 只把轴标成「不足」，不产生通过／不通过的判定。"""
    rows = [make_row("j1a", [run(("MEMO",), ["a"])] * 3)]
    leg = ax.build_report([("腿", rows, {}, "", 3)], min_samples=10)["legs"][0]
    assert leg["axes"][0]["state"] == ax.STATE_INSUFFICIENT
    assert "准入" not in json.dumps(leg["axes"][0], ensure_ascii=False)
