#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""一键下载本地向量模型 bge-m3（记忆系统必需）。

背景：git 不随源码分发 backend/models/bge-m3（约 542MB，本地 ONNX int8，1024d）。
部署时若缺该目录，聊天/记忆向链路会因缺模型报错（见 app/memory/embedding.py）。

用法（项目根执行）：
  python scripts\download_models.py            # Windows
  python scripts/download_models.py            # Linux/macOS
  加 --force  强制重新下载覆盖；默认已存在则跳过。

下载来源（依次尝试）：
  ① GitHub Release v3.2.0 附件 ai_companion_public.zip（内含 backend/models/bge-m3）
     https://github.com/Gi-Tuu/AMBRACE/releases/tag/v3.2.0
  ② HuggingFace Xenova/bge-m3 的 tokenizer.json + onnx/model_int8.onnx
     https://huggingface.co/Xenova/bge-m3

下载完成后解压到 backend/models/bge-m3。任一步失败都会给出明确中文提示后退出（非 0）。
"""
import argparse
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "backend" / "models" / "bge-m3"
TOKENIZER = "tokenizer.json"
ONNX = os.path.join("onnx", "model_int8.onnx")

RELEASE_ZIP_URL = "https://github.com/Gi-Tuu/AMBRACE/releases/download/v3.2.0/ai_companion_public.zip"
HF_BASE = "https://huggingface.co/Xenova/bge-m3/resolve/main"

UA = "AMBRACE-download-models/1.0"


def model_complete() -> bool:
    return (MODEL_DIR / TOKENIZER).is_file() and (MODEL_DIR / ONNX).is_file()


def log(msg: str) -> None:
    print(f"[download_models] {msg}")


def _download(url: str, dest: Path) -> None:
    """流式下载 url 到 dest（stdlib urllib，处理 GitHub/HF 重定向与超时）。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # 115s 读超时：542MB 在慢网也够；连接超时 15s。
    with urllib.request.urlopen(req, timeout=115) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                sys.stdout.write(f"\r  {done // (1024 * 1024)}MB / {total // (1024 * 1024)}MB ({pct}%)")
                sys.stdout.flush()
    sys.stdout.write("\n")


def _extract_zip_models(zip_path: Path) -> bool:
    """从发布包 zip 中抽取 backend/models/bge-m3/ 到模型目录。返回是否找到该目录。"""
    prefix = "backend/models/bge-m3"
    found = False
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            norm = member.replace("\\", "/")
            if norm.startswith(prefix + "/"):
                rel = norm[len(prefix) + 1:]
                if not rel:
                    continue
                target = MODEL_DIR / rel
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                found = True
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read(4 * 1024 * 1024))
    return found


def _download_from_release() -> bool:
    log("方式① 从 GitHub Release v3.2.0 下载发布包...")
    log(f"  URL: {RELEASE_ZIP_URL}")
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "ai_companion_public.zip"
        try:
            _download(RELEASE_ZIP_URL, zip_path)
        except Exception as e:
            log(f"  [失败] 下载发布包失败：{e}")
            return False
        log(f"  已下载 {zip_path.stat().st_size // (1024 * 1024)}MB，开始解压模型目录...")
        ok = _extract_zip_models(zip_path)
        if not ok:
            log("  [失败] 发布包中未找到 backend/models/bge-m3 目录，改用 HuggingFace。")
            return False
        log("  解压完成。")
        return True


def _download_from_huggingface() -> bool:
    log("方式② 从 HuggingFace Xenova/bge-m3 下载 tokenizer.json + onnx/model_int8.onnx...")
    files = ((TOKENIZER, HF_BASE + "/" + TOKENIZER),
             (ONNX, HF_BASE + "/" + ONNX.replace("\\", "/")))
    for rel, url in files:
        target = MODEL_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        log(f"  下载 {rel} ...")
        try:
            _download(url, target)
        except Exception as e:
            log(f"  [失败] 下载 {rel} 失败：{e}")
            return False
        log(f"  已保存 {target.relative_to(ROOT)}（{target.stat().st_size // (1024 * 1024)}MB）")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地向量模型 bge-m3 到 backend/models/bge-m3")
    parser.add_argument("--force", action="store_true", help="强制重新下载覆盖（默认已存在则跳过）")
    args = parser.parse_args()

    print("=" * 60)
    print("  下载本地向量模型 bge-m3（约 542MB）")
    print("=" * 60)

    if model_complete() and not args.force:
        log(f"模型已存在：{MODEL_DIR.relative_to(ROOT)}，跳过。如需覆盖加 --force。")
        return 0

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if _download_from_release():
        if model_complete():
            log(f"完成：{MODEL_DIR.relative_to(ROOT)} 已就绪（tokenizer.json + onnx/model_int8.onnx）。")
            return 0
        log("发布包解压后文件不完整，尝试 HuggingFace ...")

    if _download_from_huggingface():
        if model_complete():
            log(f"完成：{MODEL_DIR.relative_to(ROOT)} 已就绪。")
            return 0
        log("[失败] HuggingFace 下载后文件不完整（应含 tokenizer.json 与 onnx/model_int8.onnx）。")
        return 1

    log("[失败] 两种下载方式均未能完成。请检查网络后重试，或手动从 Release 下载")
    log("      ai_companion_public.zip 并解压 backend/models/ 到项目根目录。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
