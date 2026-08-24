# -*- coding: utf-8 -*-
"""获取服务器连接信息（部署后运行一次即可）：
    - 局域网 IP：手机与服务器同一 Wi-Fi 时，填这个地址即可
    - Tailscale IP：手机与服务器跨网络时，经 Tailscale 组网后填这个地址
    - 配置自检：LLM / 图片理解是否已配置（不输出任何密钥明文）

用法：python scripts/get_server_info.py
"""
import os
import socket
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        pass
    try:
        ips = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None)
               if i[0] == socket.AF_INET and not i[4][0].startswith("127.")]
        return ips[0] if ips else ""
    except Exception:
        return ""


def get_tailscale_ip() -> str:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=10
        )
        ip = (out.stdout or "").strip().splitlines()
        return ip[0] if ip else ""
    except Exception:
        return ""


def env_configured(keys: list[str]) -> bool:
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return False
    vals = {}
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    vals[k.strip()] = v.strip()
    except Exception:
        return False
    return any(bool(vals.get(k)) for k in keys)


def main():
    print("=" * 56)
    print("拥爱（AMBRACE）服务器连接信息")
    print("=" * 56)

    lan = get_lan_ip()
    ts = get_tailscale_ip()

    print("\n[1] 服务器地址（手机端「设置 → 服务器地址」填写）")
    if lan:
        print(f"    - 同一局域网：http://{lan}:8000")
    else:
        print("    - 同一局域网：未探测到（请确认服务器网络连接）")
    if ts:
        print(f"    - Tailscale 组网：http://{ts}:8000")
    else:
        print("    - Tailscale：未安装/未登录。跨网络访问可安装 Tailscale 后重跑本脚本。")

    print("\n[2] 配置自检")
    print("    - LLM（聊天必需）：", "已在 .env 配置" if env_configured(["LLM_API_KEY", "DEEPSEEK_API_KEY"]) else "未在 .env 配置（推荐登录主账号后经「设置 → API 配置」落库）")
    print("    - 生图（可选）：", "已配置" if env_configured(["IMAGE_GEN_API_KEY"]) else "未配置（可选）")
    print("    - 图片理解云端 Key（可选）：", "已配置" if env_configured(["VLM_API_KEY"]) else "未配置（默认仅本地 OCR 识文）")

    print("\n[3] 健康检查")
    print("    启动服务后访问：http://127.0.0.1:8000/api/v1/system/health")
    print("    或：python scripts/server_manager.py status")
    print("\n提示：手机与服务器需同一局域网，或双方都装 Tailscale 并登录同一账号。")
    print("      .env 与 API Key 属隐私，不会随开源包分发，请勿提交。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
