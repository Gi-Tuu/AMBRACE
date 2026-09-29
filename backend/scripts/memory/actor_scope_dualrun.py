# -*- coding: utf-8 -*-
"""P0 第 4 步 · 感知条归属**双跑校验**（旧判定 vs 新判定逐条对比，脚本自己判 PASS/FAIL）。

判据（派单 §要求 4，一字不改地落成可执行断言）
────────────────────────────────────────────────────────
① **非感知条逐字节一致**：``source`` 不是 ``perception`` 的用例，旧/新判定的
   ``(speaker_type, speaker_id, actor)`` 三元组必须完全相等；出现任何差异 ⇒ FAIL。
② **感知条差异全部可解释为 ``NULL→perception``**：归属列由空补成 ``perception``、
   说话人 id 与准入归属都不动 ⇒ 预期差异（计入 ``null_to_perception``）；
   另允许一档 ``user_fallback_removed``＝**关闸**样本上「默认 user 兜底」被常开判定去掉
   （要求 2 的本体：``user``/``user_id`` → ``perception``/``None``）；闸开时不该出现这一档。
   落不进这两档的感知条差异 ⇒ ``unexplained`` ⇒ FAIL。

两侧判定怎么来
────────────────────────────────────────────────────────
- :func:`old_branch` ＝ 改动前 ``memory/write.py`` 落库段（原 :726-738）的**冻结复刻**
  （自带判据、不 import 活代码），代表「本轮之前会写成什么」；
- :func:`new_branch` **调用生产代码**（``app.memory.write._is_perception_source`` /
  ``perception_actor_column``），代表「本轮之后会写成什么」——新侧不许另抄一份，
  否则双跑只能证明两份抄本自洽，证明不了生产。

用法::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\memory\\actor_scope_dualrun.py
    #   再叠一遍存量投影（只读，把库里的行当作用例输入）：
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\memory\\actor_scope_dualrun.py --db backend\\data\\sqlite\\ai_companion.db

退出码：0=PASS；1=FAIL（非感知条被改，或感知条差异越出两档之外）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PERCEPTION = "perception"
USER = "user"

# 差异分档（evaluate 的计数键；同名常量便于测试逐档断言）
DIFF_SAME = "same"
DIFF_NULL_FILL = "null_to_perception"
DIFF_FALLBACK_REMOVED = "user_fallback_removed"
DIFF_UNEXPLAINED = "unexplained"


def _is_perception_local(source) -> bool:
    """感知来源判定（冻结复刻 ``write.py::_is_perception_source``，本脚本自带的旧侧判据）。"""
    return isinstance(source, str) and source.strip().lower() == PERCEPTION


# ───────────────────────────── 两侧判定 ─────────────────────────────
def old_branch(case: dict) -> dict:
    """改动前的落库判定（冻结复刻，不 import 活代码）。

    对应关系：``tag_on`` ＝ ``perception_source_tag``（旧「感知条例外」的门控闸），
    ``speaker_type`` / ``speaker_id`` ＝ 调用方入参（打标命中时上游已清空，与生产一致）。
    """
    spk_type = case.get("speaker_type")
    spk_id = case.get("speaker_id")
    perception_row = bool(case.get("tag_on")) and _is_perception_local(case.get("source"))
    if spk_type is None and spk_id is None and not perception_row:
        spk_type = USER            # 默认归属用户（丢失点 1）
        spk_id = case.get("user_id")
    actor = spk_type
    if actor is None and perception_row:
        actor = PERCEPTION         # 只留痕在准入归属，不落列（丢失点 2 的落点）
    return {"speaker_type": spk_type, "speaker_id": spk_id, "actor": actor}


def new_branch(case: dict) -> dict:
    """改动后的落库判定＝**调用生产代码**（闸不再参与感知条判定＝常开）。"""
    from app.memory.write import _is_perception_source, perception_actor_column

    spk_type = case.get("speaker_type")
    spk_id = case.get("speaker_id")
    if spk_type is None and spk_id is None and not _is_perception_source(case.get("source")):
        spk_type = USER
        spk_id = case.get("user_id")
    spk_type = perception_actor_column(spk_type, case.get("source"))
    return {"speaker_type": spk_type, "speaker_id": spk_id, "actor": spk_type}


# ───────────────────────────── 逐条比对 ─────────────────────────────
def classify(case: dict, old: dict, new: dict) -> str:
    """一条用例的差异归档；``unexplained`` 是唯一会导致 FAIL 的档。

    感知条只允许两档差异，且两档都要求「说话人 id 不被凭空改动」：
    - ``null_to_perception``：归属列由空补成 ``perception``，``speaker_id`` 一模一样；
      准入归属（actor）只允许在**关闸**样本上从「空/user」变成 ``perception``
      （要求 2 的常开效果；闸开时旧侧本来就是 ``perception``，actor 不动）；
    - ``user_fallback_removed``：关闸样本上「默认 user 兜底」被常开判定去掉
      （``user``/``user_id`` → ``perception``/``None``）——这正是断点 #5 例外升级为常开的本体。
    """
    if old == new:
        return DIFF_SAME
    if not _is_perception_local(case.get("source")):
        return DIFF_UNEXPLAINED                      # 判据 ①：非感知条一个字都不许动
    fill_only = (old["speaker_type"] is None and new["speaker_type"] == PERCEPTION
                 and old["speaker_id"] == new["speaker_id"])
    fallback_removed = (old["speaker_type"] == USER and old["speaker_id"] == case.get("user_id")
                        and new["speaker_type"] == PERCEPTION and new["speaker_id"] is None)
    if not (fill_only or fallback_removed):
        return DIFF_UNEXPLAINED
    actor_ok = new["actor"] == PERCEPTION and (
        old["actor"] == new["actor"]
        or (not case.get("tag_on") and old["actor"] in (None, USER))
    )
    if not actor_ok:
        return DIFF_UNEXPLAINED
    return DIFF_NULL_FILL if fill_only else DIFF_FALLBACK_REMOVED


def evaluate(cases: list[dict], old_fn=old_branch, new_fn=new_branch) -> dict:
    """跑完全部用例并给出 PASS/FAIL（``old_fn`` / ``new_fn`` 可注入，供测试构造反例）。"""
    counts = {DIFF_SAME: 0, DIFF_NULL_FILL: 0, DIFF_FALLBACK_REMOVED: 0, DIFF_UNEXPLAINED: 0}
    diffs: list[dict] = []
    nonperception_changed = 0
    for case in cases:
        old = old_fn(case)
        new = new_fn(case)
        kind = classify(case, old, new)
        counts[kind] = counts.get(kind, 0) + 1
        if kind != DIFF_SAME:
            rec = {"case": case, "old": old, "new": new, "kind": kind}
            diffs.append(rec)
            if not _is_perception_local(case.get("source")):
                nonperception_changed += 1
    return {
        "total": len(cases),
        "counts": counts,
        "diffs": diffs,
        "diff_total": len(diffs),
        "null_to_perception": counts[DIFF_NULL_FILL],
        "user_fallback_removed": counts[DIFF_FALLBACK_REMOVED],
        "nonperception_changed": nonperception_changed,
        "unexplained": counts[DIFF_UNEXPLAINED],
        "passed": nonperception_changed == 0 and counts[DIFF_UNEXPLAINED] == 0,
    }


# ───────────────────────────── 用例来源 ─────────────────────────────
def matrix_cases() -> list[dict]:
    """写入时点用例矩阵（覆盖非感知各来源 × 显式归属 × 有无 speaker_id × 闸开/关）。"""
    cases: list[dict] = []
    for tag_on in (True, False):
        # 非感知条：这些一支的落库值本轮一个字都不许动
        for src in (None, "", "chat", "diary", "life", "bio", "moment", "mcp_tools", "search_web"):
            for st in (None, USER, "character", "ai", "system", "tool", "  ", "未知值"):
                for sid in (None, 7):
                    cases.append({"source": src, "speaker_type": st, "speaker_id": sid,
                                  "user_id": 1, "tag_on": tag_on})
        # 感知条：打标命中路径（上游已清空两列）＋ 调用方直接写 source=perception
        for src in (PERCEPTION, " Perception ", "PERCEPTION"):
            for st, sid in ((None, None), (None, 7), (USER, 1), ("ai", 9), (USER, None)):
                cases.append({"source": src, "speaker_type": st, "speaker_id": sid,
                              "user_id": 1, "tag_on": tag_on})
    return cases


def cases_from_db(db_path: Path, limit: int = 5000) -> list[dict]:
    """存量投影用例（**只读**）：把库里的行当作用例输入，闸取生产现值＝开。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    try:
        cols = {r[1] for r in conn.execute('PRAGMA table_info("memories")').fetchall()}
        if not {"speaker_type", "source", "user_id"} <= cols:
            return []
        want = ["speaker_type", "source", "user_id"] + (["speaker_id"] if "speaker_id" in cols else ["NULL"])
        rows = conn.execute(f"SELECT {', '.join(want)} FROM memories LIMIT {int(limit)}").fetchall()
    finally:
        conn.close()
    out = []
    for st, src, uid, sid in rows:
        out.append({"source": src, "speaker_type": st, "speaker_id": sid,
                    "user_id": uid, "tag_on": True, "origin": "存量投影"})
    return out


def print_report(report: dict, source_label: str) -> None:
    c = report["counts"]
    print(f"[{source_label}] 用例={report['total']}  一致={c[DIFF_SAME]}  "
          f"预期差异={report['diff_total']}（NULL→perception={report['null_to_perception']}，"
          f"去掉user兜底={report['user_fallback_removed']}）  越界差异={report['unexplained']}")
    for d in report["diffs"][:10]:
        case, old, new = d["case"], d["old"], d["new"]
        print(f"    - [{d['kind']}] src={case.get('source')!r} 入参=({case.get('speaker_type')!r},"
              f"{case.get('speaker_id')!r}) tag_on={bool(case.get('tag_on'))} ⇒ "
              f"旧={old} 新={new}")
    if len(report["diffs"]) > 10:
        print(f"    …（其余 {len(report['diffs']) - 10} 条略）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="感知条 actor 归属双跑校验（非感知条逐字节一致）")
    parser.add_argument("--db", type=Path, default=None,
                        help="可选：叠加存量投影（只读打开，把库里的行当用例）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args(argv)

    cases = matrix_cases()
    matrix_report = evaluate(cases)
    extra_cases: list[dict] = []
    db_report = None
    if args.db is not None:
        extra_cases = cases_from_db(args.db)
        db_report = evaluate(extra_cases)
    overall = evaluate(cases + extra_cases)

    if args.json:
        print(json.dumps({"matrix": matrix_report, "db": db_report, "overall": overall,
                          "passed": overall["passed"]}, ensure_ascii=False, sort_keys=True, default=str))
    else:
        print("=== actor_scope_dualrun（旧判定 vs 新判定；新侧调用生产代码）===")
        print_report(matrix_report, "写入时点矩阵")
        if db_report:
            print_report(db_report, "存量投影（只读）")
        print("\n判据①非感知条被改=" + str(overall["nonperception_changed"]) + " 条（必须 0）")
        print("判据②感知条差异：NULL→perception=" + str(overall["null_to_perception"]) + " 条"
              "，去掉 user 兜底=" + str(overall["user_fallback_removed"]) + " 条"
              "，越界=" + str(overall["unexplained"]) + " 条（越界必须 0）")
        print("\n" + ("PASS" if overall["passed"] else "FAIL")
              + f"：差异共 {overall['diff_total']} 条 / 用例 {overall['total']} 条")
    return 0 if overall["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
