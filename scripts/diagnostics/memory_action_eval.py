# -*- coding: utf-8 -*-
"""批 0-4 · 动作式记忆评测器（M0-a：J3 检索集合判定 ＋ §2.3 三跑可解性认证，**零 LLM**）。

方案出处：`output/AMBRACE_批0-4_陪伴长期记忆回归评测集_方案_v1_20260928.md`。
补的是哪个洞（方案 §1.3）：现有测试全停在「记忆有没有进上下文块」，**没有任何一条**断言
「这次检索命中的集合对不对」，更没有「清空记忆后必须答不出」的可解性认证（泄题探测器）。

四条硬纪律（改它们＝改尺子本身，需另行拍板）：
1. **零计费**：J1/J2 要生成 ⇒ 属 M1，默认**拒绝运行**（须显式 `--allow-llm`）；
   评测期把统一 LLM 入口 `app.agent.llm_client.chat_completion` 打成抛异常——用运行时断言证明没花钱。
2. **确定性**：角色 id／内存 id 全部按用例序号分配，**不用 `hash()`**（PYTHONHASHSEED 会让两次跑不一样），
   报告里不写时间戳 ⇒ 同一提交连跑两次的 `--print-metrics` 必须逐字节一致（§4.5 可信度门槛）。
3. **J4 只允许 abstention 类**；judge 缺失或越界 ⇒ 数据集 lint 判不合法，脚本直接拒绝跑。
4. **假向量环境不得声称语义通过**（`backend/tests/conftest.py:229-231`）：`--sparse-only` 只走
   BM25／关键词／时间路，输出里 `semantic=false`，指标语义随之改名（AR_det ≠ AR_sem）。

用法（cwd=backend；全程临时库、用后即删，不碰生产）：
    .venv/Scripts/python.exe ../scripts/diagnostics/memory_action_eval.py \
        --dataset ../scripts/diagnostics/memory_action_cases_zh.jsonl \
        --judges J3 --configs baseline,no_temporal,no_peak --mode certify --out memory_action_report.md
门禁用法（M2 才接进 CI）：加 `--fail-below <AR_gold 基线−2pp>` 才有非零退出，默认保持"报告型"。
门禁**锚 AR_gold**（不是 AR_cert）：10-04 实测名次口径在已认证子集上饱和（97.9～100.0、关旗标反而更高），
饱和的列当门禁＝挂一条永远绿的线。守卫见 `test_门禁必须锚在有分辨力的列上`。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _load_bench():
    """按路径加载既有 bench：复用其 `_norm`／`parse_case`（禁止重造，方案 §五）。"""
    spec = importlib.util.spec_from_file_location("_memctx_bench_reused", str(HERE / "memory_context_bench.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bench = _load_bench()
_norm = bench._norm
parse_case = bench.parse_case

CUE_BLACKLIST = ("回忆", "上次", "之前", "说过", "还记得", "记得吗", "记得我",
                 "根据记忆", "我们聊过", "你答应", "提过", "当时")
REQUIRED_KEYS = ("cid", "category", "provenance", "persona", "seeds", "distractors", "turn",
                 "context_before", "expect", "judge", "time_anchor", "solvability",
                 "config_matrix", "flags_required", "tokens_budget")
CATEGORIES = ("fact", "preference", "relationship", "temporal", "group", "perception",
              "supersede", "abstention")
ALLOWED_JUDGES = ("J1", "J2", "J3", "J4")
J4_ONLY_CATEGORIES = ("abstention",)

# 配置轴（§4.4）——**基线＝生产实配，不是"四键全关"**。
# 2026-10-04 实测依据（只读 `runtime_flags` ＋ 读 `app/flags/agent_flags.py` 注册值）：
#   memory_temporal_recall   默认 **True**（10-02 转正，库里无覆盖行）
#   memory_recall_second_hop 默认 **True**（同上）
#   memory_peak_cutoff       默认 False，**生产覆盖 ON**（updated_at 2026-09-05）
#   memory_tiered_decay      默认 False、库里无覆盖（A19 处置＝预留）
# 另记一条口径局限：`flags_off` 只关增强旗标、**没清空记忆**，所以它不是方案 §4.4 的 B0 下限锚；
# 真 B0 要靠 `--mode certify` 的 no_mem 那趟，而 J3 档下「空库召不出」恒真 ⇒ **泄题探测要等 M1 的生成层**。
# ⇒ 把「全关」当 baseline 会把消融档读成现状（我第一版就犯了这个错，方向正好反了）。
# 其余档一律写成「在生产实配上关掉某一键」的消融，便于逐路归因。
PROD_FLAGS = {"memory_temporal_recall": True, "memory_recall_second_hop": True,
              "memory_peak_cutoff": True, "memory_tiered_decay": False}
BASELINE_FLAGS = PROD_FLAGS
CONFIG_MATRIX = {
    "baseline": {},                                                  # ＝生产实配
    "no_temporal": {"memory_temporal_recall": False},                # 消融：关时间路
    "no_second_hop": {"memory_recall_second_hop": False},            # 消融：关二跳
    "no_peak": {"memory_peak_cutoff": False},                        # 消融：关弃权硬顶
    "flags_off": {"memory_temporal_recall": False, "memory_recall_second_hop": False,
                  "memory_peak_cutoff": False},   # 四键全关；**不等于 §4.4 的 B0**（B0 还要清空记忆）
}


# ─────────────────────────── 数据集 lint（§2.1／§三） ───────────────────────────
def lint_dataset(cases) -> list[str]:
    """返回违规描述列表（空＝合法）。判分器先要被测，否则尺子不可信（R6）。"""
    out, seen = [], set()
    for c in cases:
        cid = c.get("cid") or "<无 cid>"
        if cid in seen:
            out.append("%s: cid 重复" % cid)
        seen.add(cid)
        missing = [k for k in REQUIRED_KEYS if k not in c]
        if missing:
            out.append("%s: 缺必填字段 %s" % (cid, missing))
            continue
        hit = [w for w in CUE_BLACKLIST if w in (c.get("turn") or "")]
        if hit:
            out.append("%s: 题面含检索提示词 %s（§2.1 硬要求①）" % (cid, hit))
        if c.get("category") not in CATEGORIES:
            out.append("%s: 未知 category=%r" % (cid, c.get("category")))
        if c.get("judge") not in ALLOWED_JUDGES:
            out.append("%s: judge=%r 不在 %s" % (cid, c.get("judge"), "/".join(ALLOWED_JUDGES)))
        if c.get("judge") == "J4" and c.get("category") not in J4_ONLY_CATEGORIES:
            out.append("%s: J4 只允许 %s 类（当前 %s）" % (cid, "/".join(J4_ONLY_CATEGORIES), c.get("category")))
        exp = c.get("expect") or {}
        gi = exp.get("gold_seed_idx") or []
        if not isinstance(gi, list) or any(not isinstance(i, int) for i in gi):
            out.append("%s: gold_seed_idx 必须是整数列表" % cid)
        elif not exp.get("abstain") and not gi:
            out.append("%s: 非弃权类必须有 gold_seed_idx" % cid)
        elif any(i < 0 or i >= len(c.get("seeds") or []) for i in gi):
            out.append("%s: gold_seed_idx 越界 %s（seeds 共 %d 条）" % (cid, gi, len(c.get("seeds") or [])))
        if not (c.get("distractors") or []):
            out.append("%s: 缺干扰项 ⇒ run_distractor 无法认证（§2.3）" % cid)
    return out


# ─────────────────────────── 判据（纯函数，可单测） ───────────────────────────
def j3_verdict(gold_ids, distractor_ids, recalled_ids):
    """J3 契约。两种口径**并列输出**，不偷偷换尺子：

    - `pass_rank`（默认 `pass`）：gold ⊆ top-k **且** 最后一条 gold 排在任何干扰项之前。
    - `pass_strict`：方案 §二 的字面契约「top-k ∩ distractor = ∅」。

    为什么要改默认口径（2026-10-04 首轮实测证据）：M0 的库每例只有 2 seeds ＋ 8 干扰项、k=5，
    字面契约要求 top-5 里**一个干扰项都不许有**——10 行的库里这几乎不可能满足，
    首轮 21/21 全报 E2 而 E1≈0（gold 其实都召回了）＝契约本身的前提（大库）不成立，不是记忆失效。
    保留 `pass_strict` 是为了等库规模上去后仍能与方案原文对账（改尺子必须留痕）。
    """
    got = list(recalled_ids or [])
    gset, dset = set(gold_ids or []), set(distractor_ids or [])
    missing = sorted(gset - set(got))
    polluted = sorted(set(got) & dset)
    conflict = sorted(gset & dset)
    gold_ranks = [got.index(i) for i in got if i in gset]
    dis_ranks = [got.index(i) for i in got if i in dset]
    pass_rank = bool(gset) and not missing and (not dis_ranks or max(gold_ranks) < min(dis_ranks))
    pass_strict = bool(gset) and not missing and not polluted
    # 第三种口径（2026-10-04 定口径时实测出来的，不与名次口径互相等价）：
    # `pass_gold`＝top-|gold| 全是 gold——它连**中性行**（既非 gold 也非干扰项）挤占名额也判失败，
    # 因此比名次口径严格、比"固定 k=5 内零干扰项"（在窄库上近乎恒假）有意义。
    # 实测反例（证明两者不等价）：gold=[1] 干扰=[2,3] 召回=[4,1,2] ⇒ 名次口径 True（4 是中性的、
    # gold 排在干扰项前），`pass_gold` False（top-1 被中性行占了）。⇒ 三列并列，各罚各的。
    kg = len(gset)
    pass_gold = bool(gset) and kg <= len(got) and set(got[:kg]) == gset
    return {"pass": pass_rank, "pass_rank": pass_rank, "pass_strict": pass_strict,
            "pass_gold": pass_gold,
            "missing": missing, "polluted": polluted, "expect_self_conflict": conflict,
            "n_recalled": len(got)}


def abstain_verdict(gold_ids, recalled_ids):
    """弃权类：库里本就无 gold ⇒ 任何「像 gold 的召回」都算不该有；此处只报召回条数供 ADR。"""
    return {"pass": not (set(gold_ids or []) & set(recalled_ids or [])),
            "n_recalled": len(recalled_ids or [])}


def classify_error(row):
    """§4.2 错因自动归因（规则判定、不用人）；返回 '' 表示 PASS 或无法归类。"""
    if row.get("pass"):
        return ""
    if row.get("missing") and not row.get("polluted"):
        return "E1"
    if row.get("polluted"):
        return "E2"
    return "E3_or_E4_needs_generation"


# ─────────────────────────── 临时库 / 灌数据 / 检索 ───────────────────────────
async def _init_temp_env(tmp):
    url = "sqlite+aiosqlite:///" + (tmp + "/eval.db").replace(os.sep, "/")
    os.environ["DATABASE_URL"] = url
    import app.config as cfg
    cfg.settings.database_url = url
    cfg.settings.chroma_persist_dir = tmp + "/chroma"
    os.environ["CHROMA_PERSIST_DIR"] = cfg.settings.chroma_persist_dir
    # BM25 索引默认落盘在 backend/data/bm25_cache/<character_id>.json（生产数据目录！）。
    # 评测造的假角色号（9000+）会写进那里并被下一轮读到——我第一版的「空库角色也召回到东西」
    # 就是这个跨轮缓存造成的假信号。模块自己留了隔离钩子（`_persist_root`），必须用上。
    from app.memory import bm25_index as _bm
    _bm._persist_root = Path(tmp) / "bm25_cache"
    from app.db.database import init_db
    await init_db()


def _guard_no_llm():
    """统一 LLM 入口打桩成抛异常（架构约定：所有 LLM 调用走 app/agent/llm_client.py）。"""
    import app.agent.llm_client as lc

    async def _boom(*a, **k):
        raise AssertionError("评测期出现 LLM 调用 ⇒ 违反零计费保证")

    hits = []
    for name in ("chat_completion", "chat_completion_stream", "complete"):
        if hasattr(lc, name):
            setattr(lc, name, _boom)
            hits.append(name)
    return hits


async def _ensure_parents(user_id, cid):
    """灌记忆前先补父行（临时库开了 `foreign_keys=ON`，缺 users/characters 父行会当场 IntegrityError）。

    这是「缺父行就补父行，而不是关 FK」的口径（`backend/tests/_dbclone.py` 的 clone_engine 文档串里
    把这条写死过：关 FK 掩盖的是种子缺失，不是数据库问题）。幂等：同 (user_id, cid) 只建一次。
    """
    from app.db.database import async_session_factory
    from app.models.character import AICharacter
    from app.models.user import User

    key = (user_id, cid)
    if key in _PARENTS_DONE:
        return
    async with async_session_factory() as db:
        if await db.get(User, user_id) is None:
            db.add(User(id=user_id, username="memact-eval", nickname="评测账号", is_admin=False))
        if await db.get(AICharacter, cid) is None:
            db.add(AICharacter(id=cid, user_id=user_id, name="memact-eval-%d" % cid))
        await db.commit()
    _PARENTS_DONE.add(key)


_PARENTS_DONE = set()


async def _insert(cid, user_id, rows, *, dense):
    """把一批记忆灌到指定 character_id，返回 (memory_id 顺序表, 稠密失败数)。"""
    from app.db.database import async_session_factory
    from app.models.memory import Memory
    from app.memory.embedding import text_embedding
    from app.db.vector_store import add_memory

    if not rows:
        return [], 0
    await _ensure_parents(user_id, cid)
    ids, dense_fail = [], 0
    async with async_session_factory() as db:
        for r in rows:
            d = str(r.get("date") or "").strip()
            created = datetime.strptime(d, "%Y-%m-%d") if d else datetime(2026, 8, 15)
            row = Memory(character_id=cid, user_id=user_id,
                         memory_type=r.get("memory_type") or r.get("type") or "event",
                         content=r["content"], importance=float(r.get("importance") or 50),
                         created_at=created, source=r.get("source") or "chat")
            db.add(row)
            await db.flush()
            ids.append(row.id)
            if dense:
                try:
                    emb = await text_embedding(r["content"])
                    await add_memory(row.id, cid, row.memory_type, r["content"], emb,
                                     importance=int(r.get("importance") or 50))
                except Exception:
                    dense_fail += 1
        await db.commit()
    return ids, dense_fail


async def _recall(cid, user_id, turn, k, *, sparse_only, flags):
    """走生产融合检索 `app/memory/retrieve.py:search_memories`（不是旧脚本的模拟器——§五 判词）。"""
    from app.agent.loop import AGENT_FLAGS
    saved = {kk: AGENT_FLAGS[kk] for kk in flags if kk in AGENT_FLAGS}
    AGENT_FLAGS.update(flags)
    try:
        if sparse_only:
            from app.memory.bm25_index import search as bm25_search
            from sqlalchemy import select
            from app.db.database import async_session_factory
            from app.models.memory import Memory as M
            from app.memory.time_query import parse_time_range
            out, seen = [], set()
            for mid, _sc in await bm25_search(cid, turn, top_k=k):
                if mid not in seen:
                    seen.add(mid)
                    out.append(mid)
            async with async_session_factory() as db:
                rows = (await db.execute(select(M).where(M.character_id == cid,
                                                        M.is_archived == False))).scalars().all()  # noqa: E712
            for r in rows:
                if r.id not in seen and (turn in (r.content or "")):
                    out.append(r.id)
            tr = parse_time_range(turn)
            if tr is not None:
                async with async_session_factory() as db:
                    trows = (await db.execute(select(M).where(
                        M.character_id == cid, M.is_archived == False,  # noqa: E712
                        M.created_at >= tr[0], M.created_at < tr[1]
                    ).order_by(M.importance.desc(), M.created_at.desc()).limit(k))).scalars().all()
                for r in trows:
                    if r.id not in seen:
                        out.append(r.id)
            return out[:k]
        from app.memory.retrieve import search_memories
        res = await search_memories(cid, turn, limit=k, user_id=user_id)
        return [x["id"] for x in res if isinstance(x, dict) and x.get("id") is not None]
    finally:
        for kk, v in saved.items():
            AGENT_FLAGS[kk] = v


# ─────────────────────────── 角色号段（配置之间必须隔离） ───────────────────────────
CID_BASE_DEFAULT = 9000
CID_SLOT_PER_CASE = 4          # 每例占 4 个槽：full / no_mem / distractor ／备用
CID_SLOT_PER_CONFIG = 100000   # 每个配置独占十万号段


def case_cids(cid_base, idx):
    """返回 (full, no_mem, distractor) 三个 character_id。

    为什么非要带配置序号（2026-10-04 实测踩到）：三跑认证把种子灌进**按用例序号固定的角色**，
    多配置连跑时同一角色会被前一个配置的种子重复灌满——到最后一个配置时，本配置新插入的 gold 行
    被前面的同内容旧行挤出 top-k，E1 从 3 暴涨到 53、AR 掉到 3.6%，看着像「旗标全关时记忆失效」，
    其实是**执行顺序的污染**。配置间独占号段后每档都是干净的库。
    """
    base = cid_base + idx * CID_SLOT_PER_CASE
    return base, base + 1, base + 2


# ─────────────────────────── 三跑认证（§2.3） ───────────────────────────
async def certify_case(case, idx, *, user_id, k, sparse_only, flags, cid_base=CID_BASE_DEFAULT):
    """no_mem / full / distractor 三跑；角色 id 由（配置，用例）两级号段确定性分配。"""
    cid_full, cid_nomem, cid_dis = case_cids(cid_base, idx)
    gold_idx = case["expect"].get("gold_seed_idx") or []

    seed_ids, f1 = await _insert(cid_full, user_id, case["seeds"], dense=not sparse_only)
    dis_ids, f2 = await _insert(cid_full, user_id, case.get("distractors") or [], dense=not sparse_only)
    gold_ids = [seed_ids[i] for i in gold_idx]

    full_runs = [j3_verdict(gold_ids, dis_ids, await _recall(cid_full, user_id, case["turn"], k,
                   sparse_only=sparse_only, flags=flags)) for _ in range(3)]
    full_pass = sum(1 for r in full_runs if r["pass"])

    got_nomem = await _recall(cid_nomem, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    no_mem_fail = (len(got_nomem) == 0)          # 空库必须召不出；召得出来＝题面泄料

    _dis_ids_cert, _nd = await _insert(cid_dis, user_id, case.get("distractors") or [], dense=not sparse_only)
    got_dis = await _recall(cid_dis, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    gold_texts = [case["seeds"][i]["content"] for i in gold_idx]
    dis_blob = _norm(" ".join(d["content"] for d in (case.get("distractors") or [])))
    distractor_leaks_gold = any(_norm(t) in dis_blob for t in gold_texts)

    # `distractor_no_gold`：只灌干扰项时，召回内容里不得出现任何 gold 文本——这才是能证的泄题探针。
    # （原来这里写的 `distractor_fail = len(got_dis)==0 or not full_pass` 没有意义：
    #   干扰项库里本就没有 gold 行，「判据不过」是结构必然，恒真＝空齿。）
    no_gold = not distractor_leaks_gold
    solv = {
        "no_mem_fail": bool(no_mem_fail),
        "full_pass3": int(full_pass),
        "distractor_no_gold": bool(no_gold),
        "certified": bool(no_mem_fail and full_pass >= 2 and no_gold),
        "distractor_recalled_n": len(got_dis),
        "distractor_leaks_gold": bool(distractor_leaks_gold),
        "no_mem_recalled_n": len(got_nomem),
        "dense_fail": int(f1 + f2),
    }
    return {"solvability": solv,
            "row": {"cid": case["cid"], "category": case["category"], "judge": case["judge"],
                    "certified": bool(solv["certified"]),
                    "pass": bool(full_pass >= 2),
                    "pass_strict": bool(full_runs[-1]["pass_strict"]),
                    "pass_gold": bool(full_runs[-1]["pass_gold"]),
                    "missing": full_runs[-1]["missing"],
                    "polluted": full_runs[-1]["polluted"]}}


async def score_case(case, idx, *, user_id, k, sparse_only, flags, cid_base=CID_BASE_DEFAULT):
    cid = cid_base + idx * CID_SLOT_PER_CASE
    seed_ids, _n1 = await _insert(cid, user_id, case["seeds"], dense=not sparse_only)
    dis_ids, _n2 = await _insert(cid, user_id, case.get("distractors") or [], dense=not sparse_only)
    gold_ids = [seed_ids[i] for i in (case["expect"].get("gold_seed_idx") or [])]
    got = await _recall(cid, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    v = abstain_verdict(gold_ids, got) if case["expect"].get("abstain") else j3_verdict(gold_ids, dis_ids, got)
    return {"cid": case["cid"], "category": case["category"], "judge": case["judge"],
            "certified": bool((case.get("solvability") or {}).get("certified")),
            "pass": bool(v["pass"]), "pass_strict": bool(v.get("pass_strict", v["pass"])),
            "pass_gold": bool(v.get("pass_gold", v["pass"])),
            "missing": v.get("missing", []),
            "polluted": v.get("polluted", []), "n_recalled": v.get("n_recalled", len(got))}


def summarize(rows):
    """AR 与 AR_strict/AR_gold 并列（§二 口径改动必须留痕，见 `j3_verdict` 的说明）。

    主分母＝**已认证子集**（2026-10-04 定，处置 §2.3 与棘轮的张力）：未认证的题不能用来定义
    基线，但它们留在集子里跟踪趋势（棘轮只许降）。三档分开出数，不偷偷合并：
    `ar`（全量，对账用）／`ar_certified`（主口径）／`ar_uncertified`（趋势）。
    行缺 `certified` 键时**两边都不算**（宁可少算，也不把「没标记」当成「未认证」）。
    """
    n = len(rows)
    p = sum(1 for r in rows if r["pass"])
    ps = sum(1 for r in rows if r.get("pass_strict", r["pass"]))
    pg = sum(1 for r in rows if r.get("pass_gold", r["pass"]))
    cert = [r for r in rows if r.get("certified") is True]
    uncert = [r for r in rows if r.get("certified") is False]
    by = {}
    for r in rows:
        # 累加器必须是**固定四格**（n/pass/pass_strict/pass_gold）。历史缺陷：这里用 append 追加第 4 格，
        # 读数却按固定下标 v[3] ⇒ 每类的 AR_gold 只反映该类**第一条**用例（8 条的类读出 12.5% 或 0），
        # 与总体 ar_gold 67.9% 自相矛盾。守卫见 test_分类汇总必须与总体对得上。
        b = by.setdefault(r["category"], [0, 0, 0, 0])
        b[0] += 1
        b[1] += 1 if r["pass"] else 0
        b[2] += 1 if r.get("pass_strict", r["pass"]) else 0
        b[3] += 1 if r.get("pass_gold", r["pass"]) else 0
    return {"n": n, "pass": p, "ar": round(100.0 * p / n, 1) if n else 0.0,
            "ar_strict": round(100.0 * ps / n, 1) if n else 0.0,
            "ar_gold": round(100.0 * pg / n, 1) if n else 0.0,
            "n_certified": len(cert),
            "ar_certified": round(100.0 * sum(1 for r in cert if r["pass"]) / len(cert), 1) if cert else None,
            "n_uncertified": len(uncert),
            "ar_uncertified": round(100.0 * sum(1 for r in uncert if r["pass"]) / len(uncert), 1) if uncert else None,
            "by_category": {c: {"n": v[0], "ar": round(100.0 * v[1] / v[0], 1),
                                "ar_strict": round(100.0 * v[2] / v[0], 1),
                                "ar_gold": round(100.0 * (v[3] if len(v) > 3 else 0) / v[0], 1)}
                            for c, v in sorted(by.items())}}


async def run_eval(cases, *, judges, configs, k, mode, sparse_only, limit):
    todo = [c for c in cases if c.get("judge") in judges]
    if limit:
        todo = todo[:limit]
    rep = {"cases_run": len(todo), "cases_total": len(cases), "semantic": not sparse_only,
           "skipped_judges": sorted({c.get("judge") for c in cases} - set(judges)),
           "mode": mode, "k": k, "configs": {}}
    if not todo:
        return rep
    tmp = tempfile.mkdtemp(prefix="ambrace_memact_eval_")
    try:
        await _init_temp_env(tmp)
        rep["llm_guard"] = _guard_no_llm()   # 无论语义路还是确定性路，评测期一律禁止生成
        user_id = 7100
        for ci, cfgname in enumerate(configs):
            assert cfgname in CONFIG_MATRIX, "未知配置 %s（可选：%s）" % (cfgname, ",".join(CONFIG_MATRIX))
            flags = {**BASELINE_FLAGS, **CONFIG_MATRIX[cfgname]}
            cid_base = CID_BASE_DEFAULT + ci * CID_SLOT_PER_CONFIG   # 每档独占号段
            rows = []
            for i, c in enumerate(todo):
                if mode == "certify":
                    out = await certify_case(c, i, user_id=user_id, k=k, sparse_only=sparse_only,
                                         flags=flags, cid_base=cid_base)
                    c["solvability"] = {**c.get("solvability", {}), **out["solvability"]}
                    out["row"]["err"] = classify_error(out["row"])
                    rows.append(out["row"])
                else:
                    rows.append(await score_case(c, i, user_id=user_id, k=k, sparse_only=sparse_only,
                                     flags=flags, cid_base=cid_base))
            s = summarize(rows)
            s["rows"] = rows
            rep["configs"][cfgname] = s
        return rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render(rep, *, dataset, judges, configs, mode):
    L = ["# 动作式记忆评测（批 0-4 · M0-a）", ""]
    L.append("- 数据集 `%s`｜判据 %s｜配置 %s｜k=%s｜模式 %s" % (
        dataset, ",".join(judges), ",".join(configs), rep["k"], mode))
    L.append("- 语义路：**%s**" % ("真 bge-m3（本机 backend/models/bge-m3）" if rep["semantic"]
                                   else "确定性路（BM25/关键词/时间）——**不得**当作语义通过"))
    L.append("- 零计费保证：LLM 入口被打桩 %s" % (rep.get("llm_guard") or "（未启用）"))
    if rep.get("skipped_judges"):
        L.append("- 本轮**未跑**判据：%s（J1/J2 属 M1：需生成＝计费端点＋用户授权）" % ",".join(rep["skipped_judges"]))
    L += ["", "AR＝gold 进 top-k 且排在任何干扰项之前（主口径）；**AR_strict**＝方案 §二 字面契约（固定 k 内零干扰项，窄库上近乎恒假，仅对账用）；**AR_gold**＝top-|gold| 全是 gold（连中性行挤占也判失败，最严）。三口径**可证不等价**（含反例），见测试 `test_三种口径的强弱关系与反例`。",
          "- **主分母＝已认证子集**（§2.3 认证三跑过的题）；未认证的题留在集里只跟踪趋势，不参与基线定义。AR_cert 与 AR(全量) 并列输出，不合并。"
          "**但 AR_cert 不得当门禁用**：10-04 实测认证子集已饱和（名次口径 97.9～100.0，且关旗标反而更高），门禁锚 AR_gold。",
          "", "| 配置 | 条数 | 已认证 | AR_cert(%)(主) | AR(%) | AR_strict(%) | AR_gold(%) | E1 没召回 | E2 被干扰污染 | 泄题嫌疑 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for cn, d in sorted(rep["configs"].items()):
        e1 = sum(1 for r in d["rows"] if r["missing"])
        e2 = sum(1 for r in d["rows"] if r["polluted"])
        leak = sum(1 for r in d["rows"] if r.get("solvability", {}).get("no_mem_fail") is False)
        L.append("| %s | %d | %d | %s | %s | %s | %s | %d | %d | %d |" % (
            cn, d["n"], d["n_certified"], d["ar_certified"], d["ar"], d["ar_strict"],
            d["ar_gold"], e1, e2, leak))
    for cn, d in sorted(rep["configs"].items()):
        L += ["", "## 配置 %s · 分类" % cn, "| 类别 | 条数 | AR(%) | AR_strict(%) | AR_gold(%) |",
              "|---|---|---|---|---|"]
        for cat, v in sorted(d["by_category"].items()):
            L.append("| %s | %d | %s | %s | %s |" % (cat, v["n"], v["ar"], v["ar_strict"],
                                             v["ar_gold"]))
        L += ["", "## 配置 %s · 逐用例" % cn,
              "| cid | 类别 | 判据 | PASS | 未命中 | 污染 | 召回数 |", "|---|---|---|---|---|---|---|"]
        for r in d["rows"]:
            L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                r["cid"], r["category"], r["judge"], r["pass"],
                ",".join(map(str, r["missing"])) or "—",
                ",".join(map(str, r["polluted"])) or "—", r.get("n_recalled", "")))
    return "\n".join(L)


def metrics_json(rep):
    """机器可读：排序＋无时间戳 ⇒ 同一提交连跑两次必须逐字节一致（§4.5 第一半）。"""
    slim = {"cases_run": rep["cases_run"], "cases_total": rep["cases_total"],
            "semantic": rep["semantic"], "mode": rep["mode"], "k": rep["k"],
            "skipped_judges": rep.get("skipped_judges", []),
            "configs": {cn: {"n": d["n"], "ar": d["ar"],
                             "n_certified": d["n_certified"], "ar_certified": d["ar_certified"],
                             "n_uncertified": d["n_uncertified"], "ar_uncertified": d["ar_uncertified"],
                             "by_category": d["by_category"],
                             "rows": [{kk: r.get(kk) for kk in ("cid", "category", "judge", "certified",
                                                                "pass", "pass_strict", "pass_gold",
                                                                "missing", "polluted")}
                                      for r in d["rows"]]}
                       for cn, d in sorted(rep["configs"].items())}}
    return json.dumps(slim, ensure_ascii=False, sort_keys=True, indent=1)


async def main():
    ap = argparse.ArgumentParser(description="动作式记忆评测（M0-a：J3 ＋ 三跑认证）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--judges", default="J3")
    ap.add_argument("--configs", default="baseline")
    ap.add_argument("--mode", choices=["score", "certify"], default="certify")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sparse-only", action="store_true", help="只走确定性路（CI／假向量环境）")
    ap.add_argument("--allow-llm", action="store_true", help="放行 J1/J2（计费端点，须用户授权）")
    ap.add_argument("--out", default="memory_action_report.md")
    ap.add_argument("--emit-solvability", default="", help="把认证结果写成 jsonl（回填数据集用，M0-b）")
    ap.add_argument("--print-metrics", action="store_true")
    ap.add_argument("--fail-below", type=float, default=None)
    a = ap.parse_args()

    judges = [x.strip().upper() for x in a.judges.split(",") if x.strip()]
    if {"J1", "J2"} & set(judges) and not a.allow_llm:
        print("[拒绝] J1/J2 需要生成 ⇒ 云端计费端点。方案把它们排在 M1；确要跑请显式 --allow-llm。",
              file=sys.stderr)
        return 2
    with open(a.dataset, encoding="utf-8-sig") as f:
        cases = [c for c in (parse_case(ln) for ln in f) if c]
    bad = lint_dataset(cases)
    if bad:
        print("[数据集不合法] lint 违规 %d 处：" % len(bad), file=sys.stderr)
        for b in bad[:20]:
            print("   - " + b, file=sys.stderr)
        return 3
    print("[lint] 合法：%d 条，违规 0" % len(cases))
    configs = [x.strip() for x in a.configs.split(",") if x.strip()]
    rep = await run_eval(cases, judges=judges, configs=configs, k=a.k, mode=a.mode,
                         sparse_only=a.sparse_only, limit=a.limit)
    text = render(rep, dataset=os.path.basename(a.dataset), judges=judges, configs=configs, mode=a.mode)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print(text)
    if a.emit_solvability:
        with open(a.emit_solvability, "w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps({"cid": c["cid"], "solvability": c.get("solvability")},
                                   ensure_ascii=False) + "\n")
    if a.print_metrics:
        print("METRICS_JSON_BEGIN")
        print(metrics_json(rep))
        print("METRICS_JSON_END")
    if a.fail_below is not None:
        # 门禁锚在 **AR_gold**（2026-10-04 实测后改的，不是凭口味）：认证子集上的名次口径已**饱和**
        # （baseline 97.9／flags_off 100.0／no_peak 100.0，且方向反了——关旗标分更高），
        # 拿它当门禁等于挂一条永远绿的线。AR_gold 有 32pp 余量（总体 67.9、分类 50–100），才配当棘轮。
        vals = [d["ar_gold"] for d in rep["configs"].values()]
        worst = min(vals, default=0.0)
        if worst < a.fail_below:
            print("[门禁] AR_gold %s < --fail-below %s（configs=%s）" % (
                worst, a.fail_below, sorted(rep["configs"])), file=sys.stderr)
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
