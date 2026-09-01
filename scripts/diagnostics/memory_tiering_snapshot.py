# -*- coding: utf-8 -*-
"""记忆分层衰减（M2-S2）快照 / 回滚工具。

用途：开启 flag `memory_tiered_decay`（分层衰减）前，对 memories 表的衰减相关字段做快照；
若灰度期发现衰减行为异常，可用快照一键回滚这些字段。

用法（backend/.venv/Scripts/python.exe，任意目录）：
    python scripts/diagnostics/memory_tiering_snapshot.py                    # 快照
    python scripts/diagnostics/memory_tiering_snapshot.py --restore <json>   # 回滚

快照字段：id / strength_days / importance / delete_at / last_reinforce_at / next_review_at / is_archived
（冷归档只改 is_archived；结算只改 importance/last_reinforce_at/delete_at——以上字段足以恢复灰度前状态）
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
FIELDS = ["id", "strength_days", "importance", "delete_at", "last_reinforce_at", "next_review_at", "is_archived"]


def _resolve_db() -> Path:
    """从 settings 解析真实库路径（兼容环境变量覆盖；失败回退默认位置）"""
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
    return (PROJECT / "backend" / "data" / "sqlite" / "app.db").resolve()


def snapshot() -> Path:
    db = _resolve_db()
    if not db.exists():
        raise SystemExit(f"数据库不存在: {db}")
    out_dir = PROJECT / "backend" / "data" / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"memory_tiering_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            f"SELECT {', '.join(FIELDS)} FROM memories WHERE is_archived = 0"
        ).fetchall()
    finally:
        conn.close()
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "db": str(db),
        "fields": FIELDS,
        "rows": [dict(zip(FIELDS, r)) for r in rows],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"快照完成：{out}（{len(rows)} 条未归档记忆）")
    return out


def restore(snapshot_file: Path) -> None:
    payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    rows = payload["rows"]
    fields = payload["fields"]
    db = Path(payload.get("db") or _resolve_db())
    if not db.exists():
        raise SystemExit(f"快照指向的数据库不存在: {db}")
    conn = sqlite3.connect(str(db))
    try:
        sets = ", ".join(f"{f} = ?" for f in fields if f != "id")
        vals = [tuple(r[f] for f in fields if f != "id") + (r["id"],) for r in rows]
        conn.executemany(f"UPDATE memories SET {sets} WHERE id = ?", vals)
        conn.commit()
    finally:
        conn.close()
    print(f"回滚完成：{len(rows)} 条记忆的 {', '.join(f for f in fields if f != 'id')} 已恢复")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="记忆分层衰减快照/回滚")
    ap.add_argument("--restore", metavar="JSON", help="从快照文件回滚")
    args = ap.parse_args()
    if args.restore:
        restore(Path(args.restore))
    else:
        snapshot()
