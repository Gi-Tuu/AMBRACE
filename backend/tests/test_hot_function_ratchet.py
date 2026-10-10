# -*- coding: utf-8 -*-
"""热点函数棘轮守卫（2026-10-10，来源＝外部工程评分报告的建议 3「CI 加复杂度上限」）。

为什么不直接开 Ruff 的 C901：全库当场红几百处，门禁就从"防新债"变成"刷噪音"，
下一次还是把规则关掉。本仓房式是**棘轮**——旧账认了，但不许继续长。

三条判据（算术全在纯函数里，合成用例直接喂假数据验牙；本仓同族教训＝"评测的算术必须是纯函数"）：
① **只降不升**：热点表里每个函数的（长度，近似圈复杂度）必须 ≤ 冻结值；真被拆短了**也红**，
   红话是"把上限下调到 N"——这一声红就是拆分的记账动作本身。
② **总量棘轮**：全库「长>LEN_MAX 或 CC>CC_MAX」的函数总数必须 ≤ OVER_MAX（降了也红，催下调）。
   不用"新增即红"是因为 CC>35 一下圈进几十个短而绕的函数，那种写法第一天就红成一堵墙。
③ **反向钉**：必须真扫到 ≥3000 个模块级函数，且热点表每一行都在实测里命中——
   扫描器空跑＝①②都是装饰（本仓同族前科：C1 的子进程解码、C2 的 cat-file 空扫）。

复杂度是 **AST 近似**（数 If/For/While/Try/推导式 ＋ BoolOp 值数−1），与那份外部报告同一口径，
不是商用工具原值；所以钉的是"别再变长"，不是"复杂度等于几"。
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
LEN_MAX, CC_MAX = 150, 35
OVER_MAX = 44          # 2026-10-10 实测的超标函数总数（只降不升）
FUNC_FLOOR = 3000      # 同日实测 3171 个模块级函数；掉下这个数＝扫描面塌了

# 冻结基线（2026-10-10 实测；复算脚本＝仓库外 _measure_hot_ratchet.py，同口径）
# (相对 backend/app 的路径, 函数名, 长度上限, 圈复杂度上限)
HOT_RATCHET = [
    ("agent/context/assembly.py", "assemble_context", 877, 198),
    ("memory/write.py", "save_memory", 536, 103),
    ("memory/retrieve.py", "search_memories", 420, 101),
    ("application/chat_service.py", "_run_agent_core", 417, 82),
    ("application/chat/streaming.py", "send_and_receive_stream", 332, 44),
    ("scheduling/message_generator.py", "generate_proactive_event", 263, 45),
    ("main.py", "lifespan", 260, 31),
    ("memory/extractor.py", "extract_single", 258, 74),
    ("api/games.py", "_resume_ai_turns", 243, 55),
    ("agent/nodes.py", "generate_response", 222, 51),
    ("api/chat.py", "websocket_chat", 212, 40),
    ("agent/llm_client.py", "chat_completion", 189, 48),
    ("agent/runtime.py", "build_light_social_context", 168, 45),
    ("memory/meaning.py", "run_meaning_extraction", 147, 51),
    ("memory/retrieve.py", "_rerank", 147, 52),
    ("scheduling/message_generator.py", "_run_two_rounds", 128, 45),
    ("memory/dedup.py", "deduplicate_memories", 114, 48),
    ("life/decision.py", "decide", 112, 49),
]


def _cc_of(node) -> int:
    cc = 1
    for x in ast.walk(node):
        if isinstance(x, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.comprehension)):
            cc += 1
        elif isinstance(x, ast.BoolOp):
            cc += len(x.values) - 1
    return cc


def _scan():
    """IO 只在这一层：返回 {(相对路径, 函数名): (长度, 近似圈复杂度)}。"""
    out = {}
    for p in sorted(APP.rglob("*.py")):
        rel = p.relative_to(APP).as_posix()
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[(rel, n.name)] = ((n.end_lineno or n.lineno) - n.lineno + 1, _cc_of(n))
    return out


def judge_hot(measured: dict, ratchet: list) -> list:
    """纯函数：热点表 vs 实测量。返回违规说明（空列表＝绿）。不读文件、不读时钟。"""
    out = []
    for rel, name, len_max, cc_max in ratchet:
        cur = measured.get((rel, name))
        if cur is None:
            out.append("%s::%s 找不到了（已拆／改名？）⇒ 删掉这一行或换成新切口" % (rel, name))
            continue
        ln, cc = cur
        if ln > len_max or cc > cc_max:
            out.append("%s::%s 现长 %d/CC %d ＞ 上限 %d/%d＝变长了" % (rel, name, ln, cc, len_max, cc_max))
        elif ln < len_max or cc < cc_max:
            out.append("%s::%s 已变短（%d/CC %d）⇒ 把上限从 %d/%d 下调到 %d/%d"
                       % (rel, name, ln, cc, len_max, cc_max, ln, cc))
    return out


def judge_count(n_over: int, over_max: int) -> str:
    """纯函数：总量棘轮。上升＝新债；下降＝基线该下调（也红，逼着记账）。"""
    if n_over > over_max:
        return "全库超标函数从 %d 涨到 %d ⇒ 新写的长函数要么当场拆，要么进热点表" % (over_max, n_over)
    if n_over < over_max:
        return "超标数已从 %d 降到 %d ⇒ 把 OVER_MAX 下调到 %d，别让旧基线掩盖进展" % (over_max, n_over, n_over)
    return ""


def test_热点函数只许变短不许变长():
    assert judge_hot(_scan(), HOT_RATCHET) == [], judge_hot(_scan(), HOT_RATCHET)


def test_全库超标函数总数不许上升():
    over = [1 for (_rel, _name), (ln, cc) in _scan().items() if ln > LEN_MAX or cc > CC_MAX]
    assert judge_count(len(over), OVER_MAX) == "", judge_count(len(over), OVER_MAX)


def test_棘轮判据本身有牙():
    """合成用例直接喂假数据：变长／变短／消失／超标数上升，四种都必须被说出来。"""
    good = {("a.py", "f"): (100, 20)}
    tab = [("a.py", "f", 100, 20)]
    assert judge_hot(good, tab) == []
    assert judge_hot({("a.py", "f"): (101, 20)}, tab), "函数变长必须红"
    assert judge_hot({("a.py", "f"): (100, 21)}, tab), "复杂度变高必须红"
    assert judge_hot({("a.py", "f"): (90, 20)}, tab), "拆短了也要红（催下调基线）"
    assert judge_hot({}, tab), "热点行找不到（拆掉／改名）必须红，不许静默放行"
    assert judge_count(45, 44), "超标总数上升必须红"
    assert judge_count(43, 44), "超标总数下降也必须红（把基线记下来）"
    assert judge_count(44, 44) == ""


def test_扫描器必须真的在扫():
    """反向钉：本仓已被"看着在跑其实空扫"咬过两次（C1 子进程解码、C2 cat-file 空扫）。"""
    got = _scan()
    assert len(got) >= FUNC_FLOOR, "只扫到 %d 个模块级函数＝扫描面塌了（路径或解析出错）" % len(got)
    for rel, name, _l, _c in HOT_RATCHET:
        assert (rel, name) in got, "热点表里的 %s::%s 不在扫描面上" % (rel, name)
    for rel, name, floor in (("memory/write.py", "save_memory", 400),
                             ("memory/retrieve.py", "search_memories", 300),
                             ("application/chat_service.py", "_run_agent_core", 300)):
        assert got[(rel, name)][0] >= floor, "%s::%s 长度掉到 %d＜%d＝口径或扫描不对" % (
            rel, name, got[(rel, name)][0], floor)
