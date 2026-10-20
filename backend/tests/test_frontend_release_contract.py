# -*- coding: utf-8 -*-
"""前端三包发布依赖契约守卫（PWA CI/发布闭环，2026-10-16）。

为什么要有这条：公开仓是裁剪快照、发布源码包也只带 git 跟踪文件减去内部清单——两种形态里
都没有内部 docs。于是「三包 pubspec + `../ambrace_core` 相对依赖 + core 条件导入闭包 +
PWA web 入口」必须只看源码就能验；「本机全绿但干净 checkout 里 path 依赖解析不到、条件导入
分支被删」这类问题，正是这条守住的（CI 各档检出的就是裁剪树）。

**纯合成正反例**：tmp_path 造三包树（正确相对依赖 + 仅 docs/changelog 的公开形态）⇒ 通过；
删 core/pubspec、删条件导入分支、错 path、缺 PWA web 入口、转发文件被掏空 ⇒ 各自真失败。
本文件不硬依赖 docs 或仓库外报告——公开快照里只有 docs/changelog.md 时这条照样真跑（不 skip、不空扫）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load_checker():
    path = REPO / "scripts" / "check_frontend_release.py"
    spec = importlib.util.spec_from_file_location("frontend_release_check", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core_check = _load_checker()


def _synthetic_tree(root: Path, *, core_dep: str = "../ambrace_core") -> Path:
    """最小但结构完整的公开形态树：三包 + 正确相对依赖 + core 条件导入两支 + 仅公告的 docs。"""

    def w(rel: str, text: str = "") -> None:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    # 共享核心
    w("ambrace_core/pubspec.yaml", "name: ambrace_core\npublish_to: none\n")
    w("ambrace_core/lib/ambrace_core.dart", "library;\n")
    w("ambrace_core/lib/src/api/amb_api_client.dart", "import 'xhr_stream_adapter.dart';\n")
    w(
        "ambrace_core/lib/src/api/xhr_stream_adapter.dart",
        "export 'xhr_stream_adapter_stub.dart'\n"
        "    if (dart.library.js_interop) 'xhr_stream_adapter_web.dart';\n",
    )
    w("ambrace_core/lib/src/api/xhr_stream_adapter_stub.dart", "// stub\n")
    w("ambrace_core/lib/src/api/xhr_stream_adapter_web.dart", "// web\n")
    # 手机壳（转发 core，旧 import 路径不变）
    w(
        "flutter_app/pubspec.yaml",
        "name: ai_companion\ndependencies:\n  ambrace_core:\n    path: %s\n" % core_dep,
    )
    w(
        "flutter_app/lib/models/message.dart",
        "export 'package:ambrace_core/ambrace_core.dart' show ChatMessage;\n",
    )
    w(
        "flutter_app/lib/services/api_exception.dart",
        "export 'package:ambrace_core/ambrace_core.dart' show ApiException;\n",
    )
    w(
        "flutter_app/lib/services/server_identity.dart",
        "import 'package:ambrace_core/ambrace_core.dart' as core;\n",
    )
    # PWA 薄壳
    w(
        "pwa/pubspec.yaml",
        "name: ambrace_pwa\ndependencies:\n  ambrace_core:\n    path: %s\n" % core_dep,
    )
    w("pwa/lib/main.dart", "import 'package:ambrace_core/ambrace_core.dart';\n")
    w("pwa/web/index.html", "<html></html>\n")
    w("pwa/web/sw.js", "// sw\n")
    w("pwa/web/manifest.json", "{}\n")
    # 公开形态：内部 docs 不存在，只有用户公告
    w("docs/changelog.md", "## 2026-10-16（x）\n")
    return root


def test_真实仓库三包发布依赖契约成立():
    """本仓（本地＝工作树；CI＝裁剪树）都必须真的过，不允许 skip 拿绿。"""
    problems = core_check.check(REPO)
    assert problems == [], "三包发布依赖契约不成立：\n" + "\n".join(problems)


def test_合成公开形态正例通过(tmp_path):
    root = _synthetic_tree(tmp_path / "ok")
    assert core_check.check(root) == []


def test_缺core_pubspec必须失败(tmp_path):
    root = _synthetic_tree(tmp_path / "nopub")
    (root / "ambrace_core" / "pubspec.yaml").unlink()
    problems = core_check.check(root)
    assert any("ambrace_core/pubspec.yaml" in p for p in problems), problems


def test_缺条件导入分支必须失败(tmp_path):
    """删掉 stub 支后，xhr_stream_adapter.dart 的 export 目标解析不到 ⇒ 必须红。"""
    root = _synthetic_tree(tmp_path / "nobranch")
    (root / "ambrace_core" / "lib" / "src" / "api" / "xhr_stream_adapter_stub.dart").unlink()
    problems = core_check.check(root)
    assert any("xhr_stream_adapter_stub.dart" in p for p in problems), problems


def test_错相对path必须失败(tmp_path):
    """两个消费者把 `../ambrace_core` 指偏 ⇒ 解析结果不等于包内 core，必须红。"""
    root = _synthetic_tree(tmp_path / "badpath", core_dep="../ambrace_core_x")
    problems = core_check.check(root)
    assert any("未指向包内" in p for p in problems), problems


def test_缺PWA_web入口必须失败(tmp_path):
    root = _synthetic_tree(tmp_path / "noweb")
    (root / "pwa" / "web" / "sw.js").unlink()
    problems = core_check.check(root)
    assert any("pwa/web/sw.js" in p for p in problems), problems


def test_转发文件被掏空必须失败(tmp_path):
    """转发文件被换成自带一份实现（不再引用 core）⇒ 必须红，守住「单一真相」。"""
    root = _synthetic_tree(tmp_path / "hollow")
    (root / "flutter_app" / "lib" / "models" / "message.dart").write_text(
        "class ChatMessage {}\n", encoding="utf-8"
    )
    problems = core_check.check(root)
    assert any("message.dart" in p for p in problems), problems


def test_合成树结论不依赖docs其余文件(tmp_path):
    """公开快照只有 docs/changelog.md 也照样真跑：删掉它结论不变，且问题里不出现 docs。"""
    root = _synthetic_tree(tmp_path / "docs_only")
    (root / "docs" / "changelog.md").unlink()
    problems = core_check.check(root)
    assert problems == []
    assert all("docs" not in p for p in problems)


def test_缺多行条件导入web分支必须失败(tmp_path):
    root = _synthetic_tree(tmp_path / "no_web_branch")
    (root / "ambrace_core/lib/src/api/xhr_stream_adapter_web.dart").unlink()
    assert any("xhr_stream_adapter_web.dart" in p for p in core_check.check(root))


def test_缺PWA主入口必须失败(tmp_path):
    root = _synthetic_tree(tmp_path / "no_main")
    (root / "pwa/lib/main.dart").unlink()
    assert any("pwa/lib/main.dart" in p for p in core_check.check(root))


def test_指向本机core的绝对路径也必须失败(tmp_path):
    root = _synthetic_tree(tmp_path / "absolute")
    pubspec = root / "pwa/pubspec.yaml"
    pubspec.write_text(pubspec.read_text(encoding="utf-8").replace(
        "../ambrace_core", (root / "ambrace_core").as_posix()), encoding="utf-8")
    assert any("相对 path" in p for p in core_check.check(root))


def test_真实出包流程在合成树保留共享依赖并排除工作文档(tmp_path, monkeypatch):
    root = _synthetic_tree(tmp_path / "source")
    spec = importlib.util.spec_from_file_location("frontend_release_pack", REPO / "scripts/make_release.py")
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    licenses = list(release.LICENSE_CORE) + [release.LICENSE_MANIFEST] + list(release.MODEL_LICENSE_SOURCES.values())
    for rel in licenses:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic license fixture", encoding="utf-8")
    excluded = ["docs/plans.md", "docs/reviews/REPORT.md", ".agents/notes.md", "AGENTS.md", "HANDOFF.md"]
    for rel in excluded:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic private work note", encoding="utf-8")
    tracked = [f.relative_to(root).as_posix() for f in root.rglob("*") if f.is_file()]
    # 模拟未跟踪的生成物：真实 tracked_files() 的输入就不包含这些文件。
    for rel in ("pwa/build/generated.txt", "ambrace_core/.dart_tool/cache.txt"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("untracked generated fixture", encoding="utf-8")
    dest = tmp_path / "package"
    monkeypatch.setattr(release, "PROJECT_ROOT", str(root))
    monkeypatch.setattr(release, "OUT_DIR", str(dest))
    monkeypatch.setattr(release, "OUT_ROOT", str(tmp_path))
    monkeypatch.setattr(release, "tracked_files", lambda: tracked)
    monkeypatch.setattr(release.sys, "argv", ["make_release.py"])
    release.main()
    assert core_check.check(dest) == []
    landed = {f.relative_to(dest).as_posix() for f in dest.rglob("*") if f.is_file()}
    assert "docs/changelog.md" in landed
    assert not any(rel in landed for rel in excluded)
    assert not any("/build/" in rel or "/.dart_tool/" in rel for rel in landed)
    assert release.required_license_files(root) == []
    assert release.verify_license_bundle(dest, root) == []
