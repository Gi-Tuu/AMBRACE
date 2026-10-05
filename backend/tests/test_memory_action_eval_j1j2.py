"""批 0-4 · A30：J1／J2 判分器的自测（**零 LLM、零计费**）。

为什么能在没出题、没接生成侧的情况下先测判分器：
`j1_verdict` / `j2_verdict` 都是纯函数——给它「模型输出解析出来的动作列表」或「落库槽位字典」
就能判。生成侧接线与出题是另外两件事（要计费端点＋产品口径），但**尺子必须先自证可信**
（方案 §五 R6；本轮 A25/A26/A29 三次都是栽在尺子上而不是被测对象上）。

本文件钉住四件事：
1. E3（没产出动作）与 E4（产出了但参数错）**必须分得开**——混成一类就等于放弃了
   「用了错记忆比不用记忆更危险」这条判断；
2. 归一化口径与 J3 同源（全半角／空白差异不得改变判定）；
3. lint 对「声明了 J1/J2 却没出完标注」的题必须当场拦（否则它会静默走 J3 检索尺子＝拿错尺子还有分）；
4. 漂移守卫：`KNOWN_ACTION_TYPES` 必须等于生产 `ACTION_TYPES`；
   `parse_actions` 实际产出的载荷字段必须都在判分器看得见的键里（新字段没纳入＝静默判不了）。
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
    v = ev.j1_verdict({"actions": ["MEMO"], "text_has": ["朵朵"]},
                      _acts("好，我记下来。[MEMO]女儿朵朵明天钢琴比赛[/MEMO]"))
    assert v["pass"] is True and v["err"] == "", v


def test_J1_完全没产出动作算E3_产出了但参数错算E4():
    """E3/E4 必须分开：E4 是「拿错记忆真的去落库」，比 E3 危险，不能并成一类。"""
    no_action = ev.j1_verdict({"actions": ["MEMO"], "text_has": ["朵朵"]},
                              _acts("好，我记下来啦。"))
    wrong_fact = ev.j1_verdict({"actions": ["MEMO"], "text_has": ["朵朵"]},
                               _acts("[MEMO]儿子明天钢琴比赛[/MEMO]"))
    assert no_action["pass"] is False and no_action["err"] == "E3", no_action
    assert wrong_fact["pass"] is False and wrong_fact["err"] == "E4", wrong_fact
    assert "朵朵" in wrong_fact["why"], "E4 的 why 必须点名缺了哪个片段，否则报告没法看"


def test_J1_禁用片段命中必判不过():
    """supersede 类（旧值绝不该再出现）靠 text_has_none 表达。"""
    v = ev.j1_verdict({"actions": ["MEMO"], "text_has": ["腰伤已好转"], "text_has_none": ["忌久站"]},
                      _acts("[MEMO]用户腰伤已好转，还像以前一样忌久站[/MEMO]"))
    assert v["pass"] is False and v["err"] == "E4" and "禁用片段命中:忌久站" in v["why"], v


def test_J1_归一化与J3同源_全半角与空白不改判定():
    spec = {"actions": ["MEMO"], "text_has": ["朵朵 钢琴"]}
    assert ev.j1_verdict(spec, _acts("[MEMO]女儿朵朵　钢琴比赛[/MEMO]"))["pass"] is True, \
        "全角空格／多空白应当视作同一内容"
    assert ev.j1_verdict({"actions": ["memo"], "text_has": ["朵朵"]},   # 小写动作名也要能用
                         _acts("[MEMO]朵朵[/MEMO]"))["pass"] is True


def test_J1_date_按绝对日期逐字对齐_且换算基准写进注释():
    """CAL_NOTE 的日期由生产函数换算（相对词→绝对）。锚点问题在 A30 任务书里，不在这里偷偷解决。"""
    ok = ev.j1_verdict({"actions": ["CAL_NOTE"], "date": "2026-03-15"},
                       _acts("[CAL_NOTE]2026-03-15 接孩子放学[/CAL_NOTE]"))
    assert ok["pass"] is True, ok
    off = ev.j1_verdict({"actions": ["CAL_NOTE"], "date": "2026-03-15"},
                        _acts("[CAL_NOTE]2026-03-14 接孩子放学[/CAL_NOTE]"))
    assert off["pass"] is False and off["err"] == "E4" and "date≠2026-03-15" in off["why"], off


def test_J1_NONE_期望_出现动作就判不过():
    v = ev.j1_verdict({"actions": ["NONE"], "of": ["MEMO"]}, _acts("[MEMO]用户喜欢下雨天[/MEMO]"))
    assert v["pass"] is False and v["err"] == "E4", v
    assert ev.j1_verdict({"actions": ["NONE"], "of": ["MEMO"]}, _acts("下雨天确实舒服。"))["pass"] is True


def test_J1_没标动作类型时判不了_必须显式报错而不是算过():
    """阳性对照的反面：判分器遇到「没出完的题」必须给 E_unannotated，不能返回 pass=True。"""
    for spec in ({}, {"text_has": ["朵朵"]}, {"actions": []}):
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
    for j2 in ({}, {"slots": {}}, {"slots_none": []}):
        v = ev.j2_verdict(j2, {"user_fact_health": "腰伤忌久站"})
        assert v["pass"] is False and v["err"] == "E_unannotated", (j2, v)


# ─────────────────────────── lint：题没出完必须拦住 ───────────────────────────
def _case(**kw):
    base = {"cid": "T-1", "category": "fact", "provenance": "chat", "persona": "p",
            "seeds": ["用户女儿叫朵朵"], "distractors": ["无关内容一", "无关内容二"],
            "turn": "我女儿明天有什么安排", "context_before": [], "expect": {"gold_seed_idx": [0]},
            "judge": "J3", "time_anchor": "2026-03-10", "solvability": {"certified": True, "certified_at": "2026-10-05"},
            "config_matrix": ["baseline"], "flags_required": {}, "tokens_budget": 300}
    base.update(kw)
    return base


def test_lint_J1J2标注不全的四种情形逐项咬():
    probes = [
        (_case(judge="J1"), "actions 缺失/为空"),
        (_case(judge="J1", expect={"gold_seed_idx": [0], "j1": {"actions": ["MEMO"]}}),
         "只标了动作类型"),
        (_case(judge="J1", expect={"gold_seed_idx": [0], "j1": {"actions": ["NONE"]}}),
         "必须写 of="),
        (_case(judge="J1", expect={"gold_seed_idx": [0], "j1": {"actions": ["MEMO"], "text_has": ["朵朵"]}}),
         None),                                                                 # 出完标注 ⇒ 不该再被拦
        (_case(judge="J2", expect={"gold_seed_idx": [0], "j2": {}}), "没标 slots/slots_none"),
        (_case(judge="J2", expect={"gold_seed_idx": [0], "j2": {"slots": {"user_fact_health": "腰伤"}}}),
         None),
    ]
    for case, frag in probes:
        bad = [b for b in ev.lint_dataset([case]) if b.startswith(case["cid"])]
        if frag is None:
            assert not bad, f"标注齐全的 {case['judge']} 题被误拦：{bad}"
        else:
            assert any(frag in b for b in bad), f"期望被「{frag}」拦住，实得 {bad}"


def test_lint_未知动作类型必须拦_拼错的动作名不能让题永远判不过():
    bad = ev.lint_dataset([_case(judge="J1", expect={"gold_seed_idx": [0], "j1": {
        "actions": ["MEMORISE"], "text_has": ["朵朵"]}})])
    assert any("不是已知动作类型" in b for b in bad), bad


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
    visible = set(ev._J1_PAYLOAD_KEYS) | {"date"} | set(ev._J1_META_KEYS)
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
                f"{want} 的载荷字段 {sorted(extra)} 既不在内容键 {sorted(ev._J1_PAYLOAD_KEYS)} 也不在判别键 "
                f"{sorted(ev._J1_META_KEYS)} ⇒ J1 看不见它，「参数对不对」会静默退化成「有没有产出动作」")


def test_跑分器即使给了allow_llm也拒绝跑J1J2(monkeypatch):
    """关掉「授权了就照跑」这条静默错路：生成侧没接线时，J1/J2 的题会走 J3 检索尺子＝拿错尺子还有分。"""
    import asyncio
    for argv_judges in ("J1", "J1,J2", "J2"):
        monkeypatch.setattr(ev.sys, "argv", ["memory_action_eval.py", "--dataset", "x.jsonl",
                                             "--judges", argv_judges, "--allow-llm"])
        assert asyncio.run(ev.main()) == 2, f"--allow-llm 下 {argv_judges} 竟然被放行了"
