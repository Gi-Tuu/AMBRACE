# -*- coding: utf-8 -*-
"""批 0-2 · 感知分层只读盘点（**默认纯只读**，一个字节都不改）。

输出方案 §七 的指标 1 与指标 3（打标前的历史基线，供「隔离是否来得及」判读）::

    指标 1  画像层中感知来源占比    is_core=1 里 source='perception' 的条数与占比
    指标 3  洗数据上界              快照正文 vs 记忆正文：逐字复现命中数 + 长词重叠命中数

纪律：连接一律 ``file:...?mode=ro``（uri=True）+ ``PRAGMA query_only=ON`` 双保险；
本脚本**只执行 SELECT / PRAGMA**，不建表、不加列、不改任何保留策略。
库里没有任何命中时正常输出 0（不是报错）——指标 3 现在应为 0，>0 说明隔离晚了。

用法（一律用项目 venv 的 python）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\audit_perception_tier.py
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\audit_perception_tier.py --json
    #   换库：--app-db backend\\data\\sqlite\\ai_companion.db

退出码：0=跑通（命中与否都算跑通）；1=库文件不存在或关键表缺失（无从盘点）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_APP_DB = BACKEND_DIR / "data" / "sqlite" / "ai_companion.db"
PERCEPTION_SOURCE = "perception"
# 快照保留上限（每用户最近 20 条，唯一事实源 backend/app/api/phone.py:24；本批不改该策略）
SNAPSHOT_MAX_KEEP = 20
SAMPLE_LIMIT = 5      # 重叠命中最多打印几条预览（供人工判读，不改写历史）


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读打开 SQLite（mode=ro + PRAGMA query_only 双保险）。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


# ── 指标 1：画像层中感知来源占比 ──
def metric_core_share(conn: sqlite3.Connection) -> dict:
    """is_core=1 里 source='perception' 的条数与占比（无 perception 值时恒 0）。"""
    out = {"core_total": 0, "core_perception": 0, "core_perception_ratio": 0.0}
    cols = _columns(conn, "memories")
    if "is_core" not in cols or "source" not in cols:
        return out
    out["core_total"] = _count(conn, "SELECT COUNT(*) FROM memories WHERE is_core=1")
    out["core_perception"] = _count(
        conn, f"SELECT COUNT(*) FROM memories WHERE is_core=1 AND source='{PERCEPTION_SOURCE}'")
    if out["core_total"]:
        out["core_perception_ratio"] = round(out["core_perception"] / out["core_total"], 4)
    return out


# ── 指标 3：快照正文被记忆复现的上界 ──
def snapshot_bodies(conn: sqlite3.Connection) -> list[str]:
    """按现有保留上限取全部快照正文（每用户最近 SNAPSHOT_MAX_KEEP 条）。

    正文口径与注入侧一致：``content`` ＋（有值时）``image_desc``（见 section_phone.py:48-55）。
    """
    cols = _columns(conn, "phone_snapshots")
    if "content" not in cols:
        return []
    want = ["user_id", "content"] + [c for c in ("image_desc", "created_at", "id") if c in cols]
    order = "ORDER BY user_id ASC, " + ("created_at DESC, id DESC" if "created_at" in cols else "id DESC")
    rows = conn.execute(f'SELECT {",".join(want)} FROM phone_snapshots {order}').fetchall()
    key = {c: i for i, c in enumerate(want)}
    per_user: dict[int, int] = {}
    bodies: list[str] = []
    for row in rows:
        uid = int(row[key["user_id"]] or 0)
        if per_user.get(uid, 0) >= SNAPSHOT_MAX_KEEP:
            continue
        per_user[uid] = per_user.get(uid, 0) + 1
        parts = [str(row[key["content"]] or "").strip()]
        if "image_desc" in key:
            parts.append(str(row[key["image_desc"]] or "").strip())
        body = "；".join(p for p in parts if p)
        if body:
            bodies.append(body)
    return bodies


def metric_reproduction(conn: sqlite3.Connection, bodies: list[str]) -> dict:
    """快照正文 vs 记忆正文：逐字复现命中数 + 长词重叠命中数（判据复用 perception_tier）。

    命中数只给「上界」（词面撞车也算），因此同时给出最多 :data:`SAMPLE_LIMIT` 条命中记忆预览，
    供人工判读（设计 §四「批量纠」要求：只列出、不改写历史）。
    """
    from app.memory.perception_tier import snapshot_overlap

    out = {"snapshot_rows": len(bodies), "memory_rows": 0,
           "verbatim_hits": 0, "overlap_hits": 0, "overlap_samples": []}
    cols = _columns(conn, "memories")
    if "content" not in cols or not bodies:
        out["memory_rows"] = _count(conn, "SELECT COUNT(*) FROM memories") if "content" in cols else 0
        return out
    out["memory_rows"] = _count(conn, "SELECT COUNT(*) FROM memories")
    for (mtext,) in conn.execute("SELECT content FROM memories WHERE content IS NOT NULL"):
        text = str(mtext or "")
        if not text:
            continue
        if any(body in text for body in bodies):
            out["verbatim_hits"] += 1
        if snapshot_overlap(text, bodies):
            out["overlap_hits"] += 1
            if len(out["overlap_samples"]) < SAMPLE_LIMIT:
                out["overlap_samples"].append(" ".join(text.split())[:60])
    return out


def collect(db_path: Path) -> dict:
    """打开只读连接并跑完两项指标（表缺失时留 0，不抛）。"""
    result: dict = {"db": str(db_path), "ok": False, "note": ""}
    if not db_path.is_file():
        result["note"] = f"应用库不存在: {db_path}"
        return result
    try:
        conn = _connect_ro(db_path)
    except sqlite3.Error as e:
        result["note"] = f"只读打开失败: {e.__class__.__name__}: {e}"
        return result
    try:
        present = _tables(conn)
        missing = {"memories", "phone_snapshots"} - present
        if missing:
            result["note"] = f"缺表 {sorted(missing)}（按 0 输出）"
            result.update(metric_core_share(conn))
            result.update(metric_reproduction(conn, []))
            return result
        result.update(metric_core_share(conn))
        result.update(metric_reproduction(conn, snapshot_bodies(conn)))
        result["ok"] = not result["note"]
    except sqlite3.Error as e:
        result["note"] = f"盘点失败: {e.__class__.__name__}: {e}"
    finally:
        conn.close()
    return result


def print_report(data: dict) -> None:
    print("=== audit_perception_tier（默认只读：mode=ro + PRAGMA query_only，只 SELECT/PRAGMA）===")
    print(f"库: {data.get('db')}")
    print("\n[1] 画像层中感知来源占比（指标 1）")
    print(f"    is_core 总数={data.get('core_total')}  其中 source=perception={data.get('core_perception')}"
          f"  占比={data.get('core_perception_ratio')}")
    print("\n[3] 快照正文被记忆复现的上界（指标 3）")
    print(f"    快照正文={data.get('snapshot_rows')} 条  记忆行={data.get('memory_rows')} 条"
          f"  逐字复现命中={data.get('verbatim_hits')} 条  长词重叠命中={data.get('overlap_hits')} 条")
    for i, sample in enumerate(data.get("overlap_samples") or [], 1):
        print(f"      [{i}] {sample}")
    if data.get("note"):
        print(f"    备注: {data['note']}")
    hits = int(data.get("verbatim_hits") or 0)
    overlaps = int(data.get("overlap_hits") or 0)
    print("\n结论: " + ("跑通" if data.get("ok") else "未取到数据")
          + f"；指标 1 感知占比={data.get('core_perception_ratio')}"
          + f"；指标 3 逐字复现={hits} 长词重叠={overlaps}"
          + ("（重叠数是粗判上界，词面撞车也计入，>0 需按预览人工判读后再定性）" if overlaps or hits
             else "（无复现＝零洗数据成本）"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="感知分层只读盘点（指标 1 / 指标 3）")
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB,
                        help=f"应用 SQLite 库（默认 {DEFAULT_APP_DB}）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出同样的键")
    args = parser.parse_args(argv)
    data = collect(args.app_db)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        print_report(data)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
