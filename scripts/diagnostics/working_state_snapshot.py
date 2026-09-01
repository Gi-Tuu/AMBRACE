# -*- coding: utf-8 -*-
"""工作记忆（M3-a）快照 / 回滚工具。

用法（backend/.venv/Scripts/python.exe，任意目录）：
    python scripts/diagnostics/working_state_snapshot.py                    # 快照（全部 working_state 行）
    python scripts/diagnostics/working_state_snapshot.py --character 13     # 指定角色
    python scripts/diagnostics/working_state_snapshot.py --restore <json>   # 回滚（整表工作记忆恢复到快照）

快照字段：working_state 行全量（id/user_id/character_id/content/status/superseded_by/
is_archived/created_at/updated_at）——flag `working_state_enabled` 灰度异常时一键回滚。
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
FIELDS = ["id", "user_id", "character_id", "content", "status", "superseded_by",
          "is_archived", "created_at", "updated_at"]


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--character", type=int, default=None, help="只快照指定角色")
    ap.add_argument("--restore", type=str, default=None, help="回滚到快照 JSON 文件")
    args = ap.parse_args()

    db = _resolve_db()
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row

    if args.restore:
        snap = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        rows = snap["rows"] if isinstance(snap, dict) else snap
        conn.execute("DELETE FROM memories WHERE memory_type='working_state'")
        for r in rows:
            conn.execute(
                "INSERT OR REPLACE INTO memories ({}) VALUES ({})".format(
                    ", ".join(FIELDS), ", ".join("?" for _ in FIELDS)),
                [r.get(f) for f in FIELDS],
            )
        conn.commit()
        print(f"[OK] 回滚完成：{len(rows)} 条 working_state 行已恢复（快照时刻 {snap.get('taken_at') if isinstance(snap, dict) else 'unknown'}）")
        return

    q = "SELECT {} FROM memories WHERE memory_type='working_state'".format(", ".join(FIELDS))
    params: list = []
    if args.character is not None:
        q += " AND character_id=?"
        params.append(args.character)
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    snap = {"taken_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "db": str(db), "rows": rows}
    out = Path(f"working_state_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] 快照 {len(rows)} 条 → {out}")
    conn.close()


if __name__ == "__main__":
    main()
