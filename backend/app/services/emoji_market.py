"""表情市场服务：远程索引拉取 + 安全下载安装/卸载 + 已安装包合并。

- 索引：GET 远程 index.json（默认 GitHub raw，取 config.emoji_market_url），内存 1h TTL 缓存 + 失败降级空列表（不阻断）。
- 包格式：zip = manifest.json + 贴图（png/webp/jpg）+ icon，manifest 必填 id/name/description/version/icon/emojis，emojis 每项含 file/name/meaning。
- 安装：下载字节 → sha256 与索引比对 → zip 安全校验（路径穿越/符号链接/大小/重复文件名）→ 解压到 uploads/emojis/market/{pack_id}/ → 写 user_emoji_packs(market:{id})。
- 卸载：删目录 + 删用户记录；内置包由 API 层拦截返回 400。
"""
import asyncio
import hashlib
import io
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, delete

from app.config import settings
from app.db.database import async_session_factory
from app.i18n import tr_lang
from app.models.life import UserEmojiPack
from app.plugins.zip_safety import ZipSafetyError
from app.services.upload_service import UPLOAD_DIR
from app.utils.logger import get_logger

_logger = get_logger("services.emoji_market")

# ---- 常量 ----
MAX_INDEX_BYTES = 1024 * 1024  # index.json 本体 ≤1MB
INDEX_TIMEOUT = 10.0  # 索引下载超时
ZIP_TIMEOUT = 60.0  # zip 下载超时
MAX_ZIP_BYTES = 20 * 1024 * 1024  # 总包大小上限（含 zip 头）
_MAX_ZIP_ENTRIES = 800  # 解压炸弹防护
_MAX_ZIP_EXTRACT = 60 * 1024 * 1024  # 解压总大小上限
INDEX_TTL = 3600  # 内存索引缓存 1h

# 下载 URL 域名白名单：配置的市场仓库 raw / GitHub release 相关域名
ALLOWED_MARKET_HOSTS = {
    "raw.githubusercontent.com",
    "github.com",
    "objects.githubusercontent.com",
    "codeload.github.com",
    "githubusercontent.com",
}

# 贴图/图标扩展名白名单与大小限制
IMAGE_EXTS = {".png", ".webp", ".jpg", ".jpeg"}
MAX_SINGLE_EMOJI_BYTES = 2 * 1024 * 1024  # 单贴图 ≤2MB
MAX_TOTAL_BYTES = 20 * 1024 * 1024  # 包内贴图总重 ≤20MB
REQUIRED_MANIFEST_FIELDS = ("id", "name", "description", "version", "icon", "emojis")

MARKET_PACK_ROOT = UPLOAD_DIR / "emojis" / "market"

_UA = "AICompanion-EmojiMarket/1.0"
_index_cache: dict = {"data": None, "fetched_at": 0.0}


# ---------------- 索引 URL 与地址解析 ----------------

def _index_url() -> str:
    return (settings.emoji_market_url or "").strip()


def _market_base_url() -> str:
    """索引所在目录（用于把相对 file/icon 解析为完整 URL）。"""
    url = _index_url()
    if not url:
        return ""
    return url.rsplit("/", 1)[0]


def _resolve_market_url(rel: str) -> str:
    """索引内相对路径解析为绝对 URL；已是绝对 URL 则原样返回。"""
    rel = (rel or "").strip()
    if not rel:
        return ""
    if rel.startswith(("http://", "https://")):
        return rel
    base = _market_base_url()
    return f"{base}/{rel.lstrip('/')}"


def _is_allowed_host(url: str) -> bool:
    """https 强制 + 域名白名单（市场仓库 raw / GitHub release 域名）。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in ALLOWED_MARKET_HOSTS


# ---------------- 索引拉取与缓存 ----------------

def _fetch_bytes(url: str, timeout: float, max_bytes: int) -> bytes:
    """同步 urllib 下载（禁用重定向，防重定向绕过域名白名单）。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            raise urllib.error.HTTPError(url, 302, "redirect blocked", {}, None)

    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("too large")
    return data


def _parse_index(data: bytes) -> list[dict]:
    """解析索引 JSON（数组），逐条清洗为规范字段；非法条目丢弃。"""
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception:
        raise ValueError("index must be valid JSON")
    if not isinstance(obj, list):
        raise ValueError("index must be an array")
    out: list[dict] = []
    for it in obj:
        if not isinstance(it, dict):
            continue
        pid = str(it.get("id", "")).strip()
        name = str(it.get("name", "")).strip()
        file_name = str(it.get("file", "")).strip()
        if not pid or not name or not file_name:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
            continue
        out.append({
            "id": pid,
            "name": name,
            "description": str(it.get("description", "")).strip(),
            "version": str(it.get("version", "0.0.1")).strip() or "0.0.1",
            "icon": str(it.get("icon", "")).strip(),
            "file": file_name,
            "sha256": str(it.get("sha256", "")).strip().lower(),
            "size": int(it.get("size") or 0),
            "emoji_count": int(it.get("emoji_count") or 0),
        })
    return out


async def get_market_index() -> list[dict]:
    """读取市场索引；内存 TTL 缓存命中直接返回，失败降级（有旧缓存用旧缓存，否则空列表）。"""
    now = time.time()
    cached = _index_cache.get("data")
    if cached is not None and now - _index_cache.get("fetched_at", 0) < INDEX_TTL:
        return cached
    url = _index_url()
    if not url:
        return cached or []
    try:
        data = await asyncio.to_thread(_fetch_bytes, url, INDEX_TIMEOUT, MAX_INDEX_BYTES)
        parsed = _parse_index(data)
        _index_cache["data"] = parsed
        _index_cache["fetched_at"] = now
        return parsed
    except Exception as e:
        _logger.warning("Emoji market index fetch failed: %s", e)
        return cached or []


async def clear_index_cache() -> None:
    """清空内存索引缓存（测试重置用）。"""
    _index_cache["data"] = None
    _index_cache["fetched_at"] = 0.0


# ---------------- 包清单校验 ----------------

def _validate_file_ref(fname: str, file_map: dict) -> str | None:
    """校验单个贴图引用：无路径 / 扩展名白名单 / 文件中存在 / ≤2MB。"""
    if not fname:
        return "文件名为空"
    if "/" in fname or "\\" in fname or fname.startswith(".") or ".." in fname:
        return f"文件名不允许路径: {fname}"
    ext = "." + fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in IMAGE_EXTS:
        return f"扩展名不在白名单: {ext or '(无扩展名)'}"
    if fname not in file_map:
        return f"文件不存在: {fname}"
    size = file_map[fname][1]
    if size > MAX_SINGLE_EMOJI_BYTES:
        return f"单文件超过 2MB: {fname}"
    return None


def validate_emoji_manifest(manifest: dict, file_map: dict, pack_id: str | None = None) -> str | None:
    """严格校验表情包 manifest：字段齐全 + 贴图引用合法 + 大小上限。返回错误信息，None 表示合法。"""
    if not isinstance(manifest, dict):
        return "manifest 必须是对象"
    for k in REQUIRED_MANIFEST_FIELDS:
        v = manifest.get(k)
        if k in ("id", "name", "description", "version", "icon"):
            if not isinstance(v, str) or not v.strip():
                return f"缺少必填字段 {k}"
        elif v is None:
            return f"缺少必填字段 {k}"
    pid = str(manifest["id"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", pid):
        return "id 仅允许字母数字下划线连字符"
    if pack_id is not None and pid != pack_id:
        return f"manifest id 与索引不一致: {pid} != {pack_id}"
    icon = str(manifest["icon"]).strip()
    icon_err = _validate_file_ref(icon, file_map)
    if icon_err:
        return f"icon: {icon_err}"
    emojis = manifest["emojis"]
    if not isinstance(emojis, list) or not emojis:
        return "emojis 必须是非空数组"
    total = file_map[icon][1]
    for i, item in enumerate(emojis):
        if not isinstance(item, dict):
            return f"emojis[{i}] 必须是对象"
        fname = str(item.get("file", "")).strip()
        if not fname:
            return f"emojis[{i}].file 必填"
        ferr = _validate_file_ref(fname, file_map)
        if ferr:
            return f"emojis[{i}].file: {ferr}"
        if not str(item.get("name", "")).strip():
            return f"emojis[{i}].name 必填"
        if not str(item.get("meaning", "")).strip():
            return f"emojis[{i}].meaning 必填"
        total += file_map[fname][1]
    previews = manifest.get("previews") or []
    if isinstance(previews, list):
        for i, p in enumerate(previews):
            if isinstance(p, dict) and p.get("file"):
                pfile = str(p["file"]).strip()
                perr = _validate_file_ref(pfile, file_map)
                if perr:
                    return f"previews[{i}].file: {perr}"
                total += file_map[pfile][1]
    if total > MAX_TOTAL_BYTES:
        return "贴图总大小超过 20MB 限制"
    return None


def _build_file_map(names: list[str], zf) -> dict:
    """把 zip 条目映射为「解压后文件名 -> (原名, 解压大小)」；重名直接拒绝。"""
    fm: dict = {}
    for n in names:
        norm = n.replace("\\", "/")
        if norm.endswith("/"):
            continue
        rel = norm.split("/", 1)[-1]
        if not rel:
            continue
        info = zf.getinfo(n)
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            continue
        if rel in fm:
            raise ZipSafetyError("emoji_zip_dup_file", n=rel)
        fm[rel] = (norm, info.file_size)
    return fm


def validate_emoji_zip(data: bytes) -> tuple[dict, list[str]]:
    """校验表情包 zip：安全（大小/路径穿越/符号链接）+ manifest 强校验。返回 (manifest, names)。"""
    if len(data) > MAX_ZIP_BYTES:
        raise ZipSafetyError("zip_too_large")
    if not data:
        raise ZipSafetyError("zip_empty_file")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise ZipSafetyError("zip_invalid")
    names = zf.namelist()
    if not names:
        raise ZipSafetyError("zip_empty")
    if len(names) > _MAX_ZIP_ENTRIES:
        raise ZipSafetyError("zip_too_many_entries")
    total = sum((i.file_size or 0) for i in zf.infolist())
    if total > _MAX_ZIP_EXTRACT:
        raise ZipSafetyError("zip_extract_too_large")
    manifest_name = None
    for n in names:
        norm = n.replace("\\", "/")
        parts = norm.split("/")
        if ".." in parts or norm.startswith("/") or (parts and ":" in parts[0]):
            raise ZipSafetyError("zip_illegal_path", n=n)
        info = zf.getinfo(n)
        if info.is_dir():
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ZipSafetyError("zip_symlink", n=n)
        if norm.endswith("manifest.json") and "/" not in norm.strip("/"):
            manifest_name = norm
    if manifest_name is None:
        raise ZipSafetyError("zip_no_manifest")
    try:
        manifest = json.loads(zf.read(manifest_name).decode("utf-8"))
    except Exception:
        raise ZipSafetyError("manifest_parse_failed")
    fm = _build_file_map(names, zf)
    err = validate_emoji_manifest(manifest, fm)
    if err:
        raise ZipSafetyError("emoji_manifest_invalid", err=err)
    return manifest, names


def extract_emoji_zip(data: bytes, names: list[str], target: Path) -> None:
    """安全解压表情包（校验已由 validate_emoji_zip 完成）；保留顶层目录剥离，文件落在 target/。"""
    zf = zipfile.ZipFile(io.BytesIO(data))
    target = Path(target)
    for n in names:
        norm = n.replace("\\", "/")
        if norm.endswith("/"):
            continue
        rel = norm.split("/", 1)[-1]
        if not rel:
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(n) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)


# ---------------- 已安装包视图 ----------------

def market_pack_dir(pack_id: str) -> Path:
    return MARKET_PACK_ROOT / pack_id


def load_market_pack_manifest(pack_id: str) -> dict | None:
    """读取已安装包 manifest；不存在/解析失败返回 None。"""
    p = market_pack_dir(pack_id) / "manifest.json"
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _logger.warning("market pack manifest load failed %s: %s", pack_id, e)
    return None


def _installed_pack_url(pack_id: str, filename: str) -> str:
    return f"/uploads/emojis/market/{pack_id}/{filename}"


def _market_pack_view(manifest: dict) -> dict:
    """把已安装包 manifest 转成前端包结构（type=market，emojis 含贴图 URL）。"""
    pid = str(manifest["id"])
    emojis = []
    for item in manifest["emojis"]:
        fname = str(item["file"])
        emojis.append({
            "url": _installed_pack_url(pid, fname),
            "file": fname,
            "name": str(item.get("name", "")),
            "meaning": str(item.get("meaning", item.get("name", ""))),
        })
    return {
        "id": f"market:{pid}",
        "name": str(manifest["name"]),
        "description": str(manifest.get("description", "")),
        "version": str(manifest.get("version", "")),
        "downloaded": True,
        "type": "market",
        "icon_url": _installed_pack_url(pid, str(manifest["icon"])),
        "emojis": emojis,
    }


# ---------------- 用户下载记录 ----------------

async def _market_downloaded_ids(user_id: int) -> set[str]:
    try:
        async with async_session_factory() as db:
            rows = (await db.execute(
                select(UserEmojiPack.pack_id).where(
                    UserEmojiPack.user_id == user_id,
                    UserEmojiPack.pack_id.like("market:%"),
                )
            )).scalars().all()
        return set(rows)
    except Exception as e:
        _logger.warning("market downloaded ids failed user=%d: %s", user_id, e)
        return set()


async def _write_pack_record(user_id: int, pack_id: str, pack_name: str) -> None:
    async with async_session_factory() as db:
        exists = (await db.execute(
            select(UserEmojiPack).where(
                UserEmojiPack.user_id == user_id, UserEmojiPack.pack_id == pack_id
            )
        )).scalar_one_or_none()
        if exists is None:
            db.add(UserEmojiPack(user_id=user_id, pack_id=pack_id, pack_name=(pack_name or "")[:50]))
            await db.commit()


# ---------------- 列表 ----------------

async def list_installed_market_packs(user_id: int) -> list[dict]:
    """已安装市场包（用于 /api/v1/emojis/packs 合并）；读磁盘 manifest。"""
    downloaded = await _market_downloaded_ids(user_id)
    out: list[dict] = []
    for pid_record in sorted(downloaded):
        raw = pid_record[len("market:"):] if pid_record.startswith("market:") else pid_record
        manifest = load_market_pack_manifest(raw)
        if manifest is None:
            continue
        out.append(_market_pack_view(manifest))
    return out


async def list_market_packs(user_id: int) -> list[dict]:
    """市场列表：索引包 + installed 标记；索引里没有但已安装的也补上。"""
    downloaded = await _market_downloaded_ids(user_id)
    idx = await get_market_index()
    seen: set[str] = set()
    result: list[dict] = []
    for it in idx:
        pid = it["id"]
        seen.add(pid)
        result.append({
            **it,
            "icon_url": _resolve_market_url(it.get("icon", "")),
            "installed": f"market:{pid}" in downloaded,
        })
    for pid_record in downloaded:
        raw = pid_record[len("market:"):] if pid_record.startswith("market:") else pid_record
        if raw in seen:
            continue
        manifest = load_market_pack_manifest(raw)
        if manifest is None:
            continue
        view = _market_pack_view(manifest)
        result.append({
            "id": raw,
            "name": view["name"],
            "description": view["description"],
            "version": view["version"],
            "icon": str(manifest.get("icon", "")),
            "file": "",
            "sha256": "",
            "size": 0,
            "emoji_count": len(manifest["emojis"]),
            "icon_url": str(view["icon_url"]),
            "installed": True,
        })
    return result


# ---------------- 下载 / 卸载 ----------------

async def download_market_pack(user_id: int, pack_id: str, lang: str = "zh") -> dict:
    """下载并安装一个市场表情包：下载→sha256→zip 校验→解压→写记录。返回包视图。"""
    idx = await get_market_index()
    entry = next((p for p in idx if p["id"] == pack_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=tr_lang(lang, "emoji_market_no_pack"))
    dl_url = _resolve_market_url(entry.get("file") or "")
    if not _is_allowed_host(dl_url):
        host = urllib.parse.urlparse(dl_url).hostname or ""
        raise HTTPException(status_code=403, detail=tr_lang(lang, "market_host_not_allowed", host=host))
    try:
        data = await asyncio.to_thread(_fetch_bytes, dl_url, ZIP_TIMEOUT, MAX_ZIP_BYTES)
    except Exception as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "market_download_failed", err=str(e)[:200]))
    expected = entry.get("sha256") or ""
    if expected:
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise HTTPException(status_code=400, detail=tr_lang(lang, "market_sha_mismatch"))
    try:
        manifest, names = validate_emoji_zip(data)
    except ZipSafetyError as e:
        raise HTTPException(status_code=400, detail=tr_lang(lang, e.key, **e.kwargs))
    if str(manifest.get("id", "")).strip() != pack_id:
        raise HTTPException(status_code=400, detail=tr_lang(lang, "emoji_manifest_invalid", err="manifest id 与索引不一致"))
    target = market_pack_dir(pack_id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    try:
        extract_emoji_zip(data, names, target)
    except Exception as e:
        shutil.rmtree(target, ignore_errors=True)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "install_failed", err=str(e)[:200]))
    await _write_pack_record(user_id, f"market:{pack_id}", str(manifest.get("name", "")))
    return _market_pack_view(manifest)


async def uninstall_market_pack(user_id: int, pack_id: str, lang: str = "zh") -> dict:
    """卸载市场表情包：删目录 + 删用户记录（记录删除尽力而为，不阻断卸载）。"""
    target = market_pack_dir(pack_id)
    if not (target / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail=tr_lang(lang, "emoji_market_not_installed"))
    try:
        if target.exists():
            shutil.rmtree(target)
    except Exception as e:
        _logger.warning("market pack uninstall dir failed %s: %s", pack_id, e)
        raise HTTPException(status_code=500, detail=tr_lang(lang, "emoji_market_uninstall_failed", err=str(e)[:200]))
    try:
        async with async_session_factory() as db:
            await db.execute(delete(UserEmojiPack).where(
                UserEmojiPack.user_id == user_id, UserEmojiPack.pack_id == f"market:{pack_id}"
            ))
            await db.commit()
    except Exception as e:
        _logger.warning("market pack uninstall db failed %s: %s", pack_id, e)
    return {"ok": True, "pack_id": pack_id}
