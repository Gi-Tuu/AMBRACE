# -*- coding: utf-8 -*-
"""RC1 落库级判据：回复说「记下了」之后，是否真的落了库。

背景：`action_first_audit` 的 missing 只按词表判「说记却没带标记」，两档（开/关提示）
无显著差异（42.1% vs 40.0%）——因为标记/措辞会移动分母，且动作标记 ≠ 落库。
本脚本换硬判据：

对每条 AI 回复：
  1) 含「记下/记上/记牢/记住了/记好了」措辞 ⇒ 计入分母；
  2) 回复本身带 MEMO/CAL_NOTE/【记忆：】标记 ⇒ 计入「标记落库」；
  3) 否则在窗口内（默认 120s）查同角色/同用户的 memories、calendar_notes、memo_notes 新增
     ⇒ 有则「通道落库」，无则 **missing**（说记了但无任何落库证据）。

只读、只输出计数与 id/时间（不打印正文），适合在真实库/沙箱上复用。

用法：
  python scripts/diagnostics/memory_action_landing_audit.py --days 7
  python scripts/diagnostics/memory_action_landing_audit.py --db <path> --days 30 --window 180
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAID_WORDS = ("记下", "记上", "记牢", "记住了", "记好了")
MARKER_RE = re.compile(r"[\[【](?:MEMO|CAL_NOTE)[^\]】]*[\]】]|[\[【]\s*记忆\s*[：:]")

DEFAULT_DB = Path(os.environ.get("AMBRACE_DB", "backend/data/sqlite/ai_companion.db"))  # 相对仓库根，勿写本机绝对路径


def _utc(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def _has_said(text: str) -> bool:
    return any(w in (text or "") for w in SAID_WORDS)


def _has_marker(text: str) -> bool:
    return bool(MARKER_RE.search(text or ""))


def _landed_near(con, *, character_id, user_id, t0: datetime, window_s: int) -> bool:
    t1 = t0 + timedelta(seconds=window_s)
    lo, hi = t0.strftime("%Y-%m-%d %H:%M:%S"), t1.strftime("%Y-%m-%d %H:%M:%S")
    q1 = ("SELECT 1 FROM memories WHERE character_id=? AND user_id=? "
          "AND created_at >= ? AND created_at <= ? LIMIT 1")
    q2 = ("SELECT 1 FROM calendar_notes WHERE character_id=? AND created_at >= ? "
          "AND created_at <= ? LIMIT 1")
    q3 = ("SELECT 1 FROM memo_notes WHERE character_id=? AND created_at >= ? "
          "AND created_at <= ? LIMIT 1")
    if con.execute(q1, (character_id, user_id, lo, hi)).fetchone():
        return True
    if con.execute(q2, (character_id, lo, hi)).fetchone():
        return True
    if con.execute(q3, (character_id, lo, hi)).fetchone():
        return True
    return False


def audit(db: Path, *, days: float = 7, window_s: int = 120, character_id: int | None = None,
          user_id: int | None = None, since: str | None = None) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if since is None:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        conds = ["m.sender_type='ai'", "m.created_at >= ?"]
        params: list = [since]
        if character_id is not None:
            conds.append("s.character_id = ?"); params.append(character_id)
        if user_id is not None:
            conds.append("s.user_id = ?"); params.append(user_id)
        sql = ("SELECT m.id, m.content, m.created_at, s.character_id, s.user_id "
               "FROM chat_messages m JOIN chat_sessions s ON s.id = m.session_id "
               "WHERE " + " AND ".join(conds) + " ORDER BY m.id")
        rows = con.execute(sql, params).fetchall()
        total = marker = channel = missing = 0
        missing_ids: list = []
        for mid, content, created, cid, uid in rows:
            if not _has_said(content):
                continue
            total += 1
            if _has_marker(content):
                marker += 1
                continue
            if _landed_near(con, character_id=cid, user_id=uid, t0=_utc(created),
                            window_s=window_s):
                channel += 1
            else:
                missing += 1
                if len(missing_ids) < 20:
                    missing_ids.append({"message_id": mid, "created_at": created[:19],
                                        "character_id": cid})
        swept = total - marker - channel - missing
        return {
            "db": str(db), "since": since, "window_s": window_s,
            "total_said": total, "marker_landed": marker, "channel_landed": channel,
            "missing": missing, "unclassified": swept,
            "missing_ratio_pct": round(missing / total * 100, 1) if total else 0.0,
            "landed_ratio_pct": round((marker + channel) / total * 100, 1) if total else 0.0,
            "missing_samples": missing_ids,
        }
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RC1 落库级判据（只读）")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--window", type=int, default=120)
    ap.add_argument("--character-id", type=int, default=None)
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = audit(Path(a.db), days=a.days, window_s=a.window, character_id=a.character_id,
                user_id=a.user_id, since=a.since)
    if a.json:
        import json
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"[落库级判据] {out['since']} 起 / 窗口 {out['window_s']}s")
        print(f"  「说了记」回复: {out['total_said']}")
        print(f"  标记落库: {out['marker_landed']} ｜ 通道落库: {out['channel_landed']} ｜ "
              f"missing: {out['missing']}")
        print(f"  missing 比例: {out['missing_ratio_pct']}% ｜ 落库比例: {out['landed_ratio_pct']}%")
        for s in out["missing_samples"]:
            print("  ·", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
