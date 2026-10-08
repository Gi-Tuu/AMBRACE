# -*- coding: utf-8 -*-
"""C2 守卫：公开仓卫生扫描器（`scripts/check_public_leak.py`）。

为什么这条要进 CI 而不是留在手册里：**同一类泄漏两个月内靠手工扫描拦住两次**——
10-06 是测试夹具里的真机绝对路径（`f16ee5d5`），10-08 是模块 **docstring** 里报告文件的绝对路径
（第 116 棒推前才抓到，见 dev-changelog「夜间批」）。两次都是"人记得扫才有"，第三次没人保证。

纪律：不联网、不建库；只读 git 对象与本文件字节。`test_现在的_HEAD_是干净的` 是**回归锁**——
将来谁把本机路径写进会公开的文件，这条先红，不用等到推快照那一刻。
"""
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_public_leak.py"
DOC = REPO / "docs" / "release-public-snapshot.md"


def _load():
    spec = importlib.util.spec_from_file_location("_leakscan_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scan = _load()


def _names(data: bytes):
    return {name for name, _line, _frag in scan.scan_blob("t", data)}


# 夹具本身也必须分片拼——否则这个测试文件就成了它自己要拦的那次泄漏（公开面包含 backend/tests/**）
_AMBRACE = b"AMB" + b"RACE"
_MACHINE = b"Code" + b"x-Projects"
_SHENG = b"she" + b"ng"
_BS = bytes([0x5C])          # 反斜杠：源码里不出现字面量，免得自撞（raw 字符串也没法以 \ 结尾）
_PEM_HEAD = b"-----BEGIN " + b"RSA " + b"PRIVATE KEY-----"


# ── 一、三条作者路径都必须报（正向） ──
def test_作者机器路径三类各自必须报():
    assert _names(b"x = 'D:/" + _AMBRACE + b"/backend/app/x.py'") >= {"author_repo_path"}
    assert _names(rb"path = r'D:" + _BS + _AMBRACE + _BS + rb"docs'") >= {"author_repo_path"}
    assert _names(b'home = "C:' + _BS * 2 + b'Users' + _BS * 2 + _SHENG + _BS * 2 + b'AppData"') >= {"author_home"}
    assert _names(b'ws = "D:/' + _MACHINE + b'/output"') >= {"author_machine_path"}


def test_凭据形态必须报():
    assert _names(_PEM_HEAD) >= {"private_key_pem"}
    assert _names(b'k = "sk-' + b"A" * 24 + b'"') >= {"openai_style_key"}
    assert _names(b't = "ghp_' + b"B" * 24 + b'"') >= {"github_token"}
    assert _names(b"a = AKIA" + b"CCCCCCCC" * 2) >= {"aws_access_key"}
    assert _names(b"s = xoxb-" + b"D" * 12) >= {"slack_token"}


# ── 二、反向钉：不许过度敏感（否则第一天就被 --no-verify 绕掉） ──
def test_合成盘符与示例地址不得报():
    # 10-06 那次为了躲扫描把真机路径换成合成路径，反而制造了 CI 红（见 ci-windows-locale-trap 记忆）
    assert _names(b'cases = [("E:\\\\demo\\\\db.sqlite", "demo")]') == set()
    assert _names(b"host = '192.168.1.8'") == set()
    assert _names(b"url = 'https://example.com/api'") == set()
    assert _names(b"key = 'sk-short'") == set()          # 短串不算凭据
    assert _names(b"Users / home directory concept note, no username here") == set()   # 只有用户名出现才算


# ── 三、公开面口径必须与快照构建脚本一致 ──
def test_公开面判定与排除清单():
    assert scan.is_public("backend/app/agent/runtime.py") is True
    assert scan.is_public("backend/tests/test_x.py") is True
    assert scan.is_public("docs/changelog.md") is True          # 唯一进公开仓的 docs
    assert scan.is_public("docs/plans.md") is False
    assert scan.is_public(".agents/skills/x/SKILL.md") is False
    assert scan.is_public("AGENTS.md") is False
    assert scan.is_public("HANDOFF.md") is False
    assert scan.is_public("flutter_app/.metadata") is False
    assert scan.is_public("flutter.bat") is False


def test_排除清单与脱敏规程文档逐字一致():
    text = DOC.read_text(encoding="utf-8")
    m = re.search(r"EXCL_TOP = \((.*?)\)", text, re.S)
    assert m, "规程文档里找不到 EXCL_TOP——文档改了口径就要同步这里"
    doc_top = tuple(x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip())
    assert set(doc_top) == set(scan.EXCL_TOP), (doc_top, scan.EXCL_TOP)
    m2 = re.search(r"EXCL_EXACT = \((.*?)\)", text, re.S)
    doc_exact = tuple(x.strip().strip("\"'") for x in m2.group(1).split(",") if x.strip())
    assert set(doc_exact) == set(scan.EXCL_EXACT)


# ── 四、扫描器不许自己就是泄漏（模式必须分片拼） ──
def test_扫描器自身不被自己的模式命中():
    src = SCRIPT.read_bytes()
    assert _names(src) == set(), "扫描器自己被自己的模式命中＝模式字面量漏在源码里，CI 会自咬"
    assert _MACHINE not in src and _AMBRACE not in src and _SHENG not in src
    assert not re.search(rb"Code" + rb"x-Projects", src)
    assert not re.search(rb"Users" + rb"[\\\\/]+" + _SHENG, src)


def test_本测试文件自身也不能被扫出来():
    """夹具分片拼对没拼对，用这条验：本文件（也会进公开仓）自己必须 0 命中。"""
    assert _names(Path(__file__).read_bytes()) == set()


# ── 五、cat-file --batch 解析（改名/缺失不能崩） ──
def test_batch_解析按顺序取回内容与大小():
    paths = ["a.txt", "b.txt"]
    raw = (b"1111 blob 5\nhello\n"
           b"2222 blob 7\nworld!!\n")
    assert scan.parse_batch(paths, raw) == [("a.txt", b"hello"), ("b.txt", b"world!!")]


def test_batch_解析容忍缺失对象():
    paths = ["missing.txt", "ok.txt"]
    raw = (b"missing.txt missing\n"
           b"3333 blob 3\nabc\n")
    out = scan.parse_batch(paths, raw)
    assert out and out[-1][1] == b"abc"          # 缺失那行被跳过，后面的仍然取到


# ── 六、回归锁：现在真的没有泄漏 ──
def test_现在的_HEAD_公开面是干净的():
    n, hits = scan.scan("HEAD")
    assert n > 1000, f"公开面文件数异常（{n}）——排除清单或 ls-tree 口径坏了"
    assert hits == [], f"HEAD 里有会外泄的内容：{hits[:5]}"
