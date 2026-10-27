# -*- coding: utf-8 -*-
"""B1 方向守卫：规则/扫描在中性模块，且不回头依赖 migrate/guard（允许显式兼容 re-export）。"""
from __future__ import annotations

import ast
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "app" / "db"
BANNED = ("app.db.migrate", "app.db.character_id_guard")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


def _forbidden(path: Path, banned: tuple[str, ...] = BANNED) -> list[str]:
    return sorted(m for m in _imports(path) if m in banned)


def test_中性模块不反向依赖协调模块():
    assert _forbidden(DB / "character_id_references.py") == []


def test_守卫模块任何位置都不得导入迁移模块():
    """遍历**完整 AST**（含函数体/条件分支），抓函数内延迟 import —— 旧的双向循环正是这种写法。"""
    tree = ast.parse((DB / "character_id_guard.py").read_text(encoding="utf-8"))
    assert "app.db.migrate" not in _imports(DB / "character_id_guard.py"), "guard 仍在任何位置导入 migrate"
    assert tree is not None


def test_方向守卫能抓住函数内延迟import(tmp_path):
    """负对照：把旧写法（函数内 from app.db.migrate import ...）交给检查函数，必须报出来。"""
    bad = tmp_path / "bad_lazy.py"
    bad.write_text(
        "def ensure():\n"
        "    from app.db.migrate import scan_character_id_watermark\n"
        "    return scan_character_id_watermark\n",
        encoding="utf-8")
    assert _forbidden(bad) == ["app.db.migrate"]


def test_循环依赖已消除_扫描与常量同源():
    import app.db.character_id_guard as guard
    import app.db.character_id_references as refs
    import app.db.migrate as migrate

    assert migrate.scan_character_id_watermark is refs.scan_character_id_watermark
    assert guard.scan_character_id_watermark is refs.scan_character_id_watermark
    assert guard.CLIENT_SAFE_MAX_ID == refs.CLIENT_SAFE_MAX_ID == (1 << 53) - 1
    assert migrate.CLIENT_SAFE_MAX_ID == refs.CLIENT_SAFE_MAX_ID


def test_历史revision两个helper仍可从migrate导入():
    from app.db.migrate import repair_ai_character_sequence, scan_character_id_watermark

    assert callable(scan_character_id_watermark) and callable(repair_ai_character_sequence)


def test_方向守卫本身能抓错(tmp_path):
    """负对照：含反向依赖的源码必须被检查函数报出来（证明守卫不是恒真）。"""
    bad = tmp_path / "bad.py"
    bad.write_text("from app.db.migrate import scan_character_id_watermark\n", encoding="utf-8")
    assert _forbidden(bad) == ["app.db.migrate"]
