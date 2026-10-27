#!/usr/bin/env python
"""测试耗时剖析：解析 JUnit XML，定位回归变慢的模块与用例（只读，不跑测试）。

用法（在仓库根）：
    backend\\.venv\\Scripts\\python.exe scripts\\test_profile.py <junit.xml> [--main-tests <dir>]
    backend\\.venv\\Scripts\\python.exe scripts\\test_profile.py <before.xml> <after.xml>

参数：
  --main-tests DIR  给出「对照树的 tests 目录」（如相对路径 backend/tests）时，
                    额外区分「新增模块」与「既有模块」，便于判断优化收益来自哪里。

输出：总数/墙钟口径之外的用例耗时合计、失败/跳过计数、Top 模块、Top 慢例、≥3s 用例占比；
      给两份 XML 时额外输出「用例集差异」与「共同用例的耗时变化」。
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def load(path: Path) -> list[dict]:
    rows = []
    for case in ET.parse(path).getroot().iter("testcase"):
        rows.append({
            "file": case.get("classname", "?"),
            "name": case.get("name", "?"),
            "time": float(case.get("time", 0)),
            "failed": bool(list(case.iter("failure"))),
            "error": bool(list(case.iter("error"))),
            "skipped": bool(list(case.iter("skipped"))),
        })
    return rows


def summarize(rows: list[dict], main_tests: Path | None, label: str) -> None:
    total = sum(r["time"] for r in rows)
    per_file: Counter[str] = Counter()
    count: Counter[str] = Counter()
    for r in rows:
        per_file[r["file"]] += r["time"]
        count[r["file"]] += 1

    print(f"== {label} ==")
    print(f"用例 {len(rows)}｜耗时合计 {total:.1f}s = {total / 60:.1f} 分钟｜平均 {total / max(1, len(rows)):.3f} s/例")
    print(f"failed={sum(r['failed'] for r in rows)} error={sum(r['error'] for r in rows)} "
          f"skipped={sum(r['skipped'] for r in rows)}")

    if main_tests is not None:
        new_files, old_files = {}, {}
        for name, seconds in per_file.items():
            target = main_tests / (name.split(".")[-1] + ".py")
            (old_files if target.exists() else new_files)[name] = seconds
        new_t = sum(new_files.values())
        new_c = sum(count[f] for f in new_files)
        old_t = sum(old_files.values())
        old_c = sum(count[f] for f in old_files)
        print(f"新增模块 {len(new_files)} 个/{new_c} 例/{new_t:.0f}s ({new_t / total * 100:.1f}%)｜"
              f"既有模块 {len(old_files)} 个/{old_c} 例/{old_t:.0f}s ({old_t / total * 100:.1f}%)")
        if old_c:
            print(f"既有模块平均 {old_t / old_c:.3f} s/例；新增模块平均 {new_t / max(1, new_c):.3f} s/例")

    print("\nTop 15 模块：")
    for name, seconds in per_file.most_common(15):
        print(f"  {seconds:8.1f}s / {count[name]:4d} 例 ({seconds / count[name]:6.2f}s/例)  {name}")

    print("\nTop 20 慢例：")
    for r in sorted(rows, key=lambda x: -x["time"])[:20]:
        print(f"  {r['time']:8.2f}s  {r['file']}::{r['name'][:56]}")

    slow_like = [r for r in rows if r["time"] >= 3.0]
    print(f"\n≥3s 的用例 {len(slow_like)} 个，占 {sum(r['time'] for r in slow_like) / max(1.0, total) * 100:.1f}% 耗时")


def compare(before: list[dict], after: list[dict]) -> None:
    a = {f"{r['file']}::{r['name']}": r for r in before}
    b = {f"{r['file']}::{r['name']}": r for r in after}
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    print("\n== 两份对照 ==")
    print(f"仅 A 有 {len(only_a)} 例｜仅 B 有 {len(only_b)} 例｜共同 {len(set(a) & set(b))} 例")
    for nid in only_a[:10]:
        print("  - ", nid)
    for nid in only_b[:10]:
        print("  + ", nid)
    common = set(a) & set(b)
    if common:
        ta = sum(a[k]["time"] for k in common)
        tb = sum(b[k]["time"] for k in common)
        print(f"共同用例耗时：A {ta:.0f}s → B {tb:.0f}s（{(tb - ta) / max(1e-9, ta) * 100:+.1f}%）")
        status_diff = [k for k in common
                       if (a[k]["failed"], a[k]["error"], a[k]["skipped"]) !=
                       (b[k]["failed"], b[k]["error"], b[k]["skipped"])]
        print(f"共同用例状态不一致：{len(status_diff)} 例")
        for nid in status_diff[:10]:
            print("  ! ", nid)
        worst = sorted(common, key=lambda k: b[k]["time"] - a[k]["time"], reverse=True)[:10]
        print("  B 相对 A 变慢最多的 10 例：")
        for nid in worst:
            print(f"    {a[nid]['time']:7.2f}s → {b[nid]['time']:7.2f}s  {nid[:90]}")


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    args = list(argv)
    main_tests = None
    if "--main-tests" in args:
        idx = args.index("--main-tests")
        if idx + 1 >= len(args):
            print("--main-tests 需要一个目录参数", file=sys.stderr)
            return 2
        main_tests = Path(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    files = [Path(x) for x in args]
    for f in files:
        if not f.is_file():
            print(f"文件不存在: {f}", file=sys.stderr)
            return 2
    sets = [load(f) for f in files]
    summarize(sets[0], main_tests, files[0].name)
    if len(sets) > 1:
        compare(sets[0], sets[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
