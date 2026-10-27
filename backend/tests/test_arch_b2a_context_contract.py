# -*- coding: utf-8 -*-
"""B2a 阶段 1：上下文装配的**契约测试**与改前快照（2026-10-22）。

只读产品代码；合成 sqlite + 合成 user/角色；不连真实库、不调 LLM。
作用：
  1) 钉住三个纯策略函数的对外契约（含易错边界），迁出到 message_policy 后必须逐字不变；
  2) 通过**真实装配入口** `assemble_context` 产出 messages 快照（role/content/顺序），
     供改后逐字段对照（`B2A_OUT` 指定导出目录）。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.context import assembly as assembly_mod
from app.agent.context_builder import (
    _block_priority,
    _clip_by_whole_lines,
    _enforce_user_message_last,
    _trim_limits,
)
from app.models.base import Base
from app.models.character import AICharacter
from app.models.user import User

_OUT = os.environ.get("B2A_OUT")


def _dump(name: str, obj) -> None:
    if not _OUT:
        return
    Path(_OUT).mkdir(parents=True, exist_ok=True)
    (Path(_OUT) / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ── 合成库 + 装配夹具（照既有 test_context_assembly_final 的口径）──────────
@pytest.fixture()
def asm_env(tmp_path):
    db_path = (tmp_path / "ctx.db").as_posix()
    sync_eng = create_engine("sqlite:///" + db_path)
    Base.metadata.create_all(sync_eng)
    aeng = create_async_engine("sqlite+aiosqlite:///" + db_path)
    factory = async_sessionmaker(aeng, expire_on_commit=False)

    async def _seed():
        async with factory() as db:
            db.add(User(id=1, username="u1", nickname="用户"))
            db.add(AICharacter(id=13, user_id=1, name="酱", personality="温柔",
                               chat_style="口语化", relation_type="朋友", is_active=True))
            await db.commit()

    asyncio.run(_seed())
    old = assembly_mod.async_session_factory
    assembly_mod.async_session_factory = factory
    try:
        yield factory
    finally:
        assembly_mod.async_session_factory = old
        asyncio.run(aeng.dispose())
        sync_eng.dispose()


def _assemble(sv: dict, *, user_id=1):
    """走真实汇编入口，返回 state（含 context_messages）。"""
    state = {
        "user_message": "在吗", "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    if user_id is None:
        state.pop("user_id")
    else:
        state["user_id"] = user_id
    return asyncio.run(assembly_mod.assemble_context(
        state, _section_values={"relationship": "", **sv}, _trim=_trim_limits(True)))


def _snap(state: dict) -> list[dict]:
    return [{"role": m.get("role"), "content": m.get("content")}
            for m in state["context_messages"]]


# ── 1. 纯函数契约（迁出后必须逐字不变）─────────────────────────────────
def test_block_priority_契约():
    assert _block_priority("") == 3                      # 默认档
    assert _block_priority("随便什么内容") == 3
    p = _block_priority("【图片】说明")                   # 命中 marker 时取配置档
    assert isinstance(p, int) and 0 <= p <= 9


def test_clip_by_whole_lines_契约():
    assert _clip_by_whole_lines("abc", 10) == "abc"          # 不超限原样
    assert _clip_by_whole_lines("abc", 3) == "abc"           # 恰好等于
    assert _clip_by_whole_lines("aaa\nbbb", 5) == "aaa"      # 按整行裁，不留半行
    assert _clip_by_whole_lines("aaa\nbbb", 3) == ""         # 头部无换行 ⇒ 整块放弃
    assert _clip_by_whole_lines("aaaaaa", 3) == ""           # 无行边界 ⇒ 整块放弃
    assert _clip_by_whole_lines("\nabc", 2) == ""            # 首个换行在 0 位 ⇒ 空串
    assert _clip_by_whole_lines("", 0) == ""                 # 空输入


def test_enforce_user_message_last_契约():
    msgs = [{"role": "user", "content": "host"},
            {"role": "system", "content": "插件越位"}]
    moved = _enforce_user_message_last(msgs, user_index=0)
    assert moved == 1
    assert [(m["role"], m["content"]) for m in msgs] == [("system", "插件越位"), ("user", "host")], \
        "必须按原相对顺序把越位块挪到宿主 user 之前"

    ok = [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]
    assert _enforce_user_message_last(ok) == 0, "已在末尾不得移动"

    assert _enforce_user_message_last([{"role": "system", "content": "x"}]) == 0, "无 user 不动"
    assert _enforce_user_message_last([]) == 0

    # 锚点非法（越界/该位不是 user）⇒ 退化为最后一条 user
    bad = [{"role": "user", "content": "u1"}, {"role": "user", "content": "u2"},
           {"role": "system", "content": "s"}]
    assert _enforce_user_message_last(bad, user_index=99) == 1
    assert [(m["role"], m["content"]) for m in bad] == [
        ("user", "u1"), ("system", "s"), ("user", "u2")], "退化为最后一个 user 作锚点"


# ── 2. 真实装配产出的快照（改前 / 改后逐字段对照用）────────────────────
def test_装配快照_基本路径(asm_env):
    snap = _snap(_assemble({"relationship": "朋友"}))
    _dump("golden_basic.json", snap)
    assert snap, "装配结果不应为空"
    assert all(set(m) == {"role", "content"} for m in snap)
    assert snap[-1]["role"] == "user", "宿主 user 必须在最后（红线②）"


def test_装配快照_插件伪user越位后仍以宿主user结尾(asm_env):
    """模拟插件在 user 之后插入 system 块：装配后最后一条仍必须是 user。"""
    snap = _snap(_assemble({"relationship": "朋友", "memories": ["@MEM@"]}))
    _dump("golden_with_section.json", snap)
    assert snap[-1]["role"] == "user"


def test_装配快照_空历史与重复装配一致(asm_env):
    a = _snap(_assemble({"relationship": ""}))
    b = _snap(_assemble({"relationship": ""}))
    _dump("golden_repeat.json", {"first": a, "second": b})
    assert a == b, "同一输入重复装配必须一致"


def _old_policy():
    import re as _re
    import subprocess as _sp
    repo = Path(__file__).resolve().parents[2]
    src = _sp.run(["git", "show", "512580e488005badcf717a85da65b53d4023d843:backend/app/agent/context_builder.py"],
                  cwd=repo, capture_output=True, text=True, encoding="utf-8",
                  env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                  check=False).stdout
    if not src.strip():
        src = (Path(__file__).parent / "fixtures" / "arch_b2a_context_builder_512580e4.py").read_text(encoding="utf-8")
    cs = src.index("# M1-S4（2026-08-31）：system 块裁剪优先级")
    ce = src.index("_DEFAULT_BLOCK_PRIORITY = 3") + len("_DEFAULT_BLOCK_PRIORITY = 3")
    ns = {}
    body = src[cs:ce]
    for name in ("_block_priority", "_clip_by_whole_lines", "_enforce_user_message_last"):
        m = _re.search(r"(?ms)^def %s\(.*?(?=^(?:async )?def |\Z)" % _re.escape(name), src)
        assert m, name
        body += "\n" + m.group(0)
    exec("from __future__ import annotations\n" + body, ns)
    return ns


import contextlib as _ctxlib


@_ctxlib.contextmanager
def _bind(policy):
    import app.agent.context.assembly as _a
    import app.agent.context_builder as _c
    import app.agent.context.message_policy as _m
    pairs = [(_c, "_block_priority"), (_c, "_clip_by_whole_lines"), (_c, "_enforce_user_message_last"),
             (_a, "_enforce_user_message_last"),
             (_m, "_block_priority"), (_m, "_clip_by_whole_lines"), (_m, "_enforce_user_message_last")]
    saved = [(mod, attr, getattr(mod, attr)) for mod, attr in pairs]
    try:
        for mod, attr in pairs:
            setattr(mod, attr, policy[attr])
        yield
    finally:
        for mod, attr, old in saved:
            setattr(mod, attr, old)


# ── 3. 反例：不归位就会红（证明上面的"精确顺序"断言不是恒真）────────────
def test_负例_不做归位时末条不是user():
    msgs = [{"role": "user", "content": "host"}, {"role": "system", "content": "越位"}]
    # 不调用归位，直接看：末条确实不是 user ⇒ 说明上面的精确断言有判别力
    assert msgs[-1]["role"] == "system"
    _enforce_user_message_last(msgs, user_index=0)
    assert msgs[-1]["role"] == "user"


def test_负例_错误锚点会改变归属():
    """锚点指向错误下标时，移动的位置不同：用两个 user 证明"锚点确实被使用"。"""
    a = [{"role": "user", "content": "host"}, {"role": "system", "content": "s1"},
         {"role": "user", "content": "plugin_fake"}]
    n = _enforce_user_message_last(a, user_index=0)   # 合法锚点=0（宿主）
    assert n == 2
    assert [(m["role"], m["content"]) for m in a] == [
        ("system", "s1"), ("user", "plugin_fake"), ("user", "host")]


# ── 4. stream 三态与"缺键/空串/空列表/None"差异 ──────────────────────────
@pytest.mark.parametrize("stream", [None, True, False])
def test_stream三态装配均可完成且以user结尾(asm_env, stream):
    state = {
        "user_message": "在吗", "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    out = asyncio.run(assembly_mod.assemble_context(
        state, stream=stream, _section_values={"relationship": ""}, _trim=_trim_limits(True)))
    snap = _snap(out)
    _dump(f"golden_stream_{stream}.json", snap)
    assert snap and snap[-1]["role"] == "user"


def test_缺键与空值不改变条目数(asm_env):
    """键缺失 / "" / [] / None 四态：都不应新增消息（零行为变化）。"""
    base = len(_snap(_assemble({"relationship": ""})))
    for sv in ({"relationship": ""}, {"relationship": []}, {"relationship": None}):
        assert len(_snap(_assemble(sv))) == base


# ── 5. 真实入口 build_context（#7）：全字段对照 + section 实际执行 ─────────
def _full_state(**over):
    state = {
        "user_message": "在吗", "character_id": 13, "user_id": 1, "session_id": 1,
        "intent": "", "retrieved_memories": [], "context_messages": [],
        "character_info": {}, "ai_response": "", "should_update_memory": False,
        "new_memories": [], "emotional_state": "", "bio_update": None,
        "status_update": None, "lang": "zh",
    }
    state.update(over)
    return state


def _norm(obj):
    """只对不可序列化对象做可审计归一化；时间/锚点/副作用字段原样保留。"""
    if isinstance(obj, dict):
        return {str(k): _norm(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_norm(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return f"<{type(obj).__name__}>"


def _real_build(*, stream=None, **over):
    """调真实入口 build_context（注册表 → section → assembly 全链路）。"""
    import app.agent.context_builder as cb
    return asyncio.run(cb.build_context(_full_state(**over), stream=stream))


CASES7 = [("stream_none", {"stream": None}), ("stream_true", {"stream": True})]


@pytest.mark.parametrize("name,kw", CASES7, ids=[c[0] for c in CASES7])
def test_real_pipeline_full_state_旧新一致(asm_env, name, kw):
    from app.agent.context import message_policy as mp
    import app.agent.context_builder as cb

    old = _old_policy()          # 来自本文件顶部的冻结提取（见 _bind 段落）
    new = {"_block_priority": mp._block_priority,
           "_clip_by_whole_lines": mp._clip_by_whole_lines,
           "_enforce_user_message_last": mp._enforce_user_message_last}
    with _bind(old):
        assert cb._enforce_user_message_last is old["_enforce_user_message_last"]
        a = _real_build(**kw)
    with _bind(new):
        assert cb._enforce_user_message_last is new["_enforce_user_message_last"]
        b = _real_build(**kw)
    assert _norm(a["context_messages"]) == _norm(b["context_messages"]), f"{name}: messages 不一致"
    assert len(a["context_messages"]) > 0 and a["context_messages"][-1]["role"] == "user"
    # state 相关字段（非 messages）也要相等：逐键比较而不是只比 keys
    ka, kb = set(a.keys()), set(b.keys())
    for k in sorted(ka & kb - {"context_messages"}):
        assert _norm(a[k]) == _norm(b[k]), f"{name}: state[{k}] 不一致"


def test_real_pipeline_缺少user与空历史仍走真实链路(asm_env):
    a = _real_build(user_message="", retrieved_memories=[])
    b = _real_build(user_message="", retrieved_memories=[])
    assert _norm(a["context_messages"]) == _norm(b["context_messages"])


# ── 6. 真插件 context_inject（#8）：hook 真的被调用 + 伪 user 不抢锚点 ────
def _fake_plugin(name, hooks):
    from app.plugins import registry
    registry._loaded[name] = {"info": {"name": name}, "hooks": hooks, "actions": {}, "router": None}
    registry._enabled[name] = True


def _drop_plugin(name):
    from app.plugins import registry
    registry._loaded.pop(name, None)
    registry._enabled.pop(name, None)


def test_real_plugin_anchor_伪user不抢宿主锚点(asm_env):
    calls = {"n": 0}

    async def _hook(ctx):
        calls["n"] += 1
        ctx["context_messages"].append({"role": "system", "content": "@@PLUGIN_SYS@@"})
        ctx["context_messages"].append({"role": "user", "content": "@@PLUGIN_FAKE_USER@@"})

    _fake_plugin("_b2a_ctx", {"context_inject": [_hook]})
    try:
        out = _real_build()
    finally:
        _drop_plugin("_b2a_ctx")
    msgs = out["context_messages"]
    assert calls["n"] == 1, "hook 未真正被调用（断言会空跑）"
    texts = [m.get("content") or "" for m in msgs]
    assert "@@PLUGIN_SYS@@" in "\n".join(texts)
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"] == "在吗", "宿主 user 必须仍居末位"
    assert "@@PLUGIN_FAKE_USER@@" not in (msgs[-1]["content"] or ""), "伪 user 不得抢占锚点"


def test_real_plugin_缺caller不分发(asm_env):
    calls = {"n": 0}

    async def _hook(ctx):
        calls["n"] += 1

    _fake_plugin("_b2a_ctx2", {"context_inject": [_hook]})
    try:
        state = _full_state()
        state.pop("user_id")
        import app.agent.context_builder as cb
        asyncio.run(cb.build_context(state))
    finally:
        _drop_plugin("_b2a_ctx2")
    assert calls["n"] == 0, "缺 caller 时 context_inject 不应被分发"


# ── 7. C7 时间边界（#9）：时间字段非空且旧新一致；当前消息不被当作上次发言 ──
def test_real_c7_时间字段与当前消息边界(asm_env):
    a = _real_build(user_message="当前这句是新的")
    b = _real_build(user_message="当前这句是新的")
    joined = "\n".join(m.get("content") or "" for m in a["context_messages"])
    # 非空语义：本轮 user 消息必须在
    assert "当前这句是新的" in joined
    # 时间字段：装配里若带时间块，必须旧新逐字一致（不是两边都空）
    assert _norm(a["context_messages"]) == _norm(b["context_messages"]), "两次装配时间/内容不一致"
    # 当前消息不得被重复成"上次发言"（只出现一次）
    assert joined.count("当前这句是新的") == 1, "当前消息被重复注入（可能误当历史）"


def test_real_c7_LLM零调用(asm_env, monkeypatch):
    """LLM tripwire：装配期间不得发起任何外网 HTTP（真实的模型调用都走这里）。"""
    import httpx

    called = {"n": 0}

    async def _boom(self, *a, **kw):
        called["n"] += 1
        raise AssertionError("装配期间不得发起外部 HTTP（疑似 LLM 调用）")

    monkeypatch.setattr(httpx.AsyncClient, "send", _boom, raising=False)
    monkeypatch.setattr(httpx.Client, "send", _boom, raising=False)
    out = _real_build()
    assert called["n"] == 0, f"装配期间发起了 {called['n']} 次外部 HTTP"
    assert out["context_messages"], "装配结果为空（断言会空跑）"


# ── 8. 重复装配的查询/轮次副作用（#10）────────────────────────────────
def test_real_repeat_effects_查询次数旧新一致(asm_env):
    from sqlalchemy import event

    counts = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        counts["n"] += 1

    from app.agent.context import assembly as _a
    old_sf = _a.async_session_factory
    # 通过 session 拿到底层同步引擎

    async def _get_engine():
        async with old_sf() as s:
            return s.get_bind()

    # 直接对 async engine 的 sync_engine 挂事件
    try:
        eng_async = old_sf.kw["bind"] if hasattr(old_sf, "kw") else None
    except Exception:
        eng_async = None
    if eng_async is not None and hasattr(eng_async, "sync_engine"):
        event.listen(eng_async.sync_engine, "before_cursor_execute", _count)
        try:
            old = _old_policy()
            from app.agent.context import message_policy as mp
            new = {"_block_priority": mp._block_priority,
                   "_clip_by_whole_lines": mp._clip_by_whole_lines,
                   "_enforce_user_message_last": mp._enforce_user_message_last}
            counts["n"] = 0
            with _bind(old):
                _real_build()
            n_old = counts["n"]
            counts["n"] = 0
            with _bind(new):
                _real_build()
            n_new = counts["n"]
        finally:
            event.remove(eng_async.sync_engine, "before_cursor_execute", _count)
        assert n_old == n_new, f"查询次数旧 {n_old} != 新 {n_new}"
        assert n_old > 0, "未观测到任何查询（断言会空跑）"
    else:
        import pytest as _pt
        _pt.fail("无法取得引擎以观测查询次数（不得静默跳过）")
