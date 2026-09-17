# -*- coding: utf-8 -*-
"""AMBRACE 记忆时态缺陷族·第二批任务1 · 存量污染候选清理脚本（**默认 dry-run，只读**）。

扫描 ``memories`` 里被提成 ``user_info`` / ``preference`` → 兜底 ``enduring`` 的存量污染候选：

- **元对话 / 情绪宣泄无锚点**：命中 ``meta_guard._META_ABOUT_AI`` 且无真实用户事实锚点
  （人名/职业/位置/稳定偏好），生产实证 id=9814（title「用户的名字」+ 记忆回退气话）；
- **title 无证据**：title 标「用户的名字」但捕获 token 不像人名、标「用户的职业」但无职业语义词
  （生产实证 id=6708 买酱油对话被标「用户的职业」）；
- **一次性已发生琐事**：``user_info/extracted`` 且含「买了/去了/吃了…」类过去动作
  （生产实证 id=5860/5856「买了三盒披萨回家」）。

输出：候选清单（id/角色/类型/title/内容摘要/判定理由）+ 统计 + 样例，并给出
「改造前(HEAD tense.py) → 改造后」的 classify_tense 归类对比（只读 `git show`）。

安全：
- **默认 dry-run**：全程 ``sqlite3 file:...?mode=ro`` 只 SELECT，不写库、不改数据、不删记忆；
- 只有显式 ``--apply`` 才写库（本批交接**要求不执行 --apply**：候选与样例报出来由用户拍板）；
  ``--apply`` 的动作是把候选行 ``status`` 置 ``stale``（复用 #70 语义：可追溯、不物理删），
  这是写操作，务必先复核 dry-run 清单。
用法：
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\meta_dialogue_cleanup_report.py
  ... --db D:\\...\\x.db --limit 30
  ... --apply            # ⚠ 本批不要执行
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from types import SimpleNamespace

SERVER_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND_DIR = os.path.join(SERVER_DIR, "backend")
sys.path.insert(0, BACKEND_DIR)

from app.memory.meta_guard import (  # noqa: E402
    is_meta_without_anchor,
    title_evidence_ok,
)
from app.memory.tense import classify_tense  # noqa: E402

DEFAULT_DB = os.path.join(BACKEND_DIR, "data", "sqlite", "ai_companion.db")
DEFAULT_LIMIT = 30
# 与 tense._ONE_OFF_ACTIONS 同源（此处独立一份，避免依赖私有常量）
_ONE_OFF_ACTIONS = (
    "买了", "去了", "吃了", "喝了", "看了", "逛了", "做了", "拿了", "点了",
    "订了", "收到了", "遇到了", "见到了", "玩完",
)
_SCAN_TYPES = ("user_info", "preference")
# 与 tense._ONE_OFF_ACTIONS / _ONE_OFF_QUANT_RE 同源（含数量词形态，如「买三盒披萨」）
_ONE_OFF_QUANT_RE = r"(?:买|吃|喝|拿|点|订)[\u4e00-\u9fa5]{0,2}(?:盒|杯|碗|份|斤|瓶|袋|个|块)"
_ONE_OFF_RE = re.compile("|".join(_ONE_OFF_ACTIONS) + "|" + _ONE_OFF_QUANT_RE)


def connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise SystemExit(f"[abort] 数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_legacy_classify():
    """改造前口径：只读 `git show HEAD:backend/app/memory/tense.py` 并 exec 出 classify_tense。

    只读 git 是本批允许的操作；失败则返回 (None, 原因)，脚本仍输出改造后结果。
    """
    try:
        out = subprocess.run(
            ["git", "-C", SERVER_DIR, "show", "HEAD:backend/app/memory/tense.py"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        ns: dict = {"__name__": "ambrace_tense_legacy"}
        exec(compile(out.stdout, "tense.py@HEAD", "exec"), ns)
        return ns["classify_tense"], "git HEAD:backend/app/memory/tense.py"
    except Exception as e:  # noqa: BLE001 - 只读诊断，取不到就退化
        return None, f"（改造前口径不可得：{e}）"


def _row_obj(r: dict) -> SimpleNamespace:
    return SimpleNamespace(
        memory_type=r.get("memory_type"), sub_type=r.get("sub_type"),
        is_core=False, core_category=None,
        title=r.get("title") or "", content=r.get("content") or "",
        why_it_matters="", created_at=None, valid_to=None,
    )


def judge(r: dict) -> list[str]:
    """单行污染判定理由（空列表 = 不是候选）。"""
    title = (r.get("title") or "").strip()
    content = (r.get("content") or "").strip()
    text = f"{title} {content}".strip()
    reasons: list[str] = []
    if is_meta_without_anchor(text):
        reasons.append("元对话/情绪宣泄且无事实锚点")
    if not title_evidence_ok(title, content):
        reasons.append(f"title 无证据锚点（{title}）")
    if r.get("memory_type") == "user_info" and (r.get("sub_type") or "") == "extracted" \
            and _ONE_OFF_RE.search(text):
        reasons.append("一次性已发生琐事（user_info/extracted）")
    return reasons


def scan(conn: sqlite3.Connection, limit: int):
    q = (
        "SELECT id, character_id, memory_type, sub_type, title, content, status, importance, created_at "
        "FROM memories WHERE is_archived=0 AND status='active' "
        f"AND memory_type IN ({','.join('?' * len(_SCAN_TYPES))}) ORDER BY id DESC"
    )
    rows = [dict(r) for r in conn.execute(q, _SCAN_TYPES)]
    legacy, legacy_src = load_legacy_classify()
    candidates = []
    enduring_flip = 0
    for d in rows:
        reasons = judge(d)
        obj = _row_obj(d)
        after = classify_tense(obj)
        before = legacy(obj) if legacy else None
        if before == "enduring" and after != "enduring":
            enduring_flip += 1
        if reasons:
            candidates.append((d, reasons, before, after))
    return rows, candidates, enduring_flip, legacy_src


def report_user_facts(conn: sqlite3.Connection) -> list[str]:
    """只读自检 user_facts 位置槽的证据锚点与 previous_value 回退（不写库）。"""
    from app.memory.location_guard import looks_like_location_value, strong_location_value
    from app.memory.user_facts import resolve_location_value

    lines = ["", "③ user_facts 位置槽证据锚点自检（只读，不写库）"]
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT user_id, slot, value, previous_value, source, valid_from FROM user_facts ORDER BY user_id, slot"
        )]
    except Exception as e:  # noqa: BLE001
        return lines + [f"  （读取失败：{e}）"]
    if not rows:
        return lines + ["  无 user_facts 行"]
    for d in rows:
        if d["slot"] != "location":
            continue
        cur = (d.get("value") or "").strip()
        prev = (d.get("previous_value") or "").strip()
        resolved = resolve_location_value(d)
        lines.append(
            f"  user={d['user_id']} slot=location source={d.get('source')} valid_from={str(d.get('valid_from'))[:19]}\n"
            f"      现行值 {'通过' if looks_like_location_value(cur) else '未通过'}锚点：{cur[:60]!r}\n"
            f"      previous_value 强锚点={strong_location_value(prev)}：{prev[:60]!r}\n"
            f"      共享读路径实际取值：{resolved!r}"
        )
    return lines


def report_apply(conn_ro: sqlite3.Connection, db_path: str, candidates, yes_apply: bool) -> list[str]:
    """--apply 写库分支（本批不执行）：候选行 status → stale（#70 语义，不物理删）。"""
    lines = ["", "④ --apply 写库分支"]
    if not yes_apply:
        lines.append("  未指定 --apply → **dry-run**：以上候选仅列出，未写库（本批交接要求不执行 --apply）。")
        return lines
    ids = [d["id"] for d, _reasons, _b, _a in candidates]
    lines.append(f"  ⚠ --apply 已指定：将把 {len(ids)} 条候选 status 置为 'stale'（可追溯、不物理删）。")
    if not ids:
        lines.append("  无候选，无写入。")
        return lines
    conn_ro.close()
    w = sqlite3.connect(db_path)
    try:
        w.executemany("UPDATE memories SET status='stale' WHERE id=?", [(i,) for i in ids])
        w.commit()
        lines.append(f"  已写入 {len(ids)} 行 status=stale。")
    finally:
        w.close()
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆时态缺陷族第二批任务1 · 存量污染候选（默认 dry-run 只读）")
    ap.add_argument("--db", default=DEFAULT_DB, help="SQLite 库路径（默认 backend/data/sqlite/ai_companion.db）")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="样例输出条数上限")
    ap.add_argument("--apply", action="store_true",
                    help="⚠ 真正写库（把候选 status 置 stale）；默认不开，本批交接要求不要执行")
    args = ap.parse_args()

    conn = connect_ro(args.db)
    rows, candidates, enduring_flip, legacy_src = scan(conn, args.limit)

    out = [
        "AMBRACE 记忆时态缺陷族·第二批任务1 · 存量污染候选报告",
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"库：{args.db}（mode=ro 只读）",
        f"改造前口径：{legacy_src}",
        "改造后口径：工作区 backend/app/memory/tense.py + meta_guard.py",
        "",
        "① 扫描范围与结果",
        f"  扫描 memories（is_archived=0 AND status='active' AND memory_type IN {_SCAN_TYPES}）：{len(rows)} 行",
        f"  污染候选：{len(candidates)} 行（其中改造前判 enduring、改造后已非 enduring 的：{enduring_flip} 行）",
    ]
    by_reason: dict[str, int] = {}
    for _d, reasons, _b, _a in candidates:
        for rsn in reasons:
            by_reason[rsn.split("（")[0]] = by_reason.get(rsn.split("（")[0], 0) + 1
    if by_reason:
        out.append("  理由分布：" + "；".join(f"{k}={v}" for k, v in sorted(by_reason.items(), key=lambda x: -x[1])))

    out += ["", f"② 候选样例（最多 {args.limit} 条：id/角色/类型/title/内容摘要/理由/改造前后）"]
    for d, reasons, before, after in candidates[: args.limit]:
        content = (d.get("content") or "").replace("\n", " ")[:50]
        out.append(
            f"  id={d['id']} char={d['character_id']} {d['memory_type']}/{d.get('sub_type')} "
            f"title={d.get('title')!r}\n"
            f"      内容={content!r}\n"
            f"      理由={'；'.join(reasons)}  改造前={before} 改造后={after}"
        )
    if not candidates:
        out.append("  （无候选）")

    out += report_user_facts(conn)
    out += report_apply(conn, args.db, candidates, args.apply)
    out += ["", "说明：默认 dry-run，全程只 SELECT（sqlite mode=ro）+ 只读 git show，不写库、不改数据、不删记忆。"]
    print("\n".join(out))
    try:
        conn.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
