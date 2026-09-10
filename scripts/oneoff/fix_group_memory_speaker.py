"""一次性数据修复（2026-08-18）：群聊合并记忆拆分重建。

背景：_save_group_memory 旧实现把整轮群聊（用户+多角色回应）合并成一条记忆，
speaker 只标第一个回应角色（无回应时把用户发言标成角色）→ 群聊事件归属错乱。
本脚本把存量合并记忆按发言者拆成多条（speaker 正确），删除原合并记忆。
用法：python scripts/fix_group_memory_speaker.py   （幂等，可重复执行）
"""
import sys
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "backend" / "data" / "sqlite" / "ai_companion.db"


def _name_to_id(db, user_id: int, name: str, char_ids: list[int]) -> int | None:
    """按名字查角色 id（优先限定在该记忆涉及的群成员范围内）"""
    if not name or name == "角色":
        return None
    rows = db.execute(
        "SELECT id, name FROM ai_characters WHERE name=? AND user_id=?",
        (name, user_id),
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if char_ids:
        rows2 = db.execute(
            "SELECT id FROM ai_characters WHERE name=? AND id IN (%s)" % ",".join("?" * len(char_ids)),
            [name] + char_ids,
        ).fetchall()
        if len(rows2) == 1:
            return rows2[0][0]
    return None


def main() -> None:
    if not DB.is_file():
        print(f"数据库不存在: {DB}")
        sys.exit(1)
    db = sqlite3.connect(str(DB))
    db.execute("PRAGMA foreign_keys = ON")
    mems = db.execute(
        "SELECT id, user_id, character_id, speaker_type, speaker_id, content FROM memories WHERE source='group' ORDER BY id"
    ).fetchall()
    fixed = 0
    deleted = 0
    for mid, uid, cid, sp_type, sp_id, content in mems:
        if not content:
            continue
        parts = [p.strip() for p in content.split("；") if p.strip()]
        # 判断是否为旧合并格式（含"回应：" 或 "用户在群里说："）
        has_user = any(p.startswith("用户在群里说：") for p in parts)
        has_reply = any("回应：" in p for p in parts)
        if not (has_user or has_reply):
            continue
        # 已正确拆分的新格式（"X在群里说："）不受影响
        if not has_reply and all(not p.startswith("用户在群里说：") for p in parts):
            continue
        # 群成员候选（用于名字→id）
        char_ids = [r[0] for r in db.execute(
            "SELECT character_id FROM chat_group_members WHERE group_id IN "
            "(SELECT id FROM chat_groups WHERE user_id=?)", (uid,)
        ).fetchall()]
        # 拆分条目
        entries = []
        ok = True
        for p in parts:
            if p.startswith("用户在群里说："):
                text = p[len("用户在群里说："):].strip()
                if text:
                    entries.append(("user", uid, f"用户在群里说：{text[:100]}"))
            elif "回应：" in p:
                name, _, text = p.partition("回应：")
                name = name.strip()
                text = text.strip()
                rid = _name_to_id(db, uid, name, char_ids)
                if rid is None:
                    ok = False
                    break
                entries.append(("character", rid, f"{name}在群里说：{text[:80]}"))
            else:
                # 无法识别的片段：保守跳过该条（不删原记忆）
                ok = False
                break
        if not ok or not entries:
            print(f"SKIP mem={mid} (无法解析): {content[:60]!r}")
            continue
        # 保留原记忆 id（其他表外键引用安全）：原行更新为第一条拆分（speaker 修正），其余插入新行
        first = entries[0]
        db.execute(
            "UPDATE memories SET content=?, speaker_type=?, speaker_id=?, epistemic_status='FACT', is_archived=0, updated_at=datetime('now') WHERE id=?",
            (first[2][:300], first[0], first[1], mid),
        )
        for sp_t, sp_i, text in entries[1:]:
            db.execute(
                "INSERT INTO memories (user_id, character_id, memory_type, content, title, importance, is_archived, sub_type, source, speaker_type, speaker_id, epistemic_status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,0,?,?,?,?,?, datetime('now'), datetime('now'))",
                (uid, cid, "event", text[:300], "家庭群聊", 40.0, "group", "group", sp_t, sp_i, "FACT"),
            )
        fixed += 1
        deleted += len(entries) - 1
        print(f"FIXED mem={mid}: 原行更新为第 1 条 + 新增 {len(entries) - 1} 条")
    db.commit()
    print(f"完成：修复 {fixed} 条，删除 {deleted} 条合并记忆")


if __name__ == "__main__":
    main()
