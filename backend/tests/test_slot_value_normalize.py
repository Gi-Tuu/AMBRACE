# -*- coding: utf-8 -*-
"""槽值规范化 L1（2026-09-19）——slot_guard.normalize_slot_value 回归用例。

交接口径：只做**形态归一**（去转述主语、剥结尾标点、砍连接词尾巴、按锚点挑分句），
放行与否仍由既有 slot_value_reject_reason 裁决；归一不出值返回 None，调用方（extractor）
原样传 val → 槽闸照旧拒写（fail-closed）。

最终规则（09-19 Codex 复核定稿）：
a) 空 → None；
b) 去转述主语前缀（最多两轮）+ 其后谓语（表示/说/觉得/认为/希望/透露）；
c) 剥结尾句末标点；
d) **叙述尾巴先砍**：以连接词起首的收尾分句（「…，因为…」「…，所以…」）整段丢掉（只砍尾部）；
e) **整串优先**：砍完若整串本身能过闸就直接用（避免「有课，时间赶」被裁成「有课」丢信息）；
f) 整串不过闸才取分句：**锚点命中数最多者胜、并列取最左**，跳过连接词起首的段与超长段；
g) 归一后仍 >30 字 → None（不硬截断词语）；结尾再 strip + 剥标点。

语义落槽（值该不该进这个槽）不是 L1 的职责：SLOT 由提取器给，闸只判形态。要更准走 L2（提示词直出短值）。
"""
import pytest

from app.memory.slot_guard import (
    normalize_slot_value,
    slot_value_reject_reason,
    slot_value_write_ok,
)


# ── 必须能写进槽（L1 的正题：解冻） ────────────────────────────────────────────

_WRITABLE = (
    ("relationship", "用户与sam是伴侣关系，sam是用户的老公。", "与sam是伴侣关系"),
    ("relationship", "单身多年，最近脱单有了对象", "最近脱单有了对象"),   # 锚点密度：脱单+对象 > 单身
    ("living", "用户住在海珠区，室友是新疆人", "住在海珠区，室友是新疆人"),  # 整串优先
    ("health", "今天头疼、发烧、浑身没劲", "今天头疼、发烧、浑身没劲"),      # 整串优先（并置信息不丢）
    ("job", "有课，时间赶", "有课，时间赶"),                              # 整串优先
    ("job", "用户今晚有课，时间赶", "今晚有课，时间赶"),
    ("goal_state", "用户准备比赛作品，因为他赢了比赛", "准备比赛作品"),      # 叙述尾巴被砍
    ("health", "用户今天没去上课，因为生病了", "今天没去上课"),              # 叙述尾巴被砍
)


@pytest.mark.parametrize("slot,raw,expected", _WRITABLE)
def test_normalize_produces_writable_slot_value(slot, raw, expected):
    got = normalize_slot_value(slot, raw)
    assert got == expected
    assert slot_value_write_ok(slot, got) is True, got


# ── 短值/无锚点等：不越权、不硬截断 ────────────────────────────────────────────

_UNTOUCHED = (("job", "大二在读"), ("health", "吃药了"), ("living", "住学校宿舍"))


@pytest.mark.parametrize("slot,raw", _UNTOUCHED)
def test_short_values_untouched(slot, raw):
    assert normalize_slot_value(slot, raw) == raw


_RETURN_NONE = (
    ("health", ""),
    ("health", "   "),
    ("goal_state", "……"),
    # 无锚点长句（>30 字且每段都剪不动）→ None（规则 g：不硬截断词语）
    ("job", "用户今天在图书馆看了很久的书，安静地坐了一整个下午，直到天黑了才回去"),
)


@pytest.mark.parametrize("slot,raw", _RETURN_NONE)
def test_returns_none_for_unnormalizable(slot, raw):
    assert normalize_slot_value(slot, raw) is None


# ── 形态安全：结果永远不含连接词尾巴，且要么 None 要么由闸定夺 ──────────────────

_NARRATION = (
    ("goal_state", "用户今天心情很好，因为他赢了比赛"),
    ("health", "用户今天没去上课，因为生病了"),
    ("goal_state", "用户准备比赛作品，因为他赢了比赛"),
)


@pytest.mark.parametrize("slot,raw", _NARRATION)
def test_connective_tail_never_survives(slot, raw):
    got = normalize_slot_value(slot, raw)
    if got is None:
        return
    assert not got.startswith(("因为", "所以", "然后", "而且", "但是", "不过", "可是"))
    assert "，因为" not in got and "，所以" not in got
    # 形态安全：归一结果要么由闸放行、要么由闸拒绝——L1 不替闸做决定
    assert slot_value_reject_reason(slot, got) is None or slot_value_write_ok(slot, got) is False


# ── 旗舰样本（交接点名）与幂等 ────────────────────────────────────────────────

def test_flagship_relationship_case_end_to_end():
    raw = "用户与sam是伴侣关系，sam是用户的老公。"
    assert slot_value_write_ok("relationship", raw) is False          # 原样进槽照旧被拒
    norm = normalize_slot_value("relationship", raw)
    assert norm == "与sam是伴侣关系"
    assert slot_value_write_ok("relationship", norm) is True          # 规范化后可写


def test_normalize_is_idempotent_and_never_returns_blank():
    cases = [(s, r) for s, r, _ in _WRITABLE] + list(_UNTOUCHED) + list(_NARRATION) + list(_RETURN_NONE)
    for slot, raw in cases:
        got = normalize_slot_value(slot, raw)
        if got is None:
            continue
        assert got and got == got.strip()
        assert normalize_slot_value(slot, got) == got

