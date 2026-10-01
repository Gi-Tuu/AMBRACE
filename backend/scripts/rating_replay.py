# -*- coding: utf-8 -*-
"""A3 评星侧首轮回放（2026-10-01，Codex 亲做）。

真值口径（用户 2026-10-01 拍板「按你推荐」）：**生产 stars** —— 取
agent_task_logs(route='ai_rating_char') 里当次评星写入的星分（297/297 可恢复）；
当前 memories.importance 已随强化/衰减漂移（实测仅 9/297 等于 stars×20），**不作真值**。

回放内核：**复用生产 ai_rating._rate_batch**（不重写 prompt / 不改超参），在「相同记忆」上重跑一次，
比较 star 一致率 ⇒ 这是「同一路径重复一次」的**自一致性基线**，也是任何「第二路」能达到的上限锚点。

红线：只读生产库（mode=ro）取样本；**不改任何 memories 行**；只有 --allow-llm 才真调模型；
回放调用会按生产口径记 llm_usage 行（task=memory），属记账副作用、不改业务数据。
"""
from __future__ import annotations

import argparse
import asyncio
import sys as _sys
import json
import os
import pathlib
import sqlite3
from types import SimpleNamespace
_sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # 让 app.* 可导入

DB = "file:D:/AMBRACE/backend/data/sqlite/ai_companion.db?mode=ro"
_OUT_DIR = pathlib.Path(os.environ.get("AMBRACE_A3_RATING_OUT",
                                str(pathlib.Path(__file__).resolve().parents[2].parent / "a3_rating_output")))  # 仓库外默认
OUT = _OUT_DIR / "A3评星侧首轮样本.jsonl"


def export() -> list[dict]:
    con = sqlite3.connect(DB, uri=True)
    q = con.execute
    rows = q(
        "SELECT created_at, character_id, steps_json FROM agent_task_logs "
        "WHERE route='ai_rating_char' ORDER BY created_at"
    ).fetchall()
    out: list[dict] = []
    for ts, cid, sj in rows:
        try:
            data = json.loads(sj or "{}")
        except Exception:
            continue
        for mid, star in (data.get("stars") or {}).items():
            r = q("SELECT content, memory_type, importance FROM memories WHERE id=?", (int(mid),)).fetchone()
            if r is None:
                continue
            out.append({
                "rated_at": ts, "character_id": cid, "memory_id": int(mid),
                "prod_star": float(star), "content": (r[0] or "")[:400],
                "memory_type": r[1], "importance_now": float(r[2] or 0.0),
            })
    con.close()
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(o, ensure_ascii=False) + chr(10) for o in out), encoding="utf-8", newline="")
    print("exported", len(out), "->", OUT)
    return out


async def replay(limit: int) -> None:
    from app.memory.ai_rating import _rate_batch

    samples = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    samples = samples[:limit]
    parsed = agree = 0
    detail = []
    for i, s in enumerate(samples, 1):
        item = SimpleNamespace(id=s["memory_id"], memory_type=s.get("memory_type") or "insight",
                               content=s["content"])
        char = SimpleNamespace(id=s["character_id"], name="角色", user_id=1)
        try:
            res = await _rate_batch(char, [item])
        except Exception as e:
            print("call failed", s["memory_id"], e)
            continue
        star = next((float(r["star"]) for r in res if r["id"] == s["memory_id"]), None)
        if star is None:
            continue
        parsed += 1
        same = abs(star - s["prod_star"]) < 1e-9
        agree += 1 if same else 0
        detail.append({"memory_id": s["memory_id"], "prod": s["prod_star"], "replay": star, "same": same})
        if i % 20 == 0:
            print("  ...", i, "parsed", parsed, "agree", agree)
    rate = (agree / parsed) if parsed else 0.0
    print("REPLAY_DONE limit=%d parsed=%d agree=%d rate=%.3f" % (limit, parsed, agree, rate))
    (_OUT_DIR / "A3评星侧首轮回放结果.json").write_text(
        json.dumps({"limit": limit, "parsed": parsed, "agree": agree, "rate": rate, "detail": detail},
                   ensure_ascii=False, indent=1), encoding="utf-8", newline="")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--allow-llm", action="store_true")
    ap.add_argument("--stabilize", action="store_true")
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()
    if args.export:
        export()
    if args.replay:
        if not args.allow_llm:
            raise SystemExit("需要 --allow-llm 才真调模型")
        asyncio.run(replay(args.limit))
    if args.stabilize:
        if not args.allow_llm:
            raise SystemExit("需要 --allow-llm 才真调模型")
        asyncio.run(stabilize(args.limit, args.rounds))




# ══════════ 2026-10-01（用户拍板 B）：真值稳定化 ＋ 第二路（单 token 约束）══════════
# 稳定化口径：同一记忆**独立评 3 轮** ⇒ 取「多数星」（3 轮里出现 ≥2 次的星；3 轮全不同取中位数四舍五入）。
# 第二路（A3 路径 C「借范式、走现有通道」）：**单 token 文本约束** —— 只要求回答一个 1–5 的数字，
#   不要求 JSON、不给批量上下文；与「稳定化共识值」比对一致率。


async def _ask_single_token_star(character, item) -> float | None:
    """第二路：单 token 约束打分（只回一个数字）。解析失败 ⇒ None。"""
    from app.agent.llm_client import chat_completion

    hint = (
        "你是" + (character.name or "我") + "。下面是你关于用户的一条记忆。"
        "这条记忆对你们关系有多重要？只回答一个 1 到 5 的数字，不要任何其它文字。"
        "记忆内容：" + ((item.content or "")[:80])
    )
    try:
        text = await chat_completion(
            messages=[{"role": "user", "content": hint}],
            temperature=0.2, max_tokens=8, task="memory", user_id=1,
        )
    except Exception as e:
        print("second path call failed", item.id, e)
        return None
    if not text:
        return None
    for ch in str(text):
        if ch.isdigit() and "1" <= ch <= "5":
            return float(ch)
    return None


def _consensus(stars: list[float]) -> float | None:
    """三轮多数星；无多数取中位数四舍五入。"""
    vals = [s for s in stars if s is not None]
    if not vals:
        return None
    counts = {v: vals.count(v) for v in set(vals)}
    best = max(counts.values())
    if best >= 2:
        return sorted([v for v, c in counts.items() if c == best])[0]
    return float(round(sorted(vals)[len(vals) // 2]))


async def stabilize(limit: int, rounds: int) -> None:
    """3 轮评星 ⇒ 共识真值；并跑第二路单 token 打分做对比。"""
    from app.memory.ai_rating import _rate_batch

    samples = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines() if l.strip()][:limit]
    out = []
    for i, s in enumerate(samples, 1):
        item = SimpleNamespace(id=s["memory_id"], memory_type=s.get("memory_type") or "insight",
                               content=s["content"])
        char = SimpleNamespace(id=s["character_id"], name="角色", user_id=1)
        rounds_stars = []
        for _ in range(rounds):
            try:
                res = await _rate_batch(char, [item])
            except Exception:
                res = []
            rounds_stars.append(next((float(r["star"]) for r in res if r["id"] == s["memory_id"]), None))
        cons = _consensus(rounds_stars)
        second = await _ask_single_token_star(char, item)
        out.append({
            "memory_id": s["memory_id"], "prod_star": s["prod_star"],
            "rounds": rounds_stars, "consensus": cons, "second_path": second,
            "unanimous": len({v for v in rounds_stars if v is not None}) == 1 and None not in rounds_stars,
        })
        if i % 10 == 0:
            print("  ...", i)
    ok = [o for o in out if o["consensus"] is not None]
    unanimous = [o for o in ok if o["unanimous"]]
    prod_match = [o for o in ok if abs(o["consensus"] - o["prod_star"]) < 1e-9]
    second_ok = [o for o in out if o["second_path"] is not None and o["consensus"] is not None]
    second_match = [o for o in second_ok if abs(o["second_path"] - o["consensus"]) < 1e-9]
    print("STABILIZE_DONE n=%d ok=%d unanimous=%d(%.3f) prod_match=%d(%.3f) second_ok=%d second_match=%d(%.3f)" % (
        len(samples), len(ok), len(unanimous), (len(unanimous) / len(ok) if ok else 0.0),
        len(prod_match), (len(prod_match) / len(ok) if ok else 0.0),
        len(second_ok), len(second_match), (len(second_match) / len(second_ok) if second_ok else 0.0)))
    (_OUT_DIR / "A3评星侧真值稳定化.json").write_text(
        json.dumps({"limit": limit, "rounds": rounds, "n": len(samples), "ok": len(ok),
                    "unanimous": len(unanimous), "prod_match": len(prod_match),
                    "second_ok": len(second_ok), "second_match": len(second_match), "detail": out},
                   ensure_ascii=False, indent=1), encoding="utf-8", newline="")

if __name__ == "__main__":
    main()
