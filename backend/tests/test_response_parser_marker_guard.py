# -*- coding: utf-8 -*-
"""自造标记护栏测试（2026-09-18）：模型自造的 [cron: …] 从此不进可见正文。

现场（session 11，char13，2026-09-18 11:02:38）：
「（搁下围裙，看你一眼）先把饭吃了，别空着肚子等会儿又喊胃疼。[cron: 今晚与轩一起洗澡]」
—— cron 不在任何内核标记约定里（全仓 backend/app 仅本文件的护栏涉及它），属**模型自造**；
剥离器原先只认已登记关键字白名单，故原样上屏。

本文件锁定：①闭口形态剥离；②未闭合尾部剥离；③普通方括号/括号**不受影响**（防误伤）；
④既有「推理」标记剥离不回退；⑤命中时记一条 marker_stripped 埋点（量化频率）。
"""
from app.agent import response_parser as rp


def test_cron_closed_marker_stripped():
    s = "（搁下围裙，看你一眼）先把饭吃了，别空着肚子等会儿又喊胃疼。[cron: 今晚与轩一起洗澡]"
    out = rp.strip_stream_display(s)
    assert "cron" not in out.lower()
    assert "洗澡" not in out
    assert "先把饭吃了" in out


def test_cron_unclosed_tail_stripped():
    s = "先把饭吃了，别空着肚子。[cron: 今晚一起洗"
    assert rp.strip_unclosed_markers(s) == "先把饭吃了，别空着肚子。"


def test_normal_brackets_not_touched():
    """防误伤：普通方括号/中文括号正文一律不动。"""
    s = "（笑）我在呢 [笑] 你说啥（摆手）"
    assert rp.strip_stream_display(s) == s
    assert rp.strip_unclosed_markers(s) == s


def test_reasoning_marker_still_stripped():
    """回归：既有【推理】/标记族剥离不回退。"""
    out = rp.strip_stream_display("好的。[推理] 他其实有点慌")
    assert "推理" not in out
    assert "好的。" in out


def test_cron_strip_emits_marker_stripped_metric(monkeypatch):
    """命中自造标记时记一条埋点（other 标记不记，避免刷爆）。"""
    import app.memory.observability as obs

    seen: list = []
    monkeypatch.setattr(obs, "obs_event", lambda *a, **k: seen.append((a, k)))

    rp.strip_stream_display("先吃饭。[cron: 今晚一起洗]")
    assert seen, "命中 cron 应产生埋点"
    assert "marker_stripped" in str(seen[0][0])

    seen.clear()
    rp.strip_stream_display("好的。[推理] 他有点慌")
    assert not seen, "常规标记不应产生自造标记埋点"
