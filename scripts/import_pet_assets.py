#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导入宠物高清素材包（用户提供：全部素材包_修复白色版）

将 7 物种（cat/dog/parrot/rabbit/hamster/snake/gecko）的透明背景 PNG
复制到 backend/data/uploads/pets_assets/{species}/ 供前端加载：
  sheet.png（sprite_sheet 6 帧横向，1536x256）+ idle/eating/playing/sleeping/walking 单帧

用法: python scripts/import_pet_assets.py [素材包目录]
默认目录: <素材包路径>/全部素材包_修复白色版
可重复运行（覆盖旧文件）。
"""
import argparse
import os
import shutil

DEFAULT_SRC = r"<素材包路径>/全部素材包_修复白色版"
SPECIES = ["cat", "dog", "parrot", "rabbit", "hamster", "snake", "gecko"]
# 目标文件名 -> 素材源文件名
MAP = {
    "sheet.png": "sprite_sheet.png",
    "idle.png": "idle_t.png",
    "eating.png": "eating_t.png",
    "playing.png": "playing_t.png",
    "sleeping.png": "sleeping_t.png",
    "walking.png": "walking_t.png",
}


def main():
    parser = argparse.ArgumentParser(description="导入宠物高清素材包")
    parser.add_argument("src", nargs="?", default=DEFAULT_SRC)
    args = parser.parse_args()

    # 输出目录相对脚本位置定位（与 generate_pet_sprites.py 一致）
    here = os.path.dirname(os.path.abspath(__file__))
    dst_root = os.path.abspath(os.path.join(here, "..", "backend", "data", "uploads", "pets_assets"))

    if not os.path.isdir(args.src):
        print(f"[ERROR] 素材目录不存在: {args.src}")
        raise SystemExit(1)

    total = 0
    for sp in SPECIES:
        d = os.path.join(dst_root, sp)
        os.makedirs(d, exist_ok=True)
        for out_name, src_name in MAP.items():
            s = os.path.join(args.src, f"{sp}_{src_name}")
            if not os.path.exists(s):
                print(f"[WARN] 缺少 {sp}_{src_name}")
                continue
            shutil.copy2(s, os.path.join(d, out_name))
            total += 1
        print(f"{sp}: {sorted(os.listdir(d))}")
    print(f"[OK] 共复制 {total} 个素材文件 -> {dst_root}")


if __name__ == "__main__":
    main()
