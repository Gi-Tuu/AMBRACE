# -*- coding: utf-8 -*-
"""A38（2026-10-07）承诺信号正则收窄：解 A35 僵尸清理 dry-run 暴露的假命中。

现场（生产库 dry-run，2026-10-07）：「已兑现关闭」3 条里 2 条是假的 ——
- id=175「我要看着用户吃药」＋用户消息「吃了」＝**真命中**；
- id=12「我承诺到点喊用户起床/睡觉」／id=96「回到我手里后，让用户试试能不能撑住三句粗口」
  被裸「到」判成 arrival，再被 10-06 的用户消息「我不是早就到家了吗？」误关。

两处过泛（都在 ``prospective_intent`` 这一份正则里，A37/I6 要求口径唯一，故本单只收窄它）：
① ``ARRIVAL_PAT`` 的裸「到了」：最近 4000 条用户消息命中 48 条，绝大多数不是到达
   （搜到了/知道了/找到了/感受到了/刺激到了/排到了/外卖到了）；
② ``_AWAIT_ARRIVAL_PAT`` 的裸「到」（＋裸「接」）：把「到点喊起床」「回到我手里」「用杯子接」
   都说成「在等一次到达」。

用例正文**取自生产库真实样本**（id=12/96/175 的承诺原文、以及真实用户消息），
另加派单指定的正/反例。判定仍是零 LLM 的字面/正则匹配。
"""

# ─────────── 生产库真实样本（prospective_intents.kind='promise' 正文原文）───────────
_REAL_CLOCK_ALARM = "我承诺到点喊用户起床/睡觉"                       # intent id=12
_REAL_NON_ARRIVAL = "回到我手里后，让用户试试能不能撑住三句粗口"        # intent id=96
_REAL_MEDICATION = "我要看着用户吃药"                                 # intent id=175
_REAL_ARRIVAL_PROMISE = "用户到达目的地后给sam发消息报平安"             # intent id=158（真在等到达）
_REAL_ARRIVAL_PROMISE2 = "用户到了之后在门口等我，我下楼接他并带他去急诊"  # intent id=172
_REAL_RESULT_COMPLEMENT = "我承诺陪用户把体检报告取回来"                 # 「取回来」是补语，不是等到达

# 生产库真实用户消息：曾被裸「到了」当成到达信号
_NOT_ARRIVAL_CORPUS = (
    "搜到了吗？",
    "说错了，不是“找到了吗”，是“知道了吗”",
    "别那么着急嘛，外卖刚到",
    "麦当劳到了，你帮我拿一下",
    "sam，现在到了功能验证阶段了",
    "（喘气）嗯~呼~感受到了，一根烫烫的硬硬的东西正在慢慢推进来...",
    "排到了，现在走",
    "也不至于，只是做工程的预算快到了，不是真没钱，预算而已",
    "放心，只要你乐意给我发信息，我看到了就会回复的",
)

# 生产库真实用户消息：确实的到达信号
_ARRIVAL_CORPUS = (
    "我到家了",
    "总算到家了...",
    "sam，我回来了，没想到我是第一个上台讲的，紧张死我了",
    "我到了，上课期间先不聊天了，我要认真上课",
    "到了",
    "（进门）呐，都在这了",
    "现在坐地铁2号线去转线，转线就回家了，宝宝等我，mua",
)


def _alternatives(pattern: str) -> set[str]:
    body = pattern[1:-1] if pattern.startswith("(") and pattern.endswith(")") else pattern
    return {a for a in body.split("|") if a}


def test_regex_is_narrowed_no_bare_dao():
    """钉住收窄结果：两条到达相关正则里不得再出现单字「到」或裸「到了」（泛匹配的源头）。"""
    from app.scheduling.prospective_intent import ARRIVAL_PAT, _AWAIT_ARRIVAL_PAT

    for pat in (ARRIVAL_PAT.pattern, _AWAIT_ARRIVAL_PAT.pattern):
        alts = _alternatives(pat)
        assert "到" not in alts, alts
        assert "到了" not in alts, alts          # 只允许 ^到了$ 这种整句锚定形态
    assert "^到了$" in _alternatives(ARRIVAL_PAT.pattern)


def test_real_promise_texts_are_not_misclassified_as_arrival():
    """真实样本：id=12（到点喊起床）与 id=96（回到我手里）不再判 arrival；id=175 仍判 medication。"""
    from app.scheduling.prospective_intent import _promise_awaits, classify_intent_trigger

    assert _promise_awaits(_REAL_CLOCK_ALARM) is None                   # clock：不该等到达
    assert classify_intent_trigger(_REAL_CLOCK_ALARM) == "clock"
    assert _promise_awaits(_REAL_NON_ARRIVAL) is None                   # 「回到」不算到达信号形态
    assert classify_intent_trigger(_REAL_NON_ARRIVAL) == "clock"
    assert _promise_awaits(_REAL_MEDICATION) == "medication"
    assert classify_intent_trigger(_REAL_MEDICATION) == "medication"
    assert _promise_awaits(_REAL_ARRIVAL_PROMISE) == "arrival"          # 真在等到达的仍认得出
    assert _promise_awaits(_REAL_ARRIVAL_PROMISE2) == "arrival"
    assert _promise_awaits(_REAL_RESULT_COMPLEMENT) is None             # 「取回来」≠ 等用户到家
    # 生产库同类受害行：外卖到达 ≠ 用户到达，「用杯子接」≠ 去接用户
    assert classify_intent_trigger("用户说外卖到了会叫我") == "clock"      # intent id=48
    assert classify_intent_trigger("白天陪用户喝我的体液，要求用户先排尿、喝够水、用杯子接") == "clock"  # id=64


def test_false_hit_signal_from_real_corpus_gone():
    """假命中的另一半：这些真实用户消息一律不得再被判成到达信号。"""
    from app.scheduling.prospective_intent import _signal_seen

    for text in _NOT_ARRIVAL_CORPUS:
        assert _signal_seen("arrival", text) is False, text


def test_true_arrival_signals_still_hit():
    """正例（生产真实句＋派单指定句）仍算到达信号。"""
    from app.scheduling.prospective_intent import _signal_seen

    for text in _ARRIVAL_CORPUS:
        assert _signal_seen("arrival", text) is True, text
    for text in ("我到家了", "刚回到家里", "我在楼下了", "到门口了"):
        assert _signal_seen("arrival", text) is True, text


def test_dao_ambiguity_negatives():
    """派单指定的反例：「说到做到/收到/迟到/说到这件事/到点了」都不是到达信号。"""
    from app.scheduling.prospective_intent import _signal_seen

    for text in ("我说到做到", "收到", "迟到", "说到这件事", "到点了"):
        assert _signal_seen("arrival", text) is False, text
    # 「到点了」是时钟语义（A33 ⑥ 显式分类），既不判 arrival，正文也不该被写成 arrival 标签
    from app.scheduling.prospective_intent import classify_intent_trigger
    assert _signal_seen("arrival", "到点了，我该喊用户起床了") is False
    assert classify_intent_trigger("到点喊用户起床") == "clock"


def test_medication_pattern_has_no_bare_chi():
    """MED_PAT 实测：没有裸「吃」，只认「药」相关形态与整句「吃了」；日常吃饭句不得算吃药信号。"""
    from app.scheduling.prospective_intent import MED_PAT, _signal_seen

    assert "吃" not in _alternatives(MED_PAT.pattern)
    for text in (
        "sam，我上完课回来了，吃过饭了",
        "刚刚我们都吃完豆腐了，有点饱，你看群里他两都回家了",
        "我到家了",
        "吃了没？",
        "药丸到了",                       # 「药」在到达句里不改 arrival 归属（由 awaits 决定用哪条正则）
    ):
        assert _signal_seen("medication", text) is False, text
    for text in ("吃过药了", "药吃了", "吃了药", "已经吃药", "吃了"):
        assert _signal_seen("medication", text) is True, text


def test_zombie_pair_no_longer_discharged_by_real_message():
    """端到端（纯函数，不碰库）：按正文现算时，id=12/96 不会因「我不是早就到家了吗？」被关掉。

    注：这两行在生产库里**已带 A33 回填的 trigger='arrival' 标签**（``_promise_awaits`` 的
    ``stored`` 优先），所以清理脚本读到的是冻结的旧标签；本用例只证明收窄后「按正文现算」正确。
    """
    from app.scheduling.prospective_intent import _promise_awaits, _signal_seen

    real_message = "我不是早就到家了吗？"
    for content in (_REAL_CLOCK_ALARM, _REAL_NON_ARRIVAL):
        awaits = _promise_awaits(content)
        assert awaits is None, content
        assert _signal_seen("arrival", real_message) is True   # 句子本身确实是到达语义
    med = _promise_awaits(_REAL_MEDICATION)
    assert med == "medication" and _signal_seen(med, "吃了") is True
    assert _signal_seen(med, real_message) is False
