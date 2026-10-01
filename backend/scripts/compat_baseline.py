# -*- coding: utf-8 -*-
"""A4 批 8 块 A M0 —— 兼容端点**只读**盘点脚本（§7 M0「A」行的 4 组基线数字）。

干什么
------
给 §7「429/403 触发率」「第三方流量成本归属」「角色调用集中度」等指标**建第一份基线**，
回答「今天到底怎么样」。四组数字：

① **按角色调用集中度**：从日志里 ``char=<id>`` 计数 ⇒ 对数 / 各自的占比（判断流量是否
   集中在少数角色上，决定兼容端点要不要做角色级配额）；
② **429 / 403 计数**：限流与被拒的次数（§7：403 非 0 且集中在少数 user_id ⇒ 归属口径需拍板）；
③ **``/api/v1/ai/*`` 请求量**：既有人类入口的请求规模（兼容端点的对照分母）；
④ **渠道词表命中**：``utils/llm_channel.py`` 现有词表值 + 待新增的 ``openai_compat``
   在日志里的命中次数（④用于确认「渠道还没立起来」这件事本身）。

只读纪律（写死，无开关）
------------------------
- **默认不连库**（只扫日志）。显式给 ``--app-db`` 才开库，且一律
  ``sqlite3.connect("file:...?mode=ro", uri=True)`` + ``PRAGMA query_only=ON``，
  全脚本对库**只发 SELECT**；没有 INSERT/UPDATE/DELETE/DDL 任何一处。
- 唯一落盘产物＝本脚本不落任何文件（只打印到 stdout，``--json`` 也是打印）。

怎么用
------
    backend\\.venv\\Scripts\\python.exe backend/scripts/compat_baseline.py
    backend\\.venv\\Scripts\\python.exe backend/scripts/compat_baseline.py --json
    backend\\.venv\\Scripts\\python.exe backend/scripts/compat_baseline.py --app-db <库路径>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

# 日志里 AI 角色调用的行形如 "... char=<id> ..."（character_chat_api 的日志口径）
_RE_CHAR = re.compile(r"char=(\d+)")
# HTTP 状态码：uvicorn / 访问日志形态里出现的 " 429 " " 403 "，或日志文本里的显式标注
_RE_STATUS = re.compile(r"\b(429|403)\b")
_RE_AI_PATH = re.compile(r"/api/v1/ai/")

# 渠道词表（对齐 app/utils/llm_channel.py:23-30；openai_compat 是 §2.1(1) 待新增的 M1 值）
CHANNEL_WORDS: tuple[str, ...] = (
    "app", "wechat_ilink", "server", "(unknown)", "openai_compat",
)

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_LOG = os.path.join(_BACKEND_ROOT, "data", "logs", "app.log")
DEFAULT_DB = os.path.join(_BACKEND_ROOT, "data", "sqlite", "ai_companion.db")


def _read_lines(path: str, tail_days: int) -> list[str]:
    """只读打开日志（含轮转文件），坏字节用 replace 兜底，绝不因编码抛错。"""
    paths = [path]
    if tail_days > 0:
        base = os.path.basename(path)
        for i in range(1, tail_days + 1):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            rotated = os.path.join(os.path.dirname(path), f"{base}.{day}")
            if os.path.isfile(rotated):
                paths.append(rotated)
    lines: list[str] = []
    for p in paths:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                lines.extend(fh.readlines())
        except OSError as exc:  # 只读盘点不该因一个文件不可读就失败
            print(f"[warn] 日志不可读，已跳过：{p}（{exc}）", file=sys.stderr)
    return lines


def _share(part: int, total: int) -> float:
    return round(part * 100.0 / total, 2) if total else 0.0


def scan_char_concentration(lines: list[str], top: int) -> dict:
    """① 按角色调用集中度（对数 / 占比）。"""
    counter: Counter[str] = Counter()
    for line in lines:
        for cid in _RE_CHAR.findall(line):
            counter[cid] += 1
    total = sum(counter.values())
    ranked = counter.most_common(top)
    return {
        "pairs": len(counter),
        "total_hits": total,
        "top": [{"char_id": cid, "hits": n, "share_pct": _share(n, total)}
                for cid, n in ranked],
    }


def scan_status_counts(lines: list[str]) -> dict:
    """② 429 / 403 计数。"""
    counts: Counter[str] = Counter()
    for line in lines:
        for code in _RE_STATUS.findall(line):
            counts[code] += 1
    return {
        "429": counts.get("429", 0),
        "403": counts.get("403", 0),
        "note": ("口径＝日志文本里出现的状态码字符串；若无访问日志落盘则为 0，"
                 "需在 M1 给兼容端点补结构化埋点才能出真实计数"),
    }


def scan_ai_path(lines: list[str]) -> dict:
    """③ /api/v1/ai/* 请求量。"""
    hits = sum(1 for line in lines if _RE_AI_PATH.search(line))
    return {
        "hits": hits,
        "note": ("口径＝日志里出现该路径的行数；app.log 默认不记 uvicorn 访问日志 ⇒ "
                 "为 0 属预期，接访问日志或加埋点后才有数"),
    }


def scan_channels(lines: list[str]) -> dict:
    """④ 渠道词表命中（确认 openai_compat 尚未立起来这件事本身）。"""
    blob = "\n".join(lines)
    hits = {word: blob.count(word) for word in CHANNEL_WORDS}
    return {"vocab": hits, "openai_compat_expected_zero": hits.get("openai_compat", 0) == 0}


def probe_db(path: str) -> dict:
    """可选：以 mode=ro 只读打开业务库，取 llm_usage 的任务分布（只 SELECT）。"""
    out: dict = {"enabled": True, "path": path, "tables": {}, "note": ""}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except Exception as exc:
        out["note"] = f"无法只读打开库：{exc}"
        return out
    try:
        con.execute("PRAGMA query_only=ON")
        rows = con.execute(
            "SELECT task, COUNT(*) FROM llm_usage GROUP BY task ORDER BY 2 DESC LIMIT 20"
        ).fetchall()
        out["tables"]["llm_usage_by_task"] = [{"task": t, "calls": c} for t, c in rows]
    except sqlite3.Error as exc:
        out["note"] = f"llm_usage 只读查询失败（表可能不存在，属预期）：{exc}"
    finally:
        con.close()
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="批 8 块 A M0 · 兼容端点只读盘点（零行为）")
    p.add_argument("--log", default=DEFAULT_LOG, help="主日志路径（默认 backend/data/logs/app.log）")
    p.add_argument("--tail-days", type=int, default=0,
                   help="额外并入最近 N 天的轮转日志（app.log.<YYYY-MM-DD>），默认 0＝只看主日志")
    p.add_argument("--top", type=int, default=10, help="集中度排行取前 N，默认 10")
    p.add_argument("--app-db", default=None,
                   help="可选：业务库路径（**只读** mode=ro 打开，只 SELECT）。不给则不连库")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（便于 CI/面板取数）")
    args = p.parse_args(argv)

    lines = _read_lines(args.log, max(0, args.tail_days))
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "log_lines": len(lines),
        "char_concentration": scan_char_concentration(lines, max(1, args.top)),
        "status_counts": scan_status_counts(lines),
        "ai_path_hits": scan_ai_path(lines),
        "channel_vocab": scan_channels(lines),
        "db_probe": ({"enabled": False, "note": "未给 --app-db，默认不连库"}
                     if not args.app_db else probe_db(args.app_db)),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"[块A·只读盘点] 生成 {report['generated_at']}｜日志行数 {report['log_lines']}")
    cc = report["char_concentration"]
    print(f"① 角色调用集中度：对数={cc['pairs']}  总命中={cc['total_hits']}")
    for row in cc["top"]:
        print(f"     char={row['char_id']:<6} 命中={row['hits']:<6} 占比={row['share_pct']}%")
    sc = report["status_counts"]
    print(f"② 429/403：429={sc['429']}  403={sc['403']}  （{sc['note']}）")
    ap = report["ai_path_hits"]
    print(f"③ /api/v1/ai/* 请求量：{ap['hits']}  （{ap['note']}）")
    cv = report["channel_vocab"]
    print(f"④ 渠道词表命中：{cv['vocab']}")
    print(f"   openai_compat 仍为 0（M1 才新增该渠道值）：{cv['openai_compat_expected_zero']}")
    db = report["db_probe"]
    print(f"⑤ 库探针：{'未启用（默认不连库）' if not db.get('enabled') else db.get('note') or db.get('tables')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
