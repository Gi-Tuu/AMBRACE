# -*- coding: utf-8 -*-
"""A50 批16（2026-10-13）守卫：``_run_user_turn_signals``。

背景（A50 批15 只读地图 → 批16 机械抽取）：``chat_service._run_agent_core`` 在冷战拦截之后、
``_cs_snapshot``/state 构造与 ``agent.ainvoke`` 之前，连着五个用户轮确定性副作用块——承诺兑现
结算（A32）→ 安慰词减重（#63 机制5）→ 复习成功判定（P1）→ 情绪关怀登记 → 用户时间承诺解析。
批16 把它整段搬成同文件私有协程 ``_run_user_turn_signals``（块内语句逐字未变），本文件钉住
「搬完之后行为一模一样、位置一模一样」。

本文件自己的三条纪律（照 A50 前几批的教训）：
1. **真 helper + 真 core**：一律驱动真的 ``_run_user_turn_signals`` 与真的
   ``_run_agent_core``，只桩外部依赖（DB 会话工厂 / 模型 / trace / 后台任务 / 各信号产出函数）。
   把 helper 整个打桩再数调用次数＝冒充内部行为，``test_…调的是真helper不是桩…`` 反过来钉死它。
2. **不抄真实用户数据**：会话/用户/角色 id 用 7001/7002/13 等合成值，正文是通用短语。
3. **顺序即语义**：接线位（冷战 → helper → state/agent）与 helper 内五块的先后都单独钉；
   撤掉任一真调用、把某个 flag 短路、丢一个 id，本文件必须真红。

依赖桩的落点原则：块内那些 ``from X import y`` 是**函数内 import**，所以打桩落在**被导入的那个
模块的属性**上；``async_session_factory`` / ``spawn_background`` / ``_logger`` 是 chat_service 的
裸全局晚绑定，就打桩 chat_service 侧属性（搬家没有传绑定副本，既有打桩面零迁移）。
"""
import asyncio
import inspect
import types

import pytest

from app.application import chat_service

SESSION_ID, USER_ID, CHARACTER_ID, MSG_ID = 7001, 7002, 13, 8801
_OK_TEXT = "在的，你说。"


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

    def count(self, name):
        return sum(1 for n, _ in self.events if n == name)

    def index(self, name):
        return self.names().index(name)


class _FakeDB:
    """会话工厂产物：只记 enter/exit/commit，供「commit 在会话内」这类顺序断言用。"""

    def __init__(self, log):
        self._log = log

    async def __aenter__(self):
        self._log.add("db.enter")
        return self

    async def __aexit__(self, *_exc):
        self._log.add("db.exit")
        return False

    async def commit(self):
        self._log.add("db.commit")

    async def flush(self):
        self._log.add("db.flush")

    def add(self, _obj):
        self._log.add("db.add")


class _FakeAgent:
    def __init__(self, log, text=_OK_TEXT, **extra):
        self._log = log
        self._text = text
        self._extra = extra

    async def ainvoke(self, initial_state):
        self._log.add("agent.ainvoke")
        state = {"ai_response": self._text, "reasoning": None, "streamed": False,
                 "character_name": "FixtureCharacter", "user_name": "FixtureUser"}
        state.update(self._extra)
        return state


def _install_common(monkeypatch, log, *, cold_war=False, text=_OK_TEXT, agent_extra=None):
    """与信号无关的桩：冷战闸/挡位/情感/便签落库/后台任务/模型/状态构造/trace。"""
    import app.agent.runtime as runtime_mod
    import app.agent.trace as trace_mod

    async def _cold_war(*_a, **_k):
        log.add("coldwar", hit=cold_war)
        return cold_war

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
        log.add("spawn", obj=coro)
        if hasattr(coro, "close"):        # 关掉未 await 的协程，避免 never-awaited 告警
            coro.close()
        return None

    monkeypatch.setattr(chat_service, "spawn_background", _spawn)
    monkeypatch.setattr(chat_service, "agent", _FakeAgent(log, text, **(agent_extra or {})))

    def _mk_state(**kw):
        log.add("state", **kw)
        st = {"ai_response": "", "streamed": False}
        st.update(kw)
        return st

    monkeypatch.setattr(runtime_mod, "_build_initial_state", _mk_state)
    monkeypatch.setattr(trace_mod, "new_task_id", lambda: "a50b16-task")
    monkeypatch.setattr(trace_mod, "enqueue_task_log", lambda **kw: log.add("trace", **kw))


def _install_settle(monkeypatch, log, *, boom=None):
    """A32 承诺兑现结算：块内函数内 import ⇒ 打桩落在 prospective_intent 模块属性上。"""
    import app.scheduling.prospective_intent as pi

    async def _settle(character_id, session_id, content):
        log.add("settle", character_id=character_id, session_id=session_id, content=content)
        if boom:
            raise boom

    monkeypatch.setattr(pi, "settle_promises_on_user_message", _settle)


def _install_comfort(monkeypatch, log, *, hit=True, flag_on=True, boom=None):
    """安慰词 → 心事减重：flag 也要真取（AGENT_FLAGS 是现取值，不是打桩）。"""
    import app.agent.loop as loop_mod
    import app.life.preoccupations as pre

    def _has_comfort_word(_content):
        log.add("has_comfort_word")
        if isinstance(hit, Exception):
            raise hit
        return hit

    async def _soften(db, **kw):
        log.add("soften", db=db, **kw)
        if boom:
            raise boom

    monkeypatch.setattr(pre, "has_comfort_word", _has_comfort_word)
    monkeypatch.setattr(pre, "soften_by_comfort_words", _soften)
    monkeypatch.setitem(loop_mod.AGENT_FLAGS, "preoccupation_enabled", flag_on)

    db = _FakeDB(log)

    def _factory():
        log.add("db.factory")
        return db

    monkeypatch.setattr(chat_service, "async_session_factory", _factory)
    return db


def _install_background_signals(monkeypatch, log, *, emotion="平静", care=True):
    """复习成功判定 + 情绪关怀登记：两者都经 spawn_background 且**不被 await**。"""
    import app.domain.emotion.care as care_mod
    import app.domain.emotion.model as emo_mod
    import app.scheduling.memory_review as review_mod
    import app.application.emotion_care_ports as ports_mod

    async def _noop():
        return None

    def _review(user_id, character_id, content):
        # 同步记账：块里 spawn_background(coro) 不 await ⇒ 协程体不会跑，只有创建点能记参数
        log.add("review", user_id=user_id, character_id=character_id, content=content)
        return _noop()

    def _register(user_id, character_id, content, ports=None):
        log.add("care", user_id=user_id, character_id=character_id, content=content,
                ports=ports)
        return _noop()

    def _detect(_content):
        log.add("detect")
        return emotion

    monkeypatch.setattr(review_mod, "maybe_review_success", _review)
    monkeypatch.setattr(emo_mod, "detect_user_emotion", _detect)
    monkeypatch.setattr(ports_mod, "production_care_ports", "PORTS")
    if care:
        monkeypatch.setattr(care_mod, "register_care_task", _register)


def _install_timer(monkeypatch, log, *, info=True, boom=None):
    """用户时间承诺解析：parse（同步）+ create_event（await）。"""
    import app.scheduling.promise_parser as parser_mod
    import app.scheduling.promise_service as service_mod

    def _extract(content, **kw):
        log.add("extract_timer", content=content, **kw)
        if boom:
            raise boom
        return {"content": "洗澡提醒"} if info else None

    async def _create(info_obj):
        log.add("create_event", info=info_obj)
        return 1

    monkeypatch.setattr(parser_mod, "extract_timer", _extract)
    monkeypatch.setattr(service_mod, "create_event", _create)
    # AI 侧那步（core 后段）与本切口无关，但它内部会调模块级 extract_timer ⇒ 同一轮多记一次。
    # 打桩成 None（无 AI 定时标记），账本只反映「用户轮」这一块。
    monkeypatch.setattr(parser_mod, "extract_ai_timer", lambda *_a, **_k: None)


def _install_logger(monkeypatch, log):
    class _Rec:
        def warning(self, msg, *a):
            log.add("warn", msg=str(msg) % a if a else str(msg))

        def info(self, msg, *a):
            log.add("info", msg=str(msg) % a if a else str(msg))

    monkeypatch.setattr(chat_service, "_logger", _Rec())


def _core(log, *, content="回来啦", user_msg_id=MSG_ID, **kw):
    return asyncio.run(chat_service._run_agent_core(
        SESSION_ID, USER_ID, CHARACTER_ID, content, "zh", user_msg_id, **kw))


def _install_all(monkeypatch, log, **kw):
    """默认档：五个信号块全部可观测（happy path），测试按需改其中一两个。"""
    _install_common(monkeypatch, log, **kw.pop("common", {}))
    _install_settle(monkeypatch, log, **kw.pop("settle", {}))
    _install_comfort(monkeypatch, log, **kw.pop("comfort", {}))
    _install_background_signals(monkeypatch, log, **kw.pop("background", {}))
    _install_timer(monkeypatch, log, **kw.pop("timer", {}))
    _install_logger(monkeypatch, log)
    return log


_SIGNAL_MARKS = ("settle", "has_comfort_word", "review", "detect", "extract_timer")


# ────────────────── 1 接线位：冷战早退 → helper → state/agent ──────────────────

def test_冷战阻断时五个信号一个都不动且模型不被调(monkeypatch):
    log = _install_all(monkeypatch, _Log(), common={"cold_war": True})
    assert _core(log) is None
    assert log.names() == ["coldwar"], "冷战拦截后不该有任何信号/生成动作：%s" % log.names()


def test_helper排在状态构造与生成之前且会话用户角色原样传递(monkeypatch):
    log = _install_all(monkeypatch, _Log())
    assert _core(log) is not None
    assert log.index("settle") < log.index("state") < log.index("agent.ainvoke"), (
        "信号必须排在 state 构造与 agent.ainvoke 之前：%s" % log.names())
    st = log.kw("state")
    assert (st["character_id"], st["user_id"], st["session_id"], st["source_id"]) == (
        CHARACTER_ID, USER_ID, SESSION_ID, MSG_ID), "state 侧 id 传丢了：%r" % st
    assert log.kw("settle") == {"character_id": CHARACTER_ID, "session_id": SESSION_ID,
                                 "content": "回来啦"}, "结算的三个入参必须原样"


def test_core调的是真helper不是桩(monkeypatch):
    """反「全桩 helper 数调用次数冒充内部行为」：core 的全局解析点必须就是模块里那个真协程。"""
    log = _install_all(monkeypatch, _Log())
    helper = chat_service._run_user_turn_signals
    assert helper.__module__ == "app.application.chat_service"
    assert inspect.iscoroutinefunction(helper)
    assert chat_service._run_agent_core.__globals__["_run_user_turn_signals"] is helper, (
        "core 不是经模块全局晚绑定调 helper（改成别的对象/传副本即判红）")
    src = inspect.getsource(helper)
    for name in ("settle_promises_on_user_message", "has_comfort_word", "maybe_review_success",
                 "register_care_task", "extract_timer"):
        assert name in src, "真 helper 源码里没有 %s" % name
    assert [p.kind for p in inspect.signature(helper).parameters.values()] == \
        [inspect.Parameter.KEYWORD_ONLY] * 6, "六个入参必须全是 keyword-only"


# ────────────────── 2 承诺兑现结算（A32 接线位随迁）──────────────────

def test_结算真被调且排在另外四块之前(monkeypatch):
    log = _install_all(monkeypatch, _Log())
    assert _core(log, user_timer=True) is not None
    order = log.names()
    first_four = [order.index(x) for x in ("has_comfort_word", "review", "detect", "extract_timer")]
    assert order.index("settle") < min(first_four), "结算必须先于另外四块：%s" % order


def test_结算抛异常只记warning且后续四块照跑(monkeypatch):
    log = _install_all(monkeypatch, _Log(), settle={"boom": RuntimeError("db down")})
    assert _core(log, user_timer=True) is not None, "结算失败不得影响本轮回复"
    assert "prospective settle on user message failed" in log.kw("warn")["msg"]
    for mark in _SIGNAL_MARKS[1:]:
        assert log.count(mark) >= 1, "结算失败后 %s 仍须执行" % mark


# ────────────────── 3 安慰词 → 心事减重 ──────────────────

def test_安慰词命中时减重在同一会话内提交(monkeypatch):
    log = _install_all(monkeypatch, _Log(), comfort={"hit": True, "flag_on": True})
    assert _core(log) is not None
    seq = log.names()
    assert seq[seq.index("db.factory"):seq.index("db.factory") + 4] == [
        "db.factory", "db.enter", "soften", "db.commit"], "commit 必须在会话内：%s" % seq
    assert seq.index("db.commit") < seq.index("db.exit"), "commit 在退出会话之后＝边界被搬坏了"
    sk = log.kw("soften")
    assert (sk["user_id"], sk["character_id"], sk["content"]) == (
        USER_ID, CHARACTER_ID, "回来啦")


def test_安慰词闸假时不碰会话工厂(monkeypatch):
    log = _install_all(monkeypatch, _Log(), comfort={"hit": False})
    assert _core(log) is not None
    assert log.count("has_comfort_word") == 1
    assert log.count("db.factory") == 0, "闸假仍开了会话：%s" % log.names()
    assert log.count("review") == 1, "闸假不得连带跳过后面的块"


def test_安慰词flag关时不减重(monkeypatch):
    log = _install_all(monkeypatch, _Log(), comfort={"hit": True, "flag_on": False})
    assert _core(log) is not None
    assert log.count("has_comfort_word") == 1 and log.count("soften") == 0
    assert log.count("db.factory") == 0, "flag 关时不该开会话"


def test_安慰减重抛异常原样静默且后续块照跑(monkeypatch):
    log = _install_all(monkeypatch, _Log(), comfort={"hit": True, "boom": RuntimeError("x")})
    assert _core(log, user_timer=True) is not None
    assert log.count("db.exit") == 1, "会话必须正常退出（异常不得吞掉退出会话）"
    assert log.count("review") == 1 and log.count("extract_timer") == 1


def test_安慰词判据抛异常也静默不阻断(monkeypatch):
    log = _install_all(monkeypatch, _Log(), comfort={"hit": RuntimeError("boom")})
    assert _core(log, user_timer=True) is not None
    assert log.count("db.factory") == 0
    assert log.count("settle") == 1 and log.count("review") == 1


# ────────────────── 4 两个后台任务：非 await + 原参数 ──────────────────

def test_复习成功与情绪关怀两个后台任务都未被await且参数原样(monkeypatch):
    log = _install_all(monkeypatch, _Log(), background={"emotion": "低落"})
    assert _core(log) is not None
    rk = log.kw("review")
    assert (rk["user_id"], rk["character_id"], rk["content"]) == (
        USER_ID, CHARACTER_ID, "回来啦"), "复习成功判定的参数原样：%r" % rk
    ck = log.kw("care")
    assert (ck["user_id"], ck["character_id"], ck["content"], ck["ports"]) == (
        USER_ID, CHARACTER_ID, "回来啦", "PORTS"), "关怀登记的参数原样：%r" % ck
    # 非 await 的硬判据：交给 spawn_background 的必须是「还没开始跑的协程」
    # （若被改成 spawn_background(await ...)，收到的是结果而不是协程）。
    spawned = [kw["obj"] for n, kw in log.events if n == "spawn"]
    assert len(spawned) == 3, "本档该有三个后台任务（复习成功 / 关怀登记 / 便签落库）：%r" % spawned
    for obj in spawned:
        assert inspect.iscoroutine(obj), (
            "交给 spawn_background 的必须是协程对象（被改成 await 之后再传即判红）：%r" % (obj,))


def test_情绪不低落时只登记复习成功不登记关怀(monkeypatch):
    log = _install_all(monkeypatch, _Log(), background={"emotion": "平静"})
    assert _core(log) is not None
    assert log.count("review") == 1 and log.count("care") == 0, log.names()


def test_情绪判据抛异常时关怀块静默且后续块照跑(monkeypatch):
    log = _Log()
    _install_common(monkeypatch, log)
    _install_settle(monkeypatch, log)
    _install_comfort(monkeypatch, log)
    _install_timer(monkeypatch, log)
    _install_logger(monkeypatch, log)

    import app.domain.emotion.model as emo_mod
    import app.scheduling.memory_review as review_mod

    def _review(*a, **k):
        log.add("review", args=a, kw=k)

        async def _noop():
            return None

        return _noop()

    def _detect(_c):
        raise RuntimeError("boom")

    monkeypatch.setattr(review_mod, "maybe_review_success", _review)
    monkeypatch.setattr(emo_mod, "detect_user_emotion", _detect)
    assert _core(log, user_timer=True) is not None
    assert log.count("review") == 1 and log.count("extract_timer") == 1


# ────────────────── 5 用户时间承诺解析 ──────────────────

def test_时间承诺真解析真创建且会话用户来源消息id不丢(monkeypatch):
    log = _install_all(monkeypatch, _Log(), timer={"info": True})
    assert _core(log, user_timer=True) is not None
    ek = log.kw("extract_timer")
    assert ek["content"] == "回来啦"
    assert (ek["user_id"], ek["character_id"], ek["session_id"],
            ek["source_message_id"], ek["sender"]) == (
        USER_ID, CHARACTER_ID, SESSION_ID, MSG_ID, "user"), "来源消息/会话/用户 id 传丢了：%r" % ek
    assert log.kw("create_event")["info"] == {"content": "洗澡提醒"}


def test_时间承诺解析不出东西时不创建事件(monkeypatch):
    log = _install_all(monkeypatch, _Log(), timer={"info": False})
    assert _core(log, user_timer=True) is not None
    assert log.count("extract_timer") == 1 and log.count("create_event") == 0


def test_时间承诺解析异常只记warning不阻断(monkeypatch):
    log = _install_all(monkeypatch, _Log(), timer={"boom": RuntimeError("parse down")})
    assert _core(log, user_timer=True) is not None
    assert "User timer parse failed" in log.kw("warn")["msg"]


def test_user_timer假时那两行import一次都不执行(monkeypatch):
    """钉「flag 假 ⇒ 连 import 都不发生」：用哨兵模块顶替 promise_parser，
    import 真被执行的话 getattr 必被触发（自检同款哨兵有牙）。"""
    log = _Log()
    _install_common(monkeypatch, log)
    _install_settle(monkeypatch, log)
    _install_comfort(monkeypatch, log)
    _install_background_signals(monkeypatch, log)
    _install_timer(monkeypatch, log)          # 真模块属性先打好桩
    _install_logger(monkeypatch, log)

    touched: list[str] = []

    class _Sentinel(types.ModuleType):
        def __getattr__(self, name):
            touched.append(name)
            raise AttributeError(name)

    monkeypatch.setitem(__import__("sys").modules, "app.scheduling.promise_parser",
                        _Sentinel("app.scheduling.promise_parser"))
    assert _core(log, user_timer=False) is not None
    # 注意：core 后段还有 AI 侧 `from ... import extract_ai_timer`（另一个名字、与本切口无关），
    # 它也会碰哨兵，故这里只钉**用户轮那块的那一个名字**没被 import 过。
    assert "extract_timer" not in touched, (
        "user_timer=False 时 promise_parser.extract_timer 的 import 被执行了：%r" % touched)
    assert log.count("extract_timer") == 0 and log.count("create_event") == 0

    # 自检：同一个哨兵被真 import 时确实会记账（否则上面的负断言是空断言）
    import importlib
    assert importlib.import_module("app.scheduling.promise_parser") is not None
    assert touched, "哨兵无牙：import 都没记账，上面的负断言不算数"
    assert "extract_ai_timer" in touched, "哨兵自检记录的不是 import 取名：%r" % touched


# ────────────────── 6 HTTP 三旗真跑（不能只 false 空跑）──────────────────

def test_HTTP三旗真跑_时间承诺与搜索循环与任务化都有实效(monkeypatch):
    import app.agent.loop as loop_mod
    import app.agent.task_engine as task_mod

    log = _install_all(monkeypatch, _Log(), timer={"info": True})

    async def _search_loop(final_state, **kw):
        log.add("search_loop", **kw)
        return final_state, [{"ok": True, "action_type": "search", "query": "天气",
                              "answer": "晴"}]

    def _chat_task(*a, **kw):
        # 同步记账：块里 spawn_background(coro) 不 await ⇒ 协程体不会跑，只有创建点能记参数
        log.add("run_chat_task", args=a, kw=kw)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(loop_mod, "run_search_loop", _search_loop)
    monkeypatch.setattr(task_mod, "run_chat_task", _chat_task)

    assert _core(log, user_timer=True, search_loop=True, run_chat_task=True) is not None
    # ① user_timer 真生效：解析并真的 await 了 create_event
    assert log.count("extract_timer") == 1 and log.count("create_event") == 1
    # ② search_loop 真生效：走了自主搜索循环，trace 的 route 变成 search_loop
    sk = log.kw("search_loop")
    assert (sk["user_id"], sk["character_id"], sk["initiator"]) == (
        USER_ID, CHARACTER_ID, "user")
    assert log.kw("trace")["route"] == "search_loop"
    # ③ run_chat_task 真生效：有明确工具步骤才任务化，并经 spawn_background 不阻塞
    ct = log.kw("run_chat_task")
    assert ct["args"][:4] == (CHARACTER_ID, USER_ID, SESSION_ID, "回来啦"), (
        "任务化那一步的角色/用户/会话/正文传丢了：%r" % (ct["args"],))
    assert len(ct["args"][4]) >= 1, "零步骤时不该任务化：%r" % (ct["args"],)
    n_task = log.index("run_chat_task")
    after = log.names()[n_task:]
    assert "spawn" in after, "任务化那一步必须经 spawn_background 提交（不阻塞本轮）：%s" % log.names()
    tail_obj = [kw["obj"] for n, kw in log.events[n_task:] if n == "spawn"]
    assert tail_obj and inspect.iscoroutine(tail_obj[0]), (
        "任务化提交给后台的必须是未启动协程：%r" % (tail_obj,))


def test_HTTP三旗里的工具动作缺失时run_chat_task不空跑(monkeypatch):
    import app.agent.loop as loop_mod
    import app.agent.task_engine as task_mod

    log = _install_all(monkeypatch, _Log())

    async def _search_loop(final_state, **_kw):
        return final_state, []                 # 搜索失败/无步骤 ⇒ 不该任务化

    def _chat_task(*a, **kw):
        log.add("run_chat_task", args=a, kw=kw)

        async def _noop():
            return None

        return _noop()

    monkeypatch.setattr(loop_mod, "run_search_loop", _search_loop)
    monkeypatch.setattr(task_mod, "run_chat_task", _chat_task)

    assert _core(log, search_loop=True, run_chat_task=True) is not None
    assert log.count("run_chat_task") == 0, "零步骤时不该凭空起任务"
    assert log.kw("trace")["route"] == "direct"


# ────────────────── 7 回归旁路：既有真 core 用例仍绿（不在本文件重造）──────────────────

@pytest.mark.parametrize("user_timer", [False, True])
def test_两种入口档都跑得通且返回结构不变(monkeypatch, user_timer):
    log = _install_all(monkeypatch, _Log())
    out = _core(log, user_timer=user_timer)
    assert set(out) == {"final_state", "final_text", "gen_prompt", "img_text",
                        "cal_note_text", "memo_text", "streamed", "stream_blocks",
                        "stream_saved"}
    assert out["final_text"] == _OK_TEXT
