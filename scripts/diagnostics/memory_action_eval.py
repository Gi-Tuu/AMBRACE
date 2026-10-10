# -*- coding: utf-8 -*-
"""批 0-4 · 动作式记忆评测器（M0-a：J3 检索集合判定 ＋ §2.3 三跑可解性认证，**零 LLM**）。

方案出处：`output/AMBRACE_批0-4_陪伴长期记忆回归评测集_方案_v1_20260928.md`。
补的是哪个洞（方案 §1.3）：现有测试全停在「记忆有没有进上下文块」，**没有任何一条**断言
「这次检索命中的集合对不对」，更没有「清空记忆后必须答不出」的可解性认证（泄题探测器）。

四条硬纪律（改它们＝改尺子本身，需另行拍板）：
1. **零计费是默认，不是唯一形态**：J1/J2 要生成 ⇒ 默认**拒绝运行**（须显式 `--allow-llm`）；
   2026-10-05 判分函数 `j1_verdict`/`j2_verdict` 已落地并被单测钉住，同日接上生成侧＝`--mode generate`
   （S1 试点）。两条闸**都要过**：只给 `--allow-llm` 不给 `--mode generate` 仍拒绝——
   否则 J1/J2 的题会静默走 J3 检索尺子＝拿错尺子还有分。
   `score`/`certify` 期把统一 LLM 入口 `app.agent.llm_client.chat_completion` 打成抛异常，
   用运行时断言证明没花钱；`generate` 期反过来**不许打桩**，改为从 `llm_usage` 反查真打了几次（见 `run_generate_eval`）。
2. **确定性**：角色 id／内存 id 全部按用例序号分配，**不用 `hash()`**（PYTHONHASHSEED 会让两次跑不一样），
   报告里不写时间戳 ⇒ 同一提交连跑两次的 `--print-metrics` 必须逐字节一致（§4.5 可信度门槛）。
3. **J4 只允许 abstention 类**；judge 缺失或越界 ⇒ 数据集 lint 判不合法，脚本直接拒绝跑。
4. **假向量环境不得声称语义通过**（`backend/tests/conftest.py:229-231`）：`--sparse-only` 只走
   BM25／关键词／时间路，输出里 `semantic=false`，指标语义随之改名（AR_det ≠ AR_sem）。

用法（cwd=backend；全程临时库、用后即删，不碰生产）：
    .venv/Scripts/python.exe ../scripts/diagnostics/memory_action_eval.py \
        --dataset ../scripts/diagnostics/memory_action_cases_zh.jsonl \
        --judges J3 --configs baseline,no_temporal,no_peak --mode certify --out memory_action_report.md
生成侧（**计费**，须用户授权）：
    .venv/Scripts/python.exe ../scripts/diagnostics/memory_action_eval.py \
        --dataset ../scripts/diagnostics/memory_action_cases_j1j2_draft.jsonl \
        --judges J1 --mode generate --allow-llm --temperature 0 --limit 10 --out ../output/x.md
门禁用法（M2 才接进 CI）：加 `--fail-below <基线−2pp>` 才有非零退出，默认保持"报告型"。
门禁**锚「认证子集 × AR_gold」**（不是名次口径）：10-04 实测名次口径在**语义路＋10 行小库**上饱和
（97.9～100.0、关旗标反而更高），而同样的库在**确定性路**上只有 40.4 ⇒ 饱和是「语义路×小库」的产物。
因此 M1 的加难＝**抬库规模**（`--fillers`，默认 44 条/例），认证口径**不动**（见 `certify_verdict`：
把门禁要量的 `pass_gold` 当认证门槛会造成自我循环，该列恒≈100，比饱和更糟）。
守卫见 `test_门禁必须锚在有分辨力的列上`、`test_认证门槛不得用门禁要量的那列`。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
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
# J1 期望的动作类型。**在这里复制一份而不是 import**：lint 跑在灌库之前，不该把 app 拉起来。
# 与 `app/agent/actions.py:ACTION_TYPES` 的一致性由 backend/tests/test_memory_action_eval_j1j2.py
# 的漂移守卫钉住（新增动作类型忘了同步 ⇒ 那条题会被 lint 判不合法，而不是静默判不了）。
KNOWN_ACTION_TYPES = frozenset({
    "SEARCH", "RECALL", "GEN_IMAGE", "IMG_TEXT", "CAL_NOTE", "MEMO",
    "NOTE_DONE", "TIMER", "STATUS_UPDATE",
})
# **落库通道**（不是动作类型）：`【记忆：内容】` 由 `app/agent/response_parser.py` 的 `parse_response`
# 提取后走 `save_memory`，是生产里"把用户交代的事记下来"的**主通道**；`[MEMO]` 只是小手机备忘录。
# 10-05 试点实测：模型被要求"记一下"时走的就是这条通道（j1f01 回复里明写
# `【记忆：用户女儿叫朵朵，2021年出生】`），而判分器当时只认 `parse_actions` ⇒ 把"用对了通道"
# 判成 E3"没用记忆去做事"。**这是改尺子，2026-10-05 由用户拍板（B＋A 各半）后才加。**
# 单列成 `J1_CHANNEL_TYPES` 而不是塞进 `KNOWN_ACTION_TYPES`：后者与生产 `ACTION_TYPES` 的逐位相等
# 由守卫钉着，混进去就会让那条漂移守卫失去意义。
J1_CHANNEL_TYPES = frozenset({"MEMORY"})
# 与 `parse_response` 里那条正则**同源**（漂移守卫读生产源码逐字比对，改一边另一边当场红）
MEMORY_CHANNEL_RE = r"[\[【]\s*记忆\s*[：:]\s*(.*?)[\]】]"


def extract_memory_channel(text: str) -> list[str]:
    """取出回复里的 `【记忆：…】` 载荷文本（判分用；生产落库走同一个模式）。

    A30 批 2（2026-10-10，方案 §四①）：取出的那段文本**过生产那个规范化函数**再交给判据，
    与 `app/agent/response_parser.py` 落库时那次调用同一份实现（正则→strip→normalize 同序）。
    为什么必须同源：规范化落在生产落库点，而这条读的是**模型原始输出**；不同源的话，落库
    早已定形、指纹里的「通道」两题（j1s01/j1s02）却照旧量原始措辞 ⇒ 严格一致读数永不动。
    边界：只换"判据吃的那份文本从哪来"；判据、阈值、gold、指纹维度、这条正则一律没动。
    """
    from app.memory.normalize import normalize_memory_text

    return [normalize_memory_text(s.strip())
            for s in re.findall(MEMORY_CHANNEL_RE, text or "") if s.strip()]

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
        # 形状检查（10-08 补，来历＝真实语料草稿集）：干扰项写成**裸字符串**时旧 lint 会放行，
        # 一路跑到 certify 的 `fillers_for` 才炸 `'str' object has no attribute 'get'`——
        # 尺子的规则应当在使用点之前就把人拦住，而不是让跑半小时向量的作业半途崩。
        for key in ("seeds", "distractors"):
            for j, item in enumerate(c.get(key) or []):
                if not isinstance(item, dict) or not str(item.get("content") or "").strip():
                    out.append("%s: %s[%d] 必须是含非空 content 的字典（裸字符串缺字段都会让 certify 半途崩）"
                               % (cid, key, j))
                    break
        # J1/J2 的标注完整性：判分函数已实现（吃的是数据集既有字段名），但「声明了 J1/J2
        # 却没出完标注」的题一旦被拿去跑，就会静默走 J3 的检索尺子（＝用错尺子还看着有分）。
        if c.get("judge") == "J1":
            want = _want_types(exp)
            at_raw = exp.get("action_type")
            if not want:
                out.append("%s: judge=J1 但 expect.action_type 缺失 ⇒ 这条题没出完" % cid)
            elif isinstance(at_raw, (list, tuple)) and "NONE" in want and len(want) > 1:
                out.append("%s: action_type 里 NONE 与别的值同时出现（%s）⇒ 判据自相矛盾" % (cid, want))
            else:
                unknown = [w for w in want if w not in KNOWN_ACTION_TYPES
                           and w not in J1_CHANNEL_TYPES and w != "NONE"]
                if unknown:
                    out.append("%s: expect.action_type=%s 里有不是已知动作类型/通道值的项（与 "
                               "app/agent/actions.py 的 ACTION_TYPES 或 J1_CHANNEL_TYPES 对不上＝这条题永远判不过）"
                               % (cid, unknown))
                elif "MEMORY" in want and resolve_expect_date(c.get("time_anchor")):
                    out.append("%s: MEMORY 通道没有日期字段，却又给了锚点日期 ⇒ 永远判不过"
                               "（日期题请只写 CAL_NOTE）" % cid)
                elif "NONE" not in want and not (exp.get("field_match") or exp.get("forbidden")
                                                 or resolve_expect_date(c.get("time_anchor"))):
                    out.append("%s: J1 只标了动作类型、没标 field_match/forbidden、题面也没锚点日期"
                               " ⇒ 「产出了但参数全错」判不出来" % cid)
        if c.get("judge") == "J2":
            if not (exp.get("slots") or exp.get("slots_none")):
                out.append("%s: judge=J2 但 expect.slots/slots_none 都没标 ⇒ 这条题没出完" % cid)
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


# ─────────────────────── J1 / J2 判据（§2.2 优先级 J1 > J2 > J3 > J4）───────────────────────
# **字段名沿用数据集既有词汇**，不另造平行 schema：77 条正式集早就带
# `expect.action_type / expect.field_match / expect.forbidden`（其中 `forbidden` 有 32 条填了值
# 却从来没有消费者），日期则由 `time_anchor.expect_date / expect_offset_days` 权威给出。
# 判分是**纯函数**：给它动作列表／槽位字典就能判，所以能先于生成侧接线完成自测
# （方案 §五「判分器必须先被测，否则尺子不可信」）。要「跑」才需要计费端点。
_J1_PAYLOAD_KEYS = ("query", "text", "prompt", "match")   # parse_actions 里各动作的载荷内容字段
# 判别用字段（NOTE_DONE 的 calendar/memo、TIMER 的原始标记串）：**不进内容比对**，
# 否则 `field_match:{"*": "接孩子"}` 会被类型词污染。某条题真要判它们，得显式加判据键，
# 而不是把判别词混进内容里——漂移守卫见 backend/tests/test_memory_action_eval_j1j2.py。
_J1_META_KEYS = frozenset({"type", "tag"})
_J1_DATE_FIELD = "date"          # CAL_NOTE 载荷里由 parse_actions(base_date=锚点) 换算出的绝对日期


def _payload_text(payload) -> str:
    """把动作载荷摊成一段可比对文本（MEMO/CAL_NOTE/SEARCH/NOTE_DONE 的字段名不同，判据只看内容）。"""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    return " ".join(str(payload.get(k) or "") for k in _J1_PAYLOAD_KEYS)


def resolve_expect_date(time_anchor) -> str:
    """题面锚点 → 期望绝对日期。`expect_date` 优先；否则 `as_of + expect_offset_days`；都没有 ⇒ ""。

    日期**不写进 expect**：同一个事实的日期期望本来就等于题面锚点，写两处迟早自相矛盾
    （10-05  unify 之前我的 j1.date 就是这种重复）。
    """
    if not isinstance(time_anchor, dict):
        return ""
    ed = str(time_anchor.get("expect_date") or "").strip()
    if ed:
        return ed[:10]
    off = time_anchor.get("expect_offset_days")
    as_of = str(time_anchor.get("as_of") or "").strip()
    if off is None or not as_of:
        return ""
    try:
        base = datetime.strptime(as_of[:10], "%Y-%m-%d")
        return (base + timedelta(days=int(off))).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ""


def _want_types(expect: dict) -> list:
    """`expect.action_type` 允许**字符串或列表**（列表＝"这几条通道任一达成即可"）。

    为什么不另造键名：数据集本来就管这个字段叫 `action_type`，再开一个 `accepted_channels`
    ＝同一件事两套词，后人必踩（与 10-05 那次"字段名统一回既有词表"是同一条纪律）。
    **`None` 与空串必须滤掉**：写成 `str(None)` 会变成 "NONE"，于是"这条题还没出完"
    被静默读成"期望什么都不产出"——没出完的题反而会判 E4/pass 而不是 E_unannotated。
    旧守卫 `test_J1_没标动作类型时判不了` 就是当场把这个错抓回来的（不是事后想到的）。
    """
    at = (expect or {}).get("action_type")
    vals = list(at) if isinstance(at, (list, tuple)) else ([] if at is None else [at])
    return [s for s in (str(v).strip().upper() for v in vals if v is not None) if s]


def j1_verdict(expect: dict, actions: list, *, expect_date: str = "",
               memory_texts: list | None = None) -> dict:
    """J1 动作参数判定。``actions`` ＝ ``parse_actions(模型输出, base_date=题面锚点)`` 的结果。

    读 `expect` 的三个既有键：
      `action_type`  期望达成的**落库方式**：`app/agent/actions.py` 的 ACTION_TYPES，
                     或通道值 `MEMORY`（`【记忆：…】`→`parse_response`→`save_memory`）；可写列表表示
                     "任一即达成"；``"NONE"``＝**任何动作都不该出**
      `field_match`  {载荷字段: 片段}；伪字段 ``"*"``＝片段出现在任一内容字段即可
      `forbidden`    [片段]——出现在命中候选的载荷里即失败（用错事实／已被作废的旧值复发）。
                     **例外（10-05 拍板）**：片段只出现在"同一短句内含作废词"的句子里＝作废语境，
                     不判违规，但会在返回的 `exempted` 里留痕（见 `_forbidden_check`）
    外加 `expect_date`（由 `resolve_expect_date(time_anchor)` 得到）：CAL_NOTE 必须落成这个绝对日期。
    `memory_texts` ＝ `extract_memory_channel(模型输出)`；**没传就等于这条题的 MEMORY 通道判不了**，
    此时若 `action_type` 里写了 MEMORY，返回 E_unannotated（不许静默按"没产出"扣分）。

    两种错因分开（§4.2）：**没产出期望动作**＝E3（没用记忆去做事）；
    **产出了但参数不对**＝E4（拿错事实去做事——比 E3 危险，因为它会真的落库）。
    """
    exp = expect or {}
    want = _want_types(exp)
    if not want:
        return {"pass": False, "err": "E_unannotated", "why": "expect.action_type 为空 ⇒ 这条题没出完",
                "exempted": []}
    chan = [str(t).strip() for t in (memory_texts or []) if str(t).strip()]
    got_all = actions or []
    if want == ["NONE"]:                       # 期望**不要**产出动作（常识可推的事不该记成备忘）
        produced = sorted({getattr(a, "action_type", '?') for a in got_all})
        if chan:
            produced.append("MEMORY")
        return {"pass": not produced, "err": "" if not produced else "E4",
                "why": "" if not produced else "不该产出动作却产出了：%s" % ",".join(produced),
                "exempted": []}
    if "MEMORY" in want and memory_texts is None:
        return {"pass": False, "err": "E_unannotated", "exempted": [],
                "why": "题面期望 MEMORY 通道，但调用方没传 memory_texts ⇒ 判不了（不是模型的错）"}
    got = [a for a in got_all if getattr(a, "action_type", None) in
           {w for w in want if w in KNOWN_ACTION_TYPES}]
    # 候选＝命中通道/动作的全部产出物；通道载荷没有字段名，统一当 text 看待
    cands = [(a.payload or {}) for a in got] + [{"text": t} for t in chan] if ("MEMORY" in want) \
        else [(a.payload or {}) for a in got]
    if not cands:
        seen = sorted({getattr(a, "action_type", '?') for a in got_all} | ({"MEMORY"} if chan else set()))
        return {"pass": False, "err": "E3", "exempted": [],
                "why": "未产出期望动作/通道 %s（实际产出：%s）" % ("/".join(want), ",".join(seen) or "无")}
    bad = []
    for field, frag in (exp.get("field_match") or {}).items():
        if field == "*":
            if not any(_norm(str(frag)) in _norm(_payload_text(p)) for p in cands):
                bad.append("缺片段:" + str(frag))
        elif not any(_norm(str(frag)) in _norm(str((p or {}).get(field) or "")) for p in cands):
            bad.append("%s 应含 %s" % (field, frag))
    fb, exempt = _forbidden_check(exp, cands)
    bad += fb
    if expect_date:
        # 相对时间（下周三/三个月前）必须落成绝对日期。换算由生成侧调 parse_actions(base_date=锚点)
        # 完成——**判据只比结果**，这样尺子不会跟着本机日历走（A30-② 立这条入参的原因）。
        # 日期只在动作载荷里（【记忆：】通道没有日期字段），所以这里只看 `got`；
        # lint 会拦「MEMORY 与日期判据同时出现」的题，免得它永远判不过。
        if not any(str((a.payload or {}).get(_J1_DATE_FIELD) or "")[:10] == expect_date for a in got):
            bad.append("%s≠%s" % (_J1_DATE_FIELD, expect_date))
    if bad:
        return {"pass": False, "err": "E4", "why": "；".join(str(b) for b in bad), "exempted": exempt}
    return {"pass": True, "err": "", "why": "", "exempted": exempt}


# **作废语境豁免**（10-05 深夜拍板「forbidden 加作废词豁免」）。
# 为什么必须加：supersede 类的**正确写法**就是「已搬离城北老小区，现居城西」——旧值出现在
# "声明它已作废"的那半句里。旧口径见片段就判 E4，等于把"处理对了"读成"用错了记忆"，
# 而这一类恰好是这套尺子要量的重点（拿错事实去做事）。
# 口径收紧在**短句**上：豁免只在含作废词的同一短句内生效，跨短句不豁免
# （「已搬离老地址。用户仍住在城北老小区」第二句照样违规），免得豁免变成一个吞掉一切的宽松词。
FORBIDDEN_RETIRE_WORDS = ("已搬离", "已搬走", "已换", "已改成", "已忌口", "不再是", "不再",
                          "早已", "以前", "别按", "别再", "翻篇", "作废", "改口", "已不", "不是")
# **反豁免词**（优先级高于作废词）：旧值被当作**现状仍在生效**来说。
# 这条不是补充说明，是被旧守卫打红之后补上的：`test_J1_forbidden_是数据集里那32条在等的消费者`
# 的样本「用户腰伤已好转，还像**以前**一样忌久站」含"以前"⇒ 单看作废词表会被豁免，
# 而这句恰恰是"旧值复发"的反例。**只加豁免不加反豁免＝把尺子从太严改成太松**。
FORBIDDEN_KEEP_WORDS = ("还像", "依然", "还是", "照旧", "仍然", "仍", "照样", "一贯",
                        "一直都", "一点没变", "本来就", "没变")
_CLAUSE_SPLIT = re.compile(r"[，。；！？、,.;!?\n\r\t ]+")


def _forbidden_check(expect: dict, cands: list) -> tuple:
    """返回 `(违规列表, 豁免留痕列表)`——豁免**必须被记下来**，不许静默放行。

    一个短句被判"作废语境豁免"要同时满足：含禁用片段 ＋ 含作废词 ＋ **不含反豁免词**。
    豁免条目会进报表：判据对某个片段网开一面＝这类事要能被人数出来、被复核。
    """
    bad, exempt = [], []
    for frag in (expect.get("forbidden") or []):
        n = _norm(str(frag))
        if not n:
            continue
        hits = []
        for p in cands:
            for clause in _CLAUSE_SPLIT.split(_payload_text(p) or ""):
                if clause and n in _norm(clause):
                    hits.append(clause.strip())
        if not hits:
            continue
        strict = [c for c in hits
                  if not any(_norm(w) in _norm(c) for w in FORBIDDEN_RETIRE_WORDS)
                  or any(_norm(w) in _norm(c) for w in FORBIDDEN_KEEP_WORDS)]
        if strict:
            bad.append("禁用片段命中:" + str(frag))
        else:
            exempt.append("%s｜作废语境豁免：%s" % (frag, "／".join(hits)[:60]))
    return bad, exempt


def j2_verdict(expect: dict, written_slots: dict) -> dict:
    """J2 语义槽落库判定。读 `expect.slots`（{槽 key: 必须命中的片段}）与 `expect.slots_none`（不得写的槽）。"""
    exp = expect or {}
    slots, none_keys = (exp.get("slots") or {}), [str(x) for x in (exp.get("slots_none") or [])]
    if not slots and not none_keys:
        return {"pass": False, "err": "E_unannotated", "why": "expect.slots/slots_none 都没标 ⇒ 这条题没出完"}
    got = written_slots or {}
    bad = [("缺槽 %s（应为 %s）" % (k, v)) for k, v in slots.items()
           if _norm(str(v)) not in _norm(str(got.get(k) or ""))]
    bad += ["不该写却写了 %s" % k for k in none_keys if str(got.get(k) or "").strip()]
    return {"pass": not bad, "err": "" if not bad else "E4", "why": "；".join(bad)}


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


# ─────────────────────────── 库规模填充（M1 加难，2026-10-04） ───────────────────────────
# 为什么要填充（实测证据）：M0 每例只有 2 seeds ＋ 8 干扰项＝10 行，k=5 占了库的一半，
# 「gold 全挤进前排」在这个规模下是**结构巧合**而非检索能力 ⇒ 名次口径在**语义路的**已认证子集上饱和
# （AR_cert 97.9～100.0，关旗标反而满分；同一个库走确定性路只有 40.4，所以饱和是「语义路×小库」的产物）。
# 真实陪伴库里一个角色是几十到几百条，不抬规模就量不到东西。默认把每例抬到 ~FILLER_TARGET_DEFAULT 行。
#
# 硬约束：**填充句不许夹带答案**。判据不是「字符串不等」，而是复用②通道同一个滑窗——
# 填充句里不得出现任何 gold 正文的 ≥6 字连续片段（否则它就成了换了个 id 的 gold）。
FILLER_TARGET_DEFAULT = 44
# 注意写法：曾经用过 `tuple("甲" "乙" ...)`——那是**逐字符**成元组（相邻字面量先拼接），
# 池子当场变成几十个单字，守卫 `test_填充池必须是整句` 就是钉这个的。
FILLER_POOL = (
    "今早出门晚了十分钟，地铁上挤得全靠门",
    "楼下那家面馆换老板了，味道跟以前不一样",
    "周末把阳台的衣服收了，预报说有雨",
    "昨天把耳机落在工位抽屉里了",
    "最近追一部讲深海科考的纪录片，一集二十来分钟",
    "晚上十一点还在改方案，眼睛发干",
    "把冬天的大衣送进干洗店了",
    "通勤路上听完了一本讲桥的建造史",
    "午休趴着睡，醒来手麻了半边",
    "超市促销，顺手买了两袋米，拎上六楼很累",
    "楼道感应灯坏了两天，报修还没来",
    "上周开始学着把咖啡改成少糖",
    "周五晚上约了朋友打羽毛球，场地费 aa",
    "半夜饿了煮了包泡面，加个蛋",
    "手机存储满了，删了一堆截图",
    "把书桌上的绿植挪到窗边，叶子朝着光长",
    "早高峰打车排队四十分钟，最后还是坐了地铁",
    "看了个讲老式录像带的展览，视频里说收藏者上万",
    "连续三天熬夜，第四天睡了十四个小时",
    "把旧衣服装了两大袋，准备捐掉",
    "楼下新开了一家裁缝铺，换拉链五块钱",
    "雨天路滑，骑车摔了一跤，膝盖破皮",
    "晚上加班到十点，回家只洗了脸就躺下",
    "预约了周六上午洗牙，提前一小时到",
    "把客厅的射灯换成暖光的，看着不那么刺眼",
    "朋友寄来一箱橙子，一个人吃不完",
    "早上跑步摔了个趔趄，旁边全是遛狗的",
    "看了两集讲深海热泉生态的科普片",
    "换季把被子晒了，晚上有太阳味道",
    "电脑风扇响得厉害，清了灰还是响",
    "中午吃得太油，下午一直犯困",
    "把书架上倒下来的那排书重新码齐",
    "高铁上信号断断续续，剧没看完",
    "夜里三点被楼上的装修声吵醒一次",
    "买了个带刻度的水杯，提醒自己多喝水",
    "周会改到上午十点，早高峰得提前出门",
    "把不用的会员卡注销了两个",
    "阳台上那盆多肉开始抽条，长得歪",
    "冬天手套忘在公交上了",
    "路边摊的烤红薯十块钱两个，买了俩",
    "整理手机相册，删掉三百多张重复截图",
    "下午困得趴在桌上，脸颊压出印子",
    "把厨房的纸巾架换了个位置，够着顺手",
    "夜里降温，加了床薄毯",
    "骑共享单车上学，比公交快六分钟",
    "看了一部讲极地科考船过冬的纪录片，片长九十分钟",
    "把换季鞋收进箱子里，箱子塞不进床底",
    "食堂今天排骨做得偏咸，喝了不少水",
)


def _gold_texts_of(case):
    gold_idx = case["expect"].get("gold_seed_idx") or []
    return [case["seeds"][i]["content"] for i in gold_idx]


def fillers_for(case, idx, target):
    """按用例序号**确定性**取填充句（不用 hash／随机源 ⇒ 两次跑逐字节一致）。

    返回 (填充行, 因夹带答案被剔除的条数)。剔除判据复用②通道同一个滑窗：
    填充句里不得出现任何 gold 正文的 ≥6 字连续片段——否则它就是「换了个 id 的 gold」。
    """
    if target <= 0:
        return [], 0
    from app.memory.utility_feedback import _contains_key_fragment, _core_snippet
    n = len(FILLER_POOL)
    dis_texts = {d.get("content") for d in (case.get("distractors") or [])}
    gold_frags = [_core_snippet(g) for g in _gold_texts_of(case)]
    out, skip = [], 0
    for off in range(n):
        if len(out) >= target:
            break
        text = FILLER_POOL[(idx * 7 + off) % n]
        if text in dis_texts:
            continue                                   # 别把本题干扰项当填充（会双计）
        if any(_contains_key_fragment(f, _norm(text)) for f in gold_frags):
            skip += 1
            continue                                   # 夹带答案 ⇒ 剔除
        out.append({"content": text, "memory_type": "event", "importance": 35,
                    "date": "2026-07-20", "source": "chat"})
    return out, skip


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
def certify_verdict(no_mem_fail, full_rank3, full_gold3, no_gold):
    """认证＝**题目合法性**，不是「当前系统做得好不好」。

    门槛用**名次口径** `full_rank3 ≥ 2`（方案 §2.3 的字面口径），`full_gold3` 只记录不作门槛。
    为什么不能用 gold 口径当门槛（10-04 我先这么改了，随即实测否掉）：`certified` 一旦由
    `pass_gold` 决定，「认证子集上的 AR_gold」就变成**自我循环**——选子集的条件就是门禁要量的那个数，
    该列恒 ≈100（本轮 certify 跑：47→35 条认证，认证子集 AR_gold 直接 100.0），
    headroom 归零，比原来的饱和更糟。正确的加难方向是**抬库规模**（`--fillers`）＋
    门禁读 AR_gold，让「合法题」与「做得对不对」两件事分开。
    旧名次计数与新 gold 计数都留痕（改尺子必须能对账，方案 §4.5）。
    """
    return {"no_mem_fail": bool(no_mem_fail),
            "full_pass3": int(full_rank3),
            "full_gold3": int(full_gold3),
            "distractor_no_gold": bool(no_gold),
            "certified": bool(no_mem_fail and full_rank3 >= 2 and no_gold)}


async def certify_case(case, idx, *, user_id, k, sparse_only, flags, cid_base=CID_BASE_DEFAULT,
                       filler_target=FILLER_TARGET_DEFAULT):
    """no_mem / full / distractor 三跑；角色 id 由（配置，用例）两级号段确定性分配。"""
    cid_full, cid_nomem, cid_dis = case_cids(cid_base, idx)
    gold_idx = case["expect"].get("gold_seed_idx") or []
    fil, filler_skipped = fillers_for(case, idx, filler_target)

    seed_ids, f1 = await _insert(cid_full, user_id, case["seeds"], dense=not sparse_only)
    dis_ids, f2 = await _insert(cid_full, user_id, case.get("distractors") or [], dense=not sparse_only)
    _fil_ids, f3 = await _insert(cid_full, user_id, fil, dense=not sparse_only)
    gold_ids = [seed_ids[i] for i in gold_idx]

    full_runs = [j3_verdict(gold_ids, dis_ids, await _recall(cid_full, user_id, case["turn"], k,
                   sparse_only=sparse_only, flags=flags)) for _ in range(3)]
    full_rank3 = sum(1 for r in full_runs if r["pass"])
    full_gold3 = sum(1 for r in full_runs if r["pass_gold"])

    got_nomem = await _recall(cid_nomem, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    no_mem_fail = (len(got_nomem) == 0)          # 空库必须召不出；召得出来＝题面泄料

    _dis_ids_cert, _nd = await _insert(cid_dis, user_id,
                                       (case.get("distractors") or []) + fil, dense=not sparse_only)
    got_dis = await _recall(cid_dis, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    gold_texts = [case["seeds"][i]["content"] for i in gold_idx]
    dis_blob = _norm(" ".join(d["content"] for d in (case.get("distractors") or [])))
    distractor_leaks_gold = any(_norm(t) in dis_blob for t in gold_texts)

    # `distractor_no_gold`：只灌干扰项时，召回内容里不得出现任何 gold 文本——这才是能证的泄题探针。
    # （原来这里写的 `distractor_fail = len(got_dis)==0 or not full_pass` 没有意义：
    #   干扰项库里本就没有 gold 行，「判据不过」是结构必然，恒真＝空齿。）
    no_gold = not distractor_leaks_gold
    solv = certify_verdict(no_mem_fail, full_rank3, full_gold3, no_gold)
    solv.update({
        "distractor_recalled_n": len(got_dis),
        "distractor_leaks_gold": bool(distractor_leaks_gold),
        "no_mem_recalled_n": len(got_nomem),
        "dense_fail": int(f1 + f2 + f3),
        "library_rows": int(len(case["seeds"]) + len(case.get("distractors") or []) + len(fil)),
        "filler_rows": len(fil),
        "filler_skipped": int(filler_skipped),
    })
    return {"solvability": solv,
            "row": {"cid": case["cid"], "category": case["category"], "judge": case["judge"],
                    "certified": bool(solv["certified"]),
                    "pass": bool(full_rank3 >= 2),
                    "pass_gold_majority": bool(full_gold3 >= 2),
                    "pass_strict": bool(full_runs[-1]["pass_strict"]),
                    "pass_gold": bool(full_runs[-1]["pass_gold"]),
                    "n_rows": solv["library_rows"],
                    "missing": full_runs[-1]["missing"],
                    "polluted": full_runs[-1]["polluted"]}}


async def score_case(case, idx, *, user_id, k, sparse_only, flags, cid_base=CID_BASE_DEFAULT,
                     filler_target=FILLER_TARGET_DEFAULT):
    cid = cid_base + idx * CID_SLOT_PER_CASE
    fil, _skip = fillers_for(case, idx, filler_target)
    seed_ids, _n1 = await _insert(cid, user_id, case["seeds"], dense=not sparse_only)
    dis_ids, _n2 = await _insert(cid, user_id, case.get("distractors") or [], dense=not sparse_only)
    _fid, _n3 = await _insert(cid, user_id, fil, dense=not sparse_only)
    gold_ids = [seed_ids[i] for i in (case["expect"].get("gold_seed_idx") or [])]
    got = await _recall(cid, user_id, case["turn"], k, sparse_only=sparse_only, flags=flags)
    v = abstain_verdict(gold_ids, got) if case["expect"].get("abstain") else j3_verdict(gold_ids, dis_ids, got)
    return {"cid": case["cid"], "category": case["category"], "judge": case["judge"],
            "certified": bool((case.get("solvability") or {}).get("certified")),
            "n_rows": len(case["seeds"]) + len(case.get("distractors") or []) + len(fil),
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
        # 累加器必须是**固定五格**（n/pass/pass_strict/pass_gold/认证数）。历史缺陷：这里用 append 追加第 4 格，
        # 读数却按固定下标 v[3] ⇒ 每类的 AR_gold 只反映该类**第一条**用例（8 条的类读出 12.5% 或 0），
        # 与总体 ar_gold 67.9% 自相矛盾。守卫见 test_分类汇总必须与总体对得上。
        b = by.setdefault(r["category"], [0, 0, 0, 0, 0])
        b[0] += 1
        b[1] += 1 if r["pass"] else 0
        b[2] += 1 if r.get("pass_strict", r["pass"]) else 0
        b[3] += 1 if r.get("pass_gold", r["pass"]) else 0
        b[4] += 1 if r.get("certified") else 0
    return {"n": n, "pass": p, "ar": round(100.0 * p / n, 1) if n else 0.0,
            "ar_strict": round(100.0 * ps / n, 1) if n else 0.0,
            "ar_gold": round(100.0 * pg / n, 1) if n else 0.0,
            # 认证率是**尺子自身的健康度**：加难之后分母会缩，缩了必须喊出来，
            # 否则「认证子集 AR 很高」是用「把做不到的题踢出分母」换来的。
            "cert_rate": round(100.0 * len(cert) / n, 1) if n else 0.0,
            # 库规模必须跟着读数一起出来，否则「AR 掉了」无法区分是尺子紧了还是检索坏了
            "rows_mean": round(sum(int(r.get("n_rows") or 0) for r in rows) / n, 1) if n else 0.0,
            "n_certified": len(cert),
            # 门禁锚的是 AR_gold ⇒ 必须给出「认证子集上的 AR_gold」，否则门禁读的和报表显示的不是一个数
            "ar_gold_certified": (round(100.0 * sum(1 for r in cert
                                                    if r.get("pass_gold", r["pass"])) / len(cert), 1)
                                  if cert else None),
            "ar_certified": round(100.0 * sum(1 for r in cert if r["pass"]) / len(cert), 1) if cert else None,
            "n_uncertified": len(uncert),
            "ar_uncertified": round(100.0 * sum(1 for r in uncert if r["pass"]) / len(uncert), 1) if uncert else None,
            "by_category": {c: {"n": v[0], "ar": round(100.0 * v[1] / v[0], 1),
                                "ar_strict": round(100.0 * v[2] / v[0], 1),
                                "n_certified": v[4],
                                "ar_gold": round(100.0 * v[3] / v[0], 1)}
                            for c, v in sorted(by.items())}}


# ─────────────────── S1 生成侧接线（A30，2026-10-05）───────────────────
# 口径只有一条：**装配交给生产节点**（`retrieve_memories` → `build_context`），LLM 参数照抄
# `nodes.py:generate_response` 的非流式分支。评测里绝不另拼一份 prompt——两把尺子迟早漂，
# 那与「拿已清洗的列去测原始内容＝假零」是同族的错：量的不是被测对象。
#
# 与生产刻意不同处只有一处，且必须记账（不然会把"评测里的模型"当成"用户手机上的模型"）：
#   `reasoning_level=0`（关思考）——方案 §三 要求可复现，且少一半 token。
#   `temperature` **默认跟随生产口径**（读 `state["temperature"]`，由 build_context 按心情给 0.7/0.8/0.9），
#   要钉死才传 `--temperature 0`；试点那轮就是钉 0 跑的，报表里必须出现实际用的那个数。
CHAT_MAX_TOKENS = 900                 # 与 nodes.py 非流式分支同值；那边改了这边守卫会红
EVAL_TEMPERATURE = None               # None＝读 state（生产同一行）；显式数值＝钉死（可复现档）
EVAL_REASONING_LEVEL = 0
EVAL_CHAT_TASK = "chat"
# 生成三跑一致（§S3 原话：「J1/J2 的认证还要多一列『生成三跑一致』，否则又是一把不可复现的尺子」）
GENERATE_REPEAT_DEFAULT = 3
# 10-06 用户拍板：J1 的分数与认证**只认「每一跑都过」**（不是"三跑里最好的一跑"，也不是"取二"——
# 同一批 30 次实测"取二"与"每跑都过"读数相同（没有一题恰好两跑过），中间档买不到东西，所以口径就是这一条）
CERT_J1_RULE = "检索侧四条认证 ∧ 每一跑都过(pass_all) ∧ 三跑结果一致（剥掉钟点后逐字一致，10-10 拍板）"
GENERATE_USER_ID = 7100               # 与 J3 档同一个评测账号（临时库里的假用户，不碰生产）

# 生成侧要复制的 LLM 配置表＝`_resolve_llm_config` 解析链**真正读**的那几张
# （llm_client.py：任务专用 → 新 user_llm_configs 链（角色绑定>用户默认>家庭默认）→ api_configs 服务器级 → .env）。
# 为什么"复制进临时库"而不是"把 key 传进命令行"：
#   ① 优先级链本身要被测——绕过它就等于拿"我以为的模型"去量，而门禁要的是"生产真在用的模型"
#      （10-05 只读数：服务器级 api_configs(user_id=0) 的 model 是 `deepseek-chat`，而角色 8 那条
#       `deepseek-v4-flash` enabled=0 ⇒ 复制出来的解析结果和 AGENTS.md 里写的名字不一样，这正是必须实测的理由）；
#   ② key 不进 argv（会留在命令行历史里）。
# 空表也留在清单里并照样复制（复制 0 行）：**这是"清单完整"的反证**——哪天 user_llm_configs 长出行了，
#   它已经在清单里，评测自动跟着变；若当初只挑 api_configs，那张表变化就是**静默错配**。
LLM_CONFIG_TABLES = ("api_configs", "user_llm_configs", "task_llm_configs")


def _sqlite_file(url: str) -> str:
    """从 `sqlite+aiosqlite:///路径` 里取出文件路径（绝对/相对、正斜杠反斜杠都得认）。

    `://` 之后的**第一个** `/` 是「空 host」的分隔符，只能去掉这一个：Windows 的绝对路径写三斜杠
    （`sqlite:///C:/x.db`），POSIX 的绝对路径要写四斜杠（`sqlite:////tmp/x.db`，与 SQLAlchemy 同口径，
    也是本文件 `_init_temp_env` 自己拼的形态）。见到几个斜杠就剥几个会把 POSIX 的**根斜杠**吃掉，
    路径退化成相对路径 ⇒ 库文件"不存在"（10-06 CI 的 Linux 档就是这么红的）。
    """
    s = (url or "").strip()
    if "://" in s:
        s = s.split("://", 1)[1].removeprefix("/")
    if re.match(r"^/[A-Za-z]:", s):
        s = s[1:]              # 容错：Windows 上多写一个斜杠（sqlite:////C:/x.db）
    return s.replace("/", os.sep)


_PROD_DB_CACHE = {}


def prod_database_path() -> str:
    """取**评测还没改写前**的库路径＝生产库（只读用）。非 sqlite／文件不存在 ⇒ 抛错，不静默退回无 key 配置。

    **第一次取到就缓存**：`_init_temp_env` 会把 `DATABASE_URL` 改写成临时库，`finally` 里又 `rmtree` 掉它——
    于是同一进程的第二轮再调本函数，解析出来的是"一个已被删除的临时库"（10-06 用桩验 `--repeat`
    时当场炸在这，报"生产库文件不存在"）。缓存让"跑第几轮"不再影响它指向哪份生产库。
    """
    from app.config import settings

    if _PROD_DB_CACHE.get("path"):
        return _PROD_DB_CACHE["path"]
    url = os.environ.get("DATABASE_URL") or settings.database_url or ""
    if "sqlite" not in url:
        raise RuntimeError("生成侧试点要把 LLM 配置表复制进临时库，只支持 sqlite 生产库；当前 DATABASE_URL=%s" % url)
    path = _sqlite_file(url)
    if not os.path.isfile(path):
        raise RuntimeError("生产库文件不存在：%s（解析自 %s）" % (path, url))
    _PROD_DB_CACHE["path"] = path
    return path


def borrow_llm_config(prod_path: str, dest_path: str) -> dict:
    """把 `LLM_CONFIG_TABLES` **只读**复制进临时库，返回 `{表: {rows, skipped_cols}}`。

    源侧一律 `mode=ro` ⇒ 生产库不可能被评测写（这是本试点唯一的硬约束）。
    `api_key` 整列复制（没 key 就调不动模型），但**任何输出里都不打印它**：报表只出 model/base_url/enabled。
    表在源侧不存在＝**抛错**而不是跳过——少复制一张表就是"配置链没测全"，静默继续会把这轮读数作废。
    """
    import sqlite3

    src = sqlite3.connect("file:%s?mode=ro" % prod_path.replace("\\", "/"), uri=True)
    dst = sqlite3.connect(dest_path)
    out = {}
    try:
        for t in LLM_CONFIG_TABLES:
            scols = [r[1] for r in src.execute("PRAGMA table_info(%s)" % t)]
            dcols = [r[1] for r in dst.execute("PRAGMA table_info(%s)" % t)]
            if not scols:
                raise RuntimeError("生产库没有表 %s（复制清单需要它，缺席＝配置链没测全）" % t)
            if not dcols:
                raise RuntimeError("临时库没有表 %s（init_db 建出来的模式与解析链需要的不一致）" % t)
            cols = [c for c in scols if c in dcols]
            skipped = [c for c in scols if c not in dcols]
            rows = src.execute("SELECT %s FROM %s" % (",".join('"%s"' % c for c in cols), t)).fetchall()
            dst.execute("DELETE FROM %s" % t)
            if rows:
                dst.executemany("INSERT OR REPLACE INTO %s (%s) VALUES (%s)" % (
                    t, ",".join('"%s"' % c for c in cols), ",".join("?" * len(cols))), rows)
            dst.commit()
            out[t] = {"rows": len(rows), "skipped_cols": skipped}
        return out
    finally:
        src.close()
        dst.close()


def resolved_llm_identity(user_id: int) -> dict:
    """同步读临时库，拼出"这轮实际会打到哪个端点/模型"的**脱敏**描述（绝不返回 key）。"""
    import sqlite3

    path = _sqlite_file(os.environ.get("DATABASE_URL") or "")
    out = {"user_id": user_id, "server": None, "byok": None, "task_rows": 0}
    if not os.path.isfile(path):
        return out
    c = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        def one(uid):
            r = c.execute("SELECT provider,base_url,model,enabled,length(COALESCE(api_key,'')) "
                          "FROM api_configs WHERE user_id=?", (uid,)).fetchone()
            return ({"provider": r[0], "base_url": r[1], "model": r[2], "enabled": r[3], "key_len": r[4]}
                    if r else None)
        out["server"] = one(0)
        out["byok"] = one(user_id)
        out["task_rows"] = c.execute("SELECT COUNT(*) FROM task_llm_configs").fetchone()[0]
        out["user_llm_config_rows"] = c.execute("SELECT COUNT(*) FROM user_llm_configs").fetchone()[0]
    finally:
        c.close()
    return out


def _flag_snapshot(flags: dict) -> dict:
    """只保留**注册表里真有**的键（旗标快照，报表里要出现"这轮跑在什么旗标下"）。"""
    from app.agent.loop import AGENT_FLAGS

    return {k: AGENT_FLAGS[k] for k in sorted(flags) if k in AGENT_FLAGS}


def _apply_flags(flags: dict) -> dict:
    """临时拨旗标并返回「原值快照」，配合 `_restore_flags` 用（与 `_recall` 同口径）。"""
    from app.agent.loop import AGENT_FLAGS
    saved = {k: AGENT_FLAGS[k] for k in flags if k in AGENT_FLAGS}
    AGENT_FLAGS.update(flags)
    return saved


def _restore_flags(saved: dict) -> None:
    from app.agent.loop import AGENT_FLAGS
    AGENT_FLAGS.update(saved)


async def ensure_chat_rows(user_id: int, cid: int, case: dict) -> tuple[int, int]:
    """把 `context_before` ＋ 本轮用户消息按生产落库口径写进临时库，返回 (session_id, 本轮 user 消息 id)。

    装配读的就是 `chat_sessions ⋈ chat_messages`（`context_builder.py` 那段 join）：
    这两张表没行 ⇒ 拼出来的 prompt 与生产不可能一致，评测就成了自说自话。
    本轮用户消息也要落库——生产是"先落库再进图"，`_host_user_msg_index` 那条护栏依赖这一点；
    `source_id` 同样照抄生产（chat_service 传的就是这条 user 消息的 id）。
    """
    from datetime import timedelta

    from app.db.database import async_session_factory
    from app.models.chat import ChatMessage, ChatSession

    await _ensure_parents(user_id, cid)
    base = datetime(2026, 10, 5, 12, 0, 0)
    async with async_session_factory() as db:
        s = ChatSession(user_id=user_id, character_id=cid, title="memact-eval",
                        is_active=True, created_at=base, updated_at=base)
        db.add(s)
        await db.flush()
        rows = list(case.get("context_before") or []) + [{"role": "user", "text": case["turn"]}]
        uid = None
        for i, m in enumerate(rows):
            msg = ChatMessage(session_id=s.id,
                              sender_type="user" if m.get("role") == "user" else "ai",
                              content=m.get("text") or "", is_read=True,
                              created_at=base + timedelta(seconds=i))
            db.add(msg)
            if m.get("role") == "user":
                uid = msg
        await db.commit()
        return int(s.id), int(uid.id)


async def match_production_character(cid: int) -> None:
    """把评测角色的认知开关拨到**生产实配**（10-05 只读数：生产 9 个角色 `cognitive_loop_enabled` 全＝1）。

    `perceive` 写 `state["perception"]`，`retrieve_memories` 拿它做话题/情绪派生查询 ⇒
    留着关等于评测里**少一路召回**，量的就不是生产那条链了。
    """
    from sqlalchemy import update

    from app.db.database import async_session_factory
    from app.models.character import AICharacter

    async with async_session_factory() as db:
        await db.execute(update(AICharacter).where(AICharacter.id == cid)
                         .values(cognitive_loop_enabled=True))
        await db.commit()


async def gen_state(case: dict, cid: int, user_id: int, *, flags: dict | None = None) -> dict:
    """跑**生产图的前三个节点**（perceive→retrieve_memories→build_context）拼出 `context_messages`。

    绝不在这儿自己拼 prompt：装配一旦分叉，评测尺子和生产行为迟早漂。
    """
    from app.agent.nodes import build_context as _build_ctx, perceive as _perceive
    from app.agent.nodes import retrieve_memories as _retrieve
    from app.agent.runtime import _build_initial_state

    sid, uid = await ensure_chat_rows(user_id, cid, case)
    state = _build_initial_state(character_id=cid, user_id=user_id, session_id=sid,
                                 user_message=case["turn"], lang="zh",
                                 reasoning_level=EVAL_REASONING_LEVEL, source_id=uid)
    saved = _apply_flags(flags or {})
    try:
        await _perceive(state)
        await _retrieve(state)
        await _build_ctx(state)
    finally:
        _restore_flags(saved)
    return state


async def seed_library(case: dict, idx: int, user_id: int, *, cid_base: int = CID_BASE_DEFAULT,
                       filler_target: int = FILLER_TARGET_DEFAULT):
    """把一题灌成**认证三跑里 full 那一档**的形态（seeds＋distractors＋填充，带稠密向量）。

    和 `certify_case` 用同一个 cid 算式与同一套灌法：J3 量过的库和 J1 生成用的库必须是同一个库，
    否则"检索没问题、生成没答上"这类归因根本无从对齐。
    """
    cid = cid_base + idx * CID_SLOT_PER_CASE
    fil, filler_skipped = fillers_for(case, idx, filler_target)
    seed_ids, _f1 = await _insert(cid, user_id, case["seeds"], dense=True)
    dis_ids, _f2 = await _insert(cid, user_id, case.get("distractors") or [], dense=True)
    _fid, _f3 = await _insert(cid, user_id, fil, dense=True)
    await match_production_character(cid)
    return cid, seed_ids, dis_ids, len(fil), filler_skipped


async def generate_case(case: dict, cid: int, user_id: int, *,
                        temperature: float | None = EVAL_TEMPERATURE,
                        flags: dict | None = None) -> dict:
    """一题：装配 → 生成（**这一步计费**）→ `parse_actions(base_date=锚点)` → J1 判分。

    `base_date` 传题面 `as_of`：这正是口径②存在的理由——模型写「明天」时，
    按锚点换算才是可复现的判据，按本机时钟判就是今天绿后天红。
    """
    from app.agent.actions import parse_actions
    from app.agent.llm_client import chat_completion

    state = await gen_state(case, cid, user_id, flags=flags)
    temp = temperature if temperature is not None else float(state.get("temperature") or 0.8)
    # 显式参数全传 None＝让 `_resolve_llm_config` 自己走那条优先级链（配置复制在临时库里，
    # 所以它读到的就是生产那份）；在这里替它挑 key/model 就等于把被测对象换成了我的假设。
    text = await chat_completion(
        messages=state["context_messages"], temperature=temp, max_tokens=CHAT_MAX_TOKENS,
        task=EVAL_CHAT_TASK, user_id=user_id, character_id=cid)
    if isinstance(text, tuple):        # include_reasoning 才会返回元组；这里 defensively 取正文
        text = text[0]
    anchor = str((case.get("time_anchor") or {}).get("as_of") or "").strip() or None
    actions = parse_actions(text or "", base_date=anchor)
    # 两条落库通道都交给判分器：`parse_actions` 的动作标记 ＋ `parse_response` 的【记忆：】。
    # 10-05 试点就是因为只喂了前者，把"用对了通道"读成了 E3。
    chan = extract_memory_channel(text)
    v = j1_verdict(case.get("expect") or {}, actions,
                   expect_date=resolve_expect_date(case.get("time_anchor")), memory_texts=chan)
    gold = [case["seeds"][i]["content"] for i in (case["expect"].get("gold_seed_idx") or [])]
    blob = _norm(" ".join(str(x.get("content") or x.get("text") or "")
                          for x in (state.get("retrieved_memories") or []) if isinstance(x, dict)))
    return {"cid": case["cid"], "category": case["category"], "judge": case.get("judge"),
            "pass": bool(v["pass"]), "err": v.get("err", ""), "why": v.get("why", ""),
            "exemptions": list(v.get("exempted") or []),
            "actions_seen": [getattr(a, "action_type", "?") for a in actions],
            "payloads": [_payload_text(a.payload).strip()[:70] for a in actions],
            "mem_channel": chan,
            "n_ctx_msgs": len(state["context_messages"]),
            "ctx_chars": sum(len(str(m.get("content") or "")) for m in state["context_messages"]),
            # 逐跑的**输入指纹**（装配后整段 prompt 的 sha1 前 12 位）。没有它，"第 2、3 跑没过"
            # 就永远分不清是**输入变了**（污染）还是**模型输出抖了**（不稳）——10-10 那轮 15 条失败
            # 里 6 条是「首跑过、后两跑不过」，当时答不出这一问，30 次计费没换来结论。
            "prompt_fp": hashlib.sha1("\n".join(
                str(m.get("content") or "") for m in state["context_messages"]
            ).encode("utf-8")).hexdigest()[:12],
            "prompt_fp_stable": stable_fp(state["context_messages"]),
            "n_recalled": len(state.get("retrieved_memories") or []),
            "gold_in_recall": sum(1 for g in gold if _norm(g) in blob),
            "temperature": temp,
            "reasoning_level": EVAL_REASONING_LEVEL,
            "dates_seen": sorted({str((a.payload or {}).get("date") or "")[:10] for a in actions
                                  if isinstance(a.payload, dict) and (a.payload or {}).get("date")}),
            "reply_head": (text or "").replace("\n", " ")[:120],
            # **取证要留全文**（A27甲 同一条纪律）：第一轮只存了 120 字起头，于是
            # "模型到底走了哪条通道"这个关键问题再也对不上答案（10-05 就吃了这个亏）。
            "reply_text": (text or "").strip()[:900]}


async def _llm_usage_totals() -> dict:
    """从临时库 `llm_usage` 反查这轮落了库的用量（**只是交叉核对，不是"真打了几次"的那个数**）。

    10-05 试点实测把它当请求数会错方向：**发出去 10 个请求、表里只落 9 行**
    ——用量走 `spawn_background`（fire-and-forget），跑完立刻读就会少行。
    所以请求数以入口计数层（`install_llm_call_counter`）为准，这里用来对账 token 与模型名；
    两者不一致时必须把两个数都打出来，而不是只报好看的那个。
    """
    import sqlite3

    path = _sqlite_file(os.environ.get("DATABASE_URL") or "")
    out = {"rows": 0, "prompt_tokens": 0, "completion_tokens": 0, "models": []}
    if not os.path.isfile(path):
        return out
    c = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(llm_usage)")}
        if not cols:
            return out
        sel = ["COUNT(*)"]
        for k in ("prompt_tokens", "completion_tokens"):
            sel.append("COALESCE(SUM(%s),0)" % k if k in cols else "0")
        out["rows"], out["prompt_tokens"], out["completion_tokens"] = c.execute(
            "SELECT %s FROM llm_usage" % ",".join(sel)).fetchone()
        if "model" in cols:
            out["models"] = [r[0] for r in c.execute(
                "SELECT DISTINCT model FROM llm_usage ORDER BY 1")]
    except Exception as e:
        out["error"] = repr(e)[:120]
    finally:
        c.close()
    return out


def generate_summary(rows) -> dict:
    """生成侧读数的分母口径：**剔除「锚点过期没跑」的题**——没跑不等于没过。

    单独做成纯函数是为了能被直接测（分母怎么算的，不该只在跑起来之后才知道）。
    """
    scored = [r for r in rows if r.get("err") != "E_anchor_stale"]
    n = len(scored)
    # 多跑时 AR 读「**每一跑都过**」——三跑里蒙对一次就算分＝又回到不可复现的尺子；
    # 单跑（repeat=1）没写 pass_all，退回 pass。
    p = sum(1 for r in scored if r.get("pass_all", r.get("pass")))
    return {"n": n, "pass": p, "ar": round(100.0 * p / n, 1) if n else 0.0,
            "n_asked": len(rows), "n_skipped_stale": len(rows) - n,
            "by_err": {k: sum(1 for r in rows if r.get("err") == k)
                       for k in sorted({str(r.get("err") or "") for r in rows})}}


def install_llm_call_counter():
    """在统一入口外面套一层**计数透传**，返回 `{"n": …}`，配合 `uninstall_llm_call_counter` 还原。

    为什么不能只信 `llm_usage`：用量落库走 `spawn_background`（fire-and-forget），跑完立刻读会少行
    ——10-05 试点实测：**发了 10 个请求、只落了 9 行**（每一题都拿到了不同的回复文本，所以 10 次是真发出去了）。
    把"报表里的调用次数"绑在一个会丢行的异步表上，就是把我方基础设施的时序缺陷
    当成被测对象的行为差异去读——与「拿清洗列量原始内容＝假零」同族。
    装在入口上而不是数自己的循环：装配链里若长出第二个调用点，这个数会先于读数暴露它。
    """
    import app.agent.llm_client as lc

    orig = lc.chat_completion
    box = {"n": 0, "orig": orig, "last_messages": None}

    async def counted(messages, **kw):
        box["n"] += 1
        box["last_messages"] = messages      # 模型真正看到的那段 prompt（取证用，别只信装配返回值）
        return await orig(messages, **kw)
    lc.chat_completion = counted
    return box


def uninstall_llm_call_counter(box: dict) -> None:
    import app.agent.llm_client as lc

    if box.get("orig") is not None:
        lc.chat_completion = box["orig"]


def today_beijing() -> str:
    """装配里的「现在」取的就是这个（`section_world` 用 `datetime.now(beijing_tz)`）。"""
    from datetime import timezone
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def anchor_is_stale(case: dict) -> bool:
    """这道题带**日期判据**，而题面 `as_of` 已经不等于本机今天 ⇒ 读数不可信。

    为什么必须拦（10-06 00:33 实测踩到）：`time_anchor` 进不了 prompt，模型只能按它看到的
    「今天是 10 月 6 日」算"过三天"＝10-09；判据却按副本里的 as_of=10-05 要 10-08 ⇒ E4。
    **这是跑过了午夜、平移副本过期，不是模型算错**。把这种题混进 AR＝同一族假读数。
    """
    if not resolve_expect_date(case.get("time_anchor")):
        return False
    as_of = str((case.get("time_anchor") or {}).get("as_of") or "").strip()
    return bool(as_of) and as_of != today_beijing()


def _run_signature(row: dict) -> tuple:
    """一题一跑的结果指纹：三跑一致要比的是**结果**，不是文本相似度。

    `mem_channel` 必须在指纹里（10-06 补）：拍板「B＋A 各半」之后，`【记忆：】`落库通道与标记动作
    是**并列的产出**，漏掉它就等于"这题三跑只有一跑真的落了库、其余两跑光在正文里回应"也被判成一致
    ——而落库型题恰恰是要认证的那批。
    """
    return (bool(row.get("pass")), tuple(row.get("actions_seen") or ()),
            tuple(row.get("dates_seen") or ()), tuple(row.get("exemptions") or ()),
            tuple(_norm(str(x)) for x in (row.get("payloads") or ())),
            tuple(_norm(str(x)) for x in (row.get("mem_channel") or ())))


def sig_key(row: dict) -> tuple:
    """参与「三跑一致」比对的键＝指纹各维**先剥掉钟点**（10-10 拍板的口径）。

    为什么（用户当轮同意）：装配里那段 `## 当前时间` 是活时钟，三跑各隔几秒，模型就可能把
    "17:12／17:13"这类时间写进载荷或通道文本里——那是**时间流过了**，不是模型不稳。
    用原始文本比，一致率量的就是"跑了多久"。
    仍然安全的地方：`过／不过` 这一维**不剥**，日期算错会让那一跑直接不过（判据按题面锚点算），
    所以"剥钟点"不会把答错的三跑洗成一致。
    """
    return tuple(mask_clock(str(v)) for v in _run_signature(row))


def run_sig_text(row: dict) -> str:
    """把 `_run_signature` **逐字渲染成一行文本**（报表里留的就是这个串）。

    为什么单独做这个：10-06 凌晨第一次三跑之后，我用报表离线复算一致率得到 4/10，而尺子自己报 3/10
    ——差的那一题正是**没留底的维度**（逐跑豁免）。一致率这一列存在的理由就是"可复核"，
    留底复算不出来＝这列只能信代码、不能审计。所以每条留底都必须把**参与指纹的全部维度**打出来。
    """
    def seg(v):
        # 「｜」是指纹自己的分隔符；豁免条目原文里就带「｜」（"片段｜作废语境豁免：…"），
        # 不转义就会把 6 段撑成 7 段，离线复算按段切分当场错位。
        return str(v).replace("｜", "／")

    return "｜".join([seg(x) for x in [
        "过" if row.get("pass") else "不过",
        ",".join(row.get("actions_seen") or []) or "无动作",
        ",".join(row.get("dates_seen") or []) or "无日期",
        ",".join(row.get("exemptions") or []) or "无豁免",
        " ‖ ".join(_norm(str(x)) for x in (row.get("payloads") or ())) or "无载荷",
        " ‖ ".join(_norm(str(x)) for x in (row.get("mem_channel") or ())) or "无通道",
    ]])


def mask_clock(text: str) -> str:
    """把 prompt 里**随墙上时钟变**的片段换成占位符，用来算"同一份输入"。

    为什么必须剥（10-10 用 sha1 实测）：装配里那段 `## 当前时间`（context_builder.py:840）
    写的是本机活时钟，三跑各隔几秒 ⇒ **每题三跑的 prompt 全都不一样**（10/10 题、每题 3 种 fp）。
    不剥就直接比，"三跑一致"量的就不是模型稳不稳，而是"过了几秒钟"。
    """
    t = str(text or "")
    t = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日(\s*星期[一二三四五六日天])?", "<日>", t)
    t = re.sub(r"\d{4}-\d{2}-\d{2}", "<日>", t)
    t = re.sub(r"\d{1,2}月\d{1,2}日", "<日>", t)
    t = re.sub(r"\d{1,2}:\d{2}(:\d{2})?", "<钟>", t)
    t = re.sub(r"\d+\s*(分钟|小时|天前|前)", "<距>前", t)
    return t


def stable_fp(context_messages) -> str:
    """剥钟点后的整段 prompt 指纹＝判"这三跑吃的到底是不是同一份料"的那一维。"""
    return hashlib.sha1(mask_clock("\n".join(
        str(m.get("content") or "") for m in (context_messages or [])
    )).encode("utf-8")).hexdigest()[:12]


def fold_runs(runs: list) -> dict:
    """把同一题的多跑折成一行：AR 读「每一跑都过」，一致读「结果指纹全等」，第 2、3 跑**留底可复核**。

    单独做成纯函数不是为了好看——这段逻辑原本 inline 在 `run_generate_eval` 的循环里，而那个函数
    在 pytest 里根本跑不了（engine 按进程缓存，见本文件 `_init_temp_env`）。结果变异电池当场演示：
    把 `consistent` 硬写成 True、把 `other_runs` 折成空表，**两条守卫都照样绿**（10-06 实测 2 条没牙）。
    挪成纯函数之后它们才第一次真的被测到。
    """
    sigs = [sig_key(x) for x in runs]
    first = runs[0]
    first["n_runs"] = len(runs)
    first["pass_all"] = all(bool(x["pass"]) for x in runs)
    first["pass_any"] = any(bool(x["pass"]) for x in runs)
    first["consistent"] = len(set(sigs)) == 1
    first["err_spread"] = "/".join(sorted({str(x.get("err") or "") for x in runs}))
    first["other_runs"] = [{"pass": bool(x["pass"]), "err": x.get("err") or "",
                            "actions": ",".join(x["actions_seen"]) or "无",
                            "dates": ",".join(x.get("dates_seen") or []),
                            "chan": " ‖ ".join(x.get("mem_channel") or []) or "无",
                            "sig": run_sig_text(x),
                            "gold_ctx": x.get("gold_in_ctx"),
                            "ctx_chars": x.get("ctx_chars"),
                            "n_ctx_msgs": x.get("n_ctx_msgs"),
                            "prompt_fp": x.get("prompt_fp"),
                            "prompt_fp_stable": x.get("prompt_fp_stable"),
                            "payloads": " ‖ ".join(x["payloads"])[:60]}
                           for x in runs[1:]]
    first["sig"] = run_sig_text(runs[0])
    # 输入逐跑是否同一份＝漂移的**第一归因**，必须从留底里离线判得出来（不靠再跑一次花钱）。
    # 比的是**剥掉钟点后的稳定指纹**：装配里那段 `## 当前时间` 每次都不同，拿原始 fp 比会
    # 把"过了 3 秒"报成"污染"（10-10 实测 10/10 题三跑原始 fp 全不同，就是这么撞出来的）。
    fps = [str(x.get("prompt_fp_stable") or x.get("prompt_fp") or "") for x in runs]
    first["prompt_fp"] = str(runs[0].get("prompt_fp") or "")
    first["prompt_fp_stable"] = fps[0]
    first["fp_spread"] = sorted({f for f in fps if f})
    first["input_identical"] = all(fps) and len(set(fps)) == 1
    # 墙上时钟那一维单独留底：它不同**不是**污染，只是时间流过
    raw_fps = [str(x.get("prompt_fp") or "") for x in runs]
    first["clock_only_drift"] = (len({f for f in raw_fps if f}) > 1 and len({f for f in fps if f}) == 1)
    return first


def sig_recompute(row: dict):
    """**从留底重跑一遍一致判定**：报表里那三条指纹是否全等。

    返回 `None`＝这题没多跑（单跑档，本来就没有一致可言）；否则返回布尔，供 `recount` 档与
    报表里存的 `consistent` 对账——两者不符就说明"留底与算式脱钩"，那这一列就不可信。
    """
    extra = row.get("other_runs") or []
    if not extra or not row.get("sig"):
        return None
    # 与 `fold_runs` 同一口径：比对前剥钟点（留底本身仍是原文，可审计）
    sigs = [mask_clock(str(row["sig"]))] + [mask_clock(str(o.get("sig") or "")) for o in extra]
    if any(not s for s in sigs):
        return None
    return len(set(sigs)) == 1


def certified_j1(row: dict) -> bool:
    """J1 的认证判据＝10-06 拍板口径 `CERT_J1_RULE` 里生成侧那两条：**每一跑都过** ∧ **三跑一致**。

    检索侧四条另算（`--certify-j12` 那条零计费路 10-06 已跑：12/12 认证、泄题 0），
    这里只量生成侧——两半都成立才算这道题过了 §S3 的认证，报表把两半分开写，不混成一个数。
    """
    return bool(row.get("pass_all")) and bool(row.get("consistent", True))


def rows_from_jsonl(path: str) -> list:
    """读回 `--emit-rows` 落盘的行，**按 cid 去重、后来者覆盖**。

    为什么要去重：分块跑最大的诱惑是"死一块重跑一块"，而同一题被重跑就会在文件里留下两行 ⇒
    直接算就把分母撑大（10 题读成 11 题），AR 与一致率一起被灌水。后来者覆盖＝以最后一次为准，
    这也是"重跑是补测不是加测"的口径。
    """
    by_cid = {}
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            r = json.loads(ln)
            by_cid[r["cid"]] = r
    return list(by_cid.values())


def recount_rep(rows: list) -> dict:
    """把 `--emit-rows` 落盘的行重建成报表数据（**零计费**，也是分块跑法的合并步）。

    顺手做两件事：① 按留底重算一致并与存的 `consistent` 对账（不符就点名，不静默）；
    ② 按拍板口径给出「本轮生成侧认证 X／N」。
    """
    s = generate_summary(rows)
    mism = [r["cid"] for r in rows
            if sig_recompute(r) is not None and sig_recompute(r) != bool(r.get("consistent"))]
    return {"rows": rows, **s,
            "k": (rows[0].get("k") if rows else None) or 5,
            "flags": (rows[0].get("flags") if rows else None) or {},
            "borrowed": (rows[0].get("borrowed") if rows else None) or {},
            "llm": (rows[0].get("llm") if rows else None) or {},
            "usage": {"requests_sent": sum(1 + len(r.get("other_runs") or []) for r in rows),
                      "rows": None, "prompt_tokens": None, "completion_tokens": None,
                      "models": None},
            "skipped_judges": ["J2"],
            "sig_mismatch": mism,
            "n_certified_j1": sum(1 for r in rows if certified_j1(r)),
            "recount": True}


_GEN_ROUNDS_IN_PROC = []


async def run_generate_eval(cases, *, user_id=GENERATE_USER_ID, limit=0, k=5,
                            temperature=EVAL_TEMPERATURE, filler_target=FILLER_TARGET_DEFAULT,
                            flags=None, repeat=GENERATE_REPEAT_DEFAULT, only_cid=""):
    """只跑声明 judge=J1 的题；`repeat`＝§S3 的「生成三跑一致」（J2 要写侧抽取＝另一次计费，不在本轮）。

    `only_cid`＝**按题分块跑**（10-06 上午加的，不是性能优化而是**止损**）：本机一次 python 进程活不过
    几分钟，而这条路每一步都在计费 ⇒ 一整个 30 次的长跑死在中途就是"花了钱、什么都没量到、还得重花一遍"。
    一题一个进程（3 次计费）＋ `--emit-rows` 落盘，最坏只丢一题的钱。
    """
    if _GEN_ROUNDS_IN_PROC:
        raise RuntimeError(
            "一个进程只准跑一轮生成评测：`app/db/database.py` 的 engine 按进程缓存，第二轮的 init_db "
            "会把表建进第一轮已被 rmtree 的临时库，报出来是「临时库没有表 api_configs」，"
            "看着像解析链坏了、其实是进程复用。要跑第二轮请另起进程。")
    _GEN_ROUNDS_IN_PROC.append(1)
    todo = [c for c in cases if c.get("judge") == "J1"]
    if only_cid:
        todo = [c for c in todo if c.get("cid") == only_cid]
    if limit:
        todo = todo[:limit]
    prod_path = prod_database_path()          # 必须在 _init_temp_env 改写 DATABASE_URL **之前**取
    tmp = tempfile.mkdtemp(prefix="ambrace_memact_gen_")
    rows, borrowed, identity = [], {}, {}
    counter = None
    try:
        await _init_temp_env(tmp)
        borrowed = borrow_llm_config(prod_path, os.path.join(tmp, "eval.db"))
        identity = resolved_llm_identity(user_id)
        counter = install_llm_call_counter()
        flags = dict(BASELINE_FLAGS, **(flags or {}))
        for i, c in enumerate(todo):
            if anchor_is_stale(c):
                # **不花这笔钱**：日期判据的题在锚点过期时跑出来必然是假读数，计费跑它＝花钱买噪声
                rows.append({"cid": c["cid"], "category": c.get("category"), "judge": "J1",
                             "pass": False, "err": "E_anchor_stale",
                             "why": "题面 as_of=%s ≠ 本机今天 %s ⇒ 日期判据不可信（重跑 output 下的平移副本）"
                                    % (c.get("time_anchor", {}).get("as_of"), today_beijing()),
                             "exemptions": [], "actions_seen": [], "payloads": [], "mem_channel": [],
                             "n_ctx_msgs": 0, "ctx_chars": 0, "n_recalled": 0, "gold_in_recall": 0,
                             "gold_in_ctx": 0, "temperature": None,
                             "reasoning_level": EVAL_REASONING_LEVEL, "library_rows": 0,
                             "dates_seen": [], "reply_head": "", "reply_text": ""})
                continue
            n_rep = max(1, int(repeat))
            try:
                runs = []
                for rep_i in range(n_rep):
                    # **每一跑独占一个角色号段与一份库**（10-06 中午实测出来的订正，不是洁癖）：
                    # 共用 cid＋共用库时，第 1 跑会把"这些记忆已经展示过"写进持久状态，
                    # 于是第 2 跑丢记忆、第 3 跑整段「和你相关的记忆」渲染成「暂无」——
                    # 这样三跑量的是"第 1 跑把状态改成了什么"，不是"模型稳不稳"。
                    # 同族错＝10-04 那条「多配置连跑时角色号段必须按配置×用例独占」，我在 repeat 上又犯了一遍。
                    cid, _seed_ids, _dis, n_fil, _skip = await seed_library(
                        c, i * n_rep + rep_i, user_id, filler_target=filler_target)
                    r = await generate_case(c, cid, user_id, temperature=temperature, flags=flags)
                    r["library_rows"] = len(c["seeds"]) + len(c.get("distractors") or []) + n_fil
                    # gold 是否**真的进了那段 prompt**：从入口计数层抓到的 messages 里核，
                    # 而不是核装配返回值——两者不一致时（插件改过 messages）以模型看到的为准。
                    seen = counter["last_messages"] or []
                    pblob = _norm(" ".join(str(x.get("content") or "")
                                           for x in seen if isinstance(x, dict)))
                    r["gold_in_ctx"] = sum(
                        1 for g in [c["seeds"][j]["content"] for j in
                                    (c["expect"].get("gold_seed_idx") or [])] if _norm(g) in pblob)
                    runs.append(r)
                if len(runs) > 1:
                    # §S3 那句「J1/J2 还要多一列生成三跑一致，否则又是一把不可复现的尺子」的落地。
                    rows.append(fold_runs(runs))
                else:
                    rows.append(runs[0])
            except Exception as e:
                rows.append({"cid": c["cid"], "category": c.get("category"), "judge": "J1",
                             "pass": False, "err": "E_gen_failed", "why": repr(e)[:200],
                             "exemptions": [],
                             "actions_seen": [], "payloads": [], "mem_channel": [],
                             "n_ctx_msgs": 0, "ctx_chars": 0,
                             "n_recalled": 0, "gold_in_recall": 0, "gold_in_ctx": 0,
                             "temperature": None,
                             "reasoning_level": EVAL_REASONING_LEVEL, "library_rows": 0,
                             "dates_seen": [], "reply_head": "", "reply_text": ""})
        await asyncio.sleep(1.5)              # 给 fire-and-forget 的用量落库一点时间（仍可能少于请求数）
        usage = await _llm_usage_totals()
        usage["requests_sent"] = counter["n"]
        n = len(rows)
        # 分母口径交给纯函数（可单测）：**剔除锚点过期没跑的题**，没跑≠没过
        return {"rows": rows, **generate_summary(rows),
                "borrowed": borrowed, "llm": identity, "usage": usage,
                "flags": flags, "k": k, "user_id": user_id,
                "skipped_judges": sorted({c.get("judge") for c in cases if c.get("judge") != "J1"})}
    finally:
        if counter:
            uninstall_llm_call_counter(counter)
        shutil.rmtree(tmp, ignore_errors=True)


async def run_eval(cases, *, judges, configs, k, mode, sparse_only, limit,
                   filler_target=FILLER_TARGET_DEFAULT, cert_j12=False):
    todo = [c for c in cases if c.get("judge") in judges]
    if limit:
        todo = todo[:limit]
    rep = {"cases_run": len(todo), "cases_total": len(cases), "semantic": not sparse_only,
           "skipped_judges": sorted({c.get("judge") for c in cases} - set(judges)),
           "mode": mode, "k": k, "filler_target": int(filler_target), "configs": {},
           "cert_j12": bool(cert_j12)}
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
                                         flags=flags, cid_base=cid_base, filler_target=filler_target)
                    c["solvability"] = {**c.get("solvability", {}), **out["solvability"]}
                    out["row"]["err"] = classify_error(out["row"])
                    rows.append(out["row"])
                else:
                    rows.append(await score_case(c, i, user_id=user_id, k=k, sparse_only=sparse_only,
                                     flags=flags, cid_base=cid_base, filler_target=filler_target))
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
    if rep.get("cert_j12"):
        L.append("- **本轮是 `--certify-j12`：只出 §S3 四条检索侧认证**。下面这些 AR／E1／E2 列对 J1/J2 的题"
                 "**不是分数**（它们的判据在生成侧，见 `--mode generate`）；分数请看 solvability 落盘文件与生成档报表。")
    if rep.get("skipped_judges"):
        L.append("- 本轮**未跑**判据：%s（J1/J2＝生成层：本模式（%s）不碰模型；要跑生成请 `--mode generate --allow-llm`）"
                 % (",".join(rep["skipped_judges"]), mode))
    L += ["", "AR＝gold 进 top-k 且排在任何干扰项之前（对账口径）；**AR_strict**＝方案 §二 字面契约（固定 k 内零干扰项，窄库上近乎恒假）；**AR_gold**＝top-|gold| 全是 gold（连中性行挤占也判失败，**门禁锚这列**）。三口径**可证不等价**（含反例），见测试 `test_三种口径的强弱关系与反例`。",
          "- **主分母＝已认证子集**（§2.3 认证三跑过的题）；未认证的题留在集里只跟踪趋势，不参与基线定义。AR_cert 与 AR(全量) 并列输出，不合并。"
          "**门禁一律读「认证子集 × AR_gold」**：名次口径在 M0 的小库上实测**语义路饱和**（97.9～100.0、关旗标反而满分），"
          "而同一个库在**确定性路**只有 40.4 ⇒ 饱和是「语义路 × 10 行小库」的产物，不是名次口径本身可用。",
          "- 库规模：每例均 **%s 行**（M1 加难＝把 10 行小库抬到真实量级，填充目标 %s 条/例）。"
          "认证门槛仍用**名次口径**（题目合法性），`full_gold3` 只记录不作门槛——把它当门槛会让「认证子集 × AR_gold」自我循环。" % (
              (next(iter(rep["configs"].values()))["rows_mean"] if rep["configs"] else "—"),
              rep.get("filler_target")),
          "", "| 配置 | 条数 | 库行数 | 已认证 | 认证率％ | AR_gold(认证)％·门禁 | AR_cert名次％ | AR(全量)％ | AR_strict％ | AR_gold％ | E1 | E2 | 泄题嫌疑 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cn, d in sorted(rep["configs"].items()):
        e1 = sum(1 for r in d["rows"] if r["missing"])
        e2 = sum(1 for r in d["rows"] if r["polluted"])
        leak = sum(1 for r in d["rows"] if r.get("solvability", {}).get("no_mem_fail") is False)
        L.append("| %s | %d | %s | %d | %s | %s | %s | %s | %s | %s | %d | %d | %d |" % (
            cn, d["n"], d["rows_mean"], d["n_certified"], d["cert_rate"], d["ar_gold_certified"],
            d["ar_certified"], d["ar"], d["ar_strict"], d["ar_gold"], e1, e2, leak))
    for cn, d in sorted(rep["configs"].items()):
        L += ["", "## 配置 %s · 分类" % cn,
              "| 类别 | 条数 | 认证 | AR(%) | AR_strict(%) | AR_gold(%) | 备注 |",
              "|---|---|---|---|---|---|---|"]
        for cat, v in sorted(d["by_category"].items()):
            note = "" if v["n_certified"] >= 6 else "⚠ 认证子集仅 %d 条，该类分类型读数只能当趋势看" % v["n_certified"]
            L.append("| %s | %d | %d | %s | %s | %s | %s |" % (
                cat, v["n"], v["n_certified"], v["ar"], v["ar_strict"], v["ar_gold"], note))
        L += ["", "## 配置 %s · 逐用例" % cn,
              "| cid | 类别 | 判据 | PASS | 未命中 | 污染 | 召回数 |", "|---|---|---|---|---|---|---|"]
        for r in d["rows"]:
            L.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                r["cid"], r["category"], r["judge"], r["pass"],
                ",".join(map(str, r["missing"])) or "—",
                ",".join(map(str, r["polluted"])) or "—", r.get("n_recalled", "")))
    return "\n".join(L)


def render_generate(rep) -> str:
    """生成侧报表：**打了多少次、烧了多少 token、装配有多长**必须和分数并排出来。

    少了这几行，"10 题里过了 6 题"就说不清是在什么配置、什么库规模、真网络还是打桩下得到的。
    """
    u, llm, srv = rep.get("usage") or {}, rep.get("llm") or {}, (rep.get("llm") or {}).get("server") or {}
    n_runs = max([int(r.get("n_runs") or 1) for r in rep["rows"]] or [1])
    n_consistent = sum(1 for r in rep["rows"] if r.get("consistent", True))
    n_cert = sum(1 for r in rep["rows"] if certified_j1(r))
    L = ["# 动作式记忆评测 · 生成侧试点（A30 S1）", ""]
    L += ["- 题数 **%d**（只跑 judge=J1；本轮**未跑**判据 %s＝J2 要过写侧抽取，是另一次计费）%s" % (
        rep["n"], ",".join(rep.get("skipped_judges") or []) or "无",
        "；另有 **%d 题因锚点过期没跑、也没计费**（分母已剔除，别把它读成「没过」）" % rep["n_skipped_stale"]
        if rep.get("n_skipped_stale") else ""),
        "- J1 通过 **%d／%d ＝ %s%%**；错因分布 %s%s" % (
            rep["pass"], rep["n"], rep["ar"], json.dumps(rep["by_err"], ensure_ascii=False),
            ("；**每题 %d 跑**，其中「结果完全一致」%d／%d、「每一跑都过」%d／%d"
             "（AR 读后者）%s%s" % (
                 n_runs, n_consistent, rep["n"], rep["pass"], rep["n"],
                 "；按 10-06 拍板口径（**每一跑都过 ∧ 三跑一致**）⇒ 生成侧认证 **%d／%d**"
                 "（检索侧四条另算，见 `--certify-j12`；两半都过才叫 %s）" % (n_cert, rep["n"], CERT_J1_RULE),
                 "；**不一致的题：%s**（逐跑明细见下面那张「第 2、3 跑」表，"
                 "别只盯着分子分母）" % "、".join(
                     str(r["cid"]) for r in rep["rows"] if not r.get("consistent", True))
                 if n_consistent < rep["n"] else "")) if n_runs > 1 else ""),
        "- 端点（**脱敏**，只出模型与 base_url 主机，不出 key）：model=%s provider=%s base_url=%s enabled=%s key_len=%s" % (
            srv.get("model"), srv.get("provider"), srv.get("base_url"),
            srv.get("enabled"), srv.get("key_len")),
        "- 配置表复制（生产库只读 → 临时库）：%s" % json.dumps(rep.get("borrowed") or {}, ensure_ascii=False),
        "- **计费自证**：入口计数（真发出去的请求）=%s ｜ 临时库 `llm_usage` 落库行数=%s"
        "（用量走 fire-and-forget，**落库行数可能少于请求数**，10-05 试点实测 10/9 ⇒ 只报行号会把基础设施的时序缺陷读成被测行为）"
        "｜ prompt=%s completion=%s models=%s" % (
            u.get("requests_sent"), u.get("rows"), u.get("prompt_tokens"),
            u.get("completion_tokens"), u.get("models")),
        "- 偏离生产处（记账）：reasoning_level=%s；temperature 逐题见表；"
        "旗标＝生产实配 %s" % (EVAL_REASONING_LEVEL, json.dumps(rep.get("flags") or {}, ensure_ascii=False)),
        "- **装配里的「现在」＝本机时钟**（`section_world._compute_current_time_str` 用 `datetime.now(beijing_tz)`，"
        "题面 `time_anchor` 进不去）⇒ 带日期判据的题必须把 `as_of` 对齐到跑分当天再跑，"
        "否则日期那半判据量的是尺子不是模型（10-05 试点用 output 下的平移副本）。",
        "", "| cid | 类别 | PASS | 错因 | 判据说明 | 产出动作 | 动作载荷 | 动作里的日期 | **记忆通道【记忆：】** | 召回数 | gold 进召回 | gold 进 prompt | 库行数 | ctx 条数 | ctx 字数 | temp | 回复起头 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["cid"], r.get("category"), "✓" if r["pass"] else "✗", r.get("err") or "—",
            (r.get("why") or "—").replace("|", "／")[:60],
            ",".join(r.get("actions_seen") or []) or "无",
            " ‖ ".join(r.get("payloads") or []) or "—",
            ",".join(r.get("dates_seen") or []) or "—",
            " ‖ ".join(r.get("mem_channel") or []) or "无",
            r.get("n_recalled"), r.get("gold_in_recall"), r.get("gold_in_ctx"),
            r.get("library_rows"),
            r.get("n_ctx_msgs"), r.get("ctx_chars"), r.get("temperature"),
            (r.get("reply_head") or "").replace("|", "／").replace("\n", " ")[:70]))
    n_mem = sum(1 for r in rep["rows"] if r.get("mem_channel"))
    n_noact = sum(1 for r in rep["rows"] if not r.get("actions_seen"))
    exemptions = [(r["cid"], x) for r in rep["rows"] for x in (r.get("exemptions") or [])]
    L += ["", "## 逐题回复全文（取证留底）", ""]
    for r in rep["rows"]:
        L.append("- **%s**（%s／pass=%s／err=%s）：%s" % (
            r["cid"], r.get("category"), r["pass"], r.get("err") or "—",
            (r.get("reply_text") or "").replace("\n", " ")))
    L += ["", "## 读数怎么解释（别把两件事混成一件）",
          "- 有 %d／%d 题**一个动作标记都没产出**，但其中 %d 题走了 `【记忆：…】` 通道（生产里这条由 "
          "`parse_response` 落进记忆库，不是 `parse_actions` 的动作标记）。"
          "**判据只看动作标记时，这些题会被记成 E3＝没用记忆去做事**，而实际是"
          "「用对了另一条通道」或「按 system prompt 的『同一内容别重复』选择不重复记」。"
          "⇒ 本轮 AR 不能直接当「记忆没被用上」读；口径要么改题面（待记事实别同时放进种子），"
          "要么把通道写进判据（`expect.action_type: [\"MEMO\", \"MEMORY\"]`＝任一即达成）。"
          "**10-05 拍板（B＋A 各半）后两者都已实现**：MEMORY 是 `J1_CHANNEL_TYPES` 里的通道值，"
          "不是 `parse_actions` 的动作类型；日程型题**不收** MEMORY（写了记忆也不会提醒人），"
          "落库型题两条通道任一达成即算过。**改尺子留痕**：`NONE` 现在把 `【记忆：】` 也算作"
          "\"不该产出却产出了\"，判得更严而不是更松。"
          % (n_noact, rep["n"], n_mem)]
    extra = [(r["cid"], o) for r in rep["rows"] for o in (r.get("other_runs") or [])]
    if extra:
        L += ["", "## 第 2、3 跑分别产出了什么（一致率必须可复核）", "",
              "- 每题先打**首跑指纹**，再打第 2、3 跑；**指纹＝参与判一致的全部维度**"
              "（过没过｜动作｜日期｜豁免｜载荷｜通道，文本已 `_norm`）。"
              "三跑指纹全等 ⇔ `consistent=True` ⇒ 这一列可以**从报表离线复算**，不需要信代码"
              "（10-06 第一次三跑时豁免没留底，离线复算比尺子多数 1 题，就是这么发现的）。",
              ""]
        by_cid = {}
        for r in rep["rows"]:
            if r.get("other_runs"):
                by_cid[r["cid"]] = r
        for cid in sorted(by_cid):
            r = by_cid[cid]
            L.append("- **%s**（%s／一致=%s／每跑都过=%s）" % (
                cid, r["category"], r.get("consistent"), r.get("pass_all")))
            L.append("    - 首跑 指纹 `%s`（gold 进 prompt=%s‖输入 %s／%s 字）" % (
                r.get("sig") or "—", r.get("gold_in_ctx"),
                r.get("prompt_fp") or "没留fp", r.get("ctx_chars")))
            for i, o in enumerate(r["other_runs"], start=2):
                L.append("    - 第 %d 跑 指纹 `%s`（err=%s‖gold 进 prompt=%s‖输入 %s／%s 字）" % (
                    i, o.get("sig") or "—", o["err"] or "—", o.get("gold_ctx"),
                    o.get("prompt_fp") or "没留fp", o.get("ctx_chars")))
            if r.get("clock_only_drift"):
                L.append("    - （三跑只差墙上时钟，剥掉钟点后输入同一份＝不算污染）")
            if r.get("input_identical") and not r.get("pass_all"):
                L.append("    - ⇒ 三跑输入**同一份**（sha1 相同）却没每跑都过＝**输出抖动**，"
                         "不是检索/装配退化；该改题面或改提示词，重跑只是再花一次钱。")
            elif r.get("fp_spread") and not r.get("input_identical", True):
                L.append("    - ⇒ 三跑输入**不是同一份**（fp=%s）＝污染，"
                         "这一题的过/不过都不能当模型结论读。" % ",".join(r["fp_spread"]))
    L += ["", "## `forbidden` 作废语境豁免（可数、不静默放行）",
          "- 本轮豁免 **%d 处**。口径＝禁用片段只出现在**含作废词的同一短句**内才豁免"
          "（跨短句不豁免，见 `_forbidden_check`）；作废词表＝%s" % (
              len(exemptions), "、".join(FORBIDDEN_RETIRE_WORDS)),
          "- 为什么要豁免：supersede 类的正确写法就是「已搬离城北老小区，现居城西」——"
          "旧值出现在「声明它已作废」的半句里；见片段就判红＝把「处理对了」读成「用错了记忆」。"]
    for _cid, _x in exemptions:
        L.append("  - %s：%s" % (_cid, str(_x).replace("|", "／")))
    return "\n".join(L)


def metrics_json(rep):
    """机器可读：排序＋无时间戳 ⇒ 同一提交连跑两次必须逐字节一致（§4.5 第一半）。"""
    slim = {"cases_run": rep["cases_run"], "cases_total": rep["cases_total"],
            "semantic": rep["semantic"], "mode": rep["mode"], "k": rep["k"],
            "filler_target": rep.get("filler_target"),
            "skipped_judges": rep.get("skipped_judges", []),
            "configs": {cn: {"n": d["n"], "ar": d["ar"], "rows_mean": d["rows_mean"],
                             "cert_rate": d["cert_rate"],
                             "n_certified": d["n_certified"], "ar_certified": d["ar_certified"],
                             "ar_gold_certified": d["ar_gold_certified"],
                             "n_uncertified": d["n_uncertified"], "ar_uncertified": d["ar_uncertified"],
                             "by_category": d["by_category"],
                             "rows": [{kk: r.get(kk) for kk in ("cid", "category", "judge", "certified",
                                                                "n_rows", "pass", "pass_strict", "pass_gold",
                                                                "missing", "polluted")}
                                      for r in d["rows"]]}
                       for cn, d in sorted(rep["configs"].items())}}
    return json.dumps(slim, ensure_ascii=False, sort_keys=True, indent=1)


def refusal(judges, mode: str, allow_llm: bool, cert_j12: bool = False,
            emit_solvability: str = "", fail_below=None, rows_path: str = "") -> str:
    """三道闸（返回 ''＝放行）。抽成纯函数是为了**能被逐条打假**：授权与否、尺子对否、计费范围对否。

    顺序不能换：先问「有没有授权」，再问「模式对不对」，最后问「授权范围够不够」。

    **第四个入口 `--certify-j12`（10-06 加，因为它把"认证"和"跑分"这两件事拆开了）**：
    任务书 §S3 的四条认证（`no_mem_fail`／`full_rank3`／`full_gold3`／`distractor_no_gold`）是
    **检索侧**的，J1/J2 的题也带 seeds/distractors/gold_seed_idx ⇒ 这四条**零计费就能算**。
    以前这道闸会把它一起拦掉（因为 certify 档不给 J1/J2 出分）。现在的口径：
    允许跑，但① 必须 `--mode certify`、② 必须落 `--emit-solvability`（否则报表里那几列 AR
    会被当成分数读——这正是原闸要拦的错）、③ 不许带 `--fail-below`（没有可用的分可门）。
    """
    js = {str(j).strip().upper() for j in (judges or [])}
    if mode == "recount":
        # 合并步：只吃 `--emit-rows` 落盘的行，**不碰模型** ⇒ 不该被计费闸拦，也不能被拿去做别的事。
        if not rows_path:
            return "--mode recount 必须配 --rows <jsonl>（它只重算 --emit-rows 落下来的行，零计费、不碰模型）。"
        if js != {"J1"}:
            return "--mode recount 只重算生成侧 J1 的落盘行（--judges J1）；检索侧四条认证请走 --certify-j12。"
        return ""
    wants_gen = bool({"J1", "J2"} & js)
    if wants_gen and cert_j12:
        if mode != "certify":
            return ("--certify-j12 只在 --mode certify 下有定义（它跑的是检索侧四条认证，"
                    "generate 档做的是另一件事：生成三跑一致）。")
        if not emit_solvability:
            return ("--certify-j12 必须配 --emit-solvability <路径>：不落盘的话输出里仍带着 AR 列，"
                    "而 J1/J2 的题在检索档里那几列**不是分数**，会被读错。")
        if fail_below is not None:
            return "--certify-j12 不许配 --fail-below：这条路上没有可门禁的分（判据在生成侧）。"
        return ""
    if wants_gen and not allow_llm:
        return ("J1/J2 需要生成 ⇒ 云端计费端点。方案把它们排在 M1；确要跑请显式 --allow-llm。"
                "（若只要 §S3 那四条检索侧认证，用 --certify-j12，零计费。）")
    if wants_gen and mode != "generate":
        # 关掉「授权了就照跑」这条静默错路：判分函数已就位，但**只有 --mode generate 才接了生成侧** ⇒
        # 其余模式下声明为 J1/J2 的用例会走 J3 检索档，**分数看着有、尺子其实是错的**。
        return ("J1/J2 只在 --mode generate 下有对应实现（score/certify 是 J3 检索档，拿它们跑 J1/J2＝拿错尺子）。")
    if mode == "generate" and js - {"J1"}:
        # 生成侧本轮只接了 J1：J2 要写侧抽取（reflect/save_memory 那条路，另一次计费、口径未定），
        # J3/J4 是检索档、不需要生成。授权了也不许顺带跑＝计费范围必须和口径一一对应。
        return ("--mode generate 本轮只实现 J1（J2 需写侧抽取＝另一次计费且口径未定；J3/J4 属检索档）。请 --judges J1。")
    return ""


async def main():
    ap = argparse.ArgumentParser(description="动作式记忆评测（M0-a：J3 ＋ 三跑认证）")
    ap.add_argument("--dataset", default="", help="题面 jsonl（--mode recount 不需要，它只读 --rows）")
    ap.add_argument("--judges", default="J3")
    ap.add_argument("--configs", default="baseline")
    ap.add_argument("--mode", choices=["score", "certify", "generate", "recount"], default="certify",
                    help="recount＝只重算 --emit-rows 落盘的行（零计费，也是按题分块跑完后的合并步）")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-cid", default="",
                    help="生成侧只跑这一题（按题分块＝一台会被砍 python 的机器上的止损做法）")
    ap.add_argument("--emit-rows", default="",
                    help="生成侧把逐题行（含逐跑指纹）追加落盘成 jsonl，供 --mode recount 离线合并复算")
    ap.add_argument("--rows", default="", help="--mode recount 的输入 jsonl（--emit-rows 写出来的那种）")
    ap.add_argument("--temperature", type=float, default=EVAL_TEMPERATURE,
                    help="生成侧钉死温度（不给＝读 state['temperature']，与生产同一行）")
    ap.add_argument("--fillers", type=int, default=FILLER_TARGET_DEFAULT,
                    help="每例库规模填充到多少行（0＝退回 M0 的 10 行库，仅用于 A/B 对照饱和是不是规模造成的）")
    ap.add_argument("--sparse-only", action="store_true", help="只走确定性路（CI／假向量环境）")
    ap.add_argument("--allow-llm", action="store_true", help="放行 J1/J2（计费端点，须用户授权）")
    ap.add_argument("--repeat", type=int, default=GENERATE_REPEAT_DEFAULT,
                    help="生成侧每题跑几遍（§S3『生成三跑一致』＝3）；计费＝题数×遍数，先看清再跑")
    ap.add_argument("--certify-j12", action="store_true",
                    help="只给 J1/J2 的题算 §S3 那四条**检索侧**认证（零计费）；必须配 --emit-solvability，"
                         "且此时报表里的 AR 列不是分数（判据在生成侧）")
    ap.add_argument("--out", default="memory_action_report.md")
    ap.add_argument("--emit-solvability", default="", help="把认证结果写成 jsonl（回填数据集用，M0-b）")
    ap.add_argument("--print-metrics", action="store_true")
    ap.add_argument("--fail-below", type=float, default=None)
    a = ap.parse_args()

    judges = [x.strip().upper() for x in a.judges.split(",") if x.strip()]
    why = refusal(judges, a.mode, a.allow_llm, cert_j12=a.certify_j12,
                  emit_solvability=a.emit_solvability, fail_below=a.fail_below, rows_path=a.rows)
    if why:
        print("[拒绝] " + why, file=sys.stderr)
        return 2
    if a.mode != "recount" and not a.dataset:
        # `--dataset` 原来是 argparse 的 required，为了让 `--mode recount` 能只读落盘行才放开，
        # 所以这里必须把它顶回来——否则缺题面会一路走到 `open("")` 报一个看不懂的错。
        print("[拒绝] --dataset 必填（只有 --mode recount 不吃题面，它只读 --emit-rows 落下来的行）",
              file=sys.stderr)
        return 2
    if a.mode == "recount":
        # 复算档排在读题面**之前**：它既不建临时库、也不装配、也不碰模型——整条路上一次计费都没有，
        # 这就是分块跑完之后"把 30 次的结果再看一遍而不再花钱"的那一步。
        rows = rows_from_jsonl(a.rows)
        rep = recount_rep(rows)
        text = render_generate(rep)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(text)
        if rep["sig_mismatch"]:
            print("[留底与算式不符] %s ⇒ 这些题的「一致」没法离线复算，先查 fold_runs/run_sig_text，"
                  "别把这张表当认证结果用" % "、".join(rep["sig_mismatch"]), file=sys.stderr)
            return 6
        print("[recount] 零计费复算完成：%d 题；按留底重跑的一致判定与报表存值全部相符" % rep["n"])
        return 0
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
    if a.mode == "generate":
        # **先报账再花钱**：这条路上每次请求都计费，"跑了多少题"要能在第一发之前看到，
        # 跑完之后才发现跑成了 3 倍是来不及撤的。
        n_j1 = sum(1 for c in cases if c.get("judge") == "J1"
                   and (not a.only_cid or c.get("cid") == a.only_cid))
        n_bill = min(n_j1, a.limit) if a.limit else n_j1
        print("[计费预告] %d 题 × %d 跑 ＝ **最多** %d 次请求"
              "（锚点过期的题在发之前就被跳过、不计费，所以实际可能更少）%s" % (
                  n_bill, max(1, a.repeat), n_bill * max(1, a.repeat),
                  "｜本轮只跑 %s" % a.only_cid if a.only_cid else ""), file=sys.stderr)
        rep = await run_generate_eval(cases, limit=a.limit, k=a.k, temperature=a.temperature,
                                      filler_target=a.fillers, repeat=a.repeat,
                                      only_cid=a.only_cid)
        text = render_generate(rep)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        if a.emit_rows:
            # 逐题行**追加**落盘（含逐跑指纹）＝分块跑法的合并凭证：`--mode recount` 用它离线复算，
            # 死一块只需重跑那一块，不必把整轮的 30 次再花一遍。
            ctx = {kk: rep.get(kk) for kk in ("flags", "borrowed", "llm", "k") if rep.get(kk)}
            with open(a.emit_rows, "a", encoding="utf-8") as f:
                for r in rep["rows"]:
                    f.write(json.dumps(dict(r, **ctx), ensure_ascii=False) + "\n")
        print(text)
        if a.print_metrics:
            slim = {kk: rep.get(kk) for kk in ("n", "pass", "ar", "by_err", "borrowed", "llm",
                                                "usage", "flags", "skipped_judges")}
            slim["rows"] = [{kk: r.get(kk) for kk in ("cid", "judge", "pass", "err", "actions_seen",
                                                      "n_recalled", "gold_in_recall", "ctx_chars",
                                                      "temperature", "library_rows")}
                            for r in rep["rows"]]
            print("METRICS_JSON_BEGIN")
            print(json.dumps(slim, ensure_ascii=False, sort_keys=True, indent=1))
            print("METRICS_JSON_END")
        # 生成失败（网络/配置）不是"这题没过"，是**这轮没量到东西** ⇒ 用独立退出码喊出来，别和 0 混
        ncrash = sum(1 for r in rep["rows"] if r["err"] == "E_gen_failed")
        if ncrash:
            print("[生成失败] %d/%d 题根本没跑到（先看端点与网络，再谈分数）" % (ncrash, rep["n"]),
                  file=sys.stderr)
            return 5
        return 0
    rep = await run_eval(cases, judges=judges, configs=configs, k=a.k, mode=a.mode,
                         sparse_only=a.sparse_only, limit=a.limit, filler_target=a.fillers,
                         cert_j12=a.certify_j12)
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
        # 门禁＝**主分母 × 有分辨力的判据**：认证子集上的 AR_gold（10-04 实测名次口径在认证子集饱和
        # 97.9～100.0、关旗标反而满分；AR_gold 才有 32pp 余量）。没有认证标记时退回全量 AR_gold。
        vals = [d["ar_gold_certified"] if d["ar_gold_certified"] is not None else d["ar_gold"]
                for d in rep["configs"].values()]
        worst = min(vals, default=0.0)
        if worst < a.fail_below:
            print("[门禁] 认证子集 AR_gold %s < --fail-below %s（n_cert=%s，configs=%s）" % (
                worst, a.fail_below, [d["n_certified"] for d in rep["configs"].values()],
                sorted(rep["configs"])), file=sys.stderr)
            return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
