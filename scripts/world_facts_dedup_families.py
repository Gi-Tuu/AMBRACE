# -*- coding: utf-8 -*-
"""world_facts 编纂层去重执行器（批 61 A6，2026-10-11）。三档判据，写入护栏同一套。

**为什么要有第三档（全部实测，见 output/b61_topic_measure／b61_novel_plan／b61_semantic 三份输出）**：
- 生产既有判据 `_same_curated_value`（C13/C13b，字面核心＋SequenceMatcher≥0.9）在这 72 条
  active curated 上归并出 **0 簇**；
- 字符二元组 Jaccard 两两**最高只有 0.56**；「除了对方没有新信息」的严格二值判据（≤0.20）
  只并得出 **1 条**；
- 而 #100/#109/#115/#128/#137/#142/#147/#148/#168/#190/#193/#264 明明是同一件事
  （用户与 sam 是伴侣），只是主语方向与措辞不同 ⇒ **任何字面判据都够不到**，
  这正是「重复内容越堆越多」长期清不干净的根因。

三档判据（默认第 1 档，逐档变激进）：
1) **核心短语**（默认）：正文归一化后第一个分隔符之前的核心相同 ＋ 同角色 ＋ 同 kind ⇒ 同族。
   零误伤，但只治「同一句话换了个后缀」。
2) **`--keep/--ids` 人工点名**：只并你逐条点名的行，不做任何自动匹配。
3) **`--semantic`**：本地 bge-m3 ONNX 向量余弦 ≥ **`SEMANTIC_THRESHOLD`**（默认 0.85）⇒ 同族。
   线是量出来的不是拍的：0.85 以上全部配对逐条人工看过——伴侣族 13 条（真重复）、
   胃 2 条、宠物 2 条；而「腰：亲密时控制力度」×「腰：避免剧烈活动」实测 0.820，
   **故意落在阈值之下**（不同约束不并）。跨 kind 一律不比。

写入语义（三档共用）：每族保留一条代表行（`--semantic` 取与族内其他行相似度和最大者＝最中心那条，
并列取正文最长、再并列取 id 新的；第 1 档取 id 最大的，与 C14 清账「簇代表＝最新那行」同口径），
其余成员**软停用**（`status='superseded'` ＋ `superseded_by=代表 id`，留痕不删、正文一字不改）。

护栏：默认只读报告；写库需 `--apply` 与 `--yes` 同时给；写前 sqlite3 在线备份（缺失/0 字节拒绝执行）；
逐行审计（可 `--audit-log` 落文件）；事务内全表指纹比对，变更集超出计划集就在 commit 前滚回；
复跑剩 0 族＝幂等。

用法：
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_dedup_families.py                     # 第 1 档只读
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_dedup_families.py --semantic          # 第 3 档只读
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_dedup_families.py --semantic --apply --yes
    backend\\.venv\\Scripts\\python.exe scripts/world_facts_dedup_families.py --keep 304 --ids 301,294 --apply --yes
"""
import argparse
import asyncio
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
DEFAULT_DB = str(_BACKEND / "data" / "sqlite" / "ai_companion.db")

CURATED_KINDS = ("fact", "constraint", "preference_profile", "relationship_baseline")
_MIN_CORE_LEN = 4
# 判据**不在此文件里抄第二份**：同义表/虚词表/覆盖判据的唯一真源是生产模块
# `app.events.facts`（抄一份迟早和写侧自相矛盾——这正是本项目反复登记过的口径病）。
# 取不到就当场报错，不许退回硬编码。
_SEM_TH = None
_covered = None


def _prod_judge():
    global _SEM_TH, _covered
    if _covered is None:
        if str(_BACKEND) not in sys.path:
            sys.path.insert(0, str(_BACKEND))
        from app.events.facts import CURATED_SEMANTIC_THRESHOLD, curated_is_covered
        _SEM_TH, _covered = CURATED_SEMANTIC_THRESHOLD, curated_is_covered
    return _SEM_TH, _covered


SEMANTIC_THRESHOLD = None   # 实际取值由 _prod_judge() 现取（默认＝生产同一个数）
_SPLIT = re.compile(r"[，、；;：:！？!?。.\s]+")
_STRIP = re.compile(r"[^\w]+")


def family_key(text: str) -> str:
    """核心短语：去标点空白 → 取第一个分隔符之前的片段 → 小写。太短返回空串（不成族）。"""
    head = _SPLIT.split((text or "").strip(), maxsplit=1)[0]
    key = _STRIP.sub("", head).lower()
    return key if len(key) >= _MIN_CORE_LEN else ""


def _rows(con) -> list:
    return con.execute(
        "SELECT id, character_id, kind, object_value FROM world_facts "
        "WHERE status='active' AND predicate='curated' AND kind IN ({}) "
        "ORDER BY id DESC".format(",".join("?" * len(CURATED_KINDS))),
        CURATED_KINDS).fetchall()


def absorb_by_coverage(members: list) -> list:
    """在**已经语义相近**的一簇里，只挑出「实义内容被别条完全覆盖」的行并掉。

    members＝[(id, text), ...]。返回 [(代表 id, [被并的 id...]), ...]；没有可并的就返回空。
    代表＝正文最长那条（信息量最大），并列取 id 新的；覆盖判据＝生产 `curated_is_covered`。
    宁可少并不误并：误并等于改写角色的行为规矩，少并只是页面上多留一条。
    """
    _th, covered_fn = _prod_judge()
    out, used = [], set()
    order = sorted(members, key=lambda m: (-len(m[1]), m[0]))
    for rid, val in order:
        if rid in used:
            continue
        absorbed = [oid for oid, oval in members
                    if oid != rid and oid not in used and covered_fn(oval, val)]
        if absorbed:
            out.append((rid, absorbed))
            used.update(absorbed)
            used.add(rid)
    return out


def collect_by_core(rows) -> list:
    """第 1 档：核心短语相同 ⇒ 同族。返回 [(代表 id, [成员 id...], 说明, char, 代表正文, [成员正文])]。"""
    groups = defaultdict(list)
    for rid, char, kind, val in rows:
        key = family_key(val)
        if key:
            groups[(char, kind, key)].append((rid, val))
    out = []
    for (char, kind, key), ms in groups.items():
        if len(ms) < 2:
            continue
        ms = sorted(ms, key=lambda m: m[0], reverse=True)
        out.append((ms[0][0], [m[0] for m in ms[1:]], f"核心「{key}」({kind})", char,
                    ms[0][1], [m[1] for m in ms[1:]]))
    return sorted(out, key=lambda g: (-len(g[1]), g[3]))


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math_sqrt(sum(x * x for x in a))
    nb = math_sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def math_sqrt(x: float) -> float:
    return x ** 0.5


async def _embed(texts: list) -> dict:
    """本地 bge-m3 向量（零计费）。**在函数内延迟 import**：模块加载期 import app.* 会拽起
    db engine/BM25 单例，与本脚本「自己指定库文件」的隔离口径冲突（10-10 批 57 实测踩过）。"""
    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))
    from app.memory.embedding import text_embedding
    out = {}
    for t in texts:
        out[t] = await text_embedding(t)
    return out


def collect_by_semantic(rows, vecs, th: float) -> list:
    """第 3 档：余弦 ≥ th 用并查集连族（只在同角色＋同 kind 内比）。"""
    parent = {r[0]: r[0] for r in rows}
    by_id = {r[0]: r for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sim = defaultdict(float)
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a[1] != b[1] or a[2] != b[2]:
                continue
            s = _cos(vecs[a[3]], vecs[b[3]])
            if s >= th:
                parent[find(b[0])] = find(a[0])
            sim[a[0]] += s
            sim[b[0]] += s
    clusters = defaultdict(list)
    for r in rows:
        clusters[find(r[0])].append(r)
    out = []
    for _root, members in clusters.items():
        if len(members) < 2:
            continue
        # 第二道守护：语义相近 ≠ 同一句话。**只有实义内容被代表行完全覆盖的行才并**，
        # 带独有信息的（「我们住在一起」「sam 是 AI」）留在原地。
        by_id = {m[0]: m for m in members}
        pairs = absorb_by_coverage([(m[0], m[3]) for m in members])
        for rep_id, absorbed in pairs:
            rep = by_id[rep_id]
            out.append((rep_id, absorbed,
                        f"语义≥{th:.2f}＋零信息丢弃（{rep[2]}）", rep[1], rep[3],
                        [by_id[i][3] for i in absorbed]))
    return sorted(out, key=lambda g: (-len(g[1]), g[3]))


def _manual_group(con, keep, ids_csv) -> list:
    """第 2 档：人工点名。护栏＝必须都存在、同角色、active、predicate='curated'。"""
    if keep is None or not ids_csv:
        raise SystemExit("[dedup] 点名档要同时给 --keep <保留的 id> 与 --ids <停用的 id,...>")
    ids = [int(x) for x in re.split(r"[,\s]+", ids_csv.strip()) if x]
    got = {}
    for rid in [keep] + ids:
        row = con.execute(
            "SELECT id, character_id, kind, object_value, status FROM world_facts WHERE id=?",
            (rid,)).fetchone()
        if row is None:
            raise SystemExit(f"[dedup] #{rid} 不存在，拒绝执行（别拿旧编号点名）")
        got[rid] = row
    rep = got[keep]
    if rep[4] != "active":
        raise SystemExit(f"[dedup] 保留行 #{keep} 不是 active（{rep[4]}），拒绝执行")
    members, mvals = [], []
    for rid in ids:
        r = got[rid]
        if r[1] != rep[1]:
            raise SystemExit(f"[dedup] #{rid} 与 #{keep} 不同角色，跨角色并档不允许")
        if r[4] != "active":
            print(f"[dedup] #{rid} 已不是 active（{r[4]}），跳过")
            continue
        members.append(rid)
        mvals.append(r[3])
    if not members:
        return []
    return [(keep, members, f"人工点名（保留 #{keep}）", rep[1], rep[3], mvals)]


def _fingerprint(con) -> dict:
    return {r[0]: r[1:] for r in con.execute(
        "SELECT id, status, superseded_by, CAST(superseded_at AS TEXT), CAST(updated_at AS TEXT), "
        "object_value, kind, character_id, predicate FROM world_facts ORDER BY id")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--audit-log", default=None)
    ap.add_argument("--semantic", action="store_true", help="用本地 bge-m3 向量判同族（第 3 档）")
    ap.add_argument("--threshold", type=float, default=None,
                    help="语义档余弦阈值；默认取生产常量 CURATED_SEMANTIC_THRESHOLD")
    ap.add_argument("--keep", type=int, default=None)
    ap.add_argument("--ids", default=None)
    a = ap.parse_args(argv)

    db = os.path.abspath(os.path.expanduser(a.db))
    if not os.path.exists(db):
        print(f"[dedup] 库文件不存在：{db}", file=sys.stderr)
        return 2
    con = sqlite3.connect(db)
    try:
        if a.keep is not None or a.ids:
            groups = _manual_group(con, a.keep, a.ids)
            if not groups:
                print("[dedup] 点名档：没有可停用的活跃成员行（可能已经并过）。")
                return 0
        else:
            rows = _rows(con)
            if a.semantic:
                th = a.threshold or _prod_judge()[0]
                print(f"[dedup] 语义档：本地 bge-m3 推理 {len(rows)} 条（零计费），阈值 {th}")
                vecs = asyncio.run(_embed(sorted({r[3] for r in rows})))
                groups = collect_by_semantic(rows, vecs, th)
            else:
                groups = collect_by_core(rows)
        total_members = sum(len(g[1]) for g in groups)
        print(f"[dedup] 模式={'写库' if a.apply else '只读报告'}  库={db}")
        print(f"[dedup] 族 {len(groups)} 个，拟软停用 {total_members} 条（每族保留 1 条）")
        for rep, members, desc, char, rep_val, member_vals in groups:
            print(f"\n  【char{char} {desc}】{len(members) + 1} 条")
            print(f"    保留 #{rep}：{rep_val[:60]}")
            for mid, mval in zip(members, member_vals):
                print(f"    停用 #{mid}：{mval[:60]}")
        if not a.apply:
            print("\n[dedup] 未加 --apply，不写库。")
            return 0
        if not a.yes:
            print("[dedup] --apply 需要同时给 --yes（这是写生产库的动作）。")
            return 2

        stamp = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(os.path.dirname(db), f"backup_world_facts_dedup_{stamp}.sqlite")
        dst = sqlite3.connect(backup)
        try:
            con.backup(dst)
        finally:
            dst.close()
        if not os.path.exists(backup) or os.path.getsize(backup) <= 0:
            raise RuntimeError(f"备份失败（缺失或 0 字节）：{backup}，拒绝写库")
        print(f"\n[dedup] 已备份：{backup}")

        before = _fingerprint(con)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        now_s = now.strftime("%Y-%m-%d %H:%M:%S.%f")
        audit = []
        con.execute("BEGIN")
        for rep, members, desc, char, _rv, _mvs in groups:
            for mid in members:
                cur = con.execute(
                    "UPDATE world_facts SET status='superseded', superseded_by=?, "
                    "superseded_at=?, updated_at=? "
                    "WHERE id=? AND status='active' AND character_id=? "
                    "AND predicate='curated' AND superseded_by IS NULL",
                    (rep, now_s, now_s, mid, char))
                if cur.rowcount != 1:
                    raise RuntimeError(f"#{mid} 守卫不匹配（rowcount={cur.rowcount}），整体回滚")
                audit.append(f"#{mid} active -> superseded (by #{rep}, {desc})")
        after = _fingerprint(con)          # 事务内：读得到自己的写
        changed = {i for i in after if after[i] != before.get(i)}
        allowed = {m for _r, ms, _d, _c, _rv, _mvs in groups for m in ms}
        if not changed <= allowed:
            con.rollback()
            raise RuntimeError(f"越界写入 {sorted(changed - allowed)}，已在 commit 前回滚")
        con.commit()
        for line in audit:
            print(f"    {line}")
        if a.audit_log:
            with open(a.audit_log, "a", encoding="utf-8") as f:
                f.write(f"\n== {now.isoformat()} apply backup={backup} 停用 {len(audit)} 条 ==\n")
                f.write("\n".join(audit) + "\n")
        left = collect_by_core(_rows(con))
        print(f"[dedup] 完成：变更 {len(changed)} 行；核心档复跑剩余族 {len(left)}")
        return 0
    except Exception as e:
        print(f"[dedup] 失败：{e}", file=sys.stderr)
        try:
            con.rollback()
        except Exception:
            pass
        return 1
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
