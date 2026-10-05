"""批 0-4 · A30：J1／J2 判分器的自测（**零 LLM、零计费**）。

2026-10-05 的字段统一（重要，别改回去）：判分器吃的是数据集**既有**字段名
`expect.action_type / field_match / forbidden` ＋ `time_anchor.expect_date / expect_offset_days`，
而不是另造一套 `expect.j1.*`。理由：77 条正式集早就带这三个键（`forbidden` 有 32 条填了值
却没有消费者），同一件事两套词后人必踩——与「长期词典要先沿用它自己的口径」是同一条纪律。

为什么能在没出题、没接生成侧的情况下先测判分器：
`j1_verdict` / `j2_verdict` 都是纯函数——给它「模型输出解析出来的动作列表」或「落库槽位字典」
就能判。生成侧接线与出题是另外两件事（要计费端点＋产品口径），但**尺子必须先自证可信**。

本文件钉住六件事：
1. E3（没产出动作）与 E4（产出了但参数错）**必须分得开**——混成一类就等于放弃了
   「用了错记忆比不用记忆更危险」这条判断；
2. 归一化口径与 J3 同源（全半角／空白差异不得改变判定）；
3. `field_match` 的具名字段与伪字段 `"*"` 两种写法都要成立，且判别键（type/tag）不污染内容比对；
4. 日期只认题面锚点：`resolve_expect_date` 要能吃 `expect_date` 与 `as_of + expect_offset_days` 两种写法；
5. lint 对「声明了 J1/J2 却没出完标注」的题必须当场拦（否则它会静默走 J3 检索尺子＝拿错尺子还有分）；
6. 漂移守卫：`KNOWN_ACTION_TYPES` ＝ 生产 `ACTION_TYPES`；`parse_actions` 产出的载荷字段
   必须都在判分器视野里（新字段没纳入＝静默判不了）。
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_eval_j1j2_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


def _acts(text: str):
    """用**生产解析器**产动作（判分器与生产共用一套标记语法，不另立口径）。"""
    from app.agent.actions import parse_actions
    return parse_actions(text)


# ─────────────────────────── J1：动作参数 ───────────────────────────
def test_J1_命中期望动作且参数含片段_判过():
    v = ev.j1_verdict({"action_type": "MEMO", "field_match": {"*": "朵朵"}},
                      _acts("好，我记下来。[MEMO]女儿朵朵明天钢琴比赛[/MEMO]"))
    assert v["pass"] is True and v["err"] == "", v


def test_J1_具名字段与伪字段都要生效():
    acts = _acts("[CAL_NOTE]2026-03-15 接孩子放学[/CAL_NOTE]")
    assert ev.j1_verdict({"action_type": "CAL_NOTE",
                          "field_match": {"text": "接孩子", "date": "2026-03-15"}},
                         acts, expect_date="2026-03-15")["pass"] is True
    # 具名字段写错字段名 ⇒ 必须判不过（伪字段 "*" 会掩盖这种错，所以两种都测）
    assert ev.j1_verdict({"action_type": "CAL_NOTE", "field_match": {"conent": "接孩子"}},
                         acts)["pass"] is False


def test_J1_完全没产出动作算E3_产出了但参数错算E4():
    """E3/E4 必须分开：E4 是「拿错记忆真的去落库」，比 E3 危险，不能并成一类。"""
    spec = {"action_type": "MEMO", "field_match": {"*": "朵朵"}}
    no_action = ev.j1_verdict(spec, _acts("好，我记下来啦。"))
    wrong_fact = ev.j1_verdict(spec, _acts("[MEMO]儿子明天钢琴比赛[/MEMO]"))
    assert no_action["pass"] is False and no_action["err"] == "E3", no_action
    assert wrong_fact["pass"] is False and wrong_fact["err"] == "E4", wrong_fact
    assert "朵朵" in wrong_fact["why"], "E4 的 why 必须点名缺了哪个片段，否则报告没法看"


def test_J1_forbidden_是数据集里那32条在等的消费者():
    """supersede／用错偏好类：旧值或相反值出现在动作里就该失败，而不是「有动作就算对」。"""
    v = ev.j1_verdict({"action_type": "MEMO", "field_match": {"*": "腰伤已好转"},
                       "forbidden": ["忌久站"]},
                      _acts("[MEMO]用户腰伤已好转，还像以前一样忌久站[/MEMO]"))
    assert v["pass"] is False and v["err"] == "E4" and "禁用片段命中:忌久站" in v["why"], v


def test_J1_归一化与J3同源_全半角与空白不改判定():
    spec = {"action_type": "MEMO", "field_match": {"*": "朵朵 钢琴"}}
    assert ev.j1_verdict(spec, _acts("[MEMO]女儿朵朵　钢琴比赛[/MEMO]"))["pass"] is True, \
        "全角空格／多空白应当视作同一内容"
    assert ev.j1_verdict({"action_type": "memo", "field_match": {"*": "朵朵"}},
                         _acts("[MEMO]朵朵[/MEMO]"))["pass"] is True, "动作类型大小写不敏感"


def test_J1_date_只认题面锚点_且判的是解析后的绝对日期():
    ok = ev.j1_verdict({"action_type": "CAL_NOTE"},
                       _acts("[CAL_NOTE]2026-03-15 接孩子放学[/CAL_NOTE]"), expect_date="2026-03-15")
    assert ok["pass"] is True, ok
    off = ev.j1_verdict({"action_type": "CAL_NOTE"},
                        _acts("[CAL_NOTE]2026-03-14 接孩子放学[/CAL_NOTE]"), expect_date="2026-03-15")
    assert off["pass"] is False and off["err"] == "E4" and "date≠2026-03-15" in off["why"], off


def test_resolve_expect_date_两种题面写法都要认():
    assert ev.resolve_expect_date({"expect_date": "2026-09-30", "as_of": "2026-09-25"}) == "2026-09-30"
    assert ev.resolve_expect_date({"as_of": "2026-10-05", "expect_offset_days": -7}) == "2026-09-28"
    assert ev.resolve_expect_date({"as_of": "2026-10-05", "expect_offset_days": 0}) == "2026-10-05"
    for empty in ({}, {"as_of": "2026-10-05"}, {"expect_date": ""}, None, "2026-10-05",
                  {"as_of": "坏值", "expect_offset_days": 3}):
        assert ev.resolve_expect_date(empty) == "", empty     # 缺锚点＝不判日期，不许瞎猜
    # expect_date 优先于 offset（两者同时出现时题面写死的日期是权威）
    assert ev.resolve_expect_date(
        {"expect_date": "2026-03-01", "as_of": "2026-10-05", "expect_offset_days": 10}) == "2026-03-01"


def test_J1_NONE_期望_产出任何动作都判不过():
    assert ev.j1_verdict({"action_type": "NONE"}, _acts("[MEMO]用户喜欢下雨天[/MEMO]"))["pass"] is False
    assert ev.j1_verdict({"action_type": "NONE"}, _acts("下雨天确实舒服。"))["pass"] is True


def test_J1_没标动作类型时判不了_必须显式报错而不是算过():
    """阳性对照的反面：判分器遇到「没出完的题」必须给 E_unannotated，不能返回 pass=True。"""
    for spec in ({}, {"field_match": {"*": "朵朵"}}, {"action_type": ""}):
        v = ev.j1_verdict(spec, _acts("[MEMO]朵朵[/MEMO]"))
        assert v["pass"] is False and v["err"] == "E_unannotated", (spec, v)


# ─────────────────────────── J2：语义槽落库 ───────────────────────────
def test_J2_槽位值命中判过_缺槽与值不对都判不过():
    written = {"user_fact_health": "用户腰伤忌久站", "user_fact_job": "程序员"}
    assert ev.j2_verdict({"slots": {"user_fact_health": "腰伤"}}, written)["pass"] is True
    v_miss = ev.j2_verdict({"slots": {"user_fact_relationship": "表弟"}}, written)
    assert v_miss["pass"] is False and "缺槽" in v_miss["why"], v_miss
    v_val = ev.j2_verdict({"slots": {"user_fact_health": "膝伤"}}, written)
    assert v_val["pass"] is False and v_val["err"] == "E4", v_val


def test_J2_越权落库要抓到():
    v = ev.j2_verdict({"slots": {"user_fact_health": "腰伤"},
                       "slots_none": ["user_fact_relationship"]},
                      {"user_fact_health": "腰伤忌久站", "user_fact_relationship": "有个表弟"})
    assert v["pass"] is False and "不该写却写了 user_fact_relationship" in v["why"], v


def test_J2_空槽位字典不得算过():
    assert ev.j2_verdict({"slots": {"user_fact_health": "腰伤"}}, {})["pass"] is False


def test_J2_没标slots时判不了_必须显式报错而不是算过():
    """与 J1 同一条纪律：判分器不许把「没标注」读成「没有要求 ⇒ 通过」。"""
    for exp in ({}, {"slots": {}}, {"slots_none": []}):
        v = ev.j2_verdict(exp, {"user_fact_health": "腰伤忌久站"})
        assert v["pass"] is False and v["err"] == "E_unannotated", (exp, v)


# ─────────────────────────── lint：题没出完必须拦住 ───────────────────────────
def _case(**kw):
    base = {"cid": "T-1", "category": "fact", "provenance": "synthetic",
            "persona": {"user": "化名『小舟』", "character": "评测专用角色"},
            "seeds": [{"content": "用户女儿叫朵朵", "memory_type": "fact", "importance": 70,
                       "date": "2026-08-11", "source": "chat", "epistemic_status": "USER_STATED"}],
            "distractors": [{"content": "用户曾在南京读大学", "date": "2026-09-02"},
                            {"content": "用户老家在长沙", "date": "2026-09-02"}],
            "turn": "帮我记一下周六别安排太早", "context_before": [],
            "expect": {"gold_seed_idx": [0], "abstain": False,
                       "action_type": None, "field_match": {}, "forbidden": []},
            "judge": "J3", "time_anchor": {"as_of": "2026-09-25", "tz": "Asia/Shanghai",
                                           "expect_date": None, "expect_offset_days": None},
            "solvability": {"certified": True, "certified_at": "2026-10-05"},
            "config_matrix": ["baseline"], "flags_required": {}, "tokens_budget": 300}
    base.update(kw)
    return base


def _expect(**kw):
    e = {"gold_seed_idx": [0], "abstain": False, "action_type": None, "field_match": {}, "forbidden": []}
    e.update(kw)
    return e


def test_lint_J1J2标注不全的几种情形逐项咬():
    probes = [
        (_case(judge="J1"), "action_type 缺失"),
        (_case(judge="J1", expect=_expect(action_type="MEMO")), "只标了动作类型"),
        (_case(judge="J1", expect=_expect(action_type="MEMO", field_match={"*": "朵朵"})), None),
        (_case(judge="J1", expect=_expect(action_type="CAL_NOTE")), None),   # 题面带锚点日期 ⇒ 算出完
        (_case(judge="J2", expect=_expect()), "都没标"),
        (_case(judge="J2", expect=_expect(slots={"user_fact_health": "腰伤"})), None),
    ]
    for case, frag in probes:
        if frag is None and case["judge"] == "J1" and case["expect"].get("action_type") == "CAL_NOTE":
            case["time_anchor"] = {"as_of": "2026-09-25", "expect_date": "2026-09-30"}
        bad = [b for b in ev.lint_dataset([case]) if b.startswith(case["cid"])]
        if frag is None:
            assert not bad, f"标注齐全的 {case['judge']} 题被误拦：{bad}"
        else:
            assert any(frag in b for b in bad), f"期望被「{frag}」拦住，实得 {bad}"


def test_lint_未知动作类型必须拦_拼错的动作名不能让题永远判不过():
    bad = ev.lint_dataset([_case(judge="J1", expect=_expect(action_type="MEMORISE",
                                                            field_match={"*": "朵朵"}))])
    assert any("不是已知动作类型" in b for b in bad), bad


def test_lint_不许误伤既有的J3题():
    """77 条正式集里 6 条早就标了 action_type，但 judge 仍是 J3 ⇒ lint 不能因此报违规。"""
    import json
    from pathlib import Path
    ds = Path(str(SCRIPT).replace("memory_action_eval.py", "memory_action_cases_zh.jsonl"))
    cases = [json.loads(l) for l in ds.read_text(encoding="utf-8").splitlines() if l.strip()]
    with_action = [c for c in cases if c["expect"].get("action_type")]
    assert with_action, "正式集里已标 action_type 的题不见了 ⇒ 这条守卫失去意义"
    assert ev.lint_dataset(cases) == [], ev.lint_dataset(cases)[:3]


def test_漂移守卫_KNOWN_ACTION_TYPES必须等于生产ACTION_TYPES():
    from app.agent.actions import ACTION_TYPES
    assert ev.KNOWN_ACTION_TYPES == set(ACTION_TYPES), (
        "生产新增了动作类型而 lint 的白名单没跟上 ⇒ 新题会被判不合法；"
        "或反过来：白名单里有生产没有的类型 ⇒ 那条题永远判不过")


def test_漂移守卫_parse_actions产出的载荷字段必须都在判分器视野里():
    """新字段没纳入 `_J1_PAYLOAD_KEYS` ＝ J1 看不见它 ⇒ 「参数对不对」静默退化成「有没有产出动作」。"""
    samples = {
        "SEARCH": "[SEARCH]朵朵 比赛[/SEARCH]",
        "RECALL": "[RECALL]朵朵学琴[/RECALL]",
        "MEMO": "[MEMO]女儿叫朵朵[/MEMO]",
        "CAL_NOTE": "[CAL_NOTE]2026-03-15 接孩子[/CAL_NOTE]",
        "NOTE_DONE": "[CAL_DONE]接孩子[/CAL_DONE]",
        "TIMER": "[timer:20m]",                          # 载荷只有 tag —— 漏掉它就会漏判（变异实测过）
        "STATUS_UPDATE": "【状态更新：在开会】",
    }
    visible = set(ev._J1_PAYLOAD_KEYS) | {ev._J1_DATE_FIELD} | set(ev._J1_META_KEYS)
    # 两份键的**划分本身**也要钉住：只写「派生自常量」的检查，删掉一个 meta 键是测不出来的
    # （变异 N10 就是这样漏的：tag 从 _J1_META_KEYS 拿掉，visible 跟着变小，断言照样过）。
    assert set(ev._J1_PAYLOAD_KEYS) == {"query", "text", "prompt", "match"}, \
        "内容键集合变了要连判据与注释一起改，不能只动常量"
    assert ev._J1_META_KEYS == {"type", "tag"}, "判别键集合变了＝有动作类型的载荷开始被判分器无视"
    for want, text in samples.items():
        acts = [a for a in _acts(text) if a.action_type == want]
        assert acts, f"样本标记没被生产解析器认出来：{want} ← {text!r}（口径变了要回来同步本用例）"
        for a in acts:
            extra = set((a.payload or {}).keys()) - visible
            assert not extra, (
                f"{want} 的载荷字段 {sorted(extra)} 既不在内容键 {sorted(ev._J1_PAYLOAD_KEYS)} "
                f"也不在判别键 {sorted(ev._J1_META_KEYS)} ⇒ J1 看不见它")


DRAFT = REPO / "scripts" / "diagnostics" / "memory_action_cases_j1j2_draft.jsonl"


def test_A30出题草稿必须lint零违规_且判据分布如登记():
    """草稿（12 条，B 主干口径）进不了正式集，但**必须先过 lint**：
    题面一旦不合法，勾选题的人看不出来，接上线跑分会把废题当成退化。

    注意与 `memory_action_cases_draft.jsonl` 的区别：那份是**故意**留着几条坏题给 lint 反证用的
    （见 test_memory_action_eval_v3.py 的「草稿池必须被 lint 咬住」），这份是给人勾选的，必须干净。
    """
    import json
    assert DRAFT.exists(), "A30 草稿不见了（生成器 output/a30_build_j1j2_draft.py）"
    cases = [json.loads(l) for l in DRAFT.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(cases) == 12, len(cases)
    assert ev.lint_dataset(cases) == [], ev.lint_dataset(cases)[:5]
    assert sum(1 for c in cases if c["judge"] == "J1") == 10
    assert sum(1 for c in cases if c["judge"] == "J2") == 2
    # 每题都必须真的把标注出完（lint 已核，这里再钉一次「没有一题靠默认值蒙过去」）
    for c in cases:
        e = c["expect"]
        if c["judge"] == "J1":
            assert e.get("action_type"), c["cid"]
        else:
            assert e.get("slots") or e.get("slots_none"), c["cid"]
        assert c["solvability"]["certified"] is False, \
            f"{c['cid']} 不能提前打认证勾——三跑认证要等生成侧接线"
    # 时间题必须能从题面锚点算出绝对日期（否则 date 判据永远不生效）
    temporal = [c for c in cases if c["category"] == "temporal"]
    assert len(temporal) == 4
    for c in temporal:
        assert ev.resolve_expect_date(c["time_anchor"]), c["cid"]


def test_A30草稿的日期锚点覆盖两种写法():
    """`expect_date` 与 `as_of + expect_offset_days` 两种题面写法在草稿里都要有实例，
    这样 `resolve_expect_date` 的两条分支都被真实数据走一遍。"""
    import json
    cases = [json.loads(l) for l in DRAFT.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_e = [c for c in cases if (c["time_anchor"] or {}).get("expect_date")]
    by_o = [c for c in cases if (c["time_anchor"] or {}).get("expect_offset_days") is not None]
    assert by_e and by_o, (len(by_e), len(by_o))
    assert ev.resolve_expect_date(by_o[0]["time_anchor"]) != ""


def test_跑分器即使给了allow_llm也拒绝跑J1J2(monkeypatch):
    """关掉「授权了就照跑」这条静默错路：生成侧没接线时，J1/J2 的题会走 J3 检索尺子＝拿错尺子还有分。"""
    import asyncio
    for argv_judges in ("J1", "J1,J2", "J2"):
        monkeypatch.setattr(ev.sys, "argv", ["memory_action_eval.py", "--dataset", "x.jsonl",
                                             "--judges", argv_judges, "--allow-llm"])
        assert asyncio.run(ev.main()) == 2, f"--allow-llm 下 {argv_judges} 竟然被放行了"
