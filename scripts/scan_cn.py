# -*- coding: utf-8 -*-
"""扫描改动（git diff HEAD 新增行）中的硬编码中文 UI 文案，非 0 表示有未走 i18n 的新增中文。

背景（#54/#55，2026-08-23）：新增 UI 文案要求 zh/en ARB 成对，禁止直接写中文字符串字面量。
本脚本只在「新增行」上做启发式检查，避免误报仓库内既有的历史中文文案/注释。

启发式规则（新增行）：
  - 行内含 CJK 字符；
  - 且该行不是纯注释行（去掉空白后不以 // 或 # 开头）；
  - 且该行不在 /* ... */ 块注释内部；
  - 且 CJK 出现在引号字符串内（含 Text/AppBar/标题等 UI 场景的字符串字面量）。
满足则记一条 issue，扫描完成返回非 0。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\scan_cn.py [file1 file2 ...]
  不带参数则扫描 git diff HEAD 的全部改动文件。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 含 CJK 的字符串字面量：引号内出现中文字符（含全角，避免误报 URL/ASCII）
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# 单/双引号字符串，内含至少一个 CJK（点号与引号间允许多个字符）
_IN_STRING = re.compile(r"(['\"])[^'\"\n]*[\u4e00-\u9fff\u3400-\u4dbf][^'\"\n]*\1")
_COMMENT_LINE = re.compile(r"^\s*(//|#|\*/|/\*)")


def _added_lines(rel: str) -> list[tuple[int, str]]:
    """返回该文件在 git diff HEAD 中的新增行（行号 + 文本）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "-U0", "HEAD", "--", rel],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return []
    out: list[tuple[int, str]] = []
    new_line_no = 0
    for raw in r.stdout.splitlines():
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)(?:,(\d+))?", raw)
            new_line_no = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            text = raw[1:]
            out.append((new_line_no, text))
            new_line_no += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            pass
        elif not raw.startswith(("diff ", "index ", "---", "+++", "@@")):
            # 上下文行（-U0 下通常无）
            new_line_no += 1
    return out


def _find_issue(line: str) -> str | None:
    if not _CJK.search(line):
        return None
    if _COMMENT_LINE.match(line):
        return None
    m = _IN_STRING.search(line)
    if not m:
        return None
    return line.strip()


def main() -> int:
    args = sys.argv[1:]
    files = args if args else []
    if not files:
        # 无参数：取改动文件
        try:
            r = subprocess.run(
                ["git", "-C", str(ROOT), "diff", "--name-only", "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            files = [f for f in r.stdout.splitlines() if f]
        except Exception:
            files = []
    issues: list[str] = []
    for rel in files:
        if not rel.endswith(".dart"):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        for lineno, text in _added_lines(rel):
            issue = _find_issue(text)
            if issue:
                issues.append(f"{rel}:{lineno}: {issue}")
    if issues:
        print("scan_cn: 发现新增硬编码中文 UI 文案（应改用 ARB key）：")
        for i in issues:
            print("  " + i)
        print(f"scan_cn: 共 {len(issues)} 处")
        return 1
    print(f"scan_cn: OK（扫描 {len(files)} 个文件，无新增硬编码中文 UI 文案）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
