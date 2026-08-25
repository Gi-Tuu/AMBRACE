#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""像素风宠物形象生成器（32x32 逻辑像素 → 256x256 PNG，透明背景，nearest 放大）

用法: python scripts/generate_pet_sprites.py [--out backend/data/uploads/pets]
可重复运行：改配色/造型后重新生成即可覆盖旧图。
"""
import argparse
import os

from PIL import Image

S = 32
SCALE = 8

# 调色板（RGB）
K = (30, 30, 30, 255)      # 黑：描边/眼/嘴
W = (255, 255, 255, 255)   # 白
O = (245, 158, 11, 255)    # 橘（猫）
P = (251, 146, 185, 255)   # 粉（耳内/腮红）
T = (139, 94, 60, 255)     # 棕（狗）
SAND = (232, 213, 183, 255)  # 米（肚皮/嘴部）
Y = (242, 184, 75, 255)    # 金（仓鼠）
G = (76, 175, 80, 255)     # 绿（鹦鹉）
DG = (46, 125, 50, 255)    # 深绿（翅膀）
R = (229, 57, 53, 255)     # 红（羽冠）
OR = (251, 140, 0, 255)    # 橙（喙）


def new_canvas() -> dict:
    return {}


def fill_ellipse(c: dict, cx: float, cy: float, rx: float, ry: float, color) -> None:
    for dy in range(-int(ry) - 1, int(ry) + 2):
        for dx in range(-int(rx) - 1, int(rx) + 2):
            if (dx / rx) ** 2 + (dy / ry) ** 2 <= 1.0:
                c[(int(cx + dx), int(cy + dy))] = color


def fill_tri(c: dict, x0, y0, x1, y1, x2, y2, color) -> None:
    ys = sorted([y0, y1, y2])
    for y in range(ys[0], ys[2] + 1):
        xs = []
        for (ax, ay, bx, by) in ((x0, y0, x1, y1), (x1, y1, x2, y2), (x2, y2, x0, y0)):
            if ay == by:
                continue
            if min(ay, by) <= y <= max(ay, by):
                t = (y - ay) / (by - ay)
                xs.append(ax + t * (bx - ax))
        if not xs:
            continue
        xa, xb = int(min(xs)), int(max(xs))
        for x in range(xa, xb + 1):
            c[(x, y)] = color


def eyes(c: dict, cx1, cx2, cy) -> None:
    fill_ellipse(c, cx1, cy, 1, 2, K)
    fill_ellipse(c, cx2, cy, 1, 2, K)
    c[(cx1, cy - 1)] = W  # 高光
    c[(cx2, cy - 1)] = W


def blush(c: dict, cx1, cx2, cy) -> None:
    fill_ellipse(c, cx1, cy, 2, 1, P)
    fill_ellipse(c, cx2, cy, 2, 1, P)


def tiny_mouth(c: dict, cy) -> None:
    c[(15, cy)] = K
    c[(17, cy)] = K
    c[(16, cy + 1)] = K


def draw_cat() -> dict:
    c = new_canvas()
    # 耳朵（描边→橘→粉内）
    fill_tri(c, 5, 0, 13, 0, 10, 10, K)
    fill_tri(c, 19, 0, 27, 0, 22, 10, K)
    fill_tri(c, 6, 1, 12, 1, 9, 9, O)
    fill_tri(c, 20, 1, 26, 1, 23, 9, O)
    fill_tri(c, 8, 3, 10, 3, 9, 7, P)
    fill_tri(c, 22, 3, 24, 3, 23, 7, P)
    # 头
    fill_ellipse(c, 16, 14, 12, 10, K)
    fill_ellipse(c, 16, 14, 11, 9, O)
    # 身体
    fill_ellipse(c, 16, 26, 10, 7, K)
    fill_ellipse(c, 16, 26, 9, 6, O)
    fill_ellipse(c, 16, 27, 5, 3, W)  # 白肚皮
    # 脸
    eyes(c, 11, 21, 14)
    blush(c, 8, 24, 17)
    tiny_mouth(c, 18)
    return c


def draw_dog() -> dict:
    c = new_canvas()
    # 垂耳
    fill_ellipse(c, 7, 9, 4, 6, K)
    fill_ellipse(c, 25, 9, 4, 6, K)
    fill_ellipse(c, 7, 9, 3, 5, T)
    fill_ellipse(c, 25, 9, 3, 5, T)
    # 头
    fill_ellipse(c, 16, 15, 12, 10, K)
    fill_ellipse(c, 16, 15, 11, 9, T)
    # 口鼻部（米色）
    fill_ellipse(c, 16, 19, 5, 4, SAND)
    # 鼻子
    fill_ellipse(c, 16, 17, 1, 1, K)
    # 身体
    fill_ellipse(c, 16, 26, 10, 7, K)
    fill_ellipse(c, 16, 26, 9, 6, T)
    fill_ellipse(c, 16, 27, 5, 3, SAND)
    # 脸
    eyes(c, 11, 21, 13)
    blush(c, 8, 24, 16)
    tiny_mouth(c, 20)
    return c


def draw_rabbit() -> dict:
    c = new_canvas()
    # 长耳（左/右）
    fill_ellipse(c, 10, 4, 3, 7, K)
    fill_ellipse(c, 22, 4, 3, 7, K)
    fill_ellipse(c, 10, 4, 2, 6, W)
    fill_ellipse(c, 22, 4, 2, 6, W)
    fill_ellipse(c, 10, 4, 1, 4, P)
    fill_ellipse(c, 22, 4, 1, 4, P)
    # 头
    fill_ellipse(c, 16, 14, 11, 9, K)
    fill_ellipse(c, 16, 14, 10, 8, W)
    # 身体
    fill_ellipse(c, 16, 25, 9, 6, K)
    fill_ellipse(c, 16, 25, 8, 5, W)
    fill_ellipse(c, 16, 26, 4, 2, SAND)
    # 脸
    eyes(c, 11, 21, 13)
    blush(c, 8, 24, 16)
    c[(16, 17)] = K  # 鼻
    c[(16, 18)] = K  # 三瓣嘴竖线
    c[(14, 19)] = K
    c[(18, 19)] = K
    return c


def draw_hamster() -> dict:
    c = new_canvas()
    # 圆耳
    fill_ellipse(c, 9, 6, 3, 3, K)
    fill_ellipse(c, 23, 6, 3, 3, K)
    fill_ellipse(c, 9, 6, 2, 2, Y)
    fill_ellipse(c, 23, 6, 2, 2, Y)
    fill_ellipse(c, 9, 6, 1, 1, P)
    fill_ellipse(c, 23, 6, 1, 1, P)
    # 头（圆胖）
    fill_ellipse(c, 16, 14, 12, 10, K)
    fill_ellipse(c, 16, 14, 11, 9, Y)
    # 颊囊（白）
    fill_ellipse(c, 9, 18, 3, 3, W)
    fill_ellipse(c, 23, 18, 3, 3, W)
    # 身体
    fill_ellipse(c, 16, 25, 9, 6, K)
    fill_ellipse(c, 16, 25, 8, 5, Y)
    fill_ellipse(c, 16, 26, 4, 2, W)
    # 脸
    eyes(c, 11, 21, 13)
    c[(16, 16)] = K  # 鼻
    c[(15, 18)] = K
    c[(17, 18)] = K
    return c


def draw_parrot() -> dict:
    c = new_canvas()
    # 羽冠
    fill_tri(c, 13, 0, 19, 0, 16, 7, K)
    fill_tri(c, 14, 1, 18, 1, 16, 6, R)
    # 头
    fill_ellipse(c, 16, 12, 8, 7, K)
    fill_ellipse(c, 16, 12, 7, 6, G)
    # 喙
    fill_tri(c, 13, 13, 19, 13, 16, 19, K)
    fill_tri(c, 14, 14, 18, 14, 16, 18, OR)
    # 眼睛（喙两侧）
    fill_ellipse(c, 12, 11, 1, 2, K)
    fill_ellipse(c, 20, 11, 1, 2, K)
    c[(12, 10)] = W
    c[(20, 10)] = W
    # 身体
    fill_ellipse(c, 16, 23, 8, 7, K)
    fill_ellipse(c, 16, 23, 7, 6, G)
    fill_ellipse(c, 16, 23, 4, 3, SAND)  # 胸
    # 翅膀（深绿）
    fill_ellipse(c, 8, 22, 3, 5, DG)
    fill_ellipse(c, 24, 22, 3, 5, DG)
    # 尾羽
    for y in range(29, 32):
        for x in range(14, 19):
            c[(x, y)] = DG
    return c


DRAWERS = {
    "cat": draw_cat,
    "dog": draw_dog,
    "rabbit": draw_rabbit,
    "hamster": draw_hamster,
    "parrot": draw_parrot,
}


def render(pixels: dict) -> Image.Image:
    small = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    for (x, y), rgb in pixels.items():
        if 0 <= x < S and 0 <= y < S:
            small.putpixel((x, y), rgb)
    return small.resize((S * SCALE, S * SCALE), Image.NEAREST)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/data/uploads/pets")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for species, drawer in DRAWERS.items():
        img = render(drawer())
        path = os.path.join(args.out, f"{species}.png")
        img.save(path, "PNG")
        print(f"{species}.png  {img.size[0]}x{img.size[1]}  {os.path.getsize(path)} bytes")


if __name__ == "__main__":
    main()
