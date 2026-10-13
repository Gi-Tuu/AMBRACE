# -*- coding: utf-8 -*-
"""A50 批18（2026-10-13）守卫：收尾五阶段的「搬完之后行为一模一样」。

背景（A50 批15 只读地图 → 批16 用户轮信号 → 批17 受控工具三阶段 → 批18 收尾五阶段）：
``chat_service._run_agent_core`` 在受控工具三阶段之后还连着五段——① 生图标记提取（仅非流式回写
展示文本）、② Task Trace 留痕（十个 kwarg ＋ route 三档＋ caller 的 ``_t0`` 计时）、③ Phase H
任务化（``len(_all_steps) >= 1`` 才 spawn、不 await）、④ 日历/备忘提取＋手机桌面落库＋AI 侧定时
承诺解析与动作标记剥离、⑤ 可见文本清洗（零标记兜底＋括号推理并入）与思考过载降级续写。批18 把
五段各整段机械抽成同文件私有函数/协程（块内语句逐字未变，唯一改写＝入参运输与返回值运输），
本文件钉住「搬完之后行为一模一样、顺序一模一样、计数与计时口径一模一样」。

本文件自己的三条纪律（照批16/17 的教训）：
1. **真 helper + 真 core**：一律驱动真的五个 helper 与真的 ``_run_agent_core``，只桩外部依赖
   （DB 会话工厂 / 模型 / trace / 后台任务 / 工具引擎 / 上游三段）。把 helper 整个打桩再数调用次数
   ＝冒充内部行为，``test_五阶段调的是真helper不是桩`` 反过来钉死它。
2. **不抄真实用户数据**：会话/用户/角色 id 用 7101/7102/17 等合成值，正文是通用短语。
3. **顺序即语义**：生图回写 → trace → 任务化 → 备注提取 → 承诺剥标 → 兜底清洗 → 降级 → 回写 →
   九键返回；撤掉任一真调用、换掉取源规则、把 caller 的 ``_t0`` 换成 helper 自己的计时、把
   ``spawn`` 改成 ``await``、把流式的 raw_response 当展示文本回写，本文件必须真红。
"""
import asyncio
import inspect

from app.application import chat_service

SESSION_ID, USER_ID, CHARACTER_ID = 7101, 7102, 17
_USER_MSG_ID = 8901
_OK_TEXT = "在的，你说。"
_STREAM_RAW = "[GEN_IMAGE]一只猫[/GEN_IMAGE]喵～"
_PUNCT = "……"


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

    def spawns(self):
        """spawn_background 的每一次建协程（名字＋已绑定入参）；后台协程不会被真跑。"""
        return [kw for n, kw in self.events if n == "spawn"]

    def spawned(self, coro_name):
        return [kw for kw in self.spawns() if kw.get("cname") == coro_name]

    def spawn_index(self, coro_name):
        for i, (n, kw) in enumerate(self.events):
            if n == "spawn" and kw.get("cname") == coro_name:
                return i
        raise AssertionError("没有 spawn 出来的 %s：%s" % (coro_name, self.names()))


class _Clock:
    """假时钟：monotonic 按给定序列返回——用来钉「latency 仍从 caller 的 _t0 起算」。"""

    def __init__(self, values=(100.0, 100.25)):
        self.values = list(values)
        self.n = 0

    def monotonic(self):
        v = self.values[min(self.n, len(self.values) - 1)]
        self.n += 1
        return v


class _FakeAgent:
    def __init__(self, log, text=_OK_TEXT, streamed=False, raw=None,
                 marker_truncated=False, reasoning=""):
        self._log = log
        self._text = text
        self._streamed = streamed
        self._raw = raw
        self._marker_truncated = marker_truncated
        self._reasoning = reasoning

    async def ainvoke(self, initial_state):
        self._log.add("agent.ainvoke")
        return {"ai_response": self._text, "reasoning": self._reasoning,
                "streamed": self._streamed, "raw_response": self._raw,
                "marker_truncated": self._marker_truncated,
                "character_name": "FixtureCharacter", "user_name": "FixtureUser"}


def _install_common(monkeypatch, log, *, text=_OK_TEXT, streamed=False, raw=None,
                    marker_truncated=False, reasoning="", clock=(100.0, 100.25)):
    """与五段无关的桩：冷战闸/挡位/情感/状态构造/模型/会话工厂/后台任务/假时钟/trace。"""
    import app.agent.runtime as runtime_mod
    import app.agent.trace as trace_mod
    import app.application.character_state_service as cs_mod
    import app.scheduling.memory_review as review_mod
    import app.scheduling.prospective_intent as pi_mod

    async def _cold_war(*_a, **_k):
        return False

    async def _level(*_a, **_k):
        return 0

    async def _emo(*_a, **_k):
        return ""

    async def _settle(*_a, **_k):
        return None

    async def _states(*_a, **_k):
        return {}

    async def maybe_review_success(*_a, **_k):
        return None

    monkeypatch.setattr(chat_service, "_cold_war_block", _cold_war)
    monkeypatch.setattr(chat_service, "_load_reasoning_level", _level)
    monkeypatch.setattr(chat_service, "_resolve_emotional_state", _emo)
    monkeypatch.setattr(cs_mod, "get_character_states", _states)
    monkeypatch.setattr(review_mod, "maybe_review_success", maybe_review_success)
    monkeypatch.setattr(pi_mod, "settle_promises_on_user_message", _settle)
    monkeypatch.setattr(chat_service, "agent", _FakeAgent(
        log, text, streamed, raw, marker_truncated, reasoning))

    def _spawn(coro, **_kw):
        # 记下协程名与已绑定的入参（未启动协程的 cr_frame 里有实参），再关掉免得 never-awaited 告警
        code = getattr(coro, "cr_code", None)
        frame = getattr(coro, "cr_frame", None)
        log.add("spawn", cname=getattr(code, "co_name", "?"),
                args=dict(frame.f_locals) if frame is not None else {})
        if hasattr(coro, "close"):
            coro.close()
        return None

    monkeypatch.setattr(chat_service, "spawn_background", _spawn)
    monkeypatch.setattr(trace_mod, "new_task_id", lambda: "a50b18-task")
    monkeypatch.setattr(trace_mod, "enqueue_task_log", lambda **kw: log.add("trace", **kw))

    def _mk_state(**kw):
        st = {"ai_response": "", "streamed": streamed}
        st.update(kw)
        return st

    monkeypatch.setattr(runtime_mod, "_build_initial_state", _mk_state)

    def _factory():
        raise AssertionError("本文件的用例不应真查库（会话工厂不该被调用）")

    monkeypatch.setattr(chat_service, "async_session_factory", _factory)
    clk = _Clock(clock)
    monkeypatch.setattr(chat_service, "time", clk)
    return clk


def _install_upstream(monkeypatch, log, *, steps=None):
    """批16/17 那四段不在本文件范围内：二跳与搜索只给静默桩，MCP 只给不命中的桩。"""
    import app.agent.loop as loop_mod
    import app.agent.mcp_tools as mcp_mod

    async def _recall(final_state, **_kw):
        log.add("run_recall_loop")
        return final_state, []

    async def _search_loop(final_state, **_kw):
        log.add("run_search_loop")
        return final_state, list(steps or [])

    async def _mcp(final_state, _loop_steps, **_kw):
        log.add("run_mcp_tool_stage")
        return False

    monkeypatch.setattr(loop_mod, "run_recall_loop", _recall)
    monkeypatch.setattr(loop_mod, "run_search_loop", _search_loop)
    monkeypatch.setattr(mcp_mod, "run_mcp_tool_stage", _mcp)


def _install_gen_image(monkeypatch, log, *, clean="", gen_prompt="", img_text=""):
    """D1：剥生图标记。clean 为空串表示「剥完没东西」（此时回写是空转，守卫要看的是取源）。"""

    def _extract(text):
        log.add("gen_image", src=text)
        return clean, gen_prompt, img_text

    monkeypatch.setattr(chat_service, "_extract_gen_image", _extract)


def _install_notes(monkeypatch, log, *, cal="明天十点复盘", memo="记得喝水", notes_boom=False):
    """E：日历/备忘提取与手机桌面落库（返回形状照真实现：cal 是二元组、memo 是 str 或 None）。"""

    def _cal(text):
        log.add("extract_cal_note", src=text)
        return ("CAL_NOTE", cal) if cal else None

    def _memo(text):
        log.add("extract_memo", src=text)
        return memo

    async def save_phone_desktop_notes(character_id, full_text, content):
        if notes_boom:
            raise RuntimeError("notes down")
        return None

    monkeypatch.setattr(chat_service, "_extract_cal_note", _cal)
    monkeypatch.setattr(chat_service, "_extract_memo", _memo)
    monkeypatch.setattr(chat_service, "_save_phone_desktop_notes", save_phone_desktop_notes)


def _install_timer(monkeypatch, log, *, info=None, strip_boom=False):
    """E：AI 侧承诺解析、动作标记剥离、定时标签剥离、事件创建。"""
    import app.agent.actions as actions_mod
    import app.scheduling.promise_parser as parser_mod
    import app.scheduling.promise_service as svc_mod

    def _extract_ai_timer(text, **kw):
        log.add("extract_ai_timer", src=text, **kw)
        return info

    def _strip_actions(text):
        log.add("strip_actions", src=text)
        return text.replace("[动作标记]", "").strip()

    def _strip_timer_tag(text):
        log.add("strip_timer_tag", src=text)
        if strip_boom:
            raise RuntimeError("strip down")
        return text.replace("[timer:20分钟]", "").strip()

    async def _create_event(timer_info):
        log.add("create_event", info=timer_info)
        return None

    monkeypatch.setattr(parser_mod, "extract_ai_timer", _extract_ai_timer)
    monkeypatch.setattr(parser_mod, "strip_timer_tag", _strip_timer_tag)
    monkeypatch.setattr(actions_mod, "strip_actions", _strip_actions)
    monkeypatch.setattr(svc_mod, "create_event", _create_event)


def _install_task_engine(monkeypatch, log, *, boom=False):
    """D2b：Phase H 任务引擎（只在 spawn 的协程被真跑时才有记录，因此靠 spawn 事件取入参）。"""
    import app.agent.task_engine as engine_mod

    async def run_chat_task(character_id, user_id, session_id, content, all_steps,
                            full_text, ok, **_kw):
        log.add("task_engine", character_id=character_id, user_id=user_id,
                session_id=session_id, content=content, steps=list(all_steps),
                full_text=full_text, ok=ok)
        if boom:
            raise RuntimeError("task down")
        return None

    monkeypatch.setattr(engine_mod, "run_chat_task", run_chat_task)


def _install_finalize(monkeypatch, log, *, clean=None, bracket="", degraded=None,
                      clean_boom=False, degraded_boom=False):
    """F2：零标记清洗、括号推理并入、降级续写。"""

    def _sanitize(text):
        log.add("sanitize", src=text)
        if clean_boom:
            raise RuntimeError("sanitize down")
        return (clean if clean is not None else text), bracket

    def _merge(final_state, bracket_text):
        log.add("merge_bracket", state=final_state, bracket=bracket_text)
        return 1

    async def _try_degraded(session_id, user_id, character_id, reasoning_now):
        log.add("try_degraded", session_id=session_id, user_id=user_id,
                character_id=character_id, reasoning_len=len(reasoning_now or ""))
        if degraded_boom:
            raise RuntimeError("degraded down")
        return degraded

    monkeypatch.setattr(chat_service, "_sanitize_persist_full", _sanitize)
    monkeypatch.setattr(chat_service, "_merge_bracket_reasoning", _merge)
    monkeypatch.setattr(chat_service, "_try_degraded_continuation", _try_degraded)


def _install_logger(monkeypatch, log):
    class _Rec:
        def warning(self, msg, *a):
            log.add("warn", msg=str(msg) % a if a else str(msg))

        def info(self, msg, *a):
            log.add("info", msg=str(msg) % a if a else str(msg))

    monkeypatch.setattr(chat_service, "_logger", _Rec())


def _install_all(monkeypatch, log, **kw):
    """装全套桩并返回 (log, clock)；kw 分 common/upstream/gen/notes/timer/task/finalize 七档。"""
    clock = _install_common(monkeypatch, log, **kw.pop("common", {}))
    _install_upstream(monkeypatch, log, **kw.pop("upstream", {}))
    _install_gen_image(monkeypatch, log, **kw.pop("gen", {}))
    _install_notes(monkeypatch, log, **kw.pop("notes", {}))
    _install_timer(monkeypatch, log, **kw.pop("timer", {}))
    _install_task_engine(monkeypatch, log, **kw.pop("task", {}))
    _install_finalize(monkeypatch, log, **kw.pop("finalize", {}))
    _install_logger(monkeypatch, log)
    assert not kw, "未知档位：%s" % sorted(kw)
    return log, clock


def _core(log, *, content="回来啦", **kw):
    return asyncio.run(chat_service._run_agent_core(
        SESSION_ID, USER_ID, CHARACTER_ID, content, "zh", _USER_MSG_ID, **kw))


_STEP_OK = {"ok": True, "action_type": "search", "query": "今天天气", "answer": "晴"}


# ────────────────── 1 接线位：五阶段在主干里的顺序与真 helper ──────────────────

def test_五阶段按生图trace任务化备注清洗的顺序排在生成之后(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True, run_chat_task=True) is not None
    order = log.names()
    seq = ["agent.ainvoke", "gen_image", "trace", "extract_cal_note", "extract_memo", "sanitize"]
    idx = [order.index(x) for x in seq]
    assert idx == sorted(idx), "五阶段顺序变了：%s" % order
    assert len(log.spawned("run_chat_task")) == 1, "有动作时应建任务：%s" % order
    at = log.spawn_index("run_chat_task")
    assert at > order.index("trace"), "任务化必须在 trace 之后"
    assert at < order.index("extract_cal_note"), (
        "任务化必须在备注提取之前（顺序即语义）")


def test_五阶段调的是真helper不是桩(monkeypatch):
    """反「全桩 helper 数调用次数冒充内部行为」：core 的全局解析点必须就是模块里那五个真对象。"""
    _install_all(monkeypatch, _Log())      # 真装一遍桩：守卫断言的是真模块面，不是一次性快照
    g = chat_service._run_agent_core.__globals__
    spec = {
        "_extract_gen_image_stage": False, "_emit_chat_trace": True,
        "_schedule_chat_task": True, "_run_note_and_timer_stage": True,
        "_finalize_visible_text": True,
    }
    for name, want_async in spec.items():
        helper = getattr(chat_service, name)
        assert helper.__module__ == "app.application.chat_service"
        assert inspect.iscoroutinefunction(helper) == want_async, (
            "%s 的同步/协程形态变了（纯函数不许被 await 化）" % name)
        assert g[name] is helper, "%s 不是经模块全局晚绑定调用的" % name
        kinds = [p.kind for p in inspect.signature(helper).parameters.values()]
        assert set(kinds) == {inspect.Parameter.KEYWORD_ONLY}, (
            "%s 的入参必须全是 keyword-only：%s" % (name, kinds))


# ────────────────── 2 D1 生图标记提取 ──────────────────

def test_生图提取源非流式取展示文本并回写clean(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), gen={"clean": "喵～", "gen_prompt": "一只猫",
                                                    "img_text": "一只猫"})
    out = _core(log)
    assert out is not None
    assert log.kw("gen_image")["src"] == _OK_TEXT, "非流式取当时 full_text：%r" % log.kw("gen_image")
    assert out["final_text"] == "喵～", "非流式命中必须用 clean_text 回写展示文本"
    assert out["gen_prompt"] == "一只猫" and out["img_text"] == "一只猫"
    assert out["final_state"]["ai_response"] == "喵～", "回写行要落在出口前"


def test_生图提取源流式取原始响应且不回写(monkeypatch):
    """既有语义：流式展示文本由 chunker 剥过，回写会把已剥标记带回来 ⇒ 取 raw_response 且不写。"""
    log, _ = _install_all(monkeypatch, _Log(), common={"streamed": True, "raw": _STREAM_RAW,
                                                       "text": "喵～"},
                          gen={"clean": _STREAM_RAW, "gen_prompt": "一只猫"})
    out = _core(log)
    assert out is not None
    assert log.kw("gen_image")["src"] == _STREAM_RAW, (
        "流式档必须取 raw_response（不是已剥标记的展示文本）：%r" % log.kw("gen_image"))
    assert out["final_text"] == "喵～", "流式档不许回写 full_text（既有语义）"
    assert out["streamed"] is True


def test_没命中生图标记时不回写(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), gen={"clean": "在的，你说。", "gen_prompt": "",
                                                    "img_text": ""})
    out = _core(log)
    assert out["final_text"] == _OK_TEXT, "gen_prompt 与 img_text 都空时守卫不许回写"
    assert out["gen_prompt"] == "" and out["img_text"] == ""


# ────────────────── 3 D2a Task Trace 留痕 ──────────────────

def test_trace十个kwarg与计数口径原样(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True) is not None
    tr = log.kw("trace")
    assert (tr["task_id"], tr["character_id"], tr["user_id"], tr["session_id"]) == (
        "a50b18-task", CHARACTER_ID, USER_ID, SESSION_ID), "标识四个 kwarg 传错：%r" % tr
    assert tr["trigger"] == "chat" and tr["status"] == "ok"
    assert tr["llm_calls"] == 2 and tr["tool_calls"] == 1, (
        "1 主干生成 + 1 成功搜索轮＝2 次模型、1 次工具：%r" % tr)
    assert "ensure_ascii" not in tr["steps_json"] and "search" in tr["steps_json"], (
        "steps_json 必须是非 ASCII 原样＋含搜索步骤：%r" % tr["steps_json"])
    assert isinstance(tr["latency_ms"], int)


def test_trace_route三档表达式原样(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True, trace_route="chunked") is not None
    assert log.kw("trace")["route"] == "search_loop", "真搜到过 ⇒ search_loop"
    log2, _ = _install_all(monkeypatch, _Log(), upstream={"steps": []})
    assert _core(log2, search_loop=True) is not None
    assert log2.kw("trace")["route"] == "direct", "开档但零步骤 ⇒ direct"
    log3, _ = _install_all(monkeypatch, _Log())
    assert _core(log3, trace_route="sse_fallback") is not None
    assert log3.kw("trace")["route"] == "sse_fallback", "关档沿用调用方传入的 route"


def test_trace延迟仍从caller的t0起算(monkeypatch):
    """钉住地图 §四第 7 条：计时起点是 caller 的 _t0，不许在 helper 里另起一个钟。"""
    log, clock = _install_all(monkeypatch, _Log())
    assert _core(log) is not None
    assert log.kw("trace")["latency_ms"] == 250, (
        "latency 必须是 (caller 的 _t0 → 此刻)，不是 helper 自己的计时：%r" % log.kw("trace"))
    assert clock.n == 2, "monotonic 只该被取两次（_t0 与 latency），实际 %d 次" % clock.n


def test_trace失败只记warning不影响本轮回复(monkeypatch):
    import app.agent.trace as trace_mod

    def _boom(**_kw):
        raise RuntimeError("trace db down")

    log, _ = _install_all(monkeypatch, _Log())
    monkeypatch.setattr(trace_mod, "enqueue_task_log", _boom)
    out = _core(log)
    assert out is not None, "trace 写库失败不得影响本轮回复"
    assert "Task trace failed" in log.kw("warn")["msg"]


# ────────────────── 4 D2b Phase H 任务化 ──────────────────

def test_零步骤时不建任务(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": []})
    assert _core(log, search_loop=True, run_chat_task=True) is not None
    assert not log.spawned("run_chat_task"), "零个明确动作不得建任务：%s" % log.names()
    assert log.kw("trace")["tool_calls"] == 0


def test_有步骤时按原入参建任务且不await(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True, run_chat_task=True, content="今天很累") is not None
    spawn = log.spawned("run_chat_task")[0]
    args = spawn["args"]
    assert (args["character_id"], args["user_id"], args["session_id"], args["content"]) == (
        CHARACTER_ID, USER_ID, SESSION_ID, "今天很累"), "任务入参原样：%r" % args
    assert args["ok"] is True, "末位 ok=True 是既有语义：%r" % args
    assert len(args["all_steps"]) == 1, "动作账本原样传下去：%r" % args
    assert args["full_text"] == _OK_TEXT, (
        "任务化在兜底清洗之前，拿的是当时展示文本：%r" % args)
    assert log.count("task_engine") == 0, "spawn 只建协程不 await（不许在主干里等它）"


def test_run_chat_task关档时整段不进(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]})
    assert _core(log, search_loop=True, run_chat_task=False) is not None
    assert not log.spawned("run_chat_task"), (
        "关档不得建任务（只剩便签落库那一个后台协程）：%s" % [s["cname"] for s in log.spawns()])
    assert [s["cname"] for s in log.spawns()] == ["maybe_review_success",
                                                  "save_phone_desktop_notes"], (
        "后台协程只剩复习成功与便签落库两个：%s" % [s["cname"] for s in log.spawns()])


def test_任务化异常静默不影响本轮回复(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), upstream={"steps": [_STEP_OK]},
                          task={"boom": True})
    out = _core(log, search_loop=True, run_chat_task=True)
    assert out is not None, "任务化失败不得影响本轮回复"
    assert out["final_text"] == _OK_TEXT


# ────────────────── 5 E 备注提取＋AI 承诺/剥标 ──────────────────

def test_备注提取取源与落库入参原样(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log())
    out = _core(log, content="回来啦")
    assert out["cal_note_text"] == "明天十点复盘" and out["memo_text"] == "记得喝水"
    assert log.kw("extract_cal_note")["src"] == _OK_TEXT
    assert log.kw("extract_memo")["src"] == _OK_TEXT
    save = log.spawned("save_phone_desktop_notes")[0]["args"]
    assert (save["character_id"], save["full_text"], save["content"]) == (
        CHARACTER_ID, _OK_TEXT, "回来啦"), "手机桌面落库入参原样：%r" % save
    assert log.index("extract_cal_note") < log.spawn_index("save_phone_desktop_notes"), (
        "先提取后落库，顺序不能反")


def test_备注档流式取原始响应做提取源(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), common={"streamed": True, "raw": _STREAM_RAW,
                                                       "text": "喵～"})
    assert _core(log) is not None
    assert log.kw("extract_cal_note")["src"] == _STREAM_RAW, (
        "流式档提取源必须是含标记的原始响应：%r" % log.kw("extract_cal_note"))
    assert log.kw("extract_memo")["src"] == _STREAM_RAW


def test_日历提取异常不连带跳过后面的备忘与落库(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log())

    def _boom(_text):
        log.add("extract_cal_note", src=_text)
        raise RuntimeError("cal down")

    monkeypatch.setattr(chat_service, "_extract_cal_note", _boom)
    out = _core(log)
    assert out["cal_note_text"] is None, "失败档保持 None"
    assert out["memo_text"] == "记得喝水", "前一个 try 失败不得连带跳过后一个"
    assert len(log.spawned("save_phone_desktop_notes")) == 1, "便签落库照跑"


def test_关档只剥定时标签不动动作标记(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), common={"text": "[动作标记]喵～[timer:20分钟]"},
                          finalize={"clean": "[动作标记]喵～"})
    out = _core(log, search_loop=False)
    assert log.count("strip_actions") == 0, "关档不得剥动作标记（既有语义）：%s" % log.names()
    assert log.kw("strip_timer_tag")["src"] == "[动作标记]喵～[timer:20分钟]", (
        "定时标签剥离必须吃剥标前的展示文本：%r" % log.kw("strip_timer_tag"))
    assert out["final_text"] == "[动作标记]喵～", "关档档位下标记还在（既有行为）"


def test_开档先剥动作标记再剥定时标签(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), common={"text": "[动作标记]喵～[timer:20分钟]"},
                          finalize={"clean": "喵～"})
    out = _core(log, search_loop=True)
    order = log.names()
    assert order.index("strip_actions") < order.index("strip_timer_tag"), (
        "开档的动作标记→定时标签先后被换：%s" % order)
    assert log.kw("strip_actions")["src"] == "[动作标记]喵～[timer:20分钟]"
    assert log.kw("strip_timer_tag")["src"] == "喵～[timer:20分钟]", (
        "定时标签剥离吃的是动作标记剥完、定时标签还在的文本")
    assert out["final_text"] == "喵～"


def test_承诺解析取源与三个id原样传递(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), timer={"info": {"kind": "timer", "id": 1}})
    assert _core(log) is not None
    ai = log.kw("extract_ai_timer")
    assert (ai["user_id"], ai["character_id"], ai["session_id"]) == (
        USER_ID, CHARACTER_ID, SESSION_ID), "AI 侧提取的三个 id：%r" % ai
    assert "source_message_id" not in ai, "F1b：AI 侧不许再绑用户消息 id：%r" % ai
    assert ai["src"] == _OK_TEXT
    assert log.kw("create_event")["info"] == {"kind": "timer", "id": 1}, (
        "承诺命中必须 await create_event：%s" % log.names())


def test_无承诺时不建定时事件(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), timer={"info": None})
    assert _core(log) is not None
    assert log.count("extract_ai_timer") == 1
    assert log.count("create_event") == 0, "没承诺就不许建事件"


def test_承诺段异常只记warning且不动后续清洗(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), timer={"strip_boom": True},
                          finalize={"clean": _OK_TEXT})
    out = _core(log)
    assert out is not None, "承诺段失败不得影响本轮回复"
    assert "AI tag strip failed" in log.kw("warn")["msg"]
    assert log.count("sanitize") == 1, "异常只吃掉承诺那一段，清洗照跑：%s" % log.names()
    assert out["final_text"] == _OK_TEXT


# ────────────────── 6 F2 可见文本清洗与降级续写 ──────────────────

def test_清洗在备注与承诺提取之后(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log())
    assert _core(log) is not None
    order = log.names()
    assert order.index("extract_memo") < order.index("extract_ai_timer") < order.index("sanitize"), (
        "兜底清洗必须在提取之后（提取源不受影响）：%s" % order)


def test_括号推理并入并打标(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), finalize={"clean": _OK_TEXT,
                                                         "bracket": "（心里嘀咕了一下）"})
    out = _core(log)
    mb = log.kw("merge_bracket")
    assert mb["bracket"] == "（心里嘀咕了一下）"
    assert mb["state"] is out["final_state"], "并入必须打在同一个 final_state 对象上：%r" % mb
    assert out["final_state"]["reasoning_bracket_stripped"] is True
    assert out["final_text"] == _OK_TEXT


def test_清洗改动文本时记warning并替换展示文本(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), common={"text": "喵～[GEN_IMAGE]x[/GEN_IMAGE]"},
                          finalize={"clean": "喵～"})
    out = _core(log)
    assert "Final text had marker residue" in log.kw("warn")["msg"]
    assert out["final_text"] == "喵～"
    assert out["final_state"]["ai_response"] == "喵～", "回写要用清洗后的文本"


def test_纯标点加长推理触发降级续写(monkeypatch):
    long_reasoning = "想" * 300
    log, _ = _install_all(monkeypatch, _Log(),
                          common={"text": _PUNCT, "reasoning": long_reasoning},
                          finalize={"clean": _PUNCT, "degraded": "补一句在的"})
    out = _core(log)
    d = log.kw("try_degraded")
    assert (d["session_id"], d["user_id"], d["character_id"], d["reasoning_len"]) == (
        SESSION_ID, USER_ID, CHARACTER_ID, 300), "降级续写的入参原样：%r" % d
    assert out["final_text"] == "补一句在的", "续写成功必须替换展示文本"
    assert "degraded_reply" not in out["final_state"], "成功档不该打降级标"
    assert "reply_degraded recovered" in log.kw("info")["msg"]


def test_标记截断档也触发降级(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(),
                          common={"text": "", "marker_truncated": True},
                          finalize={"clean": "", "degraded": "补一句在的"})
    out = _core(log)
    assert out["final_text"] == "补一句在的", "空正文＋标记截断＝既有触发条件"
    assert log.kw("try_degraded")["reasoning_len"] == 0


def test_续写失败只打degraded_reply标(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(),
                          common={"text": _PUNCT, "reasoning": "想" * 300},
                          finalize={"clean": _PUNCT, "degraded": None})
    out = _core(log)
    assert out["final_state"]["degraded_reply"] is True
    assert out["final_text"] == _PUNCT, "续写失败不许编一句填上去"
    assert "reply_degraded reason=" in log.kw("info")["msg"]


def test_正文非空时不触发降级(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(),
                          common={"text": _OK_TEXT, "reasoning": "想" * 300},
                          finalize={"clean": _OK_TEXT, "degraded": "补一句在的"})
    out = _core(log)
    assert log.count("try_degraded") == 0, "正文不空不许白花一次模型：%s" % log.names()
    assert out["final_text"] == _OK_TEXT
    assert "degraded_reply" not in out["final_state"]


def test_推理不够长且未截断时不触发降级(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log(), common={"text": _PUNCT, "reasoning": "想" * 299},
                          finalize={"clean": _PUNCT, "degraded": "补一句在的"})
    out = _core(log)
    assert log.count("try_degraded") == 0, "299 字不到阈值不许触发（既有口径）"
    assert out["final_state"].get("degraded_reply") is None


def test_降级续写异常照旧冒泡(monkeypatch):
    """地图 §四第 15 条：S10.2 照旧冒泡（SSE 侧靠它回退 chunked），不许顺手吞掉。"""
    log, _ = _install_all(monkeypatch, _Log(),
                          common={"text": _PUNCT, "reasoning": "想" * 300},
                          finalize={"clean": _PUNCT, "degraded_boom": True})
    try:
        _core(log)
    except RuntimeError as e:
        assert "degraded down" in str(e), "异常原样冒泡：%s" % e
    else:
        raise AssertionError("降级续写异常被吞了（端点/SSE 回退依赖它冒泡）")


# ────────────────── 7 出口契约 ──────────────────

def test_九键返回与出口回写不变(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log())
    out = _core(log)
    assert set(out) == {"final_state", "final_text", "gen_prompt", "img_text",
                        "cal_note_text", "memo_text", "streamed", "stream_blocks",
                        "stream_saved"}
    assert out["stream_blocks"] == [] and out["stream_saved"] == [], "空值口径仍是 `or []`"
    assert out["final_state"]["ai_response"] == out["final_text"], (
        "出口回写必须是清洗/降级之后的最终文本：%r" % out["final_state"])


def test_冷战早退仍是唯一的早退出口(monkeypatch):
    log, _ = _install_all(monkeypatch, _Log())

    async def _cold(*_a, **_k):
        return True

    monkeypatch.setattr(chat_service, "_cold_war_block", _cold)
    assert _core(log) is None, "冷战拦截仍立刻返回 None"
    assert log.count("agent.ainvoke") == 0 and log.count("trace") == 0