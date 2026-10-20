# -*- coding: utf-8 -*-
"""可选的 PWA 同源静态入口（M1a-1，2026-10-16）。

只在**显式配置**一个已完成的 Web build 目录时才把它的公开静态产物挂到固定 `/pwa/` 前缀；
默认 `pwa_static_dir=""` ⇒ **完全不挂载**（本机存在 `pwa/build/web` 也不会自动启用，
避免默默影响现有生产实例）。这是部署接线，不是授权：本模块不创建目录、不改配置、不重启任何进程。

边界与已踩过的坑：
- 只服务完整构建的已知根文件与assets/canvaskit/icons，拒绝隐藏文件、源码与数据库；不自动创建目录。
- 路径穿越（`../`、绝对路径、编码变体）与**越界软链接**由 Starlette `StaticFiles` 的
  `realpath + commonpath` 校验拒掉，本文件不改这套机制，只补测试钉住行为。
- 未配置 / 目录不存在 / 缺 `index.html` **分三态**给诊断串；默认未配置是正常态，
  显式配置错了要能看出原因——绝不 200 返回"加载中"或莫名挂住。
- `/pwa`（无尾斜杠）→ 重定向 `/pwa/`；目录请求 → `index.html`；不存在的 JS/资源 → 明确 404。
- 入口、`sw.js`、`manifest.json` 与 bootstrap 加 `Cache-Control: no-store`，避免陈旧缓存挡住升级。
- **不碰** `/api/v1/...`、`/uploads/...`、健康路由与 CORS；不新增全局中间件。
"""
from __future__ import annotations

import os
from pathlib import Path

from starlette.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

# 固定前缀（唯一入口）；不放根路径，避免把 API 或项目根盖住。
PWA_MOUNT_PATH = "/pwa"
INDEX_FILE = "index.html"
REQUIRED_BUILD_FILES = (INDEX_FILE, "main.dart.js", "flutter_bootstrap.js", "manifest.json", "sw.js", "offline.html")
PUBLIC_ROOT_FILES = frozenset(REQUIRED_BUILD_FILES + ("flutter.js", "flutter_service_worker.js", "version.json", "favicon.png", "404.html"))
PUBLIC_ASSET_DIRS = frozenset({"assets", "canvaskit", "icons"})

# 必须显式禁缓存的三类入口文件：陈旧缓存会挡住「升级」这件事本身。
# flutter_bootstrap.js / flutter.js 是引导脚本（未被内容哈希命名），同样按入口处理。
NO_STORE_NAMES = frozenset({
    INDEX_FILE, "sw.js", "manifest.json", "flutter_bootstrap.js", "flutter.js",
})

# 诊断串前缀（稳定标识，供测试与日志判读；后面接具体路径）。
REASON_NOT_CONFIGURED = "not_configured"
REASON_DIR_MISSING = "directory_missing"
REASON_ENTRY_MISSING = "entry_missing"
REASON_READY = "ready"


class PwaStaticFiles(StaticFiles):
    """带「入口禁缓存」的静态文件服务（目录→index.html、穿越/越界软链拒之门外沿用父类）。

    继承而非重写：`lookup_path` 的 realpath+commonpath 校验、目录重定向、404 语义都是
    经过验证的行为；这里只额外给入口类文件打 `Cache-Control: no-store`。
    """

    def __init__(self, directory: str) -> None:
        # check_dir=False：存在性由 prepare_pwa_static 负责判（分三态诊断），此处不重复。
        super().__init__(directory=directory, html=True, check_dir=False)

    async def get_response(self, path, scope):
        rel = path.replace("\\", "/").strip("/")
        if rel in ("", "."):
            rel = INDEX_FILE
        parts = rel.split("/")
        if (any(part.startswith(".") for part in parts)
                or (rel not in PUBLIC_ROOT_FILES and parts[0] not in PUBLIC_ASSET_DIRS)
                or Path(rel).suffix.lower() in {".py", ".dart", ".db", ".sqlite", ".sqlite3", ".env"}):
            raise HTTPException(status_code=404)
        full_path, _stat = self.lookup_path(path)
        if full_path:
            target = Path(full_path).relative_to(Path(self.directory).resolve()).as_posix()
            target_parts = target.split("/")
            if (target not in ("", ".") and
                    (any(part.startswith(".") for part in target_parts)
                     or (target not in PUBLIC_ROOT_FILES and target_parts[0] not in PUBLIC_ASSET_DIRS)
                     or Path(target).suffix.lower() in {".py", ".dart", ".db", ".sqlite", ".sqlite3", ".env"})):
                raise HTTPException(status_code=404)
        response = await super().get_response(path, scope)
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name in ("", ".", ".."):
            name = INDEX_FILE
        if name in NO_STORE_NAMES:
            response.headers["Cache-Control"] = "no-store"
        return response


def resolve_pwa_static_dir(configured: str, project_root: os.PathLike | str) -> str:
    """把配置解析为绝对目录（相对路径按 project_root）；不创建、不校验存在性。

    配置为空 ⇒ 返回空串（＝未配置，不是路径）。
    """
    raw = (configured or "").strip()
    if not raw:
        return ""
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(project_root) / candidate
    return str(candidate)


def prepare_pwa_static(
    configured: str, project_root: os.PathLike | str
) -> tuple[PwaStaticFiles | None, str]:
    """返回 `(可挂载的静态 app 或 None, 诊断串)`。

    诊断串：`not_configured` / `directory_missing: <path>` / `entry_missing: <path>` /
    `build_incomplete: <files>` / `ready: <path>`。任何一态都不创建目录、不改动文件系统。
    """
    resolved = resolve_pwa_static_dir(configured, project_root)
    if not resolved:
        return None, REASON_NOT_CONFIGURED
    if not os.path.isdir(resolved):
        return None, f"{REASON_DIR_MISSING}: {resolved}"
    if not os.path.isfile(os.path.join(resolved, INDEX_FILE)):
        return None, f"{REASON_ENTRY_MISSING}: {os.path.join(resolved, INDEX_FILE)}"
    missing = [name for name in REQUIRED_BUILD_FILES if not os.path.isfile(os.path.join(resolved, name))]
    if missing:
        return None, f"build_incomplete: {', '.join(missing)}"
    return PwaStaticFiles(resolved), f"{REASON_READY}: {resolved}"
