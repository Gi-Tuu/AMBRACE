"""zip 插件包安全校验（上传安装与远程市场安装共用）

校验：大小限制 / zip 可读 / 空包 / 路径穿越 / 绝对路径 / 符号链接 / 顶层 manifest / manifest 校验。
成功返回 (manifest, namelist)；失败抛 ZipSafetyError（key 对应 i18n 错误文案）。
"""
import io
import json
import shutil
import zipfile

from app.plugins.manifest import validate_manifest

# zip 安全检查常量
MAX_ZIP_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ZIP_ENTRIES = 2000  # 解压炸弹防护（P1，2026-08-16）：条目数上限
MAX_ZIP_EXTRACT_SIZE = 200 * 1024 * 1024  # 解压总大小上限（P1）


class ZipSafetyError(Exception):
    """zip 校验失败：key 为 i18n key，kwargs 为文案参数"""

    def __init__(self, key: str, **kwargs):
        self.key = key
        self.kwargs = kwargs
        super().__init__(key)


def validate_zip_bytes(data: bytes) -> tuple[dict, list[str]]:
    """校验 zip 字节流，返回 (manifest, namelist)；非法抛 ZipSafetyError"""
    if len(data) > MAX_ZIP_SIZE:
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
    if len(names) > MAX_ZIP_ENTRIES:
        raise ZipSafetyError("zip_too_many_entries")
    # 解压总大小估算（防 zip 炸弹）
    _total = sum((i.file_size or 0) for i in zf.infolist())
    if _total > MAX_ZIP_EXTRACT_SIZE:
        raise ZipSafetyError("zip_extract_too_large")

    # 安全校验：拒绝路径穿越/绝对路径/符号链接
    manifest_name = None
    for n in names:
        norm = n.replace("\\", "/")
        parts = norm.split("/")
        if ".." in parts or norm.startswith("/") or (parts and ":" in parts[0]):
            raise ZipSafetyError("zip_illegal_path", n=n)
        info = zf.getinfo(n)
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
    err = validate_manifest(manifest)
    if err:
        raise ZipSafetyError("manifest_invalid", err=err)
    return manifest, names


def extract_zip_bytes(data: bytes, names: list[str], target) -> None:
    """安全解压（校验已由 validate_zip_bytes 完成）；target 为 Path"""
    from pathlib import Path
    zf = zipfile.ZipFile(io.BytesIO(data))
    target = Path(target)
    for n in names:
        norm = n.replace("\\", "/")
        rel = norm.split("/", 1)[-1]
        if not rel:
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(n) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
