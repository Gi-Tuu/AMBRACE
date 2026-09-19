# -*- coding: utf-8 -*-
"""scripts/edit.py 的「按行保留原行尾」单测（2026-09-19 建）。

事故背景（已发生两次）：旧实现只看「文件里有没有出现 CRLF」，只要有一处，就把整份文件
归一为 LF 再全部写回 CRLF。docs/plans.md 是「全文 LF + 恰好 2 行 CRLF」，用 edit.py 改一次
就把 190+ 行全部变成 CRLF（git diff --numstat 从 1/1 变成 198/198），必须再手工修回。

现在的契约：old / new 一律按 LF 形式传入，匹配在 LF 归一化副本上做；但写回只替换命中的
那几段，未命中的行逐字节不变（\\n / \\r\\n / \\r 混排也不统一）。新写入文本的行尾取「被替换
那段原文」的首个行尾，该段没有换行时退回整份文件的主导行尾。
"""
import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_EDIT_PATH = _REPO / "scripts" / "edit.py"


def _load_edit():
    spec = importlib.util.spec_from_file_location("_test_edit_mod", str(_EDIT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


edit = _load_edit()


def _probe(tmp_path, monkeypatch, name: str, data: bytes) -> Path:
    """在 tmp_path 下建探针文件并把模块级 ROOT 指过去（不碰仓库真实文件）。"""
    path = tmp_path / name
    path.write_bytes(data)
    monkeypatch.setattr(edit, "ROOT", tmp_path)
    return path


# ── ① 混合行尾：只有被替换的那一段变，其余行逐字节不动 ──────────────────────
def test_mixed_eol_only_touches_matched_segment(tmp_path, monkeypatch, capsys):
    original = b"a\nb\r\nc\nd\r\ne\n"          # LF / CRLF 混排
    path = _probe(tmp_path, monkeypatch, "mixed.md", original)

    assert edit.apply_patch({"file": "mixed.md", "old": "c\n", "new": "X\nY\n"}) is True

    # 被替换段 "c\n" 的首个换行是 LF -> 新增行也用 LF；前后缀（含两条 CRLF 行）原样保留
    assert path.read_bytes() == b"a\nb\r\nX\nY\nd\r\ne\n"
    assert path.read_bytes().startswith(b"a\nb\r\n")
    assert path.read_bytes().endswith(b"d\r\ne\n")
    assert "已替换 1 处" in capsys.readouterr().out


# ── ② count 校验：不匹配即报错，且不留下半截改动 ────────────────────────────
def test_count_mismatch_raises_without_writing(tmp_path, monkeypatch):
    original = b"a\na\nb\n"
    path = _probe(tmp_path, monkeypatch, "cnt.md", original)

    with pytest.raises(SystemExit) as excinfo:
        edit.apply_patch({"file": "cnt.md", "old": "a", "new": "z"})
    assert "期望匹配 1 处，实际 2 处" in str(excinfo.value)
    assert path.read_bytes() == original                    # 未写入

    # 显式 count 仍可覆盖校验（语义与旧版一致）
    assert edit.apply_patch({"file": "cnt.md", "old": "a\n", "new": "z\n", "count": 2}) is True
    assert path.read_bytes() == b"z\nz\nb\n"


# ── ③ 幂等：内容一致时返回 False + 打印「无变化」，字节不变 ──────────────────
def test_idempotent_no_change(tmp_path, monkeypatch, capsys):
    original = b"a\nb\r\nc\n"
    path = _probe(tmp_path, monkeypatch, "idem.md", original)
    patch = {"file": "idem.md", "old": "b", "new": "b"}

    assert edit.apply_patch(patch) is False
    out = capsys.readouterr().out
    assert "无变化" in out
    assert edit.apply_patch(patch) is False                 # 同一 patch 再执行一次
    assert "无变化" in capsys.readouterr().out
    assert path.read_bytes() == original


# ── ④ 回归锚：全文 LF + 1 行 CRLF（plans.md 形状）不得被整份改写 ─────────────
def test_plans_md_shape_keeps_crlf_count(tmp_path, monkeypatch, capsys):
    body = "".join(f"- 第{i}条说明\n" for i in range(200))
    original = body.replace("- 第5条说明\n", "- 第5条说明\r\n").encode("utf-8")
    assert original.count(b"\r\n") == 1
    path = _probe(tmp_path, monkeypatch, "plans.md", original)

    assert edit.apply_patch({"file": "plans.md", "old": "- 第100条说明\n",
                             "new": "- 第100条说明（已更新）\n"}) is True

    after = path.read_bytes()
    # 旧实现在这里会得到 200 个 CRLF（整份改写）；现在必须恰好还是 1
    assert after.count(b"\r\n") == 1
    assert after.count(b"\n") == original.count(b"\n")
    assert after == original.replace("- 第100条说明\n".encode("utf-8"),
                                     "- 第100条说明（已更新）\n".encode("utf-8"))
    out = capsys.readouterr().out
    assert "按行保留原行尾" in out and "主导=LF" in out and "含 CRLF=True" in out


# ── ⑤ 被替换段本身是 CRLF 行：新插入的行也必须用 CRLF（哪怕主导行尾是 LF）────
def test_crlf_segment_inserts_crlf_lines(tmp_path, monkeypatch):
    # CRLF 与 LF 各 2 处 -> 主导行尾并列取 LF；但 c 行自带 CRLF，必须优先按段内行尾
    original = b"a\nb\r\nc\r\nd\n"
    assert edit._dominant_eol(original.decode("utf-8")) == "\n"
    path = _probe(tmp_path, monkeypatch, "crlf.md", original)

    assert edit.apply_patch({"file": "crlf.md", "old": "b\n", "new": "x\ny\n"}) is True

    assert path.read_bytes() == b"a\nx\r\ny\r\nc\r\nd\n"
    assert path.read_bytes().count(b"\r\n") == 3            # 只多出插入的那一行


# ── ⑥ 段内没有换行：退回整份文件的主导行尾 ─────────────────────────────────
def test_segment_without_newline_uses_dominant_eol(tmp_path, monkeypatch):
    original = b"a\nb\r\nc\r\nd\r\n"          # CRLF 主导
    path = _probe(tmp_path, monkeypatch, "dom.md", original)

    assert edit.apply_patch({"file": "dom.md", "old": "c", "new": "X\nY"}) is True

    assert path.read_bytes() == b"a\nb\r\nX\r\nY\r\nd\r\n"
