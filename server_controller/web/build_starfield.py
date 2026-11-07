"""星空横幅底板生成器（可重跑）。

口径：云状成团、不画任何拖尾线；左侧 40% 是静区（标题压在那儿），光团与密星只落右 60%；
只出墨纸档一张——亮纸档页眉走纯细线，这是 2026-11-05 拍板的第③项。

W4 多出一张「亮星层」star_tw.png：只收亮星（a≥TWINKLE_MIN）与它们的伴像素，
页眉让这一层的透明度慢慢起伏，底板本身一动不动。
时钟压在右上，那一块从亮星层里挖掉——呼吸不许在数字底下闪。

    python build_starfield.py            # 写 web/star_dark.png 与 web/star_tw.png
"""
import os
import random
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "star_dark.png")
OUT_TW = os.path.join(HERE, "star_tw.png")
W, H = 2720, 264          # 2x，供 150% DPI 下 cover 不糊
PAPER, CLOUD, STAR = "#131110", "#1d1a17", "#e9e1d4"
STAR_RGB = tuple(int(STAR[i:i + 2], 16) for i in (1, 3, 5))
TWINKLE_MIN = 175         # 只有这批亮星参与呼吸，暗星留在底板里不动
# 亮度不是按 x 一刀切（旧写法 `randint(130,255) if x>40% else randint(28,86)` 会在
# 横幅上留一条竖着的疏密断层，用户截图指出"衔接突兀"）。改成一条横跨 28%~72% 的
# 缓坡：左侧仍是标题的暗区，右侧才是亮星带，中间平滑过渡，看不出分界。
RAMP_X0, RAMP_X1 = 0.28, 0.72
DIM = (28, 86)            # 静区星的亮度区间
LIT = (130, 255)          # 亮星带的亮度区间
# 呼吸层的星要带一圈极小的晕：横幅按 cover 缩到 0.5~0.75 倍显示，
# 单像素星一缩就没了——呼吸既看不见也量不出来（第一版就栽在这里）。
HALO = ((0, 0, 10), (1, 0, 3), (-1, 0, 3), (0, 1, 3), (0, -1, 3),
        (1, 1, 2), (-1, -1, 2), (1, -1, 2), (-1, 1, 2))
# 时钟静区（图素坐标）：⑤⑥ 后日期与时间合并成一栏，组件明显变宽，
# 静区向左扩到 2240、上界抬到 32（合并后的大号日期整体上移）；下界仍钉 176，不与 W4 采样区抢行。
CLOCK_BOX = (2240, 32, 2660, 176)


def in_clock(x, y):
    """落在时钟那块矩形里——亮星层要给它让位。"""
    x0, y0, x1, y1 = CLOCK_BOX
    return x0 <= x <= x1 and y0 <= y <= y1


def in_quiet(x, y):
    """标题静区（左 40%）与时钟静区：呼吸层的晕也不许探进去。"""
    return x < int(W * 0.40) or in_clock(x, y)


def plot_star(dt, x, y, a, w, h):
    """把一颗亮星画进呼吸层：中心实心，外面一圈很淡的晕。"""
    for dx, dy, k in HALO:
        px, py = x + dx, y + dy
        if 0 <= px < w and 0 <= py < h and not in_quiet(px, py):
            dt.point((px, py), STAR_RGB + (a * k // 10,))


def star_band(x, w):
    """这颗星的亮度区间：按 x 在 DIM→LIT 之间平滑爬坡（smoothstep，两端无折角）。"""
    t = (x / float(w) - RAMP_X0) / (RAMP_X1 - RAMP_X0)
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    t = t * t * (3 - 2 * t)
    return (int(DIM[0] + (LIT[0] - DIM[0]) * t), int(DIM[1] + (LIT[1] - DIM[1]) * t))


def build(seed=7, w=W, h=H):
    """返回 (底板 RGB, 亮星层 RGBA)。

    星点位置与上一版一致（随机数取法没动，仍是每颗星一次 randint），
    但亮度改走 star_band 的缓坡，所以 40% 那条一刀切的断层没有了。
    """
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), PAPER)
    grad = Image.new("L", (w, h))
    dg = ImageDraw.Draw(grad)
    for y in range(h):                      # 底边稍亮，横幅要有层次
        dg.line([(0, y), (w, y)], fill=int(255 * (y / h) ** 1.6 * 0.5))
    img = Image.composite(Image.new("RGB", (w, h), CLOUD), img, grad)
    lay = Image.new("L", (w, h), 0)
    dl = ImageDraw.Draw(lay)
    for _ in range(11):                     # 云：只落右 60%
        cx = rnd.randint(int(w * 0.45), w)
        cy = rnd.randint(0, h)
        r = rnd.randint(int(h * 0.6), int(h * 1.5))
        dl.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rnd.randint(26, 60))
    lay = lay.filter(ImageFilter.GaussianBlur(max(10, h // 4)))
    img = Image.composite(Image.new("RGB", (w, h), CLOUD), img,
                          lay.point(lambda v: min(v, 70)))
    dr = ImageDraw.Draw(img)
    tw = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dt = ImageDraw.Draw(tw)
    sr, sg, sb = STAR_RGB
    for _ in range(int(w * h / 620)):
        x, y = rnd.randint(0, w - 1), rnd.randint(0, h - 1)
        a = rnd.randint(*star_band(x, w))
        col = tuple(int(v * a / 255) for v in (sr, sg, sb))
        px = img.getpixel((x, y))
        dr.point((x, y), fill=tuple(min(255, p + q) for p, q in zip(px, col)))
        if a >= TWINKLE_MIN:
            plot_star(dt, x, y, a, w, h)
        if rnd.random() >= 0.9 and x + 1 < w:      # 少量双像素亮星
            px2 = img.getpixel((x + 1, y))
            dr.point((x + 1, y), fill=tuple(min(255, p + q // 2) for p, q in zip(px2, col)))
            if a >= TWINKLE_MIN:
                plot_star(dt, x + 1, y, a // 2, w, h)
    return img, tw


if __name__ == "__main__":
    base, twk = build()
    base.save(OUT)
    twk.save(OUT_TW)
    alpha = twk.tobytes()[3::4]
    print("star ->", OUT, base.size, os.path.getsize(OUT), "bytes")
    print("twinkle ->", OUT_TW, twk.size, os.path.getsize(OUT_TW), "bytes",
          "有晕像素", sum(1 for v in alpha if v), "实心星", sum(1 for v in alpha if v >= TWINKLE_MIN))
