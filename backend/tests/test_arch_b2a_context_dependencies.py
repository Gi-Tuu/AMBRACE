# -*- coding: utf-8 -*-
"""B2a 方向守卫：`message_policy` 是中性纯策略模块，不得回指协调层/装配层。

允许的显式兼容：`context_builder` 对三函数与两个常量的 **re-export**（历史导入与 patch 接缝）。
"""
from __future__ import annotations

import ast
from pathlib import Path

CTX = Path(__file__).resolve().parents[1] / "app" / "agent" / "context"
CB = Path(__file__).resolve().parents[1] / "app" / "agent" / "context_builder.py"
BANNED = ("app.agent.context_builder", "app.agent.context.assembly")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):           # 全树遍历：函数内延迟 import 也会被抓到
        if isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


def test_纯策略模块不依赖协调层与装配层():
    got = sorted(m for m in _imports(CTX / "message_policy.py") if m in BANNED)
    assert got == [], f"message_policy 出现反向依赖：{got}"


def test_纯策略模块不访问数据库与LLM():
    """用 AST 判导入（不看文档里的文字，避免误伤说明）。"""
    mods = _imports(CTX / "message_policy.py")
    for bad in ("sqlalchemy", "logging", "contextvars", "openai", "httpx", "asyncio"):
        assert not any(m == bad or m.startswith(bad + ".") for m in mods), f"不应导入 {bad}：{sorted(mods)}"


def test_装配层从纯策略模块取锚点函数():
    mods = _imports(CTX / "assembly.py")
    assert "app.agent.context.message_policy" in mods
    import app.agent.context.assembly as asm
    import app.agent.context.message_policy as mp
    assert asm._enforce_user_message_last is mp._enforce_user_message_last


def test_方向守卫能抓住函数内反向import(tmp_path):
    """负对照：旧式「函数内 import 协调层」必须被报出（证明守卫不是恒真）。"""
    bad = tmp_path / "bad.py"
    bad.write_text("def f():\n    from app.agent.context_builder import _trim_limits\n"
                   "    return _trim_limits\n", encoding="utf-8")
    got = sorted(m for m in _imports(bad) if m in BANNED)
    assert got == ["app.agent.context_builder"]
