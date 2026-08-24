# -*- coding: utf-8 -*-
"""Token 消耗聚合报告：扫描 backend/data/logs/app.log* 的 LLM usage 行，按天统计。

用法：
  cd <项目根目录>
  backend/.venv/Scripts/python.exe scripts\token_budget_report.py [--days N]
"""
import os
import re
import sys
import glob
import argparse

import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_USAGE_RE = re.compile(r"LLM usage: prompt=(\d+) completion=(\d+) total=(\d+) reasoning=([\dNone]+)")
_DAY_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def main(days: int | None) -> int:
    files = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "data", "logs", "app.log*")))
    agg: dict[str, list] = {}
    n_total = 0
    for fp in files:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _USAGE_RE.search(line)
                if not m:
                    continue
                dm = _DAY_RE.match(line)
                day = dm.group(1) if dm else "unknown"
                p, c, t = int(m.group(1)), int(m.group(2)), int(m.group(3))
                r = int(m.group(4)) if m.group(4) != "None" else 0
                a = agg.setdefault(day, [0, 0, 0, 0, 0])
                a[0] += 1; a[1] += p; a[2] += c; a[3] += t; a[4] += r
                n_total += 1
    rows = sorted(agg.items())
    if days:
        rows = rows[-days:]
    print(f"{'日期':<12}{'次数':>6}{'prompt':>11}{'completion':>12}{'total':>10}{'reasoning':>10}  均值/次")
    for day, (n, p, c, t, r) in rows:
        print(f"{day:<12}{n:>6}{p:>11,}{c:>12,}{t:>10,}{r:>10,}  {t // n:>8,}")
    print(f"\n覆盖日志文件 {len(files)} 个，共 {n_total:,} 次 LLM 调用")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Token 消耗按天聚合")
    ap.add_argument("--days", type=int, default=None, help="只显示最近 N 天（默认全部）")
    args = ap.parse_args()
    sys.exit(main(args.days))
