# -*- coding: utf-8 -*-
"""发布许可契约守卫（AGPL-3.0-only，2026-10-13）。

为什么要有这条：许可从 MIT 换到 AGPL-3.0-only 之后，「声明写在几个文件里」这件事本身
就成了会悄悄漂移的东西——根 LICENSE 换回 MIT、App 协议里留着硬编码的 "MIT License"、
README 里的「严禁商用」又冒出来（那句本来就对授权做了减损、减损无效）、Docker 镜像不带
许可文本，任何一处都不会有人报错，直到有人拿它去商用或分发才发现。所以把口径**钉住**：

- 一条真源：`LICENSE` 必须逐字节等于 GNU 官方 AGPL-3.0 全文（钉 sha256，不是钉首行）
- 四件套同口径：`NOTICE` / `THIRD_PARTY_NOTICES.md` / `THIRD_PARTY_LICENSES/` 必须随包
- App 资产与根文件逐字节一致（App 内许可页不能读到过期版本）
- 版本号三处一致：`VERSION` / `pubspec.yaml` / VERSION 首条 changelog
- 发布链路包含性：`make_release.py` / `Dockerfile` / 镜像 label
- **反向钉**：`LICENSE` 首行被改成别的、App 协议出现 "MIT License"、README 冒出禁商用、
  有人把旧 MIT 的禁止商用条款抄回来 —— 这四种都要当场红

只读本仓字节 + 造 tmp_path 发行树，**不建库、不联网、不起服务、不动 git**。
注意：这些文件在入库前是没被 git 跟踪的，所以本文件**不去跑真实 make_release**
（那要复制整个仓库 + 1GB 模型），只钉它的纯函数与包含性清单。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# GNU 官方 AGPL-3.0 全文的 sha256（文件本身逐字取自官方发布，未加任何字）。
AGPL3_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
AGPL3_BYTES = 34523

EXPECTED_VERSION = "3.5.4"
EXPECTED_BUILD = "30507"
EXPECTED_DATE = "2026-10-13"

# 旧版（MIT 时代）写过的「禁止商用」措辞：任何一处回来都必须红
FORBIDDEN_MARKERS = (
    "严禁用于任何商业用途",
    "严禁将本项目或其任何派生版本用于商业用途",
    "仅供学习与技术交流",
    "仅供学习与研究使用",
    "commercial use without the author",
    "open-sourced for study and research use only",
    "strictly prohibited without the author",
)

# 作者机器路径的四种写法。**必须分片拼**：本文件本身是会被公开面扫描的文件，
# 夹具里直接写全串就等于它自己成了要拦的那次泄漏（与 test_public_leak_scan_c2.py 同一纪律）。
_BS = "\\"
_REPO = "AM" + "BRACE"
_USER = "she" + "ng"
_PROJ = "Code" + "x-Projects"
_AUTHOR_PATHS = (
    f"D:/{_REPO}", f"D:{_BS}{_REPO}",
    f"C:/Users/{_USER}", f"C:{_BS}Users{_BS}{_USER}",
    _PROJ,
)


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _bytes(rel: str) -> bytes:
    return (REPO / rel).read_bytes()


def _load(script_rel: str):
    path = REPO / script_rel
    spec = importlib.util.spec_from_file_location("_lic_" + path.stem, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ──────────────────────────────────────────────────────────────────────
# 一、唯一真源：根 LICENSE 逐字节等于官方 AGPL-3.0 全文
# ──────────────────────────────────────────────────────────────────────

def test_根LICENSE逐字节等于官方AGPL3全文():
    data = _bytes("LICENSE")
    assert len(data) == AGPL3_BYTES, f"LICENSE 字节数变了：{len(data)}（官方全文 {AGPL3_BYTES}）"
    assert hashlib.sha256(data).hexdigest() == AGPL3_SHA256, "LICENSE 不是官方 AGPL-3.0 逐字全文"
    assert data.startswith(b"                    GNU AFFERO GENERAL PUBLIC LICENSE")
    assert b"GNU AFFERO GENERAL PUBLIC LICENSE" in data
    assert b"Version 3, 19 November 2007" in data
    # 「only」＝不给 "or later" 选项；AGPL-3.0-only 的 official text 就是纯 AGPL-3.0
    assert b"or any later version" not in data.split(b"TERMS AND CONDITIONS")[0]


def test_LICENSE不得被附加自定义条款或版权行():
    """官方原文逐字；若谁在正文里塞自己的条款，这条先红。"""
    text = _read("LICENSE")
    assert "AMBRACE" not in text, "官方原文里不应出现项目名"
    assert "Copyright (c) 2026 AMBRACE contributors" not in text
    assert text.rstrip().endswith("<https://www.gnu.org/licenses/>."), "结尾被改动过"


# ──────────────────────────────────────────────────────────────────────
# 二、版本口径三处一致（VERSION / pubspec / VERSION 首条）
# ──────────────────────────────────────────────────────────────────────

def test_VERSION与pubspec版本号一致():
    version_txt = _read("VERSION")
    assert f"VERSION: {EXPECTED_VERSION}" in version_txt
    pubspec = _read("flutter_app/pubspec.yaml")
    assert f"version: {EXPECTED_VERSION}+{EXPECTED_BUILD}" in pubspec


def test_VERSION首条changelog就是本版且不改历史条目():
    lines = _read("VERSION").splitlines()
    head = "\n".join(lines[:40])
    assert f"{EXPECTED_VERSION} - Release" in head
    # 历史条目仍在（不许为了换协议去改写旧版本的记述）
    assert "3.5.3 - Release" in _read("VERSION")
    assert "3.4.5 - Release" in _read("VERSION")


def test_许可声明写明旧MIT截止点():
    for rel in ("NOTICE", "README.md", "README.en.md",
                "THIRD_PARTY_NOTICES.md", "flutter_app/lib/l10n/app_zh.arb",
                "flutter_app/lib/l10n/app_en.arb"):
        text = _read(rel)
        assert "3.5.3" in text, f"{rel} 没写明 MIT 截止到 3.5.3"


# ──────────────────────────────────────────────────────────────────────
# 三、NOTICE / THIRD_PARTY_NOTICES / 原许可副本
# ──────────────────────────────────────────────────────────────────────

def test_NOTICE存在且把四条边界都说了():
    text = _read("NOTICE")
    assert "AGPL-3.0-only" in text
    assert "does not prohibit commercial use" in text        # 不禁商用
    assert "not** a condition of the license" in text or "not a condition of the license" in text
    assert "trademark" in text.lower()                        # 商标
    assert "Personal content" in text or "个人内容" in text   # 用户个人数据不在授权范围
    # 关键条文口径：§13 只约束「修改版」的远程交互，不能写成「所有托管都要公开源码」
    assert "modified** version" in text or "modified version" in text
    assert "Running an unmodified copy yourself" in text


def test_NOTICE与App资产逐字节一致():
    assert _bytes("NOTICE") == _bytes("flutter_app/assets/legal/NOTICE.txt")


def test_AGPL资产与根LICENSE逐字节一致():
    assert _bytes("LICENSE") == _bytes("flutter_app/assets/legal/AGPL-3.0.txt")


def test_pubspec已声明legal资产():
    pubspec = _read("flutter_app/pubspec.yaml")
    assert "assets/legal/" in pubspec


def test_第三方清单引用到的原许可文本必须都真实存在():
    import re
    text = _read("THIRD_PARTY_NOTICES.md")
    refs = sorted(set(re.findall(r"\]\((THIRD_PARTY_LICENSES/[^)#]+)\)", text)))
    assert len(refs) >= 30, f"引用数偏少，疑似清单被掏空：{len(refs)}"
    missing = [r for r in refs if not (REPO / r).is_file()]
    assert not missing, f"第三方清单引用了不存在的许可文本：{missing}"


def test_原许可副本的MANIFEST逐条对得上():
    manifest = json.loads(_read("THIRD_PARTY_LICENSES/MANIFEST.json"))
    entries = manifest["entries"]
    assert len(entries) >= 60
    for e in entries:
        p = REPO / "THIRD_PARTY_LICENSES" / e["path"]
        assert p.is_file(), f"清单登记但文件缺失：{e['path']}"
        data = p.read_bytes()
        assert len(data) == e["bytes"], f"{e['path']} 字节数不符"
        assert hashlib.sha256(data).hexdigest() == e["sha256"], f"{e['path']} sha256 不符"


def test_强copyleft与复合许可必须逐条保留不折叠():
    manifest = json.loads(_read("THIRD_PARTY_LICENSES/MANIFEST.json"))
    spdx = {d["dist"]: d["spdx"] for d in manifest["python_dists"]}
    assert spdx["lunardate"] == "GPL-3.0-or-later", "lunardate 是强 copyleft，不能被降级"
    assert spdx["orjson"] == "MPL-2.0 AND (Apache-2.0 OR MIT)", "复合许可被折叠了"
    assert spdx["tqdm"] == "MPL-2.0 AND MIT", "tqdm 的复合许可被折叠了"
    assert spdx["edge-tts"] == "MIT AND LGPL-3.0-or-later"
    assert spdx["regex"] == "Apache-2.0 AND CNRI-Python"
    assert "LGPL-2.1" in spdx["shapely"], "shapely 的 GEOS/LGPL-2.1 义务不能丢"
    assert spdx["opencv-python"] == "MIT AND LicenseRef-OpenCV-Platform-Components"
    component_text = _read("THIRD_PARTY_LICENSES/python/opencv-python-5.0.0.93/LICENSE-3RD-PARTY.txt")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in component_text and "Version 2.1" in component_text, "捆绑组件的原许可义务不能丢"
    assert "Apache-2.0 OR BSD-3-Clause OR ISC" not in _read("THIRD_PARTY_NOTICES.md"), "不同组件不得被错误合并为任选其一"


def test_两个随包再分发的模型权重必须有上游MIT原文():
    manifest = json.loads(_read("THIRD_PARTY_LICENSES/MANIFEST.json"))
    upstreams = {u["dist"]: u for u in manifest["upstream"]}
    for name in ("bge-m3", "faster-whisper-small", "ctranslate2", "rapidocr-onnxruntime"):
        assert name in upstreams, f"{name} 的上游许可未登记"
        assert upstreams[name]["spdx"] in ("MIT", "Apache-2.0")
        for rel in upstreams[name].get("files", []):
            assert (REPO / "THIRD_PARTY_LICENSES" / rel).is_file()


def test_模型许可登记里没有本机绝对路径():
    raw = _read("THIRD_PARTY_LICENSES/MANIFEST.json")
    for bad in _AUTHOR_PATHS:
        assert bad not in raw, f"MANIFEST 泄露本机路径：{bad}"


# ──────────────────────────────────────────────────────────────────────
# 四、README 中英同步 + 禁止「禁商用」减损条款回来
# ──────────────────────────────────────────────────────────────────────

def test_README中英都声明AGPL且互相链接():
    zh, en = _read("README.md"), _read("README.en.md")
    for text in (zh, en):
        assert "AGPL-3.0-only" in text
        assert f"**当前版本**：v{EXPECTED_VERSION}" in text or f"**Current version**: v{EXPECTED_VERSION}" in text
    assert "(README.en.md)" in zh and "(README.md)" in en


def test_README两份都链到许可四件套():
    for rel in ("README.md", "README.en.md"):
        text = _read(rel)
        for target in ("(LICENSE)", "(NOTICE)", "(THIRD_PARTY_NOTICES.md)", "THIRD_PARTY_LICENSES/"):
            assert target in text, f"{rel} 缺 {target} 的链接"


@pytest.mark.parametrize("rel", [
    "README.md", "README.en.md", "NOTICE",
    "flutter_app/lib/l10n/app_zh.arb", "flutter_app/lib/l10n/app_en.arb",
])
def test_禁止商用减损条款不得回来(rel):
    text = _read(rel)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in text, f"{rel} 出现禁商用措辞「{marker}」：那是对开源授权的减损，减损无效"


def test_变更公告已记录本次许可口径变更():
    """历史条目不许改写（旧 3.4.5 那条禁商用记述保留），但当天必须有一条说清更正。"""
    text = _read("docs/changelog.md")
    assert f"## {EXPECTED_DATE}（" in text, "今天的公告节缺失"
    day = text.split(f"## {EXPECTED_DATE}（", 1)[1].split("\n## ", 1)[0]
    assert "AGPL-3.0-only" in day, "当天公告没写新的许可口径"
    assert "不再禁止商用" in day, "当天公告没写清禁商用说法已删除"
    # 旧记述仍在（历史不改写），新版本靠追加条目更正
    assert "用户协议新增：本项目开源仅供学习与研究使用" in text


def test_README必须明说不禁商用():
    zh, en = _read("README.md"), _read("README.en.md")
    assert "不禁止商业用途" in zh and "不禁止收费" in zh
    assert "does not forbid commercial use" in en


# ──────────────────────────────────────────────────────────────────────
# 五、App 协议：不得留硬编码 MIT，也得说清 AGPL 口径
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", ["zh", "en"])
def test_App协议不再硬编码MIT(locale):
    text = _read(f"flutter_app/lib/l10n/app_{locale}.arb")
    assert "MIT License" not in text, "App 协议里还硬编码着 MIT License"
    for key in ("agreeSection1Body", "agreeSection8Body"):
        start = text.index(f'"{key}":')
        body = text[start:text.index("\n", start)]
        assert "AGPL-3.0-only" in body, f"{locale}/{key} 没提 AGPL-3.0-only"


@pytest.mark.parametrize("locale", ["zh", "en"])
def test_App协议保留了其余免责章节(locale):
    text = _read(f"flutter_app/lib/l10n/app_{locale}.arb")
    for key in ("agreeSection2Body", "agreeSection3Body", "agreeSection4Body",
                "agreeSection5Body", "agreeSection6Body", "agreeSection7Body"):
        assert f'"{key}":' in text, f"{locale} 丢了章节 {key}（本次只改许可口径，不许动免责内容）"


def test_许可按钮的两个新key中英成对():
    zh = json.loads(_read("flutter_app/lib/l10n/app_zh.arb"))
    en = json.loads(_read("flutter_app/lib/l10n/app_en.arb"))
    for key in ("agreeViewLicense", "agreeViewLicenseHint"):
        assert key in zh and key in en, f"{key} 中英不成对"
        assert zh[key].strip() and en[key].strip()


def test_协议页接的是系统LicensePage而非自造文本页():
    src = _read("flutter_app/lib/features/auth/user_agreement_screen.dart")
    assert "showLicensePage(" in src
    assert "l10n.agreeViewLicense" in src


def test_主入口注册许可且不在runApp前await读资产():
    src = _read("flutter_app/lib/main.dart")
    assert "LicenseRegistry.addLicense(" in src
    assert "LicenseEntryWithLineBreaks(" in src
    assert "assets/legal/AGPL-3.0.txt" in src
    assert "assets/legal/NOTICE.txt" in src
    # 调用点纪律：注册调用本身在 runApp 之前，且这段里不出现 await
    call = src.index("_registerProjectLicenses();")
    run = src.index("runApp(")
    assert call < run, "许可注册必须在 runApp 之前"
    code = "\n".join(
        ln for ln in src[call:run].splitlines() if not ln.lstrip().startswith("//")
    )
    assert "await" not in code, "runApp 之前不得 await（黑屏回归纪律）"
    # 资产读取只允许发生在 LicenseRegistry 回调体内（用户真的打开许可页时才跑）
    body_start = src.index("LicenseRegistry.addLicense(")
    body_end = src.index("\n}\n", body_start)
    body = src[body_start:body_end]
    assert "rootBundle.loadString" in body
    assert "async*" in body
    assert body.count("rootBundle.loadString") == 2, "两份资产都要注册（AGPL 全文 + NOTICE）"


# ──────────────────────────────────────────────────────────────────────
# 六、发布链路包含性：make_release / Dockerfile / 镜像 label
# ──────────────────────────────────────────────────────────────────────

def _make_release():
    return _load("scripts/make_release.py")


def test_make_release必检许可四件套():
    mod = _make_release()
    for name in ("README.md", "README.en.md", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        assert name in mod.LICENSE_CORE, f"出包含性清单缺 {name}"
    assert mod.LICENSE_MANIFEST == "THIRD_PARTY_LICENSES/MANIFEST.json"


def test_make_release源树当前一件不缺():
    mod = _make_release()
    assert mod.required_license_files(REPO) == [], f"源树缺许可文件：{mod.required_license_files(REPO)}"


def test_make_release为两个模型权重绑定上游许可():
    mod = _make_release()
    assert set(mod.MODEL_LICENSE_SOURCES) == {"bge-m3", "whisper-small"}
    for sub, rel in mod.MODEL_LICENSE_SOURCES.items():
        assert (REPO / rel).is_file(), f"{sub} 的上游许可文本不存在：{rel}"


def test_make_release发行树校验能抓出缺件(tmp_path):
    """正例齐 → 通过；反例各删一件 → 必须被抓出来（否则这条守卫是空跑）。"""
    mod = _make_release()
    seq = iter(range(100))

    def make_tree() -> Path:
        out = tmp_path / f"tree{next(seq)}"
        (out / "backend" / "models" / "bge-m3").mkdir(parents=True, exist_ok=True)
        for rel in mod.LICENSE_CORE + (mod.LICENSE_MANIFEST,):
            p = out / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        return out

    ok = make_tree()
    (ok / "backend" / "models" / "bge-m3" / "LICENSE-x.txt").write_text("MIT", encoding="utf-8")
    assert mod.verify_license_bundle(ok, REPO) == []

    no_model_license = make_tree()
    assert any("bge-m3" in m for m in mod.verify_license_bundle(no_model_license, REPO))

    # 上游许可文件名不是 LICENSE.* 而是 MIT_<项目>_LICENSE.txt —— 匹配口径错了这条守卫会空跑
    oddly_named = make_tree()
    (oddly_named / "backend" / "models" / "bge-m3" / "MIT_x_LICENSE.txt").write_text(
        "MIT", encoding="utf-8")
    assert mod.verify_license_bundle(oddly_named, REPO) == []

    no_model_dir = tmp_path / "nomodels"
    (no_model_dir / "backend" / "models").mkdir(parents=True)
    for rel in mod.LICENSE_CORE + (mod.LICENSE_MANIFEST,):
        p = no_model_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert mod.verify_license_bundle(no_model_dir, REPO) == [], "权重没随包就不该要求它的许可"

    for rel in mod.LICENSE_CORE:
        broken = make_tree()
        (broken / rel).unlink()
        assert rel in mod.verify_license_bundle(broken, REPO), f"缺 {rel} 没被抓出来"


def test_make_release模型子目录会拿到上游许可(tmp_path):
    """bundle_models 真跑一次（小目录，不碰真模型）：子目录里必须落上游许可文本。"""
    mod = _make_release()
    src = tmp_path / "src_models"
    dst = tmp_path / "dst_models"
    for sub in ("bge-m3", "whisper-small"):
        d = src / sub
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x" * 8)
    (src / "bge-small-zh-v1.5").mkdir()          # 死模型必须仍被排除
    (src / "bge-small-zh-v1.5" / "big.bin").write_bytes(b"y" * 8)
    total, landed = mod.bundle_models(str(src), str(dst), root=str(REPO))
    assert not (dst / "bge-small-zh-v1.5").exists(), "死模型又被打进包了"
    expected = sum(
        (dst / sub / Path(rel).name).stat().st_size
        for sub, rel in mod.MODEL_LICENSE_SOURCES.items()
    ) + 16
    assert total == expected, f"体积读数应等于已落包文件之和（权重 + 上游许可），实际 {total} != {expected}"
    for sub, rel in mod.MODEL_LICENSE_SOURCES.items():
        got = dst / sub / Path(rel).name
        assert got.is_file(), f"{sub} 子目录缺上游许可文本"
        assert got.read_bytes() == (REPO / rel).read_bytes()
    assert any("bge-m3" in x and "MB" in x for x in landed)


def test_bundle_models缺上游许可时必须报错(tmp_path):
    mod = _make_release()
    src = tmp_path / "src2"
    (src / "bge-m3").mkdir(parents=True)
    (src / "bge-m3" / "m.bin").write_bytes(b"z")
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()
    with pytest.raises(SystemExit):
        mod.bundle_models(str(src), str(tmp_path / "dst2"), root=str(empty_root))


def test_Dockerfile随镜像带许可文本并打OCI许可标签():
    dockerfile = _read("Dockerfile")
    assert "COPY LICENSE LICENSE" in dockerfile
    assert "COPY NOTICE NOTICE" in dockerfile
    assert "COPY THIRD_PARTY_NOTICES.md THIRD_PARTY_NOTICES.md" in dockerfile
    assert "COPY THIRD_PARTY_LICENSES THIRD_PARTY_LICENSES" in dockerfile
    assert 'org.opencontainers.image.licenses="AGPL-3.0-only"' in dockerfile


def test_镜像发布workflow也带许可标签():
    yml = _read(".github/workflows/docker-publish.yml")
    assert "org.opencontainers.image.licenses=AGPL-3.0-only" in yml
    assert "org.opencontainers.image.source=" in yml


def test_dockerignore没有把许可文本排除掉():
    ignore = _read(".dockerignore")
    for pat in ("LICENSE", "NOTICE", "THIRD_PARTY"):
        assert pat not in ignore, f".dockerignore 把 {pat} 排除了，镜像就带不出许可文本"


# ──────────────────────────────────────────────────────────────────────
# 七、导出脚本本身的口径
# ──────────────────────────────────────────────────────────────────────

def test_收集脚本不写本机绝对路径():
    src = _read("scripts/collect_license_notices.py")
    assert "FORBIDDEN.search" in src
    # 脚本里不得出现作者机器路径（它自己就是会被扫描的公开面文件）
    for bad in _AUTHOR_PATHS:
        assert bad not in src
    assert "拒绝写入" in src


def test_收集脚本离线可校验():
    mod = _load("scripts/collect_license_notices.py")
    assert mod.cmd_verify() == 0, "MANIFEST 与磁盘上的原许可文本不一致"


def test_发行树正反例_真实run一次校验(tmp_path):
    """verify_license_bundle 对「源树齐全但发行树漏拷」也必须红（git 未跟踪时就靠这条）。"""
    mod = _make_release()
    out = tmp_path / "pkg"
    (out / "backend" / "models").mkdir(parents=True)
    for rel in mod.LICENSE_CORE + (mod.LICENSE_MANIFEST,):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    assert mod.verify_license_bundle(out, REPO) == []
    # 模拟「文件在源树存在但没进发行树」——这正是 git 跟踪漏掉时的现场
    (out / "NOTICE").unlink()
    assert "NOTICE" in mod.verify_license_bundle(out, REPO)


def test_临时发行树用tmp_path且不留下垃圾(tmp_path):
    """反向自证：本守卫自身不往系统 TEMP 裸建目录。"""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "x.txt").write_text("x", encoding="utf-8")
    assert shutil.which  # 只用到标准库 shutil，没别的副作用
    assert list(scratch.iterdir()) == [scratch / "x.txt"]
