# -*- coding: utf-8 -*-
"""A39 闸②影子读数（只读日志、零计费、零写库）。

为什么要一个脚本而不是"到时候 grep 一下"：影子档的价值全在**按通道×档位×原因分档**，
只有一个总数就判不了"实闸开了会少发什么"。而且判效脚本本身最容易犯的错是
**拿自己造的假行测自己的解析器**（永远绿），所以这里的正对照走真格式：
行文本由生产侧同一个 `shadow_mark()` 与 `freshness.py` 里那条 `_logger.info` 的格式串生成。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\diagnostics\\freshness_shadow_report.py [--days 5]
        [--log-dir backend/data/logs] [--min-samples 8] [--json]
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
from collections import Counter, defaultdict

# 生产侧格式串（`app/scheduling/freshness.py` 的 `_logger.info("A39 闸② channel=%s%s %s", ...)`）
# ——这两处一旦分叉，日志里就再没有一条能读的东西，所以守卫拿真格式串反解本脚本的正则。
LINE_RE = re.compile(r"A39 闸② channel=(?P<channel>\S+?)(?P<shadow>（影子）)? "
                     r"\[fresh=(?P<verdict>[a-z]+)\|(?P<reason>[^|\]]*)(?P<rest>\|[^\]]*)?\]")
DELAY_RE = re.compile(r"Delayed trigger state refreshed char=(?P<char>\d+) rule=(?P<rule>\S+)")
VERDICTS = ("cancel", "regenerate", "keep")
# 每通道样本数下限：低于这个数只报"没量到"，不许给出"该不该拨实闸"的方向。
MIN_SAMPLES_DEFAULT = 8


def parse_lines(lines) -> list[dict]:
    """纯函数：把日志行解析成读数行（非闸②的行返回里不会有）。"""
    out = []
    for line in lines:
        m = LINE_RE.search(line)
        if not m:
            continue
        stamp = line[:19]
        extras = {}
        if m.group("rest"):
            for kv in m.group("rest").strip("|").split("|"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    extras[k.strip()] = v.strip()
        out.append({"day": stamp[:10], "time": stamp, "channel": m.group("channel"),
                    "verdict": m.group("verdict"), "reason": (m.group("reason") or "").strip(),
                    "mode": "影子" if m.group("shadow") else "实拦", "extras": extras})
    return out


def summarize(rows: list[dict], days: int, min_samples: int) -> dict:
    """纯函数：分档汇总。**不读库、不读时钟**（守卫按这条钉），窗口由调用方算好传进来。"""
    by_channel = defaultdict(Counter)
    by_reason = defaultdict(Counter)
    by_mode = defaultdict(Counter)
    by_day = Counter()
    for r in rows:
        by_channel[r["channel"]][r["verdict"]] += 1
        by_reason[(r["channel"], r["verdict"])][r["reason"] or "(无原因)"] += 1
        by_mode[r["channel"]][r["mode"]] += 1
        by_day[r["day"]] += 1
    total = len(rows)
    channels = {}
    for ch, cnt in by_channel.items():
        n = sum(cnt.values())
        channels[ch] = {
            "n": n, "enough": n >= min_samples,
            "cancel": cnt.get("cancel", 0), "regenerate": cnt.get("regenerate", 0),
            "keep": cnt.get("keep", 0),
            "cancel_rate": (cnt.get("cancel", 0) / n) if n else 0.0,
            "reasons": dict(by_reason.get((ch, "cancel"), {})),
            "keep_reasons": dict(by_reason.get((ch, "keep"), {})),
            "shadow": by_mode[ch].get("影子", 0), "gate": by_mode[ch].get("实拦", 0),
        }
    return {"window_days": days, "total": total, "min_samples": min_samples,
            "by_day": dict(sorted(by_day.items())), "channels": channels,
            "unknown_verdicts": sorted({r["verdict"] for r in rows if r["verdict"] not in VERDICTS})}


def read_log_lines(log_dir: str, cutoff_day: str) -> list[str]:
    """IO 只在这一层：读本日与已轮转的日志，按日期前缀裁窗。"""
    lines: list[str] = []
    paths = [os.path.join(log_dir, "app.log")] + sorted(
        glob.glob(os.path.join(log_dir, "app.log.*")))
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            with io.open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line[:10] >= cutoff_day:
                        lines.append(line.rstrip("\r\n"))
        except OSError:
            continue
    return lines


def report(s: dict, delay_rows: list[dict]) -> str:
    out = [f"窗口＝近 {s['window_days']} 天；闸②读数 {s['total']} 条"
           f"（每通道判读下限 {s['min_samples']} 条）"]
    if not s["total"]:
        out.append("  **一条读数都没有**：这既不是「现状都没变」也不是「闸坏了」，"
                   "而是这些通道在窗口里根本没跑（或被更早的闸挡下）。别据此下结论。")
    for ch, d in sorted(s["channels"].items()):
        flag = "" if d["enough"] else "  ⚠ 样本不足，只报数不判方向"
        out.append(f"  {ch}：{d['n']} 条｜cancel {d['cancel']}／regenerate {d['regenerate']}／"
                   f"keep {d['keep']}（cancel 率 {d['cancel_rate']:.0%}）"
                   f"｜影子 {d['shadow']}・实拦 {d['gate']}{flag}")
        if d["enough"] and d["cancel"]:
            out.append(f"      cancel 原因分布＝{d['reasons']}")
            out.append(f"      ⇒ 实闸若开，这个通道大约会少发 {d['cancel']}/{d['n']}"
                       f"＝{d['cancel_rate']:.0%}（按读数条数近似，一条读数＝一次判定）")
    if delay_rows:
        by_rule = Counter(r["rule"] for r in delay_rows)
        out.append(f"  通道 3（延迟触发睡醒后现状变了）：{len(delay_rows)} 次，"
                   f"按规则＝{dict(by_rule.most_common(6))}")
    else:
        out.append("  通道 3（延迟触发）：0 次——注意它**不归影子闸管**（结构性修法），"
                   "所以 0 次只说明「延迟期间状态没变过或没触发过」，不说明闸的状态。")
    if s["unknown_verdicts"]:
        out.append(f"  ⚠ 出现表外档位 {s['unknown_verdicts']}＝格式或判据分叉了，先修尺子再读数。")
    return "\n".join(out)


def main() -> int:
    from datetime import datetime, timedelta

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--log-dir", default=os.path.join("backend", "data", "logs"))
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT)
    a = ap.parse_args()
    cutoff = (datetime.now() - timedelta(days=a.days - 1)).strftime("%Y-%m-%d")   # 时钟在这层取
    lines = read_log_lines(a.log_dir, cutoff)
    rows = parse_lines(lines)
    delay = [{"rule": m.group("rule"), "char": m.group("char")}
             for ln in lines if (m := DELAY_RE.search(ln))]
    print(report(summarize(rows, a.days, a.min_samples), delay))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
