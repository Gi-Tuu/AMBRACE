#!/usr/bin/env python3
"""Build, version, deploy and verify the PWA under the backend /pwa/ prefix.

Why this exists: the old deployment was a manual copy plus a fixed service-worker
version (`v3`).  The same version key made a redeploy reuse the previous shell
cache, and the runtime directory lived only in one machine's .env.  This script
makes the deployment reproducible and gives the service worker a build-derived
cache generation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "pwa" / "build" / "web"
RUNTIME_ROOT = Path(os.environ.get("AMBRACE_PWA_RUNTIME", ""))
if not RUNTIME_ROOT.is_absolute() or str(RUNTIME_ROOT) == ".":
    # 2026-11-23：默认落点从 %TEMP% 改成每用户持久目录。事故：临时目录被系统清理后
    # .env 的 PWA_STATIC_DIR 指向不存在的路径 → /pwa/ 404，控制台入口「完全进不去」。
    # 仍然不写作者机器绝对路径（环境变量优先，其次 LOCALAPPDATA/home）。
    _base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    RUNTIME_ROOT = Path(_base) / "AMBRACE" / "pwa-runtime"
ENV_FILE = ROOT / ".env"
BUILD_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
PLACEHOLDER = "__PWA_BUILD_ID__"


def stamp_service_worker(text: str, build_id: str) -> str:
    """Replace the explicit build-id placeholder, or fail closed.

    Kept as a pure function so the gate can be tested without building Flutter.
    """
    if not BUILD_ID_RE.fullmatch(build_id):
        raise ValueError("build_id must be a hex string of at least 12 chars")
    if PLACEHOLDER not in text:
        raise ValueError("service worker has no __PWA_BUILD_ID__ placeholder")
    stamped = text.replace(PLACEHOLDER, build_id)
    if PLACEHOLDER in stamped:
        raise ValueError("service worker still contains the build-id placeholder")
    if build_id not in stamped:
        raise ValueError("stamped service worker does not contain build id")
    return stamped


def current_static_dir() -> Path | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("PWA_STATIC_DIR="):
            value = line.split("=", 1)[1].strip()
            return Path(value) if value else None
    return None


def check(target: Path | None = None) -> list[str]:
    """Return a list of concrete problems; empty list means the gate is green."""
    target = target or current_static_dir()
    problems: list[str] = []
    if target is None:
        return ["PWA_STATIC_DIR is not set"]
    if not target.is_dir():
        return [f"PWA_STATIC_DIR is not a directory: {target}"]
    index = target / "index.html"
    sw = target / "sw.js"
    if not index.is_file():
        problems.append(f"missing index.html: {index}")
    if not sw.is_file():
        problems.append(f"missing sw.js: {sw}")
    else:
        text = sw.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            problems.append("sw.js still contains __PWA_BUILD_ID__")
        m = re.search(r"const\s+VERSION\s*=\s*`[^`]*\$\{BUILD_ID\}[^`]*`", text)
        if not m:
            problems.append("sw.js VERSION is not build-id based")
        ids = re.findall(r'\b[0-9a-f]{12,64}\b', text)
        if not ids:
            problems.append("sw.js has no build-id token")
    bootstrap = target / "flutter_bootstrap.js"
    if not bootstrap.is_file():
        problems.append(f"missing flutter_bootstrap.js: {bootstrap}")
    elif "canvasKitBaseUrl" not in bootstrap.read_text(encoding="utf-8", errors="replace"):
        problems.append("flutter_bootstrap.js has no local canvasKitBaseUrl config")
    if index.is_file():
        html = index.read_text(encoding="utf-8", errors="replace")
        if "base href=\"/pwa/\"" not in html:
            problems.append("index.html base href is not /pwa/")
        if "theme-color\" content=\"#6C63FF\"" in html:
            problems.append("index.html still uses the old purple theme-color")
    return problems


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "ambrace-pwa-deploy/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", errors="replace")


def deploy(args: argparse.Namespace) -> int:
    if not (ROOT / "pwa" / "pubspec.yaml").exists():
        raise SystemExit("pwa/pubspec.yaml not found; run from the project checkout")
    flutter = Path(r"D:\flutter\bin\flutter.bat")
    cmd = [str(flutter), "build", "web", "--release", "--base-href=/pwa/"]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT / "pwa", check=True)
    main_js = BUILD_DIR / "main.dart.js"
    if not main_js.is_file():
        raise SystemExit(f"build output missing: {main_js}")
    build_id = hashlib.sha256(main_js.read_bytes()).hexdigest()[:16]
    stamp = datetime.now().strftime("%Y%m%d%H%M")
    name = args.name or f"v3.6.8-workbench-{stamp}"
    target = RUNTIME_ROOT / name
    if target.exists():
        raise SystemExit(f"runtime target already exists (refusing to overwrite): {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(BUILD_DIR, target)

    # /pwa/ 子路径下如果让 Flutter 自动选 CanvasKit，会请求 gstatic；无外网时首帧卡死。
    # 把本地 canvaskit/ 写进生成 bootstrap 的 loader config（不能只依赖 window.flutterConfiguration）。
    bootstrap_path = target / "flutter_bootstrap.js"
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    bootstrap, n = re.subn(
        r"_flutter\.loader\.load\(\{\s*serviceWorkerSettings:",
        '_flutter.loader.load({\n  config: { canvasKitBaseUrl: "canvaskit/" },\n  serviceWorkerSettings:',
        bootstrap,
        count=1,
    )
    if n != 1:
        raise SystemExit("cannot inject local CanvasKit config into flutter_bootstrap.js")
    bootstrap_path.write_text(bootstrap, encoding="utf-8")

    sw_path = target / "sw.js"
    sw_path.write_text(
        stamp_service_worker(sw_path.read_text(encoding="utf-8"), build_id),
        encoding="utf-8",
    )

    old_dir = current_static_dir()
    backup = ENV_FILE.with_name(f".env.bak-pwa-{stamp}")
    if ENV_FILE.exists():
        shutil.copy2(ENV_FILE, backup)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    new_line = f"PWA_STATIC_DIR={target.as_posix()}"
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith("PWA_STATIC_DIR="):
            lines[i] = new_line
            replaced = True
    if not replaced:
        lines.append(new_line)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "build_id": build_id,
        "target": target.as_posix(),
        "old": old_dir.as_posix() if old_dir else None,
        "env_backup": backup.as_posix() if backup.exists() else None,
    }
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    with (RUNTIME_ROOT / "deployments.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))

    if args.restart:
        manager = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
        subprocess.run(
            [str(manager), str(ROOT / "scripts" / "server_manager.py"), "restart"],
            cwd=ROOT,
            check=True,
        )
        time.sleep(args.wait)
        status, html = http_get("http://127.0.0.1:8000/pwa/")
        _, sw = http_get("http://127.0.0.1:8000/pwa/sw.js")
        if status != 200:
            raise SystemExit(f"/pwa/ returned {status}")
        if "base href=\"/pwa/\"" not in html:
            raise SystemExit("/pwa/ index does not use base href /pwa/")
        if build_id not in sw:
            raise SystemExit("/pwa/sw.js does not expose the deployed build id")
        print(f"verified http://127.0.0.1:8000/pwa/ build_id={build_id}")

    problems = check(target)
    if problems:
        print("deployment gate FAILED:", *problems, sep="\n- ")
        return 1
    print(f"deployment gate green: {target} build_id={build_id}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deploy", action="store_true", help="build and deploy")
    ap.add_argument("--check", action="store_true", help="check current deployment")
    ap.add_argument("--target", help="runtime directory for --check")
    ap.add_argument("--name", help="runtime directory name for --deploy")
    ap.add_argument("--restart", action="store_true", help="restart backend and smoke /pwa/")
    ap.add_argument("--wait", type=float, default=8.0, help="seconds after restart")
    args = ap.parse_args()
    if args.check:
        problems = check(Path(args.target) if args.target else None)
        if problems:
            print("deployment gate FAILED:", *problems, sep="\n- ")
            return 1
        print(f"deployment gate green: {args.target or current_static_dir()}")
        return 0
    if args.deploy:
        return deploy(args)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
