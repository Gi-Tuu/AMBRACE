# -*- coding: utf-8 -*-
"""A50 批17（2026-10-13）守卫：``_run_recall_second_hop`` / ``_run_search_stage`` / ``_run_mcp_stage``。

背景（A50 批15 只读地图 → 批16 用户轮信号 → 批17 受控工具三阶段）：``chat_service._run_agent_core``
在 ``agent.ainvoke`` 之后连着三段「受控工具」——① 记忆二跳（仅非流式，带 ``[RECALL]`` 才查）、
② AI 自主搜索受控 Loop（``search_loop`` 真档）或仅剥标记兜底（关档）、③ MCP 工具执行＋再决策
（仅非流式，有 ``mcp.*`` 标记才跑）。批17 把三段各整段机械抽成同文件私有协程
（块内语句逐字未变，唯一改写＝入参运输与返回值运输），本文件钉住「搬完之后行为一模一样、
守卫位置一模一样、计数口径一模一样」。

本文件自己的三条纪律（照 A50 批16 的教训）：
1. **真 helper + 真 core**：一律驱动真的三个 helper 与真的 ``_run_agent_core``，只桩外部依赖
   （DB 会话工厂 / 模型 / trace / 后台任务 / 三个受控环本身）。把 helper 整个打桩再数调用次数
   ＝冒充内部行为，``test_core调的是真helper不是桩`` 反过来钉死它。
2. **不抄真实用户数据**：会话/用户/角色 id 用 7001/7002/13 等合成值，正文是通用短语。
3. **顺序即语义 + 计数即契约**：C1→C2→C3→trace 的先后、非流式守卫（流式档三段都不跑）、
   ``llm_calls`` 只计成功轮、MCP 再决策不单独计数——逐条单独钉；撤掉任一真调用、换 ``initiator``、
   把成功轮也计进 ``llm_calls``，本文件必须真红。

依赖桩的落点原则：三段里那些 ``from X import y`` 是**函数内 import**，打桩落在**被导入的那个
模块的属性**上；``async_session_factory`` / ``spawn_background`` / ``_logger`` / ``_extract_search`` /
``_run_web_search`` 等是 chat_service 的裸全局晚绑定，就打桩 chat_service 侧属性（搬家没有传绑定副本，
既有打桩面零迁移）。
"""
import asyncio
import inspect

from app.application import chat_service

SESSION_ID, USER_ID, CHARACTER_ID = 7001, 7002, 13
_OK_TEXT = "在的，你说。"
_MCP_TEXT = "查到了 [mcp.weather]{}[/mcp.weather] 今天晴"
_REGEN_TEXT = "查完了 [mcp.weather]今天晴[/mcp.weather]"


class _Log:
    """事件账本：名字 + 关键字参数；顺序即断言对象。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def add(self, name, **kw):
        self.events.append((name, kw))

    def names(self):
        return [n for n, _ in self.events]

    def kw(self, name):
        got = [kw for n, kw in self.events if n == name]
        assert got, "没有事件 %s（实际：%s）" % (name, self.names())
        return got[0]

    def all_kw(self, name):
        return [kw for n, kw in self.events if n == name]

    def count(self, name):
        return sum(1 for n, _ in self.events if n == name)

    def index(self, name):
        return self.names().index(name)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    """会话工厂产物：记录 enter/exit/execute/commit/add，供 gate 与浏览记录落库断言。

    ``execute`` 按次分档：第一次是 C1 的 gate 查角色开关（回布尔），之后是 C2 的浏览记录查重
    （无记录回 None ⇒ 走新增分支）——真实库里两者本来就在不同表上。
    """

    def __init__(self, log, gate_value=True, existing_row=None):
        self._log = log
        self._gate_value = gate_value
        self._existing = existing_row
        self._n = 0

    async def __aenter__(self):
        self._log.add("db.enter")
        return self

    async def __aexit__(self, *_exc):
        self._log.add("db.exit")
        return False

    async def execute(self, *_a, **_k):
        self._n += 1
        self._log.add("db.execute", n=self._n)
        if self._existing is not None:
            return _Result(self._existing)
        return _Result(self._gate_value if self._n == 1 else None)

    async def commit(self):
        self._log.add("db.commit")

    def add(self, obj):
        self._log.add("db.add", obj=obj)


class _FakeAgent:
    def __init__(self, log, text=_OK_TEXT, streamed=False):
        self._log = log
        self._text = text
        self._streamed = streamed

    async def ainvoke(self, initial_state):
        self._log.add("agent.ainvoke")
        return {"ai_response": self._text, "reasoning": None,
                "streamed": self._streamed, "character_name": "FixtureCharacter", "user_name": "FixtureUser"}


def _install_common(monkeypatch, log, *, text=_OK_TEXT, streamed=False, gate_value=True,
                    existing_row=None):
    """与三段无关的桩：冷战闸/挡位/情感/便签落库/后台任务/模型/状态构造/trace/会话工厂。"""
    import app.agent.runtime as runtime_mod
    import app.agent.trace as trace_mod

    async def _cold_war(*_a, **_k):
        return False

    async def _level(*_a, **_k):
        return 0

    async def _emo(*_a, **_k):
        return ""

    async def _notes(*_a, **_k):
        return None

    monkeypatch.setattr(chat_service, "_cold_war_block", _cold_war)
    monkeypatch.setattr(chat_service, "_load_reasoning_level", _level)
    monkeypatch.setattr(chat_service, "_resolve_emotional_state", _emo)
    monkeypatch.setattr(chat_service, "_save_phone_desktop_notes", _notes)

    def _spawn(coro, **_kw):
        log.add("spawn")
        if hasattr(coro, "close"):            # 关掉未 await 的协程，避免 never-awaited 告警
            coro.close()
        return None

    monkeypatch.setattr(chat_service, "spawn_background", _spawn)
    monkeypatch.setattr(chat_service, "agent", _FakeAgent(log, text, streamed))

    def _mk_state(**kw):
        st = {"ai_response": "", "streamed": streamed}
        st.update(kw)
        return st

    monkeypatch.setattr(runtime_mod, "_build_initial_state", _mk_state)
    monkeypatch.setattr(trace_mod, "new_task_id", lambda: "a50b17-task")
    monkeypatch.setattr(trace_mod, "enqueue_task_log", lambda **kw: log.add("trace", **kw))

    db = _FakeDB(log, gate_value=gate_value, existing_row=existing_row)

    def _factory():
        log.add("db.factory")
        return db

    monkeypatch.setattr(chat_service, "async_session_factory", _factory)
    return db


def _install_recall(monkeypatch, log, *, steps=None, boom=None, flag_on=True, tz=480):
    """C1 记忆二跳：AGENT_FLAGS 现取 + 时区偏移取法 + run_recall_loop + 失败降级剥标记。"""
    import app.agent.loop as loop_mod
    import app.agent.actions as actions_mod
    import app.utils.usertz as tz_mod

    async def _tz(user_id):
        log.add("tz", user_id=user_id)
        return tz

    async def _recall(final_state, **kw):
        # 真跑 gate 闭包（它在 helper 内部定义，只有被 drive 到才谈得上「门控查了库」）
        gate_value = None
        if kw.get("gate") is not None:
            gate_value = await kw["gate"]()
        rest = {k: v for k, v in kw.items() if k != "gate"}
        log.add("run_recall_loop", state=final_state, gate_value=gate_value, **rest)
        if boom:
            raise boom
        return final_state, list(steps or [])

    def _extract_recall(text):
        log.add("extract_recall", text=text)
        return (text.replace("[RECALL]", "").strip(), "问题")

    monkeypatch.setitem(loop_mod.AGENT_FLAGS, "memory_recall_second_hop", flag_on)
    monkeypatch.setattr(tz_mod, "get_user_tz_offset_min", _tz)
    monkeypatch.setattr(loop_mod, "run_recall_loop", _recall)
    monkeypatch.setattr(actions_mod, "extract_recall", _extract_recall)


def _install_search(monkeypatch, log, *, steps=None, boom=None, extract_s=True):
    """C2 搜索环：run_search_loop 四个注入依赖＋save_history（真闭包要驱动）；剥标记两个实现。"""
    import app.agent.loop as loop_mod

    async def _search_loop(final_state, **kw):
        log.add("run_search_loop", state=final_state,
                user_id=kw.get("user_id"), character_id=kw.get("character_id"),
                initiator=kw.get("initiator"),
                deps=(kw.get("run_search"), kw.get("throttle"), kw.get("inject_enabled"),
                      kw.get("save_history")))
        if kw.get("save_history") is not None:
            await kw["save_history"](CHARACTER_ID, "今天天气")
        if boom:
            raise boom
        return final_state, list(steps or [])

    def _extract_search(text):
        log.add("extract_search", text=text)
        return (text.replace("[SEARCH:今天天气]今天晴[/SEARCH:今天天气]", "").strip()
                if extract_s else text, None)

    monkeypatch.setattr(loop_mod, "run_search_loop", _search_loop)
    monkeypatch.setattr(chat_service, "_extract_search", _extract_search)
    monkeypatch.setattr(chat_service, "_run_web_search", lambda *a, **k: log.add("web_search"))
    monkeypatch.setattr(chat_service, "_search_throttle", lambda *a, **k: True)
    monkeypatch.setattr(chat_service, "_search_inject_enabled", lambda *a, **k: True)


def _install_mcp(monkeypatch, log, *, hit=None, boom=None):
    """C3 MCP：run_mcp_tool_stage（真值才再决策）＋ nodes.generate_response 再决策桩。"""
    import app.agent.mcp_tools as mcp_mod
    import app.agent.nodes as nodes_mod
    import app.agent.actions as actions_mod

    async def _stage(final_state, loop_steps, **kw):
        log.add("run_mcp_tool_stage", state=final_state, steps=list(loop_steps or []), **kw)
        if boom:
            raise boom
        return bool(hit)

    async def _regen(final_state):
        log.add("generate_response", state=final_state)
        out = dict(final_state)
        out["ai_response"] = _REGEN_TEXT
        return out

    def _strip_actions(text):
        log.add("strip_actions", text=text)
        return text.replace("[SEARCH:x]x[/SEARCH:x]", "").strip()

    monkeypatch.setattr(mcp_mod, "run_mcp_tool_stage", _stage)
    monkeypatch.setattr(nodes_mod, "generate_response", _regen)
    monkeypatch.setattr(actions_mod, "strip_actions", _strip_actions)


def _install_tail(monkeypatch, log):
    """core 后段（备注/定时/清洗/降级）用真实现即可，这里只把 AI 侧定时标记关掉免得干扰。"""
    import app.scheduling.promise_parser as parser_mod

    monkeypatch.setattr(parser_mod, "extract_ai_timer", lambda *_a, **_k: None)


def _install_logger(monkeypatch, log):
    class _Rec:
        def warning(self, msg, *a):
            log.add("warn", msg=str(msg) % a if a else str(msg))

        def info(self, msg, *a):
            log.add("info", msg=str(msg) % a if a else str(msg))

    monkeypatch.setattr(chat_service, "_logger", _Rec())


def _install_all(monkeypatch, log, **kw):
    _install_common(monkeypatch, log, **kw.pop("common", {}))
    _install_recall(monkeypatch, log, **kw.pop("recall", {}))
    _install_search(monkeypatch, log, **kw.pop("search", {}))
    _install_mcp(monkeypatch, log, **kw.pop("mcp", {}))
    _install_tail(monkeypatch, log)
    _install_logger(monkeypatch, log)
    return log


def _core(log, *, content="回来啦", **kw):
    return asyncio.run(chat_service._run_agent_core(
        SESSION_ID, USER_ID, CHARACTER_ID, content, "zh", 8801, **kw))


_STEP_OK = {"ok": True, "action_type": "search", "query": "今天天气", "answer": "晴"}
_STEP_BAD = {"ok": False, "action_type": "search", "query": "今天天气", "answer": None}


# ────────────────── 1 接线位：三段在主干里的顺序与真 helper ──────────────────

def test_三段按二跳搜索mcp的顺序排在生成之后trace之前(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True) is not None
    order = log.names()
    assert order.index("run_recall_loop") < order.index("run_search_loop") \
        < order.index("trace"), "三段顺序错了：%s" % order


def test_流式档三段里两个受控环都不进只剥标记(monkeypatch):
    """`_is_stream` 守卫：流式输出已定型，既有结论是二跳与 MCP 都不跑（不许顺手接进流式）。"""
    log = _install_all(monkeypatch, _Log(), common={"streamed": True}, search={"steps": [_STEP_OK]})
    assert _core(log) is not None
    assert log.count("run_recall_loop") == 0, "流式档不该进记忆二跳：%s" % log.names()
    assert log.count("run_mcp_tool_stage") == 0, "流式档不该进 MCP 工具：%s" % log.names()
    assert log.count("extract_search") == 1, "流式档只该剥 SEARCH 标记：%s" % log.names()
    assert log.count("extract_recall") == 1, "流式档只该剥 RECALL 标记：%s" % log.names()


def test_core调的是真helper不是桩(monkeypatch):
    """反「全桩 helper 数调用次数冒充内部行为」：core 的全局解析点必须就是模块里那三个真协程。"""
    _install_all(monkeypatch, _Log())      # 真装一遍桩：守卫要断言的是真模块面，不是一次性快照
    g = chat_service._run_agent_core.__globals__
    for name in ("_run_recall_second_hop", "_run_search_stage", "_run_mcp_stage"):
        helper = getattr(chat_service, name)
        assert helper.__module__ == "app.application.chat_service"
        assert inspect.iscoroutinefunction(helper)
        assert g[name] is helper, "%s 不是经模块全局晚绑定调用的（改成别的对象/传副本即判红）" % name
        kinds = [p.kind for p in inspect.signature(helper).parameters.values()]
        assert set(kinds) == {inspect.Parameter.KEYWORD_ONLY}, (
            "%s 的入参必须全是 keyword-only（防漏传/串位）：%s" % (name, kinds))


# ────────────────── 2 C1 记忆二跳 ──────────────────

def test_二跳真跑且门控查的是角色的记忆v2开关(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"gate_value": True},
                       recall={"steps": [_STEP_OK]})
    assert _core(log) is not None
    assert log.count("db.execute") >= 1, "gate 必须真查一次角色开关：%s" % log.names()
    rk = log.kw("run_recall_loop")
    assert rk["gate_value"] is True, "gate 闭包没把查库结果真回传给 run_recall_loop：%r" % rk
    assert (rk["user_id"], rk["character_id"]) == (USER_ID, CHARACTER_ID), "二跳的 id 传丢了：%r" % rk
    assert log.index("db.enter") < log.index("run_recall_loop"), "gate 的会话必须在二跳之前开"


def test_角色关掉记忆v2时gate回假(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"gate_value": False},
                       recall={"steps": [_STEP_OK]})
    assert _core(log) is not None
    assert log.kw("run_recall_loop")["gate_value"] is False, "gate 必须回真实查库结果"


def test_二跳flag开时取时区偏移且原样传下去(monkeypatch):
    log = _install_all(monkeypatch, _Log(), recall={"steps": [_STEP_OK], "flag_on": True, "tz": 480})
    assert _core(log) is not None
    assert log.kw("tz")["user_id"] == USER_ID
    assert log.kw("run_recall_loop")["tz_offset_min"] == 480, "时区偏移没传下去"


def test_二跳flag关时不取时区偏移但仍走二跳(monkeypatch):
    """既有语义（地图 §四 flag 表）：关时只跳过取偏移，gate 仍开、二跳照跑。"""
    log = _install_all(monkeypatch, _Log(), recall={"steps": [_STEP_OK], "flag_on": False})
    assert _core(log) is not None
    assert log.count("tz") == 0, "flag 关时不该多一次取时区（避免常态多一次 DB 读）"
    assert log.kw("run_recall_loop")["tz_offset_min"] is None
    assert log.count("run_recall_loop") == 1


def test_二跳有步骤时trace的模型调用次数加一(monkeypatch):
    log = _install_all(monkeypatch, _Log(), recall={"steps": [_STEP_OK]})
    assert _core(log) is not None
    assert log.kw("trace")["llm_calls"] == 2, "有二跳步骤必须记 2 次模型调用"


def test_二跳无步骤时计数仍是一次(monkeypatch):
    log = _install_all(monkeypatch, _Log(), recall={"steps": []})
    assert _core(log) is not None
    assert log.count("run_recall_loop") == 1
    assert log.kw("trace")["llm_calls"] == 1


def test_二跳抛异常只记warning并剥recall标记后续照跑(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": "问个事 [RECALL]"},
                       recall={"boom": RuntimeError("loop down")})
    assert _core(log, search_loop=True) is not None, "二跳失败不得影响本轮回复"
    assert "AI recall second-hop failed" in log.kw("warn")["msg"]
    assert log.count("extract_recall") == 1, "失败降级要剥 [RECALL] 标记兜底"
    assert log.kw("trace")["llm_calls"] == 1, "失败不计入模型调用次数"


# ────────────────── 3 C2 自主搜索环 / 关档剥标 ──────────────────

def test_搜索环真跑且发起方与四个注入依赖原样传递(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True) is not None
    sk = log.kw("run_search_loop")
    assert (sk["user_id"], sk["character_id"], sk["initiator"]) == (
        USER_ID, CHARACTER_ID, "user"), "S1 发起方口径被改（聊天回合必须是 user）：%r" % sk
    assert all(callable(d) for d in sk["deps"]), "四个注入依赖必须原样传下去：%r" % (sk["deps"],)


def test_搜索成功真落浏览记录且会话内提交(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True) is not None
    seq = log.names()
    tail = seq[seq.index("run_search_loop"):]
    assert tail.index("db.factory") < tail.index("db.enter") < tail.index("db.add") \
        < tail.index("db.commit") < tail.index("db.exit"), (
        "浏览记录必须在会话内新增并提交：%s" % seq)
    row = log.kw("db.add")["obj"]
    assert (row.character_id, row.query) == (CHARACTER_ID, "今天天气"), "浏览记录入参原样：%r" % row


def test_搜索环只有成功轮计入模型调用次数(monkeypatch):
    """既有口径（地图 §四第 3 条）：失败轮不计入；这一条也钉住「不许顺手改成全计」。"""
    log = _install_all(monkeypatch, _Log(), search={"steps": [_STEP_OK, _STEP_BAD]})
    assert _core(log, search_loop=True) is not None
    assert log.kw("trace")["llm_calls"] == 2, "1 主干 + 1 成功轮＝2（失败轮不计）"
    assert log.kw("trace")["route"] == "search_loop", "搜索成功档 trace route 必须是 search_loop"


def test_搜索环零步骤时不改route也不加计数(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"steps": []})
    assert _core(log, search_loop=True) is not None
    assert log.kw("trace")["route"] == "direct", "零步骤仍算 direct（没真搜到）"
    assert log.kw("trace")["llm_calls"] == 1


def test_搜索环抛异常只记warning并剥search标记(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"boom": RuntimeError("loop down")})
    assert _core(log, search_loop=True) is not None
    assert "AI web search loop failed" in log.kw("warn")["msg"]
    assert log.count("extract_search") == 1, "失败降级要剥 SEARCH 标记兜底"
    assert log.kw("trace")["route"] == "direct"


def test_关档时不进搜索环只按顺序剥search与recall标记(monkeypatch):
    log = _install_all(monkeypatch, _Log())
    assert _core(log, search_loop=False) is not None
    assert log.count("run_search_loop") == 0, "关档不得进自主搜索环：%s" % log.names()
    order = log.names()
    assert order.index("extract_search") < order.index("extract_recall"), (
        "关档剥标顺序（SEARCH 在前、RECALL 在后）被换：%s" % order)
    assert log.kw("trace")["route"] == "chunked", "关档沿用调用方传入的 route"


def test_关档剥标记异常也静默不阻断(monkeypatch):

    log = _install_all(monkeypatch, _Log())

    def _boom(_text):
        raise RuntimeError("strip down")

    monkeypatch.setattr(chat_service, "_extract_search", _boom)
    assert _core(log, search_loop=False) is not None, "剥标记失败不得影响本轮回复"
    assert log.count("extract_recall") == 1, "前一个 try 失败不得连带跳过后一个"


# ────────────────── 4 C3 MCP 工具阶段 ──────────────────

def test_mcp命中时真跑工具阶段并按session与id传递(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": _MCP_TEXT}, mcp={"hit": True})
    assert _core(log) is not None
    mk = log.kw("run_mcp_tool_stage")
    assert (mk["user_id"], mk["character_id"], mk["session_id"]) == (
        USER_ID, CHARACTER_ID, SESSION_ID), "MCP 阶段的三个 id 传丢了：%r" % mk
    assert isinstance(mk["steps"], list), "搜索步骤账本要原样传给 MCP 阶段"


def test_mcp命中后真的再决策一次且输出标记被剥净(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": _MCP_TEXT}, mcp={"hit": True})
    out = _core(log)
    assert out is not None
    assert log.count("generate_response") == 1, "命中后必须再决策一次：%s" % log.names()
    assert "mcp." not in out["final_text"], "再决策输出里的 mcp.* 标记必须剥净：%r" % out["final_text"]
    assert log.kw("trace")["llm_calls"] == 1, "MCP 再决策不单独计入这个计数（现状口径）"


def test_没有mcp标记时既不跑工具阶段也不再决策(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": _OK_TEXT}, mcp={"hit": True})
    assert _core(log) is not None
    assert log.count("run_mcp_tool_stage") == 0, "无标记不得空跑工具阶段：%s" % log.names()
    assert log.count("generate_response") == 0


def test_工具阶段返回假时不触发再决策(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": _MCP_TEXT}, mcp={"hit": False})
    assert _core(log) is not None
    assert log.count("run_mcp_tool_stage") == 1
    assert log.count("generate_response") == 0, "没真正跑起来就不该再决策（否则白花一次模型）"


def test_mcp阶段抛异常只记warning并剥动作标记兜底(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"text": _MCP_TEXT},
                       mcp={"boom": RuntimeError("mcp down")})
    assert _core(log) is not None, "MCP 失败不得影响本轮回复"
    assert "MCP tool stage failed" in log.kw("warn")["msg"]
    # 剥动作标记的兜底（清洗链尾部也会调 strip_actions，故只钉「至少走过一次」＋发生在 trace 之前）
    assert log.count("strip_actions") >= 1, "失败降级要剥动作标记兜底"
    assert log.index("strip_actions") < log.index("trace"), "兜底剥标必须在 trace 之前"
    assert log.count("generate_response") == 0


# ────────────────── 5 三段与后半段的交界 + 出口契约 ──────────────────

def test_搜索步骤账本是mcp与trace共同的那一份(monkeypatch):
    log = _install_all(monkeypatch, _Log(), search={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True) is not None
    steps = log.kw("run_search_loop")
    assert steps["state"]["ai_response"] == _OK_TEXT, "搜索拿到的是生成后的 state"
    tr = log.kw("trace")
    assert tr["tool_calls"] == 1, "trace 的工具次数＝解析动作 + 搜索步骤：%r" % tr
    assert "search" in tr["steps_json"], "搜索步骤要进 steps_json：%r" % tr


def test_两种入口档都跑得通且返回结构不变(monkeypatch):
    log = _install_all(monkeypatch, _Log())
    out = _core(log)
    assert set(out) == {"final_state", "final_text", "gen_prompt", "img_text",
                        "cal_note_text", "memo_text", "streamed", "stream_blocks",
                        "stream_saved"}
    assert out["final_text"] == _OK_TEXT, "三段都只搬运不改写展示文本：%r" % out["final_text"]
    assert out["final_state"]["ai_response"] == _OK_TEXT
