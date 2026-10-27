# -*- coding: utf-8 -*-
"""B2a 等价：三纯策略的冻结期望表 + 新旧路径同源（消息级等价见 contract 测试的 golden diff）。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import app.agent.context.message_policy as mp
from app.agent.context_builder import (
    _block_priority as cb_priority,
    _clip_by_whole_lines as cb_clip,
    _enforce_user_message_last as cb_enforce,
)

# 冻结期望：迁移前（HEAD 512580e4）实测值，逐条钉死
CLIP_CASES = [
    ("abc", 10, "abc"),
    ("abc", 3, "abc"),
    ("aaa\nbbb", 5, "aaa"),
    ("aaa\nbbb", 3, ""),
    ("aaaaaa", 3, ""),
    ("\nabc", 2, ""),
    ("", 0, ""),
    ("line1\nline2\nline3", 11, "line1"),
]
PRIORITY_CASES = [("", 3), ("随便什么", 3)]


@pytest.mark.parametrize("text,keep,expect", CLIP_CASES)
def test_整行裁剪_冻结期望(text, keep, expect):
    assert mp._clip_by_whole_lines(text, keep) == expect


@pytest.mark.parametrize("content,expect", PRIORITY_CASES)
def test_块优先级_冻结期望(content, expect):
    assert mp._block_priority(content) == expect


def test_锚点归位_冻结期望():
    m = [{"role": "user", "content": "host"}, {"role": "system", "content": "越位"}]
    assert mp._enforce_user_message_last(m, user_index=0) == 1
    assert m[-1]["role"] == "user"
    ok = [{"role": "system", "content": "a"}, {"role": "user", "content": "b"}]
    assert mp._enforce_user_message_last(ok) == 0 and ok[-1]["role"] == "user"
    assert mp._enforce_user_message_last([{"role": "system", "content": "x"}]) == 0
    assert mp._enforce_user_message_last([]) == 0


def test_新旧路径同源且行为一致():
    """context_builder 的 re-export 必须与 message_policy **同一对象**（不是复制实现的第二套规则）。"""
    assert cb_priority is mp._block_priority
    assert cb_clip is mp._clip_by_whole_lines
    assert cb_enforce is mp._enforce_user_message_last
    for text, keep, expect in CLIP_CASES:
        assert cb_clip(text, keep) == mp._clip_by_whole_lines(text, keep) == expect


def test_优先级常量单一真源():
    from app.agent.context_builder import _DEFAULT_BLOCK_PRIORITY, _SYSTEM_BLOCK_PRIORITY

    assert _SYSTEM_BLOCK_PRIORITY is mp._SYSTEM_BLOCK_PRIORITY
    assert _DEFAULT_BLOCK_PRIORITY == mp._DEFAULT_BLOCK_PRIORITY == 3


# ── 真 A/B：旧实现从冻结 HEAD 提取，同一进程内替换绑定跑真实入口 ──────────
import contextlib
import re as _re
import subprocess as _sp

import app.agent.context.assembly as _asm
import app.agent.context_builder as _cb
import app.agent.context.message_policy as _mp
from app.models.base import Base
from app.models.character import AICharacter
from app.models.user import User
from sqlalchemy import create_engine as _ce
from sqlalchemy.ext.asyncio import async_sessionmaker as _asmaker, create_async_engine as _cae


def _old_policy_namespace() -> dict:
    """从开工 HEAD 的 context_builder 里提取三个纯策略与两个常量（源码级冻结，不 import 候选代码）。"""
    repo = Path(__file__).resolve().parents[2]
    src = _sp.run(["git", "show", "512580e488005badcf717a85da65b53d4023d843:backend/app/agent/context_builder.py"],
                  cwd=repo, capture_output=True, text=True, encoding="utf-8",
                  env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                  check=False).stdout
    if not src.strip():
        src = (Path(__file__).parent / "fixtures" / "arch_b2a_context_builder_512580e4.py").read_text(encoding="utf-8")
    cs = src.index("# M1-S4（2026-08-31）：system 块裁剪优先级")
    ce = src.index("_DEFAULT_BLOCK_PRIORITY = 3") + len("_DEFAULT_BLOCK_PRIORITY = 3")
    consts = src[cs:ce]
    out = {}
    for name in ("_block_priority", "_clip_by_whole_lines", "_enforce_user_message_last"):
        m = _re.search(r"(?ms)^def %s\(.*?(?=^(?:async )?def |\Z)" % _re.escape(name), src)
        assert m, name
        out[name] = m.group(0)
    ns = {}
    exec("from __future__ import annotations\n" + consts + "\n" + "\n".join(out.values()), ns)
    return ns


@contextlib.contextmanager
def _bind(policy):
    """把宿主(context_builder)与装配(assembly)的实际绑定临时替换为该 policy，退出即恢复。"""
    saved = {
        (_cb, "_block_priority"): _cb._block_priority,
        (_cb, "_clip_by_whole_lines"): _cb._clip_by_whole_lines,
        (_cb, "_enforce_user_message_last"): _cb._enforce_user_message_last,
        (_asm, "_enforce_user_message_last"): _asm._enforce_user_message_last,
        (_mp, "_block_priority"): _mp._block_priority,
        (_mp, "_clip_by_whole_lines"): _mp._clip_by_whole_lines,
        (_mp, "_enforce_user_message_last"): _mp._enforce_user_message_last,
    }
    try:
        for (mod, attr) in saved:
            setattr(mod, attr, policy[attr])
        yield
    finally:
        for (mod, attr), old in saved.items():
            setattr(mod, attr, old)


def _full_snapshot(state: dict) -> dict:
    """完整 messages（每个键都带上，不只 role/content）+ 关键 state 副作用。"""
    return {
        "messages": [dict(m) for m in state["context_messages"]],
        "keys": sorted(k for k in state.keys()),
        "user_message": state.get("user_message"),
        "character_id": state.get("character_id"),
    }


def _run_entry(sv, *, user_id=1):
    import asyncio
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
    from app.agent.context_builder import _trim_limits as _tl
    return asyncio.run(_asm.assemble_context(
        state, _section_values={"relationship": "", **sv}, _trim=_tl(True)))


@contextlib.contextmanager
def _entry_db(tmp_path):
    db = (tmp_path / "ab.db").as_posix()
    sync = _ce("sqlite:///" + db)
    Base.metadata.create_all(sync)
    aeng = _cae("sqlite+aiosqlite:///" + db)
    factory = _asmaker(aeng, expire_on_commit=False)

    async def seed():
        async with factory() as s:
            s.add(User(id=1, username="u1", nickname="用户"))
            s.add(AICharacter(id=13, user_id=1, name="酱", personality="温柔",
                              chat_style="口语化", relation_type="朋友", is_active=True))
            await s.commit()

    import asyncio as _a
    _a.run(seed())
    old = _asm.async_session_factory
    _asm.async_session_factory = factory
    try:
        yield
    finally:
        _asm.async_session_factory = old
        _a.run(aeng.dispose())
        sync.dispose()


CASES = [
    ("basic", {"relationship": ""}),
    ("with_section", {"relationship": "朋友", "memories": ["@MEM@"]}),
]


@pytest.mark.parametrize("name,sv", CASES, ids=[c[0] for c in CASES])
def test_真AB_旧实现与新实现产出完全一致(tmp_path, name, sv):
    old = _old_policy_namespace()
    new = {"_block_priority": _mp._block_priority,
           "_clip_by_whole_lines": _mp._clip_by_whole_lines,
           "_enforce_user_message_last": _mp._enforce_user_message_last}
    with _entry_db(tmp_path):
        with _bind(old):
            assert _asm._enforce_user_message_last is old["_enforce_user_message_last"], "旧绑定未生效"
            snap_old = _full_snapshot(_run_entry(sv))
        with _bind(new):
            assert _asm._enforce_user_message_last is new["_enforce_user_message_last"], "新绑定未生效"
            snap_new = _full_snapshot(_run_entry(sv))
    assert snap_old == snap_new, f"{name}: 旧/新实现产出不一致"
    assert snap_new["messages"][-1]["role"] == "user"
