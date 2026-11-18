# -*- coding: utf-8 -*-
"""M6 SSE 验收 artifact：20 并发 run + 事件流，记录 offset 序列与 active_after。"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def call(base, token, method, path, body=None, headers=None, timeout=60):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return r.status, r.read().decode()


def one(base, token, idx):
    st, body = call(base, token, "POST", "/api/agent/tools/runs",
                    {"task": f"m6-{idx}", "deadline_s": 30})
    rid = json.loads(body)["run_id"]
    # 造 4 个业务事件（2 组 call/result），验证 offset=1..N 连续性
    for i in range(2):
        call(base, token, "POST", f"/api/agent/tools/internal/runs/{rid}/events",
             {"type": "tool_call", "data": {"tool": f"t{i}"}})
        call(base, token, "POST", f"/api/agent/tools/internal/runs/{rid}/events",
             {"type": "tool_result", "data": {"tool": f"t{i}", "ok": True}})
    call(base, token, "POST", f"/api/agent/tools/runs/{rid}/cancel")
    time.sleep(2.4)
    st, stream = call(base, token, "GET", f"/api/agent/tools/runs/{rid}/events",
                      headers={"Last-Event-ID": "0"})
    st2, stream2 = call(base, token, "GET", f"/api/agent/tools/runs/{rid}/events",
                        headers={"Last-Event-ID": "0"})
    ids = [int(line.split(": ", 1)[1]) for line in stream.splitlines()
           if line.startswith("id: ")]
    ids2 = [int(line.split(": ", 1)[1]) for line in stream2.splitlines()
            if line.startswith("id: ")]
    _, summary = call(base, token, "GET", f"/api/agent/tools/runs/{rid}")
    summ = json.loads(summary)
    return {"run_id": rid, "status": st, "offsets": ids, "offsets_client2": ids2,
            "terminal": "run_terminal" in stream, "eof": "event: eof" in stream,
            "last_offset": summ.get("last_offset"),
            "summary_events": [e.get("offset") for e in summ.get("events", [])]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", required=True)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--out", default="docs/yai/artifacts/m6-sse-acceptance.json")
    a = ap.parse_args()

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        runs = list(ex.map(lambda i: one(a.base, a.token, i), range(a.concurrency)))
    st, body = call(a.base, a.token, "GET", "/api/agent/tools/runs")
    active = json.loads(body).get("active")

    ok = all(
        r["status"] == 200 and r["terminal"] and r["eof"]
        and r["offsets"] == list(range(1, 6))          # 4 业务事件 + run_terminal
        and r["offsets"] == r["offsets_client2"]        # 两个订阅者序列一致
        and r["last_offset"] == 5
        and r["summary_events"] == [1, 2, 3, 4, 5]
        for r in runs)
    art = {"concurrency": a.concurrency, "runs": runs,
           "active_after": active, "passed": ok,
           "elapsed_s": round(time.time() - t0, 2)}
    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(art, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": ok, "runs": len(runs), "active_after": active,
                      "elapsed_s": art["elapsed_s"], "out": str(out)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
