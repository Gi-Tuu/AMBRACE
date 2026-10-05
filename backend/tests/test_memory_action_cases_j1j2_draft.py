"""A30 出题草稿的**机械审查**（不是人肉看一遍）——`scripts/diagnostics/memory_action_cases_j1j2_draft.jsonl`。

为什么要有这个文件：2026-10-05 我第一次出这 12 条时，人肉写完自检过一遍"看起来没问题"，
机械核出来 4 处错（offset 把周五+2 当成周三、TIMER 的日期根本判不了、用了不存在的槽名、
造了正式集没有的 memory_type）。**"我检查过了"不是检查**，能重复跑的断言才是。

七条断言：
1. 草稿必须过跑分器 lint（题面禁提示词、干扰项、锚点等）；
2. J2 槽名必须在 `MUTABLE_SLOTS` 派生出的真实槽全集里（不存在的槽＝永不命中的空齿）；
3. J1 的 `field_match` 片段必须真的出现在某条 seed 里（否则模型做对了也判不过）；
4. `forbidden` 片段不许出现在"该被引用的那条 seed"里（否则正确动作必然带禁用词，题自相矛盾）；
5. 题面说"周X"时，`expect_date`／`as_of+offset` 落出来的星期必须真是周X（星期换算最容易凭感觉错）；
6. 期望动作必须在"判据看得见载荷"的类型里——**TIMER 被明令禁止**：它的载荷只有原始标记串 `tag`，
   属判别键不参与内容比对，也没有 date 字段 ⇒ 任何"TIMER＋绝对日期/片段"的题永远判不过；
7. 每条未认证题必须带 `blocked_reason`（认证棘轮要求，合入前就该备好，而不是合入时才发现）。
"""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
DRAFT = REPO / "scripts" / "diagnostics" / "memory_action_cases_j1j2_draft.jsonl"
OFFICIAL = REPO / "scripts" / "diagnostics" / "memory_action_cases_zh.jsonl"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_draft_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


def _read(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


CASES = _read(DRAFT)

# 判据「看得见载荷内容」的动作类型：载荷里含 _J1_PAYLOAD_KEYS 之一，或含 date
VISIBLE_ACTIONS = {"MEMO", "CAL_NOTE", "SEARCH", "RECALL", "NOTE_DONE", "IMG_TEXT", "GEN_IMAGE",
                   "STATUS_UPDATE"}
WEEKDAY_WORDS = {"周一": 1, "周二": 2, "周三": 3, "周四": 4, "周五": 5, "周六": 6, "周日": 7,
                 "周末": 6, "下周三": 3, "下周四": 4, "下周五": 5, "下周六": 6, "下周日": 7}


def _resolved_date(c) -> date | None:
    ta = c.get("time_anchor") or {}
    s = ev.resolve_expect_date(ta)
    return date.fromisoformat(s) if s else None


def test_草稿存在且规模与判据分布如登记():
    assert DRAFT.exists(), "草稿不见了（生成器 output/a30_build_j1j2_draft.py）"
    assert len(CASES) == 12, len(CASES)
    assert sum(1 for c in CASES if c["judge"] == "J1") == 10
    assert sum(1 for c in CASES if c["judge"] == "J2") == 2


def test_断言1_草稿必须过跑分器lint():
    assert ev.lint_dataset(CASES) == [], ev.lint_dataset(CASES)[:5]


def test_断言2_J2槽名必须是真实存在的槽():
    from app.memory.user_facts import MUTABLE_SLOTS
    real = {"user_fact_" + k for k in MUTABLE_SLOTS}
    for c in CASES:
        e = c["expect"]
        used = set(e.get("slots") or {}) | set(e.get("slots_none") or [])
        for k in used:
            assert k in real, f"{c['cid']} 用了不存在的槽 {k}（真实槽全集：{sorted(real)}）"


def test_断言3_期望片段必须真的在种子内容里():
    for c in CASES:
        e = c["expect"]
        if c["judge"] != "J1":
            continue
        blob = " ".join(s["content"] for s in c["seeds"])
        for frag in (e.get("field_match") or {}).values():
            assert frag in blob, f"{c['cid']} 要求动作含「{frag}」，但种子里没有 ⇒ 模型做对也判不过"


def test_断言4_禁用片段不许出现在该被引用的种子里():
    """supersede 类：旧值可以存在于库里，但**不能同时是新值句子里的词**，否则自相矛盾。"""
    for c in CASES:
        e = c["expect"]
        new_val = c["seeds"][0]["content"]          # 约定：第 0 条＝作废后的新值
        for frag in (e.get("forbidden") or []):
            assert frag not in new_val, f"{c['cid']} 的禁用片段「{frag}」出现在新值句子里 ⇒ 正确动作必被判错"


def test_断言5_星期换算必须对得上():
    """星期词既可能写在题面，也可能写在**种子里**（"画室周三带课"就是种子）。

    第一版只扫 `turn`，于是把 offset 从 5 改回 2（周五+2＝周日，与种子的周三矛盾）都能过——
    变异电池 M1 就是这样把它照出来的。现在两处都扫，并额外断言这条规则真的命中过题，
    防止它退化成永真。
    """
    checked = 0
    for c in CASES:
        if c["category"] != "temporal":
            continue
        got = _resolved_date(c)
        assert got, f"{c['cid']} 时间题算不出期望日期（expect_date 与 offset 都没给）"
        text = c["turn"] + " " + " ".join(s["content"] for s in c["seeds"])
        days = {wd for word, wd in WEEKDAY_WORDS.items() if word in text}
        if len(days) == 1:
            wd = days.pop()
            assert got.isoweekday() == wd, \
                f"{c['cid']} 题面/种子说「星期{wd}」，锚点却落 {got}（星期 {got.isoweekday()}）"
            checked += 1
        elif len(days) > 1:
            raise AssertionError(f"{c['cid']} 同时出现多个星期词 {days}，锚点无法判定，题面要改")
    assert checked >= 2, f"星期规则只命中 {checked} 题 ⇒ 这条断言接近空转，样本要覆盖到"
    # 至少有一题走 offset 分支（否则断言 5 只覆盖了 expect_date 一条路）
    assert any((c["time_anchor"] or {}).get("expect_offset_days") is not None
               for c in CASES if c["category"] == "temporal")


def test_断言5b_offset与as_of的换算和expect_date互斥一致():
    ta = {"as_of": "2026-09-25", "expect_offset_days": 5}
    assert ev.resolve_expect_date(ta) == "2026-09-30"


def test_断言6_不许出判据看不见的动作类型():
    for c in CASES:
        e = c["expect"]
        at = e.get("action_type")
        if c["judge"] != "J1" or not at or at == "NONE":
            continue
        assert at in VISIBLE_ACTIONS, (
            f"{c['cid']} 期望 {at}，但 parse_actions 给它的载荷只有判别键（无内容、无 date）⇒ 这条题永远判不过；"
            "要出这类题得先扩判据键（见 memory_action_eval._J1_META_KEYS 注释）")


def test_断言7_未认证题必须带blocked_reason():
    for c in CASES:
        s = c.get("solvability") or {}
        assert s.get("certified") is False, f"{c['cid']} 不能提前打认证勾（三跑认证要真跑生成）"
        assert s.get("blocked_reason"), f"{c['cid']} 未认证却没写原因"


def test_草稿不得引入正式集没有的memory_type():
    used = {s.get("memory_type") for c in CASES for s in c["seeds"]}
    official = {s.get("memory_type") for c in _read(OFFICIAL) for s in c["seeds"]}
    assert used <= official, f"草稿造了新词 {sorted(used - official)}（正式集只用 {sorted(official)}）"


@pytest.mark.parametrize("bad_offset,expect_msg", [(2, "周日")])
def test_回归_周五锚点加两天不是周三(bad_offset, expect_msg):
    """把我犯过的第一个错固化：as_of=2026-09-25 是周五，+2 落到周日，题面说的却是周三。"""
    d = date(2026, 9, 25) + timedelta(days=bad_offset)
    assert d.isoweekday() == 7 and d.strftime("%a") != "Wed"
    assert (date(2026, 9, 25) + timedelta(days=5)).isoweekday() == 3
