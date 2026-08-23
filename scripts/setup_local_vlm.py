# -*- coding: utf-8 -*-
"""可选：一键安装 Ollama 并下载本地图片理解 VLM 模型（qwen2.5vl:3b，约 3.2GB）。

默认不启用本地 VLM（默认走云端视觉 API Key 或仅 OCR）。
需要离线/本地识图时运行本脚本：
    python scripts/setup_local_vlm.py
完成后把 .env 里的 VLM_ENABLED 改为 true，并填写 VLM_OLLAMA_EXE / VLM_OLLAMA_MODELS_DIR。
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

MODEL = "qwen2.5vl:3b"
OLLAMA_EXE_CANDIDATES = [
    "ollama",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
    os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama"),
    "/usr/local/bin/ollama",
    "/usr/bin/ollama",
]


def find_ollama() -> str | None:
    for c in OLLAMA_EXE_CANDIDATES:
        if c and os.path.exists(c):
            return c
    for c in OLLAMA_EXE_CANDIDATES[:1]:
        if c == "ollama":
            try:
                subprocess.run(["ollama", "--version"], capture_output=True, timeout=10)
                return shutil.which("ollama") or "ollama"
            except Exception:
                pass
    return None


def download(url: str, dest: str) -> None:
    print(f"下载 {url} ...")
    with urllib.request.urlopen(url) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def install_windows():
    dest = os.path.join(tempfile.gettempdir(), "OllamaSetup.exe")
    download("https://ollama.com/download/OllamaSetup.exe", dest)
    print("安装 Ollama（静默）...")
    subprocess.run([dest, "/S"], check=True)
    exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
    if not os.path.exists(exe):
        raise RuntimeError("Ollama 安装后未找到可执行文件：" + exe)
    return exe


def install_macos():
    zip_path = os.path.join(tempfile.gettempdir(), "Ollama-darwin.zip")
    download("https://ollama.com/download/Ollama-darwin.zip", zip_path)
    print("解压到 ~/Applications ...")
    os.makedirs(os.path.expanduser("~/Applications"), exist_ok=True)
    subprocess.run(["unzip", "-o", zip_path, "-d", os.path.expanduser("~/Applications")], check=True)
    return os.path.expanduser("~/Applications/Ollama.app/Contents/Resources/ollama")


def install_linux():
    print("使用官方安装脚本安装 Ollama...")
    subprocess.run("curl -fsSL https://ollama.com/install.sh | sh", shell=True, check=True)
    return "/usr/local/bin/ollama"


def pull_model(exe: str) -> None:
    print(f"下载模型 {MODEL}（约 3.2GB，耗时取决于网络）...")
    subprocess.run([exe, "pull", MODEL], check=True)


def main() -> int:
    system = platform.system()
    exe = find_ollama()
    if exe:
        print(f"检测到 Ollama：{exe}")
    else:
        print(f"未检测到 Ollama，开始安装（平台 {system}）...")
        if system == "Windows":
            exe = install_windows()
        elif system == "Darwin":
            exe = install_macos()
        else:
            exe = install_linux()
        print(f"Ollama 安装完成：{exe}")
    pull_model(exe)

    models_dir = ""
    if system == "Windows":
        models_dir = os.path.expandvars(r"%USERPROFILE%\.ollama\models")
    else:
        models_dir = os.path.expanduser("~/.ollama/models")
    print("\n完成！请在项目根目录 .env 中配置：")
    print(f"  VLM_ENABLED=true")
    print(f"  VLM_OLLAMA_EXE={exe}")
    print(f"  VLM_OLLAMA_MODELS_DIR={models_dir}")
    print("然后重启服务即可使用本地识图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
