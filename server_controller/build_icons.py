"""把 assets/icons_src/*.svg（Lucide，ISC）栅格化为 assets/icons/*.png（96×96 纯白 + 透明底）。

为什么栅格成白色而不是直接出各主题色：控制台有 3 套主题，运行时用 Pillow 染色即可，
一套素材吃三套主题（见 console_ui.IconStore）。

用法（仓库根或本目录均可）：
    backend\\.venv\\Scripts\\python.exe server_controller\\build_icons.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "assets" / "icons_src"
OUT = HERE / "assets" / "icons"
SIZE = 96  # @4x（显示 16/24 逻辑像素时仍有余量，LANCZOS 降采样不糊）

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

HTML_TPL = (
    '<!doctype html><html><head><meta charset="utf-8"><style>'
    'html,body{{margin:0;padding:0;background:transparent;overflow:hidden}}'
    'svg{{display:block}}</style></head><body>{}</body></html>'
)


def find_browser() -> str:
    for p in CHROME_CANDIDATES:
        if pathlib.Path(p).exists():
            return p
    raise SystemExit("找不到 Chrome/Edge，无法栅格化图标")


def to_white(svg_text: str) -> str:
    """Lucide 用 currentColor；强制成纯白，交给运行时染色。尺寸用正则替换（SVG 已有 width/height，
    重复属性会让解析器行为不确定）。"""
    import re
    out = svg_text.replace("currentColor", "#FFFFFF")
    out = re.sub(r'\bwidth="\d+"', f'width="{SIZE}"', out, count=1)
    out = re.sub(r'\bheight="\d+"', f'height="{SIZE}"', out, count=1)
    return out


def main() -> int:
    browser = find_browser()
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = HERE / "_icon_build"
    tmp.mkdir(exist_ok=True)
    built = 0
    for svg_path in sorted(SRC.glob("*.svg")):
        text = to_white(svg_path.read_text(encoding="utf-8"))
        html = tmp / f"{svg_path.stem}.html"
        html.write_text(HTML_TPL.format(text), encoding="utf-8")
        png = OUT / f"{svg_path.stem}.png"
        subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--no-first-run", "--no-default-browser-check",
             "--force-device-scale-factor=1", f"--window-size={SIZE},{SIZE}",
             "--default-background-color=00000000", "--virtual-time-budget=1000",
             f"--screenshot={png}", html.as_uri()],
            check=True, capture_output=True,
        )
        built += 1
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"[OK] 栅格化 {built} 枚图标 → {OUT}（{SIZE}px 纯白透明底）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
