#!/usr/bin/env python3
"""扫描并收敛 Flutter App 硬编码色值。

用法（在项目根 AMBRACE_ROOT 或任意目录执行均可，路径在脚本内固定）：
  python scripts/scan_hardcoded_colors.py                # 仅扫描统计
  python scripts/scan_hardcoded_colors.py --dry-run      # 扫描并预览将替换的内容（不写入）
  python scripts/scan_hardcoded_colors.py --apply        # 扫描并执行 token 收敛替换

功能：
- 扫描 flutter_app/lib 下所有 .dart 的硬编码色值：
    * Color(0x......)  /  const Color(0x......)
    * 文本 #hex（如 #FF8E8E93）
    * Colors.xxx 使用
- --apply 时，把高频、语义明确的 Color(0xHEX) 收敛为 AppColors.* 引用：
    * 自动保留原文件 CRLF/LF
    * 自动在文件头部注入 package:ai_companion/theme/tokens.dart 导入（仅当有替换）
    * 跳过 lib/theme/app_theme.dart（已手工引用 token）与 lib/theme/tokens.dart 本身

收敛范围：仅处理「高频且语义清晰」的重复色值，第一轮不追求清空所有 Colors.*（很多保留为语义色）。
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "flutter_app" / "lib"
IMPORT_LINE = 'import "package:ai_companion/theme/tokens.dart";'

# Color(0xHEX) -> AppColors.* 语义收敛表（首轮收敛的高频重复项）
MAPPING: dict[str, str] = {
    "FF8E8E93": "AppColors.textSecondary",  # 次文字
    "FF007AFF": "AppColors.accent",         # 品牌蓝
    "FFC6C6C8": "AppColors.separator",      # chevron / 分隔灰
    "FFF2F2F7": "AppColors.bgLight",        # 浅底
    "FF1C1C1E": "AppColors.textPrimary",    # 主文字 / 深色卡片
    "FF34C759": "AppColors.success",        # 成功 / 绿
    "FFFF3B30": "AppColors.error",          # 错误 / 红
    "FFFFFFFF": "AppColors.white",          # 白
    "FF6E6E73": "AppColors.textMuted",      # 弱文字
    "FFC7C7CC": "AppColors.textTertiary",   # 三级文字
    "FFD1D1D6": "AppColors.border",         # 边界 / 细线
    # 第二轮新增：剩余高频语义色（文字主次 / 链接 / 对比强调），保持原值，仅收敛为 token 引用
    "FF333333": "AppColors.textStrong",     # 主文字深灰
    "FF666666": "AppColors.textGray",       # 常用灰文字
    "FF4A90D9": "AppColors.accentBlue",     # 链接 / 强调蓝
    "FF576B95": "AppColors.linkBlue",       # 名称 / 链接蓝
    "FFFF7043": "AppColors.compareOrange",  # 对比 / 强调橙（状态对比、蛛网对比）
}

DART = "*.dart"
SKIP = {"app_theme.dart", "tokens.dart"}

# 匹配模式
COLOR_CONST_RE = re.compile(r"(?P<pre>const\s+)Color\(0x(?P<hex>[0-9A-Fa-f]{6,8})\)")
COLOR_BARE_RE = re.compile(r"Color\(0x(?P<hex>[0-9A-Fa-f]{6,8})\)")
HEX_TEXT_RE = re.compile(r"#[0-9A-Fa-f]{6,8}")
COLORS_USE_RE = re.compile(r"(?<!App)Colors\.[A-Za-z_]+")

# 已有的三种匹配计数（用于统计，不参与替换）
_COLOR_LIT_RE = re.compile(r"Color\(0x[0-9A-Fa-f]{6,8}\)")


def dart_files() -> list[Path]:
    return sorted(LIB.rglob(DART))


def scan() -> dict[str, Counter]:
    """返回 {file: Counter(type -> count)}。type 取 const_hex / bare_hex / #hex / Colors.xxx"""
    out: dict[str, Counter] = {}
    for p in dart_files():
        c: Counter = Counter()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        c["const_hex"] += len(COLOR_CONST_RE.findall(text))
        c["bare_hex"] += len(COLOR_BARE_RE.findall(text))
        c["#hex"] += len(HEX_TEXT_RE.findall(text))
        c["Colors.xxx"] += len(COLORS_USE_RE.findall(text))
        # 注意 bare_hex 会把 const 那部分也统计进来（bare 模式会匹配 const Color(...) 的 Color 子串）
        # 但 const 的已经被 const_hex 单独统计；这里把 bare 重复部分扣除，避免重复。
        c["bare_hex"] -= c["const_hex"]
        if sum(c.values()) > 0:
            out[str(p.relative_to(ROOT).as_posix())] = c
    return out


def hex_frequency() -> Counter:
    cnt: Counter = Counter()
    for p in dart_files():
        if p.name in SKIP:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _COLOR_LIT_RE.finditer(text):
            cnt[m.group(0).upper()] += 1
    return cnt


def _insert_import(text: str) -> str:
    if "theme/tokens.dart" in text:
        return text
    # 找到最后一个顶层 import 行，在它后面插入（保持其它行不变）
    lines = text.split("\n")
    last = -1
    for i, line in enumerate(lines):
        if re.match(r'^\s*import\s+["\'].*["\']\s*;', line):
            last = i
    if last < 0:
        # 无 import：插到文件头
        lines.insert(0, IMPORT_LINE)
    else:
        lines.insert(last + 1, IMPORT_LINE)
    return "\n".join(lines)


def _apply_one(path: Path) -> tuple[int, int]:
    """对单个文件执行收敛。返回 (替换数, 剩余 Color(0x) 数)。"""
    data = path.read_bytes()
    crlf = b"\r\n" in data
    text = data.decode("utf-8").replace("\r\n", "\n")
    replaced = 0
    for hexval, token in MAPPING.items():
        upper = hexval.upper()
        lower = hexval.lower()
        # 先处理带 const 的：整段替换成 token（去掉多余 const，避免 unnecessary_const）
        for variant in (upper, lower):
            pat_const = re.compile(r"const\s+Color\(0x" + variant + r"\)")
            text, n = pat_const.subn(token, text)
            replaced += n
            patch = re.compile(r"Color\(0x" + variant + r"\)")
            text, n = patch.subn(token, text)
            replaced += n
    # 若没有实际替换，则不写文件
    if replaced == 0:
        return (0, len(_COLOR_LIT_RE.findall(text)))
    text = _insert_import(text)
    new_data = (text.replace("\n", "\r\n") if crlf else text).encode("utf-8")
    if new_data != data:
        path.write_bytes(new_data)
    return (replaced, len(_COLOR_LIT_RE.findall(text)))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode not in ("scan", "--dry-run", "--apply"):
        print(__doc__)
        return 2

    results = scan()
    total_const = sum(c["const_hex"] for c in results.values())
    total_bare = sum(c["bare_hex"] for c in results.values())
    total_hextext = sum(c["#hex"] for c in results.values())
    total_colors = sum(c["Colors.xxx"] for c in results.values())
    total_hex = total_const + total_bare
    print("== 硬编码色值统计（flutter_app/lib） ==")
    print(f"文件数: {len(results)}")
    print(f"Color(0x) 字面量: {total_hex}  (其中带 const: {total_const}, 裸用: {total_bare})")
    print(f"文本 #hex: {total_hextext}")
    print(f"Colors.* 使用: {total_colors}（本轮不替换，保留语义色）")
    print("\n== 各文件明细（Color(0x) / #hex / Colors.*） ==")
    for f, c in sorted(results.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {f}: hex={c['const_hex'] + c['bare_hex']} #hex={c['#hex']} Colors={c['Colors.xxx']}")

    if mode in ("--dry-run", "--apply"):
        print("\n== 将执行的收敛（高频语义色 -> AppColors.*） ==")
        for f, c in sorted(results.items(), key=lambda kv: -sum(kv[1].values())):
            path = ROOT / f
            if path.name in SKIP:
                print(f"  {f}: [skip]")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            count = 0
            for hx in MAPPING:
                for variant in (hx.upper(), hx.lower()):
                    count += len(re.findall(r"const\s+Color\(0x" + variant + r"\)", text))
                    count += len(re.findall(r"Color\(0x" + variant + r"\)", text))
            if count:
                print(f"  {f}: {count} 处")

        if mode == "--apply":
            print("\n== 执行写入 ==")
            total_replaced = 0
            remaining = 0
            touched = 0
            for f in sorted(results):
                path = ROOT / f
                if path.name in SKIP:
                    continue
                rep, rem = _apply_one(path)
                if rep:
                    touched += 1
                    total_replaced += rep
                    remaining += rem
                    print(f"  {f}: 替换 {rep} 处，剩余 Color(0x) {rem} 处")
            print(f"\n[apply] 共替换 {total_replaced} 处，涉及 {touched} 个文件。")
            print("[apply] 剩余 Color(0x) 字面量计入 remaining 累计（含未映射语义色）。")

    # 展示 Hex 频率 Top（帮助定位下一轮再收敛项）
    freq = hex_frequency()
    print("\n== Color(0x) 值频率 Top15（未映射的留待后续轮次） ==")
    for val, c in freq.most_common(15):
        print(f"  {val}: {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
