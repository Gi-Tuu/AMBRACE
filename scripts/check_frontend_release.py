#!/usr/bin/env python3
"""前端三包发布依赖契约校验（PWA CI/发布闭环，2026-10-16）。

目的：公开仓是裁剪快照（`docs/` 除 `docs/changelog.md` 外全部排除），发布源码包也只带
git 跟踪文件减去内部/隐私清单。于是「三包源码 + 相对 path 依赖在干净 checkout 里可解析」
这件事必须**只看源码本身**就能验，不能依赖内部文档或仓库外报告。

拦的是「本机绿、出包/公开形态红」这一类：
- core / 手机 / PWA 三包 pubspec 缺一件 ⇒ path 依赖 `../ambrace_core` 解析不到；
- core 的入口或**条件导入分支**被删（xhr_stream_adapter 的 web/stub 两支）⇒ 干净构建在
  条件导入处编译失败，而本机因缓存/工作区残留看不出来；
- PWA 的 web 入口（index.html / sw.js / manifest.json）缺失 ⇒ release build 产物无法托管；
- 手机壳把转发 core 的文件删了 ⇒ 旧 import 路径失效、类型不再是同一个。

**只做相对路径静态解析 + 存在性校验**：不解析 pub 依赖图、不联网、不碰 git，也不读 docs
（`docs/changelog.md` 在不在都不影响结论）。退出码 0=通过、1=有缺失、2=用法错误。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 三包目录与各自 pubspec 声明的包名（dependency key 必须与包名一致，否则干净 pub get 解析不到）
CORE_DIR = "ambrace_core"
PACKAGES = {CORE_DIR: "ambrace_core", "flutter_app": "ai_companion", "pwa": "ambrace_pwa"}

# 手机壳转发 core 的三个文件（旧 import 路径保持不变，实现唯一）
PHONE_FORWARDS = (
    "flutter_app/lib/models/message.dart",
    "flutter_app/lib/services/api_exception.dart",
    "flutter_app/lib/services/server_identity.dart",
)
# PWA Web 入口三件套
PWA_WEB_ENTRY = ("pwa/web/index.html", "pwa/web/sw.js", "pwa/web/manifest.json")
# core 公共入口（其余 lib 由相对导入闭包覆盖）
CORE_ENTRY = f"{CORE_DIR}/lib/ambrace_core.dart"

_QUOTED = re.compile(r"""['"]([^'"]+\.dart)['"]""")
_DIRECTIVE = re.compile(r"^\s*(?:import|export)\s+(.*?);", re.MULTILINE | re.DOTALL)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _rel(root: Path, p: Path) -> str:
    try:
        return os.path.relpath(str(p), str(root)).replace("\\", "/")
    except ValueError:
        return str(p)


def _top_value(lines: list[str], key: str) -> str | None:
    """取顶格 `key: value` 的值（如 pubspec 的 name）。"""
    for line in lines:
        if _indent(line) == 0 and line.startswith(key + ":"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _section(lines: list[str], header: str) -> list[str]:
    """取顶格 `header`（如 `dependencies:`）到下一个顶格键之间的行。"""
    out: list[str] = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if _indent(line) == 0 and stripped == header:
                started = True
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if _indent(line) == 0:
            break
        out.append(line)
    return out


def _path_dependency(lines: list[str], pkg: str) -> str | None:
    """`dependencies:` 段里 `pkg:` 下的 `path:` 值；没有则 None。"""
    collecting = False
    for line in _section(lines, "dependencies:"):
        ind = _indent(line)
        if ind == 2 and line.strip() == pkg + ":":
            collecting = True
            continue
        if collecting:
            if ind <= 2:
                break
            entry = line.strip()
            if entry.startswith("path:"):
                return entry.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _unresolved_relative_imports(dart_file: Path) -> list[str]:
    """该 dart 文件里解析不到的相对 import/export 目标（含条件导入的每一支）。"""
    missing = []
    text = dart_file.read_text(encoding="utf-8", errors="replace")
    for directive in _DIRECTIVE.finditer(text):
        for target in _QUOTED.findall(directive.group(1)):
            if target.startswith(("package:", "dart:")):
                continue
            if not (dart_file.parent / target).resolve().is_file():
                missing.append(target)
    return missing


def _dart_closure_problems(root: Path, lib_dir: str) -> list[str]:
    """扫描共享包 lib 下全部 dart 的相对导入闭包（part 指令跳过：生成物非源码契约）。"""
    problems: list[str] = []
    base = root / lib_dir
    if not base.is_dir():
        return [f"共享包目录缺失：{lib_dir}/"]
    for dart in sorted(base.rglob("*.dart")):
        for target in _unresolved_relative_imports(dart):
            problems.append(f"相对导入解析不到：{_rel(root, dart)} → '{target}'")
    return problems


def check(root: Path) -> list[str]:
    """返回问题清单（空列表＝三包发布依赖契约成立）。纯函数，便于 tmp_path 正反例。"""
    root = Path(root)
    problems: list[str] = []
    pubspec_lines: dict[str, list[str]] = {}

    # 1. 三包 pubspec 存在且包名正确（dependency key 与包名不一致时干净 pub get 会失败）
    for pkg_dir, pkg_name in PACKAGES.items():
        pubspec = root / pkg_dir / "pubspec.yaml"
        if not pubspec.is_file():
            problems.append(f"缺 pubspec：{pkg_dir}/pubspec.yaml")
            continue
        lines = pubspec.read_text(encoding="utf-8", errors="replace").splitlines()
        pubspec_lines[pkg_dir] = lines
        got = _top_value(lines, "name")
        if got != pkg_name:
            problems.append(
                f"{pkg_dir}/pubspec.yaml 的 name 是 {got!r}，应为 {pkg_name!r}"
                "（dependency key 必须与包名一致）"
            )

    # 2. core 公共入口 + lib 相对/条件导入闭包
    if not (root / CORE_ENTRY).is_file():
        problems.append(f"缺核心入口：{CORE_ENTRY}")
    problems += _dart_closure_problems(root, f"{CORE_DIR}/lib")

    if not (root / "pwa/lib/main.dart").is_file():
        problems.append("缺 PWA 主入口：pwa/lib/main.dart")

    # 3. PWA web 入口 + lib 相对导入闭包
    for rel in PWA_WEB_ENTRY:
        if not (root / rel).is_file():
            problems.append(f"缺 PWA web 入口：{rel}")
    problems += _dart_closure_problems(root, "pwa/lib")

    # 4. 手机壳转发文件仍在且仍引用共享核心（被掏空＝实现被复制回来了）
    for rel in PHONE_FORWARDS:
        path = root / rel
        if not path.is_file():
            problems.append(f"缺手机壳转发文件：{rel}")
            continue
        if "ambrace_core" not in path.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"{rel} 没有引用共享核心 ambrace_core（转发实现可能被复制回来）")

    # 5. 两个消费者从各自目录解析 `../ambrace_core`，必须真正指向包内 core（不是字符串存在）
    core_dir = root / CORE_DIR
    if not core_dir.is_dir():
        problems.append(f"缺共享核心目录：{CORE_DIR}/")
    else:
        core_resolved = os.path.normcase(os.path.abspath(str(core_dir.resolve())))
        for pkg_dir in ("flutter_app", "pwa"):
            lines = pubspec_lines.get(pkg_dir)
            if lines is None:
                continue
            dep = _path_dependency(lines, "ambrace_core")
            if dep is None:
                problems.append(f"{pkg_dir}/pubspec.yaml 未声明 ambrace_core 的 path 依赖")
                continue
            if Path(dep).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", dep) or dep.startswith("\\"):
                problems.append(f"{pkg_dir} 的 ambrace_core 必须使用相对 path，不能使用绝对路径")
                continue
            resolved = (root / pkg_dir / dep).resolve()
            if os.path.normcase(os.path.abspath(str(resolved))) != core_resolved:
                problems.append(
                    f"{pkg_dir} 的 ambrace_core 依赖指向 {dep!r} → {resolved}，未指向包内 {CORE_DIR}/"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="前端三包发布依赖契约校验（离线，只读源码）")
    ap.add_argument("--root", default=None, help="源码根目录（默认本脚本上一级目录）")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    if not root.is_dir():
        print(f"[check_frontend_release] 用法错误：根目录不存在 {root}")
        return 2
    problems = check(root)
    print(f"[check_frontend_release] 根目录＝{root}；问题数 {len(problems)}")
    for p in problems:
        print("  [x] " + p)
    if problems:
        print("[check_frontend_release] 结论：三包发布依赖契约不成立——按上面逐条修，别出包/别推。")
        return 1
    print("[check_frontend_release] 结论：三包 pubspec + 相对依赖解析 + core 条件导入闭包 + PWA web 入口 齐备。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
