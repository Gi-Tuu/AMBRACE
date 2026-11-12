"""仿真星球表面纹理生成器（可重跑）。

产出 planet_moon.png / planet_sun.png：512x512、以 512px 为周期可水平无缝平铺的
等距圆柱投影纹理。页面用 background-position-x 平移来做**自转**，球面光照、明暗界线、
大气辉光由 CSS 叠加，所以纹理转而光不转（物理上正确），不是拿 CSS 画一个圆。

    python build_planet.py            # 写 web/planet_moon.png 与 web/planet_sun.png
"""
import os
import random

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_MOON = os.path.join(HERE, "planet_moon.png")
OUT_SUN = os.path.join(HERE, "planet_sun.png")
W = H = 512
random.seed(20261107)


def noise(scale, blur):
    """低分辨率白噪声放大 + 模糊，作为一层连续地貌/湍流（横向周期性）。"""
    n = max(2, int(W / scale))
    small = Image.new("L", (n, max(2, n // 2)))
    px = small.load()
    for y in range(small.height):
        for x in range(n):
            px[x, y] = random.randint(0, 255)
    # 左右各补一列首像素，保证 resize 后左右边界连续
    tiled = Image.new("L", (n + 1, small.height))
    tiled.paste(small, (0, 0))
    tiled.putpixel((n, 0), small.getpixel((0, 0)))
    return tiled.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(blur))


def compose(base, layers):
    out = Image.new("RGB", (W, H), base)
    op = out.load()
    for img, amp, tint in layers:
        ip = img.load()
        for y in range(H):
            for x in range(W):
                v = (ip[x, y] - 128) / 128.0
                r, g, b = op[x, y]
                op[x, y] = (max(0, min(255, int(r + amp * v * tint[0]))),
                            max(0, min(255, int(g + amp * v * tint[1]))),
                            max(0, min(255, int(b + amp * v * tint[2]))))
    return out


def seamless(img):
    """左右各 40px 向内线性融合到平均值，使 x=0 与 x=511 完全相等 —— 平铺自转无接缝。"""
    p = img.load()
    band = 40
    for y in range(H):
        l = [p[x, y] for x in range(band)]
        r = [p[W - 1 - x, y] for x in range(band)]
        for x in range(band):
            t = (band - x) / band
            a, b = l[x], r[x]
            avg = tuple((a[i] + b[i]) // 2 for i in range(3))
            p[x, y] = tuple(int(a[i] * (1 - t) + avg[i] * t) for i in range(3))
            p[W - 1 - x, y] = tuple(int(b[i] * (1 - t) + avg[i] * t) for i in range(3))
    return img


def blotches(img, mask, threshold, dark, amount):
    """用连续噪声阈值生成**不规则**暗斑（月海/黑子），不画规则圆。"""
    mp, op = mask.load(), img.load()
    for y in range(H):
        for x in range(W):
            v = mp[x, y]
            if v <= threshold:
                continue
            a = ((v - threshold) / (255 - threshold)) ** 1.4 * amount
            r, g, b = op[x, y]
            op[x, y] = (int(r * (1 - a) + dark[0] * a),
                        int(g * (1 - a) + dark[1] * a),
                        int(b * (1 - a) + dark[2] * a))
    return img


def moon():
    big = noise(64, 12)
    img = compose((150, 146, 138), [
        (big, 40, (1, 1, 1)),
        (noise(19, 5), 24, (1, 1, 1)),
        (noise(6, 1.6), 13, (1, 1, 1)),
    ])
    # 月海：大尺度噪声高值压暗成不规则玄武岩平原
    blotches(img, big, 138, (86, 84, 80), 0.55)
    # 细碎撞击坑：高频噪声低值压暗，边缘自然不圆
    fines = noise(9, 2.2)
    blotches(img, Image.eval(fines, lambda v: 255 - v), 176, (104, 101, 96), 0.30)
    return seamless(img.filter(ImageFilter.GaussianBlur(0.5)))


def sun():
    turb = noise(30, 6)
    img = compose((238, 130, 50), [
        (turb, 62, (1.0, 0.96, 0.52)),
        (noise(11, 2.6), 44, (1.0, 0.9, 0.45)),
        (noise(3.5, 1.0), 27, (1.0, 0.86, 0.4)),
    ])
    # 黑子：中尺度噪声低值；亮斑：高值，都是不规则形状
    spots = noise(22, 3.2)
    blotches(img, Image.eval(spots, lambda v: 255 - v), 168, (150, 56, 20), 0.42)
    flare = noise(14, 2)
    fp, op = flare.load(), img.load()
    for y in range(H):
        for x in range(W):
            if fp[x, y] > 196:
                a = (fp[x, y] - 196) / 59 * 0.55
                r, g, b = op[x, y]
                op[x, y] = (min(255, int(r + (255-r)*a)), min(255, int(g + (226-g)*a)),
                            min(255, int(b + (150-b)*a)))
    return seamless(img.filter(ImageFilter.GaussianBlur(0.9)))


if __name__ == "__main__":
    m, s = moon(), sun()
    m.save(OUT_MOON)
    s.save(OUT_SUN)
    print("moon ->", OUT_MOON, os.path.getsize(OUT_MOON), "bytes", m.size)
    print("sun  ->", OUT_SUN, os.path.getsize(OUT_SUN), "bytes", s.size)
