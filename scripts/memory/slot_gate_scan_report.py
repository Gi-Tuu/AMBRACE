# -*- coding: utf-8 -*-
"""通用槽值闸 · user_facts 只读量化报告（记忆时态缺陷族·第三批任务4，2026-09-17）。

按 ``app.memory.slot_guard`` 的新闸口（通用判据 + location 专有闸叠加）扫描 ``user_facts``
**全部现有行**，输出「哪几行会被拒 / 哪几行通过」的裁决清单（含``previous_value`` 参考裁决）。

安全（交接硬性约束）：
- **只读**：``sqlite3 file:...?mode=ro``，全程只 SELECT，不写库、不改数据；
- 不开服务器、不做写操作类 git；不触碰 backend/data 以外的路径。
用法：
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\slot_gate_scan_report.py
  ... --db D:\\path\\to\\ai_companion.db
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from app.memory.slot_guard import (  # noqa: E402
    ANCHOR_MIN_LEN,
    COMMA_SEGMENT_MAX_LEN,
    GENERIC_VALUE_MAX_LEN,
    slot_value_reject_reason,
)

DEFAULT_DB = os.path.join(BACKEND_DIR, "data", "sqlite", "ai_companion.db")

# 生产实证四条整句（交接文件负面用例）+ 交接指定正面短语：与测试同源，供闸口自检对照
_SELF_CHECK_NEGATIVE = (
    ("health", "用户出门去提前占个好位置"),
    ("living", "用户买了校园网，覆盖全校教学区和宿舍"),
    ("goal_state", "用户参加的比赛快截止了，用户要赶出作品来"),
    ("job", "用户今晚有课，时间赶"),
)
_SELF_CHECK_POSITIVE = (
    ("location", "常驻湛江市"),
    ("job", "大二在读"),
    ("job", "有课，时间紧"),
)


def connect_ro(db_path: str) -> sqlite3.Connection:
    """只读连接（mode=ro）；库不存在直接 abort。"""
    if not os.path.exists(db_path):
        raise SystemExit(f"[abort] 数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def verdict(slot: str, value: str) -> str:
    reason = slot_value_reject_reason(slot, value)
    return "通过" if reason is None else f"拒写（{reason}）"


def scan(conn: sqlite3.Connection) -> tuple[list[str], dict]:
    """扫描 user_facts 全部行 → (报告行, 统计)。"""
    lines: list[str] = []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, user_id, slot, value, previous_value, source, valid_from, valid_to "
            "FROM user_facts ORDER BY user_id, slot, id"
        )]
    except Exception as e:  # noqa: BLE001 - 只读诊断
        return [f"（读取 user_facts 失败：{e}）"], {}
    if not rows:
        return ["  无 user_facts 行"], {"total": 0}

    stats = {"total": len(rows), "pass": 0, "reject": 0, "reasons": {}}
    lines.append(f"  共 {len(rows)} 行（只读裁决；previous_value 一并列出作参考）")
    for d in rows:
        slot = (d.get("slot") or "").strip()
        cur = (d.get("value") or "").strip()
        prev = (d.get("previous_value") or "").strip()
        v = verdict(slot, cur)
        rejected = v != "通过"
        if rejected:
            stats["reject"] += 1
            reason = v.split("（", 1)[1].rstrip("）")
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
        else:
            stats["pass"] += 1
        lines.append("")
        lines.append(f"  id={d.get('id')} user={d.get('user_id')} slot={slot} "
                     f"source={d.get('source')} → {v}")
        lines.append(f"    现值：{cur}")
        lines.append(f"    previous_value：{prev or '（空）'} → {verdict(slot, prev) if prev else '—'}")
    return lines, stats


def self_check() -> list[str]:
    """闸口自检（纯函数，不读库）：交接的四条负面必须全拒、三条正面必须全过。"""
    lines = ["", "② 闸口自检（纯函数，不读库）"]
    ok = True
    for slot, value in _SELF_CHECK_NEGATIVE:
        v = verdict(slot, value)
        ok = ok and v != "通过"
        lines.append(f"  [负面] {slot}: {value} → {v}")
    for slot, value in _SELF_CHECK_POSITIVE:
        v = verdict(slot, value)
        ok = ok and v == "通过"
        lines.append(f"  [正面] {slot}: {value} → {v}")
    lines.append(f"  自检结论：{'全部符合预期' if ok else '存在不符合预期的样本，请复核判据'}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="通用槽值闸只读扫描（不写库）")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite 库路径（只读 mode=ro 打开）")
    args = ap.parse_args()

    print("═" * 72)
    print("通用槽值闸 · user_facts 只读量化报告（记忆时态缺陷族·第三批任务4）")
    print("═" * 72)
    print(f"库：{args.db}（只读 mode=ro，不写库）")
    print(f"判据：通用长度上限 {GENERIC_VALUE_MAX_LEN} 字（location 沿用 location_guard 的 200）；"
          f"长值（>{ANCHOR_MIN_LEN} 字）须含该槽语义锚点；")
    print("      句末标点/换行、转述句（用户…/我…）、引号类对话残留、≥2 个逗号堆叠、"
          f"逗号分句 >{COMMA_SEGMENT_MAX_LEN} 字 → 一律拒写。")
    for line in self_check():
        print(line)
    print()
    print("③ user_facts 现有行裁决清单（不写库）")
    conn = connect_ro(args.db)
    try:
        lines, stats = scan(conn)
    finally:
        conn.close()
    for line in lines:
        print(line)
    if stats.get("total"):
        print()
        print(f"  统计：总 {stats['total']} 行 → 通过 {stats['pass']}，拒写 {stats['reject']}"
              f"（原因分布：{stats['reasons'] or '无'}）")
    print()
    print("（本次扫描全程只读；未执行任何写入、未重启服务器、未改版本号/pubspec。）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
