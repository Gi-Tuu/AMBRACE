# -*- coding: utf-8 -*-
"""A30 批 1 守卫：写侧记忆文本定形（app/memory/normalize.py ＋ 三个落库写入点）。

钉的是四件事（对应任务书 §2 / §4）：
1. **前缀归属语义不变**——`memory/speaker.resolve_speaker_from_content` 靠句首「用户/对方/
   他/她」「我」判归属，规范化前后三元组必须逐字相同（正反例成对，覆盖「用户…」/句首「我…」/
   「我们一起…」/无主语/含推断词五类）；
2. **不丢信息**——除「行尾时点括注」与「同义表命中的词」以外逐字保留（用「还原同义词后＝原文
   去掉后缀」这类可判定断言，不是只断言 len()）；
3. **幂等 + fail-open**——重复规范化不再变化；空/None/非字符串/超长/emoji/中英混排不抛且原样返回；
4. **接入真的生效**——三个写入点（`extract_memo`、`parse_actions` 的 [MEMO] 载荷、`parse_response`
   的【记忆：】正文）必须真的调用规范化：把 `normalize_memory_text` 打桩成恒等函数后三处退回原样，
   未打桩时三处都是定形态（撤掉接入 ⇒ 本文件立刻变红，而不是只钉住「函数存在」）。

评测尺子（scripts/diagnostics/memory_action_eval.py）本批零改动，同源收紧属批 2。
"""
import ast
import inspect

import pytest

import app.memory.normalize as norm_mod
from app.agent import actions as agent_actions
from app.agent.response_parser import parse_response
from app.events.schema import EPISTEMIC_FACT, EPISTEMIC_INFERRED
from app.memory.normalize import normalize_memory_text
from app.memory.speaker import resolve_speaker_from_content

_USER_ID, _CHAR_ID = 7, 13

# ─────────────── ① 前缀归属语义不变 ───────────────

# 五类前缀（方案 §六-2 要求覆盖面），每条给「规范化前」与「规范化后」两个形态
_SPEAKER_SAMPLES = [
    "用户忌口辣椒（2026-10-05提到）",        # 「用户…」→ user/FACT
    "我现住城西（2026-10-09）",              # 句首「我…」→ character/INFERRED
    "我们一起（今天）",                       # 「我们一起」→ 批级回退（不因「我」字头误归角色）
    "接送牌要填女儿名字：朵朵",               # 无主语 → 批级回退
    "他可能会迟到（最近）",                   # 含推断词 → character/INFERRED（优先于前缀）
    "她说要填写入学表（2026-10-05）",         # 「她…」→ user/FACT（「要填写」不在「她说」后剥）
]


@pytest.mark.parametrize("src", _SPEAKER_SAMPLES)
def test_规范化前后归属判定逐字相同(src):
    """正例：规范化不得让 speaker 的三元组发生任何变化（含「我/用户」句首与推断词优先）。"""
    out = normalize_memory_text(src)
    before = resolve_speaker_from_content(src, "今天聊了很多", "好的", _USER_ID, _CHAR_ID)
    after = resolve_speaker_from_content(out, "今天聊了很多", "好的", _USER_ID, _CHAR_ID)
    assert after == before, f"归属被规范化改变：{src!r} -> {out!r}：{before} != {after}"


@pytest.mark.parametrize("src,expect_speaker", [
    ("用户忌口辣椒（2026-10-05）", "user"),
    ("现住城西（2026-10-05）", None),          # 无主语＋有用户消息 → 批级回退 user/FACT
    ("我觉得要填接送牌", "character"),
])
def test_归属判定取值符合既有语义(src, expect_speaker):
    """反例锚点：三类样本的判定结果本身就是已知值，规范化后仍要落在那个值上。"""
    got_speaker, got_id, status = resolve_speaker_from_content(
        normalize_memory_text(src), "在的呢", "好的", _USER_ID, _CHAR_ID)
    if expect_speaker is None:
        assert (got_speaker, got_id, status) == ("user", _USER_ID, EPISTEMIC_FACT)
    else:
        assert got_speaker == expect_speaker
        assert got_id == (_USER_ID if expect_speaker == "user" else _CHAR_ID)
        assert status == (EPISTEMIC_FACT if expect_speaker == "user" else EPISTEMIC_INFERRED)


@pytest.mark.parametrize("src", _SPEAKER_SAMPLES)
def test_句首人称词逐字不动(src):
    """句首前缀词（用户/对方/他/她/我/我们）一个字都不许被改写——规范化只作用于行尾与词级同义。"""
    out = normalize_memory_text(src)
    heads = ("用户", "对方", "他", "她", "我们", "我")
    hs = next((h for h in heads if src.startswith(h)), "")
    ho = next((h for h in heads if out.startswith(h)), "")
    assert hs == ho
    if hs:
        assert (out[:len(hs)] == hs) and (out == hs + out[len(hs):])


# ─────────────── ② 时点后缀逐形态剥净 ───────────────

# A1 绝对日期（模块注释逐条列出的形态，一条一个用例）
_A1_FORMS = ["（2026-10-05）", "（2026-10-5）", "（2026/10/05）", "（2026.10.05）",
             "（2026年10月5日）", "（2026年10月5号）", "（2026年10月）", "（2026年）",
             "（2026）", "（10月5日）", "（10月5号）", "(2026-10-05)"]
# A2 日期＋时点尾词
_A2_FORMS = ["（2026-10-05提到）", "（2026-10-05说起）", "（2026-10-05说）", "（2026-10-05起）",
             "（2026-10-05开始）", "（2026-10-05记录）", "（2026-10-05当天）", "（2026-10-05那天）",
             "（2026-10-05当时）"]
# A3 相对时点词
_A3_FORMS = ["（今天）", "（今日）", "（昨天）", "（昨日）", "（前天）", "（大前天）",
             "（刚刚）", "（刚才）", "（方才）", "（最近）", "（近期）"]


@pytest.mark.parametrize("suffix", _A1_FORMS + _A2_FORMS + _A3_FORMS)
def test_行尾时点括注逐形态剥净(suffix):
    """支持的每一种形态都必须被剥掉，且只剥掉那一段（正文逐字不动）。"""
    base = "用户忌口辣椒"
    assert normalize_memory_text(base + suffix) == base


@pytest.mark.parametrize("suffix", ["（2026-10-05提到）", "（今天）", "(2026-10-05)"])
def test_剥后保留原有收尾标点(suffix):
    """括注后面的句号是用户的标点习惯，剥括注不能顺手把标点也吃掉（不改标点习惯）。"""
    assert normalize_memory_text("用户忌口辣椒" + suffix + "。") == "用户忌口辣椒。"


def test_叠套时点括注逐层剥净且幂等():
    assert normalize_memory_text("用户忌口（最近）（2026-10-05）（今天）") == "用户忌口"
    assert normalize_memory_text("用户忌口（最近）（近期）（今天）（刚刚）") == "用户忌口"
    # 叠套 + 同义词混在一起也要一次定形到位（幂等）
    deep = "用户现住城西（最近）（2026-10-05）"
    once = normalize_memory_text(deep)
    assert once == "用户现居城西"
    assert normalize_memory_text(once) == once


@pytest.mark.parametrize("kept", [
    "用户养的橘猫叫煤球（橘猫）",
    "要带它去打第三针（第三针）",
    "用户绘本题材：（待用户补充）",
    "用户忌口辣椒（明天）",           # 未来指向＝真实信息，不剥
    "用户计划出行（下周）",           # 同上
    "用户在画室（16:30）",           # 时刻不是日期
    "用户说（不知道）",              # 括注内容非时点
])
def test_非时点括注逐字保留(kept):
    """含非时点字／未来指向的括注一律原样——该留的留下（方案 §1.2、§六-3）。"""
    assert normalize_memory_text(kept) == kept


# ─────────────── ③ 同义表逐条命中且只替换该词 ───────────────

def test_同义表规模受控():
    """表必须窄（宁少勿滥）：条数与来源都登记在模块常量里，扩表要有据。"""
    assert len(norm_mod._SYNONYM_TABLE) <= 5
    assert all(len(v) >= 2 for v, _, _ in norm_mod._SYNONYM_TABLE), "单字变体会大面积误伤"


@pytest.mark.parametrize("variant,canonical", [
    ("现住", "现居"), ("要填", "填"), ("要填写", "填写"),
])
def test_同义表命中且只替换该词(variant, canonical):
    """除被统一的那个词之外，其余字符逐字保留（不是长度断言，是逐字断言）。"""
    src = f"用户{variant}城西接送牌备注"
    out = normalize_memory_text(src)
    assert out == src.replace(variant, canonical)
    assert out == f"用户{canonical}城西接送牌备注"


def test_同义表实测抖动样本():
    """方案 §1.3 的两条真实抖动（j1s02 现住/现居、j1f01 要填/填）三跑同形。"""
    assert normalize_memory_text("用户现住城西") == normalize_memory_text("用户现居城西") == "用户现居城西"
    a = normalize_memory_text("接送牌要填女儿名字：朵朵")
    b = normalize_memory_text("接送牌填女儿名字：朵朵")
    assert a == b == "接送牌填女儿名字：朵朵"


@pytest.mark.parametrize("untouched", [
    "老师要求填写接送牌",       # 「要求」的「要」不能当情态词剥 → 否则「求填写」
    "用户不要填写地址",         # 否定！剥了会翻转成「不填写→填写」同义？实际是要保住「不要」
    "用户需要填写资料",
    "主要填写工作内容",
    "必须填写完整",
    "用户只要填写一项",
    "用户就要填写报名表",
    "用户想要填写问卷",
])
def test_情态剥离守卫_要字属前词时不动(untouched):
    """「要」是前一个词的词尾时整条不动——宁可漏定形，不可吞信息／翻转否定。"""
    assert normalize_memory_text(untouched) == untouched


# ─────────────── ④ 不丢信息（可判定断言） ───────────────

_INFO_SAMPLES = [
    "用户现住城西（2026-10-09）",
    "用户忌口辣椒（2026-10-05提到）",
    "我现住学校旁（今天）",
    "接送牌要填女儿名字：朵朵（2026年10月5日）",
    "用户喜欢喝咖啡（最近）",
]


@pytest.mark.parametrize("src", _INFO_SAMPLES)
def test_还原同义词后等于原文去掉后缀(src):
    """把统一写法还原成变体，必须逐字等于「原文 − 行尾时点括注」：除这两类外没动过任何字符。"""
    out = normalize_memory_text(src)
    back = out
    for variant, canonical, _blk in norm_mod._SYNONYM_TABLE:
        back = back.replace(canonical, variant)
    assert src.startswith(back)
    tail = src[len(back):]
    if tail.strip():
        # 差值只能是那一段时点括注——它单独挂到任何正文末尾都会被剥掉
        assert normalize_memory_text("锚" + tail) == "锚"


@pytest.mark.parametrize("src", _INFO_SAMPLES)
def test_规范化输出无时点括注残留(src):
    """定形结果里不该再有行尾时点括注，也不该再有表内变体（否则定形没做到底）。"""
    out = normalize_memory_text(src)
    for suffix in _A1_FORMS + _A2_FORMS + _A3_FORMS:
        assert not out.endswith(suffix)
    for variant, _canonical, _blk in norm_mod._SYNONYM_TABLE:
        assert variant not in out


# ─────────────── ⑤ 幂等 ───────────────

@pytest.mark.parametrize("src", _SPEAKER_SAMPLES + _INFO_SAMPLES + _A1_FORMS + [
    "用户现居城西", "接送牌填女儿名字：朵朵", "猫叫煤球（橘猫）", "", "纯文本",
])
def test_幂等_重复规范化不再变化(src):
    once = normalize_memory_text(src)
    assert normalize_memory_text(once) == once
    assert normalize_memory_text(normalize_memory_text(once)) == once


def test_幂等_已是定形态的输入逐字返回():
    for stable in ("用户现居城西", "接送牌填女儿名字：朵朵", "用户忌口辣椒。", "我觉得填接送牌"):
        assert normalize_memory_text(stable) == stable


# ─────────────── ⑥ fail-open ───────────────

@pytest.mark.parametrize("bad", [
    None, 123, 0, [], {}, ("元组",), b"bytes", object(),
])
def test_非字符串输入原样返回不抛(bad):
    assert normalize_memory_text(bad) is bad


def test_空串与纯空白不抛且不产出空载荷():
    assert normalize_memory_text("") == ""
    assert normalize_memory_text("   ") == "   "
    # 整条正文就是一个时点括注 → 剥完为空 → 返回原文（绝不让记忆载荷被剥没）
    assert normalize_memory_text("（今天）") == "（今天）"
    assert normalize_memory_text("(2026-10-05)") == "(2026-10-05)"


def test_超长正文不参与定形():
    long_src = "用户现住城西" + "很长" * 2000
    assert len(long_src) > norm_mod._MAX_LEN
    assert normalize_memory_text(long_src) == long_src


@pytest.mark.parametrize("odd", [
    "喜欢喝咖啡🎉（最近）",
    "user 现住 city（2026-10-05）",
    "User 喜欢 latte🎉",
    "未闭合（2026-10-05",
    "嵌套（（今天））",
    "（2026-10-05）开头",
    "😀（今天）😀",
    "混合 mixed 文本 要填 表单（2026年）",
])
def test_emoji中英混排畸形输入不抛(odd):
    """不抛异常即可；含中文的形态照常定形，纯英文/结构畸形的不动。"""
    out = normalize_memory_text(odd)
    assert isinstance(out, str)
    assert normalize_memory_text(out) == out


def test_fail_open_异常时返回原文并留痕(monkeypatch):
    """内部规则一旦抛错，必须原样返回（记忆不能因规范化被写丢），并 INFO 留痕。"""
    calls = []

    def _boom(text):
        raise RuntimeError("rule exploded")

    def _info(fmt, *args):
        calls.append((fmt, args))

    monkeypatch.setattr(norm_mod, "_strip_trailing_time", _boom)
    monkeypatch.setattr(norm_mod._logger, "info", _info)
    src = "用户现住城西（2026-10-09）"
    assert normalize_memory_text(src) == src
    assert calls, "fail-open 必须留痕"


# ─────────────── ⑦ 接入（撤掉接入即红） ───────────────

_RAW_MEMO = "[MEMO]用户现住城西（2026-10-09）[/MEMO]"
_RAW_CHANNEL = "好嘞。【记忆：用户现住城西（2026-10-09）】"
_FORM = "用户现居城西"


def _memo_payload(text):
    acts = agent_actions.parse_actions(text)
    memo = [a for a in acts if a.action_type == agent_actions.MEMO]
    assert len(memo) == 1
    return memo[0].payload["text"]


def _channel_content(response):
    state = parse_response(response, {"character_info": {"bio": ""}, "user_message": ""})
    mems = state["new_memories"]
    assert len(mems) == 1
    return mems[0]["content"]


def test_接入生效_三个写入点都落到定形态():
    assert _memo_payload(_RAW_MEMO) == _FORM
    assert agent_actions.extract_memo(_RAW_MEMO) == _FORM
    assert _channel_content(_RAW_CHANNEL) == _FORM


def test_变异_撤掉接入点立刻变红(monkeypatch):
    """把规范化打桩成恒等函数 ⇒ 三处退回原文：证明上面的断言钉的是「接入」而不是「函数存在」。"""
    monkeypatch.setattr(norm_mod, "normalize_memory_text", lambda t: t)
    raw = "用户现住城西（2026-10-09）"
    assert _memo_payload(_RAW_MEMO) == raw
    assert agent_actions.extract_memo(_RAW_MEMO) == raw
    assert _channel_content(_RAW_CHANNEL) == raw
    # 反证：不打桩时必须还是定形态（接入没被绕过）
    monkeypatch.undo()
    assert _memo_payload(_RAW_MEMO) == _FORM


def test_接入不改通道名判定与截断口径():
    """只规范「要落库的正文」：载荷长度上限（80 字）、标记剥离、通道正文抽取口径都不动。"""
    long_memo = "[MEMO]" + ("用户现住城西（2026-10-09）" * 8) + "[/MEMO]"
    assert len(_memo_payload(long_memo)) <= 80
    assert agent_actions.extract_memo("没有标记的文本") is None
    assert agent_actions.strip_actions(_RAW_MEMO).strip() == ""
    state = parse_response("正文完好【记忆：用户喜欢咖啡】", {"character_info": {"bio": ""},
                                                            "user_message": ""})
    assert state["ai_response"] == "正文完好"
    assert state["new_memories"][0]["content"] == "用户喜欢咖啡"


# ─────────────── ⑧ 纯度（方案 §六-1） ───────────────

_IO_PREFIXES = ("app.db", "app.models", "app.agent", "app.application", "app.scheduling",
                "app.events", "sqlalchemy", "fastapi", "openai", "chromadb")


def test_规范化模块顶层零IO依赖():
    """纯函数：顶层 import 只许有 stdlib 与 logger，不得碰 DB/ORM/LLM/上层模块。"""
    tree = ast.parse(inspect.getsource(norm_mod))
    tops = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            tops += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            tops.append(node.module or "")
    bad = [m for m in tops if any(m == p or m.startswith(p + ".") for p in _IO_PREFIXES)]
    assert not bad, f"normalize.py 顶层出现 IO 依赖：{bad}"
    assert not any(m.startswith("app.memory") for m in tops), "不得反向 import 记忆包（防环）"
