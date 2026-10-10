# -*- coding: utf-8 -*-
"""A30 批 2 守卫：写侧提示词模板收窄 ＋ 评测通道同源收紧。

钉的是五件事（对应任务书 §3）：
① **唯一来源**——「记忆正文该长什么样」那段约束在仓库里只存在一份（`context_builder.py` 的
   `SYSTEM_PROMPT_TEMPLATE`），别处再写一份分叉＝当场红；旧写法（要求写具体日期，正是 j1s01
   时点后缀抖动的来源，方案 §1.2 第 1 条）不得残留。
② **评测同源**——判据吃的那份通道文本 == 生产落库的那份文本（与 `response_parser.parse_response`
   逐条对拉），带时点后缀／同义变体的两个跑法在评测侧收敛成同一串（撤掉这一步，两题 supersede
   的读数永远不会动）。
③ **裁剪树安全**——真实语料题集 `memory_action_cases_real_draft.jsonl` **不在公开仓**（隐私硬原则），
   依赖它的用例必须「文件不在＝跳过」，不许 FileNotFoundError（先例 `test_entity_match_third_route.py:84-86`）。
④ **变异**——撤掉同源那一步（把生产规范化打桩成恒等函数）⇒ 评测侧退回原始措辞；若评测侧自带一份
   规范化冒充同源，这条同样会红（钉的是「用生产那一份」，不是「输出恰好等于期望」）。
⑤ **零泄题**（方案 §六-7 红线）——收窄后的模板里不许出现评测题的专有名词，必须写成通用形状规则。

批 1（`test_memory_normalize_a30.py`）钉的是规范化函数本体与三个落库写点；本文件只钉批 2 的两条：
提示词形状与评测通道取文来源。判据／阈值／gold／指纹维度本批零改动。
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

import app.memory.normalize as norm_mod
from app.agent.context_builder import SYSTEM_PROMPT_TEMPLATE
from app.agent.response_parser import parse_response
from app.memory.normalize import normalize_memory_text

REPO = Path(__file__).resolve().parents[2]
EVAL_SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
CONTEXT_BUILDER = REPO / "backend" / "app" / "agent" / "context_builder.py"
REAL_DRAFT = REPO / "scripts" / "diagnostics" / "memory_action_cases_real_draft.jsonl"

# 收窄后那段形状的「指纹短语」：改这段文案时若换掉它，①的计数守卫要跟着换（守卫不许空跑）
_SHAPE_MARK = "只认这一种形状"
# 旧写法（要求正文自带日期）——批 2 之后任何地方都不该再出现
_OLD_MARK = "写记忆用具体日期"
# 评测题专有名词黑名单（方案 §六-7）：出现在提示词里就是「为过题污染提示词」
_LEAK_WORDS = ("朵朵", "煤球", "城西", "城北", "拆迁", "画室", "接送牌", "橘猫",
               "疫苗", "忌口", "无辣不欢", "第三针")


def _load_eval():
    """按路径加载评测器（与 `test_memory_action_eval_j1j2.py` 同一口径，不复制一份判据）。"""
    spec = importlib.util.spec_from_file_location("_a30_b2_eval_under_test", str(EVAL_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


def _prod_contents(text):
    """生产链路对同一段回复**真正落库**的正文（【记忆：】通道）。"""
    state = parse_response(text, {"character_info": {"bio": ""}, "user_message": ""})
    return [m["content"] for m in (state.get("new_memories") or [])]


# 10-06 三跑实测到的抖动形态（j1s01 时点后缀三类／j1s02 同义动词／j1f01 情态词）
_JITTER_PAIRS = [
    ("好嘞。【记忆：用户现住城西（2026-10-05提到）】", "用户现居城西"),
    ("好嘞。【记忆：用户现居城西（2026-10-09）】", "用户现居城西"),
    ("收到。【记忆：用户忌口辣椒（2026-10-05起）】", "用户忌口辣椒"),
    ("收到。【记忆：用户忌口辣椒（最近）】", "用户忌口辣椒"),
    ("记下了。【记忆：接送牌要填女儿名字】", "接送牌填女儿名字"),
]

# ─────────────── ① 写侧形状的约束只存在一份 ───────────────

def test_形状约束在模板里只出现一次():
    src = CONTEXT_BUILDER.read_text(encoding="utf-8")
    assert src.count(_SHAPE_MARK) == 1, "形状约束被写成多份＝迟早分叉"
    assert _OLD_MARK not in src, "旧写法（让正文自带日期）还在，它就是时点后缀抖动的来源"


def test_形状约束没在别处再写一份():
    """全后端扫一遍：那段约束只许住在 context_builder 的模板里（新增分叉点＝红）。"""
    hits = [p for p in (REPO / "backend" / "app").rglob("*.py")
            if _SHAPE_MARK in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == [CONTEXT_BUILDER], "形状约束出现在多处：%s" % [str(p) for p in hits]


def test_收窄后的形状是写死的一种而非多种可选():
    line = next(ln for ln in SYSTEM_PROMPT_TEMPLATE.splitlines() if ln.startswith("【记忆：内容】"))
    assert _SHAPE_MARK in line, line
    # 一条只写一个事实＋长度预算＋不许改写＋不写日期：四件事必须在同一段里说死
    for piece in ("一条只写一个事实", "≤25 字", "别换成近义说法", "不写日期"):
        assert piece in line, "收窄文案缺约束项 %r：%s" % (piece, line)
    assert "或" not in line.split(_SHAPE_MARK, 1)[1], "形状里出现「或」＝又给模型留了第二种写法"


# ─────────────── ② 评测通道与生产落库同源 ───────────────

@pytest.mark.parametrize("raw,want", _JITTER_PAIRS,
                         ids=["现住+提到", "现居+日期", "忌口+起", "忌口+最近", "情态词"])
def test_评测读到的通道文本等于生产落库文本(raw, want):
    """判据吃的那份文本必须先过**生产那个**规范化函数，与落库逐字一致。"""
    chan = ev.extract_memory_channel(raw)
    assert chan == [want], chan
    assert chan == _prod_contents(raw), "评测通道 ≠ 生产落库正文（尺子量的不是生产真落的那份）"


def test_两个抖动跑法在指纹的通道维度上收敛():
    """批 2 要买的正是这件事：落库已定形 ⇒ 「通道」这一维在三跑里同形（此前永远不动）。"""
    def row(text):
        return {"pass": True, "actions_seen": ["MEMORY"], "dates_seen": [], "exemptions": [],
                "payloads": [], "mem_channel": ev.extract_memory_channel(text)}

    sig_a = ev._run_signature(row(_JITTER_PAIRS[0][0]))
    sig_b = ev._run_signature(row(_JITTER_PAIRS[1][0]))
    assert sig_a == sig_b, (sig_a, sig_b)
    assert sig_a[-1] == ("用户现居城西",)


def test_同源只换文本来源不动判据():
    """规范化后的通道仍是「通道命中」：判据口径（MEMORY 算产出、缺片段判 E4）一点没变。"""
    from app.agent.actions import parse_actions

    raw = "【记忆：用户现住城西（2026-10-05提到）】"
    exp = {"action_type": ["MEMO", "MEMORY"], "field_match": {"*": "城西"}}
    v = ev.j1_verdict(exp, parse_actions(raw), memory_texts=ev.extract_memory_channel(raw))
    assert v["pass"] is True, v
    # 反证：判据吃的若是同一条定形文本，结果与走没走规范化无关（变的只是文本来源）
    assert ev.j1_verdict(exp, [], memory_texts=["用户现居城西"])["pass"] is True


# ─────────────── ③ 裁剪树安全 ───────────────

def _real_draft_cases(path=REAL_DRAFT):
    """真实语料题集：文件不在＝跳过（CI 跑的是脱敏快照，这份题集不进公开仓）。"""
    if not path.is_file():
        pytest.skip("脱敏快照不含真实语料题集（隐私硬原则）；本用例只在内部全仓生效")
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_题集缺席时跳过而不是报错(tmp_path):
    """把「文件不在」这条路单独钉住：裁剪树走的就是它，绝不能是 FileNotFoundError。"""
    with pytest.raises(pytest.skip.Exception):
        _real_draft_cases(tmp_path / "does_not_exist.jsonl")


def test_真实语料正文过评测通道不丢载荷():
    """有题集时：每条真实正文过评测通道后仍是生产定形态，且不会被剥成空（内联样本才是主判据）。"""
    checked = 0
    for case in _real_draft_cases():
        for seed in (case.get("seeds") or []):
            content = str((seed or {}).get("content") or "").strip()
            # 带括号或「记忆」二字的正文会被通道正则截断/多切一段，与本批要钉的无关，跳过
            if not content or any(ch in content for ch in "[]【】") or "记忆" in content:
                continue
            text = "【记忆：%s】" % content
            chan = ev.extract_memory_channel(text)
            assert chan == [normalize_memory_text(content)], text
            assert chan != [""], text
            checked += 1
    if not checked:
        pytest.skip("题集里没有可用正文（裁剪树/题集改版）；内联样本已覆盖同一断言")


# ─────────────── ④ 变异：撤掉同源这一步立刻红 ───────────────

def test_变异_撤掉同源这一步评测退回原始措辞(monkeypatch):
    """把生产规范化打桩成恒等函数 ⇒ 评测侧跟着退回原始措辞（证明它调的就是那一份，没藏副本）。"""
    monkeypatch.setattr(norm_mod, "normalize_memory_text", lambda t: t)
    raw = "用户现住城西（2026-10-05提到）"
    assert ev.extract_memory_channel("【记忆：%s】" % raw) == [raw]
    monkeypatch.undo()
    assert ev.extract_memory_channel("【记忆：%s】" % raw) == ["用户现居城西"]


def test_评测侧不许自带一份规范化冒充同源():
    """同源那一步必须是「调用生产函数」，不是在评测脚本里抄一份正则/规则。"""
    src = EVAL_SCRIPT.read_text(encoding="utf-8")
    body = src[src.index("def extract_memory_channel"):]
    assert "from app.memory.normalize import normalize_memory_text" in body, \
        "评测通道没走生产规范化函数（抄一份＝下一批加规则时两边各红一半）"
    assert normalize_memory_text is norm_mod.normalize_memory_text


# ─────────────── ⑤ 零泄题红线 ───────────────

def test_收窄后的模板不含评测题词句():
    hit = [w for w in _LEAK_WORDS if w in SYSTEM_PROMPT_TEMPLATE]
    assert not hit, "提示词里出现评测题专有名词＝为过题污染提示词：%s" % hit


def test_通道正则仍与生产逐字同源():
    """批 2 只换了「文本从哪来」，那条正则不许顺手改（改了＝评测在读一条生产不存在的通道）。"""
    src = (REPO / "backend" / "app" / "agent" / "response_parser.py").read_text(encoding="utf-8")
    m = re.search(r'memory_pattern = r"([^"]+)"', src)
    assert m and m.group(1) == ev.MEMORY_CHANNEL_RE, (ev.MEMORY_CHANNEL_RE, m and m.group(1))
