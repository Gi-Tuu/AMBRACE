# -*- coding: utf-8 -*-
"""two-pass POC（2026-09-23）：主动消息生成前置「现状 trace」——构造口径 + 灰度 + 留痕。

守的底线（派单 P27b §1 逐条对应，禁止为绿放宽）：
1. 只取 ``world_facts.status == "active"`` 且置信达标；expired / superseded 一律不进；
2. 三分区固定顺序（现状事实 → 用户槽值 → 未完成计划），空分区整段省略，全空返回空串；
3. 单行 ≤ ``TRACE_LINE_CHARS``、总长 ≤ ``TRACE_TOTAL_CHARS``、纯文本无多行原文；
4. 不调 LLM、不写库、不上屏；构造异常收敛为空串 + WARNING；
5. ``two_pass_trace`` 默认关 **且** 角色命中 ``TWO_PASS_TRACE_GRAY_CHARS`` 才生效；关时逐字旧行为
   （不构造、不注入、不多一次 DB 查询）；
6. 注入位置在长历史 / 系统块**之前**；
7. 影子对照只记 {enabled, trace_len, trace_sha8, prompt_len, elapsed_ms}，**不落 trace 全文**。

集成用例只 patch 前置查询与 LLM；DB 用例统一走 tmp_path 临时库（_dbclone 模板克隆），不触生产库。
"""
import asyncio
import hashlib
import os

import pytest

from _dbclone import clone_engine, make_session_factory

from app.scheduling import message_generator as mg
from app.scheduling import state_trace as st

pytestmark = pytest.mark.slow

_CHAR = 13          # 灰度白名单内角色
_OTHER_CHAR = 1     # 白名单外角色
_USER = 1
_MARK = "独特句子ABC123"   # 留痕用例据此断言 trace 正文没被写进观测事件


# ────────────────────────── 临时库夹具 ──────────────────────────

@pytest.fixture()
def trace_db(monkeypatch, tmp_path):
    """临时库（模板库克隆）：patch async_session_factory，users 父行就位（FK 生产同款开启）。"""
    db_path = os.path.join(str(tmp_path), "trace.db")
    engine = clone_engine(db_path)
    factory = make_session_factory(engine)

    async def _init():
        import app.models  # noqa: F401
        from app.models.user import User
        async with factory() as db:
            db.add(User(id=_USER, username="tp_u1", nickname="主人"))
            await db.commit()

    asyncio.run(_init())
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", factory)
    import app.memory.user_facts as _uf
    monkeypatch.setattr(_uf, "async_session_factory", factory)
    yield factory
    engine.sync_engine.dispose()


def _seed(factory, *rows):
    async def _go():
        async with factory() as db:
            for r in rows:
                db.add(r)
            await db.commit()
    asyncio.run(_go())


def _fact(predicate, value, *, status="active", confidence=0.9,
          character_id=_CHAR, user_id=_USER):
    from app.models.memory import WorldFact
    return WorldFact(user_id=user_id, character_id=character_id, subject_type="character",
                     subject_id=character_id, predicate=predicate, object_value=value,
                     status=status, confidence=confidence)


def _slot(slot, value, *, valid_to=None):
    from app.models.user import GlobalUserFact
    return GlobalUserFact(user_id=_USER, slot=slot, value=value, valid_to=valid_to)


def _intent(content, *, status="pending", character_id=_CHAR):
    from app.models.memory import ProspectiveIntent
    return ProspectiveIntent(user_id=_USER, character_id=character_id,
                             content=content, status=status)


def _build(factory, character_id=_CHAR, user_id=_USER):
    async def _go():
        async with factory() as db:
            return await st.build_state_trace(db, character_id=character_id, user_id=user_id)
    return asyncio.run(_go())


# ────────────────────────── ①② 只取 active + 置信达标 ──────────────────────────

def test_只取active_superseded与expired一律不进(trace_db):
    _seed(trace_db,
          _fact("status", "在加班"),
          _fact("activity", "在加班ABC", status="superseded"),   # 被取代的旧现状
          _fact("location", "在出差DEF", status="expired"))
    # 让被取代/过期的两条 updated_at 更晚（看起来更「新」），仍不得进 trace
    async def _bump():
        from sqlalchemy import update
        from app.models.memory import WorldFact
        from app.utils.timeutil import now_naive_utc
        async with trace_db() as db:
            await db.execute(update(WorldFact).where(WorldFact.object_value.like("%ABC%"))
                             .values(updated_at=now_naive_utc()))
            await db.execute(update(WorldFact).where(WorldFact.object_value.like("%DEF%"))
                             .values(updated_at=now_naive_utc()))
            await db.commit()
    asyncio.run(_bump())

    out = _build(trace_db)
    assert "在加班" in out
    assert "在加班ABC" not in out, "superseded 现状进 trace＝错误现状被放大（Random Trace 警示）"
    assert "在出差DEF" not in out, "expired 现状不得进 trace"


def test_置信低于地板不进(trace_db):
    assert st.TRACE_CONFIDENCE_MIN > 0
    _seed(trace_db,
          _fact("status", "达标现状GHI", confidence=st.TRACE_CONFIDENCE_MIN),
          _fact("mood", "低置信推测JKL", confidence=st.TRACE_CONFIDENCE_MIN - 0.01))
    out = _build(trace_db)
    assert "达标现状GHI" in out
    assert "低置信推测JKL" not in out


# ────────────────────────── ③④⑤ 分区顺序 / 省略 / 截断与总长 ──────────────────────────

def test_三分区固定顺序与内容():
    out = st.render_state_trace(["- 状态：在加班"], ["- 位置/城市：示例市"], ["- 周末去看展"])
    lines = out.splitlines()
    assert lines[0].startswith("【当前现状速读】"), "首行应为 trace 头"
    assert lines.index(st._SEC_FACTS) < lines.index(st._SEC_SLOTS) < lines.index(st._SEC_INTENTS), \
        f"分区顺序必须固定为 事实→槽值→计划：{lines}"
    assert lines.index(st._SEC_INTENTS) < lines.index("- 周末去看展")
    assert "- 状态：在加班" in lines and "- 位置/城市：示例市" in lines


def test_空分区整段省略_全空返回空串():
    assert st.render_state_trace([], [], []) == ""
    assert st.render_state_trace(None, None, None) == ""
    assert st.render_state_trace(["   "], [""], []) == "", "全空白行等同空分区，不得只输出分区头"
    only_plan = st.render_state_trace([], [], ["- 周末去看展"])
    assert only_plan and st._SEC_INTENTS in only_plan
    assert st._SEC_FACTS not in only_plan and st._SEC_SLOTS not in only_plan, "空分区不得留下空标题"


def test_单行截断不超过TRACE_LINE_CHARS():
    long_value = "连" * 500
    out = st.render_state_trace([st.fact_line(_fact("status", long_value))], [], [])
    body = [ln for ln in out.splitlines() if ln.startswith("- ")]
    assert body and all(len(ln) <= st.TRACE_LINE_CHARS for ln in body), \
        f"单行必须 ≤{st.TRACE_LINE_CHARS}：{[len(x) for x in body]}"


def test_多行原文被压成单行():
    out = st.render_state_trace(intent_lines=[st.intent_line(_intent("第一行\n第二行\n第三行"))])
    lines = out.splitlines()
    assert lines[0].startswith("【当前现状速读】")
    assert lines[1:] == [st._SEC_INTENTS, "- 第一行 第二行 第三行"], f"多行原文必须压成单行：{lines}"


def test_总长硬上限TRACE_TOTAL_CHARS():
    lines = [f"- 状态：{st._one_line('胖' * 190)}{i}" for i in range(30)]
    out = st.render_state_trace(lines, lines, lines)
    assert out and len(out) <= st.TRACE_TOTAL_CHARS, f"总长必须 ≤{st.TRACE_TOTAL_CHARS}，实际 {len(out)}"


# ────────────────────────── ①②③ 走库的完整拼装 ──────────────────────────

def test_三分区在真实库上齐备(trace_db):
    _seed(trace_db, _fact("status", "在赶周报"), _slot("location", "示例市"),
          _intent("周末一起去看展"), _intent("已完成的约定", status="discharged"),
          _intent("过期约定", status="stale"))
    out = _build(trace_db)
    lines = out.splitlines()
    assert st._SEC_FACTS in lines and st._SEC_SLOTS in lines and st._SEC_INTENTS in lines
    assert "在赶周报" in out and "示例市" in out and "周末一起去看展" in out
    assert "已完成的约定" not in out and "过期约定" not in out, "只有 pending 计划进 trace"


def test_槽值过期与未显式开启的敏感槽不带出(trace_db):
    from app.agent.loop import AGENT_FLAGS
    from app.utils.timeutil import now_naive_utc
    assert AGENT_FLAGS.get("user_fact_relationship") is False, "敏感槽默认关（红线）"
    past = now_naive_utc()
    _seed(trace_db, _slot("location", "过期城市MNO", valid_to=past))
    assert "过期城市MNO" not in _build(trace_db), "valid_to 已过的易变槽属旧现状，不得进 trace"

    async def _fresh_location_and_secret_slot():
        from sqlalchemy import update
        from app.models.user import GlobalUserFact
        from app.utils.timeutil import now_naive_utc as _now
        async with trace_db() as db:
            await db.execute(update(GlobalUserFact)
                             .values(valid_to=None, updated_at=_now()))
            db.add(_slot("relationship", "感情状况PQR"))
            await db.commit()
    asyncio.run(_fresh_location_and_secret_slot())
    out = _build(trace_db)
    assert "感情状况PQR" not in out, "relationship 未经该账号显式开启永不带出"


def test_构造零写入_只读不改任何表(trace_db):
    from sqlalchemy import func, select
    from app.models.memory import ProspectiveIntent, WorldFact
    from app.models.user import GlobalUserFact
    _seed(trace_db, _fact("status", "在加班"), _slot("location", "示例市"),
          _intent("周末去看展"))

    async def _counts():
        async with trace_db() as db:
            return [
                (await db.execute(select(func.count()).select_from(WorldFact))).scalar(),
                (await db.execute(select(func.count()).select_from(GlobalUserFact))).scalar(),
                (await db.execute(select(func.count()).select_from(ProspectiveIntent))).scalar(),
            ]
    before = asyncio.run(_counts())
    assert any(before), "夹具应已插入数据"
    assert _build(trace_db)
    assert asyncio.run(_counts()) == before, "trace 构造必须零写入"


def test_构造抛异常收敛为空串并记WARNING(monkeypatch):
    """fail-open：查库炸了也不冒泡（返回空串 + WARNING），主链路照旧。"""
    recorded: list[str] = []

    class _Loud:
        def warning(self, msg, *a):
            recorded.append(str(msg) % a if a else str(msg))
        def info(self, *_a):
            pass

    class _Boom:
        async def execute(self, *_a, **_k):
            raise RuntimeError("boom-db")

    monkeypatch.setattr(st, "_logger", _Loud())
    assert asyncio.run(st.build_state_trace(_Boom(), character_id=_CHAR, user_id=_USER)) == ""
    assert recorded and "boom-db" in recorded[0], f"应记 WARNING 且带上原因：{recorded}"


# ────────────────────────── ⑥⑦ 灰度双条件 ──────────────────────────

def test_flag默认关():
    from app.agent.loop import AGENT_FLAGS
    assert AGENT_FLAGS.get("two_pass_trace") is False, "新 flag 必须默认关（零行为变化）"
    assert mg.two_pass_trace_allowed(_CHAR) is False
    assert mg.TWO_PASS_TRACE_GRAY_CHARS == frozenset({13})


@pytest.mark.parametrize("flag_on,char,expected", [
    (False, _CHAR, False),      # 总开关关 → 白名单内也不生效
    (True, _CHAR, True),        # 开 + 命中白名单
    (True, _OTHER_CHAR, False),  # 开 + 白名单外角色
    (True, None, False),        # 无角色
    (True, "13", True),         # 字符串 id 也按 int 口径命中
    (True, "abc", False),       # 脏值不炸、按不生效处理
])
def test_灰度双条件_开关开且命中白名单才生效(monkeypatch, flag_on, char, expected):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", flag_on)
    assert mg.two_pass_trace_allowed(char, flags=AGENT_FLAGS) is expected
    if flag_on:  # 生效时必须真读 AGENT_FLAGS（热切口径），不能只认显式传入的 flags
        assert mg.two_pass_trace_allowed(char) is expected


# ────────────────────────── ⑥⑦⑧ 注入行为（集成） ──────────────────────────

class _FakeResult:
    def scalar_one_or_none(self):
        return None

    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def __aenter__(self):
        self._sink.append("db")
        return self

    async def __aexit__(self, *_a):
        return False

    async def execute(self, *_a, **_k):
        return _FakeResult()


_UNSET = object()   # 未指定 loader → 装「不该被调用」哨兵
_KEEP = object()    # 显式保留真实 _load_state_trace（走库路径）


def _patch_pipeline(monkeypatch, *, gen_responses, trace_loader=_UNSET):
    """patch 主动消息前置查询 + LLM（不落真实调用）；返回 captured（LLM 收到的 messages）。

    ``trace_loader``：未指定 → 装断言哨兵（构造一次即失败，用于「不构造」用例）；
    传协程函数 → 包装并记录调用；``_KEEP`` → 保留真实实现（端到端走临时库）。
    """
    captured: dict = {"calls": [], "messages": [], "loader": []}

    async def _noop(*_a, **_k):
        return ""

    async def _noop_list(*_a, **_k):
        return []

    async def _persona(*_a, **_k):
        return {"cognitive": True, "relationship_state": "", "active_topics": "", "storyline_status": "无"}

    seq = list(gen_responses)

    async def _fake_gen(messages, *_a, **_k):
        captured["calls"].append("call")
        captured["messages"].append([dict(m) for m in messages])
        idx = min(len(captured["calls"]) - 1, len(seq) - 1)
        return seq[idx], ""

    async def _boom_loader(*_a, **_k):
        raise AssertionError("flag 关 / 角色未命中时不得构造 trace（不多一次 DB 查询）")

    if trace_loader is not _KEEP and trace_loader is not _UNSET:
        async def _loader(character_id, user_id):
            captured["loader"].append((character_id, user_id))
            return await trace_loader(character_id, user_id)
        monkeypatch.setattr(mg, "_load_state_trace", _loader)

    monkeypatch.setattr("app.agent.user_profile.build_user_profile_text", _noop)
    monkeypatch.setattr("app.agent.persona.assemble_persona_context", _persona)
    monkeypatch.setattr("app.application.weather_service.get_user_weather_line", _noop)
    monkeypatch.setattr("app.memory.search_memories", _noop_list)
    monkeypatch.setattr("app.memory.current_state.current_user_state_anchor", _noop)
    monkeypatch.setattr(mg, "_load_recent_reflection", _noop)
    monkeypatch.setattr(mg, "_gen_with_reasoning", _fake_gen)

    def _factory():
        return _FakeSession(captured.setdefault("db", []))
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", _factory)
    if trace_loader is _UNSET:
        monkeypatch.setattr(mg, "_load_state_trace", _boom_loader)
    return captured


def _resp():
    return "刚下班。路上看到月亮很亮。\n你那边今天忙完了吗？"


def _run(character_id):
    return asyncio.run(mg.generate_proactive_event(
        character_name="小爱", character_bio="", character_personality="友善",
        character_id=character_id, user_id=_USER, current_status="在家",
        last_context="用户: 今天好累\n你: 早点休息"))


async def _trace_ok(_cid, _uid):
    return f"【当前现状速读】\n- 状态：在加班{_MARK}", 12.0, False   # 批 B：三元组第三位＝是否因异常而空


def test_flag关_不构造不注入不多一次查询(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", False)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()])
    segs = _run(_CHAR)
    assert segs, "主链路必须照常生成"
    msgs = captured["messages"][0]
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert _MARK not in msgs[0]["content"]
    assert captured["loader"] == []


def test_白名单外角色不注入(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()])
    assert _run(_OTHER_CHAR)
    assert captured["loader"] == [], "白名单外角色不得构造 trace"
    assert [m["role"] for m in captured["messages"][0]] == ["system", "user"]


def test_注入位置在长历史与系统块之前(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_trace_ok)
    assert _run(_CHAR)
    assert captured["loader"] == [(_CHAR, _USER)]
    msgs = captured["messages"][0]
    assert msgs[0] == {"role": "system", "content": f"【当前现状速读】\n- 状态：在加班{_MARK}"}, \
        f"trace 必须占据首位（前置重读）：{msgs[0]}"
    assert msgs[1]["role"] == "system" and "你是一个真实的朋友" in msgs[1]["content"]
    assert msgs[2]["role"] == "user" and "先看最近聊了什么" in msgs[2]["content"], \
        "长历史在 user 块里 → trace 必须排在它之前"
    assert len(msgs) == 3


def test_trace为空时逐字旧行为(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)

    async def _empty(_cid, _uid):
        return "", 3.0, False
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_empty)
    assert _run(_CHAR)
    assert [m["role"] for m in captured["messages"][0]] == ["system", "user"]


def test_trace构造抛异常_主链路照常生成(monkeypatch):
    """fail-open：走真实 `_load_state_trace`，底层构造炸 → 空串 + WARNING → 不注入、仍生成。"""
    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)

    recorded: list = []

    class _Loud:
        def __init__(self, inner):
            self._inner = inner

        def warning(self, msg, *a):
            recorded.append(str(msg) % a if a else str(msg))

        def __getattr__(self, name):
            return getattr(self._inner, name)
    monkeypatch.setattr(mg, "_logger", _Loud(mg._logger))

    async def _boom(*_a, **_k):
        raise RuntimeError("boom-build")
    monkeypatch.setattr("app.scheduling.state_trace.build_state_trace", _boom)

    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_KEEP)
    assert asyncio.run(mg._load_state_trace(_CHAR, _USER)) == ("", 0.0, True), \
        "异常必须收敛为空串，且第三位标出「是因异常而空」（批 B：与真·拼空区分）"
    assert _run(_CHAR), "trace 构造抛异常也必须照常生成（不阻塞主链路）"
    assert [m["role"] for m in captured["messages"][0]] == ["system", "user"], "异常时不得注入"
    assert any("boom-build" in m for m in recorded), f"应记 WARNING 并带上原因：{recorded}"


# ────────────────────────── ⑦ 影子对照留痕 ──────────────────────────

def test_obs事件字段齐全且steps_json不含trace正文(monkeypatch):
    from app.agent.loop import AGENT_FLAGS
    events: list[dict] = []
    monkeypatch.setitem(AGENT_FLAGS, "memory_trace_debug", True)
    monkeypatch.setattr("app.agent.trace.enqueue_task_log",
                        lambda **kw: events.append(kw))
    trace = f"【当前现状速读】\n· 现状事实\n- 状态：在加班{_MARK}"

    mg._note_state_trace_injected(_CHAR, trace, prompt_len=4321, elapsed_ms=12.345)
    assert len(events) == 1, f"应落一条观测事件：{events}"
    ev = events[0]
    assert ev["route"] == "two_pass_trace" and ev["character_id"] == _CHAR
    detail = __import__("json").loads(ev["steps_json"])
    assert set(detail) == {"enabled", "trace_len", "trace_sha8", "prompt_len", "elapsed_ms"}, \
        f"留痕字段必须恰好这 5 个：{set(detail)}"
    assert detail["enabled"] is True
    assert detail["trace_len"] == len(trace)
    assert detail["prompt_len"] == 4321 and detail["elapsed_ms"] == 12.3
    assert detail["trace_sha8"] == hashlib.sha256(trace.encode("utf-8")).hexdigest()[:8]
    assert _MARK not in ev["steps_json"] and "现状事实" not in ev["steps_json"], \
        "steps_json 绝不含 trace 全文"


def test_留痕失败静默_不影响生成(monkeypatch):
    def _boom(**_kw):
        raise RuntimeError("obs down")
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", _boom)
    mg._note_state_trace_injected(_CHAR, "任意 trace", prompt_len=1, elapsed_ms=1.0)


def test_端到端_灰度角色走真实临时库注入active现状(trace_db, monkeypatch):
    """flag 开 + char13 + 真实临时库：active 进、superseded 不进，且留痕只记 hash。"""
    import json

    from app.agent.loop import AGENT_FLAGS
    monkeypatch.setitem(AGENT_FLAGS, "two_pass_trace", True)
    monkeypatch.setitem(AGENT_FLAGS, "proactive_naturalness_score", False)
    _seed(trace_db, _fact("status", "在赶周报ZZZ"),
          _fact("status", "上周的旧现状YYY", status="superseded"))

    events: list[dict] = []
    monkeypatch.setattr("app.agent.trace.enqueue_task_log", lambda **kw: events.append(kw))
    captured = _patch_pipeline(monkeypatch, gen_responses=[_resp()], trace_loader=_KEEP)
    # 让集成链路用真实临时库（覆盖 _patch_pipeline 里的假会话）
    def _real_factory():
        return trace_db()
    import app.db.database as db_mod
    monkeypatch.setattr(db_mod, "async_session_factory", _real_factory)

    assert _run(_CHAR)
    msgs = captured["messages"][0]
    assert msgs[0]["role"] == "system" and "在赶周报ZZZ" in msgs[0]["content"], \
        f"trace 应前置且只带 active 现状：{msgs[0]}"
    assert "上周的旧现状YYY" not in msgs[0]["content"]
    # 按 route 取注入留痕（2026-09-26 起同通道还会多写一条 two_pass_gate 入口计数，见 test_two_pass_gate_trace）
    inj = [e for e in events if e["route"] == "two_pass_trace"]
    assert len(inj) == 1 and json.loads(inj[0]["steps_json"])["enabled"] is True
    assert "在赶周报ZZZ" not in inj[0]["steps_json"]
