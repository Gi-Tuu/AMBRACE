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
import json
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

# 2026-08-24：不再硬编码版本号——动态取 GitHub latest release 的 ai_companion_public.zip 附件，
# 每次发版上传 zip 后自动生效；API 失败时回退 HuggingFace 直链（②）。
RELEASE_API = "https://api.github.com/repos/Gi-Tuu/AMBRACE/releases/latest"
HF_BASE = "https://huggingface.co/Xenova/bge-m3/resolve/main"
# 2026-08-24（新机部署报告 P1-5）：国内网络 HuggingFace 常不可达，增加 hf-mirror.com 镜像
HF_MIRROR_BASE = "https://hf-mirror.com/Xenova/bge-m3/resolve/main"

UA = "AMBRACE-download-models/1.0"


def model_complete() -> bool:
    return (MODEL_DIR / TOKENIZER).is_file() and (MODEL_DIR / ONNX).is_file()


def log(msg: str) -> None:
    print(f"[download_models] {msg}")


def _latest_release_zip_url() -> str:
    """从 GitHub latest release 解析 ai_companion_public.zip 附件直链（动态，免维护版本号）。"""
    try:
        req = urllib.request.Request(RELEASE_API, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        tag = data.get("tag_name", "")
        assets = data.get("assets", [])
        for a in assets:
            if (a.get("name") or "").lower() == "ai_companion_public.zip":
                return a.get("browser_download_url") or ""
        # 无附件时按 tag 拼标准直链（发布流程默认命名）
        return f"https://github.com/Gi-Tuu/AMBRACE/releases/download/{tag}/ai_companion_public.zip"
    except Exception:
        return ""


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
    url = _latest_release_zip_url()
    log("方式① 从 GitHub latest release 下载发布包...")
    log(f"  URL: {url}")
    if not url:
        log("  [失败] 无法解析 latest release 附件（可能尚未上传 zip），改用 HuggingFace。")
        return False
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "ai_companion_public.zip"
        try:
            _download(url, zip_path)
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
    bases = [HF_BASE, HF_MIRROR_BASE]  # 官方直连失败后自动切 hf-mirror.com（国内镜像）
    files = ((TOKENIZER, "tokenizer.json"), (ONNX, ONNX.replace("\\", "/")))
    for rel, path in files:
        target = MODEL_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        ok = False
        for base in bases:
            url = base + "/" + path
            log(f"  下载 {rel} （{base}）...")
            try:
                _download(url, target)
                log(f"  已保存 {target.relative_to(ROOT)}（{target.stat().st_size // (1024 * 1024)}MB）")
                ok = True
                break
            except Exception as e:
                log(f"  [失败] {base} 下载失败：{e}")
        if not ok:
            log(f"  [失败] 下载 {rel} 失败（官方与镜像均不可达）。")
            log("  国内网络可尝试：set HF_ENDPOINT=https://hf-mirror.com 后重跑，或手动下载放置到 backend/models/bge-m3/")
            return False
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
