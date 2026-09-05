# -*- coding: utf-8 -*-
"""P3-6 双副本一致性护栏：插件示例（源码）与运行副本（data/plugins）逐字节一致。

背景：``backend/data/plugins/`` 不进 git（见 .gitignore），是运行时加载路径；
``plugins/examples/wechat_ilink/`` 是源码仓库内的权威副本。
若只改源码而不同步运行副本，就会出现「源码新、运行旧」——改动不生效且无告警。
本测试对同名 .py/.json 比对 sha256，缺文件或内容不一致直接失败。

注意：data/plugins 不入 git，故运行副本可能落后于源码；本测试正是要守住这一点。
"""
import hashlib
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "plugins" / "examples" / "wechat_ilink"
_RUNTIME = _REPO_ROOT / "backend" / "data" / "plugins" / "wechat_ilink"

_SUFFIXES = (".py", ".json")


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_examples_and_runtime_plugin_copy_identical():
    """源码副本与运行副本的同名文件必须 sha256 一致；缺文件/不一致直接失败。"""
    assert _EXAMPLES.is_dir(), f"示例目录不存在: {_EXAMPLES}"
    if not _RUNTIME.is_dir():
        pytest.skip(
            "运行副本目录不存在（backend/data/plugins 为 gitignored 运行时副本，"
            "CI 检出无此目录；本测试在本地/部署机守护双副本一致，CI 无副本自动跳过）"
        )

    source_files = {
        f.name: f
        for f in _EXAMPLES.iterdir()
        if f.is_file() and f.suffix in _SUFFIXES
    }
    assert source_files, "示例目录未找到任何 .py/.json 文件"

    missing = [name for name in source_files if not (_RUNTIME / name).is_file()]
    assert not missing, f"运行副本缺文件: {missing}"

    mismatched = [
        name for name, src in source_files.items()
        if _sha256(src) != _sha256(_RUNTIME / name)
    ]
    assert not mismatched, (
        f"运行副本与源码不一致（源码新/运行旧，须同步 plugins/examples -> backend/data/plugins）: {mismatched}"
    )
