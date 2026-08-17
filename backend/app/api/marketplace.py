"""插件市场 API：内置市场（动态扫描示例目录）+ 远程市场（Phase C 远程生态）

- 列表：扫描 plugins/examples/*/manifest.json 生成内置条目，合并远程缓存条目（同 name 远程覆盖内置）
- 详情：条目信息 + readme 正文（可选）
- 安装：仅主账号；内置复制示例目录 / 远程下载 zip（sha256 + zip 安全校验 + 备份回滚）
- 远程市场：marketplace_config 表（enabled/urls/refresh_interval_hours/allowed_hosts/max_zip_mb）
  + POST /refresh 拉取 https index 缓存到 backend/data/marketplace_cache/
"""
import asyncio
import hashlib
import json
import os
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy import select

from app.auth.deps import get_current_user_id
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.marketplace_config import MarketplaceConfig
from app.plugins import registry
from app.plugins.manifest import load_manifest
from app.plugins.zip_safety import validate_zip_bytes, extract_zip_bytes, ZipSafetyError
from app.utils.logger import get_logger

_logger = get_logger("api.marketplace")

router = APIRouter(prefix="/api/v1/marketplace", tags=["Marketplace"])

# 内置市场补充字段（icon/readme/tags/updated_at），可选
INDEX_FILE = registry.PROJECT_ROOT / "plugins" / "marketplace" / "index.json"

# 远程市场缓存目录
CACHE_DIR = registry.PROJECT_ROOT / "backend" / "data" / "marketplace_cache"
CACHE_META_FILE = CACHE_DIR / "cache_meta.json"
MAX_INDEX_BYTES = 1024 * 1024  # index.json 本体 ≤1MB
INDEX_TIMEOUT = 10.0  # index 下载超时 10s
ZIP_TIMEOUT = 60.0  # zip 下载超时 60s

_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")
_UA = "AICompanion-Marketplace/1.0"

_DEFAULT_CONFIG = {
    "enabled": False,
    "urls": [],
    "refresh_interval_hours": 24,
    "allowed_hosts": [],
    "max_zip_mb": 10,
}


def _is_owner(user_id: int) -> bool:
    from app.config import settings
    return user_id in settings.admin_user_ids  # P1：与 .env ADMIN_USER_IDS 一致


# ---------------- 配置读写 ----------------

async def _load_config() -> dict:
    """读 marketplace_config 单行；无则返回默认"""
    async with async_session_factory() as db:
        row = (await db.execute(select(MarketplaceConfig).order_by(MarketplaceConfig.id))).scalars().first()
    if row is None:
        return dict(_DEFAULT_CONFIG)
    try:
        urls = json.loads(row.urls or "[]")
    except Exception:
        urls = []
    try:
        hosts = json.loads(row.allowed_hosts or "[]")
    except Exception:
        hosts = []
    return {
        "enabled": bool(row.enabled),
        "urls": urls if isinstance(urls, list) else [],
        "refresh_interval_hours": int(row.refresh_interval_hours or 24),
        "allowed_hosts": hosts if isinstance(hosts, list) else [],
        "max_zip_mb": int(row.max_zip_mb or 10),
    }


async def _save_config(cfg: dict) -> None:
    async with async_session_factory() as db:
        row = (await db.execute(select(MarketplaceConfig).order_by(MarketplaceConfig.id))).scalars().first()
        if row is None:
            row = MarketplaceConfig(
                enabled=bool(cfg["enabled"]),
                urls=json.dumps(cfg["urls"], ensure_ascii=False),
                refresh_interval_hours=int(cfg["refresh_interval_hours"]),
                allowed_hosts=json.dumps(cfg["allowed_hosts"], ensure_ascii=False),
                max_zip_mb=int(cfg["max_zip_mb"]),
            )
            db.add(row)
        else:
            row.enabled = bool(cfg["enabled"])
            row.urls = json.dumps(cfg["urls"], ensure_ascii=False)
            row.refresh_interval_hours = int(cfg["refresh_interval_hours"])
            row.allowed_hosts = json.dumps(cfg["allowed_hosts"], ensure_ascii=False)
            row.max_zip_mb = int(cfg["max_zip_mb"])
        await db.commit()


# ---------------- 远程缓存读写 ----------------

def _cache_path(url: str):
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return CACHE_DIR / f"{h}.json"


def _load_cache_meta() -> dict:
    try:
        if CACHE_META_FILE.is_file():
            data = json.loads(CACHE_META_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception as e:
        _logger.warning("marketplace cache_meta 解析失败: %s", e)
    return {}


def _save_cache_meta(meta: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_META_FILE.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _load_remote_items() -> list[dict]:
    """读取全部缓存文件，标注 source=remote:<market>"""
    out: list[dict] = []
    if not CACHE_DIR.is_dir():
        return out
    for f in CACHE_DIR.glob("*.json"):
        if f.name == "cache_meta.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        market = str(data.get("market", f.stem))
        items = data.get("items")
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            row = dict(it)
            row["source"] = f"remote:{market}"
            row["installed"] = False
            out.append(row)
    return out


# ---------------- 远程拉取与校验 ----------------

def _fetch_bytes(url: str, timeout: float, max_bytes: int) -> bytes:
    """同步 urllib 下载（在 to_thread 中调用）"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    # P1 安全加固（2026-08-16）：禁用重定向，防重定向绕过域名白名单
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            raise urllib.error.HTTPError(url, 302, "redirect blocked", {}, None)
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("too large")
    return data


def _is_url_allowed(url: str, cfg: dict) -> bool:
    """https 强制 + 域名白名单（debug 放行 http 本地/内网）"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https" and not (parsed.scheme == "http" and cfg.get("_debug_allow_http")):
        return False
    host = parsed.hostname or ""
    allowed = [str(h).strip().lower() for h in (cfg.get("allowed_hosts") or []) if str(h).strip()]
    if allowed and host.lower() not in allowed:
        return False
    return True


def _validate_index(data: bytes, cfg: dict) -> dict:
    """校验 index 内容：items 数组 / name 必填 / download_url https / size 上限"""
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail=tr_lang("zh", "market_index_invalid"))
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise HTTPException(status_code=400, detail=tr_lang("zh", "market_index_invalid"))
    max_bytes = int(cfg.get("max_zip_mb", 10)) * 1024 * 1024
    items: list[dict] = []
    for it in obj["items"]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name", "")).strip()
        dl = str(it.get("download_url", "")).strip()
        if not name or not dl:
            continue
        if not _is_url_allowed(dl, cfg):
            continue
        try:
            size = int(it.get("size") or 0)
        except Exception:
            size = 0
        if size > max_bytes:
            continue
        items.append({
            "name": name,
            "version": str(it.get("version", "0.0.1")),
            "description": str(it.get("description", "")),
            "author": str(it.get("author", "")),
            "category": str(it.get("category", "plugin")),
            "hooks": list(it.get("hooks", []) or []),
            "permissions": list(it.get("permissions", []) or []),
            "config": dict(it.get("config", {}) or {}),
            "usage": str(it.get("usage", "") or ""),
            "download_url": dl,
            "size": size,
            "sha256": str(it.get("sha256", "") or "").strip() or None,
            "tags": list(it.get("tags", []) or []),
            "updated_at": str(it.get("updated_at", "") or ""),
            "min_api_version": str(it.get("min_api_version", "") or ""),
        })
    return {"market": str(obj.get("market", "")), "homepage": str(obj.get("homepage", "") or ""), "items": items}


async def _refresh_one(url: str, cfg: dict) -> dict:
    """拉取单个 index 并落盘缓存；返回市场摘要"""
    if not _is_url_allowed(url, cfg):
        raise HTTPException(status_code=400, detail=tr_lang("zh", "market_url_invalid", url=url))
    try:
        data = await asyncio.to_thread(_fetch_bytes, url, INDEX_TIMEOUT, MAX_INDEX_BYTES)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang("zh", "market_download_failed", err=str(e)[:200]))
    obj = _validate_index(data, cfg)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(url).write_text(json.dumps({"market": obj["market"], "homepage": obj["homepage"], "items": obj["items"]}, ensure_ascii=False), encoding="utf-8")
    meta = _load_cache_meta()
    meta[url] = {
        "last_refresh_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "items": len(obj["items"]),
    }
    _save_cache_meta(meta)
    return {"url": url, "market": obj["market"], "items": len(obj["items"])}


# ---------------- 内置市场 ----------------

def _load_index_extras() -> dict:
    """读取 plugins/marketplace/index.json 补充字段（name -> extras）；失败返回空"""
    try:
        if INDEX_FILE.is_file():
            data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x.get("name", "")): x for x in data if isinstance(x, dict)}
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception as e:
        _logger.warning("marketplace index 解析失败: %s", e)
    return {}


def _scan_market_items() -> list[dict]:
    """扫描示例插件目录生成市场条目（轻量读 manifest，不加载插件 main.py）"""
    extras = _load_index_extras()
    items: list[dict] = []
    for path in registry._scan_dir(registry.EXAMPLE_DIR):
        m = load_manifest(str(path / "manifest.json"))
        if m is None:
            continue
        name = str(m.get("name", ""))
        ex = extras.get(name, {})
        items.append({
            "name": name,
            "version": str(m.get("version", "0.0.1")),
            "description": str(m.get("description", "")),
            "author": str(m.get("author", "")),
            "category": str(m.get("category", "plugin")),
            "hooks": list(m.get("hooks", [])),
            "permissions": list(m.get("permissions", [])),
            "config": dict(m.get("config", {})),
            "usage": str(m.get("usage", "") or ""),
            "icon": str(ex.get("icon", "") or ""),
            "readme": str(ex.get("readme", "") or ""),
            "tags": list(ex.get("tags", []) or []),
            "source": "builtin",
            "size": 0,
            "sha256": None,
            "updated_at": str(ex.get("updated_at", "") or ""),
            "download_url": None,
        })
    items.sort(key=lambda x: x["name"])
    return items


def _all_items() -> list[dict]:
    """内置 + 远程缓存合并；同 name 远程覆盖内置（升级语义）"""
    by_name: dict[str, dict] = {}
    for it in _scan_market_items():
        by_name[it["name"]] = it
    for it in _load_remote_items():
        by_name[it["name"]] = it
    return sorted(by_name.values(), key=lambda x: x["name"])


def _find_item(name: str) -> dict | None:
    for it in _all_items():
        if it["name"] == name:
            return it
    return None


def _merge_installed(items: list[dict]) -> list[dict]:
    """合并 installed/enabled/installed_version 标记"""
    installed_map = {p["name"]: p for p in registry.list_plugins()}
    out: list[dict] = []
    for it in items:
        row = dict(it)
        pl = installed_map.get(it["name"])
        row["installed"] = pl is not None
        row["enabled"] = bool(pl.get("enabled")) if pl else False
        row["installed_version"] = pl.get("version") if pl else None
        out.append(row)
    return out


# ---------------- 远程安装 ----------------

async def _install_remote(item: dict, lang: str) -> dict:
    """远程安装：下载 zip → sha256 校验 → zip 安全校验 → 解压（旧目录备份）→ 加载校验，失败回滚"""
    cfg = await _load_config()
    url = item.get("download_url") or ""
    if not _is_url_allowed(url, cfg):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_url_invalid", url=url))
    max_bytes = int(cfg.get("max_zip_mb", 10)) * 1024 * 1024
    try:
        data = await asyncio.to_thread(_fetch_bytes, url, ZIP_TIMEOUT, max_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_download_failed", err=str(e)[:200]))
    expected = (item.get("sha256") or "").strip().lower()
    if expected:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "market_sha_mismatch"))
    try:
        manifest, names = validate_zip_bytes(data)
    except ZipSafetyError as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, e.key, **e.kwargs))
    name = manifest["name"]
    if name != item["name"]:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_item_invalid", name=item["name"]))
    target = registry.USER_DIR / name
    if not str(target.resolve()).startswith(str(registry.USER_DIR.resolve())):
        raise HTTPException(status_code=400, detail="invalid plugin name (path traversal blocked)")
    backup_dir = None
    if target.exists():
        backup_dir = registry.USER_DIR / ".backup" / f"{name}-{int(time.time())}"
        backup_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(target, backup_dir)
        shutil.rmtree(target)
    try:
        os.makedirs(target, exist_ok=True)
        extract_zip_bytes(data, names, target)
    except Exception as e:
        _logger.warning("marketplace install extract failed %s: %s", name, e)
        shutil.rmtree(target, ignore_errors=True)
        if backup_dir is not None and backup_dir.is_dir():
            shutil.copytree(backup_dir, target)
            shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "install_failed", err=str(e)[:200]))
    # 加载校验，失败回滚
    loaded = registry.load_plugin_dir(target)
    if loaded is None:
        shutil.rmtree(target, ignore_errors=True)
        if backup_dir is not None and backup_dir.is_dir():
            shutil.copytree(backup_dir, target)
            shutil.rmtree(backup_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_load_failed"))
    await registry.sync_plugins_db()
    plugin = registry.get_plugin(name)
    return {"installed": True, "upgraded": backup_dir is not None, "source": item.get("source", "remote"), "plugin": plugin}


# ---------------- API ----------------

@router.get("/config")
async def get_marketplace_config(user_id: int = Depends(get_current_user_id)):
    """读取远程市场配置（仅主账号）"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang("zh", "main_account_manage_only"))
    return await _load_config()


@router.put("/config")
async def put_marketplace_config(body: dict, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """写入远程市场配置（仅主账号）"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    cfg = await _load_config()
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
    if "urls" in body:
        if not isinstance(body["urls"], list):
            raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
        cfg["urls"] = [str(u).strip() for u in body["urls"] if str(u).strip()]
    if "refresh_interval_hours" in body:
        try:
            cfg["refresh_interval_hours"] = max(1, min(168, int(body["refresh_interval_hours"])))
        except Exception:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    if "allowed_hosts" in body:
        if not isinstance(body["allowed_hosts"], list):
            raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
        cfg["allowed_hosts"] = [str(h).strip().lower() for h in body["allowed_hosts"] if str(h).strip()]
    if "max_zip_mb" in body:
        try:
            cfg["max_zip_mb"] = max(1, min(100, int(body["max_zip_mb"])))
        except Exception:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "config_invalid"))
    await _save_config(cfg)
    return cfg


@router.post("/refresh")
async def refresh_marketplace(force: bool = Query(False, description="强制刷新"), user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """拉取远程 index 缓存（仅主账号；距上次 < 间隔返回未到期，force=true 强刷）"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_manage_only"))
    cfg = await _load_config()
    if not cfg.get("enabled"):
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_remote_disabled"))
    urls = cfg.get("urls") or []
    if not urls:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_empty_urls"))
    meta = _load_cache_meta()
    interval_s = int(cfg.get("refresh_interval_hours", 24)) * 3600
    results = []
    for url in urls:
        rec = meta.get(url)
        if not force and rec and rec.get("last_refresh_at"):
            try:
                last = datetime.fromisoformat(rec["last_refresh_at"])
                if (datetime.now(timezone.utc) - last).total_seconds() < interval_s:
                    results.append({"url": url, "status": "skipped"})
                    continue
            except Exception:
                pass
        try:
            r = await _refresh_one(url, cfg)
            r["status"] = "ok"
            results.append(r)
        except HTTPException as e:
            results.append({"url": url, "status": "error", "error": e.detail if isinstance(e.detail, str) else str(e.detail)})
    return {"results": results, "total_ok": sum(1 for r in results if r.get("status") == "ok")}


@router.get("")
async def list_marketplace(
    q: str | None = Query(None, description="搜索 name/description"),
    category: str | None = Query(None, description="plugin|mcp"),
    installed: bool | None = Query(None, description="只看已安装(true)/未安装(false)"),
    user_id: int = Depends(get_current_user_id),
):
    """市场列表：内置 + 远程缓存合并（同 name 远程覆盖内置）"""
    out: list[dict] = []
    for it in _merge_installed(_all_items()):
        if q:
            ql = q.strip().lower()
            if ql and ql not in it["name"].lower() and ql not in (it["description"] or "").lower():
                continue
        if category and it["category"] != category:
            continue
        if installed is True and not it["installed"]:
            continue
        if installed is False and it["installed"]:
            continue
        out.append(it)
    return {"items": out, "total": len(out)}


@router.get("/{name}")
async def get_marketplace_item(name: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """市场条目详情（含 readme 正文）"""
    item = _find_item(name)
    if item is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "market_no_plugin"))
    row = dict(item)
    readme_text = ""
    if item.get("readme") and item.get("source") == "builtin":
        p = registry.EXAMPLE_DIR / item["name"] / str(item["readme"])
        if p.is_file():
            try:
                readme_text = p.read_text(encoding="utf-8")[:20000]
            except Exception:
                readme_text = ""
    row["readme_text"] = readme_text
    installed_map = {p["name"]: p for p in registry.list_plugins()}
    pl = installed_map.get(item["name"])
    row["installed"] = pl is not None
    row["enabled"] = bool(pl.get("enabled")) if pl else False
    row["installed_version"] = pl.get("version") if pl else None
    return row


@router.post("/{name}/install")
async def install_market_item(name: str, user_id: int = Depends(get_current_user_id), lang: str = Header(default="zh")):
    """从市场安装（仅主账号）：内置复制示例目录 / 远程下载 zip（sha256+安全校验+回滚）"""
    if not _is_owner(user_id):
        raise HTTPException(status_code=403, detail=tr_lang(lang, "main_account_install_only"))
    item = _find_item(name)
    if item is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "market_no_plugin"))
    if item.get("source") != "builtin":
        return await _install_remote(item, lang)
    src = registry.EXAMPLE_DIR / item["name"]
    if not src.is_dir():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "plugin_dir_not_found"))
    target = registry.USER_DIR / item["name"]
    if target.exists():
        shutil.rmtree(target)
    try:
        shutil.copytree(src, target, ignore=_IGNORE)
    except Exception as e:
        _logger.warning("marketplace install copy failed %s: %s", name, e)
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "install_failed", err=e))
    # 校验可加载，失败回滚
    loaded = registry.load_plugin_dir(target)
    if loaded is None:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "plugin_load_failed"))
    await registry.sync_plugins_db()
    plugin = registry.get_plugin(name)
    return {"installed": True, "plugin": plugin, "source": "builtin"}


async def prefetch_remote_marketplace() -> None:
    """启动异步预拉：已配置 url 且无缓存时拉取一次（失败静默）"""
    try:
        cfg = await _load_config()
        if not cfg.get("enabled") or not cfg.get("urls"):
            return
        for url in cfg["urls"]:
            if _cache_path(url).is_file():
                continue
            try:
                await _refresh_one(url, cfg)
                _logger.info("marketplace 启动预拉完成: %s", url)
            except Exception as e:
                _logger.warning("marketplace 启动预拉失败 %s: %s", url, e)
    except Exception as e:
        _logger.warning("marketplace prefetch failed: %s", e)
