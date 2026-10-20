# -*- coding: utf-8 -*-
"""A30 生成端私有留底的守卫（**零 LLM、零计费、零生产库**，全部合成夹具 + `tmp_path`／仓库外临时目录）。

这条腿补的是「留底复算不出来」，所以这里测的四件事都是**可复核性**本身：
① 三种指纹分得清——「输入」（`prompt_fp_stable`／`prompt_fp`）与「来源」
   （`case_source_fp`／`evaluator_source_fp`）不许互相顶替，缺哪一维就写 `unknown`；
② 完整原文真的留住了——报表那 70 字／900 字的截断口径一位都不带进留底；
③ 默认路径一个字的原文都不多落——不给 `--emit-private-evidence` 时行为与改动前逐字相同；
④ 私有文件只在仓库外、写不成必须当场报错、凭据形态整批拒写、原文不进公共 stdout。

为什么不在 pytest 里跑 `run_generate_eval`／`run_export_eval`：`app.db.database` 的 engine 按进程缓存
（与 `test_memory_action_eval_generate_chain.py` 同一条说明），整链路自证走独立进程桩脚本；
这里只测**纯函数＋取料点＋落盘闸**，接线部分用 `inspect.getsource` 守卫钉住。
"""
import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import tempfile
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_eval_a30_evidence_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()


@pytest.fixture()
def outside_repo():
    """私有留底的落盘夹具**必须在仓库外**：项目全量跑用 `--basetemp=.pytest_tmp`（仓库内），
    拿 `tmp_path` 当「仓库外路径」会让整批落盘闸测成假红（10-14 首跑就是这么撞的，
    撞出来的不是 bug 而是闸门真的在咬）。用 `TemporaryDirectory` 退出即清，不在系统 TEMP 留残留。
    """
    # 子进程 TEMP 可能指向仓库内沙箱；私有留底显式使用仓库外的用户目录，退出即清理。
    with tempfile.TemporaryDirectory(prefix="ambrace_a30_ev_", dir=Path.home()) as td:
        root = Path(td)
        assert not os.path.normcase(str(root)).startswith(
            os.path.normcase(ev.repo_root_dir()) + os.sep), "夹具目录落进仓库了 ⇒ 下面全是假绿"
        yield root

# 夹具：74 个「甲」＋ 6 字尾巴 = 80 字（正好顶满生产 `content[:80]`，同时**越过**评测的 70 字截断）
_HEAD = "甲" * 74
_TAIL = "◆载荷尾巴◆"
_LONG_TEXT = _HEAD + _TAIL
_JSON_UNICODE = '{"备注":"女儿小花★🎈","日期":"2026-10-14"}◆JSON尾部◆'
_CLOCK_A = "2026年10月14日 星期三 17:12"
_CLOCK_B = "2026年10月14日 星期三 17:13"
_CLOCK_C = "2026年10月14日 星期三 17:14"


def _act(action_type: str, payload):
    """假动作对象：留底只认 `.payload`，不必把生产解析链整段拖进来。"""
    return types.SimpleNamespace(action_type=action_type, payload=payload)


def _case(cid="jpe01", **over):
    c = {"cid": cid, "category": "fact", "judge": "J1",
         "seeds": [{"content": "用户女儿叫小花，2021年出生"}], "distractors": [],
         "expect": {"action_type": "MEMO", "field_match": {"*": "小花"}, "gold_seed_idx": [0]},
         "time_anchor": {"as_of": "2026-10-14"}}
    c.update(over)
    return c


def _msgs(clock=_CLOCK_A, extra=""):
    return [{"role": "system", "content": "你是小爱，正在陪用户。\n## 当前时间\n%s%s" % (clock, extra)},
            {"role": "user", "content": "女儿生日帮我记一下"}]


def _prow(msgs=None, **over):
    msgs = msgs if msgs is not None else _msgs()
    prow = {"cid": "jpe01", "run": 2, "category": "fact", "expect": _case()["expect"],
            "time_anchor": _case()["time_anchor"], "anchor": "2026-10-14",
            "ctx_messages": msgs, "prompt_fp": ev._prompt_fp(msgs),
            "prompt_fp_stable": ev.stable_fp(msgs), "case_source_fp": "a" * 12,
            "n_ctx_msgs": len(msgs), "ctx_chars": sum(len(m["content"]) for m in msgs),
            "temperature": 0.0}
    prow.update(over)
    return prow


# ─────────────────────── ① 三种指纹分维，不许互相顶替 ───────────────────────

def test_来源指纹与输入指纹是两种东西_命名与取值都不混用():
    """`*_source_fp`（题集／尺子）与 `prompt_fp_stable`（那段输入文本）量的是不同对象。

    冒称同一种的后果：拿题面指纹回答「这三跑吃的是不是同一份料」⇒ 输入被插件改过也永远「相同」。
    """
    case, msgs = _case(), _msgs()
    cs, inp = ev.case_source_fp(case), ev.stable_fp(msgs)
    assert len(cs) == 12 and len(inp) == 12
    assert cs != inp, "题面指纹与输入指纹算出了同一个值＝其中一维是装饰"
    efp = ev.evaluator_source_fp()
    assert efp == ev.evaluator_source_fp(), "同一份源码两次取值不同＝这一维掺了时间戳"
    assert efp == hashlib.sha1(SCRIPT.read_bytes()).hexdigest()[:12], \
        "尺子指纹不是源码的 sha1 ⇒ 复算时认不出「谁改了判分器」"
    assert len({cs, inp, efp}) == 3, "三指纹里有两个相同＝至少一维没在量它声称的东西"


def test_三跑同源_题集来源不变而带时钟fp逐跑漂_stable不变():
    """同一道题跑三遍：来源指纹恒同、带活时钟的 `prompt_fp` 各不同、剥钟点的 stable 恒同。

    这正是 10-10 拍板「一致要剥钟点比」的理由——不剥就量成「过了几秒钟」。
    """
    case = _case()
    seq = (_msgs(_CLOCK_A), _msgs(_CLOCK_B), _msgs(_CLOCK_C))
    recs = [ev.make_private_evidence("generate",
                                     row={"cid": case["cid"], "run": i + 1,
                                          "prompt_fp": ev._prompt_fp(m),
                                          "prompt_fp_stable": ev.stable_fp(m)},
                                     case=case, run=i + 1, messages=m)
            for i, m in enumerate(seq)]
    assert len({r["case_source_fp"] for r in recs}) == 1, "同一道题的来源指纹漂了"
    assert len({r["evaluator_source_fp"] for r in recs}) == 1, "尺子没改，这一维却漂了"
    assert len({r["prompt_fp_stable"] for r in recs}) == 1, "只差墙上时钟就该是同一份输入"
    assert len({r["prompt_fp"] for r in recs}) == 3, "带时钟的 fp 三跑全同＝stable 那维失去对照"
    assert all(r["input_fp_verified"] is True for r in recs)


def test_实质输入变了stable跟着变_只有题面变了来源指纹才变():
    """两种「变」必须分开抓到：装配内容真变了 ⇒ stable 变；只改题面 ⇒ 只有来源指纹变。"""
    base = ev.stable_fp(_msgs())
    assert ev.stable_fp(_msgs(extra="\n本次检索到 3 条记忆")) != base, "实质输入变化没被抓到"
    assert ev.stable_fp(_msgs(_CLOCK_B)) == base, "只有钟点变了就该是同一份输入（复用 mask_clock 口径）"
    c1, c2 = _case(), _case(seeds=[{"content": "用户女儿叫小花，2020年出生"}])
    assert ev.case_source_fp(c1) != ev.case_source_fp(c2), "改了题面而来源指纹不动＝这一维空跑"
    assert ev.case_source_fp(c1) == ev.case_source_fp(_case()), "同内容不同对象就该同指纹"
    assert ev.case_source_fp({"b": 1, "a": 2}) == ev.case_source_fp({"a": 2, "b": 1}), \
        "sort_keys 没生效 ⇒ 同一份题面换个写法就换指纹，来源指纹成了噪声"
    assert ev.case_source_fp({}) != ev.case_source_fp(_case())


def test_缺stable字段就写unknown_不拿带时钟的prompt_fp冒充():
    """留底宁可承认「这一维没测到」，也不许用带活时钟的 fp 顶替 stable 那一维。"""
    rec = ev.make_private_evidence("generate", row={"cid": "x", "prompt_fp": "abc123def456"},
                                   case=_case(), messages=_msgs())
    assert rec["prompt_fp_stable"] == ev.UNKNOWN, "拿原始 fp 冒充 stable＝把「过了三秒」读成污染"
    assert rec["prompt_fp"] == "abc123def456", "对照维也不能丢，否则 clock_only_drift 那维没法算"
    assert rec["input_fp_verified"] == ev.UNKNOWN, "没有可对照的存值却报 verified＝自证是编的"
    assert rec["err"] == ev.UNKNOWN and rec["payloads_full"] == ev.UNKNOWN
    assert rec["truncated"] is False
    # 空串/空表是**真实读数**，不许一起吞成 unknown
    empty = ev.make_private_evidence("generate", row={"cid": "x", "pass": False, "err": "",
                                                      "mem_channel": []},
                                     case=_case(), actions=[])
    assert empty["pass"] is False and empty["err"] == "" and empty["mem_channel_full"] == [] \
        and empty["payloads_full"] == []


# ─────────────────────── ② 完整原文真的留住 ───────────────────────

def test_载荷超过70字_报表照旧截断_私有留底留全文():
    """`[:70]` 是公共报表口径，私有留底一位都不带过来——否则第 71 字之后永远查不到。"""
    rec = ev.make_private_evidence("generate", row={"cid": "jpe01"}, case=_case(),
                                   messages=_msgs(), actions=[_act("MEMO", {"text": _LONG_TEXT})])
    assert len(_LONG_TEXT) > 70, "夹具本身没越过 70 字 ⇒ 这条守卫空跑"
    assert rec["payloads_full"] == [_LONG_TEXT], "留底跟着截断＝这一维等于没留"
    assert rec["payloads_full"][0].endswith(_TAIL), "尾巴丢了：截断口径混进了留底"


def test_unicode与JSON载荷逐字留底_不吞不折(outside_repo):
    """中文/全角/emoji/JSON 字面量必须**逐字**往返：写进去读出来一模一样，不转义成 \\uXXXX。"""
    msgs = _msgs(extra=_JSON_UNICODE)
    rec = ev.make_private_evidence("judge", row={"cid": "juni", "run": 1}, case=_case(),
                                   messages=msgs,
                                   actions=[_act("MEMO", {"text": _JSON_UNICODE}),
                                            _act("SEARCH", {"query": "小花★🎈 生日"})],
                                   reply_text=_JSON_UNICODE)
    p = outside_repo / "evidence.jsonl"
    assert ev.write_private_evidence([rec], str(p)) == 1
    raw = p.read_text(encoding="utf-8")
    back = json.loads(raw.splitlines()[0])
    assert back["payloads_full"] == [_JSON_UNICODE, "小花★🎈 生日"], "unicode/JSON 载荷没逐字留住"
    assert back["reply_text_full"] == _JSON_UNICODE
    assert back["ctx_messages"] == msgs
    # 字面量走的是 `ensure_ascii=False`：中文/emoji 原样落盘（只有 JSON 自身的引号该转义）
    assert "◆JSON尾部◆" in raw and "🎈" in raw and "\\u" not in raw, "落盘被转义＝人读不动这份留底"


# ─────────────────────── ③ 默认路径不变；取料点真的接到 ───────────────────────

def _stub_generate(monkeypatch, reply=None):
    reply = reply or ("【MEMO】" + _LONG_TEXT + "【/MEMO】")
    msgs = _msgs()

    async def _state(case, cid, user_id, flags=None):
        return {"context_messages": msgs, "retrieved_memories": [], "temperature": 0.0}

    async def _llm(messages=None, **kw):
        return reply

    import app.agent.llm_client as llm_mod
    monkeypatch.setattr(ev, "gen_state", _state)
    monkeypatch.setattr(llm_mod, "chat_completion", _llm)
    return msgs, reply


def test_generate_case不传evidence就一个字的原文都不多留(monkeypatch):
    """默认输出兼容是硬要求：不显式启用 ⇒ 行里不多一个全文键，且整行与启用时逐字相同。"""
    msgs, reply = _stub_generate(monkeypatch)
    case = _case()
    row_plain = asyncio.run(ev.generate_case(case, 9001, 9002))
    bag = []
    row_ev = asyncio.run(ev.generate_case(case, 9001, 9002, evidence=bag, run=1))
    assert row_plain == row_ev, "启用留底改变了报表行 ⇒ 尺子的读数被留底功能本身污染了"
    assert all("full" not in k and "source" not in k for k in row_plain), list(row_plain)
    assert row_plain["payloads"] == [_LONG_TEXT[:70]], "公共报表的 70 字口径变了"
    assert row_plain["reply_text"] == reply[:900]
    assert len(bag) == 1 and bag[0]["payloads_full"] == [_LONG_TEXT] and bag[0]["run"] == 1
    assert bag[0]["case_source_fp"] == ev.case_source_fp(case)
    assert bag[0]["ctx_messages"] == msgs, "留底里的输入必须是**那一跑真正吃进去的**整段"
    assert bag[0]["evaluator_source_fp"] == ev.evaluator_source_fp()


def test_外部judge透传完整正文并继承导出行的来源指纹(monkeypatch):
    """导出腿算好的来源指纹要**原样传给** judge 腿，并用同一份 ctx 现算 stable 自证一次。"""
    from app.agent.actions import parse_actions
    full_reply = "【MEMO】" + _LONG_TEXT + "【/MEMO】\n" + "补充说明" * 300 + "★回复正文尾部★"
    prow = _prow()
    bag = []
    out = ev.judge_external_row(prow, full_reply, evidence=bag)
    assert len(bag) == 1
    rec = bag[0]
    want = [ev._payload_text(a.payload).strip()
            for a in parse_actions(full_reply, base_date="2026-10-14")]
    assert len(want[0]) > 70, "夹具没越过 70 字 ⇒ 这条守卫空跑"
    assert rec["payloads_full"] == want, "外部腿的载荷被截断＝私有留底不再是全文"
    assert out["payloads"] == [w[:70] for w in want], "公共报表的截断口径被留底改动带跑了"
    assert rec["reply_text_full"] == full_reply.strip()
    assert rec["case_source_fp"] == prow["case_source_fp"], \
        "来源指纹没继承导出行 ⇒ 两条腿对不到同一次导出"
    assert rec["stage"] == "judge" and rec["run"] == 2 and rec["input_fp_verified"] is True
    assert rec["expect"] == prow["expect"] and rec["time_anchor"] == prow["time_anchor"], \
        "判据没进留底 ⇒ 复算时只剩结果、拿不回判据"
    # 不传 evidence 时行为与改动前逐字一致
    assert ev.judge_external_row(prow, full_reply) == out


def test_旧导出行没有来源指纹时judge留底写unknown不猜():
    """改动之前导出的 prompt 文件没这两维 ⇒ 认「没测到」，不拿别的值凑。"""
    prow = _prow()
    prow.pop("case_source_fp")
    bag = []
    ev.judge_external_row(prow, "【MEMO】小花今年三岁生日【/MEMO】", evidence=bag)
    assert bag[0]["case_source_fp"] == ev.UNKNOWN, "缺来源指纹就写 unknown，别用输入指纹冒充"


def test_两条腿的留底都接在截断之前_导出行只多两个来源键():
    """接线守卫：留底必须**就地**取全料（折叠侧手上已经只剩 70 字），跑次坐标也要接进去。"""
    ex = inspect.getsource(ev.run_export_eval)
    assert '"case_source_fp": case_source_fp(c)' in ex and '"evaluator_source_fp": esfp' in ex, \
        "导出行没带来源指纹 ⇒ 外部腿与 judge 腿没法对同一次导出"
    assert 'make_private_evidence(\n                        "export"' in ex, "导出腿没接留底"
    gc = inspect.getsource(ev.generate_case)
    assert "evidence is not None" in gc and "actions=actions" in gc and "reply_text=text or " in gc, \
        "生成腿的留底不是就地取料（会拿到已截断的载荷）"
    assert "evidence=evidence, run=rep_i + 1" in inspect.getsource(ev.run_generate_eval), \
        "跑次坐标没接进去 ⇒ 三跑留底全打在 unknown 上"
    m = inspect.getsource(ev.main)
    assert m.index("guard_private_evidence_path") < m.index("await run_generate_eval"), \
        "路径校验排在计费之后 ⇒ 钱花了才发现留底写不成"


# ─────────────────────── ④ 落盘闸：仓库外 / 写失败 / 凭据 / stdout ───────────────────────

def test_留底路径在仓库内一律拒绝(outside_repo):
    """仓库里的文件会被同步、被提交 ⇒ 完整原文一律不许落在仓库内。"""
    root = ev.repo_root_dir()
    for bad in (str(REPO / "leak.jsonl"), str(REPO / "backend" / "leak.jsonl"), str(REPO),
                str(REPO / "docs" / "sub" / "x.jsonl"), str(REPO / "docs")):
        with pytest.raises(ev.PrivateEvidenceError) as e:
            ev.guard_private_evidence_path(bad)
        assert "仓库外" in str(e.value), bad
    good = ev.guard_private_evidence_path(str(outside_repo / "ev.jsonl"))
    assert good and not os.path.normcase(good).startswith(root + os.sep)
    assert os.path.normcase(str(REPO)).startswith(root), "仓库根算错了 ⇒ 上面那些拒绝都是巧合"
    # 只以仓库名为**前缀**的兄弟目录不算仓库内（判定用 `root + os.sep`，不是裸 startswith）。
    # 拿仓库外的一对真目录演这条边界：既不碰工作区，也不会被「目录不存在」那一支岔过去。
    fake_root, sib = outside_repo / "repo", outside_repo / "repo_别踩这个前缀"
    fake_root.mkdir()
    sib.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ev, "repo_root_dir", lambda: os.path.normcase(str(fake_root)))
        assert os.path.normcase(str(sib)).startswith(os.path.normcase(str(fake_root))), \
            "夹具不对：这个兄弟目录没踩到前缀 ⇒ 下面那句「放过」是巧合"
        assert ev.guard_private_evidence_path(str(sib / "ev.jsonl"))     # 兄弟目录：放过
        for bad2 in (str(fake_root / "leak.jsonl"), str(fake_root)):
            with pytest.raises(ev.PrivateEvidenceError) as e2:
                ev.guard_private_evidence_path(bad2)
            assert "仓库外" in str(e2.value), bad2


def test_相对路径按当前工作目录判定_在仓库内照样拒(monkeypatch):
    """相对名字不是「没路径」：它按 CWD 展开，而 pytest 的 CWD 就在 backend/ 里。"""
    monkeypatch.chdir(REPO / "backend")
    with pytest.raises(ev.PrivateEvidenceError):
        ev.guard_private_evidence_path("leak.jsonl")
    with pytest.raises(ev.PrivateEvidenceError):
        ev.guard_private_evidence_path("   ")


def test_软链接落回仓库内部同样拒绝(outside_repo):
    """只看 `abspath` 会被软链接绕过去：链接本身在仓库外，内容却写进仓库里。"""
    link = outside_repo / "link_into_repo"
    try:
        os.symlink(str(REPO), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机无创建符号链接权限（开发者模式未开）")
    p = os.path.join(str(link), "leak.jsonl")
    assert not os.path.normcase(os.path.abspath(p)).startswith(
        os.path.normcase(ev.repo_root_dir())), "夹具不对：规范化口径本来就该判成仓库内"
    with pytest.raises(ev.PrivateEvidenceError) as e:
        ev.guard_private_evidence_path(p)
    assert "仓库外" in str(e.value), "软链接指回仓库没被拦 ⇒ 留底会直接写进工作区"
    with pytest.raises(ev.PrivateEvidenceError):
        ev.write_private_evidence([ev.make_private_evidence("generate", row={"cid": "x"},
                                                            case=_case(), messages=_msgs())], p)
    assert not (REPO / "leak.jsonl").exists(), "拒写没生效：工作区被落了一个文件"


def test_留底目录不存在就报错_绝不伪称保存成功(outside_repo):
    with pytest.raises(ev.PrivateEvidenceError) as e:
        ev.guard_private_evidence_path(str(outside_repo / "没有这个目录" / "ev.jsonl"))
    assert "拒写" in str(e.value)


def test_写盘失败当场抛_开文件前可承诺零写入_开文件后只说可能已写部分(outside_repo):
    """两支 OSError 的**可承诺范围不同**，报错口径必须分开（10-14 主审点名「不谎称一行没落」）。

    - 开文件之前（路径闸／序列化闸／凭据闸）＝一行都没写，可以理直气壮说「整批拒写」；
    - 开文件之后＝追加语义下失败点未知，前面的行已经进去了 ⇒ 只能报「可能已写入部分行，先核对」。
      若这里还宣称「一行都没落」，人会把半份留底当旧文件直接复跑，比不留更糟。
    """
    rec = ev.make_private_evidence("generate", row={"cid": "x"}, case=_case(), messages=_msgs())

    # ① 开文件就失败（同名占了个目录 ⇒ Windows Permission denied）
    blocked = outside_repo / "ev_blocked"
    blocked.mkdir()
    with pytest.raises(ev.PrivateEvidenceError) as e:
        ev.write_private_evidence([rec, rec], str(blocked))
    assert "写盘失败" in str(e.value)
    assert "可能已写入部分" in str(e.value) and "不得当作零写入" in str(e.value), \
        "开文件之后的失败点未知，报错却仍承诺零写入 ⇒ 会诱导人复跑半份留底"
    assert "一行都没落" not in str(e.value), "旧口径没改净：仍在谎称一行都没落"
    assert list(blocked.iterdir()) == [], "失败目标里凭空多出东西"

    # ② 对照：同一个批次换个能写的目标，两行一起落（证明上面那次是真失败，不是夹具空跑）
    good = outside_repo / "ok.jsonl"
    assert ev.write_private_evidence([rec, rec], str(good)) == 2
    assert len(good.read_text(encoding="utf-8").splitlines()) == 2


def test_第二行写失败_磁盘上真留一行_报错必须认出那半份(outside_repo, monkeypatch):
    """半份留底不是假设：第 1 行进、第 2 行抛 ⇒ 磁盘上确实躺着 1 行，报错必须与磁盘相符。"""
    rec = ev.make_private_evidence("generate", row={"cid": "half"}, case=_case(), messages=_msgs())
    target = outside_repo / "half.jsonl"
    real_open = open

    class _OneLineThenBoom:
        """替身句柄：写满一行就抛 OSError（真实失败点＝磁盘满／句柄被抢／进程被砍）。"""

        def __init__(self):
            self._buf = real_open(str(target), "a", encoding="utf-8", newline="\n")
            self._n = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._buf.close()
            return False

        def write(self, s):
            self._n += 1
            if self._n > 1:
                raise OSError(28, "夹具：设备已满")
            with real_open(str(target), "a", encoding="utf-8", newline="\n") as f:
                f.write(s)

        def flush(self):
            pass

        def fileno(self):
            return self._buf.fileno()

    monkeypatch.setattr(ev, "open", lambda *a, **k: _OneLineThenBoom(), raising=False)
    with pytest.raises(ev.PrivateEvidenceError) as e:
        ev.write_private_evidence([rec, rec], str(target))
    monkeypatch.undo()
    msg = str(e.value)
    assert "可能已写入部分" in msg and "本批 2 行" in msg, msg
    assert "不得当作零写入" in msg and "先核对" in msg
    assert len(target.read_text(encoding="utf-8").splitlines()) == 1, \
        "磁盘上没留下那半份 ⇒ 这块电池空跑（第 1 行本该进去）"


def test_inf与nan整批拒写_不是合法json也不许冒充无损(outside_repo):
    """`allow_nan=True`（json 默认）会把 `Infinity`／`NaN` 原样写进去：`json.loads` 能吞，
    严格解析器直接拒 ⇒ 一个坏数冒充「无损留住了」。这一支必须在**开文件之前**拦下。"""
    p = outside_repo / "nan.jsonl"
    for bad in (float("inf"), float("nan")):
        rec = ev.make_private_evidence("judge", row=_row_min(temperature=bad), case=_case(),
                                       messages=_msgs())
        with pytest.raises(ev.PrivateEvidenceError) as ei:
            ev.write_private_evidence([rec], str(p))
        assert "无法无损序列化" in str(ei.value) and "整批拒写" in str(ei.value)
        assert not p.exists(), "拒写却落了文件"


def test_异常文本自带原文时也不许外泄_只报异常类型(outside_repo):
    """恶意容器：它抛的 `ValueError` 文本**里就带着载荷原文** ⇒ 报错只许给异常类型名。

    `json` 的 C 编码器在 `allow_nan=False` 命中坏数时，把 `repr(o)` 拼进异常文本；载荷只要是一个
    `__repr__` 会回吐原文的对象，写成 `%s % e`（或 `repr(e)`）就等于把用户正文抄进 stderr／日志
    ——日志是公共面，留底才是它的家。
    """
    class _LeakyFloat(float):
        def __repr__(self):
            return "◆载荷原文回流◆" + str(float(self))

    bad = _LeakyFloat(float("inf"))
    with pytest.raises(ValueError) as raw:            # 夹具自证：这个向量**真的**会回显原文
        json.dumps({"v": bad}, ensure_ascii=False, sort_keys=True, allow_nan=False)
    assert "载荷原文回流" in str(raw.value), "夹具失效：这个容器不再回显原文 ⇒ 下面全是假绿"

    rec = ev.make_private_evidence("judge", row=_row_min(temperature=bad), case=_case(),
                                   messages=_msgs())
    p = outside_repo / "leak.jsonl"
    with pytest.raises(ev.PrivateEvidenceError) as ei:
        ev.write_private_evidence([rec], str(p))
    msg = str(ei.value)
    assert "载荷原文回流" not in msg, "报错文本回显了载荷原文"
    assert "异常类型=ValueError" in msg and "不回显异常文本" in msg
    assert not p.exists()


def test_凭据形态命中就整批拒写(outside_repo):
    """留底可以有完整原文，但**不能有密钥**：命中就一行都不写，也不回显命中的内容。"""
    p = outside_repo / "ev.jsonl"
    secret = "sk-" + "ABCDEFGHIJKLMNOPQRST"
    ok = ev.make_private_evidence("generate", row={"cid": "clean"}, case=_case(), messages=_msgs())
    bad = ev.make_private_evidence("generate", row={"cid": "dirty"}, case=_case(),
                                   messages=_msgs(), reply_text="key " + secret + " =")
    with pytest.raises(ev.PrivateEvidenceError) as e:
        ev.write_private_evidence([ok, bad], str(p))
    assert not p.exists(), "干净行先落了盘＝脏行没拦住还谎报了进度"
    assert "凭据" in str(e.value) and "ABCDEFGHIJKLMNOPQRST" not in str(e.value)
    assert ev.write_private_evidence([ok], str(p)) == 1        # 干净批次照常写
    ev.assert_no_secret(ok)                                    # 干净行不触发闸


def test_judge档整条路跑通_完整原文不进公共stdout(outside_repo, monkeypatch, capsys):
    """默认兼容＋隐私边界一次跑清：报表与 stdout 保持截断口径，全文只落那一个私有文件。"""
    _stub_generate(monkeypatch)
    full_reply = "【MEMO】" + _LONG_TEXT + "【/MEMO】\n" + "补" * 1200 + "★尾部不公共★"
    msgs = _msgs()
    prow = _prow(msgs, run=1, case_source_fp="b" * 12)
    pf, rf = outside_repo / "prompts.jsonl", outside_repo / "replies.jsonl"
    pf.write_text(json.dumps(prow, ensure_ascii=False) + "\n", encoding="utf-8")
    rf.write_text(json.dumps({"cid": prow["cid"], "run": 1, "text": full_reply,
                              "sent_fp": ev.sent_fp_of(msgs)}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    rep_md, evi = outside_repo / "rep.md", outside_repo / "ev.jsonl"
    base = ["x", "--mode", "judge", "--judges", "J1", "--prompts", str(pf), "--replies", str(rf),
            "--out", str(rep_md)]
    monkeypatch.setattr("sys.argv", base + ["--emit-private-evidence", str(evi)])
    assert asyncio.run(ev.main()) == 0
    cap = capsys.readouterr()
    # 公共侧的口径一位没变：回复全文只到 900 字（尾部标记在窗口外），动作载荷列只到 70 字
    assert "★尾部不公共★" not in cap.out and "★尾部不公共★" not in cap.err, "全文漏进公共 stdout"
    assert _HEAD[:70] + " |" in cap.out and "| " + _LONG_TEXT + " |" not in cap.out, \
        "报表「动作载荷」列的 70 字口径被留底改动带跑了"
    assert "★尾部不公共★" not in rep_md.read_text(encoding="utf-8"), "全文漏进报表"
    assert "[私有留底] 1 行" in cap.err and str(evi) in cap.err
    rec = json.loads(evi.read_text(encoding="utf-8").splitlines()[0])
    assert "★尾部不公共★" in rec["reply_text_full"] and rec["payloads_full"][0].endswith(_TAIL)
    assert rec["case_source_fp"] == "b" * 12 and rec["stage"] == "judge"

    # 不给这个开关 ⇒ 不新增任何留底文件（目录里除了题面/回复/报表，没有第四样东西）
    before = sorted(os.listdir(str(outside_repo)))
    monkeypatch.setattr("sys.argv", base)
    assert asyncio.run(ev.main()) == 0
    assert sorted(os.listdir(str(outside_repo))) == before, "没启用私有留底却多落了文件"


def test_私有留底开关只认生成端三条腿(tmp_path):
    """给错档就拒，不静默忽略：score／certify／recount 档没有原文可留。"""
    pe = str(tmp_path / "e.jsonl")
    for mode, kw in (("score", {}), ("certify", {}),
                     ("recount", {"rows_path": str(tmp_path / "r.jsonl")})):
        why = ev.refusal(["J1"], mode, True, private_evidence=pe, **kw)
        assert "emit-private-evidence" in why, "%s 档该拒却没拒" % mode
    assert ev.refusal(["J1"], "generate", True, private_evidence=pe) == ""
    assert ev.refusal(["J1"], "export", False, private_evidence=pe) == ""
    assert ev.refusal(["J1"], "judge", False, private_evidence=pe,
                      prompts_path=str(tmp_path / "p.jsonl"),
                      replies_path=str(tmp_path / "r.jsonl")) == ""
    # 不带这个开关时，三道闸的原有行为一个都没被动过
    assert ev.refusal(["J1"], "recount", False) != "" and ev.refusal(["J1"], "generate", False) != ""


# ─────────────────────── ⑤ 变异电池（内存内精确隔离，跑完自动恢复） ───────────────────────

def _全套留底断言(rec):
    """被两条变异打红的正是这几句——抽成函数才能证明「红」发生在断言而不是夹具。"""
    assert rec["payloads_full"] == [_LONG_TEXT], "留底被截断"
    assert rec["truncated"] is False


def test_变异电池M1_把留底截回70字则守卫当场红_恢复后重新变绿(monkeypatch):
    orig = ev.make_private_evidence

    def _muted(stage, **kw):
        rec = orig(stage, **kw)
        if isinstance(rec.get("payloads_full"), list):
            rec["payloads_full"] = [p[:70] for p in rec["payloads_full"]]
        return rec

    case = _case()
    _stub_generate(monkeypatch)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ev, "make_private_evidence", _muted)
        bag = []
        asyncio.run(ev.generate_case(case, 9001, 9002, evidence=bag))
        assert bag[0]["payloads_full"] == [_LONG_TEXT[:70]], "变异没生效 ⇒ 这块电池空跑"
        with pytest.raises(AssertionError):
            _全套留底断言(bag[0])
    bag2 = []
    asyncio.run(ev.generate_case(case, 9001, 9002, evidence=bag2))
    _全套留底断言(bag2[0])                                 # 恢复：同一条断言重新成立


def test_变异电池M2_仓库闸形同虚设则拒绝守卫当场红_恢复后重新咬(outside_repo):
    """反证：路径闸如果只认 abspath 不认 realpath，「仓库内拒写」这条守卫必须当场红。

    这块电池**只变异判定函数本身、一个字节都不写**：真去写就会顺着软链接把文件落进工作区——
    那正是这条闸要防的事，也不许拿生产工作区当变异靶子。
    """
    orig = ev.guard_private_evidence_path
    link = outside_repo / "link_repo_m2"
    try:
        os.symlink(str(REPO), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机无创建符号链接权限（开发者模式未开）")
    p = os.path.join(str(link), "leak.jsonl")
    root = os.path.normcase(ev.repo_root_dir()) + os.sep
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ev, "guard_private_evidence_path", lambda path: os.path.abspath(path))
        got = ev.guard_private_evidence_path(p)
        assert got == os.path.abspath(p), "变异没生效 ⇒ 这块电池空跑"
        assert not os.path.normcase(got).startswith(root), "变异后的闸还拦得住 ⇒ 这条守卫红不了"
    with pytest.raises(ev.PrivateEvidenceError):                      # 恢复：真闸重新咬住
        orig(p)


# ══════════════ 主审返工（2026-10-14）：动作原文留底 ＋ 导出腿尺子来源分维 ══════════════
#
# 主审点名的两处都在同一个毛病上——**宣称「留住了」而实际没留住**：
# ① `payloads_full` 走 `_payload_text`（只拼 query/text/prompt/match），于是 `CAL_NOTE.date`／
#    `NOTE_DONE.type`／`TIMER.tag` 以及载荷里的未知合法键、JSON 嵌套字段全被摊平丢掉，
#    行里却写着 `truncated:false`。处理：兼容文本字段**保留**，另增 `actions_full`
#    （动作类型＋完整载荷＋原始标记串）。
# ② judge 那一行只有「当前尺子」`evaluator_source_fp`，没有「导出时用的那把尺子」⇒ 两条腿
#    不同源会被静默抹平成同源。处理：分维新增 `export_evaluator_source_fp`（继承导出行，
#    缺失＝`unknown`，**绝不拿当前值顶替**）。
# ③ 这两条都必须在**真链路**上验。报告 §7 第 2 条自己登记过「export→judge 跨进程一次都没真跑」，
#    而 `inspect.getsource` 不是执行证据 ⇒ 本节用独立进程跑 CLI（零模型：export 档 LLM 入口打桩，
#    judge 档不碰模型不碰库）。
import subprocess  # noqa: E402  （放在本节，避免动上半部既有 import 顺序）
import sys  # noqa: E402

_LONG_MEMO = "甲" * 74 + "◆载荷尾巴◆"            # 80 字：顶满生产 [:80]，同时越过评测的 70 字截断
_DATE_ONLY = "2026-11-20"
_TAIL_BEYOND_900 = "◆900字窗外的载荷尾◆"
_FULL_REPLY_TAIL = "乙" * 68 + _TAIL_BEYOND_900   # 79 字，落在公共 900 字窗之外


def _full_act(action_type, payload, raw=""):
    """带 `raw`（原始标记串）的假动作：`AgentAction` 的三件套，留底要的就是这套原文。"""
    return types.SimpleNamespace(action_type=action_type, payload=payload, raw=raw)


def _nested_payload():
    return {"date": _DATE_ONLY, "text": _LONG_MEMO,
            "recurrence": {"每周": {"周三": ["接孩子"]}}, "unknown_legal_key": "未知合法键◆"}


def _row_min(**over):
    r = {"cid": "a30pe01", "run": 1, "prompt_fp_stable": "ab" * 6}
    r.update(over)
    return r


def test_actions_full_保住动作类型与完整载荷_date_未知键与JSON嵌套一位不丢():
    acts = [_full_act("CAL_NOTE", _nested_payload(), "[CAL_NOTE]%s 18:00 接孩子[/CAL_NOTE]" % _DATE_ONLY),
            _full_act("NOTE_DONE", {"type": "calendar", "match": "接孩子"}, "[CAL_DONE]接孩子[/CAL_DONE]"),
            _full_act("TIMER", {"tag": "[timer]周五 18:00◆标记串尾部◆[/timer]"},
                      "[timer]周五 18:00◆标记串尾部◆[/timer]")]
    rec = ev.make_private_evidence("judge", row=_row_min(), actions=acts, case_source="c" * 12)

    # 兼容文本字段一位都不改（旧读法照旧）——并**自证**它留不住 date/type/tag：
    assert rec["payloads_full"] == [_LONG_MEMO, "接孩子", ""]
    af = rec["actions_full"]
    assert [a["action"] for a in af] == ["CAL_NOTE", "NOTE_DONE", "TIMER"]
    assert af[0]["payload"] == _nested_payload()
    assert af[0]["payload"]["date"] == _DATE_ONLY
    assert af[0]["payload"]["recurrence"] == {"每周": {"周三": ["接孩子"]}}, "JSON 嵌套字段被摊平了"
    assert af[0]["payload"]["unknown_legal_key"] == "未知合法键◆", "未知合法键被挑掉了"
    assert af[1]["payload"]["type"] == "calendar"
    assert af[2]["payload"]["tag"] == "[timer]周五 18:00◆标记串尾部◆[/timer]"
    assert af[0]["raw"].startswith("[CAL_NOTE]"), "原始标记串没留 ⇒ 复算不出当时那条标记"
    assert rec["truncated"] is False

    # `None`（这一腿没产出可留）与 `[]`（真取了零个动作）仍是两件事
    assert ev.make_private_evidence("export", row=_row_min(), actions=None)["actions_full"] == ev.UNKNOWN
    assert ev.make_private_evidence("generate", row=_row_min(), actions=[])["actions_full"] == []


def test_载荷无法无损序列化_留底不当场str伪造_写盘整批明确拒写(outside_repo):
    """`default=str` 会把 tuple 伪造成一段看着完整的字符串、`truncated:false` 就成了假话。"""
    payload = {"date": (_DATE_ONLY,), "text": "接孩子", "extra": {"深": {"更": "值"}}}
    rec = ev.make_private_evidence("judge", row=_row_min(),
                                   actions=[_full_act("CAL_NOTE", payload, "[CAL_NOTE]…[/CAL_NOTE]")])
    assert isinstance(rec["actions_full"][0]["payload"]["date"], tuple), \
        "收集期就把 tuple 兜成 str ⇒ 留底在伪称完整性"
    p = str(outside_repo / "refuse.jsonl")
    with pytest.raises(ev.PrivateEvidenceError) as ei:
        ev.write_private_evidence([rec], p)
    msg = str(ei.value)
    assert "无损序列化" in msg and "整批拒写" in msg
    assert not os.path.exists(p), "拒写却落了文件 ⇒ 半份留底比不留更糟"


def test_judge留底分维保留导出腿尺子来源_缺失写unknown不拿当前值顶替():
    """**不同源反例**：导出行 stamped 一把「旧尺子」，judge 用的是「当前尺子」，两维必须并存且不等。"""
    old_fp = "deadbeef0000"
    bag = []
    ev.judge_external_row(_prow(evaluator_source_fp=old_fp), "[MEMO]%s[/MEMO]" % _LONG_MEMO,
                          evidence=bag)
    rec = bag[0]
    cur = ev.evaluator_source_fp()
    assert rec["export_evaluator_source_fp"] == old_fp
    assert rec["evaluator_source_fp"] == cur
    assert rec["export_evaluator_source_fp"] != rec["evaluator_source_fp"], "两维被抹平成同源了"

    # 旧导出文件没带这一维 ⇒ unknown，而不是拿当前尺子冒充「当时用的那把」
    prow2 = _prow()
    prow2.pop("evaluator_source_fp", None)
    assert "evaluator_source_fp" not in prow2
    bag2 = []
    ev.judge_external_row(prow2, "[MEMO]记一下[/MEMO]", evidence=bag2)
    assert bag2[0]["export_evaluator_source_fp"] == ev.UNKNOWN
    assert bag2[0]["evaluator_source_fp"] == cur

    # 导出档自己那一行：export 腿与尺子同进程 ⇒ 两维相等（这才是真同源，不是顶替出来的）
    msgs = _msgs()
    prow3 = dict(_prow(), evaluator_source_fp=cur, ctx_messages=msgs)
    exp_rec = ev.make_private_evidence("export", row=prow3, case=_case(), messages=msgs)
    assert exp_rec["export_evaluator_source_fp"] == cur == exp_rec["evaluator_source_fp"]
    # generate 档没有独立的导出腿 ⇒ 这一维只能 unknown，不许顺手写成「当前尺子」冒充同源
    gen_rec = ev.make_private_evidence("generate", row={"cid": "x", "run": 1}, case=_case(), actions=[])
    assert gen_rec["export_evaluator_source_fp"] == ev.UNKNOWN


# ─────────────── 跨进程整链：export CLI → 合成外部回复 → judge CLI（零模型） ───────────────

def _cli_py():
    py = REPO / "backend" / ".venv" / "Scripts" / "python.exe"
    return str(py) if py.is_file() else sys.executable


def _uri(p):
    """与 `_init_temp_env` 同一形态的 sqlite URI（POSIX 斜杠、三斜杠起头）。"""
    return "sqlite+aiosqlite:///" + str(p).replace(os.sep, "/")


def _base_child_env(root, *, journal=None, mutate=None, refuse=False):
    """**所有**跨进程夹具的唯一环境入口：一切路径显式落进 pytest 沙箱 `root`，绝不继承「裸 os.environ」。

    为什么这条必须写死（10-14 事故）：子进程如果拿到一个没有 `DATABASE_URL` 的环境，`app.config`
    会退回生产库路径，而评测器在 `_init_temp_env` **之前**就可能把 engine 绑上去 ⇒ 写进生产库。
    这里把 `DATABASE_URL` 钉成 `root` 内的一份**哨兵库**（只有一张 marker 表），并把 `TEMP`／
    `TMP`／`TMPDIR` 也钉进 `root`（评测器自己 `mkdtemp` 出来的临时库因此同样落在沙箱里），
    `CHROMA_PERSIST_DIR` 亦然。回归了只伤这份临时哨兵，碰不到生产、也碰不到会话共享库。
    """
    env = {
        "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": _uri(root / "sentinel.db"),
        "CHROMA_PERSIST_DIR": str(root / "chroma"),
        "TEMP": str(root), "TMP": str(root), "TMPDIR": str(root),
        "A30_SENTINEL": str(root / "sentinel.db"),
        "PYTHONWARNINGS": "ignore",
    }
    for k in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "USERPROFILE", "HOME", "HOMEDRIVE",
              "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PATHEXT", "LANG", "LC_ALL"):
        if k in os.environ:
            env[k] = os.environ[k]
    if journal is not None:
        env["A30_DB_JOURNAL"] = str(journal)
        driver = root / "a30_iso_driver.py"
        driver.write_text(_DRIVER_SRC, encoding="utf-8")
        env["A30_DRIVER"] = str(driver)
    if mutate:
        env["A30_ISO_MUT"] = mutate
        env["A30_ISO_MUTS"] = _MUTS_B64
    if refuse:
        env["A30_DB_REFUSE_SENTINEL"] = "1"
    for k in ("A30_ISO_MUT", "A30_ISO_MUTS"):      # 变异经 pytest 子进程再往下传给 CLI 孙进程
        if k in os.environ:
            env.setdefault(k, os.environ[k])
    return env


def _pytest_child_env(root, **extra):
    """给「子 pytest 会话」用的环境：**只加不改**——那一层自己也是 pytest，`conftest.py` 会把
    `DATABASE_URL`/`CHROMA_PERSIST_DIR` 指到它自己的会话沙箱；我们这层若替它把 `DATABASE_URL` 钉成
    哨兵库，会话夹具会被顶掉（实测 `ERROR at setup`，红得不对题＝这块电池空跑）。
    同理不能钉 `TEMP`：`--basetemp` 按本项目约定落在**仓库内**，钉住它会让子会话里的
    `outside_repo` 夹具判成「夹具目录落进仓库了」而整批 ERROR（也是红得不对题）。
    需要哨兵库的是**孙进程 CLI**，那一条走 `_base_child_env`；变异名经 env 原样往下传。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONPATH=str(root), **extra)
    return env


def _run_cli(*args, cwd=None, env=None, driver=False):
    """跑 CLI：`driver=True` 走隔离守卫驱动（记录每一次 engine 绑定 URL），否则直接跑脚本。

    `env` **必填**——留空就当场炸，避免又出现「继承裸 os.environ ⇒ 没设 DATABASE_URL」的那一条路。
    """
    assert env is not None, "跨进程夹具必须显式给沙箱环境（_base_child_env）"
    script = [str(SCRIPT)]
    if driver:
        script = [str(Path(env["A30_DRIVER"])), str(SCRIPT)]
    cmd = [_cli_py()] + script + [str(x) for x in args]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=str(cwd or REPO / "backend"), timeout=180)
    assert p.stdout is not None and p.stderr is not None, (
        "子进程输出没被解码（rc=%s）＝夹具自己的编码假设坏了" % p.returncode)
    return p.returncode, p.stdout, p.stderr


def _chain_case():
    """合成题（不取真实题集）：形状按 `lint_dataset` 的 15 个必填键出，判据只要 J1 能判就行。"""
    return {"cid": "a30pe01", "category": "temporal", "provenance": "synthetic",
            "persona": {"user": "化名『小舟』，评测合成角色", "character": "评测专用角色"},
            "seeds": [{"content": "用户周六上午要给母亲过生日", "memory_type": "fact", "importance": 70,
                       "date": "2026-08-11", "source": "chat", "epistemic_status": "USER_STATED"}],
            "distractors": [{"content": "用户曾在南京读大学，同学都在那边", "date": "2026-09-02"},
                            {"content": "用户老家在长沙，父母住那", "date": "2026-09-02"}],
            "turn": "周六别给我安排太早的事，家里有安排",
            "context_before": [{"role": "user", "text": "今天有点闷，随便聊聊"},
                               {"role": "assistant", "text": "嗯，我在。"}],
            "expect": {"gold_seed_idx": [0], "abstain": False,
                       "action_type": ["CAL_NOTE", "MEMO"], "field_match": {"*": "生日"},
                       "forbidden": []},
            "judge": "J1",
            "time_anchor": {"as_of": "2026-09-25", "tz": "Asia/Shanghai",
                            "expect_date": "2026-09-26", "expect_offset_days": None},
            "solvability": {"certified": False, "certified_at": None,
                            "blocked_reason": "测试合成夹具：未做三跑认证"},
            "config_matrix": ["baseline"], "flags_required": {}, "tokens_budget": 320}


_EXT_REPLY = ("好的，我记下了。\n[MEMO]%s[/MEMO]\n[CAL_NOTE]%s 18:00 接孩子放学◆日程尾部◆[/CAL_NOTE]\n"
              "[CAL_DONE]接孩子[/CAL_DONE]\n%s\n[MEMO]%s[/MEMO]" % (
                  _LONG_MEMO, _DATE_ONLY, "补记几句闲话。" + "哈" * 900, _FULL_REPLY_TAIL))


@pytest.fixture(scope="module")
def chain_export(tmp_path_factory):
    """模块级：跑**一次** `--mode export`（独立进程、零计费），供整链两发复用。

    为什么必须独立进程：`app.db.database` 的 engine 按进程缓存，pytest 进程里起不了这一腿。
    沙箱口径（10-14 事故后重写）：目录由 **pytest 的 `tmp_path_factory`** 给（不再是裸
    `tempfile.TemporaryDirectory`），子进程的 `DATABASE_URL`/`TEMP`/`CHROMA_PERSIST_DIR` 全部
    显式落进这个目录，并开 engine 绑定日志 ⇒ 即便回归也只会伤这份临时哨兵。
    留底路径仍受「仓库外」闸约束。
    """
    root = Path(tmp_path_factory.mktemp("a30_chain"))
    # 两个口径在这儿会打架，必须分开：①**跨进程环境**（DATABASE_URL 哨兵／TEMP／chroma／绑定日志）由
    # pytest 沙箱给（`root`），项目跑测试用 `--basetemp=.pytest_tmp`＝仓库内，这是本仓库的既定口径；
    # ②**私有留底**受「必须仓库外」闸约束（仓库里的东西会被同步／提交）。所以留底另落 `TemporaryDirectory`，
    # 夹具先自证它不在仓库里，否则下面全是假绿。
    if os.path.normcase(str(root)).startswith(os.path.normcase(ev.repo_root_dir()) + os.sep):
        td = tempfile.TemporaryDirectory(prefix="ambrace_a30_ev_", dir=Path.home())
        ev_root = Path(td.name)
    else:
        td, ev_root = None, root
    assert not os.path.normcase(str(ev_root)).startswith(
        os.path.normcase(ev.repo_root_dir()) + os.sep), "留底目录落进仓库了 ⇒ 下面全是假绿"
    journal = root / "engine_journal.jsonl"
    _write_sentinel(root / "sentinel.db")
    before = _sentinel_snapshot(root / "sentinel.db")
    env = _base_child_env(root, journal=journal)
    ds = root / "cases.jsonl"
    ds.write_text(json.dumps(_chain_case(), ensure_ascii=False) + "\n", encoding="utf-8")
    prompts = root / "prompts.jsonl"
    ev_export = ev_root / "ev_export.jsonl"
    rc, out, err = _run_cli("--dataset", ds, "--judges", "J1", "--mode", "export",
                            "--repeat", "2", "--fillers", "6", "--limit", "1",
                            "--emit-prompts", prompts,
                            "--emit-private-evidence", ev_export,
                            "--out", root / "report_export.md", env=env, driver=True)
    assert rc == 0, "export 档没跑通（rc=%s）：%s" % (rc, (err or out)[-900:])
    assert before == _sentinel_snapshot(root / "sentinel.db"), \
        "export 子进程动了外部哨兵库 ⇒ 隔离破了（详见 test_隔离守卫_…）"
    urls = _journal_urls(journal)
    assert urls, "没记录到任何 engine 绑定 URL ⇒ 这条沙箱断言空跑"
    for u in urls:
        assert os.path.normcase(str(root)) in os.path.normcase(u.replace("/", os.sep)), \
            "子进程 engine 绑到了沙箱之外：%s" % u
        assert "sentinel.db" not in u, "engine 提前绑在继承来的哨兵库上：%s" % u
    prows = [json.loads(l) for l in prompts.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(prows) == 2, "两跑没导出来 ⇒ 后面全空跑"
    yield {"dir": root, "prompts": prompts, "prows": prows, "ev_export": ev_export,
           "env": env, "ev_dir": ev_root}
    if td:
        td.cleanup()


def _judge_run(env, prompts, ev_path=None, rows=None, out=None):
    args = ["--judges", "J1", "--mode", "judge", "--prompts", prompts,
            "--replies", env["dir"] / "replies.jsonl",
            "--out", out or (env["dir"] / "rep_judge.md")]
    if ev_path:
        args += ["--emit-private-evidence", ev_path]
    if rows:
        args += ["--emit-rows", rows]
    return _run_cli(*args, env=env["env"])


def _write_replies(env, text):
    """合成「外部腿跑回来的回复」：`sent_fp` 必须是**这份 prompt 渲染文本**的指纹，
    否则 judge 会把它判成「对不上题面＝没量到」而整条不进留底（这条配对闸本身就是尺子）。"""
    (env["dir"] / "replies.jsonl").write_text(
        "".join(json.dumps({"cid": r["cid"], "run": r["run"], "text": text,
                            "sent_fp": ev.sent_fp_of(r["ctx_messages"] or [])},
                           ensure_ascii=False) + "\n" for r in env["prows"]), encoding="utf-8")


def test_跨进程整链_export到judge_全文与非文本字段沿途留住_同开emit_rows也不漏私有原文(chain_export):
    env = chain_export
    _write_replies(env, _EXT_REPLY)

    ev_on = env["ev_dir"] / "ev_judge.jsonl"
    rows_on = env["dir"] / "rows_on.jsonl"
    rep_on = env["dir"] / "rep_on.md"
    rep_off = env["dir"] / "rep_off.md"
    rc_off, out_off, err_off = _judge_run(env, env["prompts"],
                                          rows=env["dir"] / "rows_off.jsonl", out=rep_off)
    assert rc_off == 0, "judge 档（不给私有开关）没跑通：%s" % err_off[-700:]
    rc_on, out_on, err_on = _judge_run(env, env["prompts"], ev_path=ev_on, rows=rows_on, out=rep_on)
    assert rc_on == 0, "judge 档没跑通（rc=%s）：%s" % (rc_on, (err_on or out_on)[-900:])

    recs = [json.loads(l) for l in ev_on.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 2
    ex_recs = [json.loads(l) for l in env["ev_export"].read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(ex_recs) == 2 and all(r["actions_full"] == ev.UNKNOWN for r in ex_recs), \
        "导出腿没有动作可留，必须写 unknown 而不是空表伪装「留住了零个」"

    case_fp = ev.case_source_fp(_chain_case())
    cur_ruler = ev.evaluator_source_fp()
    for rec, prow in zip(recs, env["prows"]):
        assert rec["stage"] == "judge"
        # ① 超过 70 字的原文真在链上留住了（报表口径照旧截断，见下面的 rows 断言）
        assert _LONG_MEMO in rec["payloads_full"] and len(_LONG_MEMO) == 80 > 70
        # ② 动作原文三件套：类型＋完整载荷（date 这位 `_payload_text` 根本不给）＋原始标记串
        by_t = {a["action"]: a for a in rec["actions_full"]}
        assert by_t["CAL_NOTE"]["payload"]["date"] == _DATE_ONLY
        assert "◆日程尾部◆" in by_t["CAL_NOTE"]["payload"]["text"]
        assert "date" not in " ".join(rec["payloads_full"]), "date 混进兼容文本字段＝旧读法变了"
        assert by_t["NOTE_DONE"]["payload"]["type"] == "calendar"
        assert by_t["CAL_NOTE"]["raw"].startswith("[CAL_NOTE]")
        # ③ 回复全文越过公共 900 字窗那一段也在
        assert rec["reply_text_full"] == _EXT_REPLY
        assert _TAIL_BEYOND_900 in rec["reply_text_full"] and \
            any(_TAIL_BEYOND_900 in p for p in rec["payloads_full"])
        assert rec["truncated"] is False
        # ④ 来源沿途：题面来源＝真题面算出来的那一维；尺子两维分维且跨进程同源
        assert rec["case_source_fp"] == case_fp
        assert rec["export_evaluator_source_fp"] == cur_ruler == rec["evaluator_source_fp"]
        assert rec["input_fp_verified"] is True and rec["prompt_fp_stable"] == prow["prompt_fp_stable"]
    assert recs[0]["case_source_fp"] == recs[1]["case_source_fp"], "同一道题的题面来源在两跑之间漂了"
    for rec, prow in zip(recs, env["prows"]):
        assert rec["prompt_fp_stable"] == ev.stable_fp(prow["ctx_messages"]), \
            "stable 那一维跨进程对不上 ⇒ 留底里的「同源」判定不是现算的那把尺"

    # ⑤ `--emit-rows` 同时开：公共面一个字节都不因私有开关而变宽
    rows_off = (env["dir"] / "rows_off.jsonl").read_text(encoding="utf-8")
    assert rows_on.read_text(encoding="utf-8") == rows_off, \
        "留底开关改变了 --emit-rows 的公共行 ⇒ 私有面溢出去了"
    assert out_on == out_off, "留底开关改变了 stdout"
    assert rep_on.read_text(encoding="utf-8") == rep_off.read_text(encoding="utf-8"), \
        "留底开关改变了 --out 报表"
    public = rows_off + out_off
    for key in ("payloads_full", "actions_full", "ctx_messages", "export_evaluator_source_fp"):
        assert key not in public, "私有字段名溢到公共面：%s" % key
    assert _TAIL_BEYOND_900 not in public, "900 字窗外的原文出现在公共 rows/stdout ⇒ 泄漏"
    jrows = [json.loads(l) for l in rows_off.splitlines() if l.strip()]
    for r in jrows:
        assert _LONG_MEMO[:70] in r["payloads"], "报表侧的 70 字口径被动过"
        assert _LONG_MEMO not in r["payloads"], "公共 rows 里的载荷没截断 ⇒ 公共口径变宽了"
        assert r["reply_text"] == _EXT_REPLY.strip()[:900], "公共 900 字窗口径被动过"


def test_跨进程反例_篡改导出行的尺子来源_judge两维必须不等不抹平(chain_export):
    env = chain_export
    _write_replies(env, "[MEMO]记一下[/MEMO]")
    fake = "cafebabe0000"
    tampered = env["dir"] / "prompts_tampered.jsonl"
    lines = []
    for r in env["prows"]:
        lines.append(json.dumps(dict(r, evaluator_source_fp=fake), ensure_ascii=False))
    tampered.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ev_bad = env["ev_dir"] / "ev_tampered.jsonl"
    rc, out, err = _judge_run(env, tampered, ev_path=ev_bad)
    assert rc == 0, "judge 档拒绝合法导出行？%s" % err[-700:]
    recs = [json.loads(l) for l in ev_bad.read_text(encoding="utf-8").splitlines() if l.strip()]
    cur = ev.evaluator_source_fp()
    for rec in recs:
        assert rec["export_evaluator_source_fp"] == fake
        assert rec["evaluator_source_fp"] == cur
        assert rec["export_evaluator_source_fp"] != rec["evaluator_source_fp"], \
            "当前尺子顶替了导出尺子 ⇒ 不同源被假称同源（主审点名的正是这一处）"
        assert rec["input_fp_verified"] is True, "来源维的修法不该动输入维"


# ─────────────── 变异电池 M3/M4：精确语义变异 ⇒ 真 pytest 红 ⇒ 撤掉变异立刻恢复 ───────────────

_MUT_PLUGIN = '''
"""注入变异的 pytest 插件：只在那一发子进程里生效，工作区一个字节都不动。"""
import os
from importlib.machinery import SourceFileLoader

_ORIG = SourceFileLoader.exec_module


def _patched(self, module, *a, **k):
    _ORIG(self, module, *a, **k)
    mode = os.environ.get("A30_MUTATION", "")
    # 只认被测评测器那一个模块（`exec_module` 是全局通道，每个 import 都会过这里）
    if not mode or not hasattr(module, "_actions_full_of") or not hasattr(module, "_inherited_source_fp"):
        return module
    if mode == "drop_raw":                          # M3：动作原文丢掉 raw 那一件
        def mut(actions, _u=module.UNKNOWN):
            if actions is None:
                return _u
            return [{"action": getattr(x, "action_type", _u),
                     "payload": getattr(x, "payload", None)} for x in actions]
        module._actions_full_of = mut
    elif mode == "fake_same_source":                # M4：缺失时拿当前尺子顶上去
        def mut2(key, *srcs, _cur=module.evaluator_source_fp, _u=module.UNKNOWN):
            for s in srcs:
                if isinstance(s, dict) and s.get(key) is not None:
                    return str(s[key])
            return _cur() if key == "evaluator_source_fp" else _u
        module._inherited_source_fp = mut2
    return module


SourceFileLoader.exec_module = _patched
'''

_MUT_TARGETS = {
    "drop_raw": "tests/test_a30_private_evidence.py::test_actions_full_保住动作类型与完整载荷_date_未知键与JSON嵌套一位不丢",
    "fake_same_source": "tests/test_a30_private_evidence.py::test_judge留底分维保留导出腿尺子来源_缺失写unknown不拿当前值顶替",
}


def test_变异电池M3M4_精确语义变异让真pytest变红_撤掉变异立刻恢复(outside_repo):
    """变异**不落到工作区**：写一个临时插件，只在那一发子进程里改函数，跑完即弃。

    不能只手工 assert「我认为这条守卫会红」——这里要的是真 `pytest` 退出码非 0，
    且红的那条正是本维守卫；随后同一批 node id 不带插件必须回到绿。
    """
    plugin_dir = outside_repo / "mut"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "a30_mut_plugin.py").write_text(_MUT_PLUGIN, encoding="utf-8")
    script_sha = hashlib.sha1(SCRIPT.read_bytes()).hexdigest()
    bt = outside_repo / "basetemp"
    bt.mkdir(parents=True, exist_ok=True)          # 子进程的 --basetemp 父目录得先有，否则 ERROR 在夹具
    node_ids = list(_MUT_TARGETS.values())

    for mode, node_id in _MUT_TARGETS.items():
        env = dict(_base_child_env(outside_repo), PYTHONPATH=str(plugin_dir), A30_MUTATION=mode)
        cmd = [_cli_py(), "-m", "pytest", "-q", "-p", "a30_mut_plugin", "-p", "no:cacheprovider",
               "--basetemp", str(bt / mode), node_id]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(REPO / "backend"), timeout=150)
        blob = (p.stdout or "") + (p.stderr or "")
        assert p.returncode != 0, "变异 %s 没让真 pytest 变红 ⇒ 那条守卫是空跑：%s" % (mode, blob[-400:])
        assert "1 failed" in blob and "1 passed" not in blob, \
            "变异 %s 红得不对题：%s" % (mode, blob[-500:])

    ctrl = dict(_base_child_env(outside_repo), PYTHONPATH=str(plugin_dir), A30_MUTATION="none")
    p = subprocess.run([_cli_py(), "-m", "pytest", "-q", "-p", "a30_mut_plugin", "-p", "no:cacheprovider",
                        "--basetemp", str(bt / "ctrl"), *node_ids],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ctrl,
                       cwd=str(REPO / "backend"), timeout=150)
    assert p.returncode == 0, "撤掉变异后没恢复绿：%s" % (p.stdout or "")[-600:]
    assert hashlib.sha1(SCRIPT.read_bytes()).hexdigest() == script_sha, "变异电池动了工作区"


# ══════════════ 主审返工②（2026-10-14 隔离事故）：CLI 入口不得提前绑外部 engine ══════════════
#
# 事故链条（比「指纹漂移」更重的根因）：`main()` 里 **lint 排在 `_init_temp_env` 之前**，而
# `lint_dataset` → `_periodic_words` → `_load_periodic_words` 走的是
# `from app.memory.fallback import PERIODIC_WORDS`；`app.memory` 的包 `__init__` 一路拽起
# service→`app.db.engine`，engine 在 **import 那一刻**就按当时的 `settings.database_url` 绑死且按进程
# 缓存。之后 `_init_temp_env` 改写 `DATABASE_URL` 也救不回来了。一次没设 `DATABASE_URL` 的 export
# 探针因此把评测号段（9004/9008）写进了**生产库**。
#
# 这一节只做两件事：① 周期词表改为**按源码 AST 取字面量**，一个 `app.*` 都不 import（唯一真源不变、
# 词表一个词都不复制）；② 立一条**真子进程**隔离守卫——外部哨兵库（marker 表＋一行）在整条 CLI
# 路上必须逐 schema／行／字节都不变，并把每一次 `create_async_engine` 的 URL 记进日志以供反证。
# 本文件所有新增跨进程夹具（含 `chain_export`）一律走 `_base_child_env`，环境由 pytest 沙箱给。
import base64  # noqa: E402
import sqlite3  # noqa: E402

_ISO_MUTS = {
    # M5：把周期词表读法退回「import app.memory.fallback」⇒ 包 __init__ 提前绑外部 engine。
    # 锚点＝函数体第一句（唯一），变异体在原句前插入早退的旧实现。
    "periodic_pkg_import": (
        "    try:\n"
        '        tree = ast.parse(_FALLBACK_FILE.read_text(encoding="utf-8-sig"))\n',
        "    from app.memory.fallback import PERIODIC_WORDS\n"
        "    return tuple(PERIODIC_WORDS)\n"
        "    try:\n"
        '        tree = ast.parse(_FALLBACK_FILE.read_text(encoding="utf-8-sig"))\n'),
    # M6：默认私有收集再次开启（没给开关也把收集器接进三条腿）⇒ 无落点时仍把全文抄进内存。
    "always_collect": (
        "    ev_sink = ev_rows if ev_path else None\n",
        "    ev_sink = ev_rows\n"),
}
_MUTS_B64 = base64.b64encode(json.dumps(_ISO_MUTS, ensure_ascii=False).encode("utf-8")).decode("ascii")

# 驱动：在孙进程里跑 CLI 真入口，边跑边记录 engine 绑定 URL；变异**只存在于这段内存里的源码文本**。
_DRIVER_SRC = '''# -*- coding: utf-8 -*-
"""隔离守卫驱动：argv = [driver, script, *cli_args]；不写工作区、不 import 生产装配。"""
import asyncio
import base64
import io
import json
import os
import sys
from importlib.machinery import SourceFileLoader

SCRIPT = sys.argv[1]
sys.argv = [SCRIPT] + sys.argv[2:]
JOURNAL = os.environ.get("A30_DB_JOURNAL", "")
SENTINEL = os.path.normcase(os.environ.get("A30_SENTINEL", ""))
REFUSE = os.environ.get("A30_DB_REFUSE_SENTINEL", "") == "1"
MUT = os.environ.get("A30_ISO_MUT", "")
MUTS = json.loads(base64.b64decode(os.environ.get("A30_ISO_MUTS", "")).decode("utf-8")) if MUT else {}


def _journal(url):
    if not JOURNAL:
        return
    try:
        with io.open(JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps({"url": str(url), "env_url": os.environ.get("DATABASE_URL", "")}) + "\\n")
    except OSError:
        pass


_ORIG = SourceFileLoader.exec_module


def _exec(self, module, *a, **k):
    _ORIG(self, module, *a, **k)
    # app/db/engine.py 是 `from sqlalchemy.ext.asyncio import create_async_engine`，
    # 所以必须在**那个模块执行完、app.db.engine 还没开始 import** 时把属性换掉才拦得住。
    if getattr(module, "__name__", "") == "sqlalchemy.ext.asyncio" and hasattr(module, "create_async_engine"):
        orig = module.create_async_engine

        def wrapped(*args, **kw):
            url = kw.get("url") or (args[0] if args else "")
            _journal(url)
            if REFUSE and SENTINEL and SENTINEL in os.path.normcase(str(url)):
                raise RuntimeError("隔离守卫：拒绝把 engine 绑到外部哨兵库（＝_init_temp_env 之前就实例化了）")
            return orig(*args, **kw)
        module.create_async_engine = wrapped
    return module


SourceFileLoader.exec_module = _exec
src = io.open(SCRIPT, encoding="utf-8").read()
if MUT:
    if MUT not in MUTS:
        raise SystemExit("未知变异 " + MUT)
    old, new = MUTS[MUT]
    if old not in src:
        raise SystemExit("变异锚点没命中：" + MUT)
    src = src.replace(old, new, 1)
ns = {"__name__": "a30_cli_body", "__file__": SCRIPT}
exec(compile(src, SCRIPT, "exec"), ns)
raise SystemExit(asyncio.run(ns["main"]()))
'''

_ISO_PLUGIN = '''"""隔离变异插件：在 pytest 子进程里把 CLI 源码按锚点重跑一遍（工作区一字节不动）。"""
import base64
import io
import json
import os
from importlib.machinery import SourceFileLoader

_ORIG = SourceFileLoader.exec_module


def _patched(self, module, *a, **k):
    _ORIG(self, module, *a, **k)
    mode = os.environ.get("A30_ISO_MUT", "")
    if not mode or not hasattr(module, "_periodic_words"):
        return module
    muts = json.loads(base64.b64decode(os.environ["A30_ISO_MUTS"]).decode("utf-8"))
    old, new = muts[mode]
    path = getattr(module, "__file__", "") or ""
    src = io.open(path, encoding="utf-8").read()
    if old not in src:
        raise RuntimeError("变异锚点没命中：" + mode)
    exec(compile(src.replace(old, new, 1), path, "exec"), module.__dict__)
    return module


SourceFileLoader.exec_module = _patched
'''


def _write_sentinel(path, marker="SENTINEL-DO-NOT-TOUCH"):
    """外部哨兵库：只有一张 marker 表＋一行。它代表「评测器**不该**碰的那一份库」。"""
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE marker (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO marker VALUES ('locked', ?)", (marker,))
    con.commit()
    con.close()


def _sentinel_snapshot(path):
    """schema／行内容／字节哈希／附属文件，四样一起看——只看 flags 或只看行数都算空跑。"""
    con = sqlite3.connect(str(path))
    try:
        schema = sorted(con.execute("SELECT type, name, COALESCE(sql, '') FROM sqlite_master").fetchall())
        rows = sorted(con.execute("SELECT k, v FROM marker").fetchall())
    finally:
        con.close()
    p = Path(path)
    return {"schema": schema, "rows": rows,
            "bytes": hashlib.sha1(p.read_bytes()).hexdigest(),
            "strays": sorted(x.name for x in p.parent.glob(p.name + "*") if x.name != p.name)}


def _journal_urls(path):
    if not Path(path).exists():
        return []
    return [json.loads(l)["url"] for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _iso_setup(tmp_path, mode=None):
    """tmp_path（pytest 沙箱）里备齐：哨兵库＋驱动＋环境。返回 (root, env, sentinel, before)。"""
    root = Path(tmp_path)
    driver = root / "a30_iso_driver.py"
    driver.write_text(_DRIVER_SRC, encoding="utf-8")
    _write_sentinel(root / "sentinel.db")
    journal = root / "engine_journal.jsonl"
    env = _base_child_env(root, journal=journal, mutate=mode)
    env["A30_DRIVER"] = str(driver)
    return root, env, root / "sentinel.db", _sentinel_snapshot(root / "sentinel.db")


def test_隔离守卫_export两跑子进程绝不写外部哨兵库_拒绑模式当场反证无提前实例化(tmp_path):
    """`main export --repeat 2` 真跑一遍：**外部哨兵库的 schema／marker／字节一位都不许变**。

    两道相互独立的探针：
    ① 哨兵差分（写没写）——事故的实际后果就是这一维破了；
    ② 拒绑模式（`A30_DB_REFUSE_SENTINEL=1`：engine 一旦拿到哨兵 URL 就当场抛）——把「提前实例化」
      这件事本身变成可见的非零退出，而不是等它后来写坏东西。
    最后用绑定日志证明：整条路上确实建过 engine，且建在 pytest 沙箱之内、不是继承来的哨兵上。
    """
    root, env, sentinel, before = _iso_setup(tmp_path)
    ds = root / "cases.jsonl"
    ds.write_text(json.dumps(_chain_case(), ensure_ascii=False) + "\n", encoding="utf-8")
    prompts = root / "prompts.jsonl"
    common = ["--dataset", ds, "--judges", "J1", "--mode", "export", "--repeat", "2",
              "--fillers", "0", "--limit", "1", "--emit-prompts", prompts,
              "--out", root / "rep_export.md"]

    rc, out, err = _run_cli(*common, env=env, driver=True)
    assert rc == 0, "export 子进程没跑通（rc=%s）：%s" % (rc, (err or out)[-900:])
    assert before == _sentinel_snapshot(sentinel), \
        "隔离破了：export 子进程动了外部哨兵库（schema/marker/字节任一变了都算）"
    urls = _journal_urls(env["A30_DB_JOURNAL"])
    assert urls, "整条路一次都没建 engine ⇒ 这条守卫空跑（夹具坏了）"
    for u in urls:
        assert "sentinel.db" not in os.path.normcase(u), "engine 绑到了继承来的哨兵库：%s" % u
        assert os.path.normcase(str(root)) in os.path.normcase(u.replace("/", os.sep)), \
            "engine 绑到 pytest 沙箱之外：%s" % u

    # ② 拒绑模式：把「碰哨兵」直接判死，跑通才等于「一次都没提前实例化」
    env2 = dict(env, A30_DB_REFUSE_SENTINEL="1", A30_DB_JOURNAL=str(root / "journal_refuse.jsonl"))
    rc2, out2, err2 = _run_cli(*(common[:-2] + ["--emit-prompts", root / "prompts2.jsonl"]),
                               env=env2, driver=True)
    assert rc2 == 0, "拒绑模式下 CLI 非零退出 ⇒ 有代码路径在 _init_temp_env 之前绑了哨兵：%s" % (
        (err2 or out2)[-900:])
    assert before == _sentinel_snapshot(sentinel), "拒绑模式跑完哨兵仍被改了"


def test_隔离守卫_lint取周期词不import_app包_提前退出也不碰哨兵(tmp_path):
    """lint 是整条路上**最早**的一步，它读词表必须不产生 `app.*` import。

    用一道「周期词＋单日日期」的必然违规题演：子进程在 `run_export_eval` 之前就 return 3——
    ① 报错里带着「每逢／每周」这类**从真源读到的**词 ⇒ 证明 AST 取到的词表真的在闸上生效；
    ② 绑定日志为空＋哨兵未变 ⇒ 证明这一步一个 `app.*` 都没 import、engine 一次都没实例化。
    """
    root, env, sentinel, before = _iso_setup(tmp_path)
    periodic = dict(_chain_case(), cid="a30iso02",
                    turn="每逢周五晚上都要去接孩子",
                    expect={"gold_seed_idx": [0], "abstain": False,
                            "action_type": ["CAL_NOTE"], "field_match": {"*": "接孩子"},
                            "forbidden": []},
                    time_anchor={"as_of": "2026-09-25", "tz": "Asia/Shanghai",
                                 "expect_date": "2026-10-02", "expect_offset_days": None})
    ds = root / "periodic.jsonl"
    ds.write_text(json.dumps(periodic, ensure_ascii=False) + "\n", encoding="utf-8")
    rc, out, err = _run_cli("--dataset", ds, "--judges", "J1", "--mode", "export", "--repeat", "1",
                            "--fillers", "0", "--out", root / "rep.md", env=env, driver=True)
    assert rc == 3, "违规题没被 lint 拦住（rc=%s）：%s" % (rc, (err or out)[-600:])
    assert "周期词" in err and "每逢" in err, "闸上的词表不是从真源读到的那一份：%s" % err[-600:]
    assert _journal_urls(env["A30_DB_JOURNAL"]) == [], "lint 期实例化了 engine ⇒ 唯一真源的读法又变回 import 了"
    assert before == _sentinel_snapshot(sentinel), "在建库之前就动了哨兵库"


def test_三条腿默认不把收集器接进腿上_显式开关才接(tmp_path, outside_repo, monkeypatch, capsys):
    """**执行 main**（不是读源码）证明：不给开关 ⇒ `evidence=None`；给了 ⇒ 真采集。

    judge 腿走真链路（不碰库、不碰模型），并把 `make_private_evidence` 换成「一调就抛」的桩——
    默认档整条 main 跑完 rc==0，等于一次都没被触发；开启档必须当场炸。
    export／generate 腿把跑数据的外层桩掉（真跑会建临时库并装配，本单不许碰库），
    断言的是 main 递给它们的那个 `evidence` 形参：默认 None／开启是同一个列表对象。
    """
    boom = RuntimeError("默认档不许采集留底（这条桩被调用＝隐私面变宽）")
    monkeypatch.setattr(ev, "make_private_evidence", lambda *a, **k: (_ for _ in ()).throw(boom))
    seen = {}

    def _stub_leg(tag, ret):
        async def _f(*a, **kw):
            seen[tag] = kw.get("evidence", "!!没这个形参!!")
            return ret
        return _f

    monkeypatch.setattr(ev, "run_export_eval", _stub_leg("export", {"rows": [], "n": 0}))
    monkeypatch.setattr(ev, "run_generate_eval", _stub_leg("generate", {"rows": [], "n": 0}))
    monkeypatch.setattr(ev, "render_generate", lambda rep: "STUB-REPORT")

    ds = tmp_path / "cases.jsonl"
    ds.write_text(json.dumps(_chain_case(), ensure_ascii=False) + "\n", encoding="utf-8")
    pf, rf = tmp_path / "prompts.jsonl", tmp_path / "replies.jsonl"
    prow = _prow(case_source_fp="c" * 12)
    reply = "[MEMO]记一下小花生日[/MEMO]"
    pf.write_text(json.dumps(prow, ensure_ascii=False) + "\n", encoding="utf-8")
    rf.write_text(json.dumps({"cid": prow["cid"], "run": prow["run"], "text": reply,
                              "sent_fp": ev.sent_fp_of(prow["ctx_messages"])},
                             ensure_ascii=False) + "\n", encoding="utf-8")
    legs = {
        "judge": ["x", "--mode", "judge", "--judges", "J1", "--prompts", str(pf), "--replies", str(rf)],
        "export": ["x", "--mode", "export", "--dataset", str(ds), "--judges", "J1", "--repeat", "1"],
        "generate": ["x", "--mode", "generate", "--dataset", str(ds), "--judges", "J1",
                     "--allow-llm", "--repeat", "1"],
    }
    for tag, argv in legs.items():
        monkeypatch.setattr("sys.argv", argv + ["--out", str(tmp_path / ("out_%s.md" % tag))])
        assert asyncio.run(ev.main()) == 0, "%s 腿默认档没跑通" % tag
        assert seen.get(tag, None) is None, "%s 腿默认档仍被接了收集器：%r" % (tag, seen.get(tag))

    evf = outside_repo / "ev.jsonl"
    for tag in ("export", "generate"):
        argv = legs[tag] + ["--out", str(tmp_path / ("on_%s.md" % tag)),
                            "--emit-private-evidence", str(evf)]
        monkeypatch.setattr("sys.argv", argv)
        assert asyncio.run(ev.main()) == 0, "%s 腿开启档没跑通" % tag
        assert isinstance(seen.get(tag), list), "%s 腿开启档没把收集器接上去：%r" % (tag, seen.get(tag))
    monkeypatch.setattr("sys.argv", legs["judge"] + ["--out", str(tmp_path / "on_judge.md"),
                                                     "--emit-private-evidence", str(evf)])
    with pytest.raises(RuntimeError):
        asyncio.run(ev.main())
    capsys.readouterr()


_ISO_MUT_TARGETS = {
    "periodic_pkg_import": "tests/test_a30_private_evidence.py::"
                           "test_隔离守卫_export两跑子进程绝不写外部哨兵库_拒绑模式当场反证无提前实例化",
    "always_collect": "tests/test_a30_private_evidence.py::"
                      "test_三条腿默认不把收集器接进腿上_显式开关才接",
}


def test_隔离变异电池M5M6_语义变异让真pytest变红_撤掉变异立刻恢复绿(tmp_path):
    """两块变异都不落到工作区：插件／驱动只在子进程内存里改源码文本，跑完即弃。

    M5 恢复「`from app.memory.fallback import`」＝包 __init__ 提前绑外部 engine ⇒ 哨兵被写坏；
    M6 恢复「默认也把收集器接进三条腿」⇒ main 递给腿上的 `evidence` 不再是 None。
    两发都必须是**真 pytest 退出码非 0 且红的正是本维守卫**，撤掉变异后同一批 node id 回到绿。
    """
    plugin_dir = tmp_path / "iso_mut"
    plugin_dir.mkdir()
    (plugin_dir / "a30_iso_mut_plugin.py").write_text(_ISO_PLUGIN, encoding="utf-8")
    script_sha = hashlib.sha1(SCRIPT.read_bytes()).hexdigest()
    test_sha = hashlib.sha1(Path(__file__).resolve().read_bytes()).hexdigest()
    bt_root = tmp_path / "basetemp"
    bt_root.mkdir()

    for mode, node_id in _ISO_MUT_TARGETS.items():
        env = _pytest_child_env(plugin_dir, A30_ISO_MUT=mode, A30_ISO_MUTS=_MUTS_B64)
        p = subprocess.run([_cli_py(), "-m", "pytest", "-q", "-p", "a30_iso_mut_plugin",
                            "-p", "no:cacheprovider", "--basetemp", str(bt_root / mode), node_id],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, cwd=str(REPO / "backend"), timeout=400)
        blob = (p.stdout or "") + (p.stderr or "")
        assert p.returncode != 0, "变异 %s 没让真 pytest 变红 ⇒ 那条守卫是空跑：%s" % (mode, blob[-500:])
        assert "1 failed" in blob, "变异 %s 红得不对题：%s" % (mode, blob[-700:])

    ctrl = _pytest_child_env(plugin_dir, A30_ISO_MUTS=_MUTS_B64)
    p = subprocess.run([_cli_py(), "-m", "pytest", "-q", "-p", "a30_iso_mut_plugin",
                        "-p", "no:cacheprovider", "--basetemp", str(bt_root / "ctrl"),
                        *_ISO_MUT_TARGETS.values()],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=ctrl, cwd=str(REPO / "backend"), timeout=400)
    assert p.returncode == 0, "撤掉变异后没恢复绿：%s" % ((p.stdout or "") + (p.stderr or ""))[-800:]
    assert "2 passed" in p.stdout, "恢复的控制组没跑到两发：%s" % (p.stdout or "")[-500:]
    assert hashlib.sha1(SCRIPT.read_bytes()).hexdigest() == script_sha, "变异电池改了被测脚本"
    assert hashlib.sha1(Path(__file__).read_bytes()).hexdigest() == test_sha, "变异电池改了本测试文件"
