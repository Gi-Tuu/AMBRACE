"""照片级素材入库：原始出图 → `assets/photo/*.jpg`（按显示尺寸预降采样）。

用法（原始图在 output 里，仓库外）：

    backend\\.venv\\Scripts\\python.exe server_controller\\build_photos.py [源目录]

源目录用环境变量 `AMBRACE_PHOTO_SRC` 指定（出图产物在仓库外，不进仓）；未设置时回落到
仓库内 `assets/photo`（原地重跑＝无操作）。重跑即覆盖，**到货即生效**：
用户重新生成同名图后，只要再跑一次这条命令，控制台不用改代码。

为什么转 JPEG 而不是留 PNG：这 8 张是不带 alpha 的照片，PNG 存照片一张 0.4–1.5MB、
合计 8.6MB；降到显示尺寸 + JPEG q84 后合计约 0.4MB。圆角与边缘渐隐由运行时
`PhotoStore` 现算（`putalpha`），所以素材本身不需要 alpha 通道。

尺寸按「槽位显示尺寸 × 2」给：hero 会被拉到接近整卡宽（约 1200 物理像素），其余槽位
都在 200–420 之间。原始文件名里的 `photo_` 前缀保留，便于和出图清单逐行对账。
"""
from __future__ import annotations

import os
import pathlib
import sys

from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
# 源目录（出图产物）在仓库外，用环境变量指定；未设置时回落到仓库内 assets/photo（原地重跑＝无操作）
DEFAULT_SRC = pathlib.Path(os.environ.get("AMBRACE_PHOTO_SRC") or (HERE / "assets" / "photo"))
OUT = HERE / "assets" / "photo"
QUALITY = 84
# 长边上限：未列出的按 420（约等于槽位显示尺寸的 2 倍）
MAX_EDGE = {"photo_about_hero.png": 1280}
DEFAULT_EDGE = 420
# 白名单＝出图清单里的 8 个正名。源目录里还留着出图轮次的旧变体
# （`photo_about_hero_1789982341993_869c81c7.png` 这种带时间戳后缀的），按名字排除最稳。
WANT = (
    "photo_sidebar_brand.png",
    "photo_about_hero.png",
    "photo_empty_accounts.png",
    "photo_empty_audit.png",
    "photo_empty_log.png",
    "photo_empty_disconnected.png",
    "photo_locked_gate.png",
    "photo_offline.png",
)


def convert(src: pathlib.Path, dst_dir: pathlib.Path) -> list[str]:
    dst_dir.mkdir(parents=True, exist_ok=True)
    done: list[str] = []
    for name in WANT:
        path = src / name
        if not path.exists():
            print("  缺 %s（跳过，控制台对应槽位会走无图版式）" % name)
            continue
        edge = MAX_EDGE.get(name, DEFAULT_EDGE)
        im = Image.open(path).convert("RGB")
        if max(im.size) > edge:
            s = edge / max(im.size)
            im = im.resize((round(im.size[0] * s), round(im.size[1] * s)), Image.LANCZOS)
        out = dst_dir / (path.stem + ".jpg")
        im.save(out, "JPEG", quality=QUALITY, optimize=True)
        done.append("%s  %dx%d  %.0fKB" % (out.name, im.size[0], im.size[1],
                                           out.stat().st_size / 1024))
    return done


def main() -> int:
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    if not src.is_dir():
        print("源目录不存在：%s" % src)
        return 1
    for line in convert(src, OUT):
        print("  " + line)
    files = sorted(OUT.glob("*.jpg"))
    total = sum(p.stat().st_size for p in files) / 1024
    print("完成：%d 张 → %s（合计 %.0fKB）" % (len(files), OUT, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
