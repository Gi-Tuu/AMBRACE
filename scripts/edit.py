"""安全文本编辑工具：内容匹配替换，按行保留原文件行尾（未改动的行逐字节不变）。

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
- old / new 一律按「LF 形式」传入，匹配在 LF 归一化副本上做；但写回时只替换命中的那几段，
  未命中的行保持原样 —— 同一文件里 \\n / \\r\\n / \\r 混排也不会被统一。
  （历史坑：旧实现按「文件里出现任意一处 CRLF 就把整份文件归一为 LF 再全部写回 CRLF」，
   改 docs/plans.md 这种「全文 LF + 恰好 2 行 CRLF」的文件，一次就把 190+ 行全变成 CRLF，
   git diff --numstat 从 1/1 变成 198/198，还得手工修回）
- 新写入文本的行尾取「被替换的那段原文」里出现的行尾（首个换行是 \\r\\n 就用 \\r\\n）；
  该段没有换行时，用整份文件的主导行尾
- --check 时对 .py 文件做 py_compile 语法校验
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_EOL_NAME = {"\r\n": "CRLF", "\r": "CR", "\n": "LF"}


def _to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _norm_bounds(raw: str) -> list[int]:
    """「归一化（LF 形式）下标 -> 原文下标」映射，长度为 len(归一化文本) + 1。

    \\r\\n 与单个 \\r 在归一化后各占一个 \\n，因此归一化第 k 个字符对应原文中那段行尾的起始下标。
    """
    bounds = []
    i = 0
    n = len(raw)
    while i < n:
        bounds.append(i)
        if raw[i] == "\r":
            i += 2 if raw[i + 1 : i + 2] == "\n" else 1
        else:
            i += 1
    bounds.append(n)
    return bounds


def _dominant_eol(raw: str) -> str:
    """整份文件的主导行尾（并列时取 LF）。"""
    crlf = raw.count("\r\n")
    cr = raw.count("\r") - crlf
    lf = raw.count("\n") - crlf
    if crlf > lf and crlf > cr:
        return "\r\n"
    if cr > lf and cr > crlf:
        return "\r"
    return "\n"


def _first_eol(segment: str) -> str:
    """段内首个换行的原始写法；段内没有换行时返回空串。"""
    for i, ch in enumerate(segment):
        if ch == "\r":
            # 2026-09-22 修：`\r\r\n`（双 CR 行尾，见 background_polling_service.dart）此前被读成裸 `\r`，
            # 于是替换段内的新行会写成裸 CR —— 而且两种 numstat 口径都看不出来（静默损坏）。
            if segment[i + 1 : i + 3] == "\r\n":
                return "\r\r\n"
            return "\r\n" if segment[i + 1 : i + 2] == "\n" else "\r"
        if ch == "\n":
            return "\n"
    return ""


def apply_patch(patch: dict) -> bool:
    rel = patch["file"]
    path = ROOT / rel
    if not path.is_file():
        raise SystemExit(f"[edit.py] 文件不存在: {rel}")
    data = path.read_bytes()
    raw = data.decode("utf-8")
    old = patch["old"]
    new = patch["new"]
    if not old:
        raise SystemExit(f"[edit.py] {rel}: old 不能为空")
    text = _to_lf(raw)
    count = text.count(old)
    expect = patch.get("count", 1)
    if count != expect:
        raise SystemExit(f"[edit.py] {rel}: 期望匹配 {expect} 处，实际 {count} 处 -> {old[:60]!r}")
    bounds = _norm_bounds(raw)
    dominant = _dominant_eol(raw)
    lf_new = _to_lf(new)
    pieces = []
    pos = 0       # 原文游标
    start = 0     # 归一化文本游标
    while True:
        s = text.find(old, start)
        if s < 0:
            break
        lo, hi = bounds[s], bounds[s + len(old)]
        pieces.append(raw[pos:lo])
        pieces.append(lf_new.replace("\n", _first_eol(raw[lo:hi]) or dominant))
        pos = hi
        start = s + len(old)
    pieces.append(raw[pos:])
    new_data = "".join(pieces).encode("utf-8")
    if new_data == data:
        print(f"[edit.py] {rel}: 无变化（内容一致）")
        return False
    path.write_bytes(new_data)
    has_crlf = "\r\n" in raw
    print(
        f"[edit.py] {rel}: 已替换 {count} 处（按行保留原行尾；"
        f"主导={_EOL_NAME[dominant]}；含 CRLF={has_crlf}）"
    )
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
