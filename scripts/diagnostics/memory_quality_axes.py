# -*- coding: utf-8 -*-
"""A30 五轴离线报表（零 LLM、零库、零网络）：只从**明确传入**的既有评测产物复算。

五轴各自出数、不合总分（判据提案 §2 三条硬约束）：

| 轴 | 量什么 | 分子／分母口径 |
|---|---|---|
| S1 结构稳定性 | 同输入三跑的**条数**与**关键片段集合**是否稳 | 条数一致题数／可比题数，片段集合一致题数／可比题数（两列单列，"条数同但片段不同"必须能显示） |
| S2 字面关键片段正确性 | 产出里的期望片段是否**逐条命中** | 命中片段数／期望片段数（逐题逐跑报依据，不套百分比） |
| S3 遗漏（欠产） | 该记的没记 | E3 跑数／有效判定跑数（沿用生成期 `j1_verdict` 落的 `err`，不重算旧数） |
| S4 错误事实（误记） | 产出了但参数错／旧值复发 | E4 跑数／有效判定跑数，＋ 禁用片段类**豁免前**与**豁免后**两列 |
| S5 补记条数 | 单轮系统代记补了几条 | 每次调用条数分布（只数条、不取正文） |

辅轴＝**原逐字严格认证读数**，逐字并列保留，不被新轴覆盖（`certified_j1`／`sig_recompute` 同一份实现）。

三条口径纪律：
1. **判据同源**：片段匹配与禁用片段判定一律调 `memory_action_eval` 现成的 `j1_verdict`／
   `_forbidden_check`，本文件不拼 prompt、不写第二套匹配逻辑、不新造标注。
2. **没记录 ≠ 0**：字段缺失、跑数不足、输入不同源、题面未标注 ⇒ 该格明确报 `unknown`／`不足`
   并给原因，绝不进分子也不进分母。
3. **不泄漏**：输出只有匿名题号、计数与缺失原因；留底里的载荷正文／通道正文／回复正文
   一律不进 stdout 与报表（片段只做归一化比对，比对结果以计数呈现）。
   `--map-out`（匿名题号↔原 cid）是**私有派生物**，工具当场拒绝写进仓库。
4. **逐跑自证**：完整性核对是**逐跑**做的，不是只看首跑——三跑里任一趟没记输入指纹 ⇒ 整题不可比；
   任一趟的动作数与载荷数配不上对 ⇒ 那一趟记 unknown、不进任何分母（别的不受牵连的跑照算）。
   载荷触到生成期 70 字截断位的跑，S2 命中与 S4 禁用片段一律标**下界**（没看见≠没有）。

用法（cwd=backend，全部只读）：
    .venv\\Scripts\\python.exe ..\\scripts\\diagnostics\\memory_quality_axes.py \\
        --rows <评测留底.jsonl> \\
        --dataset ..\\scripts\\diagnostics\\memory_action_cases_j1j2_draft.jsonl \\
        --shadow-log <留痕日志>  --out <报表.md>
不传 `--rows` 就报错退出（空扫＝把"没数据"读成"没问题"）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BACKEND = REPO_ROOT / "backend"
for _p in (str(BACKEND), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from memory_action_eval import (_forbidden_check, certified_j1, j1_verdict,  # noqa: E402
                                mask_clock, sig_recompute)

SIG_SEP = "｜"                      # run_sig_text 的分段符（段内已把「｜」转义成「／」）
ITEM_SEP = " ‖ "                   # 同 run_sig_text 里载荷／通道的条目分隔符
NONE_TOKENS = ("无动作", "无日期", "无豁免", "无载荷", "无通道", "")
SHADOW_MARK = "A30 兜底 route=memory_fallback"
SKIPPED_MARK = " skipped="
# 生成侧 `payloads=[_payload_text(a.payload).strip()[:70] ...]` 的截断位：触到这一行的载荷
# **看不见后半句**，所以 S2 命中／S4 禁用片段只能是下界（把"没扫到"报成"全篇 0 违规"就是假干净）。
PAYLOAD_TRUNC = 70
AXES = ("S1", "S2", "S3", "S4", "S5")

STATE_OK = "ok"
STATE_UNKNOWN = "unknown"
STATE_INSUFFICIENT = "不足"


class RebuiltAction:
    """留底里一条产出的**最小重建物**（action_type ＋摊平后的载荷文本）。

    只为了让 `j1_verdict` 能吃离线留底：判据本身一行不改，改的只是入参来源。
    载荷用 `{"text": ...}` 承载＝与尺子对【记忆：】通道的同一处理方式（`_payload_text` 认 `text` 键）。
    """

    __slots__ = ("action_type", "payload")

    def __init__(self, action_type, payload_text):
        self.action_type = action_type
        self.payload = {"text": payload_text}


def load_jsonl(path):
    """读一份 jsonl 产物。文件不存在或读空 ⇒ 抛错（空分母不许出读数）。"""
    if not os.path.isfile(path):
        raise SystemExit("[停手] 输入产物不存在：%s" % path)
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    if not rows:
        raise SystemExit("[停手] 输入产物为空：%s ⇒ 分母为 0，不报读数" % path)
    return rows


def load_expect(dataset):
    """题面 ⇒ {cid: expect}（只读判据字段，正文不进报表）。不给 dataset ⇒ S2/S4 全部 unknown。"""
    if not dataset:
        return {}
    out = {}
    for r in load_jsonl(dataset):
        cid = str(r.get("cid") or "")
        if cid:
            out[cid] = {"expect": r.get("expect") or {},
                        "time_anchor": r.get("time_anchor") or {},
                        "judge": str(r.get("judge") or "")}
    return out


def sig_parts(sig):
    """把逐跑指纹切成 6 段；段数不对 ⇒ 返回 None（该跑的留底不可用，不许猜）。"""
    parts = str(sig or "").split(SIG_SEP)
    if len(parts) != 6:
        return None
    return [p.strip() for p in parts]


def _items(seg):
    if not seg or seg == "无载荷" or seg == "无通道":
        return []
    return [x.strip() for x in seg.split(ITEM_SEP) if x.strip()]


def runs_of_row(row):
    """一题的三跑视图（全部来自留底，零重跑）：`[{run, pass, err, types, items, dates, exemptions, sig_ok}]`。

    第 2、3 跑的逐跑判定与片段都只能从 `other_runs` 的 `pass/err/sig` 里拿（生成期就是这些维度参与了
    指纹比对，见 `run_sig_text`）；`sig` 段数不对 ⇒ 该跑标 `sig_ok=False`，由调用方按 unknown 处理。
    """
    out = []
    first = _one_run(1, row.get("sig"), bool(row.get("pass")), str(row.get("err") or ""),
                     list(row.get("actions_seen") or []), [str(x) for x in (row.get("payloads") or [])],
                     [str(x) for x in (row.get("mem_channel") or [])],
                     list(row.get("dates_seen") or []), list(row.get("exemptions") or []),
                     str(row.get("prompt_fp_stable") or row.get("prompt_fp") or ""))
    out.append(first)
    for i, o in enumerate(row.get("other_runs") or [], start=2):
        out.append(_one_run(i, o.get("sig"), bool(o.get("pass")), str(o.get("err") or ""),
                            None, None, None, None, None,
                            str(o.get("prompt_fp_stable") or o.get("prompt_fp") or "")))
    return out


def _csv_seg(seg, none_token):
    return [] if seg == none_token else [x.strip() for x in seg.split(",") if x.strip()]


def _one_run(n, sig, ok, err, types, payloads, chan, dates, exempts, fp):
    """首跑用显式字段（生成期原样）；第 2/3 跑从 `sig` 反解——两者是同一份 `run_sig_text` 的产物。

    逐跑自证性（`usable`／`why`）：生成侧 `actions_seen` 与 `payloads` 是同一次列表推导（逐动作、
    同序），指纹首段与该跑 `pass` 同源 ⇒ 任一配不上对就是**留底自相矛盾**。这种跑只能记 unknown
    并踢出分母：拿"读不出来的那一维"当 0，就是把没记录读成了没问题。
    """
    parts = sig_parts(sig)
    r = {"run": n, "pass": bool(ok), "err": err, "fp": fp, "sig": str(sig or ""),
         "sig_ok": parts is not None, "why": []}
    if types is None:                              # 非首跑：一切从 sig 来
        if parts is None:
            r.update(usable=False, types=[], items=[], dates=[], n_payloads=0, n_chan=0,
                     exemptions=0, trunc=False)
            return r
        types = _csv_seg(parts[1], "无动作")
        pay = _items(parts[4])
        chan = _items(parts[5])
        dates = _csv_seg(parts[2], "无日期")
        exemptions = len(_csv_seg(parts[3], "无豁免"))
        if (parts[0] == "过") != bool(ok):
            r["why"].append("指纹首段（过／不过）与该跑 `pass` 不符")
    else:
        pay = [str(x).strip() for x in (payloads or []) if str(x).strip()]
        chan = [str(x).strip() for x in (chan or []) if str(x).strip()]
        dates = [str(x).strip() for x in (dates or []) if str(x).strip()]
        exemptions = len(exempts or [])
        r["exemption_entries"] = list(exempts or [])
        if parts is not None and types != _csv_seg(parts[1], "无动作"):
            r["why"].append("逐跑指纹的动作段与 `actions_seen` 不符")
    if len(types) != len(pay):
        r["why"].append("动作数 %d 与载荷数 %d 配不上对⇒类型↔载荷判不了" % (len(types), len(pay)))
    r["trunc"] = any(len(x) >= PAYLOAD_TRUNC for x in pay)
    r["usable"] = r["sig_ok"] and not r["why"]
    r.update(types=list(types), items=pay + chan, dates=dates,
             n_payloads=len(pay), n_chan=len(chan), exemptions=exemptions)
    return r


def _fp_facts(c, runs):
    """逐跑输入指纹：缺一趟就判不出"这三跑吃的是不是同一份料" ⇒ 整题不可比（不是"少一趟也能算"）。

    只核**非空**的 fp 是假可比：三跑里只留了首跑 fp 时，`set(fps)` 只有 1 个元素、照样"通过"，
    于是第 2、3 跑到底吃了什么输入永远答不出来（10-10 加这一维就是为了答这一问）。
    """
    missing = [r["run"] for r in runs if not r["fp"]]
    if missing:
        c["codes"].append("fp_absent")
        c["reasons"].append("逐跑输入指纹不全：第 %s 跑没记⇒判不出三跑是否同一份料" % "、".join(str(x) for x in missing))
    if len({r["fp"] for r in runs if r["fp"]}) > 1:
        c["codes"].append("fp_mismatch")
        c["reasons"].append("输入指纹不一致（剥钟点后仍不同＝非同源）")
    spread = c["row"].get("fp_spread")
    if isinstance(spread, (list, tuple)) and spread:
        claimed = sorted({str(x) for x in spread if str(x)})
        got = sorted({r["fp"] for r in runs if r["fp"]})
        if claimed != got:
            c["codes"].append("fp_mismatch")
            c["reasons"].append("留底 `fp_spread`（%d 个）与逐跑可读指纹（%d 个）不符⇒fp 这一维自相矛盾"
                                 % (len(claimed), len(got)))
    identical = c["row"].get("input_identical")
    if identical is False and len(runs) > 1 and len({r["fp"] for r in runs}) == 1:
        c["codes"].append("fp_mismatch")
        c["reasons"].append("留底 `input_identical=False` 但逐跑 fp 全等⇒生成侧与留底脱钩")


def integrity(rows, expect_map, want_runs=3):
    """跨三跑核 case／run／source 指纹 ⇒ 逐题给出「可比」与不可比原因（拒收不偷算）。"""
    seen = Counter(str(r.get("cid") or "") for r in rows)
    cases, order = {}, []
    for r in rows:
        cid = str(r.get("cid") or "")
        if not cid:
            continue
        if cid in cases:
            cases[cid]["reasons"].append("同 cid 在产物里出现多行")
            continue
        cases[cid] = {"cid": cid, "reasons": [], "runs": runs_of_row(r), "row": r}
        order.append(cid)
    out = []
    for cid in order:
        c = cases[cid]
        c["codes"] = []
        if seen[cid] > 1:
            c["codes"].append("dup_cid")
            c["reasons"].append("重复 cid（分母会被灌水）")
        runs = c["runs"]
        raw_n = c["row"].get("n_runs")
        declared = None
        if raw_n is not None:
            try:
                declared = int(raw_n)
            except (TypeError, ValueError):
                c["codes"].append("artifact_conflict")
                c["reasons"].append("留底声明的 n_runs=%r 不可解释⇒跑数核不了，不猜实值" % (raw_n,))
        if declared is None:
            declared = len(runs) or 1
        c["declared_runs"] = declared
        if declared != len(runs):
            c["codes"].append("artifact_conflict")
            c["reasons"].append("留底声明 n_runs=%d 与实际可读跑数 %d 不符" % (declared, len(runs)))
        if len(runs) < want_runs:
            c["codes"].append("runs_missing")
            c["reasons"].append("跑数不足：实际 %d／要求 %d" % (len(runs), want_runs))
        if any(not r["sig_ok"] for r in runs):
            c["codes"].append("sig_unparsable")
            c["reasons"].append("逐跑指纹段数不可解（留底与 `run_sig_text` 口径脱钩）")
        first = runs[0] if runs else None
        if first is not None and first["sig_ok"] and first["types"] and \
                len(first["types"]) != first["n_payloads"]:
            c["codes"].append("artifact_conflict")
            c["reasons"].append("首跑动作数与载荷数不等（留底自相矛盾）")
        _fp_facts(c, runs)
        rec = sig_recompute(c["row"])
        if rec is not None and rec != bool(c["row"].get("consistent", True)):
            c["codes"].append("recalc_mismatch")
            c["reasons"].append("留底存的 `consistent` 与按 `sig_recompute` 复算不符")
        c["has_expect"] = bool(expect_map.get(cid))
        c["comparable"] = not c["reasons"]
        c["anon"] = "Q%02d" % (len(out) + 1)   # 匿名题号：报表与 stdout 只用它，不用原 cid
        # 逐跑判不了的跑：整题仍可比的，被踢掉的那一趟单独点名（unknown 不进分母，必须看得见）
        c["run_notes"] = ["%s 第%d跑：%s⇒该跑不进分母" % (c["anon"], r["run"], "；".join(r["why"]) or "逐跑留底不可用")
                          for r in runs if not r.get("usable")]
        out.append(c)
    return out


def axis_s1(cases):
    """S1 结构稳定性：条数一致 ∧ 关键片段集合一致（集合恒等，不比整句）——两列单列。"""
    cmp_cases = [c for c in cases if c["comparable"] and _usable_runs(c) >= 2]
    n_count = n_frag = n_count_only = 0
    cells = []
    for c in cmp_cases:
        runs = _usable_runs_of(c)
        counts = [len(r["items"]) for r in runs]
        sets = {frozenset(mask_clock(i) for i in r["items"]) for r in runs}
        same_n = len(set(counts)) == 1
        same_s = len(sets) == 1
        n_count += 1 if same_n else 0
        n_frag += 1 if same_s else 0
        n_count_only += 1 if (same_n and not same_s) else 0
        cells.append({"q": c["anon"], "counts": "/".join(str(x) for x in counts),
                      "same_n": same_n, "same_set": same_s,
                      "note": "条数同但片段集合不同" if (same_n and not same_s) else
                              ("条数与片段集合都稳" if same_n and same_s else "条数在抖")})
    unknown = ["%s（%s）" % (c["anon"], "；".join(c["reasons"])) for c in cases
               if not (c["comparable"] and _usable_runs(c) >= 2)]
    if _excluded_notes(cases):
        unknown.append("S1 实际参与比对的跑数见逐条依据（%d 趟因逐跑自证性被推翻而不进分母）" % len(_excluded_notes(cases)))
    return _axis("S1", "结构稳定性", cmp_cases, [
        _cell("条数一致率", n_count, len(cmp_cases), "留底逐跑片段计数（`run_sig_text` 第 5/6 段）"),
        _cell("片段集合一致率", n_frag, len(cmp_cases), "剥钟点后片段集合恒等（`mask_clock` 同一口径）"),
        _cell("条数同但片段不同", n_count_only, len(cmp_cases), "S1 两列交叉格＝新信息，必须单列不许合")],
        detail=cells, unknown=unknown + _excluded_notes(cases))


def _usable_runs_of(c):
    """能进分母的跑＝指纹解得开 **且** 逐跑自证性没被推翻（动作↔载荷／过↔指纹配得上对）。"""
    return [r for r in c["runs"] if r.get("usable")]


def _usable_runs(c):
    return len(_usable_runs_of(c))


def _excluded_notes(cases):
    """各轴共用的"这一跑为什么不在分母里"清单（unknown 必须点名，不许悄悄少算）。"""
    return [n for c in cases for n in c.get("run_notes") or []]


def _trunc_runs(cases):
    return sum(1 for c in cases for r in _usable_runs_of(c) if r.get("trunc"))


def axis_s2(cases, expect_map):
    """S2 字面关键片段正确性：逐题逐跑报「命中／期望」与依据，不合并成百分比。"""
    num = den = 0
    cells, unknown = [], []
    for c in cases:
        cid = c["cid"]
        em = (expect_map.get(cid) or {}).get("expect") or {}
        fm = em.get("field_match") or {}
        if not c["comparable"]:
            unknown.append("%s（%s）" % (c["anon"], "；".join(c["reasons"])))
            continue
        if not fm:
            unknown.append("%s（题面未标 field_match ⇒ 该轴不适用）" % c["anon"])
            continue
        want_types = em.get("action_type")
        per_run = []
        for r in _usable_runs_of(c):
            hits, cand_n, basis = _fragment_hits(r, fm, want_types, em)
            num += hits
            den += cand_n
            per_run.append({"run": r["run"], "hit": hits, "want": cand_n, "basis": basis})
        if not per_run:
            unknown.append("%s（该题没有任何一趟跑得通判据⇒S2 不出数，不折成 0／0）" % c["anon"])
            continue
        cells.append({"q": c["anon"], "runs": per_run,
                      "hit": sum(p["hit"] for p in per_run),
                      "want": sum(p["want"] for p in per_run)})
    t = _trunc_runs(cases)
    state = STATE_OK if den else STATE_UNKNOWN
    return _axis("S2", "字面关键片段正确性", [c for c in cases if c["comparable"]],
                 [_cell("命中片段数／期望片段数", num, den,
                        "判据＝`j1_verdict` 对 `expect.field_match` 的原式（伪字段 `*`＝任一内容字段）"
                        + ("；%d 趟载荷触到生成期截断位⇒读数是下界，未看见的片段不算已命中" % t if t else ""))],
                 detail=cells, unknown=unknown + _excluded_notes(cases), state=state)


def _fragment_hits(run, field_match, want_types, expect):
    """逐片段问一次 `j1_verdict`：只喂这一条 field_match，其余判据不带 ⇒ bad 为空＝这条片段命中。"""
    actions = [RebuiltAction(t, p) for t, p in _pairs(run)]
    chan = run["items"][run.get("n_payloads", len(run["items"])):]
    want = want_types
    if not want or (isinstance(want, (list, tuple)) and not [x for x in want if str(x).strip()]):
        return 0, 0, "题面未标 action_type ⇒ 判不了（不是模型的错，也不许按 0 分算）"
    if not actions and not chan:
        return 0, 0, "该跑无产出物⇒片段判不了（归 S3 遗漏，不折成\"片段错\"）"
    hits = 0
    tried = 0
    basis = []
    for field, frag in (field_match or {}).items():
        if str(field) == "date":
            tried += 1
            hits += 1 if str(frag) in (run.get("dates") or []) else 0
            basis.append("date：按留底 `dates_seen` 比对")
            continue
        if str(field) != "*":
            basis.append("%s：留底只存摊平文本、未存该字段原值⇒不可复算" % field)
            continue
        v = j1_verdict({"action_type": want, "field_match": {"*": frag}}, actions,
                       memory_texts=list(chan))
        if v.get("err") in ("E3", "E_unannotated"):
            basis.append("该跑未产出期望通道⇒归 S3，不计入 S2 分母")
            continue
        tried += 1
        hits += 1 if v.get("pass") else 0
    basis.append("第 %d 跑载荷%s，%s" % (
        run["run"], "为生成期原值" if run["run"] == 1 else "由逐跑指纹反解",
        ("触到生成期 %d 字截断位⇒该跑命中只能是下界" % PAYLOAD_TRUNC) if run.get("trunc")
        else "未触截断位⇒按留底全文判"))
    return hits, tried, "；".join(basis)


def _pairs(run):
    """把 (动作类型, 载荷文本) 配成对：两者在生成期同序生成（`generate_case` 里同一列表推导）。"""
    types = list(run.get("types") or [])
    pay = [x for x in (run.get("items") or [])[:run.get("n_payloads", len(run.get("items") or []))]]
    if not types or len(types) != len(pay):
        return []
    return list(zip(types, pay))


def _judged_errs(runs):
    """`E_unannotated`＝这条题当时还没出完，**判不了**：既不算漏、也不算过，不进分母。

    原口径写在 basis 里却说"E_unannotated 不计入分母"，代码却把这一维混在 `len(got)` 里——
    分母被"没出完的题"灌水，S3/S4 的率就被稀释（10-14 复核时发现的口径与实现对不上）。
    """
    errs = [r["err"] for r in runs]
    return [e for e in errs if e != "E_unannotated"], sum(1 for e in errs if e == "E_unannotated")


def axis_s3(cases):
    """S3 遗漏（欠产）：直接沿用生成期 `j1_verdict` 落的逐跑 `err`（不重算旧数）。"""
    e3 = judged = unann = 0
    cells, unknown = [], []
    for c in cases:
        if not c["comparable"]:
            unknown.append("%s（%s）" % (c["anon"], "；".join(c["reasons"])))
            continue
        runs = _usable_runs_of(c)
        if not runs:
            unknown.append("%s（三跑均无可用逐跑判定⇒不出数，不读作 0 条遗漏）" % c["anon"])
            continue
        got, n_un = _judged_errs(runs)
        unann += n_un
        e3 += got.count("E3")
        judged += len(got)
        cells.append({"q": c["anon"], "runs": len(got), "E3": got.count("E3"),
                      "err_spread": "/".join(sorted({g or "过" for g in got})) or "无",
                      "判不了跑": n_un,
                      "依据": "留底逐跑 `err`（生成期 j1_verdict 结果）"})
    return _axis("S3", "遗漏（欠产）", [c for c in cases if c["comparable"]],
                 [_cell("E3 跑数／有效判定跑数", e3, judged,
                        "分子＝没产出期望动作的跑；E_unannotated 不计入分母（判不了≠没漏）"
                        + ("；本轮剔出 %d 趟未标注的跑" % unann if unann else ""))],
                 detail=cells, unknown=unknown + _excluded_notes(cases),
                 state=STATE_OK if judged else STATE_UNKNOWN)


def axis_s4(cases, expect_map):
    """S4 错误事实：E4 总列 ＋ 禁用片段「豁免前／豁免后」两列（豁免逻辑一行不动）。"""
    e4 = judged = unann = 0
    before = after = exempt = 0
    den_fb = 0
    trunc_fb = 0
    cells, unknown = [], []
    for c in cases:
        cid = c["cid"]
        em = (expect_map.get(cid) or {}).get("expect") or {}
        forbidden = em.get("forbidden") or []
        if not c["comparable"]:
            unknown.append("%s（%s）" % (c["anon"], "；".join(c["reasons"])))
            continue
        runs = _usable_runs_of(c)
        if not runs:
            unknown.append("%s（三跑均无可用逐跑判定⇒误记轴不出数）" % c["anon"])
            continue
        got, n_un = _judged_errs(runs)
        unann += n_un
        e4 += got.count("E4")
        judged += len(got)
        row = {"q": c["anon"], "runs": len(got), "E4": got.count("E4"), "判不了跑": n_un,
               "留痕豁免": sum(r["exemptions"] for r in runs), "禁用片段复算": "未适用"}
        if forbidden:
            per = {"before": 0, "after": 0, "exempt": 0, "runs": 0, "trunc": 0}
            for r in runs:
                want_list = em.get("action_type")
                want_list = list(want_list) if isinstance(want_list, (list, tuple)) else [want_list]
                want_set = {str(x).strip().upper() for x in want_list if x is not None}
                # 候选口径与 `j1_verdict` 一致：只取**命中期望通道**的产出物（＋ MEMORY 通道文本）
                cands = [a.payload for a in (RebuiltAction(t, p) for t, p in _pairs(r))
                         if str(a.action_type).strip().upper() in want_set]
                if "MEMORY" in want_set:
                    cands += [{"text": x} for x in r["items"][r.get("n_payloads", 0):]]
                if not cands:
                    continue                      # 该跑没产出物⇒无从判误记，归 S3，不折算成"没错"
                bad, ex = _forbidden_check(em, cands)
                per["before"] += len(bad) + len(ex)
                per["after"] += len(bad)
                per["exempt"] += len(ex)
                per["runs"] += 1
                if r.get("trunc"):
                    per["trunc"] += 1
            if per["runs"]:
                before += per["before"]
                after += per["after"]
                exempt += per["exempt"]
                den_fb += per["runs"]
                trunc_fb += per["trunc"]
                row["载荷截断跑"] = per["trunc"]
                row["禁用片段复算"] = "%s豁免前 %d／豁免后 %d／放行 %d（判 %d 跑）" % (
                    "截断下界 " if per["trunc"] else "", per["before"], per["after"],
                    per["exempt"], per["runs"])
                if per["trunc"]:
                    row["禁用片段复算"] += "：有 %d 跑载荷只留了前 %d 字，后半句里的旧值看不见⇒豁免后 %d 是下界，不得读作该题无违规" % (
                        per["trunc"], PAYLOAD_TRUNC, per["after"])
            else:
                row["禁用片段复算"] = "该题各跑均无产出物⇒禁用片段判不了"
        else:
            row["禁用片段复算"] = "题面未标 forbidden⇒该轴不适用"
        cells.append(row)
    if judged == 0:
        return _axis("S4", "错误事实（误记）", [], [_cell("—", 0, 0, "无有效判定跑")],
                     unknown=["全部题目不可比"], state=STATE_UNKNOWN)
    lb = "；其中 %d 跑载荷触到截断位⇒两列均为下界，未看见的旧值不等于没写" % trunc_fb if trunc_fb else ""
    return _axis("S4", "错误事实（误记）", [c for c in cases if c["comparable"]], [
        _cell("E4 跑数／有效判定跑数", e4, judged,
              "分子＝产出了但参数不对（含日期算错），沿用留底逐跑 `err`；E_unannotated 不进分母"
              + ("；本轮剔出 %d 趟未标注的跑" % unann if unann else "")),
        _cell("禁用片段·豁免前／可判跑数", before, den_fb,
              "`_forbidden_check` 返回的（违规＋豁免）条数" + lb),
        _cell("禁用片段·豁免后／可判跑数", after, den_fb,
              "违规条数（豁免逻辑不改，10-05 拍板）" + lb),
        _cell("豁免放行条数", exempt, den_fb,
              "被网开一面的条数＝要能被人数出来、被复核" + lb)],
        detail=cells, unknown=unknown + _excluded_notes(cases), state=STATE_OK)


def axis_s5(shadow_log):
    """S5 补记条数：影子留痕只数条、不取正文；没给输入就是 unknown。

    留痕行的真实格式（`app/application/chat/tools.py` 的 `_logger.info`）是
    `A30 兜底 route=memory_fallback char=%d made=%s skipped=%s`，`made=` 的值是**用户原话的子串**
    拼出来的 ⇒ 它自己就可能带 " skipped="。字段边界读不出的行**不计入调用数**，也不许当成"补了 0 条"：
    后者会把"读不出"稀释成"没补记"，正是这一轴要防的假干净。
    """
    if not shadow_log:
        return _axis("S5", "补记条数", [], [_cell("—", 0, 0, "未提供 --shadow-log ⇒ 该轴未可算")],
                     unknown=["未给影子留痕输入"], state=STATE_UNKNOWN)
    if not os.path.isfile(shadow_log):
        raise SystemExit("[停手] S5 输入不存在：%s（不猜路径）" % shadow_log)
    calls = nonempty = 0
    items = 0
    dist = Counter()
    reasons = Counter()
    malformed = 0          # 命中留痕标记，但 made= 读不出／字段边界有歧义 ⇒ 不猜实值
    no_skipped = 0         # made 读得出、skipped 读不出 ⇒ 原因分类只是下界
    with open(shadow_log, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if SHADOW_MARK not in ln:
                continue
            if ln.count(SKIPPED_MARK) != 1:
                malformed += 1            # 没有 skipped=（格式变了）或出现多次（正文撞了分隔串）
                continue
            made = _field(ln, "made=", SKIPPED_MARK)
            if not made:
                malformed += 1            # 空值＝这一维没记下来；生成侧空补记写的是"无"，不是空串
                continue
            calls += 1
            skipped = _field(ln, "skipped=", None)
            if skipped is None:
                no_skipped += 1
            n = 0 if made == "无" else len([x for x in made.split(ITEM_SEP) if x.strip()])
            dist[n] += 1
            items += n
            nonempty += 1 if n else 0
            if skipped and skipped != "无":
                for kv in skipped.split("; "):
                    reasons[kv.split("=", 1)[0].strip()] += 1
    unknown = [] if calls else ["未读到任何可解析的兜底留痕行"]
    if malformed:
        unknown.append("留痕 %d 行缺 made=／字段边界有歧义⇒未计入调用数（没记录≠补了 0 条）" % malformed)
    if no_skipped:
        unknown.append("留痕 %d 行缺 skipped= 字段⇒原因分类为下界" % no_skipped)
    cells = [{"q": "全体 %d 次调用" % calls, "分布": "／".join("%d条×%d次" % (k, v) for k, v in sorted(dist.items())) or "无",
              "不可解析行数": malformed,
              "skipped 原因分类（只报原因名）": "／".join("%s×%d" % kv for kv in sorted(reasons.items())) or "无"}]
    state = STATE_OK if calls else STATE_UNKNOWN
    lb = "；另有 %d 行读不出字段、未计入（不许当 0 条）" % malformed if malformed else ""
    return _axis("S5", "补记条数", [], [
        _cell("made 非空次数／调用次数", nonempty, calls,
              "留痕行 `%s` 的 made= 字段只数条" % SHADOW_MARK + lb),
        _cell("补记条目总数", items, calls, "只计数，正文一律不进报表" + lb)],
        detail=cells, unknown=unknown, state=state)


def _field(line, start_marker, end_marker):
    i = line.find(start_marker)
    if i < 0:
        return None
    i += len(start_marker)
    if end_marker is None:
        return line[i:].strip()
    j = line.find(end_marker, i)
    return line[i:j].strip() if j >= 0 else None


def legacy_axis(rows, cases):
    """辅轴＝原逐字严格读数，**逐字并列**、不被新轴覆盖。

    与新轴的口径差别写死在这里：新轴（S1～S4）要求"三跑吃同一份料"才可比；旧读数是历史值，
    不因输入同源性被拒收——只在这四种硬伤下拒收（重复 cid／留底自相矛盾／指纹不可解／跑数缺失／
    留底与算式脱钩），其余照常列出，并把同源可复核的题数**单列一格**当 caveat。
    """
    by_cid = {str(c["cid"]): c for c in cases}
    hard = ("dup_cid", "artifact_conflict", "sig_unparsable", "runs_missing", "recalc_mismatch")
    n = pa = cons = cert = 0
    src_ok = 0
    dropped = []
    cells = []
    for r in rows:
        cid = str(r.get("cid") or "")
        if not cid:
            continue
        c = by_cid.get(cid) or {}
        codes = c.get("codes") or []
        if c and any(k in codes for k in hard):
            dropped.append(str(c.get("anon") or "Q??"))
            continue
        n += 1
        pa += 1 if r.get("pass_all") else 0
        cons += 1 if r.get("consistent") else 0
        cert += 1 if certified_j1(r) else 0
        src_ok += 1 if not any(k in codes for k in ("fp_mismatch", "fp_absent")) else 0
        cells.append({"q": str(c.get("anon") or "Q??"), "pass_all": bool(r.get("pass_all")),
                      "consistent": bool(r.get("consistent")), "cert": bool(certified_j1(r)),
                      "err_spread": str(r.get("err_spread") or "")})
    note = "每一跑都过 ∧ 剥钟点后逐字一致（`certified_j1` 原口径，值逐字取自留底、不重算不覆盖）"
    if dropped:
        note += "；拒收 %d 题（重复 cid／留底自相矛盾／指纹不可解／跑数缺失／与算式脱钩）：%s" % (
            len(dropped), "、".join(dropped))
    return _axis("LEGACY", "辅轴·逐字严格认证（旧口径不动）", [None] * n, [
        _cell("每一跑都过／题数", pa, n, note),
        _cell("三跑逐字一致／题数", cons, n, "`sig_recompute` 与留底 `consistent` 同口径"),
        _cell("认证／题数", cert, n, "两半同时成立才算认证，不折成单分"),
        _cell("其中三跑输入同源／题数", src_ok, n, "旧读数不因此格偏低而作废；不同源＝S1～S4 不可比的来源")],
        detail=cells)


def _cell(name, num, den, basis):
    return {"name": name, "num": num, "den": den, "basis": basis,
            "state": STATE_OK if den else STATE_UNKNOWN}


def _axis(key, label, cases, cells, detail=None, unknown=None, state=None):
    """一轴的结构：分子／分母（`cells`）＋逐条依据（`detail`）＋未可算原因（`unknown`）。"""
    detail = detail or []
    den_total = max([c["den"] for c in cells] or [0])
    st = state or (STATE_OK if den_total else STATE_UNKNOWN)
    return {"axis": key, "label": label, "state": st, "n_cases": len(cases),
            "cells": cells, "detail": detail, "unknown": unknown or [],
            "basis_note": "分母＝可比题×有效跑；unknown 题不进任何一格"}


def build_report(legs, min_samples=None):
    """legs: [(名字, rows, cases, axes...)] ⇒ 报表结构（纯函数、无时间戳 ⇒ 同输入逐字节可复现）。"""
    out = {"legs": [], "axes_supported": [], "axes_unknown": [], "anon_map": {}}
    seen_axes = set()
    for name, rows, expect_map, shadow_log, want_runs in legs:
        cases = integrity(rows, expect_map, want_runs)
        for c in cases:
            out["anon_map"][name + "/" + c["anon"]] = c["cid"]
        axes = [axis_s1(cases), axis_s2(cases, expect_map), axis_s3(cases),
                axis_s4(cases, expect_map), axis_s5(shadow_log), legacy_axis(rows, cases)]
        for a in axes:
            if a["state"] == STATE_OK and min_samples and a["cells"] and \
                    max(c["den"] for c in a["cells"]) < min_samples:
                a["state"] = STATE_INSUFFICIENT
            if a["state"] == STATE_OK:
                seen_axes.add(a["axis"])
            else:
                out["axes_unknown"].append("%s·%s：%s" % (name, a["label"], a["state"]))
        out["legs"].append({"leg": name, "n_cases": len(cases),
                            "n_comparable": sum(1 for c in cases if c["comparable"]),
                            "axes": axes})
    out["axes_supported"] = sorted(seen_axes)
    return out


def render(report):
    L = ["# A30 五轴离线报表（零 LLM／零库／零网络，只从明确传入的留底复算）", ""]
    L.append("> 五轴各自出数**不合成总分**；辅轴＝原逐字严格读数，与旧值并列、不覆盖。")
    L.append("> 输出只含匿名题号＋计数＋缺失原因；载荷正文不进本报表（真实用户数据即使匿名也不进公开面）。")
    L.append("")
    for leg in report["legs"]:
        L.append("## 腿 `%s`（题 %d，可比 %d）" % (leg["leg"], leg["n_cases"], leg["n_comparable"]))
        L.append("")
        L.append("| 轴 | 读数 | 分子／分母 | 状态 | 口径依据 |")
        L.append("|---|---|---|---|---|")
        for a in leg["axes"]:
            for c in a["cells"]:
                L.append("| %s %s | %s | %s／%s | %s | %s |" % (
                    a["axis"], a["label"], c["name"], c["num"], c["den"], c["state"], c["basis"]))
        for a in leg["axes"]:
            if a["unknown"]:
                L.append("- %s 未可算／不适用：%s" % (a["axis"], "；".join(a["unknown"][:12])))
        L.append("")
        for a in leg["axes"]:
            if not a["detail"]:
                continue
            L.append("### %s 逐条依据（S2 命中来源／S3 错因分布／S4 豁免前后，不套百分比）" % a["axis"])
            L.append("")
            L.append(_render_detail(a))
            L.append("")
    L.append("## 轴覆盖")
    L.append("")
    L.append("- 已支持：%s" % ("、".join(report["axes_supported"]) or "无"))
    L.append("- 未可算／不足：%s" % ("；".join(report["axes_unknown"]) or "无"))
    L.append("- 阈值：**未批准 ⇒ 本报表不做准入判定**（`--min-samples` 只把某轴标成「不足」，不判通过）。")
    L.append("- 私有派生物：`--map-out`（匿名题号↔原 cid）能反查真实题面，**禁止写进仓库**（工具当场拒收），"
             "任何带原数据的派生输出同理放仓库外。")
    return "\n".join(L) + "\n"


def _render_detail(a):
    rows = a["detail"]
    if a["axis"] == "S1":
        L = ["| 题 | 三跑条数 | 条数一致 | 片段集合一致 | 交叉格 |", "|---|---|---|---|---|"]
        L += ["| %s | %s | %s | %s | %s |" % (c["q"], c["counts"], "是" if c["same_n"] else "否",
                                             "是" if c["same_set"] else "否", c["note"]) for c in rows]
    elif a["axis"] == "S2":
        L = ["| 题 | 逐跑命中／期望＋依据 | 合计 |", "|---|---|---|"]
        L += ["| %s | %s | %d／%d |" % (
            c["q"],
            "　".join("跑%d %d／%d（%s）" % (p["run"], p["hit"], p["want"], p["basis"]) for p in c["runs"]),
            c["hit"], c["want"]) for c in rows]
    elif a["axis"] == "S3":
        L = ["| 题 | 判定跑数 | E3 跑数 | 判不了跑（未标注，不进分母） | 逐跑错因 | 依据 |",
             "|---|---|---|---|---|---|"]
        L += ["| %s | %d | %d | %d | %s | %s |" % (c["q"], c["runs"], c["E3"], c["判不了跑"],
                                                   c["err_spread"], c["依据"]) for c in rows]
    elif a["axis"] == "S4":
        L = ["| 题 | 跑数 | E4 跑数 | 留痕豁免条数 | 禁用片段复算（豁免前／后／放行） |", "|---|---|---|---|---|"]
        L += ["| %s | %d | %d | %d | %s |" % (c["q"], c["runs"], c["E4"], c["留痕豁免"], c["禁用片段复算"]) for c in rows]
    elif a["axis"] == "S5":
        L = ["| 样本 | 条数分布 | skipped 原因分类 | 不可解析行 |", "|---|---|---|---|"]
        L += ["| %s | %s | %s | %d |" % (c["q"], c["分布"], c["skipped 原因分类（只报原因名）"],
                                        c["不可解析行数"]) for c in rows]
    else:
        L = ["| 题 | 每一跑都过 | 逐字一致 | 认证 | 逐跑错因 |", "|---|---|---|---|---|"]
        L += ["| %s | %s | %s | %s | %s |" % (c["q"], "是" if c["pass_all"] else "否",
                                             "是" if c["consistent"] else "否",
                                             "是" if c["cert"] else "否", c["err_spread"]) for c in rows]
    return "\n".join(L)


def _require_outside_repo(path, what):
    """私有派生物（匿名题号↔原 cid 的映射＝能反查回真实题面）落进仓库，迟早跟着脱敏推送漏出去。"""
    p = Path(path).resolve()
    if p == REPO_ROOT or REPO_ROOT in p.parents:
        raise SystemExit("[停手] %s 不得写进仓库（%s）⇒ 放仓库外的留底目录" % (what, REPO_ROOT))
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="A30 五轴离线报表（只读复算，零 LLM）")
    ap.add_argument("--rows", action="append", default=[],
                    help="既有留底 jsonl（生成侧 --emit-rows），可重复；不传就拒绝出数")
    ap.add_argument("--label", action="append", default=[], help="与 --rows 同序的腿名（缺省用文件名）")
    ap.add_argument("--dataset", default="", help="J1 题面（只读 expect 判据字段）")
    ap.add_argument("--shadow-log", default="", help="影子留痕日志（S5 只数条）")
    ap.add_argument("--expect-runs", type=int, default=3, help="要求每题的跑数（默认三跑）")
    ap.add_argument("--min-samples", type=int, default=0,
                    help="样本下限：只把该轴标成「不足」，**不构成准入判定**（阈值未批准）")
    ap.add_argument("--out", default="", help="报表落盘路径（只含匿名题号与计数）")
    ap.add_argument("--map-out", default="",
                    help="【私有】匿名题号↔原 cid 映射，能反查真实题面 ⇒ 只准落仓库外，工具拒绝写进仓库")
    args = ap.parse_args(argv)
    if not args.rows:
        raise SystemExit("[停手] 没有 --rows 输入 ⇒ 无产物可复算；不猜路径、不出读数")
    if args.label and len(args.label) != len(args.rows):
        raise SystemExit("[停手] --label 数量与 --rows 不一致")
    if args.map_out:
        _require_outside_repo(args.map_out, "--map-out（题号映射，私有）")
    expect_map = load_expect(args.dataset)
    legs = []
    for i, path in enumerate(args.rows):
        name = args.label[i] if args.label else Path(path).stem
        legs.append((name, load_jsonl(path), expect_map, args.shadow_log, args.expect_runs))
    report = build_report(legs, min_samples=args.min_samples or None)
    text = render(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    if args.map_out:
        with open(args.map_out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(report["anon_map"], f, ensure_ascii=False, indent=1, sort_keys=True)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
