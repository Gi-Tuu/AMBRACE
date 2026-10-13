# -*- coding: utf-8 -*-
"""A42 ②c 注入档（前置，2026-10-10）守卫：默认关＝逐字节不变 · 独立配额 · 纯函数不查库。

派单＝交接文档 §四-2：**只做前置**（渲染器＋分区＋落位），③「投影进 prompt 的退化量化」要计费授权，本文件不碰。

钉的五件事（对应派单的三条守卫＋我自己在做的过程中撞到的两个坑）：
① **注入闸关 ⇒ 装配产出逐字节相同**：跑真 `assemble_context()` 比 `context_messages` 本身，
   并配「投影确实填了几格」的反向钉，否则"相等"是空对空；
② **builder 与落位分开钉**：闸关 ⇒ builder 返回空列表；`_sv` 里没这个键 ⇒ append 链一条都不追加
   （钉的是"注册表没产出时不会有人替它渲染一遍"——项目已经删掉过 13 段这种内联兜底）；
③ **两把闸各自独立**：开注入不开影子 ⇒ 只渲染 ws 上现成的格，**一次额外查询都不发**
   （用计数桩，不用异常桩：整条路径 fail-open，异常桩证不了"没调用"）；
④ **配额是新区，不挤占既有段**：既有各段 (quota_tokens, order) 与基线表逐键相同，新区 200/71 独占；
⑤ **注册时机**：`import app.agent.context` 之后注册表里就得有这个键——挂在 `assembly.py` 里注册
   会因该文件是**函数内惰性 import** 而"每进程第一轮不注入"（本单实测过这个形状，故挪进 section_*.py）。

纪律：临时库（`_dbclone`），绝不连生产库；不调模型、零计费。
"""
import asyncio
import json

import pytest
from _dbclone import clone_engine, make_session_factory

import app.agent.context as _ctx  # noqa: F401  触发所有 section_*.py（含 section_projection）注册
import app.agent.context.assembly as assembly_mod
import app.agent.workspace_projection as proj_mod
from app.agent.context.section_projection import workspace_projection_section
from app.agent.context.sections import get_sections
from app.agent.workspace import CognitiveWorkspace
from app.agent.workspace_projection import (
    INJECT_FLAG, INJECT_HEADER, INJECT_QUOTA_TOKENS, project_workspace, render_projection_block,
)
from app.flags import agent_flags

CHAR_ID, USER_ID = 13, 1

# 既有分区的配额/取号基线：②c 只许**新增**一区，从这些段里挤就会改它们的裁剪结果
_BASELINE_SECTIONS = {
    "current_state_anchor": (220, 42),
    "user_now": (300, 44),
    "working_state": (300, 20),
    "location": (300, 81),
    "thought_pool": (0, 70),
    "continue_payload": (0, 60),
    "mcp_tools": (800, 30),
    "world_facts": (600, 45),
}


@pytest.fixture(scope="module")
def ctx_db(tmp_path_factory):
    """临时库 + User(1) + AICharacter(13)：与 `test_projection_output_equivalence_a42.py` 同一形状。"""
    db_file = (tmp_path_factory.mktemp("a42c") / "ctx.db").as_posix()
    engine = clone_engine(db_file)
    factory = make_session_factory(engine)

    async def _seed():
        from app.models.character import AICharacter
        from app.models.user import User

        async with factory() as db:
            db.add(User(id=USER_ID, username="a42c_user", nickname="本人"))
            db.add(AICharacter(id=CHAR_ID, user_id=USER_ID, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True,
                               self_statement="我是酱，说话短。", bio="住在花园隔壁。"))
            await db.commit()

    asyncio.run(_seed())
    old = assembly_mod.async_session_factory
    assembly_mod.async_session_factory = factory
    yield factory
    assembly_mod.async_session_factory = old
    asyncio.run(engine.dispose())


def _base_state(**over):
    state = {
        "user_message": "在吗", "character_id": CHAR_ID, "user_id": USER_ID, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {"self_statement": "我是酱，说话短。", "bio": "住在花园隔壁。"},
        "character_name": "酱", "user_name": "本人", "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None, "status_update": None,
        "lang": "zh", "perception": {"topic": "刚下班到家", "mood": "疲惫"},
        "workspace": CognitiveWorkspace(),
    }
    state.update(over)
    return state


def _assemble(state, **sv):
    """跑真装配函数。`sv` 用来模拟注册表产出（键不存在＝该段本轮没产出）。"""
    from app.agent.context_builder import _trim_limits

    section_values = {"relationship": ""}
    section_values.update(sv)
    out = asyncio.run(assembly_mod.assemble_context(
        state, _section_values=section_values, _trim=_trim_limits(True),
    ))
    return out["context_messages"]


def _fingerprint(messages) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True)


def _flag(key, on):
    old = agent_flags.AGENT_FLAGS.get(key)
    agent_flags.AGENT_FLAGS[key] = on
    return old


def _restore(key, old):
    if old is None:
        agent_flags.AGENT_FLAGS.pop(key, None)
    else:
        agent_flags.AGENT_FLAGS[key] = old


def _run_projection(state, *, shadow: bool):
    old = _flag(proj_mod.SHADOW_FLAG, shadow)
    try:
        return asyncio.run(proj_mod.project_into_workspace(state))
    finally:
        _restore(proj_mod.SHADOW_FLAG, old)


# ───────────────── 动态插件通道固定（2026-10-13，CI run 38405243473）─────────────────
#
# 红的是本文件第①条，差异内容 `【你的抖音账号】…尚未绑定登录…` 与投影无关：
#   · `app/agent/context/assembly.py:828-844` 在**调用时**才 `from app.plugins.registry import
#     run_hook`，随后 `await run_hook("context_inject", {...}, user_id=..., callsite=...)`；
#   · `app/plugins/registry.py:317-333` 按进程级 `_loaded` / `_enabled` 分发——本会话更早的插件
#     测试把 douyin_mcp 装进内存并置为启用，本文件才会吃到真插件（本机/快测档绿、全量档红的顺序依赖）；
#   · `plugins/examples/douyin_mcp/main.py:2788` 的 `context_inject` → `_build_section(tenant)`，
#     而 `main.py:2002-2011` 带着进程级时间节流 `_last_inject_ts`（:110）：返回提示的同一条分支里
#     `_last_inject_ts = time.time()`，此后 `inject_minutes`（默认 120 分钟）内的调用一律 `return ""`。
# ⇒ 第一次装配（off）拿到提示、第二次（on）被插件自己写的时间戳判空，逐字节比较吃的是插件缓存的差。
# 影子闸没有改业务插件状态（`project_into_workspace` 不碰 registry / 插件配置），所以这里按挂点固定，
# 不桩 `assemble_context`、不关投影链、不删逐字节比较、不忽略那条差异。
import app.plugins.registry as registry_mod  # noqa: E402  在打桩前拿到生产分发函数本体

_RUN_HOOK_ORIGIN = registry_mod.run_hook

# 人工合成的注入内容：不含任何真实用户／角色正文，只用来证明插件通道两次都被消费
PLUGIN_INJECT_PIN = "【测试插件注入】合成提示（固定动态插件输出用，非真实账号数据）"


@pytest.fixture(autouse=True)
def pinned_plugin_inject(monkeypatch):
    """在生产读取的那一处分发点（`registry.run_hook` 的 `context_inject`）返回同一条**非空**提示。

    返回调用次数列表：主断言用它证「两次装配各走了一次插件注入」，缺一次就说明比较面是空的。
    """
    calls = []

    async def _pinned(hook_name, ctx, *a, **kw):
        if hook_name != "context_inject":
            return await _RUN_HOOK_ORIGIN(hook_name, ctx, *a, **kw)
        calls.append(kw.get("callsite") or "context_inject")
        ctx["context_messages"].append({"role": "system", "content": PLUGIN_INJECT_PIN})
        return None

    monkeypatch.setattr(registry_mod, "run_hook", _pinned)
    return calls


def _has_plugin_block(messages) -> bool:
    return any(m.get("content") == PLUGIN_INJECT_PIN for m in messages)


def test_固定插件时分钟跨越不改变本夹具提示(ctx_db, monkeypatch, pinned_plugin_inject):
    """This fixture omits the current-time section; advancing its assembly clock is neutral."""
    from datetime import datetime, timedelta, timezone

    class AssemblyClock(datetime):
        instant = datetime(2026, 10, 13, 10, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return cls.instant.replace(tzinfo=None)
            return cls.instant.astimezone(tz)

    monkeypatch.setattr(assembly_mod, "datetime", AssemblyClock)
    before = _assemble(_base_state())
    AssemblyClock.instant += timedelta(minutes=1)
    after = _assemble(_base_state())
    assert len(pinned_plugin_inject) == 2
    assert _has_plugin_block(before) and _has_plugin_block(after)
    assert _fingerprint(before) == _fingerprint(after)


def _five_field_workspace():
    """用**真投影入口**填五格（自己拼字段形状＝只测到渲染器对假数据的适应）。"""
    ws = CognitiveWorkspace()
    project_workspace(
        ws,
        identity={"character_name": "酱", "user_name": "本人", "bio": "住在花园隔壁。"},
        perception={"topic": "刚下班到家"},
        current_state={"entries": [
            {"key": "location", "label": "位置", "value": "在家", "source": "s"},
            {"key": "activity", "label": "在做什么", "value": "吃饭", "source": "s"},
        ], "empty": False},
        world={"as_of": "2026-10-10T05:00:00", "version": 2, "routes": {
            "user_facts": {"kind": "user_fact", "items": [{"predicate": "job", "value": "程序员"}]},
            "world_facts": {"kind": "world_fact", "items": [{"value": "今天降温"}]},
            "working_state": {"kind": "working_state", "items": [{"value": "在聊周末安排"}]},
        }},
        topics=[{"topic": "周末去哪散步", "importance": 0.8}],
    )
    return ws


# ───────────────────────── ① 默认关＝逐字节不变 ─────────────────────────

def test_影子闸开注入闸关时装配产出逐字节相同(ctx_db, pinned_plugin_inject):
    msgs_off = _assemble(_base_state())
    state = _base_state()
    report = _run_projection(state, shadow=True)
    msgs_on = _assemble(state)
    assert report is not None, "投影没跑起来（state 里没 workspace？）⇒ 本例前提失效"
    assert len(pinned_plugin_inject) == 2, (
        f"两次装配只走到插件分发 {len(pinned_plugin_inject)} 次 ⇒ 装配链没消费 context_inject 挂点，"
        "下面的『相等』是空对空"
    )
    assert _has_plugin_block(msgs_off) and _has_plugin_block(msgs_on), (
        "固定的插件提示没进 prompt ⇒ 逐字节比较看不到插件注入通道，本例的相等没有意义"
    )
    assert _fingerprint(msgs_on) == _fingerprint(msgs_off), (
        "只开影子闸就改了 prompt ⇒ 两把闸并不独立，②c 的前置门禁不成立"
    )


def test_插件时间节流会让逐字节比较变红_反向钉(ctx_db, monkeypatch):
    """把挂点换回 douyin_mcp 的真实形状（`_last_inject_ts`：第一次非空、第二次空），本例必须红。

    没有这一条，上面『固定成同一条提示』就可能是把比较面糊掉了；这一条证的是：
    ① 逐字节比较**看得见**插件注入通道（插件差能把它拉开）；② 主断言之所以绿，靠的是
    通道输出被钉住，而不是投影链被关掉。autouse 夹具先打的桩在这里被覆盖。
    """
    seen = []

    async def _throttled(hook_name, ctx, *a, **kw):
        if hook_name != "context_inject":
            return await _RUN_HOOK_ORIGIN(hook_name, ctx, *a, **kw)
        if not seen:                      # 复刻 main.py:2006-2011 的节流形状
            seen.append(1)
            ctx["context_messages"].append({"role": "system", "content": PLUGIN_INJECT_PIN})
        return None

    monkeypatch.setattr(registry_mod, "run_hook", _throttled)
    msgs_off = _assemble(_base_state())
    state = _base_state()
    _run_projection(state, shadow=True)
    msgs_on = _assemble(state)
    assert _has_plugin_block(msgs_off) and not _has_plugin_block(msgs_on), seen
    assert _fingerprint(msgs_on) != _fingerprint(msgs_off), (
        "插件注入有『一条 vs 没有』的差，逐字节比较却没变红 ⇒ 本文件的比较看不到 prompt 通道"
    )


def test_投影确实填了几格否则上一条是空对空(ctx_db):
    state = _base_state()
    report = _run_projection(state, shadow=True)
    filled = report.get("filled") or {}
    assert len(filled) >= 2, f"'相等'可能只是因为啥都没投：{list(filled)}"


def test_逐字节比较对prompt通道真的敏感(ctx_db):
    """没有这一条，上面的"相等"可能是在比两份没人读的东西（同义反复）。"""
    a = _fingerprint(_assemble(_base_state()))
    b = _fingerprint(_assemble(_base_state(user_message="换个说法在吗")))
    assert a != b, "换了用户消息两次装配还逐字节相同 ⇒ 本文件的比较没看到 prompt，所有'相等'作废"


# ───────────────────────── ② builder 与落位分开钉 ─────────────────────────

def test_闸关时builder首行就返回空列表(ctx_db):
    state = _base_state()
    _run_projection(state, shadow=True)          # 把 ws 填上，证明"有内容也不渲染"
    old = _flag(INJECT_FLAG, False)
    try:
        assert asyncio.run(workspace_projection_section(state, {})) == []
    finally:
        _restore(INJECT_FLAG, old)


def test_闸开时builder产出一块且渲染的是投影结果(ctx_db):
    state = _base_state()
    _run_projection(state, shadow=True)
    old = _flag(INJECT_FLAG, True)
    try:
        out = asyncio.run(workspace_projection_section(state, {}))
    finally:
        _restore(INJECT_FLAG, old)
    assert len(out) == 1, out
    assert out[0].startswith(INJECT_HEADER), out[0][:60]


def test_注册表没产出时append链不替它渲染(ctx_db):
    """钉的是"只有一处渲染点"：`_sv` 里没这个键 ⇒ 一条都不追加（项目已删过 13 段这种内联兜底）。"""
    base = _fingerprint(_assemble(_base_state()))
    with_key = _fingerprint(_assemble(_base_state(), workspace_projection=["假块内容"]))
    assert base != with_key, "append 链没消费这个键 ⇒ 落位是假的"
    msgs = _assemble(_base_state(), workspace_projection=["假块内容"])
    assert [m for m in msgs if m.get("content") == "假块内容"], msgs[-3:]
    assert msgs[-1]["role"] == "user", "红线②：用户消息必须仍在最后（注入不许越位到诉求之后）"


# ───────────────────────── ③ 两把闸独立：开注入不额外查库 ─────────────────────────

def test_开注入不开影子时一次额外查询都不发(monkeypatch):
    """注入档只渲染 ws 上已有的东西；取数永远在影子闸后面（这是它能被单独打开的前提）。

    计数桩而非异常桩：builder 整体 fail-open，抛出去的异常会被自己的 except 吞掉，证不了"没调用"。
    """
    import app.agent.topic_tracker as tt
    import app.events.world_state as wsv
    import app.memory.current_state as cs

    calls = []

    async def _cnt_cs(**kw):
        calls.append("current_state")
        return None

    async def _cnt_world(**kw):
        calls.append("world_state")
        return None

    async def _cnt_topics(cid, uid):
        calls.append("topics")
        return []

    monkeypatch.setattr(cs, "get_current_user_state", _cnt_cs, raising=False)
    monkeypatch.setattr(wsv, "world_state_snapshot", _cnt_world, raising=False)
    monkeypatch.setattr(tt, "load_active_topics_rows", _cnt_topics)

    state = _base_state()
    old = _flag(INJECT_FLAG, True)
    try:
        asyncio.run(workspace_projection_section(state, {}))
    finally:
        _restore(INJECT_FLAG, old)
    assert calls == [], f"注入档自己就去查库了：{calls}"


def test_空workspace时闸开着也不塞空壳(ctx_db):
    """没跑过投影 ⇒ 五格全空 ⇒ 返回 []，绝不能只塞一个标题进 prompt。"""
    old = _flag(INJECT_FLAG, True)
    try:
        assert asyncio.run(workspace_projection_section(_base_state(), {})) == []
    finally:
        _restore(INJECT_FLAG, old)


# ───────────────────────── ④ 配额是新区，不挤占既有段 ─────────────────────────

def test_既有分区的配额与取号没被动过():
    got = {s.key: (s.quota_tokens, s.order) for s in get_sections()}
    for key, want in _BASELINE_SECTIONS.items():
        assert got.get(key) == want, f"{key} 的配额/取号被挪了：{got.get(key)} ≠ {want}"


def test_新区独立且取号独占():
    got = [s for s in get_sections() if s.key == "workspace_projection"]
    assert len(got) == 1, f"注册了 {len(got)} 份＝import 时机或注册表被改坏"
    assert got[0].quota_tokens == INJECT_QUOTA_TOKENS == 200, got[0].quota_tokens
    assert got[0].order == 71, got[0].order
    assert [s.key for s in get_sections() if s.order == 71] == ["workspace_projection"], "71 号被两个键占了"


# ───────────────────────── ⑤ 渲染器：纯函数 · 整行取舍 · 预算内 ─────────────────────────

def test_五格齐全时体量不超新区预算():
    text = render_projection_block(_five_field_workspace())
    assert text.startswith(INJECT_HEADER), text[:60]
    assert len(text) <= INJECT_QUOTA_TOKENS * 2, (len(text), text)
    assert len(text.splitlines()) <= 1 + 5, text


def test_超长值整行不留也不截半句():
    """预算不够时**丢掉整行**：半句话进 prompt＝模型读到一条被腰斩的事实，比没有更糟。"""
    ws = _five_field_workspace()
    ws.current_state = {"entries": [{"key": "note", "label": "备注", "value": "长" * 5000}],
                        "empty": False}
    text = render_projection_block(ws, quota_tokens=60)
    assert len(text) <= 60 * 2, len(text)
    assert "备注" not in text, "超预算那一行该整行丢掉，不该留下半句"
    for ln in text.splitlines()[1:]:
        assert ln.startswith("- "), f"出现不是整行的碎片：{ln!r}"
    # 反向钉：预算给足时这一格必须出现（否则上面的"丢掉"只是因为压根没渲染出东西）
    assert "备注" in render_projection_block(ws, quota_tokens=400)


def test_渲染是纯函数源码里没有IO():
    import inspect

    src = inspect.getsource(render_projection_block)
    for banned in ("async_session_factory", "chat_completion", "await ", "select("):
        assert banned not in src, f"渲染函数出现 IO 入口 {banned}"
    assert render_projection_block(None) == ""
    assert render_projection_block(CognitiveWorkspace()) == ""


def test_极小预算时返回空串而不是只剩标题():
    assert render_projection_block(_five_field_workspace(), quota_tokens=4) == ""
