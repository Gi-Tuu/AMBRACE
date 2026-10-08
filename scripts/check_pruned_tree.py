# -*- coding: utf-8 -*-
"""C4 的判据工具：在**裁剪树**上真跑一遍测试，把「本机绿、CI 红」这一类一次性量出来。

为什么不写静态扫描器：解析「这个表达式最终读到哪个路径」不完备就等于没牙
（v1 AST 版静默 0 命中、v2 行窗口版也漏了 `_read(REAL)` 这种跨行绑定）。
裁剪树是 CI 实际检出的那份文件集合，直接在上面跑＝完备。

用法：
    python scripts/check_pruned_tree.py                 # 全量
    python scripts/check_pruned_tree.py -k "docs or flag"  # 子集（先验证跑通）
"""
import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # 不写死盘符：本文件自身进公开面，
# 把作者机的绝对路径写进注释或 docstring，就是 C2 扫描器要拦的那一次泄漏（第 116 棒前科）
PY = str(ROOT / "backend" / ".venv" / "Scripts" / "python.exe")
EXCL_PREFIX = (".agents", "docs", "AGENTS.md", "HANDOFF.md",
               "flutter.bat", "start_server.bat", "restart_server.bat")


def git(*a):
    p = subprocess.run(["git", "-C", str(ROOT)] + list(a), capture_output=True)
    if p.returncode != 0:
        raise SystemExit("git 失败: " + " ".join(a) + p.stderr.decode("utf-8", "replace"))
    return p.stdout


def is_public(rel: str, excl_exact) -> bool:
    """与快照构建同口径：docs/ 只留 changelog.md，其余按前缀与精确清单排除。"""
    if rel in excl_exact:
        return False
    top = rel.split("/")[0]
    if top in EXCL_PREFIX and rel != "docs/changelog.md":
        return False
    if rel in EXCL_PREFIX:
        return False
    return True


def build_tree(rev: str) -> Path:
    """从 git 对象导出 rev 的工作树，按公开面口径裁剪，返回临时目录。"""
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_public_leak as cpl  # 排除清单的唯一真源
    excl_exact = tuple(str(x) for x in getattr(cpl, "EXCL_EXACT", ()))

    t = Path(tempfile.mkdtemp(prefix="ambrace_pruned_"))
    tar = tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", rev)))
    try:
        tar.extractall(t, filter="tar")
    except TypeError:
        tar.extractall(t)
    tar.close()

    removed = 0
    for dirpath, dirnames, filenames in os.walk(str(t), topdown=False):
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = str(full.relative_to(t)).replace("\\", "/")
            if not is_public(rel, excl_exact):
                full.unlink()
                removed += 1
        if not os.listdir(dirpath) and dirpath != str(t):
            try:
                os.rmdir(dirpath)
            except OSError:
                pass
    print("[裁剪树] rev=%s 删掉 %d 个文件 目录 %s" % (rev[:8], removed, t))
    # CI 检出的是公开仓：那里 `git ls-files` 恰好等于公开面。本地要照做，
    # 否则读 git 的守卫（如 C2 的「现在的 HEAD 干净」）会因为临时树没有 .git 而假红。
    # 索引一律用 `update-index --stdin` 灌：`git add -A` 在这份树上要做全树 status 扫描，
    # 实测把整轮拖到 85 分钟（pytest 本身只 4:49），那样这工具推前没人会跑。
    import time as _t
    t0 = _t.time()
    for cmd in (["git", "init", "-q"],):
        p = subprocess.run(cmd, cwd=str(t), capture_output=True)
        if p.returncode != 0:
            print("[警告] git %s 失败：%s" % (cmd[1], p.stderr.decode("utf-8", "replace")[:160]))
            break
    files = [str(x.relative_to(t)).replace("\\", "/") for x in t.rglob("*") if x.is_file()]
    p = subprocess.run(["git", "-c", "core.autocrlf=false", "update-index", "--add", "--stdin"],
                       cwd=str(t), input="\n".join(files).encode("utf-8"), capture_output=True)
    if p.returncode != 0:
        print("[警告] update-index 失败：%s" % p.stderr.decode("utf-8", "replace")[:200])
    tree_sha = subprocess.run(["git", "write-tree"], cwd=str(t), capture_output=True)
    if tree_sha.returncode == 0:
        # `git commit <tree>` 会把 tree 当 pathspec（实测报 did not match）⇒ 用 commit-tree + update-ref
        c = subprocess.run(["git", "-c", "user.email=ci@local", "-c", "user.name=ci",
                            "commit-tree", tree_sha.stdout.strip(), "-m", "pruned-snapshot"],
                           cwd=str(t), capture_output=True)
        if c.returncode != 0:
            print("[警告] commit-tree 失败：%s" % c.stderr.decode("utf-8", "replace")[:200])
        else:
            r = subprocess.run(["git", "update-ref", "refs/heads/master", c.stdout.strip()],
                               cwd=str(t), capture_output=True)
            if r.returncode != 0:
                print("[警告] update-ref 失败：%s" % r.stderr.decode("utf-8", "replace")[:200])
    n = len(subprocess.run(["git", "ls-files"], cwd=str(t), capture_output=True).stdout.splitlines())
    print("[裁剪树] 已建成独立 git 仓，git ls-files = %d 个文件（CI 视角），建树耗时 %.1f 秒" % (n, _t.time() - t0))
    return t


def run_pytest(tree: Path, extra):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [PY, "-m", "pytest", "tests", "-q", "-n", "8",
           "--basetemp=.pytest_pruned", "-p", "no:cacheprovider"] + extra
    # 日志落系统临时目录：不往仓库里丢产物（免得每次跑完 git status 多一行），也不写死作者机路径
    log = Path(os.environ.get("PRUNED_LOG") or (Path(tempfile.gettempdir()) / "pruned_tree_pytest.log"))
    with open(log, "wb") as fh:
        p = subprocess.run(cmd, cwd=str(tree / "backend"), stdout=fh,
                           stderr=subprocess.STDOUT, env=env)
    txt = log.read_text(encoding="utf-8", errors="replace")
    return p.returncode, txt, log


def classify(txt: str):
    """把失败按「是不是缺席文件类」分开——前者是 C4 的债，后者是环境差异，别混。"""
    missing = sorted(set(re.findall(r"FAILED ([^\s:]+::[^\s]+)", txt)
                        + re.findall(r"ERROR ([^\s:]+(?:::[^\s]+)?)", txt)))
    fnf = re.findall(r"(?:FileNotFoundError|No such file or directory).*?([A-Za-z0-9_\-./\\]+(?:\.md|\.jsonl|\.py|\.json|\.db))",
                     txt)
    return missing, sorted(set(fnf))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rev", nargs="?", default="HEAD")
    ap.add_argument("-k", dest="k", default=None)
    ap.add_argument("--full", action="store_true",
                    help="跑全量（本机实测 4:49；日常推前用默认子集几十秒，全量也可靠 CI）")
    ap.add_argument("--keep", action="store_true", help="跑完不删临时树")
    a = ap.parse_args()

    # 默认只跑「会读仓库文件」的那一族：这类债就长在这一族里，30 秒量得完。
    QUICK_K = ("docs or flag or plan or eval or leak or snapshot or b15 or memory_trace "
               "or entity_match or edit_tool or changelog or readme")
    if a.k:
        extra = ["-k", a.k]
    elif a.full:
        extra = []
    else:
        extra = ["-k", QUICK_K]
        print("[模式] 子集＝会读仓库文件的那一族；要全量加 --full")

    tree = build_tree(a.rev)
    try:
        code, txt, log = run_pytest(tree, extra)
        tail = [x for x in txt.strip().splitlines() if x.strip()][-1:] or ["(空)"]
        print("[pytest] exit=%s 末行=%s 日志=%s" % (code, tail[0][:140], log))
        missing, fnf = classify(txt)
        print("[缺席文件] 报 FileNotFoundError/No such file 的路径 %d 个：" % len(fnf))
        for f in fnf[:20]:
            print("    ", f)
        print("[失败/错误用例] %d 个：" % len(missing))
        for m in missing[:40]:
            print("    ", m)
        if code == 0:
            print("结论：裁剪树上全绿 ⇒ 没有 C4 这类债")
        elif code == 5:
            print("结论：没有用例匹配（-k 写宽一点，或不带 -k 跑默认子集）")
        else:
            print("结论：裁剪树上有红，见上表")
        return 1 if code not in (0, 5) else 0
    finally:
        if not a.keep:
            shutil.rmtree(tree, ignore_errors=True)
            print("[清理] 临时树已删")
        else:
            print("[保留] 临时树", tree)


if __name__ == "__main__":
    sys.exit(main())
