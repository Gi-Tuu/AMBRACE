# -*- coding: utf-8 -*-
"""发版 CI 守卫（2026-10-13，run 38405243473）：法务文本在 Windows 检出必须逐字节不变。

红在 Windows quick job（同文件在 Linux/py3.14 是绿的）：

- `test_release_license_contract.py::test_根LICENSE逐字节等于官方AGPL3全文` → `35184（官方全文 34523）`
- `test_release_license_contract.py::test_原许可副本的MANIFEST逐条对得上` → 逐条「字节数不符」
- `test_release_license_contract.py::test_收集脚本离线可校验` → `cmd_verify()` 列出 24 条 sha256/字节不符

根因是**检出**这一步、不是仓库对象库：托管 Windows runner 的 git 全局配置默认
`core.autocrlf=true`，而仓库根此前没有 `.gitattributes`（`git check-attr text -- LICENSE`
= `unspecified`）⇒ LF 检出成 CRLF。本地磁盘上 `LICENSE` 是 34523 字节 / 661 个裸 LF，
34523 + 661 正好等于 CI 报的 35184；本机 `core.autocrlf=false`，所以本机永远复现不出来。

本文件钉三条（只加检出元数据 + 测试，不改许可原文、不改 MANIFEST、不改生产代码）：

① **反向钉**：没有 attrs 时，`core.autocrlf=true` 的检出确实会改动字节——官方 AGPL 全文
   （LF-only）与真混合行尾的第三方原文（自带 CRLF **且**有裸 LF）都会被改；
② **正向钉**：按仓库根 `.gitattributes` 的**实际内容**重放同一种检出，官方 AGPL 全文 /
   混合行尾第三方原文 / App 内对应法务资产三份都逐字节相同、SHA256 相同，且与
   `THIRD_PARTY_LICENSES/MANIFEST.json` 记录的 sha/bytes 一致；
③ **沙箱纪律**：所有 Git 操作只发生在 pytest `tmp_path` 里自建的空仓库（先断言绝对路径落在
   tmp_path 内、且不在本仓库内），本仓索引/HEAD/工作树一律不碰。

离线：不联网、不调模型、不起服务、不装依赖。

为什么用 `-text` 而不是 `eol=lf`：`THIRD_PARTY_LICENSES` 里 numpy / Pillow 两份原文本身就是
混合行尾，`eol=lf` 会在检出时把它们改写——那等于改许可文本。`-text` 是双向都不转换。
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
TP_DIR = REPO / "THIRD_PARTY_LICENSES"
GITATTRS = REPO / ".gitattributes"

# 参与往返的三份真实载荷：官方 AGPL 全文（LF-only）、App 资产（同形）、第三方混合行尾原文
REL_ROOT_LICENSE = "LICENSE"
REL_APP_ASSET = "flutter_app/assets/legal/AGPL-3.0.txt"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mixed_eol_third_party():
    """从 MANIFEST 里挑一份「磁盘上既有 CRLF 又有裸 LF、且 sha 对得上」的第三方原文。

    必须在 entries 里选：这样第②条能顺带拿 MANIFEST 记录的 sha/bytes 当外部基准。
    """
    manifest = json.loads((TP_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        p = TP_DIR / entry["path"]
        if not p.is_file():
            continue
        data = p.read_bytes()
        crlf, lf = data.count(b"\r\n"), data.count(b"\n")
        if crlf and lf > crlf and _sha(data) == entry["sha256"] and len(data) == entry["bytes"]:
            return entry["path"], data, entry
    return None, None, None


REL_TP_MIXED, TP_MIXED_BYTES, TP_MIXED_ENTRY = _mixed_eol_third_party()


@pytest.fixture(scope="module")
def git_exe():
    exe = shutil.which("git")
    if not exe:
        pytest.skip("PATH 上没有 git：本文件的检出模拟无法执行")
    return exe


def _make_sandbox(tmp_path: Path, name: str) -> Path:
    """建沙箱目录并**先验证绝对路径落在 tmp_path 内**——git 命令只允许从这里发出。

    注意「在 tmp_path 内」不等于「在仓库外」：CI 的全量/快测档就用 `--basetemp=.pytest-tmp`
    （工作区内），本机定向也带独立 basetemp。所以真正的兜底是 `_checkout_roundtrip` 里核对
    `git rev-parse --show-toplevel` 恰是沙箱自己，外加本仓 HEAD/index 前后逐字节相同。
    """
    root = tmp_path.resolve()
    sb = (root / name).resolve()
    assert sb.is_relative_to(root), f"沙箱必须落在 tmp_path 内：{sb} ∉ {root}"
    assert not (sb / ".git").exists(), f"沙箱必须是空仓库起点，已存在 .git 说明复用了别处：{sb}"
    sb.mkdir(parents=True, exist_ok=True)
    return sb


def _sandbox_env(sb: Path) -> dict:
    """隔离配置：全局/系统配置各指到一个空文件，避免继承本机 autocrlf 或身份。

    自建的 git 身份是人工合成值（example.invalid），不用真名、不写任何全局配置。
    """
    env = os.environ.copy()
    for key in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
        probe = sb / f"{key.lower()}.cfg"
        probe.write_text("", encoding="utf-8")
        env[key] = str(probe)
    env["HOME"] = str(sb)
    env["USERPROFILE"] = str(sb)
    for leak in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(leak, None)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "ci-hygiene"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "ci-hygiene@example.invalid"
    return env


def _git(git: str, cwd: Path, env: dict, *args: str) -> str:
    proc = subprocess.run([git, *args], cwd=str(cwd), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, f"git {' '.join(args)} 失败（cwd={cwd}）：\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def _checkout_roundtrip(tmp_path: Path, git: str, payload: dict, *, attrs) -> dict:
    """模拟一次 Windows 冷检出：沙箱仓库 `core.autocrlf=true` → add/commit → 删工作树副本 → checkout。

    `attrs=None` 复现「仓库根没有 .gitattributes」的旧状态；传入仓库根实际内容即修复后状态。
    """
    name = "sim-attrs" if attrs is not None else "sim-no-attrs"
    sb = _make_sandbox(tmp_path, name)
    env = _sandbox_env(sb)
    _git(git, sb, env, "init", "-q")
    top = Path(_git(git, sb, env, "rev-parse", "--show-toplevel").strip()).resolve()
    assert top == sb, f"git 认到的仓库顶层不是沙箱（{top}），说明命令会落到别处的 .git：本例中止"
    _git(git, sb, env, "config", "core.autocrlf", "true")
    if attrs is not None:
        (sb / ".gitattributes").write_text(attrs, encoding="utf-8", newline="\n")
    for rel, data in payload.items():
        dst = sb / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    tracked = sorted(payload)
    if attrs is not None:
        tracked.append(".gitattributes")
    _git(git, sb, env, "add", "--", *tracked)
    _git(git, sb, env, "commit", "-q", "-m", "simulate release checkout")
    for rel in tracked:
        (sb / rel).unlink()
    _git(git, sb, env, "checkout", "--", ".")
    return {rel: (sb / rel).read_bytes() for rel in payload}


def _payload() -> dict:
    if REL_TP_MIXED is None:
        pytest.skip("THIRD_PARTY_LICENSES 里找不到 sha/字节对得上的混合行尾原文，反向/正向钉都失去样本")
    return {
        REL_ROOT_LICENSE: (REPO / REL_ROOT_LICENSE).read_bytes(),
        REL_APP_ASSET: (REPO / REL_APP_ASSET).read_bytes(),
        f"THIRD_PARTY_LICENSES/{REL_TP_MIXED}": TP_MIXED_BYTES,
    }


def _repo_state() -> tuple:
    """本仓 HEAD 与工作树法务文本的当前指纹；沙箱跑完必须逐位相同＝对主仓零写入的硬证据。"""
    head = _git("git", REPO, os.environ.copy(), "--no-optional-locks", "rev-parse", "HEAD").strip()
    disk = {rel: _sha(data) for rel, data in _payload().items()}
    return head, disk


# ───────────────────────── 检出元数据本身 ─────────────────────────

def test_仓库根gitattributes只关法务文本的行尾转换():
    assert GITATTRS.is_file(), "仓库根缺 .gitattributes：Windows autocrlf 检出会改写许可字节"
    rules = GITATTRS.read_text(encoding="utf-8").splitlines()
    active = [ln for ln in rules if ln.strip() and not ln.strip().startswith("#")]
    for want in ("/LICENSE -text", "/NOTICE -text",
                 "/THIRD_PARTY_LICENSES/** -text", "/flutter_app/assets/legal/** -text"):
        assert want in active, f"缺少逐字节保真规则：{want}（现有：{active}）"
    for ln in active:
        if ln.startswith("* "):
            pytest.fail(f"全仓归一会顺带改写别的文件，本单只许逐项锚定：{ln!r}")


def test_check_attr把法务文本判成不转换(git_exe):
    """`git check-attr` 只读、不写锁文件：直接问 Git「这些路径的 text 属性是什么」。"""
    paths = [REL_ROOT_LICENSE, "NOTICE", "THIRD_PARTY_LICENSES/MANIFEST.json",
             f"THIRD_PARTY_LICENSES/{REL_TP_MIXED}", REL_APP_ASSET]
    env = os.environ.copy()
    out = _git(git_exe, REPO, env, "--no-optional-locks", "check-attr", "text", "--", *paths)
    for line in out.splitlines():
        assert line.endswith(": text: unset"), f"该路径仍会被做行尾转换：{line}"


# ───────────────────────── ① 反向钉：无 attrs 会被改字节 ─────────────────────────

def test_autocrlf无attrs时检出确实改写许可字节(git_exe, tmp_path):
    original = _payload()
    got = _checkout_roundtrip(tmp_path, git_exe, original, attrs=None)
    for rel, data in original.items():
        assert got[rel] != data, f"没有 attrs 时检出却没改字节，本文件的因果前提不成立：{rel}"
        assert _sha(got[rel]) != _sha(data), f"{rel} 的 sha 竟然没变"
    lic, lic_out = original[REL_ROOT_LICENSE], got[REL_ROOT_LICENSE]
    assert lic_out == lic.replace(b"\n", b"\r\n"), "LF-only 的官方全文应当被整份换成 CRLF"
    assert len(lic_out) == len(lic) + lic.count(b"\n") == 34523 + 661, (
        f"CI 报的 35184 = 34523 + 661 个 CR，本例复现不出这个数说明模拟没命中根因：{len(lic_out)}"
    )
    mixed = original[f"THIRD_PARTY_LICENSES/{REL_TP_MIXED}"]
    assert mixed.count(b"\n") > mixed.count(b"\r\n") > 0, "样本必须是真混合行尾"
    assert got[f"THIRD_PARTY_LICENSES/{REL_TP_MIXED}"].count(b"\r\n") == \
        mixed.count(b"\n"), "混合行尾原文被归一成清一色 CRLF（正是 eol=lf/-text 要避开的那种改写）"


# ───────────────────────── ② 正向钉：按仓库 attrs 字节与 SHA 不变 ─────────────────────────

def test_autocrlf按仓库attrs检出字节与sha不变(git_exe, tmp_path):
    before = _repo_state()
    original = _payload()
    got = _checkout_roundtrip(tmp_path, git_exe, original, attrs=GITATTRS.read_text(encoding="utf-8"))
    for rel, data in original.items():
        assert got[rel] == data, f"检出改了法务文本字节：{rel}（{len(data)} → {len(got[rel])}）"
        assert _sha(got[rel]) == _sha(data), f"{rel} 的 SHA256 变了"
    assert _sha(got[REL_APP_ASSET]) == _sha(original[REL_ROOT_LICENSE]), \
        "App 内法务资产与根 LICENSE 必须同一份字节，否则公告页与仓库文本已经分叉"
    rel_tp = f"THIRD_PARTY_LICENSES/{REL_TP_MIXED}"
    assert _sha(got[rel_tp]) == TP_MIXED_ENTRY["sha256"], "检出副本对不上 MANIFEST 记录的 sha"
    assert len(got[rel_tp]) == TP_MIXED_ENTRY["bytes"], "检出副本对不上 MANIFEST 记录的字节数"
    assert _repo_state() == before, "沙箱检出模拟动过本仓 HEAD 或工作树法务文本（主仓必须零写入）"


# ───────────────────────── ③ 沙箱纪律本身 ─────────────────────────

def test_沙箱路径守卫拦得住tmp_path之外的目录(tmp_path):
    ok = _make_sandbox(tmp_path, "guard-ok")
    assert ok.is_relative_to(tmp_path.resolve()) and (ok / ".git").is_dir() is False
    for escape in ("../ambrace_escape_probe", "../../ambrace_escape_probe2"):
        with pytest.raises(AssertionError, match="tmp_path"):
            _make_sandbox(tmp_path, escape)
