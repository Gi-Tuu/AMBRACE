"""安全文本编辑工具：内容匹配替换，自动保留原文件换行风格（CRLF/LF）。

用法：
  backend\\.venv\\Scripts\\python.exe scripts/edit.py <patch.json> [--check]

patch.json 格式（数组）：
  [
    {"file": "backend/app/xxx.py", "old": "旧文本（子串）", "new": "新文本"},
    ...
  ]

特性：
- 按内容匹配（不用行号，避免行号偏移误删）
- old 在文件中必须唯一（可传 count 字段覆盖校验）
- 自动保留原文件换行风格：CRLF 文件写回 CRLF，LF 写回 LF
- --check 时对 .py 文件做 py_compile 语法校验
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def apply_patch(patch: dict) -> bool:
    rel = patch["file"]
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"[edit.py] 文件不存在: {rel}")
    data = path.read_bytes()
    crlf = b"\r\n" in data
    text = data.decode("utf-8").replace("\r\n", "\n")
    old = patch["old"]
    new = patch["new"]
    count = text.count(old)
    expect = patch.get("count", 1)
    if count != expect:
        raise SystemExit(f"[edit.py] {rel}: 期望匹配 {expect} 处，实际 {count} 处 -> {old[:60]!r}")
    text = text.replace(old, new)
    new_data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
    if new_data == data:
        print(f"[edit.py] {rel}: 无变化（内容一致）")
        return False
    path.write_bytes(new_data)
    print(f"[edit.py] {rel}: 已替换 {count} 处（{'CRLF' if crlf else 'LF'}）")
    if patch.get("check", True) and rel.endswith(".py"):
        subprocess.run(
            [str(ROOT / "backend/.venv/Scripts/python.exe"), "-m", "py_compile", str(path)],
            check=True,
        )
        print(f"[edit.py] {rel}: py_compile OK")
    return True


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        patches = json.load(f)
    changed = 0
    for p in patches:
        if apply_patch(p):
            changed += 1
    print(f"[edit.py] 完成：{changed}/{len(patches)} 个文件有改动")
    if "--check" in sys.argv:
        # 全量语法校验
        py_files = sorted((ROOT / "backend/app").rglob("*.py"))
        py = str(ROOT / "backend/.venv/Scripts/python.exe")
        for f in py_files:
            subprocess.run([py, "-m", "py_compile", str(f)], check=True)
        print(f"[edit.py] py_compile 全量校验 OK（{len(py_files)} 个文件）")


if __name__ == "__main__":
    main()
