# -*- coding: utf-8 -*-
"""第七十一批守卫：发布包只带「代码真会加载」的模型；下载脚本不许拼造不存在的附件直链。

口径（2026-10-11 用户拍板）：完整包 `ai_companion_public.zip` **只在某个版本的第一次发布时挂**，
同版本的后续修订棒/换附件不重挂。于是两件事必须成立——
① 发布包里不许再混入无人加载的死模型（体积要拿去上传，报出来的数还得是真的）；
② `download_models.py` 的「方式① 取 latest Release 的 zip」在**没有附件时是预期状态**，
   必须直接跳到 HuggingFace，而不是按 tag 拼一个必然 404 的 URL 去空跑。
"""
import importlib.util
import io
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load_script(rel):
    path = REPO / rel
    if not path.is_file():
        pytest.skip(f"{rel} 不在当前检出面（公开仓裁剪后可能缺该脚本）")
    spec = importlib.util.spec_from_file_location("b71_" + path.stem, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def make_release():
    return _load_script("scripts/make_release.py")


@pytest.fixture(scope="module")
def download_models():
    return _load_script("scripts/download_models.py")


def _tree(root, files):
    for rel, size in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)


def _sum_bytes(root):
    return sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(root) for f in fs)


class _FakeResp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_死模型不进发布包(make_release, tmp_path):
    """bge-small-zh-v1.5 与 .git/LFS 缓存必须被排除，bge-m3 与 whisper 照常随包。"""
    src = tmp_path / "models"
    _tree(src, {
        "bge-m3/tokenizer.json": 1024,
        "bge-m3/onnx/model_int8.onnx": 4096,
        "bge-m3/.git/lfs/objects/dead": 8192,          # LFS 缓存：不是模型本体
        "whisper-small/model.bin": 2048,
        "bge-small-zh-v1.5/onnx/model.onnx": 16384,     # 无人加载的死模型
    })
    dst = tmp_path / "out" / "backend" / "models"
    total, landed = make_release.bundle_models(src, dst)

    assert not (dst / "bge-small-zh-v1.5").exists(), "死模型被打包了"
    assert (dst / "bge-m3/onnx/model_int8.onnx").is_file()
    assert (dst / "whisper-small/model.bin").is_file()
    assert not any(".git" in Path(r).parts for r, _, _ in os.walk(dst)), ".git/LFS 缓存漏进包"
    names = " ".join(landed)
    assert "bge-m3" in names and "whisper-small" in names and "bge-small" not in names


def test_随包体积读数来自落包文件而非源目录(make_release, tmp_path):
    """反向钉：按源目录累加会把死模型与 LFS 一起报出来（包里没有那些字节）。

    2026-10-13 起 bundle_models 还会往每个权重子目录放一份**上游原许可**
    （AGPL 下再分发要随附），所以期望值 = 权重字节 + 许可字节；不变式
    「读数 == 已落包文件之和，且不含死模型/LFS」照旧。
    """
    src = tmp_path / "models"
    _tree(src, {
        "bge-m3/onnx/model_int8.onnx": 1024,
        "bge-m3/.git/lfs/objects/dead": 4096,
        "bge-small-zh-v1.5/onnx/model.onnx": 8192,
    })
    dst = tmp_path / "out" / "backend" / "models"
    total, _ = make_release.bundle_models(src, dst)

    landed_license = next(
        int(p.stat().st_size)
        for p in (dst / "bge-m3").iterdir()
        if "LICENSE" in p.name.upper() or "COPYING" in p.name.upper()
    )
    assert total == _sum_bytes(dst) == 1024 + landed_license
    assert total < _sum_bytes(src), "读数按源目录累加＝发布包体积在报假数"
    assert not (dst / "bge-small-zh-v1.5").exists(), "死模型被打包了"
    assert not any(".git" in Path(r).parts for r, _, _ in os.walk(dst)), ".git/LFS 缓存漏进包"


def test_latest无完整包附件时返回空串(download_models, monkeypatch):
    """预期状态：latest 只有 APK ⇒ 返回空串（历史上这里拼造直链，导致每次空跑一轮下载）。"""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req)
        return _FakeResp({"tag_name": "v9.9.9", "assets": [{"name": "app-release.apk"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert download_models._latest_release_zip_url() == ""
    assert len(calls) == 1


def test_有附件时用附件自带直链(download_models, monkeypatch):
    url = "https://github.com/Gi-Tuu/AMBRACE/releases/download/v9.9.9/ai_companion_public.zip"

    def fake_urlopen(req, timeout=None):
        return _FakeResp({"tag_name": "v9.9.9",
                          "assets": [{"name": "app-release.apk"},
                                     {"name": "ai_companion_public.zip",
                                      "browser_download_url": url}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert download_models._latest_release_zip_url() == url


def test_源码里不许有拼造直链那行(download_models):
    """变异自测的反向钉：删掉「按 tag 拼标准直链」这行才是修法，写回去就红。"""
    src = (REPO / "scripts" / "download_models.py").read_text(encoding="utf-8")
    assert "releases/download/{tag}" not in src, "又退回拼造 404 直链的旧写法"


def test_排除清单与规程口径一致(make_release):
    src = (REPO / "scripts" / "make_release.py").read_text(encoding="utf-8")
    assert "bge-small-zh-v1.5" in make_release.MODELS_EXCLUDE
    assert "MODELS_EXCLUDE" in src and "bundle_models" in src
    # 排除项要能被 copytree 的 ignore 真正吃到（漏接＝清单形同虚设）
    import inspect
    body = inspect.getsource(make_release.bundle_models)
    assert "MODELS_EXCLUDE" in body


def test_规程登记了首发挂zip():
    doc = REPO / "docs" / "release-public-snapshot.md"
    if not doc.is_file():
        pytest.skip("docs/ 不在公开面（CI 检出的裁剪树没有这份规程）")
    text = doc.read_text(encoding="utf-8")
    assert "ai_companion_public.zip" in text
    assert "首次发布" in text, "zip 只在版本首发时挂的口径没写进规程"
