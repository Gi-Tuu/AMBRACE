# -*- coding: utf-8 -*-
"""C2 守卫：公开仓卫生扫描器（`scripts/check_public_leak.py`）。

为什么这条要进 CI 而不是留在手册里：**同一类泄漏两个月内靠手工扫描拦住两次**——
10-06 是测试夹具里的真机绝对路径（`f16ee5d5`），10-08 是模块 **docstring** 里报告文件的绝对路径
（第 116 棒推前才抓到，见 dev-changelog「夜间批」）。两次都是"人记得扫才有"，第三次没人保证。

纪律：不联网、不建库；只读 git 对象与本文件字节。`test_现在的_HEAD_是干净的` 是**回归锁**——
将来谁把本机路径写进会公开的文件，这条先红，不用等到推快照那一刻。
"""
import importlib.util
import os
import re
import shutil
from pathlib import Path

import pytest

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


def _read_doc_exclusions(doc: Path):
    """从脱敏规程文档里抠出排除清单；**文档不存在返回 None**（CI 上 docs 不发布，见第 117 棒教训）。"""
    if not doc.exists():
        return None
    text = doc.read_text(encoding="utf-8")
    m = re.search(r"EXCL_TOP = \((.*?)\)", text, re.S)
    assert m, "规程文档里找不到 EXCL_TOP——文档改了口径就要同步这里"
    doc_top = {x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()}
    m2 = re.search(r"EXCL_EXACT = \((.*?)\)", text, re.S)
    doc_exact = {x.strip().strip("\"'") for x in m2.group(1).split(",") if x.strip()}
    return doc_top, doc_exact


def test_排除清单与脱敏规程文档逐字一致():
    got = _read_doc_exclusions(DOC)
    if got is None:
        pytest.skip("脱敏快照不含 docs/release-public-snapshot.md（docs 不进公开仓）⇒ 清单一致性只在本地核")
    doc_top, doc_exact = got
    assert doc_top == set(scan.EXCL_TOP), (sorted(doc_top), scan.EXCL_TOP)
    assert doc_exact == set(scan.EXCL_EXACT), (sorted(doc_exact), scan.EXCL_EXACT)


def test_文档缺席时这条守卫跳过而不是红():
    """第 117 棒的教训：CI 跑的是**裁剪后的树**，`docs/` 根本不存在。
    本地全绿 6841 例、一推上去四个档同时红 1 例——就是这条没做缺席处理。"""
    assert _read_doc_exclusions(REPO / "docs" / "no-such-file-abc.md") is None


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
# ── 五、用户隐私硬原则：真实语料派生内容不得出现在公开面（2026-10-08 用户拍板） ──
REAL_DERIVED = ["scripts/diagnostics/memory_action_cases_real_draft.jsonl"]


@pytest.mark.parametrize("path", REAL_DERIVED)
def test_真实语料派生题集不在公开面(path):
    """源头是真实用户对话/记忆的内容，**换了名字也还是用户数据** ⇒ 一律不进公开仓。

    有牙证明：把这条从排除清单里摘掉，`is_public()` 立刻返回 True（下面这行就是那个反证）。"""
    assert scan.is_public(path) is False, "%s 会进公开仓 ⇒ 违反用户隐私硬原则" % path
    saved = scan.EXCL_EXACT
    try:
        scan.EXCL_EXACT = tuple(x for x in saved if x != path)
        assert scan.is_public(path) is True, "这条断言本身没牙：摘掉排除它却仍不在公开面"
    finally:
        scan.EXCL_EXACT = saved


def test_排除清单文档与扫描器都写着这条隐私排除():
    doc = _read_doc_exclusions(DOC)
    if doc is None:                       # 裁剪树没有 docs/ ⇒ 跳过（第 117 棒口径）
        pytest.skip("脱敏快照无 docs/release-public-snapshot.md")
    for path in REAL_DERIVED:
        assert path in doc[1], "脱敏规程文档的 EXCL_EXACT 里没有这条隐私排除"
        assert path in scan.EXCL_EXACT, "扫描器清单没同步（守卫只比对两边是否一致，这里保证两边都真的有）"


def test_现在的_HEAD_公开面是干净的():
    n_files, n_parsed, hits = scan.scan("HEAD")
    assert n_files > 1000, f"公开面文件数异常（{n_files}）——排除清单或 ls-tree 口径坏了"
    # 分母必须来自"真解析到的文件数"。第五节那两条只喂了合成的 --batch 输出，测的是解析器；
    # 2026-10-09 那次空扫（请求 1802、解析 0、命中 0、报"干净"）正是从这条缝里过去的。
    assert n_parsed == n_files, f"公开面请求 {n_files} 个只解析到 {n_parsed} 个 ⇒ 这条'干净'是空的"
    assert hits == [], f"HEAD 里有会外泄的内容：{hits[:5]}"


# ── 七、端到端：正对照必须走真命令＋真 git，不许再拿合成输出测（2026-10-09 空扫的教训） ──
def _tmp_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """在 pytest 的 tmp_path 里建一棵真 git 仓并提交，返回仓根。"""
    import subprocess

    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.t", "LC_ALL": "C",
           "PYTHONIOENCODING": "utf-8"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
        r = subprocess.run(["git", "-C", str(tmp_path)] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, **env})
        assert r.returncode == 0, r.stderr
    return tmp_path


def _run_cli(repo: Path):
    """把扫描脚本原样复制进那棵临时仓再跑：ROOT 由 `__file__` 推出 ⇒ 落点就是临时仓。"""
    import subprocess
    import sys

    dst = repo / "scripts" / "check_public_leak.py"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(SCRIPT), str(dst))
    return subprocess.run([sys.executable, str(dst), "--rev", "HEAD"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(repo))


def test_端到端正对照_真带泄漏的仓必须报红(tmp_path):
    # 夹具本身分片拼（本文件在公开面上，写整串就成了它自己要拦的那次泄漏）
    leak = "x = r'D:" + chr(0x5C) + _AMBRACE.decode() + chr(0x5C) + "backend'"
    p = _run_cli(_tmp_repo(tmp_path, {"backend/app/a.py": leak,
                                      "backend/app/b.py": "y = 1\n"}))
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 1, f"种了泄漏却 exit={p.returncode}：{out}"
    assert "author_repo_path" in out, out
    assert "已解析 2 个" in out, f"解析数没如实报出来：{out}"


def test_端到端反向钉_同一棵干净仓必须报绿且解析数不为零(tmp_path):
    p = _run_cli(_tmp_repo(tmp_path, {"backend/app/a.py": "y = 1\n",
                                      "docs/private.md": "D:/随便写不外泄"}))
    out = (p.stdout or "") + (p.stderr or "")
    assert p.returncode == 0, f"干净仓却红了：{out}"
    assert "公开面文件 1 个；已解析 1 个；命中 0 处" in out, \
        f"要么没按排除清单裁，要么又空扫了：{out}"


def test_解析数不等于请求数时必须判扫描无效而不是干净(monkeypatch):
    """新加的 exit=2 分支的牙：分母不齐＝这次什么都没量到，不能算通过。"""
    import sys as _s

    monkeypatch.setattr(scan, "scan", lambda rev: (1802, 0, []))
    monkeypatch.setattr(_s, "argv", ["check_public_leak.py"])
    assert scan.main() == 2, "分母不齐仍被判成干净 ⇒ 空扫又会绿"



# ───────────────────────── C4：裁剪树预跑工具的清单必须只有一份 ─────────────────────────

PRUNED = REPO / "scripts" / "check_pruned_tree.py"


def _load_pruned():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_pruned_under_test", str(PRUNED))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_裁剪树工具不许有第二份排除清单():
    """这工具裁的是「CI 那份树」，口径一旦与扫描器分叉，它会**跑出另一个世界的绿**。

    判据看源码形态：不许自己再列一份 EXCL_PREFIX／不许把 docs/AGENTS 这些名字写死成清单，
    且必须真的委托 check_public_leak.is_public。
    """
    src = PRUNED.read_text(encoding="utf-8")
    assert "is_public(" in src, "本例锚点失效：工具里连 is_public 都没有，改法要重看"
    assert "check_public_leak" in src, "没引用唯一真源 check_public_leak"
    assert "EXCL_PREFIX = " not in src, "工具里又列了一份前缀清单 ⇒ 两处会各改各的，口径分叉"
    for name in ("AGENTS.md", "HANDOFF.md"):
        assert '"%s"' % name not in src, "排除名单被抄进了工具源码（%s）" % name


def test_裁剪树工具与扫描器对同一路径判据一致():
    pr = _load_pruned()
    samples = ["backend/app/main.py", "docs/changelog.md", "docs/plans.md", ".agents/skills/x/SKILL.md",
               "AGENTS.md", "HANDOFF.md", "flutter.bat", "README.md",
               "scripts/diagnostics/memory_action_cases_real_draft.jsonl", "flutter_app/.metadata"]
    assert any(scan.is_public(s) is False for s in samples), "样本里一个该排除的都没有 ⇒ 本例没牙"
    for s in samples:
        assert pr.is_public(s) == scan.is_public(s), "路径 %s 两边判据不一致" % s
