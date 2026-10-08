# -*- coding: utf-8 -*-
"""A39 守卫（一）：`domain/proactivity/freshness.py` 的纯度、零模型、判据口径唯一，以及三档回归。

三件事各钉一个方向，缺一不可：
  ① **纯度**：domain 侧不许 import IO／models（仿 `test_domain_care_purity`）；
  ② **零模型**：判定路径上 `chat_completion` 计数必须为 0——用**计数桩**而不是异常桩，
     因为调用链多处 fail-open，抛异常的桩会被自己吞掉（同 B15/A42 的教训）；
  ③ **不写第四套正则**：freshness 一侧只许 import 既有的 `_signal_seen`／`ready_result_seen`／
     `_story_advanced` 与 `ARRIVAL_PAT`／`MED_PAT`，出现第二份字面表即红。
三档（cancel／regenerate／keep）各至少一例，另加 clock 档窗的现场形态回归（A37 id=160 提前 55′29″）。
"""
import ast
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.domain.proactivity import freshness as fr

DOMAIN_FILE = Path(fr.__file__)
# 从包内位置推，不写作者机绝对路径——写死的那个串正是本仓 C2 扫描器要拦的形态（第 130 棒推前抓到）
SCHED_FILE = DOMAIN_FILE.parents[2] / "scheduling" / "freshness.py"


def _imports(src: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_domain侧零IO():
    src = DOMAIN_FILE.read_text(encoding="utf-8")
    bad = [m for m in _imports(src)
           if m.split(".")[0] in {"sqlalchemy", "fastapi", "app"}
           or re.search(r"models|scheduling|application|llm_client|asyncio|sqlite", m)]
    assert bad == [], f"domain 侧引入了 IO／上层：{bad}"


def test_判定路径零模型调用():
    """真把 chat_completion 装上计数桩，跑一遍全部判定，必须一次都没被调。"""
    import app.agent.llm_client as llm

    calls = []
    real = getattr(llm, "chat_completion", None)
    if real is None:
        pytest.skip("llm_client 未暴露 chat_completion，改口径再看")

    async def _spy(*a, **kw):                                    # noqa: ARG001
        calls.append(1)
        return ""

    llm.chat_completion = _spy
    try:
        base = fr.FreshFacts(now=datetime(2026, 10, 9, 8, 0), due_start=datetime(2026, 10, 9, 8, 0),
                             due_end=datetime(2026, 10, 9, 8, 0), exact_moment=True,
                             signal_seen=True, result_ready=True, topic_active=True,
                             user_replied=True, story_advanced=True, snapshot_age_seconds=999)
        for ch in fr.CHANNEL_ALLOWED_FIELDS:
            fr.decide(ch, base)
            fr.decide(ch, fr.FreshFacts())
    finally:
        llm.chat_completion = real
    assert calls == [], "判定路径里调了模型＝违反 A37 批 2 边界（判定只读现状，不生成）"


def test_调度层包装不新写正则():
    """`scheduling/freshness.py` 只许 import 既有判据与两张字面表，不许自带第四套 pattern。"""
    if not SCHED_FILE.exists():
        pytest.skip("scheduling/freshness.py 尚未落地（本批分步提交）")
    src = SCHED_FILE.read_text(encoding="utf-8")
    assert re.search(r"re\.compile", src) is None, "调度层包装里出现了新的正则字面表"
    for must in ("_signal_seen", "ready_result_seen", "_story_advanced"):
        assert must in src, f"没复用既有判据：{must}"


def test_通道白名单与实现一致():
    """`unknown_channel` 必须只对表外的通道为真；表内通道一个都不许被认成未知。"""
    for ch in fr.CHANNEL_ALLOWED_FIELDS:
        assert fr.unknown_channel(ch) is False, ch
    assert fr.unknown_channel("made_up_channel") is True


# ───────────────────────── 三档回归：cancel／regenerate／keep 各至少一例

def test_cancel_clock未到点_id160现场形态():
    """A37 现网 id=160：19:04 发「明早八点叫你」。

    现场形状＝due_start 被写成当天 00:00、due_end 23:59 ⇒ 旧口径"当天全天可提"。
    新口径＝精确时刻型只允许 [到点-2min, 到点]，19:04 必须 cancel。
    """
    due = datetime(2026, 10, 9, 8, 0)                 # 明早八点
    f = fr.FreshFacts(now=datetime(2026, 10, 8, 19, 4),
                      due_start=datetime(2026, 10, 9, 0, 0),   # 旧代码落成的"当天全天"
                      due_end=due, exact_moment=True)
    verdict, reason = fr.decide("prospective_clock", f)
    assert verdict == fr.CANCEL, f"提前兑现没被拦：{verdict}/{reason}"
    assert "档窗" in reason
    # 反向钉：到点前两分钟之内必须放行（否则这条闸成了"永远不发"）
    ok = fr.FreshFacts(now=due - timedelta(minutes=1), due_start=due, due_end=due, exact_moment=True)
    assert fr.decide("prospective_clock", ok)[0] != fr.CANCEL, "闸变成永远 cancel ⇒ 没有牙只是关掉通道"


def test_cancel_事件信号与结果兑现():
    v1 = fr.decide("prospective_arrival", fr.FreshFacts(signal_seen=True))
    v2 = fr.decide("timer_general", fr.FreshFacts(result_ready=True))
    assert v1[0] == fr.CANCEL and "信号" in v1[1]
    assert v2[0] == fr.CANCEL and "兑现" in v2[1]
    # 反向钉：没看到信号时不许取消
    assert fr.decide("prospective_arrival", fr.FreshFacts(signal_seen=False))[0] != fr.CANCEL


def test_cancel_话题已收尾或用户已接着说():
    a = fr.decide("unfinished_topic", fr.FreshFacts(topic_active=False))
    b = fr.decide("unfinished_topic", fr.FreshFacts(user_replied=True))
    assert a[0] == fr.CANCEL and "进行中" in a[1]
    assert b[0] == fr.CANCEL and "复述" in b[1]
    # 反向钉：话题仍进行中且没人接话 ⇒ 不许 cancel（否则这条通道被静默关掉）
    assert fr.decide("unfinished_topic", fr.FreshFacts(topic_active=True))[0] != fr.CANCEL


def test_regenerate_只在快照过期且现状真变时():
    r = fr.decide("state_trigger_delayed",
                  fr.FreshFacts(user_replied=True, snapshot_age_seconds=600))
    assert fr.decide("state_trigger_delayed",
                     fr.FreshFacts(state_changed=True, snapshot_age_seconds=600))[0] == fr.REGENERATE
    assert r[0] == fr.REGENERATE, f"该重写的没重写：{r}"
    # 反向钉①：快照不老 ⇒ keep（不许白白多花一次 LLM）
    assert fr.decide("state_trigger_delayed",
                     fr.FreshFacts(user_replied=True, snapshot_age_seconds=5))[0] == fr.KEEP
    # 反向钉②：老但现状没变 ⇒ 也 keep
    assert fr.decide("state_trigger_delayed",
                     fr.FreshFacts(user_replied=False, snapshot_age_seconds=600))[0] == fr.KEEP
    # 反向钉③：非"会变"的通道不因"老"就重写（所有通道都多烧一次钱＝不可接受）
    assert fr.decide("moment_comment",
                     fr.FreshFacts(user_replied=True, snapshot_age_seconds=600))[0] == fr.KEEP
    # 反向钉④：依据的事实没了 ⇒ cancel，不是 regenerate（cancel 在前，不花钱）
    assert fr.decide("life_regression",
                     fr.FreshFacts(underlying_gone=True, snapshot_age_seconds=600))[0] == fr.CANCEL


def test_keep_是默认档():
    assert fr.decide("prospective_arrival", fr.FreshFacts())[0] == fr.KEEP
    assert fr.decide("life_regression", fr.FreshFacts(snapshot_age_seconds=10))[0] == fr.KEEP


def test_影子标记格式可被脚本解析():
    mark = fr.shadow_mark(fr.CANCEL, "剧情已推进", {"ch": "C09"})
    assert mark.startswith("[fresh=") and "fresh=cancel" in mark and "ch=C09" in mark
    assert mark.count("|") == 2
    # 反向钉：档位与原因都必须进串（只留一个＝判效时无法分档统计）
    assert "剧情已推进" in fr.shadow_mark(fr.CANCEL, "剧情已推进")


def test_三档常量闭包():
    assert fr.VERDICTS == (fr.CANCEL, fr.REGENERATE, fr.KEEP)
    got = {fr.decide(ch, fr.FreshFacts())[0] for ch in fr.CHANNEL_ALLOWED_FIELDS}
    assert got == {fr.KEEP}, f"默认事实下必须全 keep（现状没变），实际 {got}"
