# -*- coding: utf-8 -*-
"""P0 语义统一 · 第 3 步：Observation / Event 语义贯通（行为变更，flag 门控默认关）。

守的六条底线（方案 v1 步骤 3 / 差距表 G6·G9＋词表收口）：

1. **flag 关＝注入文本逐字节等于旧文本**：三处注入点（``agent/runtime.py`` 的工具分支与小手机
   分支、``agent/mcp_tools.py`` 的 MCP 分支）各钉一条参数化断言，期望串直接照 HEAD 原文写死；
2. **flag 开＝新增认知标注且旧语义内容一个不丢**（工具名/摘要/那句「不要说我去执行了」都在）；
3. **provenance 词表收口**：收口文件内不留散落字面量（棘轮 0），全仓散落字面量只减不增（棘轮基线）；
4. **事件侧只判定+只计数**：``normalize_sender`` 差异、``origin`` 非法值全部只进内存计数，
   **落库值逐字不变**（本步不折半、不拒收）；
5. **``require_actor`` 是新纯判定**，``require_speaker`` 的抛出语义原样不动；
6. **观测面只读**：计数端点零查询零写库；判定/计数异常一律 fail-open，不阻断主链路。

异步用例统一 ``asyncio.run``（项目无 pytest-asyncio，对齐 tests/test_domain_events.py）。
"""
import asyncio
import inspect
import re
from pathlib import Path

import pytest

from app import actors
from app.agent import mcp_tools as mcp_tools_mod
from app.agent import runtime as runtime_mod
from app.agent import tools as tools_mod
from app.agent.actions import AgentAction
from app.actors import (
    OBS_PROVENANCE_EMOTION_CARE,
    OBS_PROVENANCE_MEMORY_EXTRACT,
    OBS_PROVENANCE_MEMORY_FACT_CHECK,
    OBS_PROVENANCE_MEMORY_SUMMARY,
    OBS_PROVENANCE_MCP_PREFIX,
    OBS_PROVENANCE_NOTE,
    OBS_PROVENANCE_TOOL,
    OBS_PROVENANCE_VALUES,
    OBS_PROVENANCE_WEB_SEARCH,
)
from app.agent.tools import ToolSpec, register_tool, unregister_tool
from app.events import store as event_store
from app.events.schema import require_actor, require_speaker
from app.flags.agent_flags import AGENT_FLAGS

FLAG = "observation_label_v1"

# 旧文本（HEAD 原文逐字）：flag 关时必须一字不差复原这三条
LEGACY_NOTE = "【工具结果】已记录到小手机（{name}）。基于真实结果继续回复，不要说'我去执行了'。"
LEGACY_TOOL = "【工具结果】工具 {name} 已执行完成：{obs}（基于真实结果继续回复，不要说'我去执行了'）。"
LEGACY_MCP = ("【工具结果】MCP 工具 {action} 已执行完成：{obs}"
              "（基于真实结果继续回复，不要说'我去执行了'）。")


# ═══════════════════════════ fixtures / helpers ═══════════════════════════

@pytest.fixture(autouse=True)
def _counters_clean():
    """每条用例从干净计数开始（进程内计数跨用例累积会让断言变成玄学）。"""
    tools_mod.reset_observation_semantics_counters()
    event_store.reset_domain_event_semantics_counters()
    yield
    tools_mod.reset_observation_semantics_counters()
    event_store.reset_domain_event_semantics_counters()


def _set_flag(monkeypatch, on: bool) -> None:
    monkeypatch.setitem(AGENT_FLAGS, FLAG, on)


def _fake_tool_runner(monkeypatch, observation: dict, *, status: str = "ok"):
    """把 execute_tool 换成固定 observation 的假实现（注入点读的就是这个 dict）。"""
    async def _fake(spec, payload, **kw):
        return {"status": status, "tool": spec.name,
                "result": {"ok": True}, "observation": dict(observation)}

    monkeypatch.setattr("app.agent.tool_runner.execute_tool", _fake)


def _fake_parse_actions(monkeypatch, action_types):
    """标记解析换成固定动作列表（不依赖具体标记正则，专注注入文本本身）。"""
    actions = [AgentAction(action_type=a, payload={}, raw="") for a in action_types]

    def _fake(text):
        return list(actions)

    monkeypatch.setattr("app.agent.actions.parse_actions", _fake)


def _probe_spec(name: str, action_type: str, **kw) -> ToolSpec:
    return ToolSpec(name=name, description="probe", action_type=action_type,
                    execute=lambda p: {"ok": True}, **kw)


def _with_spec(spec: ToolSpec, run):
    """临时登记探针工具并跑一次，跑完按原状还原注册表（别把真实 note_memo 之类留在身后）。"""
    from app.agent.tools import _REGISTRY
    previous = _REGISTRY.get(spec.name)
    register_tool(spec)
    try:
        return run()
    finally:
        unregister_tool(spec.name)
        if previous is not None:
            register_tool(previous)


def _run_runtime_stage(monkeypatch, *, spec: ToolSpec, action_type: str, observation: dict):
    """跑一次 runtime._run_tool_stage，返回注入到 context_messages 的那条 content。"""
    _fake_parse_actions(monkeypatch, [action_type])
    _fake_tool_runner(monkeypatch, observation)
    state = {"ai_response": "x", "tools_used": [], "context_messages": []}

    def _go():
        asyncio.run(runtime_mod._run_tool_stage(
            state, [], character_id=1, user_id=1, session_id=1))

    _with_spec(spec, _go)
    assert state["context_messages"], "工具分支没有注入任何上下文"
    return state["context_messages"][-1]["content"]


def _run_mcp_stage(monkeypatch, *, spec: ToolSpec, observation: dict) -> str:
    _fake_parse_actions(monkeypatch, [spec.name])
    _fake_tool_runner(monkeypatch, observation)
    state = {"ai_response": "x", "tools_used": [], "context_messages": []}

    def _go():
        asyncio.run(mcp_tools_mod.run_mcp_tool_stage(
            state, [], user_id=1, character_id=1, session_id=1))

    _with_spec(spec, _go)
    assert state["context_messages"], "MCP 分支没有注入任何上下文"
    return state["context_messages"][-1]["content"]


# ═══════════════════════════ ① flag 登记与门控 ═══════════════════════════

def test_flag_默认关且双面登记():
    """新键必须先存在于 AGENT_FLAGS（默认 False），并在开关目录有用户向条目（双向锁）。"""
    assert FLAG in AGENT_FLAGS
    assert AGENT_FLAGS[FLAG] is False, "注入文本变更默认必须关"
    from app.application.flag_catalog import FLAG_CATALOG, meta_for
    assert set(AGENT_FLAGS) == set(FLAG_CATALOG), "AGENT_FLAGS 与 catalog 双向锁破了"
    m = meta_for(FLAG, "zh")
    assert m["title"].strip() and m["desc"].strip()
    assert m["visible"] is False


def test_flag_判定面不可用时按关处理(monkeypatch):
    """开关面坏掉 ⇒ observation_label_enabled False ⇒ 注入片段空串（旧文本），绝不抛。"""
    import app.flags.agent_flags as af

    class _Boom:
        def get(self, *a, **kw):
            raise RuntimeError("switch board down")

    monkeypatch.setattr(af, "AGENT_FLAGS", _Boom())
    assert tools_mod.observation_label_enabled() is False
    assert tools_mod.observation_tag({"epistemic_status": "FACT", "provenance": "tool"}) == ""


# ═══════════════════════════ ② provenance 词表收口 ═══════════════════════════

def test_词表常量值逐字照现状():
    """收口＝换写法不换值：任何一处改名都会让存量标注漂移，这里逐字钉住。"""
    assert OBS_PROVENANCE_TOOL == "tool"
    assert OBS_PROVENANCE_WEB_SEARCH == "web_search"
    assert OBS_PROVENANCE_NOTE == "note"
    assert OBS_PROVENANCE_MEMORY_EXTRACT == "memory_extract"
    assert OBS_PROVENANCE_MEMORY_FACT_CHECK == "memory_fact_check"
    assert OBS_PROVENANCE_EMOTION_CARE == "emotion_care"
    assert OBS_PROVENANCE_MEMORY_SUMMARY == "memory_summary"
    assert OBS_PROVENANCE_MCP_PREFIX == "mcp:"
    assert len(set(OBS_PROVENANCE_VALUES)) == len(OBS_PROVENANCE_VALUES)
    assert OBS_PROVENANCE_MCP_PREFIX not in OBS_PROVENANCE_VALUES  # 前缀不是取值


def test_ToolSpec默认来源与MCP适配走常量():
    """默认值与 MCP 适配层的值必须与改前逐字相同（含 f-string 拼接结果）。"""
    assert ToolSpec(name="n", description="d").provenance == "tool"
    from app.mcp.tool_adapter import mcp_tool_to_spec
    spec = mcp_tool_to_spec("demo", {"name": "echo", "description": "", "input_schema": {}}, 1)
    assert spec.provenance == "mcp:demo"
    assert spec.provenance == f"{OBS_PROVENANCE_MCP_PREFIX}demo"
    assert spec.epistemic_status == "UNVERIFIED"


def test_内部工具注册值全在词表内():
    """agent/tools.py 注册的 5 个内部工具，来源取值必须落进 OBS_PROVENANCE_VALUES。"""
    names = ("memory_extract", "memory_fact_check", "emotion_care", "weave_card", "memory_summary")
    for n in names:
        spec = tools_mod.get_tool(n)
        assert spec is not None, f"工具 {n} 未注册（注册表结构变了，棘轮需重核）"
        assert spec.provenance in OBS_PROVENANCE_VALUES, (n, spec.provenance)


# 散落字面量形态：provenance= "..." / provenance: str = "..."（Event 侧 provenance={...} 字典不在内）
# 引号后 (?!{) 排除「常量前缀拼接」的 f-string（f"{OBS_PROVENANCE_MCP_PREFIX}{server_name}" 属已收口写法）
_PROVENANCE_LITERAL_RE = re.compile(r"""provenance\s*(?::[^=\n]{0,20})?=\s*f?["'](?!{)[^"']+["']""")

# 基线：本步收口后，仍留在收口范围之外的注册点（内置工具 5 个，属后续批次）
_SCATTERED_BASELINE = 5


def _app_root() -> Path:
    return Path(actors.__file__).resolve().parent  # backend/app


def test_棘轮_收口文件内不留来源字面量():
    """纯机械替换的自证：收口完成的两个文件里，来源字段只能是常量引用。"""
    root = _app_root()
    for rel in ("agent/tools.py", "mcp/tool_adapter.py"):
        text = (root / rel).read_text(encoding="utf-8")
        hits = _PROVENANCE_LITERAL_RE.findall(text)
        assert not hits, f"{rel} 仍有散落来源字面量：{hits}"


def test_棘轮_全仓散落来源字面量只减不增():
    """全仓口径登记（只减不增）：新增散落字面量＝词表重新分裂，直接红。"""
    root = _app_root()
    found = {}
    for py in sorted(root.rglob("*.py")):
        hits = _PROVENANCE_LITERAL_RE.findall(py.read_text(encoding="utf-8", errors="replace"))
        if hits:
            found[str(py.relative_to(root))] = len(hits)
    total = sum(found.values())
    assert total <= _SCATTERED_BASELINE, f"散落来源字面量 {total} 处 > 基线：{found}"


# ═══════════════════════════ ③ 注入文本：flag 关＝逐字节旧文本 ═══════════════════════════

def test_flag关_三处注入文本逐字节等于旧文本(monkeypatch):
    """本单的自证核心：关＝期望串与 HEAD 原文 format 出来完全一致（含标点、空格、引号）。"""
    _set_flag(monkeypatch, False)
    obs = {"epistemic_status": "FACT", "provenance": "web_search", "summary": "查到 3 条"}

    got = _run_runtime_stage(
        monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
        observation=obs)
    assert got == LEGACY_TOOL.format(name="obs_probe", obs="查到 3 条")

    got_note = _run_runtime_stage(
        monkeypatch, spec=_probe_spec("note_memo", "OBS_MEMO"), action_type="OBS_MEMO",
        observation=obs)
    assert got_note == LEGACY_NOTE.format(name="note_memo")

    got_mcp = _run_mcp_stage(
        monkeypatch,
        spec=_probe_spec("mcp.demo.echo", "mcp.demo.echo", provenance="mcp:demo"),
        observation={"epistemic_status": "UNVERIFIED", "provenance": "mcp:demo", "summary": "外部返回"})
    assert got_mcp == LEGACY_MCP.format(action="mcp.demo.echo", obs="外部返回")

    # 关时标注全记为「被丢弃」＝现状丢失点 G6 被量化出来
    c = tools_mod.observation_semantics_counters()
    assert (c["injection_total"], c["injection_label_dropped"], c["injection_labeled"]) == (3, 3, 0)


@pytest.mark.parametrize("epi,prov,expect_prefix", [
    ("FACT", "web_search", "【工具结果·FACT·web_search】"),
    ("INFERRED", "mcp:ambrace", "【工具结果·INFERRED·mcp:ambrace】"),
])
def test_flag开_注入行带认知标签且旧语义内容不丢(monkeypatch, epi, prov, expect_prefix):
    """开＝前缀多两段标签，正文（工具名/摘要/继续回复约束）逐段仍在，不吞不改。"""
    _set_flag(monkeypatch, True)
    got = _run_runtime_stage(
        monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
        observation={"epistemic_status": epi, "provenance": prov, "summary": "摘要正文"})
    assert got.startswith(expect_prefix)
    tail = got[len(expect_prefix):]
    assert tail == LEGACY_TOOL.format(name="obs_probe", obs="摘要正文")[len("【工具结果】"):]
    assert "工具 obs_probe 已执行完成：摘要正文" in got
    assert "不要说'我去执行了'" in got
    assert tools_mod.observation_semantics_counters()["injection_labeled"] == 1


def test_flag开_小手机分支从不读observation改为补标注(monkeypatch):
    """旧分支是硬编码文案、完全不读 observation（地图 §1.2 第 5 丢失点）；开之后带上标签。"""
    _set_flag(monkeypatch, True)
    got = _run_runtime_stage(
        monkeypatch, spec=_probe_spec("note_calendar", "OBS_CAL"), action_type="OBS_CAL",
        observation={"epistemic_status": "FACT", "provenance": "note", "summary": "已写入日历"})
    assert got.startswith("【工具结果·FACT·note】已记录到小手机（note_calendar）。")
    assert got.endswith(LEGACY_NOTE.format(name="note_calendar")[len("【工具结果】"):])


def test_flag开_MCP分支来源为mcp命名空间(monkeypatch):
    _set_flag(monkeypatch, True)
    got = _run_mcp_stage(
        monkeypatch,
        spec=_probe_spec("mcp.demo.echo", "mcp.demo.echo", provenance="mcp:demo"),
        observation={"epistemic_status": "UNVERIFIED", "provenance": "mcp:demo", "summary": "外部返回"})
    assert got.startswith("【工具结果·UNVERIFIED·mcp:demo】MCP 工具 mcp.demo.echo 已执行完成：外部返回")
    assert got.endswith(LEGACY_MCP.format(action="mcp.demo.echo", obs="外部返回")[len("【工具结果】"):])


def test_标注缺失时兜底不抛(monkeypatch):
    """observation 没带认知态/来源（或压根不是 dict）时落兜底值，注入行结构不破。"""
    _set_flag(monkeypatch, True)
    assert tools_mod.observation_label({}) == ("UNVERIFIED", "tool")
    assert tools_mod.observation_label(None) == ("UNVERIFIED", "tool")
    assert tools_mod.observation_label({"epistemic_status": "  ", "provenance": 123}) == (
        "UNVERIFIED", "tool")
    got = _run_runtime_stage(
        monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
        observation={"summary": "只有摘要"})
    assert got.startswith("【工具结果·UNVERIFIED·tool】工具 obs_probe 已执行完成：只有摘要")
    assert tools_mod.observation_semantics_counters()["injection_label_unavailable"] == 1


def test_脏标注值不破坏括号框架(monkeypatch):
    """外部 MCP 服务器名不可控：带 】/·/换行的脏值必须被清洗，标签段不能提前闭合框架。"""
    _set_flag(monkeypatch, True)
    tag = tools_mod.observation_tag({"epistemic_status": "FA】CT", "provenance": "mcp:a·b\nc"})
    assert tag == "·FA_CT·mcp:a_b_c"
    assert "】" not in tag and "\n" not in tag
    assert tag.count("·") == 2  # 只有两个分隔点，模型侧结构不会被脏值撑破
    long_tag = tools_mod.observation_tag({"epistemic_status": "X" * 200, "provenance": "Y" * 200})
    assert len(long_tag) <= 2 + 2 * tools_mod._LABEL_MAX_CHARS


# ═══════════════════════════ ④ 计数：只加判定、不改文本 ═══════════════════════════

def test_计数_flag开全记为生效(monkeypatch):
    _set_flag(monkeypatch, True)
    _run_runtime_stage(
        monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
        observation={"epistemic_status": "FACT", "provenance": "tool", "summary": "s"})
    c = tools_mod.observation_semantics_counters()
    assert c["injection_total"] == 1 and c["injection_labeled"] == 1 and c["injection_label_dropped"] == 0
    assert tools_mod.observation_semantics_counters() is not c  # 返回副本，外部改不动


def test_计数异常不改注入文本(monkeypatch):
    """计数面坏掉（键被换掉）也不能污染注入行、不能抛断工具链。"""
    _set_flag(monkeypatch, False)
    broken = "not-a-dict"  # 任何下标写操作都会抛
    original = tools_mod._OBS_COUNTERS
    tools_mod._OBS_COUNTERS = broken
    try:
        got = _run_runtime_stage(
            monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
            observation={"epistemic_status": "FACT", "provenance": "tool", "summary": "摘要"})
    finally:
        tools_mod._OBS_COUNTERS = original
    assert got == LEGACY_TOOL.format(name="obs_probe", obs="摘要")


def test_标注函数对任意脏入参不抛():
    for junk in (None, 0, [], "", {"epistemic_status": None}, {"provenance": {"x": 1}}):
        assert tools_mod.observation_tag(junk) == ""  # flag 默认关
        epi, prov = tools_mod.observation_label(junk)
        assert epi == "UNVERIFIED" and prov == "tool"


# ═══════════════════════════ ⑤ Event：require_actor 与软校验计数 ═══════════════════════════

@pytest.mark.parametrize("event,expect", [
    ({"type": "t", "speaker": {"type": "user", "id": 3}}, True),
    ({"type": "t", "speaker": {"type": "character", "id": 1}}, True),
    ({"type": "t", "speaker": {"type": "user"}}, False),          # 缺 id
    ({"type": "t", "speaker": {"type": "user", "id": 0}}, False),  # id 假值
    ({"type": "t", "speaker": {"id": 3}}, False),                  # 缺 type
    ({"type": "t", "speaker": None}, False),
    ({"type": "t"}, False),                                        # 整个字段缺
    ({"speaker": "user"}, False),                                  # 脏值：字符串不是 dict
    ("not-a-dict", False),                                        # 脏值：非 dict 不抛
    (None, False),
])
def test_require_actor_纯判定含脏值缺字段(event, expect):
    assert require_actor(event) is expect


def test_require_speaker_抛出语义原样不动():
    """新增判定不能顺手改掉抛出版的契约（make_event 的 5 个调用点还依赖它）。"""
    with pytest.raises(ValueError):
        require_speaker({"type": "t", "speaker": {"type": "user"}})
    require_speaker({"type": "t", "speaker": {"type": "user", "id": 3}})  # 不抛
    with pytest.raises(AttributeError):
        require_speaker({"type": "t", "speaker": "user"})  # 脏值照旧抛，不被吞


class _FakeSession:
    def __init__(self):
        self.rows, self.committed = [], 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.rows.append(obj)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        pass


def _append(monkeypatch, **kw) -> _FakeSession:
    monkeypatch.setattr(event_store, "domain_events_enabled", lambda: True)
    fake = _FakeSession()
    monkeypatch.setattr(event_store, "async_session_factory", lambda: fake)
    asyncio.run(event_store.append_domain_event(
        "chat.message_sent", "chat_session", 7, entity_type="chat_message", entity_id=101, **kw))
    return fake


def test_actor归一差异只计数_落库值逐字不变(monkeypatch):
    """ai 归一是 character，但 domain_events.actor_type 仍写 ai（存量口径不动）；差异进计数。"""
    fake = _append(monkeypatch, actor_type="ai", actor_id=9, origin="ai_message")
    assert fake.rows[0].actor_type == "ai"
    assert fake.rows[0].origin == "ai_message"
    c = event_store.domain_event_semantics_counters()
    assert (c["append_total"], c["actor_total"], c["actor_diff"]) == (1, 1, 1)
    assert c["actor_unnormalized"] == 0 and c["origin_invalid"] == 0 and c["actor_no_speaker"] == 0


def test_origin非法值计数但不折半(monkeypatch):
    """方案里「非法值折成 system_event」本步**不生效**：只量比例，落库值原样。"""
    fake = _append(monkeypatch, actor_type="user", actor_id=9, origin="totally_unknown")
    assert fake.rows[0].origin == "totally_unknown"
    c = event_store.domain_event_semantics_counters()
    assert c["append_total"] == 1 and c["origin_invalid"] == 1
    assert c["actor_diff"] == 0  # user 归一后仍是 user


def test_actor缺失与半缺都进计数(monkeypatch):
    fake = _append(monkeypatch, actor_type=None, actor_id=None, origin="user_message")
    assert fake.rows[0].actor_type is None
    c = event_store.domain_event_semantics_counters()
    assert c["actor_missing"] == 1 and c["actor_total"] == 0
    assert c["actor_no_speaker"] == 1  # 组不出 speaker（require_actor False）

    event_store.reset_domain_event_semantics_counters()
    _append(monkeypatch, actor_type="user", actor_id=None, origin="user_message")
    c2 = event_store.domain_event_semantics_counters()
    assert c2["actor_total"] == 1 and c2["actor_missing"] == 0
    assert c2["actor_no_speaker"] == 1  # 有类型没 id，照样判不出说话人


def test_未登记的actor值计为不可归一(monkeypatch):
    fake = _append(monkeypatch, actor_type="robot", actor_id=1, origin="system_event")
    assert fake.rows[0].actor_type == "robot"
    c = event_store.domain_event_semantics_counters()
    assert c["actor_unnormalized"] == 1 and c["actor_diff"] == 0


def test_流水闸关时不计数也不开会话(monkeypatch):
    """domain_events_enabled 关＝整个写点不执行，计数也不涨（零开销口径与落库一致）。"""
    monkeypatch.setattr(event_store, "domain_events_enabled", lambda: False)
    monkeypatch.setattr(event_store, "async_session_factory",
                        lambda: (_ for _ in ()).throw(AssertionError("闸关不得开会话")))
    asyncio.run(event_store.append_domain_event(
        "chat.message_sent", "chat_session", 1, entity_id=1, actor_type="ai"))
    assert event_store.domain_event_semantics_counters()["append_total"] == 0


def test_判定抛异常不影响事件落库(monkeypatch):
    """观测面坏了必须 fail-open：事件照写、异常不外抛（事件旁路不得影响主链路）。"""
    def _boom(_v):
        raise RuntimeError("归一化面炸了")

    monkeypatch.setattr(event_store, "normalize_sender", _boom)
    fake = _append(monkeypatch, actor_type="ai", actor_id=9, origin="ai_message")
    assert fake.committed == 1 and len(fake.rows) == 1
    assert fake.rows[0].actor_type == "ai"


def test_计数快照是副本改不动内部():
    c = event_store.domain_event_semantics_counters()
    c["append_total"] = 9999
    assert event_store.domain_event_semantics_counters()["append_total"] == 0


# ═══════════════════════════ ⑥ 观测端点：只读、零写库 ═══════════════════════════

def test_计数端点只读零写库(monkeypatch):
    from app.api import scheduler as sched

    def _no_db(*a, **kw):
        raise AssertionError("计数端点不得开数据库会话")

    monkeypatch.setattr(event_store, "async_session_factory", _no_db)
    monkeypatch.setattr("app.db.database.async_session_factory", _no_db)
    _set_flag(monkeypatch, False)

    # 造两条样本读数（走真实判定路径，不手写计数）
    _append(monkeypatch, actor_type="ai", actor_id=9, origin="ai_message")
    _append(monkeypatch, actor_type="user", actor_id=9, origin="bogus_origin")
    _run_runtime_stage(
        monkeypatch, spec=_probe_spec("obs_probe", "OBS_PROBE"), action_type="OBS_PROBE",
        observation={"epistemic_status": "FACT", "provenance": "tool", "summary": "s"})

    out = asyncio.run(sched.get_semantics_stats(user_id=1))
    assert set(out) >= {"tool_injection", "domain_event", "flags"}
    ti, de = out["tool_injection"], out["domain_event"]
    assert ti["injection_total"] == 1 and ti["label_drop_rate"] == 1.0
    assert de["append_total"] == 2 and de["actor_diff"] == 1
    assert de["origin_invalid_rate"] == 0.5      # 2 条里 1 条非法
    assert de["actor_diff_rate"] == 0.5          # 分母＝带 actor_type 的 2 条（ai 归一后有差异，user 无差异）
    assert out["flags"][FLAG] is False


def test_端点空计数时比例为零不除零():
    from app.api import scheduler as sched
    out = asyncio.run(sched.get_semantics_stats(user_id=1))
    assert out["tool_injection"]["label_drop_rate"] == 0.0
    assert out["domain_event"]["origin_invalid_rate"] == 0.0
    assert out["domain_event"]["actor_diff_rate"] == 0.0


def test_store不再自带origin字面量且判定走schema单一来源():
    """origin 白名单只在 events/schema.py 一份；store 侧只做判定不改值（本步约束）。"""
    src = inspect.getsource(event_store.append_domain_event)
    assert "VALID_ORIGINS" not in src  # 写点不改值 ⇒ 不做折半替换
    assert "_note_event_semantics" in src
    from app.events import schema as es
    assert event_store.VALID_ORIGINS is es.VALID_ORIGINS  # 同一个集合对象，未复制第二份
