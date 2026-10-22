# -*- coding: utf-8 -*-
"""安全 B1-C：模型下载/解压完整性与安装回滚（SEC-P1-1）合成测试。

所有 ZIP/下载内容都在 tmp_path 内合成；urllib 只用内存桩，绝不联网；
MODEL_DIR 被重定向到 tmp_path，绝不触碰本机真实模型目录。

测试先于修复编写：旧实现会截断 >4MiB 成员、忽略 ai_companion_public/ 前缀、
接受 ../ 成员，并直接写目标目录。
"""
import builtins
import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_models.py"
_spec = importlib.util.spec_from_file_location("ambrace_download_models_b1", SCRIPT)
dm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dm)

PREFIX_A = "backend/models/bge-m3/"
PREFIX_B = "ai_companion_public/backend/models/bge-m3/"
TOKENIZER = b'{"tokenizer":"synthetic"}'
BIG_ONNX = b"ONNX-HEADER-" + (b"x" * (5 * 1024 * 1024))


@pytest.fixture(autouse=True)
def _guard_outside_writes(tmp_path, monkeypatch):
    """即使被测旧实现有路径穿越，也绝不允许写 tmp_path 之外的真实文件。"""
    root = tmp_path.resolve()
    real_open = builtins.open

    def _guarded_open(file, *args, **kwargs):
        try:
            p = Path(file).resolve()
        except TypeError:
            return real_open(file, *args, **kwargs)
        if p != root and root not in p.parents:
            raise AssertionError(f"outside write attempted: {p}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _guarded_open)
    yield


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_hash(path: Path) -> str:
    return _hash(path.read_bytes())


@pytest.fixture()
def model_env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    model_dir = root / "backend" / "models" / "bge-m3"
    monkeypatch.setattr(dm, "ROOT", root)
    monkeypatch.setattr(dm, "MODEL_DIR", model_dir)

    def _no_network(*args, **kwargs):
        raise AssertionError("测试禁止真实网络访问")

    monkeypatch.setattr(dm.urllib.request, "urlopen", _no_network)
    return {"root": root, "model_dir": model_dir}


def _make_zip(path: Path, members, *, stored=True) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return path


def _model_zip(path: Path, prefix=PREFIX_A, *, tokenizer=TOKENIZER, onnx=BIG_ONNX, extra=()) -> Path:
    members = [
        (prefix + "tokenizer.json", tokenizer),
        (prefix + "onnx/model_int8.onnx", onnx),
        *extra,
    ]
    return _make_zip(path, members)


def _seed_model(model_dir: Path, tokenizer=b"OLD-TOKENIZER", onnx=b"OLD-ONNX") -> dict:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "tokenizer.json").write_bytes(tokenizer)
    (model_dir / "onnx").mkdir(exist_ok=True)
    (model_dir / "onnx" / "model_int8.onnx").write_bytes(onnx)
    return {"tokenizer": _hash(tokenizer), "onnx": _hash(onnx)}


def _staging(model_dir: Path, tokenizer=TOKENIZER, onnx=BIG_ONNX) -> Path:
    staging = model_dir.parent / ".manual-staging" / "model"
    (staging / "onnx").mkdir(parents=True, exist_ok=True)
    (staging / "tokenizer.json").write_bytes(tokenizer)
    (staging / "onnx" / "model_int8.onnx").write_bytes(onnx)
    return staging


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return proc.returncode == 0 and link.exists()


def _seed_junction_model(model_env, tmp_path):
    """模型根的 onnx 是指向项目外兄弟目录的 junction；外侧有哨兵字节。"""
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    (model_dir / "tokenizer.json").write_bytes(b"OLD-TOKENIZER")
    outside = tmp_path / "outside_target"
    outside.mkdir()
    (outside / "model_int8.onnx").write_bytes(b"OUTSIDE-SENTINEL")
    (outside / "keep.txt").write_bytes(b"OUTSIDE-KEEP")
    if not _make_junction(model_dir / "onnx", outside):
        pytest.skip("Windows junction unavailable on this filesystem")
    # 先证明 junction 真能把模型路径解析到外侧，再运行被测代码。
    resolved = Path(os.path.realpath(model_dir / "onnx" / "model_int8.onnx")).resolve()
    assert resolved == (outside / "model_int8.onnx").resolve()
    return model_dir, outside


def _make_backend_junction(model_env, tmp_path, *, with_model: bool):
    """ROOT/backend 是指向 ROOT 外兄弟目录的 junction；模型路径的祖先越界。"""
    root = model_env["root"]
    outside = tmp_path / "outside"
    if with_model:
        (outside / "models" / "bge-m3" / "onnx").mkdir(parents=True)
        (outside / "models" / "bge-m3" / "tokenizer.json").write_bytes(b"OLD-TOKENIZER")
        (outside / "models" / "bge-m3" / "onnx" / "model_int8.onnx").write_bytes(b"OUTSIDE-SENTINEL")
    else:
        outside.mkdir()
    if not _make_junction(root / "backend", outside):
        pytest.skip("Windows junction unavailable on this filesystem")
    resolved = Path(os.path.realpath(root / "backend")).resolve()
    assert resolved == outside.resolve()
    if with_model:
        assert Path(os.path.realpath(model_env["model_dir"])).resolve() == (outside / "models" / "bge-m3").resolve()
    return outside


# ------------------------------------------------------------------ model_complete

def test_model_complete_rejects_empty_files(model_env):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    (model_dir / "tokenizer.json").write_bytes(b"")
    (model_dir / "onnx").mkdir()
    (model_dir / "onnx" / "model_int8.onnx").write_bytes(b"")
    assert dm.model_complete() is False, "仅存在但空文件不能算完整"


# ------------------------------------------------------------------ 两种合法布局 / 大成员完整性

@pytest.mark.parametrize("prefix", [PREFIX_A, PREFIX_B])
def test_legal_layouts_copy_full_large_member(model_env, tmp_path, prefix):
    model_dir = model_env["model_dir"]
    zpath = _model_zip(tmp_path / "release.zip", prefix)

    assert dm._extract_zip_models(zpath) is True
    assert dm.model_complete() is True
    onnx = model_dir / "onnx" / "model_int8.onnx"
    assert onnx.stat().st_size == len(BIG_ONNX), ">4MiB 成员必须完整复制，不能只写 4MiB"
    assert _file_hash(onnx) == _hash(BIG_ONNX)


def test_both_roots_rejected_as_ambiguous(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    zpath = _make_zip(tmp_path / "both.zip", [
        (PREFIX_A + "tokenizer.json", TOKENIZER),
        (PREFIX_A + "onnx/model_int8.onnx", BIG_ONNX),
        (PREFIX_B + "tokenizer.json", TOKENIZER),
        (PREFIX_B + "onnx/model_int8.onnx", BIG_ONNX),
    ])
    assert dm._extract_zip_models(zpath) is False
    assert not (model_dir / "tokenizer.json").exists()


# ------------------------------------------------------------------ 必需文件缺失/空/损坏/中途失败

def test_missing_required_file_keeps_target_unchanged(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _make_zip(tmp_path / "missing.zip", [(PREFIX_A + "tokenizer.json", TOKENIZER)])

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()
    assert not (model_dir / "onnx" / "model_int8.onnx").exists()


def test_empty_required_file_rejected(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _model_zip(tmp_path / "empty.zip", onnx=b"")

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "onnx" / "model_int8.onnx").exists()


def test_crc_corruption_rejected(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _model_zip(tmp_path / "crc.zip", onnx=b"CRC-MARKER-" + b"c" * 4096)
    raw = bytearray(zpath.read_bytes())
    pos = raw.find(b"CRC-MARKER-")
    assert pos > 0
    raw[pos] ^= 0xFF
    zpath.write_bytes(bytes(raw))

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()


def test_mid_read_failure_does_not_publish_partial(model_env, tmp_path, monkeypatch):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _model_zip(tmp_path / "midread.zip")

    def _boom(zf, info, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial")
        raise OSError("injected mid-read failure")

    monkeypatch.setattr(dm, "_copy_zip_member", _boom)
    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()
    assert not (model_dir / "onnx" / "model_int8.onnx").exists()


# ------------------------------------------------------------------ 路径穿越/绝对/盘符/UNC/反斜杠

@pytest.mark.parametrize("bad_rel", [
    "../../evil.txt",
    "..\\..\\evil.txt",
    "C:/evil.txt",
    "D:\\evil.txt",
    "/server/share/evil.txt",
])
def test_path_escape_rejected_with_no_outside_write(model_env, tmp_path, bad_rel):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _model_zip(tmp_path / "escape.zip", extra=[(PREFIX_A + bad_rel, b"pwn")])

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (tmp_path / "evil.txt").exists()
    assert not (model_env["root"] / "evil.txt").exists()
    assert not (model_env["root"] / "backend" / "models" / "evil.txt").exists()
    assert not (model_dir / "tokenizer.json").exists()


def test_symlink_member_rejected(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(PREFIX_A + "tokenizer.json", TOKENIZER)
        zf.writestr(PREFIX_A + "onnx/model_int8.onnx", BIG_ONNX)
        link = zipfile.ZipInfo(PREFIX_A + "link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "tokenizer.json")

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()


def test_duplicate_normalized_target_rejected(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    zpath = _make_zip(tmp_path / "dup.zip", [
        (PREFIX_A + "tokenizer.json", TOKENIZER),
        (PREFIX_A + "TOKENIZER.JSON", b'{"other":1}'),
        (PREFIX_A + "onnx/model_int8.onnx", BIG_ONNX),
    ])

    assert dm._extract_zip_models(zpath) is False
    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()


# ------------------------------------------------------------------ 安装回滚

def test_force_install_second_file_failure_restores_old(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)
    staging = _staging(model_dir, tokenizer=b"NEW-TOKENIZER", onnx=b"NEW-ONNX")
    calls = {"n": 0}
    real_copy = dm._copy_file

    def _fail_second(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected install failure")
        return real_copy(src, dst)

    monkeypatch.setattr(dm, "_copy_file", _fail_second)
    with pytest.raises(OSError):
        dm._install_staged_model(staging)

    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]
    assert not list(model_dir.parent.glob(".bge-m3-backup-*"))


def test_new_install_second_file_failure_removes_published(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    model_dir.mkdir(parents=True)
    sentinel = model_dir / "sentinel.txt"
    sentinel.write_bytes(b"keep")
    staging = _staging(model_dir, tokenizer=b"NEW-TOKENIZER", onnx=b"NEW-ONNX")
    calls = {"n": 0}
    real_copy = dm._copy_file

    def _fail_second(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("injected install failure")
        return real_copy(src, dst)

    monkeypatch.setattr(dm, "_copy_file", _fail_second)
    with pytest.raises(OSError):
        dm._install_staged_model(staging)

    assert sentinel.read_bytes() == b"keep"
    assert not (model_dir / "tokenizer.json").exists()
    assert not (model_dir / "onnx" / "model_int8.onnx").exists()
    assert dm.model_complete() is False
    assert not list(model_dir.parent.glob(".bge-m3-backup-*"))


def test_old_complete_model_bad_zip_unchanged(model_env, tmp_path):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)
    zpath = _make_zip(tmp_path / "bad.zip", [(PREFIX_A + "tokenizer.json", TOKENIZER)])

    assert dm._extract_zip_models(zpath) is False
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]


# ------------------------------------------------------------------ 既有 junction / Windows 别名

@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_existing_junction_in_model_root_rejected(model_env, tmp_path):
    """R3：模型根的 onnx 是指向根外的 junction 时，合法 ZIP 也不得写穿到外侧。"""
    model_dir, outside = _seed_junction_model(model_env, tmp_path)
    zpath = _model_zip(tmp_path / "release.zip")

    assert dm._extract_zip_models(zpath) is False
    assert (outside / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"
    assert (outside / "keep.txt").read_bytes() == b"OUTSIDE-KEEP"
    assert (model_dir / "tokenizer.json").read_bytes() == b"OLD-TOKENIZER"
    assert (model_dir / "onnx" / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"
    assert not list(model_dir.parent.glob(".bge-m3-backup-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_existing_junction_at_model_parent_rejected(model_env, tmp_path):
    """R3：模型父目录（backend/models）本身是 junction 时也必须失败关闭。"""
    root = model_env["root"]
    (root / "backend").mkdir(parents=True)
    outside_models = tmp_path / "outside_models"
    (outside_models / "bge-m3" / "onnx").mkdir(parents=True)
    (outside_models / "bge-m3" / "tokenizer.json").write_bytes(b"OLD-TOKENIZER")
    (outside_models / "bge-m3" / "onnx" / "model_int8.onnx").write_bytes(b"OUTSIDE-SENTINEL")
    if not _make_junction(root / "backend" / "models", outside_models):
        pytest.skip("Windows junction unavailable on this filesystem")
    resolved = Path(os.path.realpath(model_env["model_dir"])).resolve()
    assert resolved == (outside_models / "bge-m3").resolve()

    assert dm._extract_zip_models(_model_zip(tmp_path / "release.zip")) is False
    assert (outside_models / "bge-m3" / "tokenizer.json").read_bytes() == b"OLD-TOKENIZER"
    assert (outside_models / "bge-m3" / "onnx" / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_backend_junction_outside_rejected(model_env, tmp_path):
    """R3 补：ROOT/backend 是 junction 时，祖先 realpath 必须让合法 ZIP 失败关闭。"""
    outside = _make_backend_junction(model_env, tmp_path, with_model=True)
    zpath = _model_zip(tmp_path / "release.zip")

    assert dm._extract_zip_models(zpath) is False
    assert (outside / "models" / "bge-m3" / "tokenizer.json").read_bytes() == b"OLD-TOKENIZER"
    assert (outside / "models" / "bge-m3" / "onnx" / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"
    assert not list(outside.rglob(".bge-m3-*"))


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_backend_junction_missing_model_dir_not_created_outside(model_env, tmp_path):
    """R3 补：模型目录缺失时，祖先校验必须先于 mkdir，不能先往根外建目录再拒绝。"""
    outside = _make_backend_junction(model_env, tmp_path, with_model=False)
    zpath = _model_zip(tmp_path / "release.zip")

    assert dm._extract_zip_models(zpath) is False
    assert not (outside / "models").exists(), "越界位置不得被 mkdir 创建"
    assert not list(outside.rglob(".bge-m3-*"))
    assert (model_env["root"] / "backend").exists()


def test_normal_missing_model_dir_created_under_root(model_env, tmp_path):
    """R3 正对照：无链接且模型目录缺失时，正常新装仍应在 ROOT 内创建。"""
    zpath = _model_zip(tmp_path / "release.zip")

    assert dm._extract_zip_models(zpath) is True
    model_dir = model_env["model_dir"]
    assert (model_dir / "tokenizer.json").read_bytes() == TOKENIZER
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == _hash(BIG_ONNX)
    assert model_env["root"].resolve() in model_dir.resolve().parents


def test_trailing_dot_alias_rejected_before_publish(model_env, tmp_path):
    """R4：onnx./model... 与 onnx/model... 在 Windows 同目标，必须在发布前拒绝。"""
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)
    zpath = _model_zip(
        tmp_path / "dot.zip",
        extra=[(PREFIX_A + "onnx./model_int8.onnx", b"ALIASED")],
    )

    assert dm._extract_zip_models(zpath) is False
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]
    assert not list(model_dir.parent.glob(".bge-m3-backup-*"))


@pytest.mark.parametrize("bad_rel", [
    "onnx./model_int8.onnx",
    "onnx /model_int8.onnx",
    "onnx../model_int8.onnx",
    "CON/model_int8.onnx",
    "aux.txt",
    "onnx:ads/model_int8.onnx",
    "bad?/model_int8.onnx",
])
def test_windows_alias_components_rejected(model_env, tmp_path, bad_rel):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)
    zpath = _model_zip(
        tmp_path / "alias.zip",
        extra=[(PREFIX_A + bad_rel, b"ALIASED")],
    )

    assert dm._extract_zip_models(zpath) is False
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]
    assert not (model_dir / "aux.txt").exists()
    assert not list(model_dir.parent.glob(".bge-m3-backup-*"))


# ------------------------------------------------------------------ main：默认跳过 / --force / 回退

def test_default_complete_model_skips_downloads(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)

    monkeypatch.setattr(dm, "_download_from_release", lambda: (_ for _ in ()).throw(AssertionError("no release")))
    monkeypatch.setattr(dm, "_download_from_huggingface", lambda: (_ for _ in ()).throw(AssertionError("no hf")))
    monkeypatch.setattr(sys, "argv", ["download_models.py"])

    assert dm.main() == 0
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]


def test_force_success_replaces_model(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    _seed_model(model_dir)

    def _fake_release():
        dm._install_staged_model(_staging(model_dir, tokenizer=b"FORCED-TOKENIZER", onnx=b"FORCED-ONNX"))
        return True

    monkeypatch.setattr(dm, "_download_from_release", _fake_release)
    monkeypatch.setattr(sys, "argv", ["download_models.py", "--force"])

    assert dm.main() == 0
    assert (model_dir / "tokenizer.json").read_bytes() == b"FORCED-TOKENIZER"
    assert (model_dir / "onnx" / "model_int8.onnx").read_bytes() == b"FORCED-ONNX"


def test_force_failure_restores_old_and_returns_nonzero(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)

    def _fake_release():
        staging = _staging(model_dir, tokenizer=b"NEW", onnx=b"NEW")
        calls = {"n": 0}
        real_copy = dm._copy_file

        def _fail_second(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("injected")
            return real_copy(src, dst)

        monkeypatch.setattr(dm, "_copy_file", _fail_second)
        try:
            dm._install_staged_model(staging)
        except OSError:
            return False
        return True

    monkeypatch.setattr(dm, "_download_from_release", _fake_release)
    monkeypatch.setattr(dm, "_download_from_huggingface", lambda: False)
    monkeypatch.setattr(sys, "argv", ["download_models.py", "--force"])

    assert dm.main() == 1
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]


def test_release_missing_falls_back_to_huggingface(model_env, monkeypatch):
    model_dir = model_env["model_dir"]

    def _fake_hf():
        dm._install_staged_model(_staging(model_dir))
        return True

    monkeypatch.setattr(dm, "_latest_release_zip_url", lambda: "")
    monkeypatch.setattr(dm, "_download_from_huggingface", _fake_hf)
    monkeypatch.setattr(sys, "argv", ["download_models.py"])

    assert dm.main() == 0
    assert dm.model_complete() is True
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == _hash(BIG_ONNX)


# ------------------------------------------------------------------ HF 流式下载（urllib 内存桩）

class _FakeResponse:
    def __init__(self, chunks, headers=None, drop_after_chunks=None):
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.drop_after_chunks = drop_after_chunks
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        if self.drop_after_chunks is not None and self.reads >= self.drop_after_chunks:
            raise OSError("connection dropped")
        if self.reads >= len(self.chunks):
            return b""
        chunk = self.chunks[self.reads]
        self.reads += 1
        return chunk


def test_download_rejects_truncated_content_length(model_env, tmp_path, monkeypatch):
    resp = _FakeResponse([b"short"], headers={"Content-Length": "100"})
    monkeypatch.setattr(dm.urllib.request, "urlopen", lambda *args, **kwargs: resp)

    with pytest.raises(Exception):
        dm._download("https://example.invalid/model", tmp_path / "out.bin")


def test_hf_midstream_failure_keeps_old_complete_model(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    old = _seed_model(model_dir)

    def _urlopen(*args, **kwargs):
        return _FakeResponse([b"partial"], headers={"Content-Length": "100"}, drop_after_chunks=1)

    monkeypatch.setattr(dm.urllib.request, "urlopen", _urlopen)
    assert dm._download_from_huggingface() is False
    assert _file_hash(model_dir / "tokenizer.json") == old["tokenizer"]
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == old["onnx"]


def test_hf_streaming_success_installs_model(model_env, monkeypatch):
    model_dir = model_env["model_dir"]
    tokenizer = b'{"hf":"tokenizer"}'
    onnx = b"HF-ONNX-" + b"y" * (5 * 1024 * 1024)

    def _urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        data = tokenizer if url.endswith("tokenizer.json") else onnx
        return _FakeResponse([data[:1024], data[1024:]], headers={"Content-Length": str(len(data))})

    monkeypatch.setattr(dm.urllib.request, "urlopen", _urlopen)
    assert dm._download_from_huggingface() is True
    assert (model_dir / "tokenizer.json").read_bytes() == tokenizer
    assert _file_hash(model_dir / "onnx" / "model_int8.onnx") == _hash(onnx)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_hf_install_rejects_existing_junction(model_env, tmp_path, monkeypatch):
    """R3：HF 与 ZIP 共用安装路径，junction 越界同样必须失败关闭。"""
    model_dir, outside = _seed_junction_model(model_env, tmp_path)
    tokenizer = b'{"hf":"tokenizer"}'
    onnx = b"HF-ONNX-" + b"y" * 1024

    def _urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        data = tokenizer if url.endswith("tokenizer.json") else onnx
        return _FakeResponse([data], headers={"Content-Length": str(len(data))})

    monkeypatch.setattr(dm.urllib.request, "urlopen", _urlopen)
    assert dm._download_from_huggingface() is False
    assert (outside / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"
    assert (outside / "keep.txt").read_bytes() == b"OUTSIDE-KEEP"
    assert (model_dir / "tokenizer.json").read_bytes() == b"OLD-TOKENIZER"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_hf_backend_junction_rejected(model_env, tmp_path, monkeypatch):
    """R3 补：HF 与 ZIP 共用祖先路径校验，backend junction 同样失败关闭。"""
    outside = _make_backend_junction(model_env, tmp_path, with_model=True)
    tokenizer = b'{"hf":"tokenizer"}'
    onnx = b"HF-ONNX-" + b"y" * 1024

    def _urlopen(req, timeout=None):
        url = getattr(req, "full_url", str(req))
        data = tokenizer if url.endswith("tokenizer.json") else onnx
        return _FakeResponse([data], headers={"Content-Length": str(len(data))})

    monkeypatch.setattr(dm.urllib.request, "urlopen", _urlopen)
    assert dm._download_from_huggingface() is False
    assert (outside / "models" / "bge-m3" / "tokenizer.json").read_bytes() == b"OLD-TOKENIZER"
    assert (outside / "models" / "bge-m3" / "onnx" / "model_int8.onnx").read_bytes() == b"OUTSIDE-SENTINEL"
    assert not list(outside.rglob(".bge-m3-*"))
