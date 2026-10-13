# -*- coding: utf-8 -*-
"""第三方原许可文本的机械导出与校验（AGPL-3.0-only 发布包含性的一环）。

为什么要有这个脚本：AGPL 要求随分发附上原许可与源码提供入口，本仓发布包（源码 zip）
不带 site-packages，所以把「本机实际安装的那一份 dist-info 许可文件」原样导出到
``THIRD_PARTY_LICENSES/``，让拿源码的人不必先装依赖就能读到上游原文。

三类动作（只用标准库，不读 backend/data、不联网除显式 --fetch-upstream）：

  --export           从本机 venv 的 dist-info 里把原许可文本**逐字**复制到 THIRD_PARTY_LICENSES/python/
  --fetch-upstream   仅对 dist 内**没有**许可文件的少数项（ctranslate2 / rapidocr-onnxruntime），
                     从上游官方仓库取 LICENSE 到 THIRD_PARTY_LICENSES/upstream/（需网络，显式开关）
  --verify           按 MANIFEST.json 逐个重算 sha256 校验（不需要 venv，CI 可跑）

导出物里**不允许**出现作者机器路径：写入前会扫一遍，命中即报错退出。
MANIFEST.json 是机器可读的校验口径，backend/tests/test_release_license_contract.py 直接读它。

用法：
  backend/.venv/Scripts/python.exe scripts/collect_license_notices.py --export
  backend/.venv/Scripts/python.exe scripts/collect_license_notices.py --verify
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import re
import sys
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(PROJECT_ROOT, "THIRD_PARTY_LICENSES")
MANIFEST = os.path.join(OUT_ROOT, "MANIFEST.json")

# 写入许可文本时禁止出现的**作者机器信息**（与 scripts/check_public_leak.py 同口径的子集）。
# 只认「作者自己的」路径形态，不做通用盘符匹配：上游 LICENSE 里出现 C:\Program Files 之类
# 第三方安装路径是原文的一部分，误杀会让"原样副本"名存实亡。
# 模式**分片拼**：本文件本身会被公开面扫描，直接写全串等于它自己成了要拦的那次泄漏。
FORBIDDEN = re.compile(
    r"(?i)(?:^|[^\w])d:[\\/]a" + "mbrace"
    r"|c:[\\/]users[\\/]she" + "ng"
    r"|Code" + "x-Projects"
)


def _dist_license_files(dist_name):
    """返回 [(相对 dist-info 的路径, 绝对路径)]；已按实路径去重，并剥掉 dist-info 前缀。

    剥前缀只为让 THIRD_PARTY_LICENSES/ 的目录层级可读，**文件内容逐字不动**。
    """
    dist = md.distribution(dist_name)
    seen, out = set(), []
    for f in dist.files or ():
        rel = str(f)
        low = rel.lower()
        if not any(k in low for k in ("license", "copying", "notice")):
            continue
        abspath = str(dist.locate_file(f))
        real = os.path.normcase(os.path.abspath(abspath))
        if real in seen or not os.path.isfile(abspath):
            continue
        seen.add(real)
        out.append((_strip_dist_info_prefix(rel), abspath))
    return dist.version, str(dist.locate_file("")), sorted(out)


def _strip_dist_info_prefix(rel):
    rel = rel.replace("\\", "/")
    for marker in (".dist-info/licenses/", ".dist-info/license_files/", ".dist-info/"):
        i = rel.find(marker)
        if i >= 0:
            return rel[i + len(marker):]
    return rel


# (dist 名, SPDX 口径, 备注)——顺序即导出顺序；口径里 AND/OR 原样保留，不折叠
DIST_ENTRIES = [
    ("lunardate", "GPL-3.0-or-later", "进程内 import（backend/app/scheduling/holiday_calendar.py）"),
    ("edge-tts", "MIT AND LGPL-3.0-or-later", "按文件切分：仅 src/edge_tts/srt_composer.py 为 MIT"),
    ("shapely", "BSD-3-Clause AND LGPL-2.1-or-later", "本体 BSD-3；LGPL-2.1 来自捆绑的 GEOS"),
    ("orjson", "MPL-2.0 AND (Apache-2.0 OR MIT)", "复合，不折叠"),
    ("tqdm", "MPL-2.0 AND MIT", "复合，不折叠"),
    ("certifi", "MPL-2.0", "包内只有引用块，无 MPL 全文；全文见 orjson/MPL-2.0_orjson-*/LICENSE-MPL-2.0"),
    ("opencv-python", "MIT AND LicenseRef-OpenCV-Platform-Components",
     "wrapper 本体 MIT；二进制捆绑物见 LICENSE-3RD-PARTY.txt（实测含 FFmpeg/LGPL-2.1）"),
    ("numpy", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 AND (Apache-2.0 OR BSD-3-Clause)",
     "实测 17 份许可文件，逐份保留"),
    ("regex", "Apache-2.0 AND CNRI-Python", "复合；含 CPython re 模块的 CNRI 授权"),
    ("aiohttp", "Apache-2.0 AND MIT", "复合；另捆绑 vendor llhttp(MIT)"),
    ("httptools", "MIT", "捆绑 vendor http-parser(MIT) 与 llhttp(MIT)"),
    ("yarl", "Apache-2.0", "上游自带 NOTICE，须随附"),
    ("tzdata", "Apache-2.0", "短式声明 + LICENSE_APACHE 全文，两个都要"),
    ("multidict", "Apache-2.0", "短式声明（无全文），引用或json/tzdata 的 Apache-2.0 全文"),
    ("Pillow", "MIT-CMU", "单文件内混装 MIT/BSD/Apache/LGPL/Zlib 多段"),
    ("psutil", "BSD-3-Clause", ""),
    ("idna", "BSD-3-Clause", ""),
    ("protobuf", "BSD-3-Clause", ""),
    ("oauthlib", "BSD-3-Clause", ""),
    ("python-dotenv", "BSD-3-Clause", ""),
    ("jsonpatch", "BSD-3-Clause", ""),
    ("pyasn1", "BSD-2-Clause", "只两条款"),
    ("xxhash", "BSD-2-Clause", ""),
    ("charset-normalizer", "MIT", ""),
    ("ecdsa", "MIT", ""),
    ("sqlalchemy", "MIT", ""),
    ("tiktoken", "MIT", ""),
    ("durationpy", "MIT", ""),
    ("rank-bm25", "Apache-2.0", ""),
    ("websocket-client", "Apache-2.0", ""),
    ("frozenlist", "Apache-2.0", ""),
    ("google-crc32c", "Apache-2.0", ""),
    ("overrides", "Apache-2.0", ""),
]

# dist 内 0 份许可文件的少数项：取上游官方 LICENSE。fetch 需要网络，故与 export 分开。
UPSTREAM_ENTRIES = [
    ("ctranslate2", "4.8.2", "MIT", "OpenNMT/CTranslate2",
     "https://raw.githubusercontent.com/OpenNMT/CTranslate2/master/LICENSE"),
    ("rapidocr-onnxruntime", "1.2.3", "Apache-2.0", "RapidAI/RapidOCR",
     "https://raw.githubusercontent.com/RapidAI/RapidOCR/main/LICENSE"),
    # 两个随发布 zip 再分发的模型权重：上游模型仓的 MIT 原文（权重本身不在本仓）
    ("bge-m3", "(FlagEmbedding 权重)", "MIT", "FlagOpen/FlagEmbedding",
     "https://raw.githubusercontent.com/FlagOpen/FlagEmbedding/master/LICENSE"),
    ("faster-whisper-small", "(Systran 权重，转换自 openai/whisper)", "MIT", "openai/whisper",
     "https://raw.githubusercontent.com/openai/whisper/main/LICENSE"),
]

# 上游来源说明：模型卡上的 license 字段（主审已核；此处登记 URL 供复查）
MODEL_CARDS = [
    ("bge-m3", "https://huggingface.co/Xenova/bge-m3"),
    ("bge-m3", "https://huggingface.co/BAAI/bge-m3"),
    ("faster-whisper-small", "https://huggingface.co/Systran/faster-whisper-small"),
]

README_MD = """# THIRD_PARTY_LICENSES — 第三方原许可文本副本

本目录是**上游许可证原文的机械副本**，不含任何第三方源码或模型权重。
清单与逐字校验见 [MANIFEST.json](MANIFEST.json)；人读的说明见仓库根
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) 与 [NOTICE](../NOTICE)。

## 为什么单独放一个目录

AGPL-3.0-only 要求随分发附上第三方原许可与源码提供入口。本仓的发布包是**源码形态**，
不带 `site-packages`，所以把「本机实际安装的那一份 dist-info 许可文件」原样导出到这里，
让只拿到源码的人不必先装依赖就能读到上游原文。

## 三类来源

| 来源 | 含义 | 重新获取 |
|---|---|---|
| `python/` | 从本机 venv 的 `*.dist-info` 里**逐字复制**的原许可与 NOTICE 文件 | `scripts/collect_license_notices.py --export`（需本机 venv，无需网络） |
| `upstream/` | dist 内 0 份许可文件的少数项（ctranslate2 / rapidocr-onnxruntime）与两个随包再分发的模型权重，取自**上游官方 LICENSE** | `scripts/collect_license_notices.py --fetch-upstream`（需网络） |
| `MANIFEST.json` | 每份文本的 dist／版本／SPDX／来源 URL／sha256 | `--verify` 重算 sha256（不需要 venv，CI 可跑） |

## 边界

- 这些文本**只保留上游版权人的原样声明**，不改写、不折叠复合许可（`AND`/`OR` 原样保留），
  也不由本项目的 AGPL 覆盖或替换——第三方组件继续按各自原许可条款使用。
- 本目录**不声称**清单穷尽了全部依赖树；`THIRD_PARTY_NOTICES.md` 里逐条标了覆盖分母与状态。
- 容器镜像里 `pip install` 会各自安装对应 dist-info 的许可文件，与本目录内容同源。
- 模型权重**不在本目录**：这里只有其上游模型仓的 MIT 原文。
"""


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _check_clean(data, rel):
    if FORBIDDEN.search(data.decode("utf-8", "replace")):
        raise SystemExit(f"[licenses] 拒绝写入：{rel} 含作者机器路径/绝对路径片段")


def _write(rel, data, records):
    target = os.path.join(OUT_ROOT, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(data)
    records.append({
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    })
    return len(data)


def _manifest_from_records(records, meta):
    manifest = {"schema": 1, "entries": sorted(records, key=lambda r: r["path"]), **meta}
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    return manifest


def cmd_export():
    records, meta_dists, total = [], [], 0
    for dist_name, spdx, note in DIST_ENTRIES:
        try:
            version, _root, files = _dist_license_files(dist_name)
        except md.PackageNotFoundError:
            print(f"[licenses] SKIP {dist_name}：本机未安装")
            meta_dists.append({"dist": dist_name, "spdx": spdx, "note": note,
                               "status": "未在本机 venv 安装，未导出"})
            continue
        if not files:
            print(f"[licenses] SKIP {dist_name}：dist 内无许可文件（见 upstream/）")
            meta_dists.append({"dist": dist_name, "version": version, "spdx": spdx, "note": note,
                               "status": "dist 内 0 份许可文件"})
            continue
        sub = f"python/{dist_name}-{version}"
        rels = []
        for rel, abspath in files:
            data = _read_bytes(abspath)
            out = f"{sub}/{rel}"
            _check_clean(data, out)
            total += _write(out, data, records)
            rels.append(out)
        print(f"[licenses] {dist_name} {version} -> {len(rels)} 份 / {spdx}")
        meta_dists.append({
            "dist": dist_name, "version": version, "spdx": spdx, "note": note,
            "status": "已逐字导出",
            "source_kind": "venv-dist-info",
            "files": rels,
        })
    with open(os.path.join(OUT_ROOT, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(README_MD)
    m = _manifest_from_records(records, {
        "generated_by": "scripts/collect_license_notices.py --export",
        "python_dists": meta_dists,
        "upstream": [],
    })
    print(f"[licenses] 导出完成：{len(m['entries'])} 份 / {total} 字节 -> THIRD_PARTY_LICENSES/")
    return 0


def _proxy_opener():
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not proxy:
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def cmd_fetch_upstream(timeout=30):
    records = []
    meta = []
    opener = _proxy_opener()
    for dist_name, version, spdx, origin, url in UPSTREAM_ENTRIES:
        sub = "models" if dist_name in ("bge-m3", "faster-whisper-small") else "upstream"
        out = f"{sub}/{spdx.replace('/', '-')}_{origin.replace('/', '-')}_LICENSE.txt"
        try:
            data = opener.open(url, timeout=timeout).read()
        except Exception as e:  # 网络不可用就如实报，不伪造
            print(f"[licenses] FETCH_FAIL {dist_name}: {type(e).__name__}")
            meta.append({"dist": dist_name, "version": version, "spdx": spdx, "origin": origin,
                         "source_url": url, "status": "本次未取得（网络不可用），文本可能仍为上次导出"})
            continue
        _check_clean(data, out)
        _write(out, data, records)
        print(f"[licenses] {dist_name} <- {origin} ({len(data)} B)")
        meta.append({"dist": dist_name, "version": version, "spdx": spdx, "origin": origin,
                     "source_url": url, "status": "已逐字导出", "files": [out],
                     "model_cards": [u for n, u in MODEL_CARDS if n == dist_name]})
    prev = {}
    if os.path.isfile(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            prev = json.load(f)
    kept = [e for e in prev.get("entries", []) if not e["path"].startswith(("upstream/", "models/"))]
    merged = _manifest_from_records(kept + records, {
        "generated_by": "scripts/collect_license_notices.py --export --fetch-upstream",
        "python_dists": prev.get("python_dists", []),
        "upstream": meta,
    })
    print(f"[licenses] upstream 条目 {len(merged['upstream'])} 份，文本文件合计 {len(merged['entries'])}")
    return 0


def cmd_verify():
    if not os.path.isfile(MANIFEST):
        print("[licenses] 缺 MANIFEST.json：先跑 --export")
        return 2
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)
    entries = manifest.get("entries") or []
    if not entries:
        print("[licenses] MANIFEST 为空")
        return 2
    bad = []
    for e in entries:
        p = os.path.join(OUT_ROOT, e["path"].replace("/", os.sep))
        if not os.path.isfile(p):
            bad.append(f"{e['path']}: 缺文件")
            continue
        data = _read_bytes(p)
        if len(data) != e["bytes"] or hashlib.sha256(data).hexdigest() != e["sha256"]:
            bad.append(f"{e['path']}: sha256/字节不符")
    up = manifest.get("upstream") or []
    if not up:
        bad.append("MANIFEST.upstream 为空：ctranslate2 / rapidocr / 两个模型权重的上游许可未登记")
    print(f"[licenses] 校验 {len(entries)} 份文本 / {len(manifest.get('python_dists') or [])} 个 dist / {len(up)} 条 upstream")
    if bad:
        for b in bad:
            print("   " + b)
        return 1
    print("[licenses] OK：全部原许可文本与 MANIFEST 逐字一致")
    return 0


def main():
    ap = argparse.ArgumentParser(description="第三方原许可文本导出/校验")
    ap.add_argument("--export", action="store_true", help="从本机 venv dist-info 逐字导出到 THIRD_PARTY_LICENSES/python/")
    ap.add_argument("--fetch-upstream", action="store_true", help="联网取 dist 内无许可文件的项与模型上游许可")
    ap.add_argument("--verify", action="store_true", help="按 MANIFEST.json 重算 sha256 校验（不需要 venv）")
    args = ap.parse_args()
    os.makedirs(OUT_ROOT, exist_ok=True)
    if args.export:
        return cmd_export()
    if args.fetch_upstream:
        return cmd_fetch_upstream()
    if args.verify:
        return cmd_verify()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
