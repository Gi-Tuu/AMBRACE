"""Independent contracts for the new synthetic migration-tree fixture."""
from pathlib import Path
import pytest
import _memory_migration_fixture as fixture


def node(root: Path, rev, parent, *, annotated=False):
    root.mkdir(exist_ok=True)
    p = root / (rev + ".py")
    typ = ": str" if annotated else ""
    p.write_text(f"revision{typ} = {rev!r}\ndown_revision{typ} = {parent!r}\n", encoding="utf-8")
    return p


def test_annotation_ancestor_closure_and_pruning(tmp_path):
    node(tmp_path, "a", None)
    node(tmp_path, "b", "a", annotated=True)
    node(tmp_path, "c", "b")
    assert fixture.ancestors_closure(tmp_path, "b") == {"a", "b"}
    assert fixture.prune_to_boundary(tmp_path, "b") == {"c"}
    assert set(fixture.read_chain(tmp_path)) == {"a", "b"}


def test_missing_parent_is_rejected(tmp_path):
    node(tmp_path, "a", "missing")
    with pytest.raises(RuntimeError):
        fixture.ancestors_closure(tmp_path, "a")


def test_cycle_is_rejected(tmp_path):
    node(tmp_path, "a", "b")
    node(tmp_path, "b", "a")
    with pytest.raises(RuntimeError):
        fixture.ancestors_closure(tmp_path, "a")


def test_duplicate_revision_is_rejected(tmp_path):
    p = node(tmp_path, "a", None)
    (tmp_path / "duplicate.py").write_bytes(p.read_bytes())
    with pytest.raises(RuntimeError):
        fixture.read_chain(tmp_path)


def test_nonliteral_parent_must_not_become_false_root(tmp_path):
    (tmp_path / "bad.py").write_text("revision = 'b'\ndown_revision = UNKNOWN_PARENT\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        fixture.ancestors_closure(tmp_path, "b")


def test_merge_parents_are_preserved_or_explicitly_rejected(tmp_path):
    node(tmp_path, "a", None)
    node(tmp_path, "b", None)
    node(tmp_path, "merge", ("a", "b"))
    try:
        result = fixture.ancestors_closure(tmp_path, "merge")
    except RuntimeError:
        return  # A single-chain fixture may reject a merge, never truncate it.
    assert result == {"a", "b", "merge"}


def test_drop_removes_descendants_and_keeps_ancestors(tmp_path):
    node(tmp_path, "a", None)
    node(tmp_path, "b", "a")
    node(tmp_path, "c", "b")
    assert fixture.prune_excluding(tmp_path, "b") == {"b", "c"}
    assert set(fixture.read_chain(tmp_path)) == {"a"}


def test_future_extends_real_head_and_rejects_nonhead(tmp_path):
    node(tmp_path, "a", None)
    node(tmp_path, "b", "a")
    with pytest.raises(RuntimeError):
        fixture.append_future(tmp_path, new_rev="bad", on_head="a")
    fixture.append_future(tmp_path, new_rev="future", on_head="b")
    assert fixture.ancestors_closure(tmp_path, "future") == {"a", "b", "future"}
