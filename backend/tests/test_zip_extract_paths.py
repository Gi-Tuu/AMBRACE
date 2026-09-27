# -*- coding: utf-8 -*-
"""P2-10 回归（2026-09-28 修复）：插件包解压不得把子目录拍平。

原实现无条件 norm.split("/", 1)[-1]（剥掉第一段）；而 validate_zip_bytes 只接受**根级**
manifest.json（要求名字里没有斜杠）⇒ 任何带子目录的包（如 pages/main.html）都会被拍平，
manifest.page 指向的路径解析不到。修复后：仅当全包被同一个顶层目录包着时才剥那一段。
"""
import io
import json
import zipfile

from app.plugins.zip_safety import extract_zip_bytes

MANIFEST = {
    "id": "demo",
    "name": "Demo",
    "version": "1.0.0",
    "page": "pages/main.html",
}


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_根级包保留子目录路径(tmp_path):
    data = _zip({
        "manifest.json": json.dumps(MANIFEST),
        "pages/main.html": "<html>hi</html>",
        "assets/style.css": "body{}",
    })
    # 本用例只测解压路径口径；manifest 校验由既有插件用例覆盖
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    extract_zip_bytes(data, names, tmp_path)
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "pages" / "main.html").exists(), "子目录必须保留（修复前会被拍平成 main.html）"
    assert (tmp_path / "assets" / "style.css").exists()


def test_带顶层包装目录的包仍剥掉那一段(tmp_path):
    """兼容历史形态：全包被单一顶层目录包住时，仍按旧口径剥掉一段。"""
    data = _zip({
        "plugin/manifest.json": json.dumps(MANIFEST),
        "plugin/pages/main.html": "<html>hi</html>",
    })
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    extract_zip_bytes(data, names, tmp_path)
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "pages" / "main.html").exists()
