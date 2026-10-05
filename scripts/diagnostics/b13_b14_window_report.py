# -*- coding: utf-8 -*-
"""B13 / B14 观察窗口判效报告（只读，零生产改动；2026-10-05 预热）。

窗口到期（B13 约 10-07、B14 约 10-08）时**一条命令出数**，不用临时抓埋点口径：

    backend\\.venv\\Scripts\\python.exe scripts\\diagnostics\\b13_b14_window_report.py --days 7

- **B13**（批7 M1 `emotion_drive_modulation` shadow）：留痕在 `agent_task_logs`
  （trigger=memory_obs，route=emotion_drive_modulation），逐 drive 一条，payload＝
  `drive_key/multiplier/valence/arousal/bias/new_level/kind`。判据＝乘子是否贴
  `[0.80,1.25]` 两端、性格偏置（±0.10 上限）是否触顶。
- **B14**（A14 M0 多轮共识干跑，flag `ai_rating_vote3` ∧ 灰度角色 13）：同一张表
  route=ai_rating_char，干跑批带 `rounds`（每轮 {memory_id: star} 列表）＋ `consensus`。
  判据＝跨轮完全一致率、第 1 轮 vs 共识的分歧率，以及「若真按共识写回会改动几条星」。

约束与坑（读代码核过，别当 bug 清单）：
1. 全程 `mode=ro` 只读连接，绝不写库；日志清理/删除类操作不在本脚本职责内。
2. `shadow` 与 `on` 两条路径都写 `kind="shadow"`（`emotion_modulation.py` 里硬编码），
   所以留痕**分不清**「只是影子」还是「已经作用」——判 B13 只能按乘子本身判，别拿 kind 当开关证据。
3. `steps_json` 在 `ai_rating._trace_event` 里截到 1600 字符；10 候选 × 3 轮实测约 1.05K，
   余量约五成。候选数翻倍就可能截成坏 JSON，故本脚本对「解析失败」单独计数并显式报警，
   而不是静默丢样本（丢了会让分歧率看着更小）。
4. `created_at` 是 naive UTC（北京时间 = UTC+8），窗口按 UTC 算，输出时标出 UTC 边界。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]

DRIVE_ROUTE = "emotion_drive_modulation"
RATING_ROUTE = "ai_rating_char"
TRACE_STEPS_MAX = 1600          # 与 ai_rating.TRACE_STEPS_MAX 同口径（这里只用来判截断风险）
MULT_FLOOR, MULT_CEIL = 0.80, 1.25
BIAS_CAP = 0.10
EPS = 1e-9


def _resolve_db() -> Path:
    try:
        sys.path.insert(0, str(PROJECT / "backend"))
        from app.config import settings
        url = settings.database_url
        if url.startswith("sqlite"):
            p = url.split("///", 1)[-1].split("?", 1)[0]
            if p and p != ":memory:":
                return Path(p).resolve()
    except Exception:
        pass
    return (PROJECT / "backend" / "data" / "sqlite" / "ai_companion.db").resolve()


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "-"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> str:
    """比例的 95% 置信区间（Wilson）。干跑样本天生就小（每天最多 1 批 ×10 条），
    只报点估计会在判效时被误读成"稳"；区间比点数诚实。"""
    if not n:
        return "-"
    p = k / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return f"[{max(0.0, ctr - half) * 100:.1f}%, {min(1.0, ctr + half) * 100:.1f}%]"


def _bj(utc_naive: str) -> str:
    """naive UTC → 北京时间字符串（只为读表方便，判定不用它）。"""
    try:
        dt = datetime.strptime(utc_naive, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
    except Exception:
        return utc_naive


def collect_b13(conn: sqlite3.Connection, since: str) -> dict:
    """把窗口内的 shadow 样本聚成判定用的数字。**与打印分开**：判效的数要能被单测钉住。"""
    rows = conn.execute(
        "SELECT created_at, character_id, steps_json FROM agent_task_logs "
        "WHERE trigger='memory_obs' AND route=? AND created_at>=? ORDER BY created_at",
        (DRIVE_ROUTE, since),
    ).fetchall()
    s: dict = {"n": len(rows), "per_day": Counter(), "per_char": Counter(), "mult": Counter(),
               "bias": Counter(), "char_bias": {}, "bad_json": 0,
               "floor_hit": 0, "ceil_hit": 0, "identity": 0}
    for created_at, char_id, raw in rows:
        s["per_day"][created_at[:10]] += 1
        s["per_char"][char_id] += 1
        try:
            d = json.loads(raw)
        except Exception:
            s["bad_json"] += 1
            continue
        m = d.get("multiplier")
        b = d.get("bias")
        if isinstance(m, (int, float)):
            s["mult"][round(float(m), 4)] += 1
            if abs(float(m) - MULT_FLOOR) < 1e-4:
                s["floor_hit"] += 1
            elif abs(float(m) - MULT_CEIL) < 1e-4:
                s["ceil_hit"] += 1
            elif abs(float(m) - 1.0) < 1e-9:
                s["identity"] += 1
        if isinstance(b, (int, float)):
            # 偏置是角色固有属性（性格决定），按「样本条数」算比例＝同一个角色重复计几千次，
            # 所以另存一份**按角色**的构成；打印时用那份，不给百分比也不给置信区间。
            s["bias"][round(float(b), 4)] += 1
            s["char_bias"].setdefault(char_id, Counter())[round(float(b), 4)] += 1
    return s


def report_b13(conn: sqlite3.Connection, since: str) -> dict:
    s = collect_b13(conn, since)
    n = s["n"]
    print(f"\n=== B13 emotion_drive_modulation shadow（{since} UTC 起，样本 {n} 条）===")
    if not n:
        print("  无样本 ⇒ 判效不了：先确认 relational_drive_shadow / memory_trace_debug 是否开着")
        return s
    if s["bad_json"]:
        print(f"  ⚠ 坏 JSON {s['bad_json']} 条（被截断的可能就在这批里，别把缺失当「没有信号」）")
    print(f"  每日样本：{' ｜ '.join(f'{k}:{v}' for k, v in sorted(s['per_day'].items()))}")
    print(f"  覆盖角色 {len(s['per_char'])} 个：{' ｜ '.join(f'c{k}={v}' for k, v in sorted(s['per_char'].items()))}")
    print(f"  乘子取值（降序按次数）：{' ｜ '.join(f'{k}×{v}' for k, v in s['mult'].most_common(12))}")
    print(f"  贴总闸两端：下沿 {MULT_FLOOR} = {s['floor_hit']}（{_pct(s['floor_hit'], n)}）｜"
          f"上沿 {MULT_CEIL} = {s['ceil_hit']}（{_pct(s['ceil_hit'], n)}）｜恒等 1.0 = {s['identity']}（{_pct(s['identity'], n)}）")
    tot_bias = sum(s["bias"].values())
    cap_hi = s["bias"].get(BIAS_CAP, 0)
    cap_lo = s["bias"].get(-BIAS_CAP, 0)
    print(f"  性格偏置取值：{' ｜ '.join(f'{k}×{v}' for k, v in sorted(s['bias'].items()))}")
    print(f"  偏置触顶（±{BIAS_CAP}）：+{cap_hi}（{_pct(cap_hi, tot_bias)}）／-{cap_lo}（{_pct(cap_lo, tot_bias)}）")
    print("  按角色的偏置构成（单位＝角色，不是条数）："
          + " ｜ ".join(f"c{k}: " + ",".join(f"{v}×{cnt}" for v, cnt in sorted(c.items()))
                        for k, c in sorted(s["char_bias"].items())))
    print("  判读口径：两端计数≈0 ⇒ 「乘子贴边」不成立；触顶只发生在性格偏置上 ⇒"
          " M1 该判的是偏置幅度是否过冲，而不是总闸量程。")
    return s


def _parse_rounds(d: dict) -> list[dict[str, int]] | None:
    r = d.get("rounds")
    if not isinstance(r, list) or not r:
        return None
    out = []
    for rd in r:
        if not isinstance(rd, dict) or not rd:
            return None
        out.append({str(k): v for k, v in rd.items()})
    return out


def collect_b14(conn: sqlite3.Connection, since: str) -> dict:
    """把窗口内的干跑留痕聚成判定用的数字（与打印分开，理由同 `collect_b13`）。"""
    rows = conn.execute(
        "SELECT created_at, character_id, steps_json, length(steps_json) FROM agent_task_logs "
        "WHERE trigger='memory_obs' AND route=? AND created_at>=? ORDER BY created_at",
        (RATING_ROUTE, since),
    ).fetchall()
    near_cap = [created for created, _c, raw, ln in rows if ln and ln >= TRACE_STEPS_MAX - 5]
    batches, unparsable = 0, 0
    agree_all = mem_total = 0
    diverge = 0
    star_gap_sum = 0.0
    flip_batches = 0
    flip_items = 0
    rounds_hist: Counter = Counter()
    per_day: dict[str, int] = defaultdict(int)
    for created_at, char_id, raw, _ln in rows:
        try:
            d = json.loads(raw)
        except Exception:
            unparsable += 1
            continue
        rounds = _parse_rounds(d)
        if not rounds:
            continue
        batches += 1
        per_day[created_at[:10]] += 1
        rounds_hist[len(rounds)] += 1
        cons = d.get("consensus") or {}
        first = rounds[0]
        batch_flip = sum(1 for mid, s in first.items()
                         if str(mid) in cons and cons[str(mid)] != s)
        flip_items += batch_flip
        flip_batches += 1 if batch_flip else 0
        for mid in first:
            vals = [rd.get(mid) for rd in rounds if mid in rd]
            if len(vals) < 2:
                continue
            mem_total += 1
            if len(set(vals)) == 1:
                agree_all += 1
            c = cons.get(str(mid))
            if c is not None and first.get(mid) is not None and c != first[mid]:
                diverge += 1
                star_gap_sum += abs(float(c) - float(first[mid]))

    return {"n_rows": len(rows), "batches": batches, "unparsable": unparsable,
            "near_cap": len(near_cap), "near_cap_first": near_cap[0] if near_cap else "",
            "per_day": per_day, "rounds_hist": rounds_hist, "mem_total": mem_total,
            "agree_all": agree_all, "diverge": diverge, "star_gap_sum": star_gap_sum,
            "flip_batches": flip_batches, "flip_items": flip_items}


def report_b14(conn: sqlite3.Connection, since: str) -> dict:
    print(f"\n=== B14 A14 多轮共识干跑（{since} UTC 起，留痕在窗口内）===")
    s = collect_b14(conn, since)
    if not s["n_rows"]:
        print("  无留痕 ⇒ 评星这一拍没跑，或 flag/白名单没命中")
        return s
    n_mem, n_b = s["mem_total"], s["batches"]
    print(f"  干跑批次 {n_b} 个 ｜ 每日：{' ｜ '.join(f'{k}:{v}' for k, v in sorted(s['per_day'].items()))}")
    print(f"  轮数分布：{' ｜ '.join(f'{k}轮×{v}' for k, v in sorted(s['rounds_hist'].items()))}")
    print(f"  坏 JSON {s['unparsable']} 条 ｜ 留痕长度贴 1600 上限的批 {s['near_cap']} 个"
          + (f"（{_bj(s['near_cap_first'])} 起）⇒ 候选数再涨就要截断，判效前先确认没丢样本"
             if s["near_cap"] else ""))
    gap = f"{(s['star_gap_sum'] / s['diverge']):.2f}" if s["diverge"] else "-"
    print(f"  跨轮完全一致（同一条记忆各轮同星）：{s['agree_all']}/{n_mem} = {_pct(s['agree_all'], n_mem)}"
          f" {_wilson_ci(s['agree_all'], n_mem)}")
    print(f"  第 1 轮 vs 共识 分歧：{s['diverge']}/{n_mem} = {_pct(s['diverge'], n_mem)}"
          f" {_wilson_ci(s['diverge'], n_mem)}，分歧时平均星差 {gap}")
    print(f"  按共识写回的影响：{s['flip_batches']}/{n_b} 批会被改动，共 {s['flip_items']} 条星"
          "（这才是「共识值不值 ×3 成本」的判断量）")
    print(f"  ⚠ 单位与上限：条目级 {n_mem} 条来自 {n_b} 批、且只有灰度角色 13 ⇒"
          " 置信区间只反映条目波动，批间/角色间差异没被抽样，别当生产稳态外推")
    print("  判读口径：一致率高＋改动条数少 ⇒ 单轮已够稳，M2 两档释放不必为共识让路；"
          "改动条数接近分歧条数 ⇒ 共识确有实效，值得立项。")
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="统计窗口（天，按 trace created_at，naive UTC）")
    ap.add_argument("--only", choices=("b13", "b14"), help="只跑其中一项")
    args = ap.parse_args()

    db = _resolve_db()
    if not db.exists():
        print(f"[ERR] 数据库不存在: {db}")
        return
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = (datetime.utcnow() - timedelta(days=args.days)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"DB（只读）: {db}")
    print(f"窗口: 近 {args.days} 天（UTC 起 {since} ＝北京 {_bj(since)}）")
    if args.only in (None, "b13"):
        report_b13(conn, since)
    if args.only in (None, "b14"):
        report_b14(conn, since)
    conn.close()


if __name__ == "__main__":
    main()
