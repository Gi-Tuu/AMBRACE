# -*- coding: utf-8 -*-
"""world_facts 存量收敛执行器（批 61 A7，2026-10-11）。

治的是**已经写进库的**两格错位（写入侧 P0 已挡住新行，存量不会自己变好）：

- P1 权威回滚：`author != 'user'` 却 `is_authoritative=1` 的活跃行 → 0。
  「不可动摇的权威设定」只属于用户亲手写的那几条；AI 推断拿到权威位会让它排在列表最前、
  并且让后来的证据很难纠正。
- P2 高风险域降级：curated 四类里 `verify_state='machine-confirmed'` 且命中高风险域词表
  （健康/医疗/用药/财务/法律/安全）的行 → `unverified` + `UNVERIFIED` + 7 天短复核窗。
  降级＝退出每轮确定性注入，**不删正文、不改 kind、不物理删行**；用户在「世界设定」里
  改一下保存即恢复（`update_world_fact` 会把该行置 human-reviewed）。

**默认只读报告**（dry-run）。写库必须 `--apply` 与 `--yes` 同时给；写前用 sqlite3 在线备份
API 整份复制库文件，备份不存在/大小为 0 直接拒绝执行（fail-closed）。

不做的事（有意为之，别再加回来）：
- 不按关键字 supersede/删除任何条目。事故里的「腰」既可能是比喻演绎也可能是真实情况，
  按词删会误删真实事实；注入侧 A4 已经让这 28 条不再当每轮前提，剩下的取舍归用户在页面上做。
- 不动 `author='user'` 的行；不动 `status != 'active'` 的行；不动瞬时层 `kind='status'`。

用法：
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_authority_rollback.py            # 只读报告
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_authority_rollback.py --apply --yes
可选 `--db <path>`（默认生产库）、`--audit-log <path>`（逐行审计落文件）。
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
DEFAULT_DB = str(_BACKEND / "data" / "sqlite" / "ai_companion.db")

CURATED_KINDS = ("fact", "constraint", "preference_profile", "relationship_baseline")
VERIFY_MACHINE = "machine-confirmed"
VERIFY_UNVERIFIED = "unverified"
_UNSET = object()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sensitive_keywords() -> tuple:
    """词表唯一真源＝生产模块 `app.events.facts._SENSITIVE_DOMAIN_KEYWORDS`。

    **在函数内延迟 import**：模块加载期 import app.* 会拽起 db engine/BM25 单例，
    与本脚本「自己指定库文件」的隔离口径冲突（10-10 批 57 实测踩到）。
    拿不到就直接报错，不许退回硬编码——硬编码＝假同源。
    """
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    from app.events.facts import _SENSITIVE_DOMAIN_KEYWORDS
    return tuple(_SENSITIVE_DOMAIN_KEYWORDS)


def _is_sensitive(text: str, kws: tuple) -> bool:
    t = (text or "").lower()
    return any(k in t for k in kws)


def _fingerprint(con) -> dict:
    rows = con.execute(
        "SELECT id, status, is_authoritative, verify_state, epistemic_status, "
        "       CAST(stale_after AS TEXT), object_value, author, kind "
        "FROM world_facts ORDER BY id").fetchall()
    return {r[0]: r[1:] for r in rows}


def _backup(db_path: str) -> str:
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        f"backup_world_facts_rollback_{stamp}.sqlite")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise RuntimeError(f"备份失败（文件缺失或为 0 字节）：{dest}，拒绝写库")
    return dest


def plan(con, kws: tuple) -> tuple:
    """返回 (p1_ids, p2_rows)；p2_rows 带正文（审计要能看懂改了什么）。"""
    p1 = [r[0] for r in con.execute(
        "SELECT id FROM world_facts "
        "WHERE status='active' AND COALESCE(author,'system') <> 'user' AND is_authoritative=1 "
        "ORDER BY id")]
    p2 = []
    q = ("SELECT id, kind, object_value FROM world_facts "
         "WHERE status='active' AND kind IN ({}) AND verify_state=? "
         "AND COALESCE(author,'system') <> 'user' ORDER BY id").format(
            ",".join("?" * len(CURATED_KINDS)))
    for rid, kind, val in con.execute(q, (*CURATED_KINDS, VERIFY_MACHINE)):
        if _is_sensitive(val, kws):
            p2.append((rid, kind, val))
    return p1, p2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--audit-log", default=None)
    a = ap.parse_args(argv)

    db = os.path.abspath(os.path.expanduser(a.db))
    if not os.path.exists(db):
        print(f"[rollback] 库文件不存在：{db}", file=sys.stderr)
        return 2
    kws = _sensitive_keywords()

    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=OFF")  # 与生产 sqlite 默认一致（写 world_facts 不级联）
        p1, p2 = plan(con, kws)
        before = _fingerprint(con)
        print(f"[rollback] 模式={'写库' if a.apply else '只读报告'}  库={db}")
        print(f"[rollback] P1 权威回滚（AI 行却带不可动摇标记）：{len(p1)} 条")
        print(f"[rollback] P2 高风险域降级为待确认（不删不改文）：{len(p2)} 条")
        for rid, kind, val in p2:
            print(f"    P2 #{rid} {kind}: {val[:60]}")
        if not a.apply:
            print("[rollback] 未加 --apply，不写库。")
            return 0
        if not a.yes:
            print("[rollback] --apply 需要同时给 --yes（这是写生产库的动作）。")
            return 2

        backup = _backup(db)
        print(f"[rollback] 已备份：{backup}")

        now = _now()
        stale = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S.%f")
        audit = []
        con.execute("BEGIN")
        for rid in p1:
            cur = con.execute(
                "UPDATE world_facts SET is_authoritative=0, updated_at=? "
                "WHERE id=? AND status='active' AND COALESCE(author,'system') <> 'user' "
                "AND is_authoritative=1",
                (now.strftime("%Y-%m-%d %H:%M:%S.%f"), rid))
            if cur.rowcount != 1:
                raise RuntimeError(f"P1 #{rid} 守卫不匹配（rowcount={cur.rowcount}），整体回滚")
            audit.append(f"P1 #{rid} is_authoritative 1 -> 0")
        for rid, kind, _val in p2:
            cur = con.execute(
                "UPDATE world_facts SET verify_state=?, epistemic_status='UNVERIFIED', "
                "stale_after=?, updated_at=? "
                "WHERE id=? AND status='active' AND kind=? AND verify_state=? "
                "AND COALESCE(author,'system') <> 'user'",
                (VERIFY_UNVERIFIED, stale, now.strftime("%Y-%m-%d %H:%M:%S.%f"),
                 rid, kind, VERIFY_MACHINE))
            if cur.rowcount != 1:
                raise RuntimeError(f"P2 #{rid} 守卫不匹配（rowcount={cur.rowcount}），整体回滚")
            audit.append(f"P2 #{rid} {kind} machine-confirmed -> unverified（stale_after=+7d）")
        after = _fingerprint(con)          # 仍在事务内：读得到自己的写
        changed = {i for i in after if after[i] != before.get(i)}
        allowed = set(p1) | {r[0] for r in p2}
        if not changed <= allowed:
            con.rollback()
            raise RuntimeError(f"越界写入 {sorted(changed - allowed)}，已在 commit 前回滚")
        con.commit()
        for line in audit:
            print(f"    {line}")
        if a.audit_log:
            with open(a.audit_log, "a", encoding="utf-8") as f:
                f.write(f"\n== {now.isoformat()} apply backup={backup} "
                        f"P1={len(p1)} P2={len(p2)} ==\n")
                f.write("\n".join(audit) + "\n")
        p1_left, p2_left = plan(con, kws)
        print(f"[rollback] 完成。残留 P1={len(p1_left)} P2={len(p2_left)}（应为 0，幂等复跑同一结果）")
        print(f"[rollback] 变更行数={len(changed)}，范围核对通过（未越界）")
        return 0
    except Exception as e:
        print(f"[rollback] 失败：{e}", file=sys.stderr)
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
