# -*- coding: utf-8 -*-
"""本批（2026-10-23 Qwen）专用：冻结迁移树的**祖先闭包**夹具。

只服务本批（迁移旧树构造 + 未来 head 单头 + 六窗故障注入）：不改动 alembic 树本身、
也不执行任何历史 revision 模块。

为什么需要它（REMAINING_FAILURES.md 归类第一条）：
- 旧树夹具以前只按名字 unlink「候选文件 b2a0c1d2e3f4」，但它已经有后代
  ``c4e6f8a0b2d4``（down_revision = b2a）；删父留子 ⇒ 迁移图断裂，alembic 加载
  ScriptDirectory 时报 KeyError，导致「旧形态库无注入可升到 head」「真跑 b2a 六窗」
  等所有相关用例集体失败。
- 正确做法：以 revision 图为真源，**沿 down_revision 反向走**得到「保留集合」——
  指定边界及其全部祖先；不在集合内的文件一律移除（含未登记父/孤儿后代）。
- 该「反向走」必须走**元数据**，不能 import 历史模块（它们会跑 ``import op`` 之类的
  side-effect，且测试环境不该依赖具体 revision 语义）。用 ``ast.parse`` 只读
  ``revision`` / ``down_revision`` 两个字面量赋值；带 ``AnnAssign``（如
  ``revision: str = "x"`` 或 ``down_revision: Union[str, None] = "y"``）也要能识别。
- 若发现「未知父」（down_revision 指向本目录里不存在的 revision）或「断链」，
  **不静默忽略**——抛 RuntimeError。这正是原批次失败暴露出来的形态：静默 ⇒ 图坏掉
  但用例照跑 ⇒ 掩盖真问题。

本文件在测试里既做正例（迁移旧树构造）也做反例（发现断链时拒绝），具体见
``test_actor_semantics_shadow`` 之外的迁移测试。
"""
from __future__ import annotations

import ast
import shutil
from pathlib import Path


CONSTRAINT_REVISION = "b2a0c1d2e3f4"
CONSTRAINT_PARENT = "e2f4a6b8c0d2"
_VERSIONS_SUBDIR = "versions"


def _decode_literal(py: Path, name: str, value: ast.expr) -> str | None:
    """把 revision/down_revision 的字面量取出来。

    **绝不静默丢信息**（REVIEW_REWORK1 §4 独立红证）：
    - 非 Constant 且非 Tuple/List 的表达式（Name/Call/BinOp/...）⇒ RuntimeError；
      早先版本静默返回 None，让「down_revision = some_var」看起来像根节点，
      把断链图当成合法图。
    - Constant 但不是 str/None（int/float/bool/bytes）⇒ RuntimeError。
    - Tuple/List：全部元素都必须是字符串字面量；0 元或 >1 元 ⇒ RuntimeError。
      本批冻结树夹具只支持单链，合并 revision 会静默丢父，宁可拒。
    """
    if isinstance(value, ast.Constant):
        if value.value is None:
            return None
        if isinstance(value.value, str):
            return value.value
        raise RuntimeError(
            f"{py.name}: {name} 字面量类型不是 str/None（收到 {type(value.value).__name__}）；"
            f"迁移元数据不可解析，拒绝静默"
        )
    if isinstance(value, (ast.Tuple, ast.List)):
        parents: list[str] = []
        for el in value.elts:
            if isinstance(el, ast.Constant) and isinstance(el.value, str):
                parents.append(el.value)
            else:
                raise RuntimeError(
                    f"{py.name}: {name} 合并形态元素非字符串字面量（{type(el).__name__}）；"
                    f"拒绝只取首项导致静默丢父"
                )
        if len(parents) == 0:
            raise RuntimeError(f"{py.name}: {name} 空元组：迁移合并节点必须有父")
        if len(parents) > 1:
            raise RuntimeError(
                f"{py.name}: {name} 多父合并 {parents}；本批冻结树夹具只支持单链，"
                f"拒绝只取首项（会静默丢父）"
            )
        return parents[0]
    raise RuntimeError(
        f"{py.name}: {name} 赋值右侧不是字面量（{type(value).__name__}）；"
        f"迁移元数据不可解析，拒绝静默"
    )


def _read_meta(py: Path) -> tuple[str | None, str | None]:
    """从单个 revision 文件读出 (revision, down_revision)；只走 AST，不 import。

    识别两种赋值：
    - ``Assign``：``revision = "x"``
    - ``AnnAssign``：``revision: str = "x"`` / ``down_revision: Union[str, None] = "y"``

    合并 revision（``down_revision = ("a", "b")``）与根（``None``）都按 :func:`_decode_literal`
    如实处理；任何「读不出」的形态（非字面量/多父/类型不符）一律 RuntimeError，
    **不再静默返回 None**——上一版把不可解析当成根，会让断链图被误认作合法图。
    """
    tree = ast.parse(py.read_text(encoding="utf-8"))
    rev: str | None = None
    down: str | None = None
    for node in tree.body:
        target_name: str | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n in ("revision", "down_revision") for n in names):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    raise RuntimeError(
                        f"{py.name}: revision/down_revision 用了多目标/非 Name 目标 "
                        f"({[type(t).__name__ for t in node.targets]})，元数据不可解析"
                    )
                target_name = node.targets[0].id
                value = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                target_name = node.target.id
                value = node.value
        if target_name not in ("revision", "down_revision"):
            continue
        if value is None:
            # AnnAssign 无值（仅声明 `revision: str`）不算赋值；已在上面过滤。
            continue
        got = _decode_literal(py, target_name, value)
        if target_name == "revision":
            rev = got
        else:
            down = got
    if rev is not None and not isinstance(rev, str):
        raise RuntimeError(f"{py.name}: revision 必须解析为字符串")
    return rev, down


def read_chain(versions_dir: Path) -> dict[str, tuple[Path, str | None]]:
    """versions 目录 → {revision_id: (path, down_revision)}；无 revision 的文件忽略。

    重复 revision id 视为「两文件写同一个 id」的图坏形态，直接抛错（不静默覆盖）。
    """
    out: dict[str, tuple[Path, str | None]] = {}
    for py in sorted(versions_dir.glob("*.py")):
        if py.name.startswith("_"):
            continue
        rev, down = _read_meta(py)
        if rev is None:
            continue
        if rev in out:
            raise RuntimeError(
                f"migration graph corrupted: duplicate revision {rev} "
                f"({out[rev][0].name} vs {py.name})"
            )
        out[rev] = (py, down)
    return out


def ancestors_closure(versions_dir: Path, boundary: str) -> set[str]:
    """从 boundary 沿 down_revision 反向走到根，返回**含边界**的祖先集合。

    - 任一 down_revision 指向本目录里没有的 id ⇒ 抛「断链/未知父」，不静默；
    - 出现环路 ⇒ 抛错（alembic 加载本身也会炸，这里提前给出清晰消息）。
    """
    chain = read_chain(versions_dir)
    if boundary not in chain:
        raise RuntimeError(
            f"boundary revision {boundary} not found in {versions_dir}"
        )
    keep: set[str] = set()
    cur: str | None = boundary
    while cur is not None:
        if cur in keep:
            raise RuntimeError(f"migration graph has a cycle at {cur}")
        if cur not in chain:
            raise RuntimeError(
                f"broken chain: down_revision {cur} has no file in {versions_dir}"
            )
        keep.add(cur)
        cur = chain[cur][1]
    return keep


def prune_to_boundary(versions_dir: Path, boundary: str) -> set[str]:
    """把 versions 目录就地裁到「boundary 及其祖先」；返回被删掉的 revision id 集合。

    被删的包括 boundary 之后的一切后代（含分叉支）；不改动 boundary 与祖先本身。
    这一步是**图遍历**驱动的，而不是「硬删某几个文件名」——后者会在链上出现新后代时
    留下孤儿，正是本批原失败的直接成因。
    """
    keep = ancestors_closure(versions_dir, boundary)
    chain = read_chain(versions_dir)
    removed: set[str] = set()
    for rev, (path, _down) in chain.items():
        if rev in keep:
            continue
        path.unlink()
        removed.add(rev)
    # 兜底：非 __pycache__ 的 .pyc 也清一遍，避免 Python 从缓存重新导入被删模块。
    for pyc in list(versions_dir.rglob("*.pyc")):
        pyc.unlink()
    return removed


def prune_excluding(versions_dir: Path, drop: str) -> set[str]:
    """删除 drop 及其全部后代，同时保留 drop 的所有祖先。

    与 :func:`prune_to_boundary` 的区别：这里不指定「保留到哪个祖先为止」，
    而是给「要删的锚点」；对「候选未接入 → 老形态库」这类夹具更直观，
    并且**天然覆盖候选后代**（旧脚本只 unlink 候选本身 → 后代变孤儿）。
    """
    chain = read_chain(versions_dir)
    if drop not in chain:
        raise RuntimeError(f"revision {drop} not found in {versions_dir}")
    # 反向图：{parent: [children]}
    children: dict[str | None, list[str]] = {}
    for rev, (_path, down) in chain.items():
        children.setdefault(down, []).append(rev)
    # BFS drop 与后代
    to_remove: set[str] = set()
    stack = [drop]
    while stack:
        r = stack.pop()
        if r in to_remove:
            continue
        to_remove.add(r)
        for c in children.get(r, ()):
            stack.append(c)
    removed: set[str] = set()
    for rev in to_remove:
        path = chain[rev][0]
        path.unlink()
        removed.add(rev)
    # 剩余图必须仍然完整：所有 down_revision 要么为 None，要么指向仍在图里的 id。
    remaining = read_chain(versions_dir)
    for rev, (_p, down) in remaining.items():
        if down is not None and down not in remaining:
            raise RuntimeError(
                f"prune_excluding({drop}) 后仍断链：{rev}.down_revision={down} 已不在图中"
            )
    for pyc in list(versions_dir.rglob("*.pyc")):
        pyc.unlink()
    return removed


def append_future(versions_dir: Path, *, new_rev: str, on_head: str,
                  name: str | None = None) -> Path:
    """在指定 head 上追加一个合成未来 revision（noop upgrade/downgrade）。

    **不删除任何已有迁移**——真实链的完整性是测试断言的一部分；只有本函数负责把
    head 前移，形成单头。用于 test_character_id_boundaries_b2b 的伪未来 head 场景。
    """
    chain = read_chain(versions_dir)
    if on_head not in chain:
        raise RuntimeError(f"anchor head {on_head} 不在 {versions_dir} 中")
    # on_head 必须真是当前 head：即没有其他 revision 把它当作 down_revision。
    for rev, (_p, down) in chain.items():
        if down == on_head:
            raise RuntimeError(
                f"anchor {on_head} 不是 head：{rev} 仍以它为父；先确认 head 再追加未来"
            )
    if new_rev in chain:
        raise RuntimeError(f"future revision id {new_rev} 已存在")
    fname = name or f"{new_rev}_synthetic_future.py"
    target = versions_dir / fname
    target.write_text(
        "# synthetic successor for rework test; not part of delivery chain\n"
        f'revision = "{new_rev}"\n'
        f'down_revision = "{on_head}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n"
        "def upgrade():\n    pass\n\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def clone_tree(alembic_src: Path, dst: Path) -> Path:
    """把 backend/alembic 整树拷到 tmp 目录，忽略 __pycache__。"""
    shutil.copytree(alembic_src, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


__all__ = [
    "CONSTRAINT_PARENT",
    "CONSTRAINT_REVISION",
    "ancestors_closure",
    "append_future",
    "clone_tree",
    "prune_excluding",
    "prune_to_boundary",
    "read_chain",
]
