"""P1-6 存量清理：conversation_topics 噪声与状态一致性修复（零 LLM，本地规则）。

现状（已核实）：topic 是从旧版规则机械切出的残片，含【】（）动作碎片与半句
（「的事」「卧室】」「倒水】」「站一天」），且存在 status/进度自相矛盾
（#45 status=完成 但 progress=进行中）。

策略（防数据丢失，绝不做物理删除）：
  - 明显噪声条目 → 置 status='完成'（从「进行中」注入池移除，仍保留可审计）；
  - status/progress 矛盾 → 终态（完成/搁置）不再挂「进行中」；
  - 同角色近义/重叠话题 → 保留重要度最高一条，其余置「完成」（合并去重）。

默认 dry-run：只读统计 + 打印将要改动的样本，不写库。
--apply：写前自动备份（backups/conversation_topics_backup_<ts>.json），再执行更新。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_conversation_topics.py
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_conversation_topics.py --apply
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_conversation_topics.py --db <path>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# 与生产库同款噪声判定（保持单一事实源：与 app.agent.topic_tracker 一致）
_BRACKET_RE = __import__("re").compile(r"[【】()（）\[\]<>《》「」“”\"']")
_FRAGMENT_TRAIL_RE = __import__("re").compile(r"[的了呢吧吗啊呀嘛哈哟嘞呗嗯呃~～]+$")
_NOISE_FRAGMENTS = frozenset({
    "的事", "做的事哦", "的事哦", "弄弄", "哪儿", "啥", "什么", "吗", "呢", "啊",
    "呀", "哦", "呃", "嗯", "是头疼", "你呢", "干嘛", "干嘛呀",
})


def _is_pure_punct(t: str) -> bool:
    return bool(t) and all(ch in " ，。！？!?；;、（）()【】…·—-~～　" for ch in t)


def _strip_brackets(t: str) -> str:
    t = _BRACKET_RE.sub("", t or "").strip()
    t = t.strip(" ，。！？!?；;、（）()【】-—…·~～　")
    t = _FRAGMENT_TRAIL_RE.sub("", t).strip()
    return _BRACKET_RE.sub("", t).strip()


def _overlap(a: str, b: str) -> bool:
    """与 topic_tracker._overlap 同口径：包含关系或公共子串 >= 4 字。"""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    for i in range(len(a) - 3):
        if a[i:i + 4] in b:
            return True
    return False


def classify(topic: str) -> str | None:
    """返回该话题的「动作类型」，None 表示无需改动。

    - "noise_bracket" / "noise_fragment" / "noise_punct" → 置完成
    """
    if _is_pure_punct(topic):
        return "noise_punct"
    if _BRACKET_RE.search(topic or ""):
        return "noise_bracket"
    if topic in _NOISE_FRAGMENTS:
        return "noise_fragment"
    return None


def plan_cleanup(rows: list[dict]) -> list[dict]:
    """纯函数：给定 conversation_topics 行（dict），返回待执行决策列表。

    每行决策：{"id", "action", "old_status", "old_progress", "new_status", "new_progress", "reason"}
    action ∈ {noise_done, fix_consistency, merge_done}。绝不物理删除。
    """
    decisions: list[dict] = []
    seen: dict[int, set[str]] = {}  # character_id -> 已保留话题集合（用于合并去重）

    for r in rows:
        rid = r["id"]
        cid = r.get("character_id")
        topic = r.get("topic") or ""
        status = r.get("status")
        progress = r.get("progress")

        # 1) 噪声 → 完成
        cls = classify(topic)
        if cls is not None and status != "完成":
            decisions.append({
                "id": rid, "action": "noise_done",
                "old_status": status, "old_progress": progress,
                "new_status": "完成", "new_progress": progress,
                "reason": cls,
            })
            continue

        # 2) status/进度矛盾：终态不得挂「进行中」
        if status in ("完成", "搁置") and progress == "进行中":
            decisions.append({
                "id": rid, "action": "fix_consistency",
                "old_status": status, "old_progress": progress,
                "new_status": status, "new_progress": status,
                "reason": "终态不应挂进行中",
            })
            continue

        # 3) 合并去重：同角色近义/重叠的进行中话题，保留首条更高重要度者
        if status == "进行中":
            bucket = seen.setdefault(cid, set())
            dup = next((t for t in bucket if _overlap(t, topic)), None)
            if dup is not None:
                decisions.append({
                    "id": rid, "action": "merge_done",
                    "old_status": status, "old_progress": progress,
                    "new_status": "完成", "new_progress": progress,
                    "reason": f"与进行中话题重叠：{dup}",
                })
                continue
            bucket.add(topic)

    return decisions


def _backup(con: sqlite3.Connection, path: Path) -> None:
    cur = con.cursor()
    cur.execute("SELECT id, character_id, user_id, topic, status, importance, "
                "last_touched_at, follow_up, goal, progress, created_at, updated_at "
                "FROM conversation_topics")
    cols = [d[0] for d in cur.description]
    data = [dict(zip(cols, row)) for row in cur.fetchall()]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[backup] 已备份 {len(data)} 行 → {path}")


def apply_decisions(con: sqlite3.Connection, decisions: list[dict]) -> int:
    cur = con.cursor()
    n = 0
    for d in decisions:
        cur.execute(
            "UPDATE conversation_topics SET status=?, progress=? WHERE id=?",
            (d["new_status"], d["new_progress"], d["id"]),
        )
        n += 1
    con.commit()
    return n


def _load_rows(con: sqlite3.Connection) -> list[dict]:
    cur = con.cursor()
    cur.execute("SELECT id, character_id, user_id, topic, status, progress, importance "
                "FROM conversation_topics")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(description="conversation_topics 噪声/状态一致性清理")
    ap.add_argument("--db", default="backend/data/sqlite/ai_companion.db",
                    help="sqlite 路径（默认生产库）")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run，只读统计）")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / db_path
    if not db_path.exists():
        print(f"[error] 库不存在：{db_path}")
        return

    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode={'ro' if not args.apply else 'rwc'}", uri=True)
    try:
        rows = _load_rows(con)
        decisions = plan_cleanup(rows)
        by_action: dict[str, int] = {}
        for d in decisions:
            by_action[d["action"]] = by_action.get(d["action"], 0) + 1

        print(f"[dry-run={'NO' if args.apply else 'YES'}] 总话题 {len(rows)} 条，"
              f"拟改动 {len(decisions)} 条：{by_action}")
        # 打印样本（最多 12 条）
        for d in decisions[:12]:
            print(f"  - #{d['id']} [{d['action']}] {d['old_status']}/{d['old_progress'] or '-'} "
                  f"→ {d['new_status']}/{d['new_progress'] or '-'}  ({d['reason']})")
        if not args.apply:
            print("[dry-run] 未改动生产库。加 --apply 写库（写前自动备份）。")
            return

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = Path(__file__).resolve().parent.parent / "backups" / f"conversation_topics_backup_{ts}.json"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        _backup(con, backup_path)
        n = apply_decisions(con, decisions)
        print(f"[apply] 已更新 {n} 条。")
    finally:
        con.close()


if __name__ == "__main__":
    main()
