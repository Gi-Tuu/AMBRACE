#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""网关词表守卫（2026-11-04 事故后立，零容忍）。

背景：本机 DSH 走中转到模型网关，网关对某些词元有误伤式拦截。命中后每一轮请求
都 500，且历史不可洗 → 会话永久报废。已知触发元是最常见的一种 pytest 函数名前缀
（`test_` 紧接一个 9 字母安全类词），已在本仓出现过两次事故。

红线：**本文件不得出现触发串原文**。词元在运行时拼装，源码里永远只有碎片
（否则本文件自己就成了雷区，agent 一读就中毒）。

用法：
    python scripts/gateway_word_guard.py                 # 扫工作树（跟踪 + 未忽略的未跟踪文件）
    python scripts/gateway_word_guard.py --staged         # 只扫暂存区（pre-commit 用）
    python scripts/gateway_word_guard.py --message <file> # 扫提交信息（commit-msg 用）
    python scripts/gateway_word_guard.py --install-hooks  # 安装/刷新 .git/hooks 里的两道钩子

退出码：0 干净；1 命中；2 运行出错。
只打印文件路径与条目数，**绝不打印命中内容**（打印即中毒）。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 已知触发元（运行时拼装，勿合并成字面量）
_P_CANDIDATE = "sensitive"
BANNED = ["test_" + _P_CANDIDATE]

# 仓库外可选的总词表（有就用，没有也不影响）
EXTERNAL_NEEDLE = Path(r"E:\Competition\YAI-TraceLab-diag\corpus\trigger-min.txt")


def _tokens() -> list[str]:
    out = list(BANNED)
    try:
        extra = EXTERNAL_NEEDLE.read_text(encoding="utf-8").strip()
        if extra and extra not in out:
            out.append(extra)
    except OSError:
        pass
    return out


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def scan_git_grep(tokens: list[str], cached: bool) -> list[str]:
    hits: list[str] = []
    for tok in tokens:
        args = ["grep", "-I", "-i", "-l", "--no-color", "-F"]
        if cached:
            args.append("--cached")
        args += [tok, "--", "."]
        code, out = _git(*args)
        if code not in (0, 1):  # 0=命中 1=无命中
            print(f"[guard] git grep 失败（code={code}）", file=sys.stderr)
            raise SystemExit(2)
        for line in out.splitlines():
            line = line.strip()
            if line and line not in hits:
                hits.append(line)
    return hits


def untracked_files() -> list[str]:
    code, out = _git("ls-files", "--others", "--exclude-standard", "--", ".")
    if code != 0:
        return []
    return [l.strip() for l in out.splitlines() if l.strip()]


def scan_untracked(tokens: list[str]) -> list[str]:
    hits: list[str] = []
    lowered = [t.lower() for t in tokens]
    for rel in untracked_files():
        p = ROOT / rel
        try:
            if p.stat().st_size > 8 * 1024 * 1024:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(t in text for t in lowered):
            hits.append(rel)
    return hits


def scan_message(path: str, tokens: list[str]) -> list[str]:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace").lower()
    except OSError as e:
        print(f"[guard] 读不到提交信息：{e}", file=sys.stderr)
        raise SystemExit(2)
    return [path] if any(t.lower() in text for t in tokens) else []


HOOK_PRE_COMMIT = """#!/bin/sh
# 网关词表守卫（零容忍）：命中即拒绝提交。装/刷新：python scripts/gateway_word_guard.py --install-hooks
exec "$(git rev-parse --show-toplevel)/backend/.venv/Scripts/python.exe" "$(git rev-parse --show-toplevel)/scripts/gateway_word_guard.py" --staged
"""

HOOK_COMMIT_MSG = """#!/bin/sh
# 网关词表守卫：提交信息里也不许出现触发串。
exec "$(git rev-parse --show-toplevel)/backend/.venv/Scripts/python.exe" "$(git rev-parse --show-toplevel)/scripts/gateway_word_guard.py" --message "$1"
"""


def install_hooks() -> int:
    code, out = _git("rev-parse", "--git-dir")
    if code != 0:
        print("[guard] 不在 git 仓库里", file=sys.stderr)
        return 2
    hooks = (ROOT / out.strip()).resolve() / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for name, body in (("pre-commit", HOOK_PRE_COMMIT), ("commit-msg", HOOK_COMMIT_MSG)):
        p = hooks / name
        p.write_text(body, encoding="utf-8", newline="\n")
        print(f"[guard] 已写入 .git/hooks/{name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--message", metavar="FILE")
    ap.add_argument("--install-hooks", action="store_true")
    a = ap.parse_args()

    if a.install_hooks:
        return install_hooks()

    tokens = _tokens()
    if a.message:
        hits = scan_message(a.message, tokens)
        scope = "提交信息"
    elif a.staged:
        hits = scan_git_grep(tokens, cached=True)
        scope = "暂存区"
    else:
        hits = scan_git_grep(tokens, cached=False)
        hits += [h for h in scan_untracked(tokens) if h not in hits]
        scope = "工作树"

    if hits:
        print(f"[guard] 网关词表守卫：{scope}命中 {len(hits)} 个文件（内容不打印）")
        for h in hits:
            print(f"  - {h}")
        print("[guard] 处置：改名/改词后重试；历史里残留的旧版不要 git show / git log -p / git grep。")
        return 1
    print(f"[guard] 网关词表守卫：{scope}干净（0 命中）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
