# -*- coding: utf-8 -*-
"""A33/B.3.2 一次性回填：给存量承诺（prospective_intents）补 `trigger` 事件/时钟标签。

背景（2026-10-07 派单，批 2）：A33 ⑥ 让 promise 在**落库当时**就把「在等什么」写进
cue_terms_json 的 `trigger`（arrival|medication|clock），读侧优先用已存标签、缺失才按正文现算。
升级前写下的 pending/matched 承诺没有这个标签，每轮判定都要重新推断；本脚本按内容关键词一次性补上。

**只加元数据**：terms / confidence / side 逐字保留（旧 list 容器升级为 dict 时原 list 原样放进
`terms` 键，`_loads_cue_terms` 两种格式都能读，匹配语义不变）。判定口径直接 import 生产函数
``app.scheduling.prospective_intent.classify_intent_trigger``，脚本里**不另写一套正则**（口径唯一）。

用法（默认只读；写库前自动整库备份）：
    backend\\.venv\\Scripts\\python.exe backend/scripts/backfill_intent_triggers.py           # dry-run
    backend\\.venv\\Scripts\\python.exe backend/scripts/backfill_intent_triggers.py --apply    # 真写（先备份）
可选：`--statuses=pending,matched`（默认值，只补还活着的承诺）。无法解析的 cue_terms_json
一律跳过并计数（不拿「修复脏值」当理由改数据）。

路径口径：优先环境变量 AMBRACE_DB，缺省用「仓库根/backend/data/sqlite/ai_companion.db」，不写死作者机器路径。
生产库是否执行由用户/Codex 决定；建议运行期间停止服务器，避免并发写。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = Path(os.environ.get("AMBRACE_DB", str(ROOT / "backend" / "data" / "sqlite" / "ai_companion.db")))
VALID = ("arrival", "medication", "clock")

sys.path.insert(0, str(ROOT / "backend"))
try:
    from app.scheduling.prospective_intent import classify_intent_trigger
except Exception as e:  # 用系统 python 跑的常见后果：给出可执行的纠正指引，而不是半截改库
    print(f"[ERROR] 无法导入 app.scheduling.prospective_intent（判定口径唯一来源）: {e}\n"
          f"        请用 venv：backend\\.venv\\Scripts\\python.exe {sys.argv[0]}")
    raise SystemExit(2)


def _parsable(raw: str) -> bool:
    try:
        json.loads(raw)
        return True
    except Exception:
        return False


def new_payload(raw: str | None, trigger: str, force: bool = False) -> str | None:
    """原 cue_terms_json → 补 trigger 后的新串；返回 None ＝ 这条不动（已带合法标签，幂等）。"""
    text = (raw or "").strip()
    if not text:
        return json.dumps({"terms": [], "trigger": trigger}, ensure_ascii=False)
    v = json.loads(text)                             # 调用方已确保可解析
    if isinstance(v, dict):
        if v.get("trigger") in VALID:
            if not force or v.get("trigger") == trigger:
                return None                          # 已带标签（--retag 下算出来相同也算）：幂等跳过
            return json.dumps({**v, "trigger": trigger}, ensure_ascii=False)   # --retag：按新口径覆盖旧标签
        return json.dumps({**v, "trigger": trigger}, ensure_ascii=False)
    if isinstance(v, list):                          # 旧 list 容器：原样搬进 terms 键，只加标签
        return json.dumps({"terms": v, "trigger": trigger}, ensure_ascii=False)
    return None                                      # 标量等意外形态不碰


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    retag = "--retag" in argv   # A38b（2026-10-07）：按新口径覆盖已有 trigger（默认只补缺、不覆盖）
    statuses = ("pending", "matched")
    for a in argv:
        if a.startswith("--statuses"):
            arg = a.split("=", 1)[1] if "=" in a else ""
            statuses = tuple(x.strip() for x in arg.split(",") if x.strip()) or statuses
    if not DB.exists():
        print(f"[ERROR] 库不存在: {DB}")
        return 2

    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT id, character_id, content, cue_terms_json FROM prospective_intents "
            f"WHERE kind = 'promise' AND status IN ({','.join('?' * len(statuses))}) "
            "ORDER BY character_id, id",
            statuses,
        ).fetchall()
    except sqlite3.OperationalError as e:            # 插件裸 schema 库无此表（同迁移守卫先例）
        print(f"[SKIP] 库中无 prospective_intents 表，无需回填：{e}")
        return 0
    finally:
        con.close()

    plan: list[tuple[int, str, str]] = []            # (id, trigger, 新串)
    tagged = broken = 0
    for rid, char_id, content, raw in rows:
        if (raw or "").strip() and not _parsable(raw):
            broken += 1
            print(f"[跳过] id={rid} char={char_id} cue_terms_json 无法解析，不改")
            continue
        trigger = classify_intent_trigger(content or "")
        payload = new_payload(raw, trigger, force=retag)
        if payload is None:
            tagged += 1
            continue
        shape = "旧 list 格式" if (raw or "").lstrip().startswith("[") \
            else ("无 cue_terms_json" if not (raw or "").strip() else "缺 trigger")
        plan.append((rid, trigger, payload))
        print(f"[将改] id={rid} char={char_id} trigger={trigger}（{shape}）正文={str(content)[:40]}")

    ids = [p[0] for p in plan]
    dist = {t: sum(1 for _, x, _ in plan if x == t) for t in VALID}
    print(f"[{'APPLY' if apply else 'DRY-RUN'}] 存量承诺 {len(rows)} 条 / 已带标签 {tagged} 条 / "
          f"坏 JSON 跳过 {broken} 条 / 将改 {len(plan)} 条: {ids}（分布 {dist}）")
    if not plan:
        return 0
    if not apply:
        print("（只读演练；确认无误后加 --apply 执行，会先整库备份）")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = DB.with_name(DB.name + f".bak-triggers-{stamp}")
    # 用 sqlite3 在线 backup API，而不是文件拷贝：库处于 WAL 活跃状态时，
    # 直接拷 .db 可能漏掉还在 -wal 里的最新事务；backup API 取一致性快照。
    _src = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    _dst = sqlite3.connect(str(bak))
    _src.backup(_dst)
    _dst.close()
    _src.close()

    con = sqlite3.connect(str(DB))
    con.execute("PRAGMA busy_timeout=30000")
    with con:
        con.executemany("UPDATE prospective_intents SET cue_terms_json = ? WHERE id = ?",
                        [(payload, rid) for rid, _, payload in plan])
    con.close()
    print(f"[OK] 已补 trigger {len(plan)} 条；备份: {bak}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
