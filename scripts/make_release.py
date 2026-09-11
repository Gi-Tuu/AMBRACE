# -*- coding: utf-8 -*-
"""生成开源发布包（脱敏）：复制 git 跟踪文件 → 排除隐私/内部文件 → 副本脱敏 → 随包复制 README/LICENSE（取自仓库）→ 可选 zip

用法：python scripts/make_release.py [--zip]
输出：<release_output>/ai_companion_public/
"""
import io
import os
import re
import shutil
import subprocess
import sys
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 输出目录：优先读环境变量 AMBRACE_RELEASE_OUT，缺省用项目根同级 release_output/
OUT_ROOT = os.environ.get("AMBRACE_RELEASE_OUT") or os.path.abspath(
    os.path.join(PROJECT_ROOT, "..", "release_output")
)
OUT_DIR = os.path.join(OUT_ROOT, "ai_companion_public")

# git 跟踪清单 = 复制白名单（排除一切未入库的本机生成物）
def tracked_files():
    out = subprocess.run(
        ["git", "-C", PROJECT_ROOT, "ls-files"], capture_output=True, text=True
    ).stdout.splitlines()
    return [f.replace("\\", "/") for f in out if f.strip()]

EXCLUDE_PREFIX = (".agents/", "flutter.bat",  # 内部工具/技能目录与本机脚本，不进开源包
                  "AGENTS.md", "HANDOFF.md",
                  "restart_server.bat", "start_server.bat",
                  # 开发文档与内部规划（含真实用户名/角色名等隐私）不进开源包
                  "docs/")

# 副本内脱敏替换（相对路径 → [(old, new), ...]）
# 仓库代码/脚本已不含作者本机路径，按需在此追加规则。
DESENS = {}

def copy_with_desens(src_root, rel):
    src = os.path.join(src_root, rel.replace("/", os.sep))
    dst = os.path.join(OUT_DIR, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with io.open(src, "rb") as fin:
        data = fin.read()
    # 仅对文本文件做脱敏替换
    if rel.endswith((".py", ".md", ".dart", ".yaml", ".yml", ".json", ".toml", ".txt", ".ini", ".bat", ".ps1", ".properties", ".gradle", ".kts", ".xml", ".plist")):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            for old, new in DESENS.get(rel, []):
                text = text.replace(old, new)
            data = text.encode("utf-8")
    with io.open(dst, "wb") as fout:
        fout.write(data)
    return len(data)


def main():
    files = tracked_files()
    picked = [f for f in files if not f.startswith(EXCLUDE_PREFIX)]
    skipped = [f for f in files if f.startswith(EXCLUDE_PREFIX)]

    if os.path.exists(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    total = 0
    for rel in picked:
        total += copy_with_desens(PROJECT_ROOT, rel)

    # 捆绑向量模型：backend/models 未纳入 git（体积大），但记忆系统必须依赖，发布包必须携带
    models_src = os.path.join(PROJECT_ROOT, "backend", "models")
    if os.path.isdir(models_src):
        models_dst = os.path.join(OUT_DIR, "backend", "models")
        shutil.copytree(models_src, models_dst, ignore=shutil.ignore_patterns(".git"))  # 排除 HuggingFace 仓库自带的 .git LFS 缓存
        model_mb = 0
        for root2, _, names in os.walk(models_src):
            if os.sep + ".git" in root2:
                continue
            for n in names:
                try:
                    model_mb += os.path.getsize(os.path.join(root2, n))
                except OSError:
                    pass
        print(f"[release] bundled backend/models ({model_mb / 1024 / 1024:.1f} MB)")

    # App 更新公告数据源：docs/changelog.md 是唯一随包分发的 docs 文件（其余内部文档仍排除）
    changelog_src = os.path.join(PROJECT_ROOT, "docs", "changelog.md")
    if os.path.isfile(changelog_src):
        copy_with_desens(PROJECT_ROOT, "docs/changelog.md")
        print("[release] bundled docs/changelog.md")

    # README / LICENSE 单一来源：直接取项目根被 git 跟踪的 README.md / LICENSE（随上面复制流程进包）
    for _name in ("README.md", "LICENSE"):
        if not os.path.isfile(os.path.join(OUT_DIR, _name)):
            raise SystemExit(f"[release] 缺少 {_name}：请确认项目根存在该文件且已被 git 跟踪")

    # 微信赞赏码（作者放置于项目根目录，发布前替换；不存在则跳过，README 占位图缺失属预期）
    qr_src = os.path.join(PROJECT_ROOT, "reward-qrcode.png")
    if os.path.isfile(qr_src):
        shutil.copy2(qr_src, os.path.join(OUT_DIR, "reward-qrcode.png"))
        print("[release] bundled reward-qrcode.png")

    # 复制后安全扫描：对脱敏后的发布包内容检查密钥/本机路径/局域网 IP（命中告警但不阻止）
    text_exts = (".py", ".md", ".dart", ".yaml", ".yml", ".json", ".toml", ".txt", ".ini", ".bat", ".ps1", ".properties", ".gradle", ".kts", ".xml", ".plist", ".example", ".lock")
    key_re = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{16,}")
    priv_re = re.compile(r"([A-Za-z]:\\|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})")
    # 真实用户名/角色名等隐私词（命中告警，防止内部案例文档混入）
    # 名单从 scripts/.privacy_names 读取（本地维护，不随发布包分发）；文件不存在则跳过
    name_re = None
    name_file = os.path.join(PROJECT_ROOT, "scripts", ".privacy_names")
    if os.path.exists(name_file):
        with io.open(name_file, encoding="utf-8") as f:
            _names = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        if _names:
            name_re = re.compile("|".join(re.escape(n) for n in _names))
    warnings = []
    for root2, _, names in os.walk(OUT_DIR):
        for n in names:
            if not any(n.endswith(e) for e in text_exts) and '.' in n:
                continue  # 有扩展名且非文本 → 跳过；无扩展名（.gitignore/.metadata/VERSION/LICENSE）也扫描
            full = os.path.join(root2, n)
            rel = os.path.relpath(full, OUT_DIR).replace("\\", "/")
            if rel == "scripts/make_release.py":
                continue  # 自身含安全扫描的正则模式定义（盘符/IP 片段），跳过
            rel = os.path.relpath(full, OUT_DIR).replace("\\", "/")
            try:
                text = io.open(full, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for m in key_re.finditer(text):
                warnings.append(f"[KEY] {rel}: {m.group(0)[:14]}...")
            for m in priv_re.finditer(text):
                warnings.append(f"[PRIV] {rel}: {m.group(0)}")
            if name_re:
                for m in name_re.finditer(text):
                    warnings.append(f"[NAME] {rel}: {m.group(0)}")
    if warnings:
        print("[release] 以下文件疑似含密钥/隐私（请人工确认后再上传）：")
        for warn in warnings[:30]:
            print("   " + warn)
    else:
        print("[release] 安全扫描通过：发布包无疑似密钥/本机路径/局域网 IP")

    print(f"[release] {len(picked)} files copied, {total / 1024:.1f} KB")
    print(f"[release] excluded {len(skipped)} privacy/internal files: {skipped}")

    if "--zip" in sys.argv:
        zpath = os.path.join(OUT_ROOT, "ai_companion_public.zip")
        if os.path.exists(zpath):
            os.remove(zpath)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, names in os.walk(OUT_DIR):
                for n in names:
                    full = os.path.join(root, n)
                    zf.write(full, os.path.relpath(full, OUT_ROOT))
        print(f"[release] zip: {zpath}")


if __name__ == "__main__":
    main()
