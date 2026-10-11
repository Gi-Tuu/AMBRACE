# -*- coding: utf-8 -*-
"""第七十二批守卫：公开面扫描必须有「隐私词名单」这条腿，且不许把词本身打进日志。

背景＝公开仓的测试夹具里出现过本机隐私名单上的真实角色名（17 个文件 27 处），而原有的两类模式
（作者机器路径／凭据形态）**类别上够不到它**：`1843／1843／0` 是真扫过、但这一类根本没量。
名单文件不入库（CI 上不存在），所以这一类只能靠「推前本机预扫」兜住——因此它必须：
① 命中时只报编号不报词（日志是第二次外泄渠道，CI 日志尤其）；
② 名单不可用时**明写未覆盖**，`--require-names` 直接判无效（延续「没量到 ≠ 干净」）。
"""
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(rel, tag):
    path = REPO / rel
    if not path.is_file():
        pytest.skip(f"{rel} 不在当前检出面")
    spec = importlib.util.spec_from_file_location(tag, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def scanner():
    return _load("scripts/check_public_leak.py", "b72_scanner")


@pytest.fixture(scope="module")
def names(scanner):
    got = scanner.load_privacy_names()
    if not got:
        pytest.skip("名单只在本机（scripts/.privacy_names 不入库）：CI 上这一类无从扫起，属预期跳过")
    return got


def test_命中只报编号不报词(scanner, names):
    data = ('x = "角色名占位"\n' + "name = \"" + names[0].decode("utf-8") + "\"\n").encode("utf-8")
    hits = scanner.scan_blob("sample.py", data, names)
    assert hits, "名单上的词进了公开面内容却没被抓到＝这条腿没牙"
    name, lineno, frag = hits[0]
    assert name == "privacy_name" and lineno == 2
    assert names[0].decode("utf-8") not in frag, "把词本身打进日志＝第二次外泄"
    assert "名单第" in frag and "号词" in frag


def test_名单文件不许进公开面(scanner):
    """一旦有人把名单 commit 上来，扫描器就会拿它去扫自己——必须当场红。"""
    public = scanner.list_public_files(None)
    assert scanner.PRIVACY_NAMES_FILE not in public
    assert "scripts/.privacy_names" in (REPO / ".gitignore").read_text(encoding="utf-8").split("\n"), \
        "名单不在 .gitignore 里＝离入库只差一次 git add"


def test_名单不可用时结论必须明写未覆盖(scanner, monkeypatch, capsys):
    monkeypatch.setattr(scanner, "load_privacy_names", lambda: [])
    monkeypatch.setattr("sys.argv", ["check_public_leak.py", "--files", "README.md"])
    assert scanner.main() == 0
    out = capsys.readouterr().out
    assert "隐私词名单腿＝不可用" in out
    assert "未覆盖" in out, "名单没跑还报「干净」＝把没量到读成干净"


def test_require_names时名单缺失判无效(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "load_privacy_names", lambda: [])
    monkeypatch.setattr("sys.argv", ["check_public_leak.py", "--files", "README.md", "--require-names"])
    assert scanner.main() == 2, "推前预扫加了 --require-names 却因名单缺失而放行＝等于没这条腿"


def test_工作区公开面扫不出名单词(scanner, names):
    """回归锁：即将进公开仓的内容里不许有名单上的词。

    扫工作区而不是 HEAD——改名与这条守卫落在同一个提交里，按 HEAD 扫会自指
    （提交时 HEAD 还是上一版＝本批第一跑必红，而红的是尺子不是代码）。
    推真快照那一步另有 `check_public_leak.py --rev <快照提交> --require-names` 兜住。
    """
    n_files, n_parsed, hits = scanner.scan(None, names)
    assert n_parsed == n_files, f"解析数 {n_parsed} ≠ 请求数 {n_files}＝扫描无效"
    leaked = [h for h in hits if h[0] == "privacy_name"]
    assert not leaked, f"公开面仍有名单词：{leaked[:3]}"


def test_与发布包自检同源一张名单(scanner):
    """make_release.py 的 [NAME] 告警与这里必须读同一个文件，否则两张名单会各自漂移。"""
    src = " ".join((REPO / "scripts" / "make_release.py").read_text(encoding="utf-8").split())
    assert scanner.PRIVACY_NAMES_FILE == "scripts/.privacy_names"
    assert '"scripts", ".privacy_names"' in src, "发布包自检读的不是同一张名单（会一边拦得住一边拦不住）"
