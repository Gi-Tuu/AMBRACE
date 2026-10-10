"""A28 第一阶段（认知语义基座）守卫：Observation 统一 ＋ Workspace 纯运行时 ＋ 统一 State Constructor。

立批缘由：外部分析（10-05）指出「每个系统各自一套认知」，第一阶段先建最小语义基座。
本文件钉的是**这一批的红线＝零行为变化**：新抽象只能「换承载方式」，不许换产物形态。

判据写法说明（为什么不写「看起来一样」）：
- S1 用**测试侧冻结的历史实现**做逐键对比（`_legacy_make_observation` 是 `git show HEAD^` 那份的照抄），
  这样任何一次「顺手把 3 键扩成 9 键」「把 text 兜底删掉」「把 120 改成别的默认」都会在CI 当场红。
- S3 用 **import 白名单**钉「Workspace 不落库、不调 LLM」，用 **context 文本逐字节相同**钉「只写不读」。
"""
import pytest

from app.agent.observation import Observation, clamp_summary, from_spec, summarize_result
from app.agent.tool_runner import _make_observation

# ── 历史实现照抄（A28-S1 之前，`app/agent/tool_runner.py:42-56`）──────────────────────────
# 这份**故意**与被测对象重复：它的作用是「钉住过去」，不是复用逻辑。
def _legacy_make_observation(spec, result, status):
    summary = ""
    if isinstance(result, dict):
        summary = str(result.get("summary") or result.get("message") or result.get("result") or result.get("text") or "")
    elif isinstance(result, str):
        summary = result
    max_chars = int(getattr(spec, "max_observation_chars", 120) or 120)
    return {
        "epistemic_status": getattr(spec, "epistemic_status", "FACT"),
        "provenance": getattr(spec, "provenance", "tool"),
        "summary": str(summary)[:max_chars],
    }


class _Spec:
    """最小 ToolSpec 替身：只暴露历史上真被 getattr 读过的四个属性。"""

    def __init__(self, **kw):
        self.name = kw.pop("name", "demo_tool")
        for k, v in kw.items():
            setattr(self, k, v)


_LONG = "结" * 5000


@pytest.mark.parametrize("spec,result", [
    (_Spec(), {"summary": "摘要", "message": "消息"}),          # summary 优先
    (_Spec(), {"message": "消息", "result": "结果"}),           # message 次之
    (_Spec(), {"result": "结果", "text": "文本"}),              # result 次之
    (_Spec(), {"text": "文本"}),                               # MCP 的 text 兜底
    (_Spec(), {"nothing": 1}),                                  # 全空
    (_Spec(), "纯字符串返回"),                                   # str 直接用
    (_Spec(), None),                                            # blocked/error
    (_Spec(), 123),                                             # 非 dict 非 str
    (_Spec(max_observation_chars=4000), {"text": _LONG}),        # MCP 长文
    (_Spec(max_observation_chars=0), {"text": _LONG}),           # 0 回落 120
    (_Spec(max_observation_chars=None), {"text": _LONG}),        # None 回落 120
    (_Spec(epistemic_status="INFERRED", provenance="mcp"), {}),  # 显式标注
    (_Spec(epistemic_status=None, provenance=None), {}),         # 属性存在但为 None：历史返回 None，不许"顺手兜底"
])
def test_make_observation_必须与历史三键产物逐字相同(spec, result):
    """统一语义**只许换承载、不许换产物**：主链路看到的仍是同一份三键 dict。"""
    got = _make_observation(spec, result, "ok")
    assert got == _legacy_make_observation(spec, result, "ok"), (
        f"{spec.__dict__} / {str(result)[:40]!r} ⇒ 观察产物变了（{got} ≠ 历史形态）"
    )


def test_make_observation_产物键集恒为三键_完整记录不许混进主链路():
    """把 9 键完整记录直接返回给 execute_tool 的消费者＝行为变化，这条是防它的闸。"""
    got = _make_observation(_Spec(), {"text": "x"}, "ok")
    assert set(got) == {"epistemic_status", "provenance", "summary"}, f"主链路观察 dict 多出键：{sorted(got)}"


def test_observation_完整记录带来源与归属_且不改核心三键():
    obs = from_spec(_Spec(name="web_search"), {"text": "命中 3 条"}, "ok",
                    source="tool", user_id=3, character_id=13, session_id=11)
    full = obs.to_dict()
    assert full["source"] == "tool" and full["status"] == "ok"
    assert full["tool_name"] == "web_search"
    assert (full["user_id"], full["character_id"], full["session_id"]) == (3, 13, 11)
    assert obs.to_core_dict() == _legacy_make_observation(_Spec(name="web_search"), {"text": "命中 3 条"}, "ok")
    assert set(obs.to_core_dict()) == {"epistemic_status", "provenance", "summary"}


def test_observation_缺归属就是None_不许编造默认账号():
    """与派单 F（A2-M0）同一条红线：缺 caller 时键存在=None，不许 `or 1` 臆造 1 号账号。"""
    full = from_spec(_Spec(), {}, "blocked").to_dict()
    assert full["user_id"] is None and full["character_id"] is None and full["session_id"] is None
    assert full["status"] == "blocked"


def test_summarize_与clamp_的边界口径():
    assert summarize_result({"summary": "", "message": "M"}) == "M"      # 空串走 or 链，不锁死在第一个键
    assert summarize_result({"summary": None, "text": "T"}) == "T"
    assert clamp_summary("abcdefgh", 4) == "abcd"
    assert clamp_summary("abc", None) == "abc"                            # None→120，短文本原样
    assert clamp_summary(_LONG, 4000) == _LONG[:4000]


def test_Observation_是纯dataclass_不碰数据库与LLM():
    """第一阶段红线：Observation 只承载语义。"""
    import inspect

    from app.agent import observation as ob

    src = inspect.getsource(ob)
    for banned in ("sqlalchemy", "async_session_factory", "llm_client", "chat_completion", "httpx"):
        assert banned not in src, f"observation.py 里出现了 {banned}——语义承载层不许长手脚"
    o = Observation(source="memory", status="ok", summary="s")
    assert o.extra == {} and o.to_dict()["source"] == "memory"
    assert Observation(source="m", status="ok", summary="s", extra={"a": 1}).to_dict()["extra"] == {"a": 1}


# ── S2：current_state 结构化读取 ＋ 文本 renderer（拆分前后逐字节同形）─────────────────────

import asyncio  # noqa: E402


def _legacy_anchor_text(char_facts, prof_loc, slot_facts, *, include_profile_location=True, max_chars=200):
    """拆分前 `current_user_state_anchor` 的函数体照抄（A28-S2 之前）。

    这份与生产代码**故意重复**：它的作用是钉住「过去的输出」，让「顺手调顺序/改去重/动截断」当场红。
    """
    parts: dict[str, str] = {}
    for p in ("location", "status", "mood", "activity"):
        if char_facts.get(p):
            parts[{"location": "位置", "status": "状态", "mood": "心情", "activity": "近况"}[p]] = char_facts[p]
    if include_profile_location:
        if prof_loc and "位置" not in parts:
            parts["位置"] = prof_loc
    for slot, val in slot_facts.items():
        label = {"location": "位置", "job": "工作", "relationship": "感情",
                 "living": "居住", "goal_state": "近期目标", "health": "健康"}.get(slot, slot)
        if val and label not in parts:
            parts[label] = val
    if not parts:
        return ""
    body = "；".join(f"{k}：{v}" for k, v in parts.items())
    if len(body) > max_chars:
        body = body[:max_chars].rstrip("，；,;") + "…"
    return f"\nTA 当前已知现状（以此为准，旧记忆不得与此矛盾）：{body}。\n"


@pytest.mark.parametrize("char_facts,prof,slots,inc,max_chars", [
    ({}, None, {}, True, 200),                                        # 三源全空
    ({"location": "湖光校区"}, "市区城市", {}, True, 200),                # 位置去重：per-char 优先
    ({"location": "湖光校区", "status": "在上班"}, None, {}, True, 200),      # 位置＋状态同现＝顺序敏感（变异自测靠这条）
    ({"status": "在上班", "mood": "还行", "activity": "在练背"}, None, {}, True, 200),
    ({"location": "湖光校区", "status": "在上班", "mood": "还行", "activity": "在练背"},
     "市区城市", {"location": "别处", "job": "学生"}, True, 200),          # 三源全同现：顺序＋去重一起验
    ({"status": "在上班"}, "市区城市", {}, True, 200),                    # 位置来自 profile，排在状态之后
    ({"location": "湖光校区"}, "市区城市", {}, False, 200),               # 关 profile 源
    ({}, "市区城市", {}, False, 200),                                   # 关源且 per-char 无位置：这条唯一能证明开关真生效
    ({"location": "甲"}, None, {}, True, 4),                            # body 长度恰好＝max_chars：截断等号边界
    ({"activity": "在练背"}, None, {"location": "别处", "relationship": "单身"}, True, 200),  # 槽位冲突：位置不覆盖
    ({}, None, {"job": "学生", "health": "腰伤"}, True, 200),           # 只有共享槽
    ({"location": "X" * 180}, None, {"goal_state": "长" * 60}, True, 200),   # 触发截断
    ({"location": "甲"}, None, {"job": "乙"}, True, 5),                 # 短窗口截断＋rstrip 尾巴标点
    ({"location": "地点，"}, None, {"job": "工作；"}, True, 200),           # 值自带标点也走同一渲染
])
def test_锚点文本在拆分前后必须逐字节相同(monkeypatch, char_facts, prof, slots, inc, max_chars):
    from app.memory import current_state as cs

    async def _facts(*a, **kw):
        return dict(char_facts)

    async def _prof(*a, **kw):
        return prof

    async def _slots(*a, **kw):
        return dict(slots)

    monkeypatch.setattr(cs, "_char_world_user_facts", _facts)
    monkeypatch.setattr(cs, "_profile_location", _prof)
    monkeypatch.setattr(cs, "_global_slot_facts", _slots)

    got = asyncio.run(cs.current_user_state_anchor(character_id=1, user_id=1,
                                                   include_profile_location=inc, max_chars=max_chars))
    want = _legacy_anchor_text(char_facts, prof, slots, include_profile_location=inc, max_chars=max_chars)
    assert got == want, f"锚点文本变了：\n  现={got!r}\n  旧={want!r}"


def test_结构化读取给出来源与优先级_不虚构没有的字段(monkeypatch):
    from app.memory import current_state as cs

    async def _facts(*a, **kw):
        return {"location": "湖光校区", "mood": "有点累"}

    async def _prof(*a, **kw):
        return "不该出现的城市"          # 位置已被 per-char 占住 ⇒ 去重丢弃

    async def _slots(*a, **kw):
        return {"relationship": "单身", "health": ""}   # 空值不产出条目

    monkeypatch.setattr(cs, "_char_world_user_facts", _facts)
    monkeypatch.setattr(cs, "_profile_location", _prof)
    monkeypatch.setattr(cs, "_global_slot_facts", _slots)

    st = asyncio.run(cs.get_current_user_state(character_id=1, user_id=1))
    assert st["empty"] is False
    assert [e["label"] for e in st["entries"]] == ["位置", "心情", "感情"], st["entries"]
    assert [e["source"] for e in st["entries"]] == ["world_fact", "world_fact", "global_slot"]
    assert st["entries"][0] == {"key": "location", "label": "位置", "value": "湖光校区", "source": "world_fact"}
    assert set(st["entries"][0]) == {"key", "label", "value", "source"}, "不许凭空多出没依据的字段（如 fresh_until）"


def test_三源任一抛错时现状读取退化为空_锚点仍是空串(monkeypatch):
    """失败静默是既有语义：主链路绝不因取现状而断。"""
    from app.memory import current_state as cs

    async def _boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(cs, "_char_world_user_facts", _boom)
    st = asyncio.run(cs.get_current_user_state(character_id=1, user_id=1))
    assert st == {"entries": [], "empty": True}
    assert asyncio.run(cs.current_user_state_anchor(character_id=1, user_id=1)) == ""


def test_renderer_是纯函数_不查库(monkeypatch):
    """渲染层不许有手脚：只吃 entries，输出文本。"""
    import inspect

    from app.memory import current_state as cs

    assert asyncio.run  # 占位，确保上面用例已跑过异步路径
    src = inspect.getsource(cs.render_current_state_anchor)
    for banned in ("await", "async_session_factory", "select("):
        assert banned not in src, f"renderer 里出现了 {banned}：渲染层不该再取数"
    assert cs.render_current_state_anchor([]) == ""
    assert cs.render_current_state_anchor([{"label": "位置", "value": "家"}]) == (
        "\nTA 当前已知现状（以此为准，旧记忆不得与此矛盾）：位置：家。\n")


# ── S3：Cognitive Workspace＝纯运行时对象，且第一阶段「只写不读」─────────────────────────

import ast  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from app.agent.state import AgentState  # noqa: E402
from app.agent.workspace import (  # noqa: E402
    MAX_OBSERVATIONS,
    CognitiveWorkspace,
    add_decision,
    add_observation,
    create_workspace,
    set_focus,
)


def test_workspace_的import白名单只有标准库():
    """红线①：不落库、不查库、不调 LLM——最硬的钉法是「除了标准库什么都不许 import」。"""
    from app.agent import workspace as ws_mod

    tree = ast.parse(Path(ws_mod.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "dataclasses", "typing"}, (
        f"workspace.py 引进了外部依赖：{sorted(imported - {'__future__', 'dataclasses', 'typing'})}"
        "——工作台只许承载认知材料，取数/生成都不归它")


def test_workspace_源码里不许出现取数与生成入口():
    import inspect

    from app.agent import workspace as ws_mod

    src = inspect.getsource(ws_mod)
    for banned in ("async_session_factory", "sqlalchemy", "llm_client", "chat_completion",
                   "search_memories", "AGENT_FLAGS", "get_current_user_state"):
        assert banned not in src, f"workspace.py 出现 {banned}：它不许伸手去拿数据"


def test_AgentState_必须声明工作台三键_否则LangGraph静默丢弃():
    """真流式那次事故的教训（SSE 打字机失效）：TypedDict 没声明的 key 会被 LangGraph 1.x 丢掉。"""
    for key in ("workspace", "observations", "decision"):
        assert key in AgentState.__annotations__, f"AgentState 缺 {key} 声明 ⇒ 图里传不过去"


def test_runtime初始状态自带工作台_且缺caller时归属为None不臆造():
    from app.agent import runtime as rt

    st = rt._build_initial_state(character_id=13, user_id=None, session_id=None,
                                 user_message="hi", lang="zh", reasoning_level=0, save_memory=True)
    ws = st["workspace"]
    assert isinstance(ws, CognitiveWorkspace)
    assert (ws.character_id, ws.user_id, ws.session_id) == (13, None, None)
    assert st["observations"] == [] and st["decision"] is None
    assert st["user_id"] is None, "派单 F 的 fail-closed 语义不许被顺手动掉"


def test_观测溢出丢最旧_工作台是此刻的窗口不是日志():
    ws = create_workspace(character_id=1, user_id=1)
    for i in range(MAX_OBSERVATIONS + 5):
        add_observation(ws, {"source": "tool", "summary": f"o{i}"})
    assert len(ws.observations) == MAX_OBSERVATIONS
    assert ws.observations[0]["summary"] == "o5" and ws.observations[-1]["summary"] == f"o{MAX_OBSERVATIONS + 4}"


def test_record_decision_补reason占位_缺省置信度不写():
    ws = create_workspace()
    add_decision(ws, {"kind": "reflection", "result": None})
    assert ws.last_decision == {"kind": "reflection", "result": None, "reason": None}
    assert ws.confidence is None
    add_decision(ws, {"kind": "x"}, confidence=0.6)
    assert ws.confidence == 0.6


@pytest.mark.parametrize("helper,payload", [
    (add_observation, {"source": "tool", "summary": "s"}),
    (add_decision, {"kind": "reflection"}),
    (set_focus, "健身"),
])
def test_写入入口对None静默_旧路径没有工作台也不炸(helper, payload):
    """第一阶段主聊天链还没有工作台（S4 才统一），任何写入都必须对 None 免疫。"""
    helper(None, payload)          # 不抛即通过
    helper(None, None)             # 连载荷都缺也不许炸


def test_to_dict_是快照_改返回值不污染工作台():
    ws = create_workspace(character_id=1)
    add_observation(ws, {"source": "memory", "summary": "他腰有伤"})
    snap = ws.to_dict()
    snap["observations"][0]["summary"] = "被改过"
    snap["character_id"] = 999
    assert ws.observations[0]["summary"] == "他腰有伤"
    assert ws.character_id == 1


def test_上下文里只有注入档那一处消费workspace():
    """「只写不读」的第一阶段红线，10-10 由 ②c 前置正式改为**受闸的单点读**。

    这条守卫的原文说过：真要投影时它会红，那是故意的——届时必须连提示词回归一起评审，而不是顺手加一段。
    本次评审＝交接文档 §四-2（A42 ②c 前置，默认关＋独立配额）。所以这里不再禁止"出现 workspace 字样"，
    而是把**谁能读**钉成一份显式白名单，并核"读必须在这把闸后面"：
      · `section_projection.py`＝唯一取数点，builder 首行必须短路在 `inject_flag_on()` 之后；
      · `assembly.py`＝只有 append 落位（消费注册表算好的 `_sv`，自己不碰 ws 对象）；
      · 其它任何装配文件（含 `context_builder.py` 与其余 `section_*.py`）出现 workspace ⇒ 当场红。
    默认关时产出逐字节不变由 `test_projection_inject_budget_a42c.py` 的主断言钉（本条只证"没有第二处"）。
    """
    from app.agent import context_builder as cb

    gated = {"section_projection.py", "assembly.py"}
    targets = [Path(cb.__file__)]
    ctx_dir = Path(cb.__file__).parent / "context"
    if ctx_dir.is_dir():
        targets += sorted(ctx_dir.glob("*.py"))
    hits = {p.name: "workspace" in p.read_text(encoding="utf-8") for p in targets}
    reading = sorted(n for n, v in hits.items() if v)
    assert set(reading) <= gated, f"出现了白名单外的 workspace 读取点：{reading}"

    # 反向钉（缺了它，"删掉闸门把这一档常开"这条变异就不会红）：
    # ① 读点必须显式在注入闸后面；② 装配主文件不许自己拿 ws 对象渲染。
    sec = (ctx_dir / "section_projection.py").read_text(encoding="utf-8")
    body = sec[sec.index("async def workspace_projection_section"):sec.index("register_section(")]
    assert "inject_flag_on()" in body, "section_projection 的读点没在闸门后面＝常开"
    assert body.index("inject_flag_on()") < body.index("render_projection_block"), \
        "先渲染后查闸＝闸关着也照样取数"
    asm = (ctx_dir / "assembly.py").read_text(encoding="utf-8")
    assert "render_projection_block" not in asm and "state.get(\"workspace\")" not in asm, \
        "assembly 里出现了第二个渲染/取点＝读取面又散了，白名单失效"


# ── S4：chat / social / continue 三条路统一走一个构造器，且**逐键取值不变**────────────────

# 允许「这条路本来没这个键」而统一后新出现的键。判据：新增键的值必须是中性假值
# （None/False/""/[]/{}）——因为 .get(k) 与 .get(k, 默认) 在键缺失时读到的就是这类值。
# 已核实全仓只有一处对这些键用了非空默认（section_overlay 的 group_shared_fact, False ⇒ 仍是 False）。
_ALLOWED_NEW_KEYS = {
    "continue_payload", "group_id", "group_shared_fact", "channel_hint", "source_id",
    "stream_sink", "tts", "voice_params", "tts_subdir", "block_sink",
    "character_states_snapshot", "skip_memory_save",
}


def _assert_no_drift(got: dict, legacy: dict, path: str) -> None:
    """既有键：值逐键相同；新增键：必须在中性默认上，且不许冒出名单外的键。"""
    for k, v in legacy.items():
        assert k in got, f"{path}：既有键 {k} 被构造器弄丢了（下游按 .get 读它会静默退化）"
        assert got[k] == v, f"{path}：既有键 {k} 的值变了 ⇒ {got[k]!r} ≠ {v!r}"
    extra = set(got) - set(legacy) - {"workspace", "observations", "decision"}
    assert extra <= _ALLOWED_NEW_KEYS, f"{path}：冒出名单外的新键 {sorted(extra)}"
    for k in extra:
        assert not got[k] or k == "skip_memory_save" and got[k] is True, (
            f"{path}：新增键 {k} 的值 {got[k]!r} 不是中性默认 ⇒ 这条路的行为会变")


def test_主聊天路径的state与统一前的字面量逐键相同():
    from app.agent import runtime as rt

    legacy = {   # A28-S4 之前 chat_service.py:167 的字面量（照抄，含当时算好的值）
        "user_message": "腰还疼吗", "character_id": 13, "user_id": 3, "session_id": 11, "intent": "",
        "retrieved_memories": [], "context_messages": [], "character_info": {}, "ai_response": "",
        "should_update_memory": False, "new_memories": [], "emotional_state": "",
        "bio_update": None, "status_update": None, "source_id": 9001, "lang": "zh",
        "reasoning_level": 2, "tools_used": [],
        "stream_sink": None, "tts": True, "voice_params": {"voice_id": "Ethan"},
        "tts_subdir": "t/202610", "block_sink": None,
        "character_states_snapshot": {"mood": 60}, "channel_hint": "wechat_ilink",
    }
    got = rt._build_initial_state(
        character_id=13, user_id=3, session_id=11, user_message="腰还疼吗", lang="zh",
        reasoning_level=2, save_memory=True, source_id=9001, channel_hint="wechat_ilink",
        stream_sink=None, tts=True, voice_params={"voice_id": "Ethan"}, tts_subdir="t/202610",
        block_sink=None, character_states_snapshot={"mood": 60},
    )
    _assert_no_drift(got, legacy, "主聊天")


def test_继续指令路径的state与统一前的字面量逐键相同():
    from app.agent import runtime as rt

    legacy = {   # A28-S4 之前 continue_chat() 的字面量（第三套 state，原文点名必须并进来）
        "user_message": "（用户没有说话，等你继续）",
        "continue_payload": {"last_ai_content": "上一条回复"},
        "character_id": 13, "user_id": 3, "session_id": 11, "intent": "",
        "retrieved_memories": [], "context_messages": [], "character_info": {}, "ai_response": "",
        "should_update_memory": False, "new_memories": [], "emotional_state": "",
        "bio_update": None, "status_update": None, "source_id": None, "lang": "zh",
        "reasoning_level": 1, "tools_used": [],
    }
    got = rt._build_initial_state(
        character_id=13, user_id=3, session_id=11,
        user_message="（用户没有说话，等你继续）",
        continue_payload={"last_ai_content": "上一条回复"},
        lang="zh", reasoning_level=1, save_memory=True,
    )
    _assert_no_drift(got, legacy, "继续指令")


def test_社交短回复路径的state与统一前的字面量逐键相同():
    from app.agent import runtime as rt

    legacy = {   # A28-S4 之前本文件 _build_initial_state 的字面量（群聊/渠道主动链）
        "user_message": "在干嘛", "character_id": 14, "user_id": None, "session_id": None,
        "intent": "", "retrieved_memories": [], "context_messages": [], "character_info": {},
        "ai_response": "", "should_update_memory": False, "new_memories": [],
        "emotional_state": "", "bio_update": None, "status_update": None, "source_id": None,
        "lang": "en", "reasoning_level": 0, "tools_used": [],
        "group_id": 7, "group_shared_fact": True, "skip_memory_save": True,
    }
    got = rt._build_initial_state(
        character_id=14, user_id=None, session_id=None, user_message="在干嘛", lang="en",
        reasoning_level=0, save_memory=False, group_id=7, group_shared_fact=True,
    )
    _assert_no_drift(got, legacy, "社交短回复")
    assert got["skip_memory_save"] is True, "不落记忆这条既有语义（Phase E）不许被统一过程弄丢"


def test_真走一遍检索节点_观测确实落进工作台而不是死代码(monkeypatch):
    """上面全是「形状」判据；这条证明这条路**真的在写**：走真 `nodes.retrieve_memories`。

    只替掉检索出口（`app.memory.search_memories`，节点内是 call-time 取包属性 ⇒ 打桩有效）。
    """
    import app.memory as memory_pkg

    from app.agent import nodes
    from app.agent.workspace import create_workspace

    async def _fake_search(**kw):
        return [{"id": 5, "content": "他腰有伤，别久站", "epistemic_status": "OBSERVED"}]

    monkeypatch.setattr(memory_pkg, "search_memories", _fake_search)

    ws = create_workspace(character_id=13, user_id=3, session_id=11)
    state = {
        "user_message": "我腰有点酸", "character_id": 13, "user_id": 3, "session_id": 11,
        "workspace": ws, "observations": [], "decision": None,
        "retrieved_memories": [], "context_messages": [], "perception": None,
        "task_id": None,
    }
    out = asyncio.run(nodes.retrieve_memories(state))
    assert out["retrieved_memories"] == [{"id": 5, "content": "他腰有伤，别久站", "epistemic_status": "OBSERVED"}]
    assert out["workspace"] is ws, "工作台必须是同一个对象（节点原地写，LangGraph 传的也是它）"
    assert len(ws.observations) == 1, f"召回没落进工作台（观测={ws.observations}）"
    o = ws.observations[0]
    assert o["source"] == "memory" and o["status"] == "retrieved"
    assert o["provenance"] is None, (
        "记忆召回不是「工具面」产出的观测：provenance 必须留 None。"
        "给它编一个新取值会撞 `test_obs_semantics_wire.py` 的「散落来源字面量只减不增」棘轮")
    assert o["summary"] == "他腰有伤，别久站" and o["epistemic_status"] == "OBSERVED"
    assert o["extra"] == {"memory_id": 5}
    assert (o["user_id"], o["character_id"], o["session_id"]) == (3, 13, 11)


def test_观测来源类别走常量_不留散落字面量():
    """`Observation(source=…)` 的取值必须是 observation.py 里的常量。

    只钉 Observation 构造点：`source=` 这个参数名在别处是**另一套词表**（如记忆写入来源
    `save_memory(..., source="chat")`），不归本批管，扫进来只会造成误红。
    """
    from app.agent import observation as ob

    assert (ob.SOURCE_TOOL, ob.SOURCE_MEMORY, ob.SOURCE_REFLECTION) == ("tool", "memory", "reflection")

    root = Path(ob.__file__).resolve().parent.parent          # backend/app
    bare = re.compile(r'Observation\(\s*\n?\s*source\s*=\s*f?["\']')
    offenders = [str(p.relative_to(root)) for p in sorted(root.rglob("*.py"))
                 if bare.search(p.read_text(encoding="utf-8", errors="replace"))]
    assert not offenders, f"这些文件用裸字面量构造 Observation 的 source：{offenders}（请改用 SOURCE_* 常量）"


def test_没有工作台时节点照旧跑_不因为新键缺失而炸(monkeypatch):
    """主聊天图之外还有直调节点的旧路径（状态里没有 workspace）——写入必须完全隐形。"""
    import app.memory as memory_pkg

    from app.agent import nodes

    async def _fake_search(**kw):
        return [{"id": 9, "content": "明天要面试"}]

    monkeypatch.setattr(memory_pkg, "search_memories", _fake_search)
    state = {"user_message": "hi", "character_id": 13, "user_id": None, "session_id": None,
             "retrieved_memories": [], "context_messages": [], "perception": None, "task_id": None}
    out = asyncio.run(nodes.retrieve_memories(state))
    assert out["retrieved_memories"] == [{"id": 9, "content": "明天要面试"}]
    assert "workspace" not in out, "旧路径不许被顺手塞进新键（零行为变化）"


# ── 普查棘轮（A28 ⑦ 的产出）：编译图里写的 state 键必须先在 AgentState 声明 ────────────────
#
# 为什么钉这条：LangGraph 1.x 只保留 TypedDict 声明过的键，未声明的**在节点返回后被静默丢弃**
# ——本仓已栽两次（SSE 打字机、A28-S4 照出的 group_id）。这条不是理论：2026-10-05 实测最小图
# （`declared` 键穿过 ainvoke 存活、`undeclared` 键消失）确认了该行为。
# 图内文件清单＝编译图真会执行的那批（节点本体＋节点调用的装配/解析/反思层）。
_GRAPH_SURFACE = (
    "agent/nodes.py",
    "agent/response_parser.py",
    "agent/context_builder.py",
    "agent/reflection.py",
)

# 历史遗漏挂账名单（普查当日为 2 处）。**修好一个就删一个名字**——下面的"名单不许腐烂"断言会盯着。
# 10-05 A29-a/b：`marker_truncated` 与 `_host_user_msg_index` 已补进 AgentState 声明，
# 且各配了一条"能穿过图边界"的守卫（`tests/test_state_key_boundary.py`）⇒ 名单清空。
_KNOWN_UNDECLARED: set[str] = set()


def _undeclared_state_writes() -> dict[str, list[str]]:
    from app.agent import state as state_mod

    declared = set(state_mod.AgentState.__annotations__.keys())
    app_dir = Path(state_mod.__file__).resolve().parent.parent        # backend/app
    found: dict[str, list[str]] = {}
    for rel in _GRAPH_SURFACE + tuple(
            f"agent/context/{p.name}" for p in sorted((app_dir / "agent" / "context").glob("*.py"))):
        py = app_dir / rel
        if not py.is_file():
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name) \
                        and tgt.value.id in ("state", "final_state") \
                        and isinstance(tgt.slice, ast.Constant) and isinstance(tgt.slice.value, str):
                    key = tgt.slice.value
                    if key not in declared:
                        found.setdefault(key, []).append(f"{rel}:{node.lineno}")
    return found


def test_图内写入的state键必须声明_历史两处遗漏挂账在A29():
    """新增未声明键＝当场红；已知两处必须逐名挂账，且**修好就从名单删**（防名单腐烂成摆设）。"""
    found = _undeclared_state_writes()
    fresh = {k: v for k, v in found.items() if k not in _KNOWN_UNDECLARED}
    assert not fresh, (
        f"图内文件往 state 写了未声明的键 {fresh} ⇒ LangGraph 会在节点返回后静默丢弃，"
        "下游永远读不到（同 SSE 打字机失效那条坑）。要么在 AgentState 里声明，"
        "要么改成不跨节点传递；别让它静默生效不了。")
    stale = _KNOWN_UNDECLARED - set(found)
    assert not stale, f"挂账的键已经不在了 {sorted(stale)}：请从 _KNOWN_UNDECLARED 删除名单项（A29 已修）"
