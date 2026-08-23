"""插件发布工具：校验 manifest → 打 zip → 计算 size/sha256 → 生成市场 index 条目。

用法：
  python scripts/publish_plugin.py <插件目录> [--out 输出目录]
"""
import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.plugins.manifest import load_manifest, validate_manifest  # noqa: E402


def _should_ignore(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return any(
        p in ("__pycache__", ".git", ".venv", "node_modules")
        or p.endswith(".pyc")
        for p in parts
    )


def build_zip(plugin_dir: Path, out_zip: Path) -> None:
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"缺 manifest.json: {plugin_dir}")
    manifest = load_manifest(str(manifest_path))
    if manifest is None:
        raise SystemExit(f"manifest 解析失败: {manifest_path}")
    err = validate_manifest(manifest)
    if err:
        raise SystemExit(f"manifest 校验失败: {err}")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(plugin_dir.rglob("*")):
            if p.is_dir() or not p.is_file():
                continue
            rel = p.relative_to(plugin_dir).as_posix()
            if _should_ignore(rel):
                continue
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))  # 固定时间戳，本地/CI 重建 sha256 稳定
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, p.read_bytes())
    print(f"[publish] zip 已生成: {out_zip}")


def make_entry(plugin_dir: Path, zip_path: Path) -> dict:
    manifest = load_manifest(str(plugin_dir / "manifest.json"))
    if manifest is None:
        raise SystemExit(f"manifest 解析失败: {plugin_dir / 'manifest.json'}")
    size = zip_path.stat().st_size
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return {
        "name": manifest["name"],
        "version": manifest.get("version", "0.0.1"),
        "description": manifest.get("description", ""),
        "author": manifest.get("author", ""),
        "category": manifest.get("category", "plugin"),
        "type": manifest.get("type", "http"),
        "icon": manifest.get("icon", ""),
        "page": manifest.get("page", ""),
        "hooks": manifest.get("hooks", []),
        "permissions": manifest.get("permissions", []),
        "config": manifest.get("config", {}),
        "usage": manifest.get("usage", ""),
        "download_url": "",
        "size": size,
        "sha256": sha256,
        "min_api_version": manifest.get("min_api_version", "1.0"),
        "tags": [],
        "updated_at": "",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plugin_dir", type=Path, help="插件目录（含 manifest.json）")
    ap.add_argument("--out", type=Path, default=Path("marketplace"), help="输出目录")
    ap.add_argument("--market", default="AMBRACE 社区市场", help="市场名（index.json market 字段）")
    args = ap.parse_args()

    plugin_dir = args.plugin_dir.resolve()
    if not plugin_dir.is_dir():
        raise SystemExit(f"插件目录不存在: {plugin_dir}")
    args.out.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(str(plugin_dir / "manifest.json"))
    if manifest is None:
        raise SystemExit(f"manifest 解析失败: {plugin_dir / 'manifest.json'}")
    name = manifest["name"]
    version = manifest.get("version", "0.0.1")

    tmp = Path(tempfile.mkdtemp(prefix="publish_plugin_"))
    try:
        zip_path = tmp / f"{name}-{version}.zip"
        build_zip(plugin_dir, zip_path)
        entry = make_entry(plugin_dir, zip_path)
        out_zip = args.out / f"{name}-{version}.zip"
        shutil.copy2(zip_path, out_zip)
        index_file = args.out / "index.json"
        items = []
        if index_file.is_file():
            try:
                data = json.loads(index_file.read_text(encoding="utf-8"))
                items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except Exception:
                items = []
        kept = [it for it in items if it.get("name") != name]
        kept.append(entry)
        index = {"market": args.market, "homepage": "", "updated_at": "", "items": kept}
        index_file.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[publish] 条目已写入: {index_file}")
        print(f"[publish] name={name} version={version} size={entry['size']} sha256={entry['sha256'][:16]}...")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()