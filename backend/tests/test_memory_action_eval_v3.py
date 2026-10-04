"""批 0-4「动作式记忆评测」的 **CI 档**（方案 §六.2）：数据集 lint ＋ 判分器自测 ＋ 确定性跑通。

三条原则（都是这个仓反复踩过的）：
1. **判分器先要被测**（方案 R6）：J3 的两种口径、弃权口径、错因归类一律用固定输入钉死；
2. **lint 不许空转**：正式集必须 0 违规，**并且**草稿池里那几条带「上次／之前」的题必须被咬到（反证）；
3. **假向量环境不得声称语义通过**（`backend/tests/conftest.py:229-231`）：本文件只跑确定性子集，
   并断言输出里 `semantic is False`；真 bge-m3 的语义档属本机／夜间跑，不进 CI。

端到端那一例走**子进程**（不在本 pytest 进程里改 `DATABASE_URL`）：脚本自己会重新绑定临时库，
在测试进程内直接调它会写进 conftest 的会话沙箱——那是另一种「测试污染」。
"""
import ast
import collections
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
DATASET = REPO / "scripts" / "diagnostics" / "memory_action_cases_zh.jsonl"
DRAFT = REPO / "scripts" / "diagnostics" / "memory_action_cases_draft.jsonl"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_eval_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


def _read(path):
    return [c for c in (ev.parse_case(ln) for ln in path.read_text(encoding="utf-8").split("\n")) if c]


OFFICIAL = _read(DATASET)


# ─────────────────────────── ① 数据集 lint ───────────────────────────
def test_正式集_lint_必须零违规():
    assert OFFICIAL, "正式集读不出用例（文件被挪动或编码变了）"
    # 62＝M0-b（7 类 × 8 ＋ 弃权 6）；＋15＝A26(a) 补题（relationship 7、temporal 8，**只增不减**）
    assert len(OFFICIAL) == 77, "正式集应为 77 条（M0-b 62 ＋ A26 补 15）；实际 %d 条" % len(OFFICIAL)
    assert "ar09" not in {c["cid"] for c in OFFICIAL}, (
        "候选 ar09 未过认证却进了正式集 ⇒ 违反 §2.3（掉出认证的候选题不入集，补题不是把坏题塞进来）")
    assert ev.lint_dataset(OFFICIAL) == [], "正式集 lint 违规：%s" % ev.lint_dataset(OFFICIAL)[:6]


def test_草稿池必须被_lint_咬住_防空转():
    """反证：如果 lint 什么都咬不到，『正式集 0 违规』这句话就毫无价值。"""
    bad = ev.lint_dataset(_read(DRAFT))
    assert bad, "草稿池本该不合法，lint 却全放过 ⇒ 黑名单失效"
    cue = [b for b in bad if "检索提示词" in b]
    assert any("dg01" in b for b in cue), "带『上次』的题没被抓：%s" % cue
    assert any("da01" in b for b in cue), "带『之前／提过』的题没被抓：%s" % cue


def test_每类题量必须够做分类统计():
    """方案 §三：每类 ≥6 条才有分类型统计意义——只报总体会把单类塌陷平均掉（§4.5）。"""
    cats = collections.Counter(c["category"] for c in OFFICIAL)
    thin = {k: v for k, v in cats.items() if v < 6}
    assert not thin, "这些类不足 6 条：%s" % thin
    assert set(cats) == set(ev.CATEGORIES), "类别集合与跑分器不一致：%s" % (set(cats) ^ set(ev.CATEGORIES))


def test_方案点名缺失的三类必须已补齐():
    """方案 §1.3 论据 6：现 104 条里 relationship／group／perception 一条都没有。"""
    cats = collections.Counter(c["category"] for c in OFFICIAL)
    for k in ("relationship", "group", "perception"):
        assert cats[k] >= 8, "%s 类不足 8 条（实际 %s）" % (k, cats[k])


def test_三种口径的强弱关系与反例():
    """用户授权「按你推荐执行」⇒ J3 口径这样定，并留下可检验的关系而不是含糊说法：

    - `pass`（主口径＝名次）：gold 全进 top-k 且最后一条 gold 排在任何**干扰项**之前。中性行占位不罚。
    - `pass_gold`（最严）：top-|gold| 必须恰好全是 gold ⇒ 中性行挤占也罚。
    - `pass_strict`（方案 §二 字面契约、固定 k）：top-k 内不得出现干扰项 ⇒ 在窄库上近乎恒假，只留作对账。

    关系：`pass_gold ⇒ pass`、`pass_strict ⇒ pass`；**两个反向都不成立**（下面各给一个反例）。
    这条测试一开始我是按「名次口径 ≡ 严格@|gold|」写的，跑出来直接红——那说明两者不等价，
    于是口径从"我以为一样"改成"关系与反例都钉住"。
    """
    cases = [
        ([1, 2], [3, 4], [1, 2, 3, 4]),      # 常规：名次过、strict@5 不过、gold 过
        ([1], [2, 3], [4, 1, 2]),            # 反例 A：中性行 4 占位 ⇒ 名次 True 但 gold False
        ([1, 2], [3, 4], [3, 1, 4, 2]),      # 干扰项插队 ⇒ 全 False
        ([1, 2, 3], [4], [1, 2, 3, 4]),      # gold 全在前 ⇒ 名次与 gold 都 True
        ([1, 2], [], [2, 1]),                # 无干扰项
        ([], [3], [3]),                      # 没有 gold ⇒ 一律 False（不许空判通过）
        ([1, 2], [3], [1, 2]),               # 召回不足 k：gold 全中且无干扰 ⇒ strict True
    ]
    for gold, dis, recalled in cases:
        v = ev.j3_verdict(gold, dis, recalled)
        if v["pass_gold"]:
            assert v["pass"], ("pass_gold 必须蕴含名次口径", gold, dis, recalled)
        if v["pass_strict"] and gold:
            assert v["pass"], ("pass_strict 必须蕴含名次口径", gold, dis, recalled)

    a = ev.j3_verdict([1], [2, 3], [4, 1, 2])
    assert a["pass"] is True and a["pass_gold"] is False, "反向蕴含（名次⇒gold）不该成立"
    b = ev.j3_verdict([1, 2], [3, 4], [1, 2, 3, 4])
    assert b["pass"] is True and b["pass_strict"] is False, "反向蕴含（名次⇒strict@5）不该成立"


def test_认证状态必须逐条可追溯_且未认证数只许降():
    """方案 §2.3「未认证不入正式集」在 M0 只能落成"逐条标状态"：

    实测发现硬要求①（题面不许提示检索）与 J3「gold 必须进 top-k」天然对冲——不提示的题，gold 在
    向量／关键词空间里本来就可能很远，M0 那轮 9/56 条因此过不了认证。**删题会把 relationship 类打到
    4 条、击穿「每类 ≥6」的分类统计基础**，所以改成保留＋标记，并把未认证条数做成只许降不许升的棘轮。

    M1（10-04）把库规模从 10 行抬到 54 行后重认证：47→43 条过，未认证 15→19（J3 13 ＋ J4 豁免 6）。
    上限跟着实测抬一次，**这是唯一一次允许抬高**——之后只许降；抬的理由是"题更难了"而不是"题坏了"，
    逐条 `blocked_reason` 里写着当时的 full_pass3/no_mem_fail/gold3 与库行数，可对账。
    """
    UNCERTIFIED_CAP = 19   # 2026-10-04 M1 重认证实测；此前 15（M0 小库）
    un = [c["cid"] for c in OFFICIAL if not (c.get("solvability") or {}).get("certified")]
    assert len(un) <= UNCERTIFIED_CAP, "未认证题数 %d 超过棘轮上限 %d：%s" % (len(un), UNCERTIFIED_CAP, un)
    for c in OFFICIAL:
        s = c.get("solvability") or {}
        if s.get("certified"):
            assert s.get("certified_at"), "%s 标了已认证但没有 certified_at" % c["cid"]
            assert s.get("certified_by"), "%s 的认证没写是哪一轮/哪个口径做的（标记不能无出处）" % c["cid"]
        else:
            assert (s.get("blocked_reason") or s.get("exempt_reason")), (
                "%s 既未认证又没写原因＝静默放过，尺子上多了一个空齿" % c["cid"])
    # 抬难度**不许把某类打出分母**（低于 3 条就失去任何分类型意义），也不许悄悄缩：
    # 认证不足 6 条的类必须在报表里显式带 ⚠，读的人不会被「总体 AR 挺高」糊过去。
    left = collections.Counter(c["category"] for c in OFFICIAL
                               if (c.get("solvability") or {}).get("certified"))
    for cat, n in left.items():
        assert n >= 3, "认证子集里 %s 类只剩 %d 条，该类已无法读任何趋势" % (cat, n)
    # A26(a) 补题之后：任何类都不许再出现「认证不足 6 条」（此前 relationship/temporal 各只剩 3 条）
    thin = {k: v for k, v in left.items() if v < 6 and k != "abstention"}
    assert not thin, "这些类认证不足 6 条，分类统计读不动：%s" % thin
    # 指代型轮次必须被归到「指代无锚・J3 不测」，**不许当检索栈缺陷的证据**（A26 的实测结论：
    # 16 道带情境线索的候选题 15 道直接过认证、且 gold3=3，说明掉出的老题是题面没锚，不是召不回）
    for c in OFFICIAL:
        s = c.get("solvability") or {}
        if c["category"] == "abstention":
            assert s.get("exempt_reason"), "%s 弃权类必须带豁免理由" % c["cid"]
        elif not s.get("certified") and c["category"] in ("relationship", "temporal"):
            assert s.get("blocked_class") == "指代无锚", (
                "%s 掉出认证却没归类 ⇒ 会被后人当成「检索做不到」去改召回" % c["cid"])
            assert s.get("full_pass3") == 0 and s.get("no_mem_fail") is True, (
                "%s 的「指代无锚」必须是「满库召不出＋空库不泄题」，别把别的故障混进来" % c["cid"])


def test_分类表必须带认证列并在不足时报警():
    """缩分母必须可见：分类表要有「认证」列，不足 6 条的类要带 ⚠。"""
    rows = [{"cid": "a1", "category": "temporal", "judge": "J3", "certified": True, "pass": True,
             "pass_strict": False, "pass_gold": True, "n_rows": 54, "missing": [], "polluted": []},
            {"cid": "a2", "category": "temporal", "judge": "J3", "certified": False, "pass": False,
             "pass_strict": False, "pass_gold": False, "n_rows": 54, "missing": [1], "polluted": []}]
    s = ev.summarize(rows)
    assert s["by_category"]["temporal"]["n_certified"] == 1, s["by_category"]
    rep = {"k": 5, "semantic": False, "llm_guard": "stub", "skipped_judges": [],
           "cases_run": 2, "cases_total": 2, "mode": "score", "filler_target": 44,
           "configs": {"baseline": {**s, "rows": rows}}}
    txt = ev.render(rep, dataset="d.jsonl", judges=["J3"], configs=["baseline"], mode="score")
    assert "| 类别 | 条数 | 认证 |" in txt, "分类表没有认证列"
    assert "⚠ 认证子集仅 1 条" in txt, "认证不足的类没报警"


def test_认证子集必须单独出数_缺标记的行两边都不算():
    """主分母改成「已认证子集」必须**能被证明**，不是改个文案：

    ① 换了分母数就真的变（全量 AR 与认证子集 AR 在同一个行集上不相等）；
    ② 行里缺 `certified` 键时既不记已认证也不记未认证（宁可少算，也不把「没标记」当「未认证」，
       否则棘轮和门禁都会被静默做脏）；
    ③ 报表把两个口径并列输出，人一眼能看出用的是哪个分母（§4.5 改尺子必须留痕）。
    """
    def _row(cid, cert, ok, cat="fact"):
        r = {"cid": cid, "category": cat, "judge": "J3", "pass": ok,
             "pass_strict": False, "pass_gold": ok, "missing": [], "polluted": []}
        if cert is not None:
            r["certified"] = cert
        return r

    rows = [_row("a", True, True), _row("b", True, False),
            _row("c", False, True), _row("d", False, True), _row("e", None, False)]
    s = ev.summarize(rows)
    assert (s["n"], s["n_certified"], s["n_uncertified"]) == (5, 2, 2), s
    assert s["ar"] == 60.0 and s["ar_certified"] == 50.0 and s["ar_uncertified"] == 100.0, s
    assert s["n_certified"] + s["n_uncertified"] < s["n"], "缺 certified 键的行被算进了某个子集"

    unmarked = ev.summarize([{"cid": "x", "category": "fact", "judge": "J3", "pass": True,
                              "missing": [], "polluted": []}])
    assert unmarked["n_certified"] == 0 and unmarked["ar_certified"] is None, unmarked

    rep = {"k": 5, "semantic": False, "llm_guard": "stub", "skipped_judges": [],
           "cases_run": 5, "cases_total": 5, "mode": "score",
           "configs": {"baseline": {**s, "rows": rows}}}
    txt = ev.render(rep, dataset="d.jsonl", judges=["J3"], configs=["baseline"], mode="score")
    assert "AR_cert" in txt and "主分母＝已认证子集" in txt, "报表没写清主分母是谁"
    assert '"n_certified": 2' in ev.metrics_json(rep), "机器口径没带认证子集计数"


def test_分类汇总必须与总体对得上():
    """钉住 by_category 的累加器（历史缺陷：第 4 格用 append、读数按固定下标 v[3]，
    于是每类 AR_gold 只反映该类**第一条**用例——8 条的类只能读出 12.5% 或 0%，
    总体 AR_gold 67.9% 与分类表 12.5% 自相矛盾却没人发现）。

    口径：分类表的分子之和必须等于总体的分子，三类三口径各自对账一次。
    """
    def _r(cat, ok, strict, gold):
        return {"cid": cat + str(ok), "category": cat, "judge": "J3", "pass": ok,
                "pass_strict": strict, "pass_gold": gold, "missing": [], "polluted": [],
                "certified": True}

    # 故意把「首条失败、后面成功」放一起：只看第一条的累加器会在这里露馅
    rows = [_r("fact", False, False, False), _r("fact", True, True, True),
            _r("fact", True, False, False), _r("fact", True, True, True),
            _r("group", True, False, False), _r("group", False, False, False),
            _r("temporal", True, True, True)]
    s = ev.summarize(rows)
    bc = s["by_category"]
    assert sum(v["n"] for v in bc.values()) == s["n"] == 7, bc
    for key, overall in (("ar", s["ar"]), ("ar_strict", s["ar_strict"]), ("ar_gold", s["ar_gold"])):
        num = sum(round(v[key] * v["n"] / 100.0) for v in bc.values())
        assert num == round(overall * s["n"] / 100.0), (key, num, overall, bc)
    assert bc["fact"]["ar_gold"] == 50.0, bc["fact"]      # 4 条里 2 条过
    assert bc["fact"]["ar_strict"] == 50.0, bc["fact"]
    assert bc["group"]["ar_gold"] == 0.0 and bc["temporal"]["ar_gold"] == 100.0, bc


def test_门禁必须锚在有分辨力的列上():
    """10-04 权威基线实测：**名次口径在已认证子集上饱和**（AR_cert 97.9～100.0，且 flags_off 反而＝100），
    拿它当门禁就是挂一条永远绿的线；AR_gold 67.9（分类 50～100）才有 32pp 余量。

    所以这条守卫钉两件事：① `--fail-below` 读的必须是 `ar_gold`，**不许**读回 ar/ar_certified；
    ② 报表里必须写着「AR_cert 不得当门禁用」——口径换了要在文件里留痕，不能只活在提交信息里。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    i = src.index("if a.fail_below is not None:")
    block = src[i:src.index("return 4", i)]
    assert "ar_gold_certified" in block, "门禁没读「认证子集 × AR_gold」这列：%s" % block
    assert "ar_certified" not in block and '"ar"' not in block, "门禁读回了饱和列／全量名次列"
    assert "门禁一律读「认证子集 × AR_gold」" in src, "报表没写清门禁读哪列"
    assert "自我循环" in src, "报表没留下「为什么不能用 gold 口径当认证门槛」的证据"
    # 反证：饱和的认证子集在名次口径下满分，同一行集在 pass_gold 下必须掉下来
    rows = [{"cid": "s1", "category": "fact", "judge": "J3", "certified": True, "pass": True,
             "pass_gold": False, "n_rows": 54, "missing": [], "polluted": [7]},
            {"cid": "s2", "category": "fact", "judge": "J3", "certified": True, "pass": True,
             "pass_gold": True, "n_rows": 54, "missing": [], "polluted": []}]
    s = ev.summarize(rows)
    assert s["ar_certified"] == 100.0 and s["ar_gold_certified"] == 50.0, s


def test_填充池必须是整句而不是单字():
    """M1 加难靠填充抬库规模。真实踩过的坑：`tuple("甲" "乙" ...)` 里相邻字面量**先拼接**再逐字符成元组，
    池子当场变成几十个单字——填充行数照样凑满，但每行只有一个字，规模是假的、跑分照样绿。
    """
    pool = ev.FILLER_POOL
    assert len(pool) >= ev.FILLER_TARGET_DEFAULT + 4, "池子太小，凑不满目标行数就要出重复"
    assert len(set(pool)) == len(pool), "填充句有重复"
    for x in pool:
        assert isinstance(x, str) and len(x) >= 10, "填充句不是整句：%r" % (x,)
    # 填充句不许含任何题面词，否则等于给检索递线索
    for c in OFFICIAL:
        for x in pool:
            assert x != c["turn"], "填充句与某题题面相同：%s" % c["cid"]


def test_填充句不得夹带答案_且规模真的抬起来():
    """硬不变式：填充行里不得出现任何 gold 正文的 ≥6 字连续片段（复用②通道同一个滑窗）——
    否则它就是「换了个 id 的 gold」，AR 会被自己造的数据抬高，尺子白紧一遍。"""
    from app.memory.utility_feedback import _contains_key_fragment, _core_snippet
    target = ev.FILLER_TARGET_DEFAULT
    for i, c in enumerate(OFFICIAL):
        fil, skipped = ev.fillers_for(c, i, target)
        assert len(fil) == target, (c["cid"], len(fil))
        assert len({r["content"] for r in fil}) == target, "%s 填充行内部重复" % c["cid"]
        dis_texts = {d.get("content") for d in (c.get("distractors") or [])}
        for r in fil:
            assert r["content"] not in dis_texts, "%s 把本题干扰项当填充（会双计）" % c["cid"]
            for g in ev._gold_texts_of(c):
                assert not _contains_key_fragment(_core_snippet(g), ev._norm(r["content"])), (
                    "%s 的填充句夹带答案：%s ⇄ %s" % (c["cid"], g, r["content"]))
        assert skipped == 0, "剔除了 %d 条说明池子跟题面撞了，池子要重写（不是放宽判据）" % skipped
        rows = len(c["seeds"]) + len(c.get("distractors") or []) + len(fil)
        assert rows >= 40, "%s 库规模只有 %d 行，没到真实量级" % (c["cid"], rows)
    # 确定性：同一 (case, idx) 两次取必须一致；不同 case 必须错开（否则所有题面对同一批竞争行）
    assert ev.fillers_for(OFFICIAL[3], 3, 20)[0] == ev.fillers_for(OFFICIAL[3], 3, 20)[0]
    assert ev.fillers_for(OFFICIAL[0], 0, 5)[0] != ev.fillers_for(OFFICIAL[1], 1, 5)[0]
    assert ev.fillers_for(OFFICIAL[0], 0, 0)[0] == [], "target=0 应关闭填充（A/B 对照要用）"
    # **正例控制**（防空齿）：往池子里塞一条真含 gold 的句子，剔除判据必须咬住它。
    # 只靠「现有池子恰好不含答案」是验不出这颗粒子的——池子改了、判据被删，测试照样绿。
    gold0 = ev._gold_texts_of(OFFICIAL[0])[0]
    saved_pool = ev.FILLER_POOL
    try:
        ev.FILLER_POOL = (gold0, "别的无关句子一", "别的无关句子二")
        got, skipped = ev.fillers_for(OFFICIAL[0], 0, 3)
        assert skipped >= 1 and all(r["content"] != gold0 for r in got), (
            "夹带答案的填充句没被剔除 ⇒ 守卫是空齿")
    finally:
        ev.FILLER_POOL = saved_pool
    assert ev.FILLER_POOL == saved_pool, "池子没还原干净"


def test_认证门槛不得用门禁要量的那列():
    """认证＝**题目合法性**，与「当前检索做得对不对」必须是两件事。

    10-04 我自己先犯过一次：把认证门槛从名次口径改成 `pass_gold ≥ 2/3`，理由是「名次口径饱和」。
    跑完立刻循环了——选子集的条件就是门禁要量的那个数，于是「认证子集 × AR_gold」恒 ≈100
    （那一轮：认证 47→35 条，认证子集 AR_gold 直接 100.0），headroom 归零，比饱和更糟。
    现在加难只加在**库规模**上，认证口径钉回名次；真值表逐格钉住，任何一格回退都红。
    """
    v = ev.certify_verdict
    assert v(True, 3, 0, True)["certified"] is True, "合法但当前做不好＝正是门禁要抓的东西，必须留在分母里"
    assert v(True, 1, 3, True)["certified"] is False, "名次不过 2/3 不得认证"
    assert v(True, 3, 3, True)["certified"] is True
    assert v(False, 3, 3, True)["certified"] is False, "空库也召得出＝泄题，不得认证"
    assert v(True, 3, 3, False)["certified"] is False, "干扰项里含 gold＝题不合法"
    got = v(True, 3, 1, True)
    assert got["full_pass3"] == 3 and got["full_gold3"] == 1, "gold 计数必须记录（不作门槛，但要能对账）"
    # 源码锚点：认证表达式里不许出现 pass_gold／full_gold3 当条件
    src = SCRIPT.read_text(encoding="utf-8")
    i = src.index('"certified": bool(')
    line = src[i:src.index("\n", i)]
    assert "full_rank3" in line and "full_gold3" not in line, "认证门槛又回退成门禁要量的那列：%s" % line


def test_报表必须把库规模与门禁列一起出来():
    """门禁读的是「认证子集 × AR_gold」，报表必须同一列可见；库规模不写出来，读数就没法解释。"""
    rows = [{"cid": "x1", "category": "fact", "judge": "J3", "certified": True, "pass": True,
             "pass_gold": False, "n_rows": 54, "missing": [], "polluted": []},
            {"cid": "x2", "category": "fact", "judge": "J3", "certified": True, "pass": True,
             "pass_gold": True, "n_rows": 54, "missing": [], "polluted": []}]
    s = ev.summarize(rows)
    assert s["rows_mean"] == 54.0 and s["ar_gold_certified"] == 50.0, s
    assert s["cert_rate"] == 100.0, s        # 认证率＝尺子自身健康度，加难后分母缩了必须可见
    rep = {"k": 5, "semantic": False, "llm_guard": "stub", "skipped_judges": [],
           "cases_run": 2, "cases_total": 2, "mode": "score", "filler_target": 44,
           "configs": {"baseline": {**s, "rows": rows}}}
    txt = ev.render(rep, dataset="d.jsonl", judges=["J3"], configs=["baseline"], mode="score")
    assert "库规模" in txt and "AR_gold(认证)" in txt and "认证率" in txt, "报表没写库规模／门禁列／认证率"
    assert '"cert_rate": 100.0' in ev.metrics_json(rep), "机器口径没带认证率"


def test_lint_逐项都能咬():
    """逐条构造违规，钉住 lint 的每个分支（缺一个分支＝尺子上有个空齿）。"""
    base = dict(OFFICIAL[0])
    checks = [
        ("未知 category", {**base, "category": "vibes"}),
        ("非法 judge", {**base, "judge": "J9"}),
        ("J4 用在非弃权类", {**base, "judge": "J4"}),          # base 是 fact 类
        ("缺必填字段", {k: v for k, v in base.items() if k != "time_anchor"}),
        ("gold 下标越界", {**base, "expect": {**base["expect"], "gold_seed_idx": [99]}}),
        ("非弃权类没有 gold", {**base, "expect": {**base["expect"], "gold_seed_idx": [], "abstain": False}}),
        ("缺干扰项无法认证", {**base, "distractors": []}),
    ]
    for name, case in checks:
        out = ev.lint_dataset([case])
        assert out, "lint 漏判：%s" % name


def test_重复_cid_会被抓到():
    assert any("cid 重复" in x for x in ev.lint_dataset([OFFICIAL[0], OFFICIAL[0]]))


# ─────────────────────────── ② 判分器自测 ───────────────────────────
def test_J3_rank_口径_gold_排在干扰项前才算过():
    v = ev.j3_verdict([1, 2], [3, 4], [1, 2, 3, 4])
    assert v["pass"] is True and v["pass_rank"] is True
    assert v["pass_strict"] is False and v["polluted"] == [3, 4]


def test_J3_干扰项插队必须判不过():
    v = ev.j3_verdict([1, 2], [3, 4], [3, 1, 4, 2])
    assert v["pass"] is False and v["pass_strict"] is False


def test_J3_gold_缺席算_E1():
    v = ev.j3_verdict([1, 2], [3], [3])
    assert v["pass"] is False and v["missing"] == [1, 2]


def test_J3_期望自相矛盾要单独暴露():
    """同一条记忆既标 gold 又标 distractor ⇒ 是标注错，不该被当成检索失败。"""
    v = ev.j3_verdict([1], [1], [1])
    assert v["expect_self_conflict"] == [1]


def test_J3_没有_gold_时一律判不过():
    assert ev.j3_verdict([], [3], [3])["pass"] is False


def test_弃权口径_与_J3_分开():
    assert ev.abstain_verdict([], [7, 8])["pass"] is True
    assert ev.abstain_verdict([7], [7])["pass"] is False


def test_错因归类_E1_与_E2_分得开():
    assert ev.classify_error({"pass": False, "missing": [1], "polluted": []}) == "E1"
    assert ev.classify_error({"pass": False, "missing": [], "polluted": [3]}) == "E2"
    assert ev.classify_error({"pass": True, "missing": [], "polluted": []}) == ""


def test_配置矩阵里的旗标必须都真实注册():
    """A19 的教训复用：配置轴上写了个不存在的键 ⇒ 三档跑分逐字节相同，还以为在对比。"""
    from app.flags.agent_flags import AGENT_FLAGS

    keys = set(ev.BASELINE_FLAGS)
    for cfg in ev.CONFIG_MATRIX.values():
        keys |= set(cfg)
    missing = sorted(k for k in keys if k not in AGENT_FLAGS)
    assert not missing, "评测要拨的键不在字典里（空转配置）：%s" % missing


def test_三档配置必须是不同的快照():
    """消融档若与 baseline 算出同一组旗标，分档就没有意义（＝白跑一遍）。"""
    assert "baseline" in ev.CONFIG_MATRIX and "no_temporal" in ev.CONFIG_MATRIX
    flat = {n: {**ev.BASELINE_FLAGS, **ev.CONFIG_MATRIX[n]} for n in ("baseline", "no_temporal", "no_peak")}
    assert flat["baseline"] != flat["no_temporal"] != flat["no_peak"]


def test_基线必须等于生产实配而不是全关():
    """2026-10-04 自己踩过的反例：把「四键全关」当 baseline，于是把消融档读成了现状，结论方向正好相反。

    依据（只读实测）：`memory_temporal_recall`／`memory_recall_second_hop` 默认已是 True（10-02 转正），
    `memory_peak_cutoff` 生产覆盖 ON。⇒ 基线里这三键不得全 False。
    """
    b = ev.BASELINE_FLAGS
    assert b.get("memory_temporal_recall") is True and b.get("memory_recall_second_hop") is True
    assert b.get("memory_peak_cutoff") is True, "基线漏掉生产已拨开的弃权硬顶档"
    assert b.get("memory_tiered_decay") is False, "分层衰减仍是预留（A19），不该出现在基线里"


def test_配置之间必须独占角色号段():
    """2026-10-04 实测踩到的坑：多配置连跑时角色 id 若按用例序号固定，后一个配置的 top-k
    会被前一个配置灌进去的同内容旧行挤掉——E1 从 3 涨到 53 看着像「记忆失效」，其实是执行顺序污染。"""
    c0 = [ev.case_cids(ev.CID_BASE_DEFAULT + ci * ev.CID_SLOT_PER_CONFIG, i)
          for ci in range(4) for i in range(20)]
    flat = [x for tup in c0 for x in tup]
    assert len(flat) == len(set(flat)), "配置／用例之间有角色号重叠 ⇒ 多档连跑互相污染"


def test_基线定义必须等于生产实配():
    """把「baseline＝四键全关」这个错误定义钉死（10-04 犯过一次，结论方向因此说反）。"""
    assert ev.BASELINE_FLAGS == ev.PROD_FLAGS
    assert ev.PROD_FLAGS["memory_temporal_recall"] is True     # 10-02 转正
    assert ev.PROD_FLAGS["memory_recall_second_hop"] is True   # 10-02 转正
    assert ev.PROD_FLAGS["memory_peak_cutoff"] is True         # 生产覆盖 ON（2026-09-05）
    assert "flags_off" in ev.CONFIG_MATRIX, "缺「四键全关」消融档"
    # 方案 §4.4 的 B0 还要清空记忆；J3 档下 no_mem 恒召不出 ⇒ B0 下限锚在 M0 尚未成立（M1 才验）
    assert ev.CONFIG_MATRIX["flags_off"]["memory_temporal_recall"] is False


# ─────────────────────────── ③ 端到端（子进程，确定性路） ───────────────────────────
# Windows CI 坑（2026-10-04 实测，红在第 99 棒的 py3.13-Windows 档）：`text=True` 是按**本地编码**
# （CI 上是 cp1252）解码子进程输出的，而跑分器打的是中文 UTF-8 ⇒ 解码异常被 subprocess 内部吞掉，
# `p.stdout/p.stderr` 直接变成 None，测试于是报 `'NoneType' object has no attribute 'split'`——
# 看着像跑分器坏了，其实是测试自己的编码假设。两头都要锁：父进程按 utf-8 解，子进程按 utf-8 写。
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _communicate(p):
    """把「输出没解码出来」变成一条精确的失败信息，而不是留个 None 让下一个人猜。"""
    assert p.stdout is not None and p.stderr is not None, (
        "子进程输出没被解码（rc=%s）＝测试自己的编码假设坏了，不是跑分器坏了；见 CHILD_ENV 注释"
        % p.returncode)
    return p.stdout, p.stderr


def _run_cli(tmp_path, *extra):
    """子进程跑分器：报告一律写进 pytest 的 `tmp_path`。

    为什么不能写 `Path(py).parent`（第一版就这么错过）：那是 **venv 的 Scripts 目录**——
    本机把产物塞进虚拟环境、CI 上还可能不可写；AGENTS 的测试卫生纪律要求临时产物归 pytest 自管。
    """
    py = str(Path(ev.__file__).resolve().parents[2] / "backend" / ".venv" / "Scripts" / "python.exe")
    if not Path(py).is_file():                     # 非本机（CI linux）用当前解释器
        py = sys.executable
    out_path = Path(tmp_path) / "memact_report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)   # 第二次跑用子目录，父目录得先存在
    cmd = [py, str(SCRIPT), "--dataset", str(DATASET), "--judges", "J3",
           "--configs", "baseline", "--mode", "score", "--sparse-only", "--limit", "3",
           "--print-metrics", "--out", str(out_path)]
    p = subprocess.run(cmd + list(extra), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV, timeout=900)
    out, err = _communicate(p)
    return p.returncode, out, err


def test_评测不许往生产缓存目录写东西(tmp_path):
    """不变式：`backend/data/bm25_cache/` 是**生产**数据目录，评测造的假角色号（9000+）不得落盘到那里。

    2026-10-04 实测踩到：BM25 默认落盘 `data/bm25_cache/<character_id>.json`，评测一跑就写进 708 个假角色缓存，
    下一轮再读到它 ⇒ 报出「空库角色也召回到东西」的**假信号**（差点被我当成生产跨角色泄漏上报）。
    跑分器已用模块自己留的隔离钩子 `bm25_index._persist_root` 指到临时目录，这条守住它不被改回去。
    """
    cache_dir = REPO / "backend" / "data" / "bm25_cache"
    before = {p.name for p in cache_dir.glob("*.json")} if cache_dir.is_dir() else set()
    rc, _out, err = _run_cli(tmp_path, "--limit", "2")
    assert rc == 0, f"端到端跑失败：{err[-400:]}"
    after = {p.name for p in cache_dir.glob("*.json")} if cache_dir.is_dir() else set()
    fresh = sorted(after - before)
    assert not [f for f in fresh if f[:-5].isdigit() and int(f[:-5]) >= 9000], (
        "评测把假角色缓存写进了生产目录：%s" % fresh)


def test_确定性路端到端跑通且两次输出逐字节一致(tmp_path):
    """方案 M0 验收：同一提交连跑两次的机器输出必须逐字节一致（尺子先要稳）。"""
    rc1, out1, err1 = _run_cli(tmp_path)
    rc2, out2, _err2 = _run_cli(tmp_path / "second")
    assert rc1 == 0, "第一次跑非零退出：%s\n%s" % (err1[-800:], out1[-400:])
    assert rc2 == rc1
    m1 = out1.split("METRICS_JSON_BEGIN")[1].split("METRICS_JSON_END")[0]
    m2 = out2.split("METRICS_JSON_BEGIN")[1].split("METRICS_JSON_END")[0]
    d1 = json.loads(m1)
    assert d1["semantic"] is False, "确定性档却标了语义通过 ⇒ 违反 conftest 约束"
    assert d1["cases_run"] == 3
    assert m1 == m2, "两次跑输出不一致 ⇒ 判分器不稳定，先修尺子（§4.5）"


def test_J1J2_未授权必须拒绝运行(tmp_path):
    """零计费保证：J1/J2 要生成＝云端计费端点，必须显式授权才放行。"""
    rc, _out, err = _run_cli(tmp_path, "--judges", "J1,J2,J3")
    assert rc == 2, "未加 --allow-llm 却跑了 J1/J2（退出码 %s）：%s" % (rc, err[-400:])
    assert "J1/J2" in err or "计费" in err


def test_lint_不过时端到端拒绝跑(tmp_path):
    (tmp_path / "neg.md").parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([sys.executable, str(SCRIPT), "--dataset", str(DRAFT), "--judges", "J3",
                        "--sparse-only", "--out", str(tmp_path / "neg.md")],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV, timeout=600)
    out, err = _communicate(p)
    assert p.returncode == 3, "草稿池不合法却开跑了：%s" % out[-300:]


def test_子进程调用必须显式锁编码():
    """第 99 棒 CI 红在 Windows 档的真实原因（2026-10-04）：`text=True` 按**本地编码**（CI 上是 cp1252）
    解码，跑分器打的是中文 UTF-8 ⇒ 解码异常被 subprocess 内部吞掉、`p.stdout` 变成 None，
    报出来的却是 `'NoneType' object has no attribute 'split'`——看着像跑分器坏了，其实是测试的编码假设。

    口径：每一次 `subprocess.run` 都必须带 `encoding`/`errors`（父进程按 utf-8 解）和 `env`
    （子进程侧按 `PYTHONIOENCODING=utf-8` 写，否则 Windows 下它自己就 UnicodeEncodeError）。
    用 AST 找真调用，不按文本数——文本数会把这条守卫自己的文案也数进去（第一版就自咬了一次）。
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "run" and isinstance(n.func.value, ast.Name)
             and n.func.value.id == "subprocess"]
    assert len(calls) >= 2, "本文件已经没有端到端子进程调用了？那这条守卫要跟着删掉"
    for c in calls:
        kw = {k.arg for k in c.keywords}
        assert {"encoding", "errors", "env"} <= kw, (
            "第 %s 行的 subprocess.run 没锁编码：缺 %s" % (c.lineno,
                                                       sorted({"encoding", "errors", "env"} - kw)))
