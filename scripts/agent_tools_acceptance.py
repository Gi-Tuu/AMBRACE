# -*- coding: utf-8 -*-
"""M5 验收矩阵（本地/staging）：spec、run 生命周期、审计、记忆、失败关闭。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\agent_tools_acceptance.py
  ... --base http://127.0.0.1:8000 --token <Bearer> [--device character_id]
逐项输出 PASS/FAIL，并写 JSON 报告到 --out。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def call(base: str, token: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(base + path, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default="")
    ap.add_argument("--character-id", type=int, default=0)
    ap.add_argument("--out", default="agent_tools_acceptance_report.json")
    a = ap.parse_args()
    results = []

    def check(name: str, ok: bool, detail: str = ""):
        results.append({"check": name, "ok": bool(ok), "detail": detail[:300]})
        print(("PASS " if ok else "FAIL ") + name + ((" | " + detail[:160]) if detail else ""))

    # 1) 注册 spec
    st, spec = call(a.base, "", "GET", "/openapi.json")
    paths = spec.get("paths") or {}
    schemas = (spec.get("components") or {}).get("schemas") or {}
    check("spec 4 工具", st == 200 and set(paths) == {
        "/api/agent/tools/device_observe", "/api/agent/tools/device_act",
        "/api/agent/tools/memory_recall", "/api/agent/tools/memory_remember"}, str(list(paths)))
    check("spec 无 internal/runs", "/internal/" not in json.dumps(spec) and "/runs" not in json.dumps(spec))
    check("spec 声明 BearerAuth", "BearerAuth" in json.dumps(spec))
    check("spec 无后台模型泄漏",
          not ({"CharacterResponse", "MemoryResponse", "LlmConfigUpsert"} & set(schemas)))

    if not a.token:
        print("（未提供 token，跳过需要鉴权的用例）")
        ok = all(r["ok"] for r in results)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        return 0 if ok else 1

    # 2) 未授权
    st, _ = call(a.base, "", "GET", "/api/agent/tools/runs")
    check("无 token 访问被拒", st in (401, 403), f"status={st}")

    # 3) run 生命周期
    st, run = call(a.base, a.token, "POST", "/api/agent/tools/runs",
                   {"task": "acceptance", "deadline_s": 30})
    rid = run.get("run_id", "")
    check("run 创建", st == 200 and run.get("status") == "running", str(run)[:160])
    st, got = call(a.base, a.token, "GET", f"/api/agent/tools/runs/{rid}")
    check("run 查询", st == 200 and got.get("run_id") == rid)
    st, can = call(a.base, a.token, "POST", f"/api/agent/tools/runs/{rid}/cancel")
    check("cancel 无伪造 DONE",
          st == 200 and can.get("status") == "cancelled"
          and not [e for e in can.get("events", []) if e.get("type") == "done"]
          and any(e.get("type") == "run_terminal" for e in can.get("events", [])))

    # 4) 记忆 recall + 审计
    st, rec = call(a.base, a.token, "POST", "/api/agent/tools/memory_recall",
                   {"character_id": a.character_id or 13, "query": "雨", "k": 2})
    check("memory_recall", st == 200 and isinstance(rec.get("items"), list), f"items={len(rec.get('items', []))}")
    st, audit = call(a.base, a.token, "GET", "/api/agent/tools/audit?tool=memory_recall&limit=1")
    check("memory_recall 审计可查", st == 200 and len(audit.get("items", [])) >= 1)
    st, runs = call(a.base, a.token, "GET", "/api/agent/tools/runs")
    check("run registry 统计可查", st == 200 and "active" in runs, str(runs)[:120])

    # 5) 失败关闭（无快照的 act）
    st, res = call(a.base, a.token, "POST", "/api/agent/tools/device_act",
                   {"snapshot_id": "missing", "ref": "@x:e1", "verb": "click"})
    check("无效快照失败关闭", st in (409, 400), f"status={st} code={(res.get('detail') or {}).get('code')}")

    ok = all(r["ok"] for r in results)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n{sum(1 for r in results if r['ok'])}/{len(results)} passed -> {a.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
