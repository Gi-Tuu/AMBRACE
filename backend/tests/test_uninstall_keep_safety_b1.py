# -*- coding: utf-8 -*-
"""安全 B1-A：卸载保留保障（SEC-P0-3）的真实 Windows 端到端测试。

只在 pytest 的 tmp_path 内建最小假项目并运行 uninstall.bat 的副本：
- server_manager / netstat 用 PATH 上的批处理桩，绝不停止 8000 或触碰真实服务；
- move 失败用「源目录内文件持有 CreateFileW 无共享删除句柄」真实注入（先证明源仍在）；
- purge 的延迟自删在假项目内等待结束，不启动可见窗口。

测试先于修复编写：旧脚本会因忽略 move errorlevel / 删 backups / 清空保留目录而红。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# R2：必须在任何 Windows API 初始化之前做模块级跳过；pytestmark 无法阻止
# 模块导入期的 ctypes.WinDLL 访问（Linux 上会先报 AttributeError）。
if sys.platform != "win32":
    pytest.skip("uninstall.bat 的真实端到端测试需要 Windows cmd.exe", allow_module_level=True)

import ctypes
from ctypes import wintypes

REPO_ROOT = Path(__file__).resolve().parents[2]
UNINSTALL_BAT = REPO_ROOT / "scripts" / "uninstall.bat"

GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
_CreateFileW.restype = wintypes.HANDLE
_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wintypes.HANDLE]
_CloseHandle.restype = wintypes.BOOL


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _under_tmp(tmp_path: Path, path: Path) -> Path:
    """测试自身的硬约束：任何被操作的路径都必须解析在 tmp_path 内。"""
    root = tmp_path.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise AssertionError(f"test path escaped tmp_path: {resolved}")
    return resolved


class _NoShareHandle:
    """持有源文件的删除共享禁止句柄；真实 cmd move 会因此返回非 0。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = _CreateFileW(
            str(path), GENERIC_READ, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
        )
        if self.handle == INVALID_HANDLE:
            raise OSError(f"CreateFileW failed: {ctypes.get_last_error()}")

    def close(self) -> None:
        if self.handle not in (None, INVALID_HANDLE):
            _CloseHandle(self.handle)
            self.handle = None


def _make_project(tmp_path: Path) -> dict:
    """建最小假项目；fakebin/tempdir 放在项目外，避免被卸载器删除。"""
    project = _under_tmp(tmp_path, tmp_path / "proj")
    _under_tmp(tmp_path, project / "scripts").mkdir(parents=True)
    _under_tmp(tmp_path, project / "backend" / "app").mkdir(parents=True)
    _under_tmp(tmp_path, project / "backend" / "data").mkdir(parents=True)
    _under_tmp(tmp_path, project / "backend" / "models" / "bge-m3").mkdir(parents=True)
    _under_tmp(tmp_path, project / "backups").mkdir(parents=True)
    _under_tmp(tmp_path, project / "plugins").mkdir(parents=True)

    shutil.copyfile(UNINSTALL_BAT, project / "scripts" / "uninstall.bat")
    # 用真实 python.exe 执行本桩（batch 版 python 桩被无 call 调用时会转走控制权，
    # 会把「停服后继续删」的旧缺陷伪装成中止，故必须用 exe + 项目内脚本）。
    (project / "scripts" / "server_manager.py").write_text(
        "import os, sys\n"
        "with open(os.environ['STUB_LOG'], 'a', encoding='utf-8') as f:\n"
        "    f.write(' '.join(sys.argv[1:]) + '\\n')\n"
        "raise SystemExit(int(os.environ.get('STUB_RC', '0')))\n",
        encoding="utf-8",
    )
    (project / "VERSION").write_text("9.9.9-test\n", encoding="utf-8")
    (project / "backend" / "app" / "main.py").write_text("# fake main\n", encoding="utf-8")

    env_bytes = b"AMBRACE_TEST_ENV=1\n"
    data_bytes = b"sqlite-fake-db-bytes\n"
    model_bytes = b"onnx-fake-model-bytes\n"
    backup_bytes = b"backup-must-survive\n"
    plugin_bytes = b"plugin-program-file\n"
    root_bytes = b"root-program-file\n"
    (project / ".env").write_bytes(env_bytes)
    (project / "backend" / "data" / "db.sqlite").write_bytes(data_bytes)
    (project / "backend" / "models" / "bge-m3" / "model.onnx").write_bytes(model_bytes)
    (project / "backups" / "keep.txt").write_bytes(backup_bytes)
    (project / "plugins" / "junk.txt").write_bytes(plugin_bytes)
    (project / "rootfile.txt").write_bytes(root_bytes)

    fakebin = _under_tmp(tmp_path, tmp_path / "fakebin")
    fakebin.mkdir()
    # 只桩 netstat（在 for /f 子 cmd 中执行，不会转走父脚本控制权）；
    # python 必须解析到真实 exe，才能让 scripts\server_manager.py 桩返回可控退出码。
    (fakebin / "netstat.bat").write_text(
        "@echo off\r\n"
        'if "%STUB_PORT_BUSY%"=="1" echo   TCP    0.0.0.0:8000    0.0.0.0:0    LISTENING    4242\r\n'
        "exit /b 0\r\n",
        encoding="ascii", newline="",
    )
    tempdir = _under_tmp(tmp_path, tmp_path / "tempdir")
    tempdir.mkdir()

    return {
        "tmp_path": tmp_path,
        "project": project,
        "fakebin": fakebin,
        "tempdir": tempdir,
        "stop_log": _under_tmp(tmp_path, tmp_path / "stop.log"),
        "env_bytes": env_bytes,
        "data_bytes": data_bytes,
        "model_bytes": model_bytes,
        "backup_bytes": backup_bytes,
        "plugin_bytes": plugin_bytes,
        "root_bytes": root_bytes,
    }


def _run(project: dict, *, bat: str = "scripts\\uninstall.bat", extra=(), stub_rc="0",
         port_busy="0", temp_override: Path | None = None) -> tuple[int, str, str]:
    env = os.environ.copy()
    system_root = env.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join([
        str(Path(sys.executable).resolve().parent),
        str(project["fakebin"]),
        str(Path(system_root) / "System32"),
        system_root,
    ])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["TEMP"] = str(temp_override if temp_override is not None else project["tempdir"])
    env["TMP"] = env["TEMP"]
    env["STUB_RC"] = str(stub_rc)
    env["STUB_LOG"] = str(project["stop_log"])
    env["STUB_PORT_BUSY"] = str(port_busy)
    cmd = ["cmd.exe", "/d", "/c", bat, *extra]
    proc = subprocess.run(
        cmd, cwd=str(project["project"]), env=env, capture_output=True, timeout=90,
    )
    out = (proc.stdout or b"").decode("gbk", errors="replace")
    err = (proc.stderr or b"").decode("gbk", errors="replace")
    return proc.returncode, out, err


def _known_keep_dirs(project: dict) -> list[Path]:
    return sorted(project["tempdir"].glob("aic_uninstall_keep_*"))


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    target.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
    )
    return proc.returncode == 0 and link.exists()


def _assert_keep_hashes(project: dict) -> None:
    p = project["project"]
    assert _sha((p / ".env").read_bytes()) == _sha(project["env_bytes"])
    assert _sha((p / "backend" / "data" / "db.sqlite").read_bytes()) == _sha(project["data_bytes"])
    assert _sha((p / "backend" / "models" / "bge-m3" / "model.onnx").read_bytes()) == _sha(project["model_bytes"])


def test_keep_mode_normal_keeps_hashes_and_backups(tmp_path):
    project = _make_project(tmp_path)
    rc, out, err = _run(project)

    assert rc == 0, f"stdout={out}\nstderr={err}"
    _assert_keep_hashes(project)
    p = project["project"]
    assert (p / "backups" / "keep.txt").read_bytes() == project["backup_bytes"], "默认保留模式必须原样保留 backups"
    assert (p / "scripts" / "uninstall.bat").is_file(), "卸载器自身 scripts 目录保留"
    assert not (p / "plugins").exists(), "程序文件应被删除"
    assert not (p / "rootfile.txt").exists(), "根目录程序文件应被删除"
    assert not (p / "backend" / "app" / "main.py").exists(), "程序代码应被删除"
    assert _known_keep_dirs(project) == [], "恢复成功后本次临时保留目录应清理"


def test_temp_outside_project_positive_control(tmp_path):
    """项目外正常 TEMP 正对照：保留/恢复主路径不得被 R1 边界检查误伤。"""
    project = _make_project(tmp_path)
    rc, out, err = _run(project)

    assert rc == 0, f"stdout={out}\nstderr={err}"
    _assert_keep_hashes(project)
    assert (project["project"] / "backups" / "keep.txt").read_bytes() == project["backup_bytes"]
    assert _known_keep_dirs(project) == []


def test_temp_inside_project_aborts_without_moving_sources(tmp_path):
    """R1：TEMP 在项目内时，KEEP_DIR 会落入待删树，必须在任何 move 前中止。"""
    project = _make_project(tmp_path)
    inside_tmp = _under_tmp(tmp_path, project["project"] / "local_temp")
    inside_tmp.mkdir()

    rc, out, err = _run(project, temp_override=inside_tmp)

    assert rc != 0, f"stdout={out}\nstderr={err}"
    _assert_keep_hashes(project)
    p = project["project"]
    assert (p / "backups" / "keep.txt").read_bytes() == project["backup_bytes"]
    assert (p / "plugins" / "junk.txt").read_bytes() == project["plugin_bytes"]
    assert (p / "rootfile.txt").read_bytes() == project["root_bytes"]
    for keep in inside_tmp.glob("aic_uninstall_keep_*"):
        assert list(keep.iterdir()) == [], "边界检查拒绝时不得把源文件移入项目内 KEEP_DIR"
    text = (out + err).lower()
    assert "keep directory" in text or "inside" in text or "aliases" in text


def test_temp_junction_into_project_aborts_without_moving_sources(tmp_path):
    """R1：TEMP 经 junction 指回项目时，字符串前缀看不出，必须用真实路径拒绝。"""
    project = _make_project(tmp_path)
    target = _under_tmp(tmp_path, project["project"] / "inside_target")
    target.mkdir()
    (target / "sentinel.txt").write_bytes(b"junction-sentinel")
    probe = target / "probe.txt"
    probe.write_bytes(b"probe")
    link = _under_tmp(tmp_path, tmp_path / "temp_junction")
    if not _make_junction(link, target):
        pytest.skip("Windows junction unavailable on this filesystem")

    # 先证明 realpath 确实能把别名解析回项目内，再运行卸载器；否则不执行破坏性用例。
    assert Path(os.path.realpath(link / "probe.txt")).resolve() == probe.resolve()

    rc, out, err = _run(project, temp_override=link)

    assert rc != 0, f"stdout={out}\nstderr={err}"
    _assert_keep_hashes(project)
    p = project["project"]
    assert (p / "backups" / "keep.txt").read_bytes() == project["backup_bytes"]
    assert (p / "plugins" / "junk.txt").read_bytes() == project["plugin_bytes"]
    assert (target / "sentinel.txt").read_bytes() == b"junction-sentinel"


def test_stop_failure_aborts_before_any_file_change(tmp_path):
    project = _make_project(tmp_path)
    rc, out, err = _run(project, stub_rc="1")

    assert rc != 0, "停服失败必须非 0 退出"
    assert project["stop_log"].is_file(), "应实际调用 server_manager stop 桩"
    assert "stop" in project["stop_log"].read_text(encoding="utf-8")
    _assert_keep_hashes(project)
    p = project["project"]
    assert (p / "backups" / "keep.txt").is_file()
    assert (p / "plugins" / "junk.txt").is_file(), "停服失败不得进入删除阶段"
    assert _known_keep_dirs(project) == [], "停服失败不应创建保留目录"


def test_port_still_listening_aborts_even_if_stop_exit_zero(tmp_path):
    project = _make_project(tmp_path)
    rc, out, err = _run(project, stub_rc="0", port_busy="1")

    assert rc != 0, "stop 返回 0 但 8000 仍监听时必须中止"
    _assert_keep_hashes(project)
    assert (project["project"] / "plugins" / "junk.txt").is_file(), "不得进入删除阶段"


def test_keep_dir_creation_failure_aborts(tmp_path):
    project = _make_project(tmp_path)
    blocked = _under_tmp(tmp_path, tmp_path / "blocked-temp")
    blocked.write_text("not a directory", encoding="utf-8")
    rc, out, err = _run(project, temp_override=blocked)

    assert rc != 0, "KEEP_DIR 创建失败必须中止"
    _assert_keep_hashes(project)
    assert (project["project"] / "plugins" / "junk.txt").is_file(), "不得进入删除阶段"


def test_first_move_failure_leaves_everything(tmp_path):
    project = _make_project(tmp_path)
    handle = _NoShareHandle(project["project"] / ".env")
    try:
        rc, out, err = _run(project)
    finally:
        handle.close()

    assert rc != 0, f"第一步 move 失败必须非 0；stdout={out}\nstderr={err}"
    assert (project["project"] / ".env").read_bytes() == project["env_bytes"], "失败前置：源必须仍在"
    assert (project["project"] / "backend" / "data" / "db.sqlite").read_bytes() == project["data_bytes"]
    assert (project["project"] / "backend" / "models" / "bge-m3" / "model.onnx").read_bytes() == project["model_bytes"]
    assert (project["project"] / "plugins" / "junk.txt").is_file(), "move 失败不得进入删除阶段"
    keeps = _known_keep_dirs(project)
    assert len(keeps) <= 1
    if keeps:
        assert list(keeps[0].iterdir()) == [], "第一步失败时保留目录应为空"


def test_partial_move_failure_keeps_sources_and_prints_keep_dir(tmp_path):
    project = _make_project(tmp_path)
    handle = _NoShareHandle(project["project"] / "backend" / "data" / "db.sqlite")
    try:
        rc, out, err = _run(project)
    finally:
        handle.close()

    assert rc != 0, f"部分移动后失败必须非 0；stdout={out}\nstderr={err}"
    p = project["project"]
    assert (p / "backend" / "data" / "db.sqlite").read_bytes() == project["data_bytes"], "失败前置：data 源仍在"
    assert (p / "backend" / "models" / "bge-m3" / "model.onnx").read_bytes() == project["model_bytes"]
    assert (p / "plugins" / "junk.txt").is_file(), "不得进入删除阶段"
    keeps = _known_keep_dirs(project)
    assert len(keeps) == 1, "已移动的 .env 必须留在明确命名的 KEEP_DIR"
    assert (keeps[0] / ".env").read_bytes() == project["env_bytes"]
    assert str(keeps[0]).lower() in (out + err).lower(), "失败输出必须打印真实 KEEP_DIR 绝对路径"


def test_restore_failure_keeps_unique_copy_and_real_path(tmp_path):
    project = _make_project(tmp_path)
    bat = project["project"] / "scripts" / "uninstall.bat"
    text = bat.read_text(encoding="utf-8")
    anchor = 'move "%KEEP_DIR%\\data" "backend\\data"'
    assert anchor in text, "测试锚点缺失：恢复 data 的 move 行"
    bat.write_text(text.replace(anchor, "cmd /c exit /b 1", 1), encoding="utf-8", newline="\n")

    rc, out, err = _run(project)

    assert rc != 0, "恢复失败必须非 0"
    keeps = _known_keep_dirs(project)
    assert len(keeps) == 1, "恢复失败不得删除 KEEP_DIR"
    keep = keeps[0]
    assert (keep / "data" / "db.sqlite").read_bytes() == project["data_bytes"], "未恢复的 data 必须完整留在 KEEP_DIR"
    assert (keep / "models" / "bge-m3" / "model.onnx").read_bytes() == project["model_bytes"]
    assert (project["project"] / ".env").read_bytes() == project["env_bytes"], "已恢复的 .env 应在项目根"
    assert not (project["project"] / "backend" / "data").exists()
    assert str(keep).lower() in (out + err).lower(), "失败输出必须打印真实恢复路径"


def test_purge_removes_backups_and_self_deletes_scripts(tmp_path):
    project = _make_project(tmp_path)
    rc, out, err = _run(project, extra=("--purge",))

    assert rc == 0, f"purge 正常退出；stdout={out}\nstderr={err}"
    p = project["project"]
    for name in (".env", "backend", "backups", "plugins", "VERSION", "rootfile.txt"):
        assert not (p / name).exists(), f"purge 必须删除 {name}"

    deadline = time.time() + 15.0
    while time.time() < deadline and (p / "scripts").exists():
        time.sleep(0.2)
    assert not (p / "scripts").exists(), "purge 的延迟自删应完成（等待 15s 后 scripts 仍存在）"
    assert _known_keep_dirs(project) == [], "purge 不需要 KEEP_DIR"


@pytest.mark.parametrize("missing", ["VERSION", "backend/app/main.py"])
def test_invalid_root_aborts_before_stop(tmp_path, missing):
    project = _make_project(tmp_path)
    target = project["project"] / missing
    target.unlink()

    rc, out, err = _run(project)

    assert rc != 0, f"缺 {missing} 必须拒绝"
    assert not project["stop_log"].exists(), "根校验必须先于任何真实停服调用"
    _assert_keep_hashes(project)
    assert (project["project"] / "plugins" / "junk.txt").is_file(), "不得进入删除阶段"


def test_wrong_script_location_aborts_before_stop(tmp_path):
    project = _make_project(tmp_path)
    wrong_dir = _under_tmp(tmp_path, project["project"] / "sub")
    wrong_dir.mkdir()
    shutil.copyfile(UNINSTALL_BAT, wrong_dir / "uninstall.bat")

    rc, out, err = _run(project, bat="sub\\uninstall.bat")

    assert rc != 0, "脚本不在 <root>\\scripts\\ 时必须拒绝"
    assert not project["stop_log"].exists(), "误放子目录不得先停真实服务"
    _assert_keep_hashes(project)
    assert (project["project"] / "plugins" / "junk.txt").is_file(), "不得进入删除阶段"
