# -*- coding: utf-8 -*-
"""candidate_preflight.py 的行为合同（REWORK1 安全模型：授权根/O_EXCL/编辑授权/严格图）。

全部合成路径（tmp_path），不探测生产路径。主审 7 反例的同等独立负例在本文件
（primary_review/test_primary_preflight_review.py 只读），正例同样在案。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "candidate_preflight.py"
WORKSPACE = SCRIPT.parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("preflight_mod_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False, cwd=str(WORKSPACE),
        env={**os.environ, "INSTANCE_LOCK_PORT": "0"},
    )


# ─────────────── 快检（无写动作） ───────────────

def test_quick_check_passes():
    r = _run()
    assert r.returncode == 0, r.stdout + r.stderr
    for marker in ("migration_chain", "alembic_cli_crosscheck", "package_versions"):
        assert marker in r.stdout


def test_quick_check_writes_nothing(tmp_path):
    """默认快检不写任何库/目录/文件（tmp 快照前后零变化）。"""
    before = sorted(p.name for p in tmp_path.iterdir())
    _run()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_json_without_root_rejected():
    """--json 不带 --root ⇒ 拒绝（报告只写受控根内），且不产生文件。"""
    r = _run("--json", str(Path(__file__).parent / "should_not_exist_report.json"))
    assert r.returncode == 3
    assert not (Path(__file__).parent / "should_not_exist_report.json").exists()


def test_json_report_machine_readable(tmp_path):
    """受控根内 --json：可解析、结构完整（根/报告均由本工具受控新建）。"""
    root = tmp_path / "pf_root"
    out = root / "report.json"
    assert not root.exists(), "受控根必须由工具创建（测试不预置）"
    r = _run("--json", str(out), "--root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert root.is_dir() and out.is_file(), "报告应在新受控根内"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {"checks", "passed", "failed", "workspace"} <= set(data)
    names = {c["name"] for c in data["checks"]}
    assert {"git_head", "git_worktree", "migration_chain", "parent_chain",
            "alembic_cli_crosscheck", "required_files", "package_versions",
            "path_dependency_scope", "verification_entries"} <= names


def test_json_cannot_overwrite_existing_data(tmp_path):
    """--json 目标已存在（.db/源码/JSON）⇒ 拒绝且原字节不变（O_EXCL；主审反例 7 同型）。"""
    root = tmp_path / "pf_root2"
    root.mkdir()  # 预置已存在根 ⇒ gate 拒绝，原文件必然不动
    victim = root / "synthetic_existing.db"
    original = b"synthetic opaque bytes"
    victim.write_bytes(original)
    r = _run("--json", str(victim), "--root", str(root))
    assert victim.read_bytes() == original, "已有数据被报告输出覆盖"
    assert r.returncode == 3


def test_json_rejects_target_outside_root(tmp_path):
    """--json 在受控根外 ⇒ 拒绝。"""
    outside = tmp_path / "outside.json"
    root = tmp_path / "pf_root3"
    r = _run("--json", str(outside), "--root", str(root))
    assert r.returncode == 3
    assert not outside.exists()


# ─────────────── 演练安全门（rehearsal_gate 单参契约 + 授权根） ───────────────

def test_gate_rejects_existing_empty_dir(tmp_path):
    """已存在空目录 ⇒ 拒绝（本轮新建独占；主审反例 1 同型）。"""
    mod = _load()
    d = tmp_path / "already_exists"
    d.mkdir()
    ok, msg = mod.rehearsal_gate(d)
    assert not ok and "已存在" in msg


def test_gate_rejects_parent_symlink_alias(tmp_path, monkeypatch):
    """父级 symlink 别名穿透 ⇒ 拒绝（主审反例 2 同型）。"""
    mod = _load()
    protected = tmp_path / "protected"
    protected.mkdir()
    alias = tmp_path / "alias"
    os_symlink = os.symlink
    os_symlink(protected, alias, target_is_directory=True)
    monkeypatch.setattr(mod, "FORBIDDEN_DIR_MARKERS", [str(protected)])
    ok, _msg = mod.rehearsal_gate(alias / "new_child")
    assert not ok


@pytest.mark.skipif(os.name != "nt", reason="Windows 大小写别名仅 Windows 有语义")
def test_gate_rejects_windows_case_alias(tmp_path, monkeypatch):
    """Windows 大小写别名 ⇒ 拒绝（主审反例 3 同型；resolve+normcase 真实身份判定）。"""
    mod = _load()
    protected = tmp_path / "ProtectedCase"
    protected.mkdir()
    monkeypatch.setattr(mod, "FORBIDDEN_DIR_MARKERS", [str(protected)])
    ok, _msg = mod.rehearsal_gate(protected.with_name("protectedcase") / "child")
    assert not ok


def test_gate_accepts_fresh_dir_and_creates(tmp_path):
    """正例：受控根内全新目录通过并创建（gate 不恒 False）。"""
    mod = _load()
    root = tmp_path / "ctl_root"
    target = root / "rehearsal"
    ok, _msg = mod.gate_under_controlled_root(target, root)
    assert ok and target.is_dir()


def test_gate_rejects_existing_controlled_root(tmp_path):
    """受控根本身已存在 ⇒ 拒绝（非本轮新建独占）。"""
    mod = _load()
    root = tmp_path / "ctl_root2"
    root.mkdir()
    ok, msg = mod.gate_under_controlled_root(root / "r", root)
    assert not ok and "已存在" in msg


def test_gate_rejects_escape_via_dotdot(tmp_path):
    """.. 逃逸受控根 ⇒ 拒绝。"""
    mod = _load()
    root = tmp_path / "ctl_root3"
    ok, _msg = mod.gate_under_controlled_root(root / ".." / "escaped", root)
    assert not ok


# ─────────────── check_git：编辑授权 ≠ 存在性 ───────────────

def test_git_edit_whitelist_excludes_business_files(tmp_path, monkeypatch):
    """REQUIRED_FILES 中的业务文件（chat_service.py）被改 ⇒ 必须红（主审反例 4 同型）。"""
    mod = _load()

    def run(args, **kw):
        text = ("d3a22b15952db5c668cf9dc04a6051c8acab1818\n"
                if "rev-parse" in args else
                " M backend/app/application/chat_service.py\n")
        return subprocess.CompletedProcess(args, 0, text, "")

    monkeypatch.setattr(mod.subprocess, "run", run)
    checks, flags = [], []
    mod.check_git(checks, flags)
    assert not flags[-1], "未授权业务文件修改被报安全"
    # 命中描述里要出现该路径（不是静默吞）
    assert any("chat_service.py" in d for (_n, _o, d) in checks)


def test_git_edit_whitelist_allows_authorized(tmp_path, monkeypatch):
    """正例：授权五文件内的修改 ⇒ 通过（并带内容 SHA）。"""
    mod = _load()

    def run(args, **kw):
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, "d3a22b15952db5c668cf9dc04a6051c8acab1818\n", "")
        if "status" in args:
            return subprocess.CompletedProcess(
                args, 0,
                " M README.md\0?? scripts/candidate_preflight.py\0?? logs/\0", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", run)
    checks, flags = [], []
    mod.check_git(checks, flags)
    assert flags[-1], "授权内修改应通过"
    assert any("SHA" in d or "sha" in d for (_n, _o, d) in checks), "应登记内容 SHA"


def test_git_status_nonzero_is_red(tmp_path, monkeypatch):
    """git status 非 0 ⇒ 红（不静默当干净）。"""
    mod = _load()

    def run(args, **kw):
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, "d3a22b15952db5c668cf9dc04a6051c8acab1818\n", "")
        if "status" in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: synthetic")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(mod.subprocess, "run", run)
    checks, flags = [], []
    mod.check_git(checks, flags)
    assert not flags[-1]


# ─────────────── path 依赖边界 ───────────────

def test_path_deps_resolved_and_inside(tmp_path, monkeypatch):
    """正例：相对 path 依赖在候选根内 ⇒ 过；越界/绝对外指 ⇒ 红（App/core/PWA 全查）。"""
    mod = _load()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "flutter_app").mkdir()
    (ws / "ambrace_core").mkdir()
    (ws / "pwa").mkdir()
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  ambrace_core:\n    path: ../ambrace_core\n",
        encoding="utf-8")
    (ws / "ambrace_core" / "pubspec.yaml").write_text("version: 0.1.0\n", encoding="utf-8")
    (ws / "pwa" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  core:\n    path: ../ambrace_core\n",
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks

    # 越界：../../outside_core
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  ambrace_core:\n    path: ../../outside_core\n",
        encoding="utf-8")
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "../ 越界被报内部"

    # 绝对外指
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  ambrace_core:\n    path: D:/some/other/place\n",
        encoding="utf-8")
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "绝对外指被报内部"


# ─────────────── 迁移图/CLI 严格交叉 ───────────────

def test_cli_nonzero_or_extra_head_is_red(monkeypatch):
    """CLI 非 0 且第二头 ⇒ 红（主审反例 6 同型）。"""
    mod = _load()
    monkeypatch.setattr(mod, "_parse_versions",
                        lambda: {mod.EXPECTED_HEAD: [mod.EXPECTED_OLD_HEAD],
                                 mod.EXPECTED_OLD_HEAD: []})

    def run(*a, **kw):
        return subprocess.CompletedProcess(
            a, 1, mod.EXPECTED_HEAD + " (head)\nrogue_head (head)\n", "synthetic error")

    monkeypatch.setattr(mod.subprocess, "run", run)
    checks, flags = [], []
    mod.check_migration_chain(checks, flags)
    assert not flags[-1], "CLI 非 0 + 额外头不得报通过"


def test_cli_exit_zero_but_extra_head_also_red(monkeypatch):
    """CLI exit 0 但解析出第二头 ⇒ 也红（不能只看首行）。"""
    mod = _load()
    monkeypatch.setattr(mod, "_parse_versions",
                        lambda: {mod.EXPECTED_HEAD: [mod.EXPECTED_OLD_HEAD],
                                 mod.EXPECTED_OLD_HEAD: []})

    def run(*a, **kw):
        return subprocess.CompletedProcess(
            a, 0, mod.EXPECTED_HEAD + " (head)\nrogue_head (head)\n", "")

    monkeypatch.setattr(mod.subprocess, "run", run)
    checks, flags = [], []
    mod.check_migration_chain(checks, flags)
    assert not flags[-1], "额外第二头（即便 exit 0）必须红"


def test_graph_rejects_cycle_duplicate_unknown_parent():
    """图校验：循环/未知父/多父拒绝；健康链通过。"""
    mod = _load()
    results, flags = [], []
    # 未知父
    assert mod._validate_graph({"a": ["ghost"]}, results, flags) is False
    # 多父
    results, flags = [], []
    assert mod._validate_graph({"a": ["b", "c"], "b": [], "c": []}, results, flags) is False
    # 循环
    results, flags = [], []
    assert mod._validate_graph({"a": ["b"], "b": ["a"]}, results, flags) is False
    # 健康链
    results, flags = [], []
    assert mod._validate_graph({"h": ["g"], "g": []}, results, flags) is True


def test_naming_migration_gate_not_rehearsal():
    """命名准确性：安全门只是目录安全；迁移演练是 pytest——文档/脚本描述须如实。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "真正的迁移演练由" in src or "迁移演练由" in src, "脚本应明示演练由 pytest 执行"
    assert "不执行迁移" in src or "本工具不执行迁移" in src


# ─────────────── REWORK2：受控根生命周期贯穿**真实 CLI** ───────────────

def test_cli_rejects_preexisting_root(tmp_path):
    """外部调用**预先存在**的根（含空目录）⇒ 拒绝，且根内不新增任何文件。

    REWORK2：走真实入口（子进程 CLI）——工具不得自行声明「本轮已创建」。
    """
    root = tmp_path / "pf_existing"
    root.mkdir()
    marker = root / "must_not_appear.json"
    r = _run("--json", str(marker), "--root", str(root))
    assert r.returncode == 3, r.stdout + r.stderr
    assert not marker.exists(), "预存根被复用并写出了报告"
    assert list(root.iterdir()) == [], "预存根内不应新增任何文件"


def test_cli_fresh_root_and_fresh_subdir_succeeds(tmp_path):
    """正例：真实 CLI 的**新根 + 新子目录** ⇒ 成功（受控根门不是恒 False）。"""
    root = tmp_path / "pf_fresh"
    drill = root / "drill"
    assert not root.exists(), "受控根必须由工具创建（测试不预置）"
    r = _run("--rehearsal-dir", str(drill), "--root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert root.is_dir() and drill.is_dir(), "受控根与演练子目录应由工具受控新建"
    assert "受控新建就绪" in r.stdout


def test_cli_fresh_root_with_json_succeeds(tmp_path):
    """正例：真实 CLI 的**新根 + JSON 报告** ⇒ 成功，报告落在根内。"""
    root = tmp_path / "pf_json_fresh"
    out = root / "report.json"
    r = _run("--json", str(out), "--root", str(root))
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["failed"] == [], data["failed"]


def test_cli_rejects_rehearsal_dir_outside_root(tmp_path):
    """演练子目录在受控根外 ⇒ 拒绝（真实 CLI 口径）。"""
    root = tmp_path / "pf_outside_root"
    outside = tmp_path / "outside_drill"
    r = _run("--rehearsal-dir", str(outside), "--root", str(root))
    assert r.returncode == 3, r.stdout + r.stderr
    assert not outside.exists()


def test_controlled_root_ownership_object_reuses_same_root(tmp_path):
    """本轮受控根经**进程内所有权对象**复用 ⇒ 不再重复触发「根必须不存在」门。"""
    mod = _load()
    root = tmp_path / "owned_root"
    root.mkdir()
    owned = mod._ControlledRoot(root, created=True)
    target = root / "child"
    ok, msg = mod.gate_under_controlled_root(target, root, ownership=owned)
    assert ok and target.is_dir(), msg


def test_controlled_root_ownership_mismatch_rejected(tmp_path):
    """所有权对象与目标根不一致 ⇒ 拒绝（不可跨根套用所有权）。"""
    mod = _load()
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    owned = mod._ControlledRoot(a, created=True)
    ok, msg = mod.gate_under_controlled_root(b / "child", b, ownership=owned)
    assert not ok and "不一致" in msg


def test_no_ownership_file_is_created(tmp_path):
    """所有权**不落盘**（可伪造的永久文件一律不做）。"""
    mod = _load()
    root = tmp_path / "no_ownership_file"
    root.mkdir()
    owned = mod._ControlledRoot(root, created=True)
    mod.gate_under_controlled_root(root / "child", root, ownership=owned)
    leftovers = [p.name for p in root.iterdir() if p.is_file()]
    assert leftovers == [], f"受控根内不应落盘所有权文件: {leftovers}"


def test_alias_in_ancestor_chain_is_detected(tmp_path):
    """词法祖先链上的 symlink 别名 ⇒ 拒绝（先 resolve 会把别名抹掉）。"""
    mod = _load()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    os.symlink(real, alias, target_is_directory=True)
    reason = mod._check_existing_tree_aliases(alias / "fresh" / "child")
    assert reason is not None, "祖先别名未被探测到（先 resolve 抹掉了别名）"


def test_lstat_permission_error_is_not_swallowed(tmp_path, monkeypatch):
    """祖先探测 PermissionError ⇒ 拒绝（不得当成「非 Windows 属性缺失」放行）。"""
    mod = _load()
    parent = tmp_path / "parent"
    parent.mkdir()
    original = mod.os.stat

    def denied(path, *args, **kwargs):
        if kwargs.get("follow_symlinks") is False and Path(path) == parent:
            raise PermissionError("synthetic lstat failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "stat", denied)
    reason = mod._check_existing_tree_aliases(parent / "child")
    assert reason is not None, "PermissionError 被静默吞掉"
    assert "无法确认边界" in reason


def test_probe_oserror_is_not_swallowed(tmp_path, monkeypatch):
    """祖先探测 OSError ⇒ 拒绝（边界无法确认就不放行）。"""
    mod = _load()
    parent = tmp_path / "parent"
    parent.mkdir()
    original = mod.os.stat

    def denied(path, *args, **kwargs):
        if kwargs.get("follow_symlinks") is False and Path(path) == parent:
            raise OSError("synthetic probe failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "stat", denied)
    reason = mod._check_existing_tree_aliases(parent / "child")
    assert reason is not None and "无法确认边界" in reason


def test_exclusive_create_semantics_at_os_level(tmp_path):
    """O_EXCL 语义：已存在文件再开必抛 FileExistsError（原字节不动）。"""
    target = tmp_path / "x.bin"
    target.write_bytes(b"original")
    with pytest.raises(FileExistsError):
        os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    assert target.read_bytes() == b"original"


# ─────────────── REWORK2：path 依赖解析（注释/引号/内联/不可判定） ───────────────

def _mk_ws(tmp_path):
    """最小三包工作区（App/core/PWA），供 path 依赖用例复用。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("flutter_app", "ambrace_core", "pwa"):
        (ws / name).mkdir()
    (ws / "flutter_app" / "pubspec.yaml").write_text("version: 1.0.0\n", encoding="utf-8")
    (ws / "ambrace_core" / "pubspec.yaml").write_text("version: 0.1.0\n", encoding="utf-8")
    (ws / "pwa" / "pubspec.yaml").write_text("version: 1.0.0\n", encoding="utf-8")
    return ws


def test_path_dep_with_trailing_comment_is_parsed(tmp_path, monkeypatch):
    """path 依赖带合法 YAML 行尾注释 ⇒ 仍被解析（不得整行跳过）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  ambrace_core:\n"
        "    path: ../ambrace_core # 本地路径依赖\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks


def test_path_dep_quoted_value_with_spaces(tmp_path, monkeypatch):
    """引号内含空格的 path 依赖 ⇒ 按整体路径解析并判定。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "ambrace_core dir").mkdir()
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        'version: 1.0.0\ndependencies:\n  core:\n    path: "../ambrace_core dir"\n',
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks


def test_path_dep_inline_mapping(tmp_path, monkeypatch):
    """内联映射 path 依赖 ⇒ 解析（真实 -z/git 之外的另一种合法 YAML 形态）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "pwa" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  core: {path: ../ambrace_core}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks


def test_path_dep_unparsable_is_reported_not_green(tmp_path, monkeypatch):
    """无法可靠解析的 path 值 ⇒ **报红**（不得当 0 依赖绿）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  weird:\n    path: {a: b}\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "不可判定的 path 依赖被当成 0 依赖绿"
    detail = " ".join(d for (_n, _o, d) in checks)
    assert "不支持" in detail or "无法判定" in detail


def test_path_dep_symlink_alias_outside_is_red(tmp_path, monkeypatch):
    """候选根内的 symlink 别名指向根外 ⇒ 红（按真实身份判定，不是字符串前缀）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    os.symlink(outside, ws / "alias_out", target_is_directory=True)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  core:\n    path: ../alias_out\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "symlink 别名指向根外被报内部"


def test_path_dep_in_core_package_is_parsed_and_checked(tmp_path, monkeypatch):
    """path 依赖声明在 **core 包**内 ⇒ 同样被解析与判定（App/core/PWA 三包全覆盖）。

    REWORK2 §2：解析与边界判定必须覆盖三包，不能只查 App/PWA。
    """
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "local_pkg").mkdir()
    # core 包内声明一个合法的本地 path 依赖 ⇒ 应通过
    (ws / "ambrace_core" / "pubspec.yaml").write_text(
        "version: 0.1.0\ndependencies:\n  local_pkg:\n    path: ../local_pkg\n",
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks
    # 同一个 core 包内改成越界 ⇒ 必须红
    (ws / "ambrace_core" / "pubspec.yaml").write_text(
        "version: 0.1.0\ndependencies:\n  local_pkg:\n    path: ../../outside_pkg\n",
        encoding="utf-8")
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "core 包内的越界 path 依赖被报内部"


def test_path_dep_in_pwa_package_is_parsed_and_checked(tmp_path, monkeypatch):
    """path 依赖声明在 **PWA 包**内 ⇒ 同样被解析与判定（三包全覆盖）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "local_pkg").mkdir()
    (ws / "pwa" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  local_pkg:\n    path: ../local_pkg\n",
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert all(flags), checks
    (ws / "pwa" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  local_pkg:\n    path: ../not_there\n",
        encoding="utf-8")
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "PWA 包内不存在的 path 依赖被报通过"


def test_path_dep_missing_target_is_red(tmp_path, monkeypatch):
    """path 依赖指向根内但**不存在** ⇒ 红。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  core:\n    path: ../not_there\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    assert not flags[-1], "不存在的 path 依赖被报通过"


# ─────────────── REWORK3：YAML 真 fail closed（主审 3 反例 + 正负用例） ───────────────
#
# REWORK3 主审反例（primary_review3/test_yaml_boundaries.py）：
#   ① dependencies: {bad: {path: ../../outside}}   顶层内联依赖块
#   ② "path": ../../outside                          合法引号键
#   ③ dependencies: &deps                            YAML 锚点
# 三者曾被自制行扫描器**静默当 0 依赖绿**。现改用 PyYAML 真解析器，任何无法可靠判定
# ⇒ 报红（fail closed），不得漏检。

_REWORK3_LEAKS = [
    ("顶层内联依赖块", "dependencies: {bad: {path: ../../outside}}\n"),
    ("引号键 path", 'dependencies:\n  bad:\n    "path": ../../outside\n'),
    ("YAML 锚点 &deps", "dependencies: &deps\n  bad:\n    path: ../../outside\n"),
]


@pytest.mark.parametrize("label,content", _REWORK3_LEAKS)
def test_yaml_leak_forms_must_fail_closed(tmp_path, monkeypatch, label, content):
    """主审 3 反例：三种曾静默漏检的 YAML 写法 ⇒ 必须判红（不得当 0 依赖绿）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\n" + content, encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert not scope[1], f"{label}：外指/未知写法被静默放绿 → {scope}"


@pytest.mark.parametrize("label,content", _REWORK3_LEAKS)
def test_yaml_leak_forms_inside_workspace_are_still_checked(tmp_path, monkeypatch, label, content):
    """同一批写法即使指向**工作区内**也必须被识别为 path 依赖（不是「不支持」蒙混过关）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "local_pkg").mkdir()
    inside = content.replace("../../outside", "../local_pkg")
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\n" + inside, encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert scope[1], f"{label}：工作区内的合法 path 依赖被误判为不支持 → {scope}"
    assert "1 项" in scope[2], f"{label}：path 依赖未被识别为 1 项 → {scope}"


def test_yaml_real_three_packages_are_green():
    """正例：候选工作区**现有三包的真实 pubspec** ⇒ 绿，且 path 依赖计数为 2。"""
    mod = _load()
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    vers = next(c for c in checks if c[0] == "package_versions")
    assert scope[1], scope
    assert vers[1], vers
    assert "2 项" in scope[2], scope
    assert "app" in vers[2] and "core" in vers[2] and "pwa" in vers[2], vers


def test_yaml_supported_forms_all_green(tmp_path, monkeypatch):
    """正例：块状 / 引号值 / 行尾注释 / 内联映射四种已支持口径 ⇒ 全绿且都算 path 依赖。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "pkg_a").mkdir()
    (ws / "pkg_b").mkdir()
    (ws / "pkg_c").mkdir()
    (ws / "pkg_d").mkdir()
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\n"
        "dependencies:\n"
        "  a:\n"
        "    path: ../pkg_a          # 块状 + 行尾注释\n"
        "  b:\n"
        '    path: "../pkg_b"        # 引号值\n'
        "  c: {path: ../pkg_c}       # 内联映射\n"
        '  d:\n'
        '    "path": ../pkg_d         # 引号键\n',
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert scope[1], scope
    assert "4 项" in scope[2], scope


def test_yaml_broken_syntax_fails_closed(tmp_path, monkeypatch):
    """未知/非法语法 ⇒ 报红（无法可靠判定 ⇒ 不当 0 依赖绿）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\n  bad:\n   - [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert not scope[1], scope
    assert "无法可靠解析" in scope[2] or "不支持" in scope[2] or "无法判定" in scope[2], scope


def test_yaml_non_mapping_dependency_section_fails_closed(tmp_path, monkeypatch):
    """依赖段不是映射 ⇒ 报红（不得当 0 依赖绿）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies: not_a_mapping\n", encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert not scope[1], scope


def test_yaml_empty_dependency_section_is_not_a_problem(tmp_path, monkeypatch):
    """空依赖段（``dependencies:`` 后面什么都没有）⇒ 合法，不报红（不误伤）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    (ws / "flutter_app" / "pubspec.yaml").write_text(
        "version: 1.0.0\ndependencies:\ndev_dependencies:\n  flutter_test:\n    sdk: flutter\n",
        encoding="utf-8")
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert scope[1], scope


def test_yaml_missing_pyyaml_fails_closed(tmp_path, monkeypatch):
    """环境缺 PyYAML ⇒ 无法可靠判定 ⇒ **判红**（不装包、不静默绿）。"""
    mod = _load()
    ws = _mk_ws(tmp_path)
    monkeypatch.setattr(mod, "WORKSPACE", ws)
    monkeypatch.setattr(mod, "_yaml_module", lambda: None)
    checks, flags = [], []
    mod.check_packages(checks, flags)
    scope = next(c for c in checks if c[0] == "path_dependency_scope")
    assert not scope[1], "缺 PyYAML 时被静默放绿"
    assert "PyYAML" in scope[2], scope


def test_yaml_parser_does_not_use_homegrown_interpreter():
    """口径钉住：pubspec 走**真 YAML 解析器**（PyYAML），不再自制通用解释器。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "import yaml" in src, "未使用 PyYAML 真解析器"
    assert "_strip_yaml_comment" not in src, "自制行扫描器仍在"
    assert "_scan_dep_block" not in src, "自制行扫描器仍在"
    assert "safe_load" in src, "未走 YAML safe_load"


# ─────────────── REWORK2：Git -z rename 协议 + 重复 revision ───────────────

def test_git_rename_uses_second_nul_field(monkeypatch):
    """rename 在 -z 下是**第二个 NUL 字段**；两侧都须在编辑授权内才放行。"""
    mod = _load()

    def make(status_text):
        def run(args, **kw):
            if "rev-parse" in args:
                return subprocess.CompletedProcess(
                    args, 0, "d3a22b15952db5c668cf9dc04a6051c8acab1818\n", "")
            if "status" in args:
                return subprocess.CompletedProcess(args, 0, status_text, "")
            return subprocess.CompletedProcess(args, 0, "", "")
        return run

    # 两侧都在授权内 ⇒ 放行
    monkeypatch.setattr(mod.subprocess, "run", make("R  README.md\0docs/migrate-drill.md\0"))
    checks, flags = [], []
    mod.check_git(checks, flags)
    assert flags[-1], "两侧都在授权内的 rename 应放行"

    # 来源不在授权内 ⇒ 必须红（按 tab 拆分会漏掉来源侧）
    monkeypatch.setattr(
        mod.subprocess, "run",
        make("R  README.md\0backend/app/application/chat_service.py\0"))
    checks, flags = [], []
    mod.check_git(checks, flags)
    assert not flags[-1], "rename 来源不在授权内却被放行"


def test_parse_porcelain_z_reads_rename_source_field():
    """解析器：-z 的 rename 目标与来源分列两个字段（不是 tab 分隔）。"""
    mod = _load()
    records = mod._parse_porcelain_z("R  README.md\0docs/migrate-drill.md\0 M other.py\0")
    assert records[0]["xy"] == "R "
    assert records[0]["path"] == "README.md"
    assert records[0]["src"] == "docs/migrate-drill.md", "rename 来源必须取第二个 NUL 字段"
    assert records[1]["path"] == "other.py"


def test_duplicate_revision_files_are_explicit_failure(tmp_path, monkeypatch):
    """重复 revision 在**解析期**被发现 ⇒ 显式失败（不被 dict 覆盖成恒假检查）。"""
    mod = _load()
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "old.py").write_text(
        f'revision = "{mod.EXPECTED_OLD_HEAD}"\ndown_revision = None\n', encoding="utf-8")
    for name in ("a.py", "b.py"):
        (versions / name).write_text(
            f'revision = "{mod.EXPECTED_HEAD}"\ndown_revision = "{mod.EXPECTED_OLD_HEAD}"\n',
            encoding="utf-8")
    monkeypatch.setattr(mod, "VERSIONS", versions)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 0, mod.EXPECTED_HEAD + " (head)\n", ""))
    checks, flags = [], []
    mod.check_migration_chain(checks, flags)
    assert not all(flags), "重复 revision 不得被 dict 覆盖后当通过"
    assert any(n == "graph_duplicate_revision" for (n, _o, _d) in checks)


def test_parse_versions_reports_duplicate_files(tmp_path, monkeypatch):
    """解析器自身报出重复 revision 的**文件列表**。"""
    mod = _load()
    versions = tmp_path / "versions"
    versions.mkdir()
    for name in ("a.py", "b.py"):
        (versions / name).write_text(
            f'revision = "{mod.EXPECTED_HEAD}"\ndown_revision = None\n', encoding="utf-8")
    monkeypatch.setattr(mod, "VERSIONS", versions)
    revs = mod._parse_versions()
    assert revs.duplicates, "解析期未报出重复 revision"
    rev, files = revs.duplicates[0]
    assert rev == mod.EXPECTED_HEAD
    assert set(files) == {"a.py", "b.py"}


def test_duplicate_detection_is_not_tautological():
    """不再保留 `len(dict)==len(set(dict))` 这类恒假占位。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "len(revs) != len(set(revs))" not in src, "恒假重复检测占位仍在"
    assert "graph_duplicate_revision" in src
