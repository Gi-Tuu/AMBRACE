# -*- coding: utf-8 -*-
"""A30 批 A：写侧兜底（`app/memory/fallback.py`）的守卫。**零 LLM、零计费、零写库**。

这个文件要咬的是**内容**，不是形状（第 ⑧ 类：守卫咬占位符＝永远没牙）：

1. **不许造句**——兜底写出的每一个字都必须逐字出现在「本轮用户原话」或「召回原文」里；
   这条是兜底的命门：一旦允许它自己组织语言，误记就从"选错条目"升级成"编造事实"。
2. **改口时绝不把作废旧值记回去**（M0 第一次读数就栽在这格：supersede 两题的召回里只有旧值）。
3. **周期约定没有那一个日期**（10-10 批 51 口径）——不许把「每到画室带课那天」换算成某个周三。
4. **零标记才触发**——模型已经落了通道，兜底必须闭嘴（两档读数的边界）。
5. **词表同源**——尺子的周期词必须是兜底那一份，硬编码第二遍当场红。
6. **上限读数有牙**——把改口分支拆掉，上限就要掉下来；不然那个 10/10 是恒真判据（第 ⑫ 类）。
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVAL_SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
REPLAY_SCRIPT = REPO / "scripts" / "diagnostics" / "memory_fallback_replay.py"
DATASET = REPO / "scripts" / "diagnostics" / "memory_action_cases_j1j2_draft.jsonl"

from app.memory.fallback import (  # noqa: E402
    CHANNEL_CAL, CHANNEL_MEMO, PERIODIC_WORDS,
    fallback_actions, new_value_clause, resolve_date_phrase)


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load(EVAL_SCRIPT, "_fallback_eval_under_test")
rp = _load(REPLAY_SCRIPT, "_fallback_replay_under_test")
CASES = rp.load_cases(str(DATASET))
CASE_BY_CID = {c["cid"]: c for c in CASES}


def _out(turn, recall=(), have=(), base="2026-09-25"):
    return fallback_actions(turn, recall_texts=list(recall),
                            model_channels=set(have), base_date=base)


def _bodies(got):
    return [str(a.payload.get("text") or "") for a in got["actions"]] + list(got["memory_texts"])


# ───────── 1. 不许造句（命门） ─────────
@pytest.mark.parametrize("cid", sorted(CASE_BY_CID))
def test_兜底正文必须逐字来自本轮原话或召回原文(cid):
    case = CASE_BY_CID[cid]
    turn, recall = case.get("turn") or "", rp.recall_of(case)
    got = _out(turn, recall, base=rp.anchor_of(case))
    for body in _bodies(got):
        plain = body.replace("重复约定：", "")
        assert plain in turn or any(plain in r for r in recall), (cid, body)


def test_没有用户下令时兜底一个字都不补():
    got = _out("今天天气不错，我在地铁上", ["用户女儿叫朵朵"])
    assert got["actions"] == [] and got["memory_texts"] == []
    assert CHANNEL_CAL in got["skipped"], got["skipped"]


def test_模型已产出该通道时兜底不许重复补():
    got = _out("下个月 28 号提醒我对一下账", ["用户每月 28 号结项目款"], have={CHANNEL_CAL})
    assert got["actions"] == []
    assert got["skipped"].get(CHANNEL_CAL) == "模型已产出该通道"


# ───────── 2. 改口不得回灌旧值 ─────────
def test_改口时正文取本轮原话_绝不把作废的旧值记回去():
    got = _out("我最近胃不行，辣椒已经忌口了，按这个来记一下", ["用户以前无辣不欢"])
    body = got["memory_texts"][0]
    assert "忌口" in body, body
    assert "无辣不欢" not in body, "把作废旧值写回库＝用户最不能原谅的误记形状"


def test_周期命中时不算改口_正文仍取召回():
    got = _out("以后每到画室带课那天，别约我下午的事", ["用户的画室周三上午带课"])
    assert "周三" in got["actions"][0].payload["text"], got["actions"][0].payload


# ───────── 3. 周期没有那一个日期 ─────────
def test_周期约定永远不塞具体日期():
    got = _out("以后每到画室带课那天，别约我下午的事", ["用户的画室周三上午带课"])
    cal = [a for a in got["actions"] if a.action_type == CHANNEL_CAL][0]
    assert cal.payload["date"] == "", cal.payload
    assert set(cal.payload) == {"date", "text"}, "载荷形状只许一套"


@pytest.mark.parametrize("word", PERIODIC_WORDS)
def test_每个周期词都走周期分支不落在改口与日期上(word):
    turn = "%s都要提醒我说一声" % word
    assert new_value_clause(turn) == "", "周期不是改口，词表要全"
    assert resolve_date_phrase(turn, "2026-09-25") == ""


# ───────── 4. 日期换算跟锚点走，不跟本机日历 ─────────
@pytest.mark.parametrize("turn,base,expect", [
    ("周六别给我安排太早的事", "2026-09-25", "2026-09-26"),
    ("过三天我要去城北", "2026-09-25", "2026-09-28"),
    ("下个月 28 号提醒我对一下账", "2026-09-25", "2026-10-28"),
    ("下个月 28 号提醒我对一下账", "2026-12-30", "2027-01-28"),
    ("明天记得带报告", "2026-09-25", "2026-09-26"),
])
def test_日期短语按锚点换算(turn, base, expect):
    assert resolve_date_phrase(turn, base) == expect


def test_认不出日期时不许瞎猜():
    assert resolve_date_phrase("有空的话提醒我说一声", "2026-09-25") == ""
    got = _out("有空的话提醒我说一声", ["用户喜欢喝咖啡"])
    assert got["actions"] == [] and "认不出日期" in got["skipped"][CHANNEL_CAL]


# ───────── 5. 召回选择：宁缺毋滥 ─────────
def test_多条召回并列或对不上时不补():
    got = _out("帮我把那件事记一下", ["用户周一要交周报", "用户爱喝美式"])
    assert got["memory_texts"] == [] and got["actions"] == []


def test_单条召回直接用():
    got = _out("学校要填接送牌，写我家娃的名字，你先把她的名字记进备忘",
               ["用户女儿叫『朵朵』，2021 年出生"])
    assert "朵朵" in got["actions"][0].payload["text"]


def test_只说明示备忘才写小手机_其余只落记忆通道():
    """一轮只补一处：两处都写＝用户在同屏看见两条重复，那是会被当 bug 报的误记。"""
    memo = _out("你先把她的名字记进备忘", ["用户女儿叫朵朵"])
    mem = _out("辣椒已经忌口了，按这个来记一下", ["用户以前无辣不欢"])
    assert [a.action_type for a in memo["actions"]] == [CHANNEL_MEMO]
    assert mem["actions"] == [] and mem["memory_texts"], mem


# ───────── 6. 幂等 / fail-open ─────────
def test_同一输入两次结果逐字一致():
    args = ("过三天我要去城北，那天早上提醒我早点吃", ["用户三天后要去城北体检"], set(), "2026-09-25")
    assert _bodies(_out(*args)) == _bodies(_out(*args))


@pytest.mark.parametrize("bad", [None, 123, "", [], {}, "   "])
def test_坏输入一律不抛且不补(bad):
    got = fallback_actions(bad, recall_texts=["用户女儿叫朵朵"],
                           model_channels=(), base_date="2026-09-25")
    assert got["actions"] == [] and got["memory_texts"] == []


def test_召回里混进坏条目也不抛():
    got = _out("把娃的名字记进备忘", [None, 42, "用户女儿叫朵朵"])
    assert "朵朵" in "".join(_bodies(got)), got


# ───────── 7. 词表同源（抄第二遍当场红） ─────────
def test_尺子的周期词就是兜底那一份():
    assert tuple(ev._periodic_words()) == tuple(PERIODIC_WORDS)
    src = EVAL_SCRIPT.read_text(encoding="utf-8-sig")
    assert '"每到", "每逢"' not in src, "尺子里硬编码周期词＝假同源（改一边另一边不会红）"


# ───────── 8. 上限读数有牙 ─────────
def test_M0零产出上限钉住十题全过():
    passed, total, detail = rp.upper_bound(CASES)
    assert total == 10 and passed == 10, [(d["cid"], d["err"], d["why"]) for d in detail]


def test_拆掉改口分支上限必须掉下来(monkeypatch):
    """反向钉：上限若是恒真判据（怎么拆都 10/10），这条会红。"""
    import app.memory.fallback as fb
    monkeypatch.setattr(fb, "NEW_VALUE_WORDS", ())
    passed, total, _ = rp.upper_bound(CASES)
    assert passed < total, "拆掉改口分支还满分 ⇒ 上限读数没有牙"


def test_周期分支真的靠这张词表判定(monkeypatch):
    """反向钉：把词表掏空，周期判定必须当场失效——否则说明"周期不塞日期"根本没吃这张表（摆设）。"""
    import app.memory.fallback as fb
    turn = "以后每到画室带课那天，别约我下午的事"
    assert fb.is_periodic(turn) is True
    monkeypatch.setattr(fb, "PERIODIC_WORDS", ())
    assert fb.is_periodic(turn) is False, "周期判定没消费这张词表＝词表是第二份死字"


def test_兜底不覆盖模型已经写错的东西():
    """E4（产出了但参数不对）不该被兜底悄悄改掉——那是另一件事，得单独拍板。"""
    row = {"cid": "j1t04", "run": 1, "pass": False, "err": "E4", "sig": "S-MODEL", "other_runs": []}
    proj = rp.certify_projection([CASE_BY_CID["j1t04"]], [row])
    assert proj["per_case"][0]["sources"] == "模型", proj


def test_三跑全没产出时兜底让这一题逐字一致():
    """G2 的形状：系统写的文本三跑同形 ⇒ 全是 E3 的题在推算里应当认证过。"""
    rows = [{"cid": "j1t01", "run": 1, "pass": False, "err": "E3", "sig": "M1",
             "other_runs": [{"pass": False, "err": "E3", "sig": "M2"},
                            {"pass": False, "err": "E3", "sig": "M3"}]}]
    proj = rp.certify_projection([CASE_BY_CID["j1t01"]], rows)
    p = proj["per_case"][0]
    assert p["pass_all"] and p["consistent"] and p["cert"], p


def test_混合来源在逐字一致这一维必然不一致():
    """把这条写死，免得以后有人以为兜底能把一致率抬上去。"""
    rows = [{"cid": "j1s01", "run": 1, "pass": True, "err": "", "sig": "M1",
             "other_runs": [{"pass": False, "err": "E3", "sig": "M2"},
                            {"pass": False, "err": "E3", "sig": "M3"}]}]
    proj = rp.certify_projection([CASE_BY_CID["j1s01"]], rows)
    p = proj["per_case"][0]
    assert p["pass_all"] and not p["consistent"] and not p["cert"], p


def test_题面为空时分母必须报错不许报零(tmp_path):
    """空分母会被读成"0 题全过"＝第 ⑰ 类空扫。"""
    import json
    p = tmp_path / "empty.jsonl"
    p.write_text(json.dumps({"cid": "j9x01", "turn": "随便说一句", "seeds": []}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rp.load_cases(str(p))
