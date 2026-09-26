# -*- coding: utf-8 -*-
"""承重结构批 B（2026-09-26，中-3 / 低-5）：去重误合并 + gate 第四态。

守的底线（逐条对应 app/scheduling/state_trace.py 与 message_generator.py 的批 B 改动）：
1. **区分性槽位不同 ⇒ 绝不合并**（中-3 的根因）：人名（Sam / Leo）与时间词（昨天 / 今天、
   明早 / 9月27日）正是「不同事件」的标识，旧判据先剥掉再比较 ⇒ 键全等 ⇒ 误并（丢约定/丢频次）；
2. **长句不做相似度**：等长句只差 k 字时 ratio = 1 - k/n，n≥40 时 k=2 已能撞上 0.95，
   故 ``_LONG_KEY_LEN`` 之上只认全等/前缀；
3. **真重复仍要合并**（不得把修复做成「一律不并」）：全等 / 短键前缀 / 近乎逐字短句；
4. 无 ASCII 人名、无时间词差异者**不受本批影响**（沿用原判据，保守判否）；
5. ``select_fact_rows`` / ``select_intent_rows`` 既有行为不回归（同键一条、谓词名额、脏行不抛）；
6. gate 第四态（低-5）：``_load_state_trace`` 三元组第三位区分「查库/构造异常」与「真·拼空」。

纯函数、零 DB、零 LLM（第 ⑥ 组只 monkeypatch 会话工厂，绝不连生产库）。
**用例文本全部为脱敏合成样例**（甲/乙 + 拉丁名 sam/leo），不抄生产库私密原文。
"""
import asyncio

import pytest

from app.scheduling import message_generator as mg
from app.scheduling import state_trace as st

_CHAR = 13
_USER = 1


def _row(predicate, value):
    return {"predicate": predicate, "object_value": value}


def _irow(content):
    return {"content": content}


# ────────────────── ① 时间槽位差异：不同事件，绝不合并 ──────────────────

def test_时间槽位差异_昨天与今天不合并():
    a = "昨天 Sam 来了宿舍一起聊天"
    b = "今天 Sam 来了宿舍一起聊天"
    # 旧判据：剥掉时间后两条键同为「来了宿舍一起聊天」⇒ 误并（丢新近度/频次）
    assert st._dedup_key(a) == st._dedup_key(b), "前提：剥离槽位后键确实全等"
    assert st._texts_duplicate(a, b) is False


def test_绝对日期与相对日期差异_不合并():
    a = "我答应明早给甲带饭"
    b = "我答应9月27日给甲带饭吃"
    assert st._texts_duplicate(a, b) is False, "换日期＝换了那一趟，不是同一件事"


# ────────────────── ② 人名槽位差异：两个约定不得并成一个 ──────────────────

def test_人名槽位差异_两个约定不合并():
    a = "明天要和 Sam 去看电影"
    b = "明天要和 Leo 去看电影"
    assert st._dedup_key(a) == st._dedup_key(b), "前提：剥掉人名后键全等（旧版会并成一个约定）"
    assert st._texts_duplicate(a, b) is False


def test_人名槽位差异_计划段两条都保留():
    out = st.select_intent_rows([_irow("明天要和 Sam 去看电影"),
                                 _irow("明天要和 Leo 去看电影")], limit=5)
    assert [r["content"] for r in out] == ["明天要和 Sam 去看电影", "明天要和 Leo 去看电影"], \
        "两个约定必须各占一条（并掉就等于把其中一个约忘了）"


def test_事实段人名差异两条都保留():
    out = st.select_fact_rows([_row("curated", "昨天 Sam 来了宿舍一起聊天"),
                               _row("curated", "今天 Sam 来了宿舍一起聊天")], limit=8)
    assert len(out) == 2


# ────────────────── ③ 长句只差 2 字宾语：不得靠相似度合并 ──────────────────

_LONG_TPL = ("下午三点我会一直在宿舍里等着那个{X}送过来对吧这样今天就不用再跑一趟门卫室了"
             "省下来的时间还能把周报写完")


def test_长句两字宾语差异_不走相似度路径():
    a = _LONG_TPL.replace("{X}", "快递")
    b = _LONG_TPL.replace("{X}", "外卖")
    ka, kb = st._dedup_key(a), st._dedup_key(b)
    assert len(ka) >= st._LONG_KEY_LEN, f"前提：键长必须过 {st._LONG_KEY_LEN} 阈值（实测 {len(ka)}）"
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, ka, kb).ratio() >= st._TRACE_SIMILARITY, \
        "前提：长句只差 2 字时相似度仍会撞上阈值——正因如此才要 _LONG_KEY_LEN 拦掉"
    assert st._texts_duplicate(a, b) is False


# ────────────────── ④ 真重复仍必须合并（不得过度收紧） ──────────────────

def test_完全相同的两句_仍合并():
    s = "甲最近腰不好需要少坐软沙发"
    assert st._texts_duplicate(s, s) is True


def test_近乎逐字重复的短句_仍合并():
    assert st._texts_duplicate("用户说十一点的那件事记着别忘",
                               "用户说十一点的那件事他记着别忘") is True


def test_标点空格差异规范化后全等_仍合并():
    assert st._texts_duplicate("甲习惯晚睡。", "甲 习惯 晚睡") is True


def test_短键是长键前缀且够长_仍合并():
    short = "我是甲的伴侣"
    long_ = "我是甲的伴侣，会照顾他的腰"
    assert len(st._dedup_key(short)) >= st._TRACE_MIN_CORE
    assert st._texts_duplicate(short, long_) is True


# ────────────────── ⑤ 槽位相同 ⇒ 判据与改造前一致 ──────────────────

def test_槽位相同仅句尾多字_仍合并():
    assert st._texts_duplicate("明天要和 Sam 去看电影", "明天要和 Sam 去看电影啦") is True


def test_同为明早的两条_仍按原判据合并():
    # 两侧时间槽位集合相同（都是「明早」）⇒ 不因本批改动而变化，前缀判据命中
    assert st._texts_duplicate("我答应明早给甲带饭吃", "我答应明早给甲带饭") is True


# ────────────────── ⑥ 无 ASCII 人名/无时间差异者：不回归 ──────────────────

def test_中文名差异_无槽位可剥_保守判否():
    # 中文名不会被 _ASCII_NAME_RE 捕获 ⇒ 走原判据；同模板不同名字在 0.95 下仍不并（既有行为）
    assert st._texts_duplicate("昨天和小明去食堂吃饭", "昨天和小红去食堂吃饭") is False


def test_同模板不同宾语_保守判否():
    assert not st._texts_duplicate("我答应明早给甲带饭", "我答应明早给甲带汤")
    assert not st._texts_duplicate("甲喜欢喝美式咖啡", "甲喜欢喝拿铁咖啡")


# ────────────────── ⑦ select_fact_rows 既有行为不回归 ──────────────────

def test_select_fact_rows_同谓词同键只留一条():
    out = st.select_fact_rows([_row("curated", "甲习惯晚睡。"),
                               _row("curated", "甲 习惯 晚睡"),
                               _row("status", "甲习惯晚睡。")], limit=8)
    assert [r["predicate"] for r in out] == ["curated", "status"], "判重不跨谓词"


def test_select_fact_rows_名额上限():
    rows = [_row("curated", f"近况合成样本甲第{i}号内容") for i in range(10)]
    out = st.select_fact_rows(rows, limit=8, max_per_predicate={"curated": 4})
    assert len([r for r in out if r["predicate"] == "curated"]) == 4
    assert len(st.select_fact_rows(rows, limit=10)) == 10


def test_select_fact_rows_脏行不抛():
    assert len(st.select_fact_rows([_row(None, None), _row("", "  ")], limit=6)) == 1
    assert st.select_fact_rows([], limit=8) == []
    assert st.select_fact_rows(None, limit=8) == []
    assert st.select_fact_rows([_row("curated", "单条甲")], limit=0) == []
    assert st.select_intent_rows([_irow("   "), _irow("正常计划丙")], limit=5) == [{"content": "正常计划丙"}]


# ────────────────── ⑧ 批 B 常量口径 ──────────────────

def test_阈值与长句门槛取值():
    assert st._TRACE_SIMILARITY == 0.95, "中-3：宁少合，阈值由 0.9 收紧到 0.95"
    assert st._LONG_KEY_LEN == 40


def test_dedup_slots_原样返回被剥掉的槽位():
    names, dates = st._dedup_slots("明天 Sam 和 Leo 上午九点见面")
    assert names == frozenset({"sam", "leo"})
    assert "明天" in dates and "上午" in dates and "九点" in dates
    assert st._dedup_slots(None) == (frozenset(), frozenset())


# ────────────────── ⑨ gate 第四态：查库失败 vs 真·拼空（低-5） ──────────────────

class _FakeSession:
    def __init__(self):
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_a):
        return False


def _patch_factory(monkeypatch, factory):
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)


def test_查库失败_第三位为True(monkeypatch):
    """异常被 fail-open 吞掉：空串 + 第三位 True（与真·拼空在数据上分开）。"""
    def _boom():
        raise RuntimeError("db down")
    _patch_factory(monkeypatch, _boom)
    text, ms, err = asyncio.run(mg._load_state_trace(_CHAR, _USER))
    assert text == "", "异常必须收敛为空串（不冒泡）"
    assert err is True, "查库失败必须标记为 True"
    assert ms == 0.0


def test_真拼空_第三位为False(monkeypatch):
    session = _FakeSession()
    _patch_factory(monkeypatch, lambda: session)

    async def _empty(db, **_kw):
        return ""
    monkeypatch.setattr("app.scheduling.state_trace.build_state_trace", _empty)
    text, ms, err = asyncio.run(mg._load_state_trace(_CHAR, _USER))
    assert (text, err) == ("", False), "正常返回但拼空 ⇒ 属于「真·拼空」，不得记成 trace_error"
    assert ms >= 0.0 and session.entered is True


def test_trace非空_第三位为False(monkeypatch):
    session = _FakeSession()
    _patch_factory(monkeypatch, lambda: session)

    async def _ok(db, **_kw):
        return "【当前现状速读】\n- 状态：在加班"
    monkeypatch.setattr("app.scheduling.state_trace.build_state_trace", _ok)
    text, _ms, err = asyncio.run(mg._load_state_trace(_CHAR, _USER))
    assert text.startswith("【当前现状速读】")
    assert err is False


def test_gate常量四态互不冲突():
    assert mg.GATE_TRACE_ERROR == "trace_error"
    states = {mg.GATE_NOT_ALLOWED, mg.GATE_EMPTY_TRACE, mg.GATE_TRACE_ERROR, mg.GATE_INJECTED}
    assert len(states) == 4, f"四态必须各有独立取值：{states}"


if __name__ == "__main__":  # 便于单独跑文件调试
    pytest.main([__file__, "-q"])
