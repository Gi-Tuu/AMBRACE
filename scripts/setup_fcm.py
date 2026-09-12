# -*- coding: utf-8 -*-
"""FCM 离线推送一键配置（2026-09-12）

用法：
    python scripts/setup_fcm.py <google-services.json> <firebase-服务账号.json>
    python scripts/setup_fcm.py --check          # 只检查现网配置状态

做的事：
    1. 从 google-services.json 提取客户端配置（project_id / app_id / api_key / sender_id / storage_bucket）；
    2. 校验服务账号 JSON（type=service_account、含 private_key、project_id 与客户端一致）；
    3. 把服务账号文件复制到 backend/data/fcm-service-account.json（稳定位置，且已被 .gitignore 忽略）；
    4. 写入/更新项目根 .env 的 4 个 PUSH_FCM_* 键；
    5. 打印结果与后续动作（重启服务）。

注意：两个输入文件都是敏感凭证，本脚本会把服务账号复制进 backend/data/（已忽略），
     google-services.json 不需要留在项目里。
"""
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")
SA_DST = os.path.join(ROOT, "backend", "data", "fcm-service-account.json")


def fail(msg):
    print("[FCM] 错误：" + msg)
    sys.exit(1)


def load_json(path, what):
    if not os.path.isfile(path):
        fail("%s 不存在：%s" % (what, path))
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        fail("%s 解析失败（%s）：%s" % (what, path, e))


def extract_client_config(gs):
    """从 google-services.json 里取客户端所需字段。"""
    pi = gs.get("project_info") or {}
    clients = gs.get("client") or []
    if not clients:
        fail("google-services.json 里没有 client 段（是不是选错了文件？）")
    ci = clients[0]          # 多包名时取第一个；如需指定，请自行裁剪该文件
    client_info = ci.get("client_info") or {}
    api_keys = (ci.get("api_key") or [{}])
    cfg = {
        "apiKey": (api_keys[0] or {}).get("current_key", ""),
        "appId": client_info.get("mobilesdk_app_id", ""),
        "messagingSenderId": pi.get("project_number", ""),
        "projectId": pi.get("project_id", ""),
        "storageBucket": pi.get("storage_bucket", ""),
    }
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        fail("google-services.json 缺少字段：%s" % ", ".join(missing))
    pkg = ((client_info.get("android_client_info") or {}).get("package_name") or "")
    return cfg, pkg


def check_service_account(sa, expect_project):
    if sa.get("type") != "service_account":
        fail("服务账号 JSON 的 type 不是 service_account（看起来不是「服务账号私钥」文件）")
    if not sa.get("private_key"):
        fail("服务账号 JSON 缺少 private_key")
    if expect_project and sa.get("project_id") and sa["project_id"] != expect_project:
        fail("服务账号 project_id（%s）与客户端 project_id（%s）不一致，请确认两者来自同一个 Firebase 项目"
             % (sa["project_id"], expect_project))


def upsert_env(keys):
    lines = []
    if os.path.isfile(ENV_PATH):
        with io.open(ENV_PATH, encoding="utf-8", newline="") as f:
            lines = f.read().splitlines()
    done = set()
    out = []
    for ln in lines:
        name = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if name in keys:
            out.append("%s=%s" % (name, keys[name]))
            done.add(name)
        else:
            out.append(ln)
    for k, v in keys.items():
        if k not in done:
            out.append("%s=%s" % (k, v))
    with io.open(ENV_PATH, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(out) + "\n")
    return sorted(keys.keys())


def do_check():
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        from app.config import settings
    except Exception as e:
        fail("导入 settings 失败（先确认 venv 可用）：%s" % e)
    print("[FCM] 当前配置：")
    print("  PUSH_FCM_ENABLED        =", settings.push_fcm_enabled)
    print("  PUSH_FCM_PROJECT_ID     =", settings.push_fcm_project_id or "(空)")
    print("  PUSH_FCM_CREDENTIALS    =", settings.push_fcm_credentials_path or "(空)",
          "->", "存在" if settings.push_fcm_credentials_path and
          os.path.isfile(settings.push_fcm_credentials_path) else "文件不存在/未配置")
    cfg = (settings.push_fcm_client_config or "").strip()
    print("  PUSH_FCM_CLIENT_CONFIG  =", (cfg[:60] + "...") if cfg else "(空)")
    print("[FCM] 结论：" + ("已启用" if (settings.push_fcm_enabled and cfg and
          settings.push_fcm_credentials_path) else "未启用（缺配置）"))


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--check":
        do_check()
        return
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    gs = load_json(args[0], "google-services.json")
    sa = load_json(args[1], "服务账号 JSON")
    cfg, pkg = extract_client_config(gs)
    check_service_account(sa, cfg["projectId"])

    os.makedirs(os.path.dirname(SA_DST), exist_ok=True)
    shutil.copy2(args[1], SA_DST)

    keys = {
        "PUSH_FCM_ENABLED": "true",
        "PUSH_FCM_CREDENTIALS_PATH": SA_DST.replace("\\", "/"),
        "PUSH_FCM_PROJECT_ID": cfg["projectId"],
        "PUSH_FCM_CLIENT_CONFIG": json.dumps(cfg, ensure_ascii=False, separators=(",", ":")),
    }
    written = upsert_env(keys)

    print("[FCM] 配置完成")
    print("  Android 包名（客户端） :", pkg or "（未读到）")
    print("  Firebase 项目          :", cfg["projectId"])
    print("  服务账号已复制到        :", SA_DST)
    print("  已写入 .env 的键        :", ", ".join(written))
    print("")
    print("[FCM] 后续：")
    print("  1) 重启服务：python scripts/server_manager.py restart")
    print("  2) App 侧登录后会自动 GET /api/v1/device/fcm-config 并用它初始化 Firebase")
    print("  3) 把 App 退到后台/划掉，让角色发一条主动消息，验证离线接收")
    print("  4) 确认 backend/data/fcm-service-account.json 未被提交（.gitignore 已含）")
    if pkg and pkg != "com.gituu.ambrace":
        print("")
        print("[FCM] 提醒：客户端包名是 %s，与当前 Android 包名 com.gituu.ambrace 不一致，"
              "推送会收不到，请在 Firebase 里注册正确包名。" % pkg)


if __name__ == "__main__":
    main()
