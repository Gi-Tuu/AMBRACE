# -*- coding: utf-8 -*-
"""A30 批 A 的 M0 读数尺：写侧兜底**到底救得回几题**（零 LLM、零库、零计费）。

两种读数分开报，别混：

- **零产出上限**（`upper_bound`，默认读数）：假设模型这一跑**什么都没落**，只让兜底补 ⇒ 判据能不能过。
  这是"兜底最多能顶到哪"的**上界**，不是认证——它的召回输入用题面种子（通常只有一条），
  而生产里召回是多条混排，`pick_recall` 的"不严格胜出就不补"会吃掉一部分。把它当认证报＝第 ⑥ 类。
- **并档**（带 `--rows`）：读生成侧留底，把模型**真产出**的通道并进去，两列并排（模型档不许因为兜底
  存在就停止单列）；留底路径由调用方给，本脚本不猜路径、不硬编码仓库外目录。

判据用的是尺子那一份 `j1_verdict`（同源，不另立口径）；兜底用的是生产那一份
`app/memory/fallback.py`（同源）。⇒ 本脚本量的是"生产真会怎么写"，不是"我以为它会怎么写"。

用法：
    backend\\.venv\\Scripts\\python.exe scripts/diagnostics/memory_fallback_replay.py
    backend\\.venv\\Scripts\\python.exe scripts/diagnostics/memory_fallback_replay.py --rows 留底.jsonl
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "backend"
for _p in (str(BACKEND), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.agent.actions import parse_actions                     # noqa: E402
from app.memory.fallback import fallback_actions                # noqa: E402
from memory_action_eval import extract_memory_channel, j1_verdict, resolve_expect_date  # noqa: E402

DATASET_DEFAULT = str(HERE / "memory_action_cases_j1j2_draft.jsonl")
SOURCE_LABEL = {"turn": "本轮原话（改口）", "recall": "召回原文", "clause": "本轮含日期从句"}


def load_cases(path=DATASET_DEFAULT):
    """读 J1 题面（j1* 前缀）。数据集不在＝报错，不许当成"0 题所以全过"（第 ⑰ 类空扫）。"""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    cases = [c for c in rows if str(c.get("cid", "")).startswith("j1")]
    if not cases:
        raise SystemExit("[M0] 题面里一条 j1 都没读到（%s）⇒ 分母为空，不许报读数" % path)
    return cases


def recall_of(case):
    return [str(s.get("content") or "").strip() for s in (case.get("seeds") or [])
            if str(s.get("content") or "").strip()]


def anchor_of(case):
    return str((case.get("time_anchor") or {}).get("as_of") or "")


def one_case(case, model_channels=()):
    """单题跑一遍兜底并判——返回 (判定结果, 兜底产出)。判据与生成侧同一份实现。"""
    exp = case.get("expect") or {}
    got = fallback_actions(case.get("turn") or "", recall_texts=recall_of(case),
                           model_channels=model_channels, base_date=anchor_of(case))
    v = j1_verdict(exp, list(got["actions"]), expect_date=resolve_expect_date(case.get("time_anchor")),
                   memory_texts=list(got["memory_texts"]))
    return v, got


def upper_bound(cases):
    """零产出上限：模型什么都没落时，兜底能顶过几题。返回 (过数, 总数, 逐题明细)。"""
    detail, passed = [], 0
    for c in cases:
        v, got = one_case(c)
        passed += 1 if v["pass"] else 0
        detail.append({"cid": c["cid"], "pass": bool(v["pass"]), "err": v.get("err") or "",
                       "why": v.get("why") or "", "made": [a.action_type for a in got["actions"]]
                       + (["MEMORY"] if got["memory_texts"] else []),
                       "source": got.get("content_source") or "",
                       "skipped": dict(got["skipped"] or {})})
    return passed, len(cases), detail


def merged_with_rows(cases, rows):
    """并档：逐跑把模型真产出的通道并进去，重判。返回逐跑列表（模型档/系统＋模型档各一列）。"""
    by_cid = {}
    for r in rows:
        by_cid.setdefault(str(r.get("cid")), []).append(r)
    out = []
    for c in cases:
        if c["cid"] not in by_cid:
            continue
        exp = c.get("expect") or {}
        expect_date = resolve_expect_date(c.get("time_anchor"))
        for prow in sorted(by_cid[c["cid"]], key=lambda x: int(x.get("run") or 1)):
            text = prow.get("reply_text") or ""
            actions = parse_actions(text, base_date=anchor_of(c))
            chan = extract_memory_channel(text)
            have = {getattr(a, "action_type", "") for a in actions} | ({"MEMORY"} if chan else set())
            mv = j1_verdict(exp, list(actions), expect_date=expect_date, memory_texts=list(chan))
            got = fallback_actions(c.get("turn") or "", recall_texts=recall_of(c),
                                   model_channels=have, base_date=anchor_of(c))
            fv = j1_verdict(exp, list(actions) + list(got["actions"]), expect_date=expect_date,
                            memory_texts=list(chan) + list(got["memory_texts"]))
            out.append({"cid": c["cid"], "run": prow.get("run"),
                        "model_pass": bool(mv["pass"]), "merged_pass": bool(fv["pass"]),
                        "model_err": mv.get("err") or "", "merged_err": fv.get("err") or "",
                        "model_channels": sorted(x for x in have if x),
                        "fallback": [a.action_type for a in got["actions"]]
                        + (["MEMORY"] if got["memory_texts"] else []),
                        "truncated": len(text) >= 900})
    return out


def certify_projection(cases, rows):
    """认证推算（不是读数）：把「模型这一跑整条没落」换成「系统那一条确定性文本」，再算两腿。

    为什么必须单列一个推算：留底里**只有首跑存了逐字正文**（第 2/3 跑只有指纹与起头），
    所以拿留底重跑全文并档做不到 30 跑；而指纹（sig）是逐跑都存了的 ⇒ 可以按"来源替换"推算。
    规则逐条写死，不靠记忆：
      - 那一跑**本来就过** ⇒ 沿用模型指纹（兜底不动已落的东西）；
      - 那一跑 **E3（整条没产出）** 且该题兜底能过 ⇒ 换成兜底指纹（系统写的，三跑同一份）；
      - 那一跑 **E4/其它错** ⇒ 保持不过——**兜底不覆盖模型已经写出来的东西**，这是设计边界不是缺陷。
    ⚠ 混合来源（有的跑模型写、有的跑系统写）在「逐字一致」这一维**必然不一致**——那是两种笔迹，
    不是模型不稳。这一列存在的意义就是把这个事实摆出来，而不是替它遮掉。
    """
    passed, _n_cases, detail = upper_bound(cases)
    bound = {d["cid"]: d for d in detail}
    by_cid = {}
    for r in rows:
        by_cid.setdefault(str(r.get("cid")), []).append(r)
    out, cert_n, allpass_n, cons_n = [], 0, 0, 0
    for c in cases:
        runs = by_cid.get(c["cid"])
        if not runs:
            continue
        runs = sorted(runs, key=lambda x: int(x.get("run") or 1))
        first, others = runs[0], (runs[0].get("other_runs") or [])
        per_run = [{"run": 1, "pass": bool(first.get("pass")), "err": first.get("err") or "",
                    "sig": first.get("sig") or ""}]
        per_run += [{"run": i + 2, "pass": bool(o.get("pass")), "err": o.get("err") or "",
                     "sig": o.get("sig") or ""} for i, o in enumerate(others)]
        b = bound.get(c["cid"], {})
        fb_sig, fb_ok = _fallback_sig(c), bool(b.get("pass"))
        merged = []
        for r in per_run:
            if r["pass"]:
                merged.append((True, r["sig"], "模型"))
            elif r["err"] == "E3" and fb_ok:
                merged.append((True, fb_sig, "兜底"))
            else:
                merged.append((False, r["sig"], "模型"))
        ap = all(m[0] for m in merged)
        cons = len({m[1] for m in merged}) == 1
        cert = ap and cons
        allpass_n += 1 if ap else 0
        cons_n += 1 if cons else 0
        cert_n += 1 if cert else 0
        out.append({"cid": c["cid"], "n_runs": len(merged), "pass_all": ap, "consistent": cons,
                    "cert": cert, "sources": "/".join(m[2] for m in merged),
                    "run_errs": "/".join(r["err"] or "过" for r in per_run)})
    return {"per_case": out, "pass_all": allpass_n, "consistent": cons_n, "cert": cert_n,
            "n": len(out), "fallback_bound": (passed, len(cases))}


def _fallback_sig(case):
    """兜底那条文本的**指纹文本**——走尺子同一个 `run_sig_text`，维度与截断口径照 `generate_case` 复刻。

    留底存的就是 `run_sig_text` 的输出，所以只有用同一个渲染函数（含 `_payload_text(...)[:70]`
    与 `mem_channel` 不截断这两条口径），替换后的指纹才与留底可比。自己拼一遍字符串＝第 ⑥ 类
    （评测算术必须是纯函数）＋第 ⑧ 类（守卫咬的是占位符不是内容）。
    """
    from memory_action_eval import run_sig_text, _payload_text
    v, got = one_case(case)
    dates = sorted({str((a.payload or {}).get("date") or "")[:10] for a in got["actions"]
                    if (a.payload or {}).get("date")})
    row = {"pass": bool(v["pass"]),
           "actions_seen": [a.action_type for a in got["actions"]],
           "dates_seen": dates,
           "exemptions": list(v.get("exempted") or []),
           "payloads": [_payload_text(a.payload).strip()[:70] for a in got["actions"]],
           "mem_channel": list(got["memory_texts"])}
    return run_sig_text(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DATASET_DEFAULT)
    ap.add_argument("--rows", default="", help="生成侧留底 jsonl（外部腿字段亦可），给了就加报并档")
    args = ap.parse_args()

    cases = load_cases(args.dataset)
    passed, total, detail = upper_bound(cases)
    print("[M0 兜底读数] 题数=%d  LLM 调用=0  库写入=0  判据＝尺子那一份 j1_verdict" % total)
    print("  ⚠ 「零产出上限」的召回输入是题面种子（生产是多条混排）⇒ **不能当认证报**\n")
    print("| cid | 期望通道 | 兜底补出什么 | 零产出上限 | 没补/没过的原因 |")
    print("|---|---|---|---|---|")
    for c, d in zip(cases, detail):
        made = ",".join(d["made"]) or "什么都没补"
        if made != "什么都没补":
            made += "←" + SOURCE_LABEL.get(d["source"], "?")
        reason = d["why"] or "；".join("%s=%s" % kv for kv in d["skipped"].items())
        print("| %s | %s | %s | %s | %s |" % (
            d["cid"], json.dumps(c.get("expect", {}).get("action_type"), ensure_ascii=False),
            made, "过" if d["pass"] else "不过（%s）" % d["err"], reason[:70]))
    print("\n**零产出上限＝%d／%d**（模型整条不落时，兜底能顶到几题）" % (passed, total))

    if not args.rows:
        return
    rows = []
    with open(args.rows, encoding="utf-8-sig") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    merged = merged_with_rows(cases, rows)
    if not merged:
        print("\n[并档] 留底 %s 与本题面集**没有一条对得上** ⇒ 不报数（cid 变了就别用旧留底）"
              % os.path.basename(args.rows))
        return
    mp = sum(1 for m in merged if m["model_pass"])
    fp = sum(1 for m in merged if m["merged_pass"])
    print("\n## 并档（留底 %s，逐跑合并兜底后重判，共 %d 跑）\n" % (os.path.basename(args.rows), len(merged)))
    print("| cid | 跑 | 模型已产出 | 兜底补出 | 模型档 | 系统＋模型档 |")
    print("|---|---|---|---|---|---|")
    for m in merged:
        print("| %s | %s | %s | %s | %s | %s |" % (
            m["cid"], m["run"], ",".join(m["model_channels"]) or "无",
            ",".join(m["fallback"]) or "不补",
            "过" if m["model_pass"] else "不过", "过" if m["merged_pass"] else "不过"))
    trunc = sum(1 for m in merged if m["truncated"])
    print("\n**并档：模型档 %d／%d＝%.0f%% ⇒ 系统＋模型档 %d／%d＝%.0f%%**" % (
        mp, len(merged), 100.0 * mp / len(merged), fp, len(merged), 100.0 * fp / len(merged)))
    if trunc:
        print("⚠ 其中 %d 跑的留底正文达到 900 字上限（可能被截断）⇒ 这些跑的并档只够参考" % trunc)
    print("⇒ 两列必须并排看：兜底只补模型没落的通道，**模型档不许因为兜底存在就停止单列**。")

    proj = certify_projection(cases, rows)
    print("\n## 认证推算（逐跑指纹按来源替换）\n")
    print("为什么是推算不是读数：留底**只有首跑存了逐字正文**（第 2/3 跑只有指纹），"
          "所以不能拿留底重跑 30 次全文并档；但指纹是逐跑都存了的 ⇒ 可以按来源替换着算。\n")
    print("| cid | 跑数 | 逐跑原判 | 替换后来源 | 每一跑都过 | 三跑一致 | 认证 |")
    print("|---|---|---|---|---|---|---|")
    for p in proj["per_case"]:
        print("| %s | %s | %s | %s | %s | %s | %s |" % (
            p["cid"], p["n_runs"], p["run_errs"], p["sources"],
            "过" if p["pass_all"] else "不过", "一致" if p["consistent"] else "不一致",
            "认证" if p["cert"] else "—"))
    n = proj["n"]
    print("\n**推算：每一跑都过 %d／%d ⇒ 三跑逐字一致 %d／%d ⇒ 认证 %d／%d**" % (
        proj["pass_all"], n, proj["consistent"], n, proj["cert"], n))
    print("⚠ 混合来源（有的跑模型写、有的跑系统写）在「逐字一致」这一维**必然不一致**——那是两种笔迹，"
          "不是模型不稳。这一列要往上走，缺的是**把模型文本也定形成同一模板**（批 B），不是把兜底再多写一点。")


if __name__ == "__main__":
    main()
