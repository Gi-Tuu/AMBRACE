# -*- coding: utf-8 -*-
"""M1-S7 复习校准测试：日额度 flag、实质回复判定（纯函数）"""
from app.scheduling.memory_review import _is_substantive_reply, _review_daily_cap


def test_daily_cap_flag_on():
    monkey_flags = {"review_daily_plus": True}

    class _FakeAF(dict):
        pass

    import app.agent.loop as loop_mod
    original = loop_mod.AGENT_FLAGS
    loop_mod.AGENT_FLAGS = {**original, **monkey_flags}
    try:
        assert _review_daily_cap() == 4
    finally:
        loop_mod.AGENT_FLAGS = original


def test_daily_cap_flag_off_falls_back():
    import app.agent.loop as loop_mod
    original = loop_mod.AGENT_FLAGS
    loop_mod.AGENT_FLAGS = {**original, "review_daily_plus": False}
    try:
        assert _review_daily_cap() == 3
    finally:
        loop_mod.AGENT_FLAGS = original


def test_substantive_replies():
    for text in ("好的我知道了", "对，那天我们确实去了", "不用提这个了", "哈哈原来如此", "是吗？我忘了"):
        assert _is_substantive_reply(text) is True, text


def test_non_substantive_replies():
    for text in ("哦", "嗯", "哦哦", "呵呵", "。", "？？", "！", "嗯。", "  ", "", "好"):
        assert _is_substantive_reply(text) is False, text


def test_similarity_still_wins_over_filler_set():
    """相似命中与实质判定互补：非敷衍短词"好耶"算实质；纯语气词走词表排除"""
    assert _is_substantive_reply("好耶") is True
    assert _is_substantive_reply("嗯嗯") is False


# ------------- Sam 主动消息错接昨晚剧情修复（2026-09-08）：F1/F4/F5 纯函数 -------------

def test_cn_now_prefix_fixed_time():
    """F1：北京时间锚点前缀（固定时刻断言；事发时刻 2026-09-08 12:05 = 星期二 中午）。"""
    from datetime import datetime

    from app.scheduling.life_regression import _cn_now_prefix as _prefix_lr
    from app.scheduling.memory_review import _cn_now_prefix as _prefix_mr
    fixed = datetime(2026, 9, 8, 12, 5)
    expect = "现在是北京时间 2026年9月8日 星期二 中午 12:05。"
    assert _prefix_mr(fixed) == expect
    assert _prefix_lr(fixed) == expect


def test_cn_now_prefix_now_nonempty():
    """F1：默认取当前时间——前缀非空、含星期与午别。"""
    from app.scheduling.life_regression import _cn_now_prefix as _prefix_lr
    from app.scheduling.memory_review import _cn_now_prefix as _prefix_mr
    for p in (_prefix_mr, _prefix_lr):
        out = p()
        assert out.startswith("现在是北京时间 ")
        assert "星期" in out
        assert any(w in out for w in ("凌晨", "早上", "上午", "中午", "下午", "晚上", "深夜"))


def test_cn_noon_label_boundaries():
    """F1：午别边界（5/9/11/14/18/23 点切换）。"""
    from app.scheduling.memory_review import _cn_noon_label
    assert _cn_noon_label(0) == "凌晨"
    assert _cn_noon_label(4) == "凌晨"
    assert _cn_noon_label(5) == "早上"
    assert _cn_noon_label(9) == "上午"
    assert _cn_noon_label(11) == "中午"
    assert _cn_noon_label(13) == "中午"
    assert _cn_noon_label(14) == "下午"
    assert _cn_noon_label(18) == "晚上"
    assert _cn_noon_label(23) == "深夜"


def test_recent_context_line_gap_scenarios():
    """F4：≤2h 维持「最近在聊」；>2h 标注「N 小时前 + 北京午别 + 场景已结束」；空上下文返回空。"""
    from datetime import datetime

    from app.scheduling.memory_review import _recent_context_line
    ctx = "用户: 宝宝晚安，抱抱我睡\n你: 嗯，抱紧了。明天要早起，别磨蹭。"
    assert _recent_context_line("", 12, None) == ""
    assert _recent_context_line(ctx, None, None) == "\n你们最近在聊：\n" + ctx + "\n"
    assert _recent_context_line(ctx, 1.5, None) == "\n你们最近在聊：\n" + ctx + "\n"
    last = datetime(2026, 9, 7, 16, 4, 58)  # UTC = 北京 09-08 00:04（昨晚哄睡请求）
    out = _recent_context_line(ctx, 12.1, last)
    assert "12 小时前" in out
    assert "9月8日凌晨" in out
    assert "那段场景已经结束" in out
    assert "不要接着上次的场景续演" in out
    assert ctx in out


def test_is_replay_of_recent_gates():
    """F5-b：复读昨晚哄睡句且与记忆无关 → True；围绕记忆/新话头 → False；空输入放行。"""
    from app.scheduling.memory_review import _is_replay_of_recent
    ctx = "用户: 宝宝晚安，抱抱我睡\n你: 嗯，抱紧了。明天要早起，别磨蹭。"
    mem = "夫妻关系：抖音运营被动收入，别乱发照片"
    assert _is_replay_of_recent("嗯，抱紧了。明天要早起，别磨蹭。", ctx, mem) is True   # 复读哄睡句
    assert _is_replay_of_recent("对了，你抖音那个号后来怎么弄的？", ctx, mem) is False  # 围绕记忆
    assert _is_replay_of_recent("今天中午吃火锅怎么样？", ctx, mem) is False           # 新话头
    assert _is_replay_of_recent("", ctx, mem) is False
    assert _is_replay_of_recent("嗯，抱紧了。明天要早起，别磨蹭。", "", mem) is False
    # 复读句但本身与记忆内容高度重合（记忆相关）→ 不拦（只拦"与最近聊天复读"这一种）
    assert _is_replay_of_recent(
        "嗯，抱紧了。明天要早起，别磨蹭。",
        "你: 嗯，抱紧了。明天要早起，别磨蹭。",
        "睡前说好了明天要早起，别磨蹭，抱紧了") is False
