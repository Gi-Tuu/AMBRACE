#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开仓卫生扫描（C2，2026-10-08）：把「推前手工扫三条」变成一条命令＋CI 阻断。

用法：
    backend\\.venv\\Scripts\\python.exe scripts\\check_public_leak.py            # 扫 HEAD 树（＝会进公开仓的内容）
    backend\\.venv\\Scripts\\python.exe scripts\\check_public_leak.py --rev <sha> # 扫快照树（脱敏快照构建后、推送前）
    backend\\.venv\\Scripts\\python.exe scripts\\check_public_leak.py --worktree  # 扫工作区已跟踪文件（提交前）

检查两类东西：
1. **作者机器信息**——本机路径／外部工作区路径／用户名（这三条此前靠手工 `git grep` 拦下过两次：
   10-06 是测试夹具里的真机路径，10-08 是模块 **docstring** 里报告文件的绝对路径）；
2. **凭据形态**——私钥块、`sk-`／`ghp_`／`xox`／AWS AKIA 等长串。

排除口径与 [docs/release-public-snapshot.md] 第 2 步**刻意保持一致**（改了那边要同步这里，守卫
`backend/tests/test_public_leak_scan_c2.py` 会比对两边清单）：`.agents/`、`docs/`（只留
`docs/changelog.md`，它是 App 更新公告数据源）、`AGENTS.md`、`HANDOFF.md`、`flutter.bat`、
`start_server.bat`、`restart_server.bat`、`flutter_app/.metadata`。

实现约束（都是踩过的坑）：
- **只用字节**：`git ls-tree`/`git show` 拿 bytes，正则也在 bytes 上做 ⇒ 不受本机码页影响
  （Windows 机曾因 `subprocess(text=True)` 的解码假设被 CI 打红，见 AGENTS.md 与 dev-changelog）。
- **模式一律分片拼接**：本文件自身绝不能被自己的模式命中，否则「扫描器自己算不算泄漏」会永远说不清。
- 任何命中 ⇒ 退出码 1，并打印 `文件:行号`＋脱敏片段（片段里的敏感串本身只打前 24 字，避免日志二次外泄）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 与脱敏快照构建脚本同口径的排除清单
EXCL_TOP = (".agents", "docs", "AGENTS.md", "HANDOFF.md", "flutter.bat", "start_server.bat", "restart_server.bat")
EXCL_EXACT = ("flutter_app/.metadata",)
KEEP_IN_EXCLUDED = ("docs/changelog.md",)

# 分片拼接：本文件里不出现任何完整模式字面量
_PATTERNS = [
    ("author_machine_path", rb"Code" + rb"x-" + rb"Projects"),
    ("author_home", rb"Users" + rb"[\\/]{1,2}" + rb"she" + rb"ng"),
    ("author_repo_path", rb"D:" + rb"[\\/]+" + rb"AMB" + rb"RACE"),
    ("private_key_pem", rb"-----BEGIN" + rb" [A-Z ]*PRIVATE KEY-----"),
    ("openai_style_key", rb"\bsk-" + rb"[A-Za-z0-9]{20,}"),
    ("github_token", rb"\bgh" + rb"[pousr]_" + rb"[A-Za-z0-9]{20,}"),
    ("slack_token", rb"\bxox" + rb"[baprs]-" + rb"[A-Za-z0-9-]{10,}"),
    ("aws_access_key", rb"\bAKIA" + rb"[0-9A-Z]{16}\b"),
]
COMPILED = [(name, re.compile(pat)) for name, pat in _PATTERNS]


def is_public(path: str) -> bool:
    """这个文件会不会进公开仓。"""
    if path in KEEP_IN_EXCLUDED:
        return True
    top = path.split("/", 1)[0]
    if top in EXCL_TOP:
        return False
    return path not in EXCL_EXACT


def git(*args: str, inp: bytes | None = None) -> bytes:
    p = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, input=inp, check=False)
    if p.returncode != 0:
        raise SystemExit(f"[check_public_leak] git {' '.join(args)} 失败：{p.stderr.decode('utf-8', 'replace')}")
    return p.stdout


def list_public_files(rev: str | None) -> list[str]:
    if rev:
        raw = git("ls-tree", "-r", "--name-only", rev)
        names = [n.decode("utf-8", "replace") for n in raw.split(b"\n") if n]
    else:
        names = [n.decode("utf-8", "replace") for n in git("ls-files", "-z").split(b"\0") if n]
    return [n for n in names if is_public(n)]


def parse_batch(paths: list[str], raw: bytes) -> list[tuple[str, bytes]]:
    """解析 `git cat-file --batch` 的输出（`<sha> blob <size>\\n<size 字节>\\n`）。

    MISSING／非 blob 的行**跳过而不抛**：改名或空树时不该让整个扫描崩掉。
    """
    blobs: list[tuple[str, bytes]] = []
    pos = 0
    for path in paths:
        nl = raw.find(b"\n", pos)
        if nl < 0:
            break
        parts = raw[pos:nl].decode("utf-8", "replace").split(" ")
        if len(parts) < 3 or parts[1] != "blob":
            pos = nl + 1
            continue
        size = int(parts[2])
        blobs.append((path, raw[nl + 1: nl + 1 + size]))
        pos = nl + 1 + size + 1  # 内容后跟一个换行
    return blobs


def read_blobs(rev: str | None, paths: list[str]) -> list[tuple[str, bytes]]:
    """一次 `cat-file --batch` 取回全部 blob（逐文件起子进程要 1800 次，太慢）。"""
    if rev is None:
        out = []
        for path in paths:
            p = ROOT / path
            out.append((path, p.read_bytes() if p.is_file() else b""))
        return out
    inp = b"".join(pp.encode("utf-8", "surrogatepass") + b"\n" for pp in paths)
    return parse_batch(paths, git("cat-file", "--batch", inp=inp))


def scan_blob(path: str, data: bytes) -> list[tuple[str, int, str]]:
    hits = []
    for lineno, line in enumerate(data.split(b"\n"), start=1):
        for name, rx in COMPILED:
            m = rx.search(line)
            if m:
                frag = m.group(0)[:24]
                hits.append((name, lineno, frag.decode("utf-8", "replace")))
    return hits


def scan(rev: str | None) -> tuple[int, list[tuple[str, str, int, str]]]:
    """返回（公开面文件数，命中清单）。二进制跳过。"""
    files = list_public_files(rev)
    hits = []
    for path, data in read_blobs(rev, files):
        if b"\x00" in data[:4096]:
            continue
        for name, line, frag in scan_blob(path, data):
            hits.append((name, path, line, frag))
    return len(files), hits


def main() -> int:
    ap = argparse.ArgumentParser(description="公开仓卫生扫描（作者机器路径＋凭据形态）")
    ap.add_argument("--rev", default=None, help="扫描某个 git 版本/树的快照（默认 HEAD）")
    ap.add_argument("--worktree", action="store_true", help="扫描工作区已跟踪文件（提交前用）")
    ap.add_argument("--files", default=None, help="只扫给定文件（相对仓库根，调试用）")
    args = ap.parse_args()

    if args.files:
        data = (ROOT / args.files).read_bytes()
        found = [(name, args.files, line, frag) for name, line, frag in scan_blob(args.files, data)]
        rev = None
        n_files = 1
    else:
        rev = None if args.worktree else (args.rev or "HEAD")
        n_files, found = scan(rev)

    scope = "工作区" if rev is None and not args.rev else (args.rev or "HEAD")
    print(f"[check_public_leak] 扫描范围＝{scope}；公开面文件 {n_files} 个；命中 {len(found)} 处")
    for name, path, line, frag in found:
        print(f"  [{name}] {path}:{line}  →  {frag}")
    if found:
        print("[check_public_leak] 结论：有内容会随公开仓外泄——按 docs/release-public-snapshot.md 第 3 步改源码后重扫，别推。")
        return 1
    print("[check_public_leak] 结论：干净（0 命中）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
