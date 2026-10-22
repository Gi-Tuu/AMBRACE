#!/usr/bin/env python3
r"""一键下载本地向量模型 bge-m3（记忆系统必需）。

背景：git 不随源码分发 backend/models/bge-m3（约 542MB，本地 ONNX int8，1024d）。
部署时若缺该目录，聊天/记忆向链路会因缺模型报错（见 app/memory/embedding.py）。

用法（项目根执行）：
  python scripts\download_models.py            # Windows
  python scripts/download_models.py            # Linux/macOS
  加 --force  强制重新下载覆盖；默认已存在则跳过。

下载来源（依次尝试）：
  ① 当前 latest Release 的完整包附件 ai_companion_public.zip（内含 backend/models/bge-m3）
     ——**该附件只在每个版本首次发布时上传**（2026-10-11 拍板），同版本后续修订不重挂，
     所以这里按「附件存在才用它，不存在就直接走②」处理，不猜直链。
  ② HuggingFace Xenova/bge-m3 的 tokenizer.json + onnx/model_int8.onnx
     https://huggingface.co/Xenova/bge-m3

安全 B1（SEC-P1-1）：
- ZIP 成员流式完整复制（不再一次 read(4MiB) 截断），逐成员校验字节数与 CRC；
- 只接受 backend/models/bge-m3/ 与 ai_companion_public/backend/models/bge-m3/ 两种根，
  ../、绝对路径/盘符/UNC、反斜杠绕过、符号链接、重复目标一律拒绝；
- 同卷专属临时区先解压/校验，再逐文件发布；安装失败按回滚清单恢复旧文件。
  多文件逐项 replace 不是集合原子；突然断电等边界不做绝对原子承诺。

下载完成后解压到 backend/models/bge-m3。任一步失败都会给出明确中文提示后退出（非 0）。
"""
import argparse
import itertools
import json
import ntpath
import os
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "backend" / "models" / "bge-m3"
TOKENIZER = "tokenizer.json"
ONNX = os.path.join("onnx", "model_int8.onnx")

# 2026-08-24：不再硬编码版本号——动态取 GitHub latest release 的 ai_companion_public.zip 附件。
# 2026-10-11：完整包改为「只在每个版本首次发布时上传」⇒ latest 上没有该附件是**预期状态**，
# 此时直接回退 HuggingFace 直链（②），不许按 tag 拼造一个不存在的下载直链去空跑。
RELEASE_API = "https://api.github.com/repos/Gi-Tuu/AMBRACE/releases/latest"
HF_BASE = "https://huggingface.co/Xenova/bge-m3/resolve/main"
# 2026-08-24（新机部署报告 P1-5）：国内网络 HuggingFace 常不可达，增加 hf-mirror.com 镜像
HF_MIRROR_BASE = "https://hf-mirror.com/Xenova/bge-m3/resolve/main"

UA = "AMBRACE-download-models/1.0"

# 只接受这两种发布包布局；不任意剥掉第一级目录。
_MODEL_ROOTS = (
    "backend/models/bge-m3/",
    "ai_companion_public/backend/models/bge-m3/",
)
_REQUIRED = (TOKENIZER, ONNX.replace("\\", "/"))
_TMP_COUNTER = itertools.count(1)

# R4：Windows 文件名别名/危险组件（模型合法布局不需要这些形态，遇到即拒绝）。
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_WINDOWS_BAD_CHARS = frozenset('<>:"|?*')


def model_complete() -> bool:
    """两个必需文件都存在且非空才算完整；仅“文件存在”不能算成功。"""
    for rel in _REQUIRED:
        path = MODEL_DIR / rel
        if not path.is_file() or path.stat().st_size <= 0:
            return False
    return True


def log(msg: str) -> None:
    print(f"[download_models] {msg}")


def _latest_release_zip_url() -> str:
    """从 GitHub latest release 解析 ai_companion_public.zip 附件直链；附件不存在则返回空串。

    反证：2026-09-07 起各 Release 只挂 APK，而这里曾按 tag 拼造标准直链，于是方式①每次
    都拿着一个必然 404 的 URL 去下载 542MB，白等一轮超时才回退②（latest 无 zip 已成惯例）。
    """
    try:
        req = urllib.request.Request(RELEASE_API, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        for a in data.get("assets", []):
            if (a.get("name") or "").lower() == "ai_companion_public.zip":
                return a.get("browser_download_url") or ""
        return ""
    except Exception:  # noqa: BLE001 - latest release 查询失败必须回退 HF，不能中断下载
        return ""


def _download(url: str, dest: Path) -> None:
    """流式下载 url 到 dest（stdlib urllib，处理 GitHub/HF 重定向与超时）。

    有 Content-Length 时校验实际字节数；空响应视为失败。失败时 dest 可能是部分文件，
    调用方必须只把 dest 放在本次专属临时区，不能直接写最终模型目录。
    """
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
    if total and done != total:
        raise OSError(f"incomplete download: {done}/{total} bytes")
    if done <= 0:
        raise OSError("empty download")


def _is_reparse(path: Path) -> bool:
    """Windows junction/symlink 或 POSIX symlink 都视为重定向；lstat 错误向上抛（fail-closed）。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attrs & flag) or os.path.islink(path)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child_s = os.path.normcase(os.path.abspath(str(child)))
        parent_s = os.path.normcase(os.path.abspath(str(parent)))
        return os.path.commonpath([child_s, parent_s]) == parent_s
    except ValueError:
        return False


def _assert_safe_existing_ancestors(path: Path) -> None:
    """R3：ROOT→path 的每级已存在组件必须是真实目录，且 realpath 仍在 ROOT 内。

    ROOT 是可信锚点（脚本路径 resolve 后得到）；ROOT 自身若为 reparse 则失败关闭。
    不存在的组件只允许在已通过检查的父级下由 mkdir 创建；调用方在创建后需再检一次。
    """
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        raise ValueError(f"path outside project root: {path}")
    if not ROOT.is_dir():
        raise ValueError(f"project root is not a directory: {ROOT}")
    if _is_reparse(ROOT):
        raise ValueError(f"refusing reparse point at project root: {ROOT}")
    root_real = Path(os.path.realpath(ROOT)).resolve()
    cur = ROOT
    for part in rel.parts:
        cur = cur / part
        if not os.path.lexists(cur):
            break
        if _is_reparse(cur):
            raise ValueError(f"refusing reparse point in model path ancestors: {cur}")
        real = Path(os.path.realpath(cur)).resolve()
        if not _is_within(real, root_real):
            raise ValueError(f"model path escapes project root via link: {cur} -> {real}")
    if os.path.lexists(path):
        real = Path(os.path.realpath(path)).resolve()
        if not _is_within(real, root_real):
            raise ValueError(f"model path escapes project root via link: {path} -> {real}")


def _model_parent() -> Path:
    """模型目录的父目录（backend/models）；mkdir 之前先校验 ROOT→父目录的真实路径。"""
    parent = MODEL_DIR.parent
    _assert_safe_existing_ancestors(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_existing_ancestors(parent)
    return parent


def _assert_safe_model_path(path: Path) -> None:
    """R3：目标必须在 MODEL_DIR 内；ROOT→目标的祖先与既有文件均不得 reparse/越界。"""
    try:
        path.relative_to(MODEL_DIR)
    except ValueError:
        raise ValueError(f"model install path outside model root: {path}")
    _assert_safe_existing_ancestors(path)


def _validate_windows_component(part: str, member: str) -> None:
    """R4：拒绝 Windows 会别名/截断的组件（尾点/尾空格/设备名/冒号/非法字符）。"""
    if not part or part in (".", ".."):
        raise ValueError(f"illegal member component: {member!r}")
    if part != part.rstrip(" ."):
        raise ValueError(f"trailing dot/space in member component: {member!r}")
    if any(ch in _WINDOWS_BAD_CHARS for ch in part) or any(ord(ch) < 32 for ch in part):
        raise ValueError(f"invalid character in member component: {member!r}")
    if part.split(".", 1)[0].rstrip(" .").upper() in _WINDOWS_RESERVED:
        raise ValueError(f"reserved device name component: {member!r}")


def _normalized_rel(name: str) -> str:
    """把 ZIP 成员名规范成相对模型根的 POSIX 路径；非法成员抛 ValueError。"""
    norm = name.replace("\\", "/")
    if "\x00" in norm:
        raise ValueError(f"member name contains NUL: {name!r}")
    for root in _MODEL_ROOTS:
        if norm.startswith(root):
            rel = norm[len(root):]
            break
    else:
        raise ValueError(f"member outside legal roots: {name!r}")
    if not rel:
        return ""
    if rel.startswith("/") or ntpath.splitdrive(rel)[0]:
        raise ValueError(f"illegal absolute member: {name!r}")
    parts = PurePosixPath(rel).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"illegal traversal member: {name!r}")
    if any(part in ("", ".") for part in parts):
        raise ValueError(f"illegal member component: {name!r}")
    for part in parts:
        _validate_windows_component(part, name)
    return PurePosixPath(*parts).as_posix()


def _plan_zip_model(zf: zipfile.ZipFile) -> dict | None:
    """预检 ZIP：只选一个合法根，校验成员类型/路径/重复/冲突/必需文件。

    返回 {"roots": ..., "files": [(ZipInfo, rel), ...]}；没有模型目录返回 None；
    任一非法成员抛 ValueError（此时尚未写任何文件）。
    """
    by_root: dict[str, list[tuple[zipfile.ZipInfo, str, bool]]] = {r: [] for r in _MODEL_ROOTS}
    for info in zf.infolist():
        norm = info.filename.replace("\\", "/")
        root = next((r for r in _MODEL_ROOTS if norm.startswith(r)), None)
        if root is None:
            # 发布包里其它项目文件不搬入模型目录。
            continue
        rel = _normalized_rel(info.filename)
        if not rel:
            continue
        mode = info.external_attr >> 16
        ftype = stat.S_IFMT(mode)
        if ftype == stat.S_IFLNK:
            raise ValueError(f"symlink member rejected: {info.filename!r}")
        if ftype not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"special file member rejected: {info.filename!r}")
        is_dir = info.is_dir() or norm.endswith("/")
        by_root[root].append((info, rel, is_dir))

    roots_with_files = [r for r, items in by_root.items() if any(not is_dir for _, _, is_dir in items)]
    if not roots_with_files:
        return None
    if len(roots_with_files) > 1:
        raise ValueError("both legal model roots are present; refusing ambiguous package")
    root = roots_with_files[0]

    files: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    dirs: set[str] = set()
    for info, rel, is_dir in by_root[root]:
        key = rel.casefold()
        if is_dir:
            if key in files:
                raise ValueError(f"file/directory conflict: {rel!r}")
            dirs.add(key)
            continue
        if key in files:
            raise ValueError(f"duplicate member target: {rel!r}")
        if key in dirs:
            raise ValueError(f"file/directory conflict: {rel!r}")
        files[key] = (info, rel)

    for key in files:
        parts = key.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            if parent in files:
                raise ValueError(f"file used as directory: {parent!r}")

    file_map = {rel: info for info, rel in files.values()}
    for rel in _REQUIRED:
        info = file_map.get(rel)
        if info is None:
            raise ValueError(f"required model file missing: {rel}")
        if info.file_size <= 0:
            raise ValueError(f"required model file empty: {rel}")
    return {"root": root, "files": [(info, rel) for info, rel in files.values()]}


def _copy_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path) -> None:
    """流式完整复制单个成员；zipfile 在读到 EOF 时校验 CRC，短读也显式失败。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with zf.open(info) as src, open(target, "wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)
            written += len(chunk)
    if written != info.file_size:
        raise OSError(f"short member read: {info.filename!r} ({written}/{info.file_size})")


def _validate_staged_model(staging: Path) -> None:
    """发布前校验：两个必需文件存在且非空。"""
    for rel in _REQUIRED:
        path = staging / rel
        if not path.is_file() or path.stat().st_size <= 0:
            raise ValueError(f"model file missing or empty: {rel}")


def _ensure_dir(path: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    cur = path
    while not cur.exists():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        d.mkdir()
        created.append(d)
    if not path.is_dir():
        raise ValueError(f"target parent is not a directory: {path}")


def _copy_file(src: Path, dst: Path) -> None:
    """先写同目录临时文件再 os.replace，避免失败时原地截断旧文件。"""
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{next(_TMP_COUNTER)}")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _install_staged_model(staging: Path) -> None:
    """把 staging 下的完整文件集合逐文件发布到 MODEL_DIR；失败按清单回滚。

    不是集合级原子：每个文件用临时文件 + os.replace；旧文件先移到本次专属 backup 区。
    回滚失败时保留 backup 区并打印路径，绝不把唯一旧副本删掉。
    """
    _validate_staged_model(staging)
    _assert_safe_model_path(MODEL_DIR)
    parent = _model_parent()
    backup_root = Path(tempfile.mkdtemp(prefix=".bge-m3-backup-", dir=str(parent)))
    installed: list[Path] = []
    backed_up: list[tuple[Path, Path]] = []
    created_dirs: list[Path] = []
    rollback_ok = True
    try:
        for src in sorted(p for p in staging.rglob("*") if p.is_file()):
            rel = src.relative_to(staging)
            dst = MODEL_DIR / rel
            _assert_safe_model_path(dst.parent)
            _ensure_dir(dst.parent, created_dirs)
            _assert_safe_model_path(dst)
            if dst.exists():
                if dst.is_dir():
                    raise ValueError(f"target is a directory, refusing to replace: {dst}")
                backup = backup_root / rel
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(dst, backup)
                backed_up.append((backup, dst))
            _copy_file(src, dst)
            installed.append(dst)
    except Exception:
        for dst in reversed(installed):
            try:
                dst.unlink()
            except FileNotFoundError:
                pass
            except Exception as e:  # noqa: BLE001 - 回滚必须继续尝试其余文件并保留副本
                rollback_ok = False
                log(f"[WARN] 回滚删除失败: {dst}: {e}")
        for backup, dst in reversed(backed_up):
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, dst)
            except Exception as e:  # noqa: BLE001 - 回滚逐项尝试，失败则保留 backup 区
                rollback_ok = False
                log(f"[WARN] 回滚恢复失败: {backup} -> {dst}: {e}")
        for d in reversed(created_dirs):
            try:
                d.rmdir()
            except OSError:
                pass
        if not rollback_ok:
            log(f"[ERROR] 回滚未完成；旧文件保留在: {backup_root}")
        raise
    finally:
        if rollback_ok:
            shutil.rmtree(backup_root, ignore_errors=True)


def _extract_zip_models(zip_path: Path) -> bool:
    """从发布包 zip 中抽取模型到同卷临时区，校验后安装。返回是否成功安装完整模型。"""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            plan = _plan_zip_model(zf)
            if plan is None:
                log("  [失败] 发布包中未找到合法的 backend/models/bge-m3 目录。")
                return False
            staging_root = Path(tempfile.mkdtemp(prefix=".bge-m3-extract-", dir=str(_model_parent())))
            try:
                staging = staging_root / "model"
                staging.mkdir()
                for info, rel in plan["files"]:
                    _copy_zip_member(zf, info, staging / PurePosixPath(rel))
                _validate_staged_model(staging)
                _install_staged_model(staging)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)
        return model_complete()
    except Exception as e:  # noqa: BLE001 - 损坏/越界/短读统一转为失败并回退下一来源
        log(f"  [失败] 模型包解压/校验失败：{e}")
        return False


def _download_from_release() -> bool:
    url = _latest_release_zip_url()
    if not url:
        log("方式① 跳过：latest release 未附完整包（每个版本只有首次发布挂 zip，属预期）。")
        return False
    log("方式① 从 GitHub latest release 下载发布包...")
    log(f"  URL: {url}")
    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "ai_companion_public.zip"
        try:
            _download(url, zip_path)
        except Exception as e:  # noqa: BLE001 - 网络失败必须回退 HF
            log(f"  [失败] 下载发布包失败：{e}")
            return False
        log(f"  已下载 {zip_path.stat().st_size // (1024 * 1024)}MB，开始解压模型目录...")
        ok = _extract_zip_models(zip_path)
        if not ok:
            log("  [失败] 发布包模型不完整或无效，改用 HuggingFace。")
            return False
        log("  解压并安装完成。")
        return True


def _download_from_huggingface() -> bool:
    log("方式② 从 HuggingFace Xenova/bge-m3 下载 tokenizer.json + onnx/model_int8.onnx...")
    bases = [HF_BASE, HF_MIRROR_BASE]  # 官方直连失败后自动切 hf-mirror.com（国内镜像）
    files = ((TOKENIZER, "tokenizer.json"), (ONNX, ONNX.replace("\\", "/")))
    staging_root = None
    try:
        staging_root = Path(tempfile.mkdtemp(prefix=".bge-m3-hf-", dir=str(_model_parent())))
        staging = staging_root / "model"
        staging.mkdir()
        for rel, path in files:
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            ok = False
            for base in bases:
                url = base + "/" + path
                log(f"  下载 {rel} （{base}）...")
                try:
                    _download(url, target)
                    if target.stat().st_size <= 0:
                        raise OSError("empty file")
                    log(f"  已保存 {target.relative_to(staging)}（{target.stat().st_size // (1024 * 1024)}MB）")
                    ok = True
                    break
                except Exception as e:  # noqa: BLE001 - 官方失败继续试镜像
                    log(f"  [失败] {base} 下载失败：{e}")
            if not ok:
                log(f"  [失败] 下载 {rel} 失败（官方与镜像均不可达）。")
                log("  国内网络可尝试：set HF_ENDPOINT=https://hf-mirror.com 后重跑，或手动下载放置到 backend/models/bge-m3/")
                return False
        _validate_staged_model(staging)
        _install_staged_model(staging)
        return True
    except Exception as e:  # noqa: BLE001 - 下载/校验/安装失败统一回退并保留旧模型
        log(f"  [失败] HuggingFace 模型校验/安装失败：{e}")
        return False
    finally:
        if staging_root is not None:
            shutil.rmtree(staging_root, ignore_errors=True)


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

    _model_parent()

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
