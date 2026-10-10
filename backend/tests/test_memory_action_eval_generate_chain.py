# -*- coding: utf-8 -*-
"""批 0-4 · A30 S1：生成侧接线的守卫（**零 LLM、零计费**）。

生成侧这一层和判分器不一样：它碰的是**进程与环境**（临时库、配置表复制、生产节点装配），
所以这里测的不是"分数对不对"，而是四件事——
① 三道授权闸逐条咬（少一道就是"静默走错尺子"或"静默花钱"）；
② LLM 配置表从生产库**只读**复制进临时库：源必须 ro、清单必须全、缺表必须抛而不是少复制；
③ 装配只能来自生产节点（评测里绝不另拼 prompt）；
④ 报表里的身份描述不得出现 api_key，且必须自带"真打了几次"的那一行。

为什么不在 pytest 里跑整链路（`gen_state` ＋桩 LLM）：`app.db.database` 的引擎在进程内是缓存的，
`_init_temp_env` 一改写 `DATABASE_URL`／`settings.database_url`，同一 pytest 会话的后续用例就连到
一个已被删掉的库（实测第二轮直接报"临时库没有表 api_configs"），xdist 并行下更是互相踩。
整链路自证走独立进程脚本 `output/a30_gen_chain_stub.py`（把统一 LLM 入口整个换成桩，三档：
好回复／空回复／错日期，后两档是反证）。
"""
import asyncio
import importlib.util
import inspect
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "diagnostics" / "memory_action_eval.py"
NODES = REPO / "backend" / "app" / "agent" / "nodes.py"
LLM_CLIENT = REPO / "backend" / "app" / "agent" / "llm_client.py"
LLM_SERVICE = REPO / "backend" / "app" / "application" / "llm_config_service.py"


def _load_eval():
    spec = importlib.util.spec_from_file_location("_memact_eval_gen_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load_eval()

_CFG_TABLES = ("api_configs", "user_llm_configs", "task_llm_configs")


def _mk_cfg_db(path: Path, *, drop=()) -> Path:
    """只建解析链需要的三张配置表（不整库克隆：这里要验的是**表在不在**，不是数据全不全）。"""
    from app.models.agent import TaskLlmConfig
    from app.models.base import Base
    from app.models.config import ApiConfig, UserLlmConfig

    tables = [t.__table__ for t in (ApiConfig, UserLlmConfig, TaskLlmConfig)
              if t.__tablename__ not in drop]
    eng = create_engine("sqlite:///" + str(path).replace(os.sep, "/"))
    Base.metadata.create_all(eng, tables=tables)
    eng.dispose()
    return path


def _put_server_cfg(path: Path, key: str = "SENTINEL-KEY-DO-NOT-LEAK") -> None:
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO api_configs (user_id,provider,base_url,api_key,model,enabled) "
                  "VALUES (0,'deepseek','https://x/v1',?, 'some-model',1)", (key,))
        c.execute("INSERT INTO task_llm_configs (user_id,task,provider,base_url,api_key,model,enabled) "
                  "VALUES (0,'chat','deepseek','https://x/v1','K2','m2',1)")
        c.commit()


# ─────────────────────────── ① 三道闸 ───────────────────────────
@pytest.mark.parametrize("judges,mode,allow,refuse", [
    (["J1"], "generate", False, True),         # 没授权就想跑生成
    (["J1"], "certify", True, True),           # 授权了但落在 J3 检索档＝拿错尺子
    (["J1"], "score", True, True),             # 同上
    (["J2"], "generate", True, True),          # 生成侧本轮只实现 J1
    (["J1", "J3"], "generate", True, True),    # 授权了也不许把别的判据混进计费轮
    (["J1"], "generate", True, False),         # 正当组合：必须放行
    (["J3"], "certify", False, False),         # J3 档本来就不花钱
    (["J3"], "certify", True, False),          # 给了授权也不该反过来拦 J3（拦了评测跑不起来）
], ids=["generate未授权", "J1掉进certify", "J1掉进score", "generate带J2",
         "generate混J3", "正当组合放行", "J3不需要授权", "授权不误伤J3"])
def test_三道闸逐条咬且正当组合放行(judges, mode, allow, refuse):
    got = ev.refusal(judges, mode, allow)
    assert bool(got) is refuse, "%s/%s/allow=%s 期望%s，实际 %r" % (
        judges, mode, allow, "拒绝" if refuse else "放行", got)


def test_每条拒绝都要指出下一步而不是只说不行():
    # 只回"不行"的报错会被绕过（补个 --allow-llm 就完事）；文案必须指出**正确的那条路**
    assert "--allow-llm" in ev.refusal(["J1"], "generate", False)
    assert "generate" in ev.refusal(["J1"], "certify", True)
    assert "J1" in ev.refusal(["J2"], "generate", True)


# ─────────────────────────── ② 配置表复制 ───────────────────────────
def test_复制把三张配置表都带上_行数与源一致(tmp_path):
    src = _mk_cfg_db(tmp_path / "prod.db")
    dst = _mk_cfg_db(tmp_path / "tmp.db")
    _put_server_cfg(src)
    out = ev.borrow_llm_config(str(src), str(dst))
    assert set(out) == set(ev.LLM_CONFIG_TABLES), "清单不齐：只复制了 %s" % sorted(out)
    assert out["api_configs"]["rows"] == 1 and out["task_llm_configs"]["rows"] == 1
    assert out["user_llm_configs"]["rows"] == 0, "空表也要在清单里出现（0 行是事实，不是漏抄）"
    with sqlite3.connect(dst) as c:
        got = c.execute("SELECT api_key,model FROM api_configs").fetchone()
    assert got == ("SENTINEL-KEY-DO-NOT-LEAK", "some-model"), \
        "复制必须整列原样，否则解析链读到的是残缺配置"


def test_复制不得动过生产库(tmp_path):
    """反证：源侧真被写成一次就不算"只读"。用文件字节做前后对比（比读代码里的连接串更硬）。"""
    src = _mk_cfg_db(tmp_path / "prod.db")
    dst = _mk_cfg_db(tmp_path / "tmp.db")
    _put_server_cfg(src)
    before = src.read_bytes()
    ev.borrow_llm_config(str(src), str(dst))
    assert src.read_bytes() == before, "生产库字节变了＝复制过程写了源库（本试点的硬约束）"


def test_源侧必须以mode_ro且uri_true打开(tmp_path, monkeypatch):
    """看它**实际传给 sqlite3.connect 的参数**——`mode=ro` 不配 `uri=True` 是无效的静默失败。"""
    src = _mk_cfg_db(tmp_path / "prod.db")
    dst = _mk_cfg_db(tmp_path / "tmp.db")
    seen = []
    real = sqlite3.connect

    def spy(path, *a, **kw):
        seen.append((str(path), kw.get("uri")))
        return real(path, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", spy)
    ev.borrow_llm_config(str(src), str(dst))
    s = [x for x in seen if "prod.db" in x[0]]
    d = [x for x in seen if "tmp.db" in x[0]]
    assert s and all("mode=ro" in p for p, _ in s), "源侧没走 mode=ro＝可能写生产库"
    assert all(u is True for _, u in s), "mode=ro 只有配 uri=True 才生效"
    assert d and all("mode=ro" not in p for p, _ in d), "目标侧还只读＝什么都没复制进去"


def test_源库缺表必须抛错而不是少复制一张(tmp_path):
    src = _mk_cfg_db(tmp_path / "prod.db", drop=("user_llm_configs",))
    dst = _mk_cfg_db(tmp_path / "tmp.db")
    with pytest.raises(RuntimeError, match="user_llm_configs"):
        ev.borrow_llm_config(str(src), str(dst))


def test_临时库缺表也要抛_不能默默用残缺配置去跑计费(tmp_path):
    src = _mk_cfg_db(tmp_path / "prod.db")
    dst = _mk_cfg_db(tmp_path / "tmp.db", drop=("api_configs",))
    with pytest.raises(RuntimeError, match="api_configs"):
        ev.borrow_llm_config(str(src), str(dst))


def test_身份描述不得出现api_key(tmp_path, monkeypatch):
    src = _mk_cfg_db(tmp_path / "prod.db")
    _put_server_cfg(src)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" + str(src).replace(os.sep, "/"))
    ident = ev.resolved_llm_identity(ev.GENERATE_USER_ID)
    assert ident["server"]["model"] == "some-model"
    assert ident["server"]["key_len"] == len("SENTINEL-KEY-DO-NOT-LEAK")
    blob = json.dumps(ident, ensure_ascii=False)
    assert "SENTINEL" not in blob and "api_key" not in blob, "身份描述把 key 带出去了"


# ─────────────────────────── ③ 装配＝生产节点 ───────────────────────────
def test_复制清单必须覆盖解析链真正读的配置表():
    """漂移守卫：清单是手抄的，抄漏一张＝评测用的模型和生产用的不是一个。

    做法＝扫两个源文件里所有 `select(<模型>)`，把能解析成 ORM 类的表名收进来，
    凡表名含 "config" 的都必须在清单里。反面情形（新增 `*_llm_configs` 却没进清单）当场红。
    """
    import app.models as models_ns

    txt = "".join(f.read_text(encoding="utf-8") for f in (LLM_CLIENT, LLM_SERVICE))
    assert txt, "两个源文件都读不到内容＝守卫空跑"
    names = set(re.findall(r"select\(\s*([A-Z][A-Za-z0-9_]*)", txt))
    assert names, "一个 select 都没扫到＝正则坏了（守卫没牙）"
    cfg_tables = set()
    for n in names:
        tn = getattr(getattr(models_ns, n, None), "__tablename__", "")
        if "config" in tn:
            cfg_tables.add(tn)
    assert cfg_tables, "扫到了 %d 个模型名却没有一张配置表＝解析链读法变了，守卫要看一眼" % len(names)
    missing = cfg_tables - set(ev.LLM_CONFIG_TABLES)
    assert not missing, "解析链在读 %s，而复制清单只有 %s" % (sorted(missing), sorted(ev.LLM_CONFIG_TABLES))


def test_装配函数只准调生产节点():
    """守卫要管的维度＝**这三个节点真的被 await 了**，而不是"名字出现在文件里"。

    第一版只查 `perceive`/`retrieve_memories`/`build_context` 三个词在不在源码里，
    于是变异电池当场把我抓出来：把 `await _perceive(state)` 换成 `pass` 之后，
    import 行里那个词还在 ⇒ 守卫照样绿（14 条变异里唯一没牙的一条）。
    现在改成：先从 import 行解析出**别名**，再逐个断言它被 `await 别名(` 调用。
    """
    src = inspect.getsource(ev.gen_state)
    pairs = re.findall(r"([A-Za-z_]\w*)\s+as\s+(_\w+)", src)      # (生产节点名, 本函数里的别名)
    nodes = {"perceive", "retrieve_memories", "build_context"}
    assert nodes <= {a for a, _ in pairs}, "装配没 import 全生产节点：%s" % (nodes - {a for a, _ in pairs})
    for name, alias in pairs:
        assert "await %s(state)" % alias in src, "%s 只 import 没被 await＝装配偷偷少了一段" % name
    assert "_build_initial_state(" in src, "没走唯一 State Constructor（A28-S4）"
    assert "app.agent.nodes" in src
    for bad in ("SYSTEM_PROMPT", "context_messages = [", "messages = ["):
        assert bad not in src, "生成侧自己拼了 prompt（%s）＝评测与生产两把尺子迟早漂" % bad


def test_评测角色要拨到生产实配的认知开关():
    """`retrieve_memories` 会用 `perceive` 的话题/情绪派生查询 ⇒ 认知开关关了等于**少一路召回**。

    10-05 只读数：生产 9 个角色 `cognitive_loop_enabled` 全＝1，所以评测角色也必须是 1。
    """
    src = inspect.getsource(ev.match_production_character)
    assert "cognitive_loop_enabled=True" in src
    assert "cognitive_loop_enabled=True" in inspect.getsource(ev.seed_library) or \
        "match_production_character" in inspect.getsource(ev.seed_library), \
        "灌库后没拨开关＝这轮量的不是生产那条链"


def test_判分前必须把题面锚点交给parse_actions():
    src = inspect.getsource(ev.generate_case)
    assert "base_date=anchor" in src, "没传锚点＝相对日期按本机时钟判，今天绿后天红"
    assert "resolve_expect_date" in src, "没换算 expect_date＝日期判据恒空"
    for bad in ("api_key=", "model=", "base_url="):
        assert bad not in src, "生成侧替解析链挑了 %s ⇒ 测的是我的假设而不是生产的配置" % bad


def test_评测的max_tokens与温度缺省必须跟着生产节点():
    """注释里写了"那边改了这边守卫会红"——这条就是让它真的会红。"""
    txt = NODES.read_text(encoding="utf-8")
    body = txt[txt.index("async def generate_response"):]
    mt = set(re.findall(r"max_tokens=(\d+)", body))
    assert mt, "nodes.py 里没扫到 max_tokens 字面量＝守卫空跑"
    assert str(ev.CHAT_MAX_TOKENS) in mt, "生产非流式分支的 max_tokens 变了（%s）而评测还写 %s" % (
        sorted(mt), ev.CHAT_MAX_TOKENS)
    assert 'state.get("temperature") or 0.8' in body, "生产的温度缺省写法变了"
    assert ev.EVAL_TEMPERATURE is None, (
        "评测默认应跟随生产（读 state['temperature']）；钉死温度只能靠 --temperature 显式给")


# ─────────────────────────── ④ 报表与路径 ───────────────────────────
def _fake_rep():
    return {"rows": [{"cid": "j1x", "category": "fact", "judge": "J1", "pass": True, "err": "",
                      "why": "", "actions_seen": ["MEMO"], "payloads": ["朵朵"], "mem_channel": [],
                      "exemptions": [],
                      "dates_seen": [], "n_ctx_msgs": 3, "ctx_chars": 4200, "n_recalled": 5,
                      "gold_in_recall": 1, "gold_in_ctx": 1, "temperature": 0.0,
                      "reasoning_level": 0, "library_rows": 44,
                      "reply_head": "好，记下了", "reply_text": "好，记下了。[MEMO]朵朵[/MEMO]"},
                     {"cid": "j1y", "category": "fact", "judge": "J1", "pass": False, "err": "E3",
                      "why": "未产出期望动作 MEMO", "actions_seen": [], "payloads": [],
                      "mem_channel": ["用户女儿叫朵朵"], "exemptions": ["城北老小区｜作废语境豁免：已搬离城北老小区"],
                      "dates_seen": [], "n_ctx_msgs": 3,
                      "ctx_chars": 4300, "n_recalled": 5, "gold_in_recall": 1, "gold_in_ctx": 1,
                      "temperature": 0.0, "reasoning_level": 0, "library_rows": 44,
                      "reply_head": "记着呢，朵朵嘛",
                      "reply_text": "记着呢，朵朵嘛【记忆：用户女儿叫朵朵】FULLTOKEN-EVIDENCE-ONLY"}],
            "n": 2, "pass": 1, "ar": 50.0, "by_err": {"": 1, "E3": 1}, "k": 5,
            "flags": {"memory_temporal_recall": True},
            "borrowed": {"api_configs": {"rows": 1, "skipped_cols": []}},
            "llm": {"server": {"provider": "deepseek", "base_url": "https://x/v1",
                               "model": "some-model", "enabled": 1, "key_len": 12}},
            "usage": {"requests_sent": 10, "rows": 9, "prompt_tokens": 3900,
                      "completion_tokens": 40, "models": ["some-model"]},
            "skipped_judges": ["J2"]}


def test_报表必须同时给出请求数与落库行数():
    """10-05 试点实测：发 10 个请求、`llm_usage` 只落 9 行（用量落库是 fire-and-forget）。

    把"行数"当成"真打了几次"报，就会把我方基础设施的时序缺陷读成被测对象的行为差异。
    """
    out = ev.render_generate(_fake_rep())
    assert "入口计数" in out and "fire-and-forget" in out, "报表只给一个数＝下一个人会把它当请求数"
    # 断言要咬在**结论**上而不是关键词上：第一版只查"本机时钟"在不在，
    # 结果变异把整行改成"（这条被删了）本机时钟说明"照样绿（电池当场把我抓出来）。
    assert "对齐到跑分当天" in out and "time_anchor" in out and "平移副本" in out, \
        "报表没写清「锚点进不了 prompt ⇒ 日期题要按跑分当天平移」这个后果"
    assert "记忆通道" in out, "逐题表没有『记忆通道』列＝E3 到底是不是没用记忆，下次照样对不上"
    assert "拍板" in out and "parse_response" in out, \
        "报表没写清 E3 的第二种读法（走了【记忆：】落库通道却被记成没用记忆）与这次改尺子的出处"
    # 这条只认**全文里独有的标记**：早先断言的是「用户女儿叫朵朵」，可那段文字在表格里也有，
    # 于是变异把全文留底换成 40 字起头时守卫照样绿（电池抓出来的第二个没牙）。
    assert "FULLTOKEN-EVIDENCE-ONLY" in out, "逐题回复全文没留底＝取证缺口（A27甲 同族）"
    assert "llm_usage" in out and "3900" in out and "some-model" in out, "计费与端点行没了"
    assert "j1x" in out and "朵朵" in out and "动作里的日期" in out, "逐题表缺列"
    assert "12" in out and "SENTINEL" not in out, "端点行要给 key_len（只给长度不给内容）"


def test_报表必须把作废语境豁免数出来():
    """豁免是「网开一面」，开了几处、给谁开的必须能被人数出来——否则下次没人知道尺子松过。"""
    out = ev.render_generate(_fake_rep())
    assert "本轮豁免 **1 处**" in out, "豁免处数没进报表"
    assert "作废语境豁免" in out and "已搬离城北老小区" in out, "豁免条目没进报表"
    assert "还像" in "".join(ev.FORBIDDEN_KEEP_WORDS) or "仍" in ev.FORBIDDEN_KEEP_WORDS, \
        "反豁免词表空了＝豁免会压过一切复发"


def test_锚点过期就不计费跑_也不混进分母():
    """10-06 00:33 实测踩到：平移副本过期一天 ⇒ j1t04 被判 E4，而模型按它看到的"今天=10-06"
    算出 10-09 **是对的**。日期题的锚点必须等于跑分当天，否则这半判据量的是尺子。"""
    today = ev.today_beijing()
    stale = {"time_anchor": {"as_of": "2026-09-25", "expect_date": "2026-09-30"}}
    fresh = {"time_anchor": {"as_of": today, "expect_date": today}}
    nodate = {"time_anchor": {"as_of": "1999-01-01"}}
    assert ev.anchor_is_stale(stale) is True, "锚点过期没认出来＝又要花一次钱买假读数"
    assert ev.anchor_is_stale(fresh) is False, "当天副本被判过期＝整轮会被自己跳过"
    assert ev.anchor_is_stale(nodate) is False, "没有日期判据的题不该管锚点"
    # 今天必须按**北京时区**算（本机时区不同也不能错）：prompt 里的「现在」就是北京时间。
    # 光比数值在本机（UTC+8）看不出差别，所以再加一条源码锚：必须显式带 +8 时区。
    from datetime import datetime, timedelta, timezone
    assert ev.today_beijing() == datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    assert "timedelta(hours=8)" in inspect.getsource(ev.today_beijing), \
        "today_beijing 没用北京时区＝跨午夜/跨时区时会把当天判成昨天，日期题整轮被误跳过"


def test_读数分母剔除没跑的题_且是纯函数():
    rows = [{"cid": "a", "pass": True, "err": ""}, {"cid": "b", "pass": False, "err": "E3"},
            {"cid": "c", "pass": False, "err": "E_anchor_stale"}]
    s = ev.generate_summary(rows)
    assert (s["n"], s["pass"], s["ar"]) == (2, 1, 50.0), s
    assert s["n_asked"] == 3 and s["n_skipped_stale"] == 1, s
    assert s["by_err"]["E_anchor_stale"] == 1, s
    # 反证：把 stale 混进分母会变成 1/3＝33.3%，"没跑"被读成"没过"
    assert s["ar"] != round(100.0 * 1 / 3, 1)


def test_报表要写明有几题因锚点过期没跑():
    rep = {"rows": [{"cid": "j1t9", "category": "temporal", "judge": "J1", "pass": False,
                     "err": "E_anchor_stale", "why": "as_of 过期", "actions_seen": [], "payloads": [],
                     "mem_channel": [], "exemptions": [], "dates_seen": [], "n_ctx_msgs": 0,
                     "ctx_chars": 0, "n_recalled": 0, "gold_in_recall": 0, "gold_in_ctx": 0,
                     "temperature": None, "reasoning_level": 0, "library_rows": 0,
                     "reply_head": "", "reply_text": ""}],
           "n_asked": 1}
    rep.update(ev.generate_summary(rep["rows"]))
    rep.update({"by_err": {"E_anchor_stale": 1}, "k": 5, "flags": {}, "borrowed": {},
                "llm": {"server": {}}, "usage": {}, "skipped_judges": []})
    out = ev.render_generate(rep)
    assert "锚点过期" in out and "也没计费" in out, out[:400]


def test_入口计数会委托给原函数并精确还原():
    import app.agent.llm_client as lc

    seen = []

    async def _fake(messages, **kw):
        seen.append(messages)
        return "回复"

    monkey = lc.chat_completion
    lc.chat_completion = _fake
    try:
        box = ev.install_llm_call_counter()
        assert lc.chat_completion is not _fake, "没套上计数层"
        got = [asyncio.run(lc.chat_completion([{"role": "user", "content": "x%d" % i}]))
               for i in range(3)]
        assert box["n"] == 3 and len(seen) == 3, "计数或委托断了（请求没转发给真入口）"
        assert got == ["回复"] * 3
        ev.uninstall_llm_call_counter(box)
        assert lc.chat_completion is _fake, "没还原＝把评测的桩留给了下一个调用者"
    finally:
        lc.chat_completion = monkey


@pytest.mark.parametrize("url,want_posix", [
    # 夹具用**合成路径**，不要写本项目的真机绝对路径：这个文件会随脱敏快照进公开仓，
    # 而公开仓树里"本机项目路径／个人目录"一类命中至今为 0（规程第 5 步要扫的就是这三条）。
    # 期望值一律写**正斜杠形态**，由 os.sep 换算 ⇒ 同一条用例在 Windows 与 Linux 档都该绿
    # （10-06 教训：把 `E:\...` 硬写进表里＝只在 Windows 成立，这文件第一次进 CI 就红了）。
    ("sqlite+aiosqlite:///C:/demo/backend/data/sqlite/demo.db",
     "C:/demo/backend/data/sqlite/demo.db"),
    ("sqlite+aiosqlite:////C:/demo/backend/data/sqlite/demo.db",
     "C:/demo/backend/data/sqlite/demo.db"),
    ("sqlite+aiosqlite:////demo/backend/data/sqlite/demo.db",
     "/demo/backend/data/sqlite/demo.db"),
    ("sqlite+aiosqlite:///data/sqlite/demo.db", "data/sqlite/demo.db"),
    ("sqlite:///x.db", "x.db"),
], ids=["Win绝对三斜杠", "Win绝对四斜杠", "POSIX绝对四斜杠", "相对路径", "旧式前缀"])
def test_sqlite路径解析认绝对也认相对(url, want_posix):
    assert ev._sqlite_file(url) == want_posix.replace("/", os.sep)


def test_生产库路径取不到就要抛而不是退回无key配置(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "_PROD_DB_CACHE", {})      # 缓存是进程级的，用例之间必须互不影响
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@h/db")
    with pytest.raises(RuntimeError, match="sqlite"):
        ev.prod_database_path()
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" +
                       str(tmp_path / "nope.db").replace(os.sep, "/"))
    with pytest.raises(RuntimeError, match="不存在"):
        ev.prod_database_path()


def test_拒绝路径不得写出报表(tmp_path, monkeypatch):
    """`main()` 在拒绝时 return 2 之前不能碰 --out：否则"跑了"会被误读成"跑了且没分"。"""
    out = tmp_path / "report.md"
    ds = tmp_path / "d.jsonl"
    ds.write_text(json.dumps({"cid": "x", "judge": "J1"}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["x", "--dataset", str(ds), "--judges", "J1",
                                      "--mode", "generate", "--out", str(out)])
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(ev.main()) == 2
    assert not out.exists(), "拒绝路径还是把报表文件写出来了"


# ─────────────── ⑤ §S3「生成三跑一致」＋ `--certify-j12`（10-06 加） ───────────────
@pytest.mark.parametrize("judges,mode,allow,cert,emit,fail,refuse", [
    (["J1"], "certify", False, True, "s.jsonl", None, False),   # 正当：四条认证本来就不花钱
    (["J1"], "generate", True, True, "s.jsonl", None, True),    # 同一个开关不许横跨两件事
    (["J1"], "score", True, True, "s.jsonl", None, True),
    (["J1"], "certify", True, True, "", None, True),            # 没落 solvability ⇒ 拦
    (["J1"], "certify", True, True, "s.jsonl", 80.0, True),     # 想拿它门禁 ⇒ 拦
    (["J1"], "certify", True, False, "s.jsonl", None, True),    # 不开新开关时老行为必须原样
    (["J3"], "certify", False, True, "", None, False),          # 纯检索档不受新前置影响
    (["J1"], "generate", True, False, "", None, False),         # 正当生成轮不能被新参数误伤
], ids=["认证放行", "generate下拒用", "score下拒用", "缺emit_solvability",
        "带fail_below", "不开关时行为不变", "J3不受牵连", "正当生成轮不误伤"])
def test_certify_j12的前置条件逐条咬且正当组合放行(judges, mode, allow, cert, emit, fail, refuse):
    """这个入口存在的理由＝把「认证（检索侧、零计费）」和「跑分（生成侧、计费）」拆开。
    拆开的代价是报表里会同时出现 AR 列和认证列 ⇒ 三个前置是防误读的，少一条就有人拿 AR 当分数。"""
    got = ev.refusal(judges, mode, allow, cert_j12=cert, emit_solvability=emit, fail_below=fail)
    assert bool(got) is refuse, "%s/%s/cert=%s/emit=%r/fail=%s 期望%s，实际 %r" % (
        judges, mode, cert, emit, fail, "拒绝" if refuse else "放行", got)


def test_certify_j12的拒绝必须指出缺的那个参数():
    assert "--emit-solvability" in ev.refusal(["J1"], "certify", False, True, "", None)
    assert "--fail-below" in ev.refusal(["J1"], "certify", False, True, "s.jsonl", 80.0)
    assert "certify" in ev.refusal(["J1"], "generate", True, True, "s.jsonl", None)


def _fake_cert_rep(**kw):
    d = {"n": 1, "rows_mean": 53.2, "n_certified": 1, "cert_rate": 100.0,
         "ar_gold_certified": 0.0, "ar_certified": 0.0, "ar": 0.0, "ar_strict": 0.0,
         "ar_gold": 0.0, "by_category": {},
         "rows": [{"cid": "j1t1", "category": "temporal", "judge": "J1", "pass": False,
                   "missing": [1], "polluted": [], "n_recalled": 0,
                   "solvability": {"no_mem_fail": True}}]}
    rep = {"k": 5, "semantic": False, "llm_guard": "chat_completion", "mode": "certify",
           "filler_target": 40, "skipped_judges": [], "configs": {"baseline": d},
           "cases_run": 1, "cases_total": 1}
    rep.update(kw)
    return rep


def test_certify_j12的报表必须声明AR列不是分数():
    out = ev.render(_fake_cert_rep(cert_j12=True), dataset="d", judges=["J1"],
                    configs=["baseline"], mode="certify")
    assert "--certify-j12" in out and "不是分数" in out, \
        "认证档报表没声明 AR 列不可当分数读 ⇒ 下一个人会把检索档的 0% 读成生成侧 0%"
    assert "solvability" in out, "没指出该看哪儿（落盘文件与生成档）"
    # 反向：普通 J3 档不许凭空带上这句（带上了就等于宣布自己的分也不是分）
    plain = ev.render(_fake_cert_rep(), dataset="d", judges=["J3"], configs=["baseline"],
                      mode="certify")
    assert "不是分数" not in plain, "非认证档也打这句＝这句不再有信号"


def test_三跑一致比的是结果指纹而不是回复文本():
    base = {"pass": True, "actions_seen": ["MEMO"], "dates_seen": [], "exemptions": [],
            "payloads": ["朵朵"]}
    a = dict(base, reply_text="好，记下了。[MEMO]朵朵[/MEMO]" * 3)
    b = dict(base, reply_text="行，我把这条存起来了【记忆：用户女儿叫朵朵】")
    assert ev._run_signature(a) == ev._run_signature(b), \
        "一致率把回复措辞算进去了 ⇒ 温度不为 0 时永远判「不一致」，这一列直接废掉"
    assert ev._run_signature(a) != ev._run_signature(dict(base, **{"pass": False})), "过没过没进指纹"
    assert ev._run_signature(a) != ev._run_signature(dict(base, payloads=["城北老小区"])), \
        "载荷变了却没算不一致"
    assert ev._run_signature(a) != ev._run_signature(dict(base, actions_seen=["MEMORY"])), \
        "换了通道却没算不一致（落库型题的两种产出是不同结果）"
    assert ev._run_signature(a) != ev._run_signature(dict(base, dates_seen=["2026-10-10"])), \
        "日期变了却没算不一致"
    # 拍板「B＋A 各半」之后 `【记忆：】`是与标记并列的产出，漂移必须看得见
    # （10-06 第一次三跑就栽在这：落库型题只有一跑真落了库，却被判"三跑一致"）
    assert ev._run_signature(a) != ev._run_signature(
        dict(base, mem_channel=["用户女儿叫朵朵"])), "记忆通道漂移没进指纹"


def test_读数AR读每一跑都过_单跑退回pass():
    rows = [{"pass": True, "err": ""}, {"pass": True, "pass_all": False, "pass_any": True,
                                         "err": ""}]
    s = ev.generate_summary(rows)
    assert (s["n"], s["pass"], s["ar"]) == (2, 1, 50.0), s
    # 反证：读 pass/pass_any 会得到 2/2＝100%——三跑里蒙对一次就算分，正是 §S3 要防的那件事
    assert s["pass"] != 2
    one = ev.generate_summary([{"pass": True, "err": ""}])   # repeat=1 没写 pass_all
    assert (one["n"], one["pass"]) == (1, 1), "单跑被 pass_all 缺省抹成 0 分"


def _fake_rep_multi():
    """多跑样本**用真代码 `fold_runs` 折**（不是手工塞字段）：手工塞出来的报表只能测渲染，
    测不到"一致率是怎么算出来的"——10-06 那两条没牙就是这么来的。"""
    rep = _fake_rep()
    r0, r1 = rep["rows"]
    a_runs = [dict(r0), dict(r0), dict(r0)]                       # 三跑同指纹且都过
    b_runs = [dict(r1, exemptions=[]),                      # 首跑没豁免
              dict(r1, exemptions=["城北老小区｜作废语境豁免：已搬离城北老小区"]),  # 第 2 跑漂出一条豁免
              dict(r1, exemptions=[])]
    rep["rows"] = [ev.fold_runs(a_runs), ev.fold_runs(b_runs)]
    rep.update(ev.generate_summary(rep["rows"]))
    return rep


def _parse_audit(out):
    """从报表里把「每题三条指纹」读回来——离线复算用的就是这份留底。"""
    cases, cur = {}, None
    for ln in out.splitlines():
        m = re.match(r"- \*\*(j1\w+)\*\*（.*?一致=(True|False)／每跑都过=(True|False)）", ln)
        if m:
            cur = {"declared": m.group(2) == "True", "pass_all": m.group(3) == "True", "sigs": []}
            cases[m.group(1)] = cur
            continue
        m = re.match(r"    - (?:首跑|第 \d 跑) 指纹 `(.+)`", ln)
        if m and cur is not None:
            cur["sigs"].append(m.group(1))
    return cases


def test_一致率必须能从报表离线复算():
    """§S3 加"三跑一致"这一列的理由就是**可复核**；只报一个分子分母不算可复核。

    10-06 第一次三跑的实际事故：我从报表离线复算得到 4/10，尺子自己报 3/10，
    差的那一题正是"逐跑豁免"没留底 ⇒ 这条测试就是把那个维度钉进留底。
    """
    out = ev.render_generate(_fake_rep_multi())
    cases = _parse_audit(out)
    assert set(cases) == {"j1x", "j1y"}, "报表没按题分块留底＝没法复算"
    for cid, c in cases.items():
        assert len(c["sigs"]) == 3, "%s 只留了 %d 条指纹" % (cid, len(c["sigs"]))
        assert c["declared"] == (len(set(c["sigs"])) == 1), \
            "%s 报表声明的一致=%s，与三条指纹是否全等不符＝这一列不可离线复算" % (cid, c["declared"])
    assert cases["j1x"]["declared"] is True and cases["j1y"]["declared"] is False, cases
    assert "无豁免" in cases["j1y"]["sigs"][0] and "作废语境豁免" in cases["j1y"]["sigs"][1], \
        "豁免没进指纹留底（就是那个 4/10 vs 3/10 的缺口）"
    # 每一维都要么有内容、要么显式写「无X」——缺席的那一维下次就是复算不出来的那一维。
    # 断言必须咬**内容**：变异电池把通道那段换成常量 `"无通道"` 时，只查段数／查"无通道"在不在
    # 都会照样绿（10-06 上午实测一条没牙就是这么漏的）。
    for s in cases["j1x"]["sigs"]:
        assert s.count("｜") == 5, "指纹维度不全（应 6 段）：%s" % s
        assert "MEMO" in s and "朵朵" in s and "无通道" in s, s
    for s in cases["j1y"]["sigs"]:
        assert s.count("｜") == 5, "指纹维度不全（应 6 段）：%s" % s
        assert "用户女儿叫朵朵" in s, "通道**内容**没进留底（写成常量占位也算'有这一维'）：%s" % s


def test_报表的多跑行只在真多跑时才出现且带着明细():
    out = ev.render_generate(_fake_rep_multi())
    assert "每题 3 跑" in out, "多跑了却没说跑几遍＝读数口径不明"
    assert "「结果完全一致」1／2" in out, "一致率没算进报表（或算错）"
    assert "「每一跑都过」1／2" in out and "AR 读后者" in out, \
        "没说清 AR 读哪一列 ⇒ 两列并存时下一个人随便挑一个"
    assert "第 2、3 跑分别产出了什么" in out, "一致率只有分子没有底 ⇒ 没法复核"
    assert out.count("起头：") == 4, \
        "第 2、3 跑的回复起头没进报表＝后两跑的 E3 还是只能判「没产出」（2 题 × 2 跑＝4 行）"
    assert out.count("指纹 `") == 6, "留底条数≠题数×3 跑（应 6 条）"
    assert "首跑 指纹" in out, "首跑没留底＝离线复算永远比尺子少一维（10-06 实测差 1 题）"
    assert "无动作" in out and "无通道" in out, "指纹里缺维度 ⇒ 复算不出来"
    assert "不一致的题：j1y" in out, "报表只给一致率的分母分子，不点名哪几题不稳＝下一个人无从下手"
    # 反向：三跑全一致时不许凭空点名"不一致的题"
    allc = _fake_rep_multi()
    for r in allc["rows"]:
        r["consistent"] = True
    assert "不一致的题" not in ev.render_generate(allc)
    # 最糟那档也必须点名：全不一致（分子＝0）时以前条件写成 `0 < n`，正好把这条漏掉
    # （10-06 桩实测抓出来的，不是设想出来的）
    none_ = _fake_rep_multi()
    for r in none_["rows"]:
        r["consistent"] = False
        r["pass_all"] = False
    assert "不一致的题：j1x、j1y" in ev.render_generate(none_), \
        "一题都不一致时报表反而不点名＝最该喊的那档没人喊"
    # 反向：单跑报表不许谎称多跑
    assert "每题 3 跑" not in ev.render_generate(_fake_rep())


def test_生产库路径在一个进程里必须稳定(tmp_path, monkeypatch):
    """10-06 用桩验 `--repeat` 时炸在这：`_init_temp_env` 改写 `DATABASE_URL` 后 `finally` 里 rmtree，
    第二轮再解析就指向一个已被删除的临时库 ⇒ "生产库文件不存在"。"""
    monkeypatch.setattr(ev, "_PROD_DB_CACHE", {})
    real = tmp_path / "prod.db"
    real.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" + str(real).replace(os.sep, "/"))
    assert ev.prod_database_path() == str(real)
    ghost = tmp_path / "gone.db"
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///" + str(ghost).replace(os.sep, "/"))
    assert ev.prod_database_path() == str(real), "第二轮解析到了别的库 ⇒ 复制来的是残缺配置"
    assert not ghost.exists()


def test_同进程跑第二轮生成评测必须被拒绝():
    src = inspect.getsource(ev.run_generate_eval)
    assert src.index("_GEN_ROUNDS_IN_PROC") < src.index("mkdtemp"), \
        "拦截没在最前面＝已经建了临时库才报错，留下半成品目录"
    ev._GEN_ROUNDS_IN_PROC.append(1)                    # 假装这个进程已经跑过一轮
    try:
        with pytest.raises(RuntimeError, match="另起进程"):
            asyncio.run(ev.run_generate_eval([], repeat=1))
    finally:
        ev._GEN_ROUNDS_IN_PROC.pop()


def _stub_run(**kw):
    r = {"pass": True, "actions_seen": ["MEMO"], "dates_seen": [], "exemptions": [],
         "payloads": ["朵朵"], "err": ""}
    r.update(kw)
    return r


def test_多跑折叠必须如实给出一致率与每一跑留底():
    """这段逻辑原先 inline 在跑分循环里，而那个函数在 pytest 跑不了（engine 按进程缓存）——
    变异电池当场演示：`consistent` 硬写成 True、`other_runs` 折成空表，守卫**两条都照样绿**（10-06）。
    挪成 `fold_runs` 纯函数之后才第一次真的被测到，所以这两条断言就是那两次没牙的还债。"""
    drift = _stub_run(payloads=["城北老小区"], err="E4", actions_seen=[], **{"pass": False})
    f = ev.fold_runs([_stub_run(), drift, _stub_run()])
    assert (f["n_runs"], f["pass_all"], f["pass_any"]) == (3, False, True), f
    assert f["consistent"] is False, "有一跑结果不同却报「一致」＝不可复现的尺子又装了回来"
    assert f["err_spread"] == "/E4", f
    assert len(f["other_runs"]) == 2, "第 2、3 跑没留底＝一致率只剩分子、没法复核"
    assert f["other_runs"][0]["actions"] == "无", "漂移那跑没产出动作，明细里要写成「无」而不是空"
    assert f["other_runs"][0]["chan"] == "无", "第 2、3 跑的记忆通道没留底＝落库型题的漂移看不见"
    same = ev.fold_runs([_stub_run(), _stub_run(), _stub_run()])
    assert same["consistent"] is True and same["pass_all"] is True, same
    assert same["err_spread"] == "" and same["n_runs"] == 3, same
    # 只漂**内容**不漂结果（三跑都判过、载荷却不同）也必须算不一致——否则一致率退化成"过没过"
    subtle = ev.fold_runs([_stub_run(), _stub_run(payloads=["煤球"]), _stub_run()])
    assert subtle["pass_all"] is True and subtle["consistent"] is False, subtle
    # 10-10 拍板的新口径：只差钟点＝时间流过了，算一致；但「过／不过」那一维不剥
    clock = [dict(p="接送牌要填她的名字，17:12 说"), dict(p="接送牌要填她的名字，17:13 说"),
             dict(p="接送牌要填她的名字，17:12 说")]
    ck = ev.fold_runs([_stub_run(payloads=[x["p"]]) for x in clock])
    assert ck["consistent"] is True, "只差钟点还判不一致＝一致率量的还是跑了多久"
    ck2 = ev.fold_runs([_stub_run(payloads=["接送牌填朵朵"]), _stub_run(payloads=["接送牌填煤球"]),
                        _stub_run(payloads=["接送牌填朵朵"])])
    assert ck2["consistent"] is False, "正文字不同不能被判成一致"
    ck3 = ev.fold_runs([_stub_run(), _stub_run(**{"pass": False, "err": "E4"}), _stub_run()])
    assert ck3["consistent"] is False, "有一跑没过不能因为剥了钟点就洗成一致"
    # 指纹必须逐跑留底（报表的可复核性就建在这串文本上）
    assert f["sig"] == ev.run_sig_text(f), "首跑指纹没写回行里"
    assert f["sig"] != f["other_runs"][0]["sig"], "两条指纹相同却判不一致＝一致率与留底脱钩"
    assert subtle["sig"] != subtle["other_runs"][0]["sig"], "载荷漂了指纹却没变"
    assert "无豁免" in ev.run_sig_text(_stub_run()), "指纹里没预留考维度＝离线复算必缺维"


def test_main把repeat与certify_j12接到各自的入口(tmp_path, monkeypatch):
    """参数在 CLI 上有、却没传到真正干活的那个函数＝静默按缺省跑（三跑变一跑还报一致）。"""
    seen = {}

    async def fake_gen(cases, **kw):
        seen["gen"] = kw
        return {"rows": []}

    async def fake_eval(cases, **kw):
        seen["eval"] = kw
        return {}

    monkeypatch.setattr(ev, "run_generate_eval", fake_gen)
    monkeypatch.setattr(ev, "run_eval", fake_eval)
    monkeypatch.setattr(ev, "render_generate", lambda rep: "GEN")
    monkeypatch.setattr(ev, "render", lambda rep, **kw: "EVAL")
    monkeypatch.setattr(ev, "lint_dataset", lambda cs: [])   # 题面合法性由 lint 自己的用例管
    ds = tmp_path / "d.jsonl"
    ds.write_text(json.dumps({"cid": "x", "judge": "J1"}, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    out = tmp_path / "r.md"
    argv = ["x", "--dataset", str(ds), "--judges", "J1", "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv + ["--mode", "generate", "--allow-llm", "--repeat", "2"])
    assert asyncio.run(ev.main()) == 0
    assert seen["gen"]["repeat"] == 2, "--repeat 没接到 run_generate_eval"
    solv = tmp_path / "s.jsonl"
    monkeypatch.setattr(sys, "argv", argv + ["--mode", "certify", "--certify-j12",
                                             "--emit-solvability", str(solv)])
    assert asyncio.run(ev.main()) == 0
    assert seen["eval"]["cert_j12"] is True, "--certify-j12 没接到 run_eval（报表会缺那句声明）"
    assert solv.exists(), "认证档要求必须落的 solvability 文件没落"

    # 分块跑法的两个开关也必须接到入口（漏一个就是"以为在跑一题、其实把 10 题都计费了"）
    emitted = tmp_path / "rows.jsonl"
    monkeypatch.setattr(ev, "render_generate",
                        lambda rep: "GEN" if rep.get("rows") else "EMPTY")
    async def fake_gen2(cs, **kw):
        seen["gen2"] = kw
        return {"rows": [{"cid": "j1z", "category": "fact", "judge": "J1", "pass": True,
                          "err": "", "why": "", "actions_seen": ["MEMO"], "payloads": ["朵朵"],
                          "mem_channel": [], "exemptions": [], "dates_seen": [],
                          "n_ctx_msgs": 1, "ctx_chars": 10, "n_recalled": 1, "gold_in_recall": 1,
                          "gold_in_ctx": 1, "temperature": 0.0, "reasoning_level": 0,
                          "library_rows": 50, "reply_head": "", "reply_text": ""}],
                **ev.generate_summary([{"pass": True, "err": ""}]),
                "k": 5, "flags": {}, "borrowed": {}, "llm": {}, "usage": {},
                "skipped_judges": ["J2"]}
    monkeypatch.setattr(ev, "run_generate_eval", fake_gen2)
    monkeypatch.setattr(sys, "argv", argv + ["--mode", "generate", "--allow-llm", "--repeat", "3",
                                             "--only-cid", "j1z", "--emit-rows", str(emitted)])
    assert asyncio.run(ev.main()) == 0
    assert seen["gen2"]["only_cid"] == "j1z", "--only-cid 没接到 run_generate_eval ⇒ 一跑就是 30 次"
    assert emitted.exists() and '"j1z"' in emitted.read_text(encoding="utf-8"), \
        "--emit-rows 没落盘 ⇒ 分块跑完没法离线合并复算"


def test_每一跑必须独占角色号段与那份库():
    """10-06 中午的读数订正：`--repeat` 三跑原本**共用一个 cid 与一份库**，
    而第 1 跑会把"这些记忆已展示过"写进持久状态 ⇒ 第 2 跑开始丢记忆、第 3 跑整段「和你相关的记忆」
    渲染成「暂无」。这样量的是"第 1 跑把状态改成了什么"，不是"模型稳不稳"。
    （同族错＝10-04 的「多配置连跑时角色号段必须按配置×用例独占」，我在 repeat 上又犯了一遍。）
    """
    src = inspect.getsource(ev.run_generate_eval)
    assert "i * n_rep + rep_i" in src, "号段又退回按用例独占 ⇒ 跑次之间会互相污染"
    assert src.count("await seed_library(") >= 1 and "for rep_i in range(n_rep)" in src, \
        "灌库没在每一跑里面做 ⇒ 三跑共用同一份库"
    assert "cid, _seed_ids, _dis, n_fil, _skip = await seed_library(c, i, user_id" not in src, \
        "旧的按用例独占写法又回来了"


def test_每跑都要留gold进prompt的底():
    """`gold 进 prompt` 是"这条记忆到底有没有被模型看到"的唯一硬证据；
    上一轮的污染正是靠它现形的（第 2、3 跑掉到 0）。"""
    r1 = dict(_stub_run(), gold_in_ctx=1)
    r2 = dict(_stub_run(), gold_in_ctx=0, payloads=[])
    f = ev.fold_runs([r1, r2, dict(r1)])
    assert f["other_runs"][0]["gold_ctx"] == 0, "第 2 跑的 gold 可见性没留底＝污染又看不见"
    assert f["gold_in_ctx"] == 1
    out = ev.render_generate(_fake_rep_multi())
    assert "gold 进 prompt=" in out, "报表没逐跑打 gold 可见性"


def test_计数器真按task分开数而不是折叠侧自己塞的():
    """批 46 的教训原样复发过一次：折叠侧的桩自己把字段喂进去＝取料侧没被证明。
    这里真装一次计数器，看**入口那半条链**有没有把账按 task 分开。"""
    import app.agent.llm_client as lc

    async def _fake(messages, **kw):
        return "ok"
    mp = pytest.MonkeyPatch()
    mp.setattr(lc, "chat_completion", _fake)
    box = ev.install_llm_call_counter()
    try:
        async def _drive():
            await lc.chat_completion([{"role": "system", "content": "x"}], task="memory")
            await lc.chat_completion([{"role": "system", "content": "y"}], task="chat")
            await lc.chat_completion([{"role": "system", "content": "z"}], task="chat")
        asyncio.run(_drive())
        assert box["n"] == 3, box
        assert box["by_task"] == {"memory": 1, "chat": 2}, \
            "入口没按 task 分账＝报表那个拆分是编出来的：%s" % box.get("by_task")
    finally:
        ev.uninstall_llm_call_counter(box)
        assert lc.chat_completion is _fake, "还原没落地＝下一段测试会打在桩上"
        mp.undo()


def test_计数器的分账真的接到了报表那行数():
    """`run_generate_eval` 在 pytest 里跑不了（engine 按进程缓存），这一维只能用源码锚点钉住：
    接线一断＝报表永远是 `{}`，而下一个人会把空拆分读成"没有装配侧调用"。"""
    src = (REPO / "scripts" / "diagnostics" / "memory_action_eval.py").read_text(encoding="utf-8")
    assert 'usage["by_task"] = dict(counter.get("by_task") or {})' in src, \
        "计数器与报表之间的接线断了（报表会装作没有 task 拆分）"


def test_报表把请求按task拆开并明说请求数不等于跑次数():
    """10-10 的账就是这么错的：题×跑＝30，实际 60 发（装配里补生成日摘要那一发也计费）。"""
    rep = _fake_rep()
    rep["usage"]["by_task"] = {"memory": 5, "chat": 5}
    out = ev.render_generate(rep)
    assert "按 task 拆分" in out, "报表只给一个总数＝下一个人还会把跑次数当钱数"
    assert '"memory": 5' in out and '"chat": 5' in out, "task 拆分没落到报表上"
    assert "请求数≠跑次数" in out, "拆了却没说清为什么拆＝数字还在但结论照样读错"
    assert "请求数≠跑次数" in out, "拆了却没说清为什么拆＝数字有了但结论照样读错"
    # 反向钉：桩里没有 by_task 时必须看得见"没这个数"，不许凭空造一个好看的拆分
    plain = _fake_rep()
    assert "by_task" not in json.dumps(plain["usage"])
    assert '按 task 拆分**={}' in ev.render_generate(plain), "读不到拆分却要装作读到了"


def test_计费预告必须在第一发请求之前打出来():
    """这条路上每次请求都计费：跑完才发现"其实是 30 次"来不及撤，所以先把账摆在前面。"""
    src = inspect.getsource(ev.main)
    assert "计费预告" in src and src.index("计费预告") < src.index("await run_generate_eval"), \
        "预告被打在了跑完之后（或根本没打）"
    assert "×" in src[src.index("计费预告"):src.index("计费预告") + 400], "预告只给题数不给乘法"
    _seg = src[src.index("计费预告"):src.index("await run_generate_eval")]
    assert "发准备" in _seg and "别把跑次数当请求数" in _seg, \
        "预告只报「题×跑＝请求」＝钱数会少报一倍（10-10 两轮各 60 发被记成 30 次）"


def test_重复跑的圈数必须引用repeat参数且缺省是3():
    """桩脚本能验"到底跑了几遍"，pytest 里跑不了整链路（engine 按进程缓存），所以这里钉两处死：
    循环上界必须读 `repeat`（写死 1 就是 `--repeat` 成摆设），缺省必须等于 §S3 的三跑口径。"""
    src = inspect.getsource(ev.run_generate_eval)
    assert "n_rep = max(1, int(repeat))" in src and "for rep_i in range(n_rep)" in src, \
        "循环上界不再引用 repeat ⇒ --repeat 成了摆设"
    assert ev.GENERATE_REPEAT_DEFAULT == 3, "§S3 的认证口径是三跑；缺省被改成 1＝悄悄降级"
    assert "default=GENERATE_REPEAT_DEFAULT" in inspect.getsource(ev.main), "CLI 缺省没跟着常量走"


# ─────────────── ⑥ 拍板口径（每一跑都过）＋ 分块跑法的合并步（10-06 上午） ───────────────
@pytest.mark.parametrize("judges,mode,allow,rows,refuse", [
    (["J1"], "recount", False, "r.jsonl", False),    # 复算不碰模型 ⇒ 不该被计费闸拦
    (["J1"], "recount", False, "", True),            # 没给输入就没得算
    (["J1"], "recount", True, "r.jsonl", False),     # 给了授权也不许反过来变成"必须花钱"
    (["J3"], "recount", False, "r.jsonl", True),     # 复算只认生成侧 J1 的落盘行
    (["J1"], "generate", False, "r.jsonl", True),    # 真正的计费路仍然要 --allow-llm
], ids=["复算不需要授权", "缺rows要拒", "带授权也算完就停", "J3不混进来", "计费闸不受影响"])
def test_recount档的闸只对输入负责(judges, mode, allow, rows, refuse):
    got = ev.refusal(judges, mode, allow, rows_path=rows)
    assert bool(got) is refuse, "%s/%s/rows=%r 期望%s，实际 %r" % (
        judges, mode, rows, "拒绝" if refuse else "放行", got)
    if refuse and not rows:
        assert "--rows" in got, "拒绝文案要指出缺的那个参数"


def test_recount档不建临时库也不碰模型(tmp_path, monkeypatch):
    """复算这一步存在的意义就是"零计费重看一遍"——它要是还去建库/装配，就等于把成本又走一遍。"""
    called = []

    async def boom(*a, **k):
        called.append(1)
        raise AssertionError("recount 档不该跑生成评测")

    monkeypatch.setattr(ev, "run_generate_eval", boom)
    rows = [dict(_stub_run(), cid="j1a", category="fact", judge="J1", n_runs=3,
                 pass_all=True, consistent=True, sig="过｜MEMO｜无日期｜无豁免｜朵朵｜无通道",
                 other_runs=[{"sig": "过｜MEMO｜无日期｜无豁免｜朵朵｜无通道", "pass": True,
                              "err": "", "actions": "MEMO", "dates": "", "chan": "无",
                              "payloads": "朵朵"}] * 2)]
    js = tmp_path / "r.jsonl"
    js.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                  encoding="utf-8")
    out = tmp_path / "rep.md"
    # 故意**不给 --dataset**：复算档只读 --rows，能给题面就说明它还想去算一遍
    monkeypatch.setattr(sys, "argv", ["x", "--judges", "J1", "--mode", "recount",
                                      "--rows", str(js), "--out", str(out)])
    monkeypatch.chdir(tmp_path)
    assert asyncio.run(ev.main()) == 0
    assert not called, "recount 还是去跑生成评测了 ⇒ 又要计费"
    assert "生成侧认证" in out.read_text(encoding="utf-8"), "复算报表没打拍板口径那一行"
    # 反过来：别的档缺 --dataset 必须被拒（原来由 argparse required 顶着，放开后要在 main 里顶回来，
    # 否则会一路走到 open("") 报一个看不懂的错）
    monkeypatch.setattr(sys, "argv", ["x", "--judges", "J1", "--mode", "generate", "--allow-llm",
                                      "--out", str(out)])
    assert asyncio.run(ev.main()) == 2


def test_落盘行必须按cid去重_后来者覆盖(tmp_path):
    """分块跑最大的坑是"死一块重跑一块"⇒ 同一题在文件里留两行。
    不去重就直接算，10 题会被读成 11 题，AR 与一致率一起被灌水。"""
    a = dict(_stub_run(), cid="j1a", n_runs=3, pass_all=True, consistent=True)
    b = dict(_stub_run(), cid="j1b", n_runs=3, pass_all=False, consistent=False)
    a2 = dict(_stub_run(payloads=["煤球"]), cid="j1a", n_runs=3, pass_all=False, consistent=False)
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in (a, b, a2)) + "\n",
                 encoding="utf-8")
    rows = ev.rows_from_jsonl(str(p))
    assert [r["cid"] for r in rows] == ["j1a", "j1b"], rows
    assert rows[0]["pass_all"] is False, "没让后来者覆盖 ⇒ 重跑的那次没生效"
    s = ev.generate_summary(rows)
    assert s["n"] == 2, "分母被重复行撑大＝读数全部失真"


def test_一致判定必须能从留底重跑并与存值对账():
    same = "过｜MEMO｜无日期｜无豁免｜朵朵｜无通道"
    drift = "过｜MEMO｜无日期｜城北老小区｜朵朵｜无通道"
    row = {"cid": "j1a", "pass": True, "err": "", "consistent": True, "sig": same,
           "other_runs": [{"sig": same}, {"sig": same}]}
    assert ev.sig_recompute(row) is True
    bad = dict(row, sig=same, other_runs=[{"sig": same}, {"sig": drift}], consistent=True)
    assert ev.sig_recompute(bad) is False, "留底里明明漂了却没算出来"
    rep = ev.recount_rep([dict(bad, cid="j1x"), dict(row, cid="j1y")])
    assert rep["sig_mismatch"] == ["j1x"], rep["sig_mismatch"]
    assert ev.sig_recompute({"cid": "s", "pass": True}) is None, "单跑题不该有一致可言"
    # 真命中"留底不完整"那条分支：有 other_runs、也有首跑指纹，但第 3 跑没写指纹
    partial = {"cid": "p", "pass": True, "consistent": True, "sig": same,
               "other_runs": [{"sig": same}, {}]}
    assert ev.sig_recompute(partial) is None, \
        "留底缺一条就该回答『算不出来』，而不是替它判过（没底也算过＝这一列又变成信代码）"


def test_逐跑输入指纹必须留底并且分得清抖动与污染():
    """10-10 认证读数里 15 条失败有 6 条是「首跑过、第 2/3 跑不过」，当时的留底答不出
    「输入变了还是模型抖了」⇒ 那 30 次计费换不到结论。这条守卫钉住缺的那一维。"""
    a = _stub_run(prompt_fp="a" * 12, ctx_chars=3338, n_ctx_msgs=5)
    b = _stub_run(prompt_fp="b" * 12, ctx_chars=2810, n_ctx_msgs=4)
    f = ev.fold_runs([dict(a), dict(a), dict(a)])
    assert f["prompt_fp"] == "a" * 12 and f["input_identical"] is True, f
    assert f["other_runs"][0]["prompt_fp"] == "a" * 12, "第 2 跑的输入没留底＝这一维又看不见"
    assert f["other_runs"][0]["ctx_chars"] == 3338, "逐跑的 prompt 体量没留底＝污染与抖动还是没法分"
    # 逐跑的**回复起头**也必须进留底（10-10 批 49）：E3 有 14/20 出在第 2、3 跑
    g = _stub_run(prompt_fp="g" * 12, reply_head="【策略：简短回应】 好，记下了")
    h = ev.fold_runs([dict(g), dict(g), dict(g)])
    assert h["other_runs"][0]["reply_head"].startswith("【策略：简短回应】"), \
        "第 2/3 跑没存起头＝那两跑的 E3 只能判「没产出」，判不出「它当时写了什么」"
    g = ev.fold_runs([dict(a), dict(b), dict(a)])
    assert g["input_identical"] is False and g["fp_spread"] == ["a" * 12, "b" * 12], g
    # 反向钉：没留 fp（旧行／单跑档）时不许凭空喊污染
    h = ev.fold_runs([_stub_run(), _stub_run()])
    assert h["input_identical"] is False and h["fp_spread"] == [], h


def test_剥掉钟点才算同一份输入():
    """`## 当前时间` 是活时钟 ⇒ 原始 fp 必然逐跑不同。判"污染"要用**稳定指纹**，不然每一题
    都会被误判成污染（10-10 实测：10/10 题三跑原始 fp 全不同，就是这个坑）。"""
    a = _stub_run(prompt_fp="r1", prompt_fp_stable="s1")
    b = _stub_run(prompt_fp="r2", prompt_fp_stable="s1")     # 只差钟点
    f = ev.fold_runs([dict(a), dict(b), dict(a)])
    assert f["input_identical"] is True, "只差钟点必须算同一份输入"
    assert f["clock_only_drift"] is True and f["fp_spread"] == ["s1"], f
    g = ev.fold_runs([dict(a), _stub_run(prompt_fp="r3", prompt_fp_stable="s9"), dict(a)])
    assert g["input_identical"] is False, "稳定指纹不同＝真的换了料，必须报污染"


def test_mask_clock只剥时间不吞正文():
    s = ev.mask_clock("当前 2026-10-10 17:12:03，用户 3 天前说过现居城西；朵朵 2021年出生")
    assert "<日>" in s and "<钟>" in s, s
    assert "现居城西" in s and "朵朵" in s, "剥钟点不许顺手吞掉正文"
    assert ev.mask_clock("用户女儿叫朵朵") == "用户女儿叫朵朵", "无时间的正文必须逐字不动"
    # 零计费探针实测到的缺口：装配里那段是**中文日期＋星期**，只剥 ISO 形式会漏
    # ⚠ 断言要盯「年字有没有」：只盯整串会被 10月10日 那条规则先满足——
    #    变异电池 M3（删掉中文日期那条掩码）当时就是这样没牙的（10-10 实测）。
    s2 = ev.mask_clock("2026年10月10日 星期六 17:12（北京时间）｜秋季｜距上次互动 4 天前")
    assert "年" not in s2, "中文年份没剥掉＝还会把每次装配都判成污染：%r" % s2
    assert "17:12" not in s2 and "星期" not in s2, s2
    assert "秋季" in s2 and "北京时间" in s2, "只该剥时间，不该剥季节/时区这些正文"
    assert ev.mask_clock("2026年10月10日 星期六 17:13（北京时间）｜秋季｜距上次互动 4 天前") == s2, \
        "只差一分钟必须归一成一个指纹"
    assert ev.stable_fp([{"content": "现在 08:00"}, {"content": "现在 08:03"}]) == \
        ev.stable_fp([{"content": "现在 08:00"}, {"content": "现在 09:11"}]), "同一份时间只差钟点要同指纹"


def test_报表对漂移必须给出定性而不是只报数():
    rep = _fake_rep()
    r0, r1 = rep["rows"]
    jitter = [dict(r0, prompt_fp="f" * 12, exemptions=[]),
              dict(r0, prompt_fp="f" * 12, exemptions=[], **{"pass": False, "err": "E3"}),
              dict(r0, prompt_fp="f" * 12, exemptions=[])]
    poison = [dict(r1, prompt_fp="a" * 12), dict(r1, prompt_fp="b" * 12)]
    rep["rows"] = [ev.fold_runs(jitter), ev.fold_runs(poison)]
    rep.update(ev.generate_summary(rep["rows"]))
    out = ev.render_generate(rep)
    assert "输出抖动" in out, "输入同一份却不每跑都过＝必须点明是抖动而非检索退化"
    assert "污染" in out, "三跑输入不同一份必须点名污染"


def test_认证口径只认每一跑都过():
    """10-06 拍板：J1 分数与认证读「每一跑都过」，不是"三跑里最好的一跑"。"""
    ok = {"pass_all": True, "consistent": True}
    assert ev.certified_j1(ok) is True
    assert ev.certified_j1({"pass_all": False, "consistent": True}) is False, "蒙对一次就算认证"
    assert ev.certified_j1({"pass_all": True, "consistent": False}) is False, "过了但不稳也算认证"
    assert "pass_all" in ev.CERT_J1_RULE and "一致" in ev.CERT_J1_RULE, "口径常量得写清读哪两列"
    out = ev.render_generate(_fake_rep_multi())
    assert "每一跑都过 ∧ 三跑一致" in out and "生成侧认证 **1／2**" in out, out[:400]


def test_跑一题必须把真实装配的输入指纹写进行(tmp_path, monkeypatch):
    """`generate_case` 那一行必须由**真 prompt 文本**算出 `prompt_fp`，不是折叠侧自己造的。

    来历：变异电池 M4（把 `hashlib.sha1(...)` 那三行删掉）当时 103 例全绿——折叠守卫拿
    `_stub_run(prompt_fp=...)` 自己把指纹喂进去，于是**取料那半条链**没人证过（同族前科：
    打桩测调用≠测落库）。这条把桩打在装配上，算的是 sha1 的真值。
    """
    import hashlib as _hl

    msgs = [{"content": "你是小爱，正在陪用户"}, {"content": "用户女儿叫朵朵，2021年出生"}]

    async def _state(case, cid, user_id, flags=None):
        return {"context_messages": msgs, "retrieved_memories": [], "temperature": 0.0}

    async def _llm(messages=None, **kw):
        return "【MEMO】用户女儿叫朵朵[/MEMO]"

    import app.agent.llm_client as llm_mod

    monkeypatch.setattr(ev, "gen_state", _state)
    monkeypatch.setattr(llm_mod, "chat_completion", _llm)
    case = {"cid": "jfp01", "category": "fact", "judge": "J1",
            "seeds": [{"content": "用户女儿叫朵朵，2021年出生"}],
            "expect": {"action": "MEMO", "gold_seed_idx": [0]}}
    row = asyncio.run(ev.generate_case(case, 9001, 9002))
    want = _hl.sha1("\n".join(m["content"] for m in msgs).encode("utf-8")).hexdigest()[:12]
    assert row["prompt_fp"] == want, "输入指纹不是从装配正文算出来的＝这一维又是装饰"
    assert row["ctx_chars"] == sum(len(m["content"]) for m in msgs), row["ctx_chars"]
