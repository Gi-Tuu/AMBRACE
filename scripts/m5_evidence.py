# -*- coding: utf-8 -*-
r"""M5 证据采集：为验收报告 12 项逐项产出独立 artifact（JSON）。

用法（staging 在 8010、生产 8000、手机在 8000 上可用）：
  python scripts/m5_evidence.py --staging-token <t> --prod-token <t> \
      --staging-db <path-to-staging.db> \
      --out docs/yai/artifacts/m5-evidence.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path


def call(base, token, method, path, body=None, timeout=60):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", default="http://127.0.0.1:8010")
    ap.add_argument("--prod", default="http://127.0.0.1:8000")
    ap.add_argument("--core", default="http://127.0.0.1:8001")
    ap.add_argument("--staging-token", required=True)
    ap.add_argument("--prod-token", required=True)
    ap.add_argument("--staging-db", required=True)
    ap.add_argument("--stability-runs", type=int, default=20)
    ap.add_argument("--out", default="docs/yai/artifacts/m5-evidence.json")
    a = ap.parse_args()

    ev = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "items": {}}

    def item(key, ok, **detail):
        ev["items"][key] = {"ok": bool(ok), **detail}
        print(("PASS " if ok else "FAIL ") + key)

    # 1 spec
    st, spec = call(a.staging, "", "GET", "/openapi.json")
    paths = spec.get("paths") or {}
    item("01_spec", st == 200 and len(paths) == 4 and "BearerAuth" in json.dumps(spec)
         and "/internal/" not in json.dumps(spec) and "/runs" not in json.dumps(spec),
         status=st, paths=list(paths))

    # 2 未授权
    st, _ = call(a.staging, "", "GET", "/api/agent/tools/runs")
    item("02_unauthorized", st in (401, 403), status=st)

    # 3 run 生命周期
    st, run = call(a.staging, a.staging_token, "POST", "/api/agent/tools/runs",
                   {"task": "evidence", "deadline_s": 30})
    rid = run.get("run_id", "")
    st2, can = call(a.staging, a.staging_token, "POST", f"/api/agent/tools/runs/{rid}/cancel")
    item("03_run_lifecycle", st == 200 and st2 == 200 and can.get("status") == "cancelled"
         and not [e for e in can.get("events", []) if e.get("type") == "done"],
         run_id=rid, status=can.get("status"),
         events=[e.get("type") for e in can.get("events", [])])

    # 4 deadline
    st, run = call(a.staging, a.staging_token, "POST", "/api/agent/tools/runs",
                   {"task": "deadline", "deadline_s": 0.2})
    time.sleep(0.8)
    st, got = call(a.staging, a.staging_token, "GET", f"/api/agent/tools/runs/{run['run_id']}")
    item("04_deadline", got.get("status") == "expired"
         and any(e.get("type") == "run_terminal" for e in got.get("events", [])),
         status=got.get("status"), events=[e.get("type") for e in got.get("events", [])])

    # 5 记忆 roundtrip（staging 库 + 直删清理）
    marker = "M5evidence-" + str(int(time.time()))
    st, rem = call(a.staging, a.staging_token, "POST", "/api/agent/tools/memory_remember",
                   {"character_id": 13, "kind": "fact", "text": marker, "tags": ["m5"]})
    mid = rem.get("id")
    st2, rec = call(a.staging, a.staging_token, "POST", "/api/agent/tools/memory_recall",
                    {"character_id": 13, "query": marker, "k": 5})
    hit_before = mid in [i["id"] for i in rec.get("items", [])]
    deleted = None
    if mid is not None and Path(a.staging_db).exists():
        con = sqlite3.connect(a.staging_db)
        try:
            con.execute("DELETE FROM memories WHERE id = ?", (mid,)); con.commit()
            deleted = True
        finally:
            con.close()
    st3, rec2 = call(a.staging, a.staging_token, "POST", "/api/agent/tools/memory_recall",
                     {"character_id": 13, "query": marker, "k": 5})
    item("05_memory_roundtrip", st == 200 and rem.get("applied") is True and hit_before
         and deleted and mid not in [i["id"] for i in rec2.get("items", [])],
         remember_status=st, id=mid, recall_hit=hit_before, deleted=deleted,
         recall_after=len(rec2.get("items", [])))

    # 6 真机成功链路（生产 8000 + 手机）
    _st, obs = call(a.prod, a.prod_token, "POST", "/api/agent/tools/device_observe",
                    {"target_app": "com.android.settings"}, timeout=60)
    items = obs.get("items", [])
    cnt = Counter(i["name"] for i in items)
    target = next((i for i in items if i["name"] in ("刷新", "网络助理") and cnt[i["name"]] == 1), None)
    act_ok = False
    act_res = {}
    if target:
        st2, act_res = call(a.prod, a.prod_token, "POST", "/api/agent/tools/device_act",
                            {"snapshot_id": obs["snapshot_id"], "ref": target["ref"], "verb": "click"},
                            timeout=60)
        act_ok = st2 == 200 and act_res.get("executed") is True
    item("06_device_success", bool(items) and act_ok,
         refs=len(items), target=(target or {}).get("name"), act=act_res.get("status"),
         path=act_res.get("path"))

    # 7 403（staging：internal observe + 无策略 click）
    tree = {"role": "window", "name": "w",
            "children": [{"role": "button", "name": "确定", "available_actions": ["Click"]}]}
    st, iobs = call(a.staging, a.staging_token, "POST", "/api/agent/tools/internal/observe",
                    {"tree": tree, "target_app": "com.android.settings"})
    ref = iobs["items"][0]["ref"]
    st2, r403 = call(a.staging, a.staging_token, "POST", "/api/agent/tools/device_act",
                     {"snapshot_id": iobs["snapshot_id"], "ref": ref, "verb": "click"})
    item("07_approval_403", st2 == 403 and (r403.get("detail") or {}).get("code") == "APPROVAL_REQUIRED",
         status=st2, code=(r403.get("detail") or {}).get("code"))

    # 8 409
    st, r409 = call(a.staging, a.staging_token, "POST", "/api/agent/tools/device_act",
                    {"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    item("08_stale_409", st == 409 and (r409.get("detail") or {}).get("code") == "STALE_REF",
         status=st, code=(r409.get("detail") or {}).get("code"))

    # 9 503（staging 无设备）
    t0 = time.time()
    st, r503 = call(a.staging, a.staging_token, "POST", "/api/agent/tools/device_observe",
                    {"target_app": "com.android.settings"}, timeout=90)
    item("09_device_503", st == 503
         and (r503.get("detail") or {}).get("code") == "DEVICE_CAPTURE_UNAVAILABLE",
         status=st, elapsed_s=round(time.time() - t0, 1))

    # 10 审计（设备 + 记忆）
    def audit(base, token, tool):
        st, d = call(base, token, "GET", f"/api/agent/tools/audit?tool={tool}&limit=5")
        return st, len(d.get("items", []))

    a1 = audit(a.prod, a.prod_token, "device_observe")
    a2 = audit(a.prod, a.prod_token, "device_act")
    a3 = audit(a.staging, a.staging_token, "memory_remember")
    item("10_audit", all(st == 200 and n >= 1 for st, n in (a1, a2, a3)),
         device_observe=a1, device_act=a2, memory_remember=a3)

    # 11 稳定性（staging，N run + 2 deadline）
    t0 = time.time(); ok = 0; bad = []
    for i in range(a.stability_runs):
        st, r = call(a.staging, a.staging_token, "POST", "/api/agent/tools/runs",
                     {"task": f"stability-{i}", "deadline_s": 30})
        if st == 200 and r.get("run_id"):
            call(a.staging, a.staging_token, "POST", f"/api/agent/tools/runs/{r['run_id']}/cancel")
            ok += 1
        else:
            bad.append((i, st))
    for i in range(2):
        st, r = call(a.staging, a.staging_token, "POST", "/api/agent/tools/runs",
                     {"task": f"dl-{i}", "deadline_s": 0.2})
        time.sleep(0.6)
        st, g = call(a.staging, a.staging_token, "GET", f"/api/agent/tools/runs/{r['run_id']}")
        if g.get("status") != "expired":
            bad.append(("deadline", i, g.get("status")))
    st, lst = call(a.staging, a.staging_token, "GET", "/api/agent/tools/runs")
    item("11_stability", ok == a.stability_runs and not bad and lst.get("active") == 0,
         runs_ok=ok, bad=bad, active_after=lst.get("active"),
         elapsed_s=round(time.time() - t0, 2))

    # 12 冷启动（Core health commit + 两仓库 git）
    st, health = call(a.core, "", "GET", "/health")
    core_commit = (health or {}).get("commit", "")
    def git_info(path):
        try:
            head = subprocess.check_output(["git", "-C", path, "rev-parse", "HEAD"],
                                           text=True).strip()
            dirty = subprocess.check_output(["git", "-C", path, "status", "--porcelain"],
                                            text=True).strip()
            return head, (dirty == "")
        except Exception:
            return "", False
    ambr_commit, ambr_clean = git_info(os.environ.get("AMBRACE_ROOT", str(Path(__file__).resolve().parents[1])))
    core_repo_commit, core_clean = git_info(r"E:\Competition\YAI Agent Core")
    item("12_cold_start", st == 200 and core_commit.startswith(core_repo_commit[:7])
         and bool(ambr_commit), core_health=st, core_commit=core_commit,
         ambr_repo_commit=ambr_commit, ambr_clean=ambr_clean,
         core_repo_commit=core_repo_commit, core_clean=core_clean)

    passed = sum(1 for v in ev["items"].values() if v["ok"])
    ev["summary"] = {"passed": passed, "total": len(ev["items"])}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{passed}/{len(ev['items'])} -> {out}")
    return 0 if passed == len(ev["items"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
