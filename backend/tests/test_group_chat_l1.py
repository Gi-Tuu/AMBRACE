# -*- coding: utf-8 -*-
"""AMBRACE 群聊调度 L1 测试（2026-08-25）：话痨度推断 + 三层漏斗 + 群控 muted + 问候语生成。

覆盖：
- _heuristic_talkativeness：外向/内向/中性文本打分；
- _talkativeness_score：显式字段优先，NULL 回退启发式；
- _select_speakers 三层漏斗各分支：@ 必回、低话痨不激活、=0 除非被@、=100 必激活、
  概率层、候选为空兜底、muted 排除但 @ 强制、≤MAX_GROUP_SPEAKERS；
- _generate_replies 把 muted_ids 传给漏斗（接线）；
- 问候语生成 _generate_greeting_text 与 generate_greeting 落库。
"""
import asyncio
import random
from types import SimpleNamespace

from app.api import chat_groups as cg
from app.api import characters as char_api
from app.agent import loop


# ---------------- 测试替身 ----------------

def _char(cid, name, personality="", chat_style="", talkativeness=None, muted_bool=False):
    return SimpleNamespace(
        id=cid, name=name, personality=personality, chat_style=chat_style,
        talkativeness=talkativeness,
    )


class _Rng:
    """可注入随机序列的假 rng（random()/shuffle/choice 确定性）。"""
    def __init__(self, rand_vals=None):
        self.rand_vals = list(rand_vals or [])
        self.i = 0
    def random(self):
        v = self.rand_vals[self.i]
        self.i += 1
        return v
    def shuffle(self, x):
        pass
    def choice(self, x):
        return x[0]


# ---------------- 话痨度推断 ----------------

def test_heuristic_外向活泼话多得分高():
    assert cg._heuristic_talkativeness("外向活泼，话多热情", "") >= 60
    # 强外向（不只一股信号）→ 100（必活跃）
    assert cg._heuristic_talkativeness("活泼开朗健谈话多", "元气满满") == 100


def test_heuristic_内向安静得分低():
    assert cg._heuristic_talkativeness("内向安静", "") < 50
    assert cg._heuristic_talkativeness("内向安静高冷寡言", "不爱说话") <= 20


def test_heuristic_中性或无信息为50():
    assert cg._heuristic_talkativeness("普通", "随缘") == 50
    assert cg._heuristic_talkativeness("", "") == 50
    assert cg._heuristic_talkativeness(None, None) == 50


def test_talkativeness_score_显式优先且钳位():
    c = _char(1, "A", personality="安静", talkativeness=0)
    assert cg._talkativeness_score(c) == 0
    c2 = _char(2, "B", talkativeness=150)
    assert cg._talkativeness_score(c2) == 100
    c3 = _char(3, "C", talkativeness=-5)
    assert cg._talkativeness_score(c3) == 0


# ---------------- 三层漏斗：@ 语义 ----------------

def test_funnel_at必回即使话痨低():
    chars = [_char(11, "小阳", personality="安静寡言", talkativeness=0),
             _char(12, "小冰", personality="活泼")]
    at = [chars[0]]
    out = cg._select_speakers(chars, at, rng=_Rng([0.9]))
    assert chars[0] in out  # @ 的人 talkativeness=0 也必回
    assert len(out) <= cg.MAX_GROUP_SPEAKERS


def test_funnel_at必回且不参与概率层():
    # @ 的人即使话痨低也在；未 @ 的按概率（此处 rng.random 给 0.9 → 高概率不激活→激活由 score 决定）
    chars = [_char(11, "小阳", talkativeness=1),
             _char(12, "小冰", talkativeness=1)]
    at = [chars[0]]
    # 用 rng.random=0.5，score=1 → 0.01 < 0.5，未@不激活
    out = cg._select_speakers(chars, at, rng=_Rng([0.5]))
    assert chars[0] in out
    assert chars[1] not in out


def test_funnel_低话痨不激活():
    chars = [_char(11, "小阳", talkativeness=10),
             _char(12, "小冰", talkativeness=10)]
    # rng.random=0.5，0.10 < 0.5 → 都不激活；候选为空 → 兜底选 1
    out = cg._select_speakers(chars, [], rng=_Rng([0.5, 0.5, 0.5]))
    assert len(out) == 1
    assert out[0] in chars


def test_funnel_talkativeness0除非被at否则不激活():
    chars = [_char(11, "小阳", talkativeness=0)]
    out = cg._select_speakers(chars, [], rng=_Rng([0.0]))
    # 兜底仍会选 1（防冷场）；但 0 话痨不因概率激活
    assert len(out) == 1 and out[0] == chars[0]


def test_funnel_talkativeness100必激活():
    chars = [_char(11, "小阳", talkativeness=100)]
    out = cg._select_speakers(chars, [], rng=_Rng([0.0]))
    assert chars[0] in out


def test_funnel_概率层按random判定():
    # score=50 → random()<0.5 激活
    chars = [_char(11, "小阳", talkativeness=50)]
    out = cg._select_speakers(chars, [], rng=_Rng([0.4]))
    assert chars[0] in out
    # random()≥0.5 不激活 → 兜底
    out2 = cg._select_speakers(chars, [], rng=_Rng([0.6]))
    assert len(out2) == 1 and out2[0] == chars[0]  # 兜底仍选 1（防冷场）


def test_funnel_候选为空随机兜底选1人():
    chars = [_char(11, "小阳", talkativeness=0)]
    out = cg._select_speakers(chars, [], rng=_Rng([0.0]))
    assert len(out) == 1
    assert out[0] == chars[0]


def test_funnel_不超过MAX_GROUP_SPEAKERS():
    chars = [_char(i, f"角色{i}", talkativeness=100) for i in range(10)]
    out = cg._select_speakers(chars, [], rng=random.Random(7))
    assert len(out) <= cg.MAX_GROUP_SPEAKERS


def test_funnel_用random默认随机源可运行():
    chars = [_char(11, "小阳", personality="活泼健谈"), _char(12, "小冰", personality="安静")]
    out = cg._select_speakers(chars, [])
    assert isinstance(out, list)


# ---------------- 三层漏斗：muted 群控 ----------------

def test_funnel_muted排除但at强制():
    chars = [_char(11, "小阳", talkativeness=100),
             _char(12, "小冰", talkativeness=100)]
    muted_ids = {12}
    # @ 到静音的 12 → 必回（即使 muted）
    at = [chars[1]]
    out = cg._select_speakers(chars, at, muted_ids=muted_ids, rng=_Rng([]))
    assert chars[1] in out
    # 不 @：静音的 12 不参与自动选择，未静音的 11 正常激活
    out2 = cg._select_speakers(chars, [], muted_ids=muted_ids, rng=_Rng([]))
    assert chars[1] not in out2
    assert chars[0] in out2


def test_funnel_全静音无at候选为空():
    chars = [_char(11, "小阳", talkativeness=100), _char(12, "小冰", talkativeness=100)]
    muted_ids = {11, 12}
    out = cg._select_speakers(chars, [], muted_ids=muted_ids, rng=_Rng([]))
    assert out == []  # 静音不参与自动选择，候选为空且无@ → 不发言


# ---------------- 接线：_generate_replies 把 muted_ids 传给漏斗 ----------------

class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _GroupDB:
    """按 SQL 文本分发 members（成员对象）/ chars / recent。"""
    def __init__(self, members, chars, recent):
        self._members = members
        self._chars = chars
        self._recent = recent
    async def execute(self, stmt, *a, **kw):
        text = str(stmt)
        if "chat_group_members" in text:
            return _Scalar(self._members)
        if "ai_characters" in text:
            return _Scalar(self._chars)
        if "chat_group_messages" in text:
            return _Scalar(self._recent)
        return _Scalar([])


def test_generate_replies_把muted_ids传给漏斗(monkeypatch):
    members = [SimpleNamespace(character_id=11, muted=True),
               SimpleNamespace(character_id=12, muted=False)]
    chars = [_char(11, "小阳"), _char(12, "小冰")]
    db = _GroupDB(members=members, chars=chars, recent=[])
    seen = {}

    def fake_select_speakers(chars_, at_chars_, muted_ids=(), *a, **kw):
        seen["muted_ids"] = set(muted_ids)
        seen["at_chars"] = list(at_chars_)
        return list(at_chars_[:1]) or chars_[:1]

    async def fake_runtime(*a, **kw):
        return []

    monkeypatch.setattr(cg, "_select_speakers", fake_select_speakers)
    monkeypatch.setattr(cg, "_generate_replies_runtime", fake_runtime)
    _orig = loop.AGENT_FLAGS.get("agent_loop_group_chat")
    loop.AGENT_FLAGS["agent_loop_group_chat"] = True
    try:
        asyncio.run(cg._generate_replies(db, 1, "@小阳 一起吃饭", "用户", user_id=4))
    finally:
        loop.AGENT_FLAGS["agent_loop_group_chat"] = _orig
    assert seen["muted_ids"] == {11}
    assert [c.id for c in seen["at_chars"]] == [11]  # @ 目标解析正确


# ---------------- 问候语生成 ----------------

def test_generate_greeting_text_返回LLM输出(monkeypatch):
    seen = {}
    async def fake_chat_completion(messages, **kw):
        seen["messages"] = messages
        seen["kw"] = kw
        return "嗨，今天过得怎么样呀？"
    monkeypatch.setattr("app.agent.llm_client.chat_completion", fake_chat_completion)
    char = SimpleNamespace(name="小阳", personality="阳光开朗", chat_style="活泼", bio="程序员")
    out = asyncio.run(char_api._generate_greeting_text(char, user_id=4))
    assert out == "嗨，今天过得怎么样呀？"
    assert seen["kw"].get("task") == "message"
    assert seen["kw"].get("user_id") == 4
    # prompt 包含人设信息
    prompt = seen["messages"][1]["content"]
    assert "小阳" in prompt and "阳光开朗" in prompt and "程序员" in prompt


def test_generate_greeting_写回greeting_message(monkeypatch):
    char = SimpleNamespace(id=1, name="小阳", personality="阳光开朗",
                           chat_style="活泼", bio="程序员", user_id=4, greeting_message=None)
    class _Res:
        def scalar_one_or_none(self): return char
    class _Db:
        committed = False
        async def execute(self, stmt, *a, **kw): return _Res()
        async def flush(self): pass
        async def refresh(self, obj): pass
        async def commit(self): self.committed = True

    async def fake_gen(char_, user_id):
        return "你好呀，很高兴见到你！"
    monkeypatch.setattr(char_api, "_generate_greeting_text", fake_gen)
    db = _Db()
    out = asyncio.run(char_api.generate_greeting(character_id=1, db=db, user_id=4, lang="zh"))
    assert char.greeting_message == "你好呀，很高兴见到你！"
    assert db.committed is True
    assert out["greeting_message"] == "你好呀，很高兴见到你！"


def test_generate_greeting_失败静默不落库(monkeypatch):
    char = SimpleNamespace(id=1, name="小阳", personality="阳光",
                           chat_style="活泼", bio="程序员", user_id=4, greeting_message=None)
    class _Res:
        def scalar_one_or_none(self): return char
    class _Db:
        committed = False
        async def execute(self, stmt, *a, **kw): return _Res()
        async def flush(self): pass
        async def refresh(self, obj): pass
        async def commit(self): self.committed = True

    async def boom(char_, user_id):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(char_api, "_generate_greeting_text", boom)
    db = _Db()
    out = asyncio.run(char_api.generate_greeting(character_id=1, db=db, user_id=4, lang="zh"))
    assert char.greeting_message is None
    assert db.committed is False
    assert out["greeting_message"] == ""
