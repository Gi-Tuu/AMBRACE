#!/usr/bin/env python
"""docs 体检 —— 只检查「与内容无关、永远该成立」的结构性事实。

设计原则（2026-10-25 用户确认）：
  1. **不写死任何业务数字**。注册键数、迁移数、flags 行数一律从源头现取
     （AST 解析 `agent_flags.py`、扫 `alembic/versions`、只读查 SQLite），
     所以源码增删键时本脚本不会「过期」，只会更准。
  2. 门禁只留三条不会烂的：行尾/CR/BOM、markdown 表格完整性、相对链接可达性。
     超长行、超大文件只**告警**（阈值见下方常量，嫌吵就调）。
  3. 检查最怕误报：红一堆没人看就等于没有。所以：
     - 表格只在「确实是表格」（表头+分隔行）的上下文里判断开；归档里逐字留存的
       表格片段不算表格，不判。
     - 链接按「所在目录 → docs/ 根 → 仓库根」三级回退解析，兼容移动过的文件。
     - `docs/archive/` 下的超长行不告警（历史留档本来就长）。
     - 有歧义的判断（列数是否一致、锚点是否存在）一律不做。

用法：
  python scripts/docs_check.py               # 体检（默认根目录＝仓库根）
  python scripts/docs_check.py --facts       # 打印「当前源头事实」，方便更新文档
  python scripts/docs_check.py --fix         # 只修机械问题：行尾归一、去 BOM
  python scripts/docs_check.py --root <dir>  # 换根目录（自测用）
  python scripts/docs_check.py --only plans  # 只处理路径含该子串的文件

退出码：门禁问题 → 1（可接 CI）；否则 0。仅标准库；只读（`--fix` 除外）；不联网。
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sqlite3
import sys

# ---- 可调阈值（仅告警，不影响退出码）----
MAX_LINE_CHARS = 1200      # 单行超过即告警（plans 整理后最长 1077）
MAX_FILE_KB = 400          # 单文件超过即告警（dev-changelog 会陆续变大，按需调）
STATUS_LINE_CHARS = 1200   # plans.md 顶部状态行额外提示线
# 日志类文件「一条一行」是既定写法，长行属预期，不做超长行告警
NO_LINE_WARN = ("dev-changelog", "dev-changelog-full", "HANDOFF.md")

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "build", "dist", ".pytest_tmp",
    ".mypy_cache", "__pycache__", ".dart_tool", "backups", ".migrate-drill",
    "browser_profile", "douyin_profile", "pets_assets",
}

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
FENCE_RE = re.compile(r"^```")
IS_ARCHIVE = ("archive",)


# --------------------------------------------------------------------------
def iter_md(root: pathlib.Path, scan_all: bool = False):
    """默认只扫 `docs/**` 与仓库根级 *.md —— 我们的文档就这些。
    `.agents/`（第三方 skill 包）、`.workbuddy/`、`.mimosa/` 等不属于本仓文档，
    要一起扫才用 `--all`（它们里的占位链接/工具产物会制造噪声）。"""
    for p in sorted(root.rglob("*.md")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not scan_all and not (rel.parts[0] == "docs" or len(rel.parts) == 1):
            continue
        yield p, rel


def in_archive(rel: pathlib.Path) -> bool:
    return any(part in IS_ARCHIVE for part in rel.parts)


# ---------------------------------------------------------------- 门禁 1
def check_encoding(p: pathlib.Path):
    """返回 (errors, warnings, kind)"""
    b = p.read_bytes()
    crlf = b.count(b"\r\n")
    lone_lf = b.count(b"\n") - crlf
    lone_cr = b.count(b"\r") - crlf
    errors, warnings = [], []
    if lone_cr:
        errors.append(f"{p}: 存在 {lone_cr} 个孤立/双 CR（\\r\\r\\n 家族），会静默改坏文件")
    if b[:3] == b"\xef\xbb\xbf":
        warnings.append(f"{p}: 带 UTF-8 BOM（新文件应为无 BOM）")
    if crlf and lone_lf:
        errors.append(f"{p}: 行尾混杂 CRLF×{crlf} + LF×{lone_lf}")
        kind = "mixed"
    elif crlf:
        kind = "crlf"
    else:
        kind = "lf"
    return errors, warnings, kind


# ---------------------------------------------------------------- 门禁 2
def check_tables(p: pathlib.Path, text: str):
    """只在「表头 + 分隔行」确立的表格上下文里检查：
       表身被空行切断（空行后又出现 | 行）＝断表。
       archive 下逐字留档的孤立表格行不构成表格，不判。
    """
    lines = text.split("\n")
    n = len(lines)
    errors = []
    in_fence = False
    i = 0
    while i < n - 1:
        if FENCE_RE.match(lines[i]):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        if lines[i].lstrip().startswith("|") and SEP_RE.match(lines[i + 1]):
            j = i + 2
            while j < n and lines[j].lstrip().startswith("|"):
                j += 1
            # 表身结束：若紧跟空行，且空行后又是 | 行 → 断表
            if j + 1 < n and lines[j].strip() == "" and lines[j + 1].lstrip().startswith("|"):
                errors.append(f"{p}:L{j+1}: 表格被空行切断（表头在 L{i+1}，空行后又出现表格行）")
            i = j
        else:
            i += 1
    return errors


# ---------------------------------------------------------------- 门禁 3
def check_links(p: pathlib.Path, text: str, root: pathlib.Path):
    """相对链接三级回退解析：文件所在目录 → docs/ → 仓库根。"""
    errors = []
    # 去掉围栏代码块，避免把示例代码里的方括号当链接
    body, in_fence = [], False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            body.append("")
            continue
        body.append("" if in_fence else line)
    text2 = "\n".join(body)

    docs_root = root / "docs"
    for m in LINK_RE.finditer(text2):
        target = m.group(1).strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "tel:", "#", "data:")):
            continue
        path_part = target.split("#", 1)[0].split("?", 1)[0].strip("<>")
        if not path_part:
            continue
        path_part = path_part.replace("%20", " ")
        candidates = [p.parent / path_part, docs_root / path_part, root / path_part]
        ok = False
        for c in candidates:
            try:
                if c.resolve().exists():
                    ok = True
                    break
            except OSError:
                continue
        if not ok:
            errors.append(f"{p}: 链接指向不存在的路径 {target}")
    return errors


# ---------------------------------------------------------------- 告警
def check_size(p: pathlib.Path, text: str, max_line: int, max_kb: int, rel: pathlib.Path):
    warns = []
    size_kb = p.stat().st_size / 1024
    if size_kb > max_kb:
        warns.append(f"{p}: 文件 {size_kb:.0f}KB > {max_kb}KB（考虑分卷或入 archive）")
    if in_archive(rel) or any(tag in p.name for tag in NO_LINE_WARN):
        return warns  # 归档/日志类：长行属预期，不告警
    for i, line in enumerate(text.split("\n"), 1):
        if len(line) > max_line:
            warns.append(f"{p}:L{i}: 单行 {len(line)} 字符 > {max_line}（考虑拆行/入 archive）")
    return warns


# ---------------------------------------------------------------- 源头事实
def facts(root: pathlib.Path):
    out = []
    flags_src = root / "backend/app/flags/agent_flags.py"
    if flags_src.exists():
        tree = ast.parse(flags_src.read_text(encoding="utf-8"))
        node = None
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and any(
                getattr(t, "id", None) == "AGENT_FLAGS" for t in n.targets
            ):
                node = n.value
                break
        if isinstance(node, ast.Dict):
            bools, others = [], []
            for k, v in zip(node.keys, node.values):
                key = getattr(k, "value", None)
                if isinstance(v, ast.Constant) and isinstance(v.value, bool):
                    bools.append((key, v.value))
                else:
                    others.append((key, ast.unparse(v)[:40]))
            on = sum(1 for _, v in bools if v)
            out.append(f"注册键        : {len(bools) + len(others)} = {len(bools)} 布尔"
                       f"（默认开 {on} / 默认关 {len(bools) - on}）+ {len(others)} 非布尔")
            for k, v in others:
                out.append(f"  非布尔档    : {k} = {v}")

    vdir = root / "backend/alembic/versions"
    if vdir.is_dir():
        revs, downs = set(), set()
        for f in vdir.glob("*.py"):
            t = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)", t, re.MULTILINE)
            d = re.search(r"^down_revision(?::\s*[^=]+)?\s*=\s*(.+)$", t, re.MULTILINE)
            if m:
                revs.add(m.group(1))
            if d:
                downs.update(re.findall(r"[\"']([^\"']+)[\"']", d.group(1)))
        heads = sorted(revs - downs)
        out.append(f"迁移文件      : {len(list(vdir.glob('*.py')))} 个  revision {len(revs)}  "
                   f"heads {len(heads)}: {', '.join(heads) or '无'}")

    db = root / "backend/data/sqlite/ai_companion.db"
    if db.exists():
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
            cur = con.cursor()
            rows = cur.execute("SELECT COUNT(*) FROM runtime_flags").fetchone()[0]
            on = cur.execute("SELECT COUNT(*) FROM runtime_flags WHERE enabled=1").fetchone()[0]
            ver = [r[0] for r in cur.execute("SELECT version_num FROM alembic_version")]
            last = cur.execute("SELECT MAX(updated_at) FROM runtime_flags").fetchone()[0]
            out.append(f"生产 flags    : {rows} 行（ON {on} / OFF {rows - on}），"
                       f"最新写入 {last}；DB alembic_version={ver}")
            con.close()
        except sqlite3.Error as e:
            out.append(f"生产库        : 只读查询失败（{e}）")

    plans = root / "docs/plans.md"
    if plans.exists():
        t = plans.read_text(encoding="utf-8")
        ls = t.split("\n")
        out.append(f"plans.md      : {plans.stat().st_size} B / {len(ls)} 行 / "
                   f"最长行 {max(len(x) for x in ls)} 字符")
    return out


# ---------------------------------------------------------------- fix
def fix_file(p: pathlib.Path, kind: str):
    b = p.read_bytes()
    changed = False
    if b[:3] == b"\xef\xbb\xbf":
        b = b[3:]
        changed = True
    if kind == "mixed":
        crlf = b.count(b"\r\n")
        lone = b.count(b"\n") - crlf
        if crlf > lone:                      # 多数派是 CRLF → 归一为 CRLF
            b = b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        else:                                # 多数派是 LF → 归一为 LF
            b = b.replace(b"\r\n", b"\n")
        b = b.replace(b"\r", b"\n") if b"\r" in b else b
        changed = True
    if changed:
        p.write_bytes(b)
        return True
    return False


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="docs 结构与源头事实体检")
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent))
    ap.add_argument("--facts", action="store_true", help="只打印源头事实，不做检查")
    ap.add_argument("--fix", action="store_true", help="只修机械问题：行尾归一、去 BOM")
    ap.add_argument("--only", default="", help="只处理路径含该子串的文件")
    ap.add_argument("--all", action="store_true",
                    help="连同 .agents/ 等非本仓文档一起扫（默认只扫 docs/ 与根级 md）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    if not root.is_dir():
        print(f"根目录不存在: {root}", file=sys.stderr)
        return 2

    if args.facts:
        print(f"# 源头事实（{root}）")
        for line in facts(root):
            print(line)
        return 0

    errors, warnings = [], []
    n_files, fixed = 0, 0
    stats = {"lf": 0, "crlf": 0, "mixed": 0}

    for p, rel in iter_md(root, args.all):
        if args.only and args.only not in str(rel).lower():
            continue
        n_files += 1
        e_enc, w_enc, kind = check_encoding(p)
        stats[kind] += 1
        if in_archive(rel):                      # 归档里的旧 BOM 属历史遗留，不再提示
            w_enc = [w for w in w_enc if "BOM" not in w]
        if args.fix and kind in ("mixed", "lf", "crlf") and fix_file(p, kind):
            fixed += 1
            e_enc, w_enc, kind = check_encoding(p)
        text = p.read_text(encoding="utf-8", errors="replace")
        errors += e_enc
        warnings += w_enc
        tbl = check_tables(p, text)
        if in_archive(rel):
            # 归档是历史逐字留档，断表只记提示、不算门禁（不回改历史文件）
            warnings += [e + "（归档留档，不回改）" for e in tbl]
        else:
            errors += tbl
        link_errs = check_links(p, text, root)
        if in_archive(rel):
            warnings += [e + "（归档留档，不回改）" for e in link_errs]
        else:
            errors += link_errs
        warnings += check_size(p, text, MAX_LINE_CHARS, MAX_FILE_KB, rel)

    plans = root / "docs/plans.md"
    if plans.exists() and not args.only:
        for line in plans.read_text(encoding="utf-8").split("\n"):
            if line.startswith("**当前状态") and len(line) > STATUS_LINE_CHARS:
                warnings.append(f"{plans}: 顶部状态行 {len(line)} 字符 > {STATUS_LINE_CHARS}，只留当前态")
                break

    if not args.quiet:
        for w in warnings:
            print(f"WARN  {w}")
        for e in errors:
            print(f"ERROR {e}")

    print(f"\n扫描 {n_files} 个 .md｜行尾 LF {stats['lf']} / CRLF {stats['crlf']} / 混杂 {stats['mixed']}"
          f"｜门禁问题 {len(errors)}｜告警 {len(warnings)}"
          + (f"｜已修正 {fixed} 个文件" if args.fix else ""))
    if errors:
        print("结论：门禁未通过（下列均为与内容无关的结构性问题）")
        return 1
    print("结论：门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
