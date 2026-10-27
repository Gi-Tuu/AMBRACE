"""统一候选发布前预检（B2/C7/C9/C8/C10 候选隔离工作区专用，2026-10-24 派单；REWORK2 收严）。

只做结构检查与安全门，不提供任何生产应用/迁移/重启/开关/推送能力，不读取凭据内容。
报告只有结构/计数/合成断言，不带真实正文、token 或配置值。
**本工具不执行迁移**：迁移演练由 backend/tests/test_candidate_migration_rehearsal.py 执行（pytest）。

REWORK2（主审窄返工 2，2026-10-24）修的是**真实入口**，不是探针形态：
- **受控根生命周期贯穿真实 CLI**：外部调用**预先存在**的根（含空目录）一律拒绝——不声明
  「本轮已创建」；本轮由工具独占创建的根用**进程内所有权对象**（`_ControlledRoot`）传递给
  后续报告与子目录操作，复用本轮根**不重复调用**「创建根」门。**不落盘 ownership 文件**
  （可伪造），**不提供**越界/覆盖开关。
- **祖先别名按词法绝对路径逐层检查**：`resolve()` 会把 symlink/junction 抹掉（先 resolve
  再遍历＝别名已消失）。改为沿**原始词法绝对路径**逐层检查已存在祖先的
  symlink/junction/reparse，**再**做 resolve/normcase 与根内比较。实际创建/写入分支同样检查。
- **探测失败不放行**：`PermissionError/OSError` 不再被当成「非 Windows 属性缺失」吞掉；
  只有平台确实没有该属性时走明确分支，**边界无法确认就拒绝**。
- **保护路径与受控根统一**：单参门与双参写门共用同一套受保护路径判定，不再出现
  「单参拦住、双参真实写门绕过」。
- **迁移图解析期即显式失败**：重复 revision 在**写入 dict 之前**发现并报错——不再保留
  `len(dict)==len(set(dict))` 这种恒假占位。
- **path 依赖解析不静默漏检**：改用 **PyYAML 真解析器**（`backend/requirements.txt` 已含
  `pyyaml==6.0.3`，venv 可用；**不装包**，不自制通用 YAML 解释器）⇒ 块状 / 内联映射 /
  引号键 / 引号值 / 行尾注释 / YAML 锚点 / 顶层内联依赖块**都能解析**；
  缺 PyYAML、YAML 语法错、顶层非映射、依赖段非映射、path 值非标量 ⇒
  一律报「不支持/无法判定」并**判红**，**不当 0 依赖绿**。
- **git porcelain -z 按真实协议解析**：rename/copy 是**第二个 NUL 字段**（不是 tab 拆分）。

用法（在候选工作区根目录）：
  python scripts/candidate_preflight.py                      # 快检（默认，秒级，不写任何文件）
  python scripts/candidate_preflight.py --long               # 显式长验证（compile + 定向新测试）
  python scripts/candidate_preflight.py --json out.json --root /tmp/pf_root
       # 报告仅写到本轮受控根内的新文件（独占创建）；不给 --root 则拒绝 --json/--rehearsal-dir
  python scripts/candidate_preflight.py --rehearsal-dir /tmp/pf_root/rehearsal --root /tmp/pf_root

退出码：0=通过；2=存在不满足项；3=参数/安全门拒绝。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
BACKEND = WORKSPACE / "backend"
VERSIONS = BACKEND / "alembic" / "versions"

EXPECTED_HEAD = "d6f8a0b2c4e6"
EXPECTED_OLD_HEAD = "e2f4a6b8c0d2"

# 存在性清单（绝不含编辑授权语义；见 check_git 的 EDIT_WHITELIST）
REQUIRED_FILES = [
    "backend/alembic/versions/b2a0c1d2e3f4_ai_characters_autoincrement.py",
    "backend/alembic/versions/c4e6f8a0b2d4_memory_evidence_write_key.py",
    "backend/alembic/versions/d6f8a0b2c4e6_plan_completion_evidence.py",
    "backend/app/scheduling/prospective_intent.py",
    "backend/app/application/chat_service.py",
    "backend/tests/test_plan_guard_c9b.py",
    "backend/tests/test_candidate_migration_rehearsal.py",
    "backend/tests/test_candidate_preflight.py",
    "scripts/verify.py",
    "scripts/backup.py",
]

# tracked 编辑授权（原任务白名单的五个文件；与"存在性"完全分离）
EDIT_WHITELIST = {
    "scripts/candidate_preflight.py",
    "backend/tests/test_candidate_preflight.py",
    "backend/tests/test_candidate_migration_rehearsal.py",
    "README.md",
    "docs/migrate-drill.md",
}

# 预期未跟踪运行产物目录（前缀豁免仅适用于 untracked 项；tracked 改动不受此豁免）
RUN_ARTIFACT_DIRS = ("logs/", ".pytest_tmp", ".pytest_cache/", ".ruff_cache/",
                     "delivery/", "rework1_delivery/", "rework2_delivery/")

# 兼容保留（主审探针 monkeypatch 点）；REWORK1/REWORK2 判定不使用字符串黑名单
FORBIDDEN_DIR_MARKERS = [str(Path(WORKSPACE.anchor) / "AMBRACE"), "\\AMBRACE", "/backend/data", "\\backend\\data",
                         "/backups", "\\backups", "/.git", "\\.git"]

_REV_RE = re.compile(r"^revision[^=]*=\s*['\"]([0-9a-zA-Z_]+)['\"]", re.MULTILINE)
_DOWN_RE = re.compile(r"^down_revision[^=]*=\s*(.+)$", re.MULTILINE)
_ID_RE = re.compile(r"['\"]([0-9a-zA-Z_]+)['\"]")

# Windows reparse point（junction/挂载点）属性位
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class _RevMap(dict):
    """revision 映射；额外携带**解析期**发现的重复 revision（文件列表）。

    保持 dict 兼容：主审/旧合同仍可用普通 dict 替换 `_parse_versions`（此时无重复信息）。
    """

    def __init__(self, *args, duplicates=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.duplicates = list(duplicates or [])


class _ControlledRoot:
    """本轮受控根的**进程内**所有权对象。

    - 只在本进程内有效：跨进程/跨调用**不可**复用（没有落盘 ownership 文件可伪造）；
    - `created=True` 表示「这个根是本轮由本工具创建的」，后续子目录/报告操作据此
      **不再重复调用**「创建根」门（受控根创建后必然存在，二次新建门会恒拒）。
    """

    __slots__ = ("created", "root")

    def __init__(self, root: Path, created: bool = True):
        self.root = Path(root)
        self.created = bool(created)

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return f"_ControlledRoot(root={self.root!s}, created={self.created})"


def _parse_versions() -> _RevMap:
    """标准库解析 versions/（不导入 app、不触 alembic env）。

    **重复 revision 在写入 dict 之前发现**：同一 revision 出现在多个文件 ⇒ 记入
    ``duplicates`` 并显式失败（不再被 dict 赋值覆盖成恒假检查）。
    """
    revs = _RevMap()
    duplicates: list[tuple[str, list[str]]] = []
    seen_files: dict[str, str] = {}
    if not VERSIONS.is_dir():
        return revs
    for fn in sorted(os.listdir(VERSIONS)):
        if not fn.endswith(".py") or fn.startswith("__"):
            continue
        try:
            src = (VERSIONS / fn).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = _REV_RE.search(src)
        d = _DOWN_RE.search(src)
        if not m:
            continue
        revision = m.group(1)
        if revision in seen_files:
            for rev, files in duplicates:
                if rev == revision:
                    files.append(fn)
                    break
            else:
                duplicates.append((revision, [seen_files[revision], fn]))
            continue
        seen_files[revision] = fn
        parents = [p for p in _ID_RE.findall(d.group(1)) if p != "None"] if d else []
        revs[revision] = parents
    revs.duplicates = duplicates
    return revs


def _validate_graph(revs: dict, results: list, ok: list) -> bool:
    """迁移图结构校验：未知父、多父、循环（visited 有界 DFS）。

    重复 revision **不在这里判**——它在解析期（`_parse_versions`）就已发现并显式失败；
    本函数不再保留 `len(dict)==len(set(dict))` 那种恒假占位。
    """
    valid = True
    unknown = {p for parents in revs.values() for p in parents if p not in revs}
    if unknown:
        results.append(("graph_unknown_parents", False, f"未知父 revision: {sorted(unknown)}"))
        ok.append(False)
        valid = False
    else:
        results.append(("graph_unknown_parents", True, "全部父 revision 可解析"))
        ok.append(True)
    multi = {r: ps for r, ps in revs.items() if len(ps) > 1}
    if multi:
        results.append(("graph_multi_parent", False, f"多父节点（merge 不受支持）: {multi}"))
        ok.append(False)
        valid = False
    else:
        results.append(("graph_multi_parent", True, "无多父节点"))
        ok.append(True)
    # 循环检测：从每个节点出发的有界 DFS（节点数为界）
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {r: WHITE for r in revs}
    cycles = []

    def _dfs(node: str) -> None:
        color[node] = GRAY
        for p in revs.get(node, []):
            if p not in color:
                continue
            if color[p] == GRAY:
                cycles.append(node)
            elif color[p] == WHITE:
                _dfs(p)
        color[node] = BLACK

    sys.setrecursionlimit(max(10000, len(revs) * 4))
    for r in list(revs):
        if color[r] == WHITE:
            _dfs(r)
    if cycles:
        results.append(("graph_cycles", False, f"检测到循环父链: {sorted(set(cycles))}"))
        ok.append(False)
        valid = False
    else:
        results.append(("graph_cycles", True, "无循环父链"))
        ok.append(True)
    return valid


def check_migration_chain(results: list, ok: list):
    """迁移单头与父链核验（静态图严格化 + CLI 严格交叉：returncode 必须 0、heads 集合必须全等）。"""
    revs = _parse_versions()
    duplicates = list(getattr(revs, "duplicates", None) or [])
    if duplicates:
        detail = "；".join(f"{rev} 出现于 {files}" for rev, files in duplicates)
        results.append(("graph_duplicate_revision", False,
                        f"重复 revision（解析期发现，未被 dict 覆盖）: {detail}"))
        ok.append(False)
    else:
        results.append(("graph_duplicate_revision", True, "无重复 revision"))
        ok.append(True)
    if not revs:
        results.append(("migration_chain", False, "versions 目录解析出 0 个 revision"))
        ok.append(False)
        return
    _validate_graph(revs, results, ok)
    children: dict[str, list[str]] = {}
    for rev, parents in revs.items():
        for p in parents:
            children.setdefault(p, []).append(rev)
    static_heads = sorted(r for r in revs if r not in children)
    if static_heads != [EXPECTED_HEAD]:
        results.append(("migration_chain", False, f"静态图 heads={static_heads}（期望单头 {EXPECTED_HEAD}）"))
        ok.append(False)
    else:
        results.append(("migration_chain", True, f"静态图单头 {EXPECTED_HEAD}，共 {len(revs)} revision"))
        ok.append(True)
    chain, cur = [], EXPECTED_HEAD
    while cur in revs and cur not in chain:
        chain.append(cur)
        parents = revs[cur]
        cur = parents[0] if parents else ""
    if EXPECTED_OLD_HEAD not in chain:
        results.append(("parent_chain", False, f"{EXPECTED_OLD_HEAD} 不在 head 父链（链长 {len(chain)}）"))
        ok.append(False)
    else:
        n = chain.index(EXPECTED_OLD_HEAD)
        results.append(("parent_chain", True,
                        f"{EXPECTED_OLD_HEAD} → head 共 {n} 个候选迁移：{chain[:n][::-1]}"))
        ok.append(True)
    # CLI 严格交叉：returncode 必须 0；全部 heads 集合必须与静态图相等
    try:
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "heads"],
            cwd=str(BACKEND), capture_output=True, text=True, timeout=120, check=False,
            env={**os.environ, "INSTANCE_LOCK_PORT": "0"},
        )
        if r.returncode != 0:
            results.append(("alembic_cli_crosscheck", False,
                            f"alembic heads 非零退出 exit={r.returncode} stderr={r.stderr[:200]}"))
            ok.append(False)
            return
        cli_heads = sorted(re.findall(r"^([0-9a-zA-Z_]+)\s*(?:\(head\)|\(heads\))", r.stdout or "", re.MULTILINE))
        if not cli_heads:
            cli_heads = sorted({m.group(1) for m in
                                re.finditer(r"^\s*([0-9a-zA-Z_]+)(?:\s*\(head\))?\s*$", r.stdout or "", re.MULTILINE)
                                if m.group(1) not in ("(head)",)})
        if cli_heads != static_heads:
            results.append(("alembic_cli_crosscheck", False,
                            f"CLI heads={cli_heads} 与静态图 heads={static_heads} 不一致"))
            ok.append(False)
        else:
            results.append(("alembic_cli_crosscheck", True, f"CLI 与静态图 heads 一致：{cli_heads}"))
            ok.append(True)
    except Exception as e:  # noqa: BLE001 - 交叉验证失败必须显式报告
        results.append(("alembic_cli_crosscheck", False, f"alembic heads 执行失败: {e}"))
        ok.append(False)


def _parse_porcelain_z(text: str) -> list[dict]:
    """解析 `git status --porcelain=v1 -z` 输出。

    真实协议：每条记录是 ``XY <path>`` 以 NUL 结尾；**rename/copy 是两个 NUL 字段**
    （先目标路径、后来源路径），**不是 tab 分隔**——按 tab 拆分会把来源路径丢掉，
    导致改名授权判定漏掉一侧。

    返回 [{"xy": str, "path": str, "src": str|None}]。
    """
    fields = text.split("\0")
    records: list[dict] = []
    i = 0
    n = len(fields)
    while i < n:
        field = fields[i]
        i += 1
        if not field:
            continue
        if len(field) < 3 or field[2] != " ":
            # 非标准记录：保守当作未知改动处理（路径取余下全文）
            records.append({"xy": field[:2], "path": field[3:] if len(field) > 3 else "",
                            "src": None})
            continue
        xy = field[:2]
        path = field[3:]
        src = None
        if ("R" in xy or "C" in xy) and i < n:
            # rename/copy：第二个 NUL 字段是来源路径
            src = fields[i]
            i += 1
        records.append({"xy": xy, "path": path, "src": src})
    return records


def _norm_paths(*paths: str) -> set[str]:
    """路径归一集合：原样 + 去尾部换行（兼容手写 stub 形态；真实 -z 文件名原样保留）。"""
    out: set[str] = set()
    for p in paths:
        if not p:
            continue
        out.add(p.replace(os.sep, "/"))
        stripped = p.rstrip("\r\n")
        if stripped != p:
            out.add(stripped.replace(os.sep, "/"))
    return out


def check_git(results: list, ok: list):
    """Git HEAD/工作区（porcelain -z；git 非0红；编辑授权≠存在性；白名单文件带 SHA）。

    输出登记：正式 HEAD（指纹）+ 工作区修改文件内容 SHA（白名单内逐个列出）；
    "仅白名单修改" 不写作 "干净已验"。"""
    def _git_bytes(*args: str):
        return subprocess.run(["git", *args], cwd=str(WORKSPACE), capture_output=True,
                              timeout=60, check=False)

    def _out(r):
        """探针 stub 可能返回 str（text 模拟）；真实 subprocess 返回 bytes。"""
        if isinstance(r.stdout, bytes):
            return r.stdout.decode("utf-8", errors="replace")
        return r.stdout or ""

    r = _git_bytes("rev-parse", "HEAD")
    if r.returncode != 0:
        results.append(("git_head", False, f"git rev-parse HEAD 非零 exit={r.returncode}"))
        ok.append(False)
        return
    head = _out(r).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        results.append(("git_head", False, f"rev-parse HEAD 异常: {head!r}"))
        ok.append(False)
        return
    results.append(("git_head", True, f"正式 HEAD={head}（候选树指纹基线；验收时与受验基线比对）"))
    ok.append(True)

    rz = _git_bytes("status", "--porcelain=v1", "-z")
    if rz.returncode != 0:
        results.append(("git_status", False, f"git status 非零 exit={rz.returncode}"))
        ok.append(False)
        return
    records = _parse_porcelain_z(_out(rz))
    tracked_modified, untracked_other = [], []
    whitelist_untracked = []
    for rec in records:
        xy = rec["xy"]
        path = rec["path"]
        if xy.strip() == "" and rec["src"] is None:
            continue
        is_untracked = xy.startswith("??")
        if is_untracked:
            # 未跟踪：仅豁免明确的运行产物目录前缀（untracked-only）
            posix = path.replace(os.sep, "/")
            if any(posix.startswith(a) or (posix + "/").startswith(a) for a in RUN_ARTIFACT_DIRS):
                continue
            if posix in EDIT_WHITELIST:
                whitelist_untracked.append(posix)
                continue
            untracked_other.append(path)
        else:
            # tracked 改动（M/R/D/A）：只接受显式编辑授权文件
            # rename/copy 的目标与来源**两侧**都要在授权内（-z 下来源是第二个 NUL 字段）
            norm = _norm_paths(path, rec["src"] or "")
            if norm and norm <= EDIT_WHITELIST:
                tracked_modified.append(path)
                continue
            untracked_other.append(path if rec["src"] is None else f"{path} <- {rec['src']}")
    sha_lines = []
    for rel in sorted(set(tracked_modified) | set(whitelist_untracked)):
        fp = WORKSPACE / rel
        if fp.is_file():
            sha_lines.append(f"{rel}={hashlib.sha256(fp.read_bytes()).hexdigest()[:16]}")
    results.append(("git_worktree", not untracked_other,
                    ("编辑授权内改动 {} 项（含内容 SHA 前缀）: {}；白名单外: {}"
                     .format(len(tracked_modified), sha_lines or "无", untracked_other or "无"))
                    if (tracked_modified or untracked_other) else "工作区完全干净"))
    ok.append(not untracked_other)


def check_required_files(results: list, ok: list):
    missing = [rel for rel in REQUIRED_FILES if not (WORKSPACE / rel).is_file()]
    results.append(("required_files", not missing,
                    "全部存在（存在性清单，非编辑授权）" if not missing else f"缺失: {missing}"))
    ok.append(not missing)


# ── pubspec 解析（REWORK3：改用真 YAML 解析器，fail closed） ──
#
# REWORK3：主审 3 个反例证明**自制行扫描器会静默漏检**——顶层内联依赖块
# `dependencies: {bad: {path: ../../outside}}`、引号键 `"path"`、YAML 锚点
# `dependencies: &deps` 三种写法都被当成「0 依赖」放绿。本仓**已有 PyYAML**
# （backend/requirements.txt 含 pyyaml==6.0.3，venv 可用，已核实）⇒ 改用真解析器，
# 不再自制通用 YAML 解释器，也**不装包**。
#
# **fail closed 口径**：PyYAML 缺失 / YAML 语法错 / 顶层不是映射 / 依赖段不是映射 /
# path 值不是标量 ⇒ 一律记为「不支持/无法判定」⇒ **判红**，绝不静默绿。

_DEP_SECTIONS = ("dependencies", "dev_dependencies", "dependency_overrides")
_PUBSPECS = (("app", "flutter_app"), ("core", "ambrace_core"), ("pwa", "pwa"))


def _yaml_module():
    """返回 PyYAML 模块；不可用时返回 None（调用方据此 fail closed，不装包）。"""
    try:
        import yaml  # 运行时导入，缺库按 fail closed 处理（不装包）
        return yaml
    except Exception:  # noqa: BLE001 - 任何导入失败都按"无法判定"处理
        return None


def _load_pubspec(text: str) -> tuple[dict | None, str | None]:
    """用 PyYAML 解析 pubspec。

    返回 ``(映射, 错误说明)``：
    - 解析成功 ⇒ ``(dict, None)``；
    - 解析失败 / 顶层不是映射 / 缺 PyYAML ⇒ ``(None, 说明)``；**说明非空即判红**。
    """
    mod = _yaml_module()
    if mod is None:
        return None, ("无法可靠解析：当前环境缺少 PyYAML"
                      "（backend/requirements.txt 已含 pyyaml==6.0.3；本工具不装包）")
    try:
        data = mod.safe_load(text)
    except Exception as e:  # noqa: BLE001 - 任何解析异常都按"无法判定"处理
        return None, f"无法可靠解析（YAML 语法错误）: {type(e).__name__}: {e}"
    if data is None:
        return None, "无法可靠解析：pubspec 为空或全为注释"
    if not isinstance(data, dict):
        return None, f"无法可靠解析：pubspec 顶层不是映射（{type(data).__name__}）"
    return data, None


def _collect_path_deps(node, dep_name: str, out: list, problems: list, where: str) -> None:
    """递归收集依赖条目里的 ``path`` 键（**任何层级**都算，避免嵌套写法漏检）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "path":
                # 引号键 `"path"` 由 PyYAML 归一为 `path`，这里天然覆盖
                if isinstance(value, str):
                    out.append((dep_name, value))
                else:
                    problems.append(
                        f"{where}: 依赖 {dep_name} 的 path 值不是标量（不支持/无法判定）: {value!r}")
            else:
                _collect_path_deps(value, dep_name, out, problems, where)
    elif isinstance(node, list):
        for item in node:
            _collect_path_deps(item, dep_name, out, problems, where)
    # 标量（版本约束等）不是 path 依赖


def _extract_path_deps(data: dict) -> tuple[list[tuple[str, str]], list[str]]:
    """从已解析的 pubspec 映射里取出全部本地 path 依赖与「不可判定」项。

    - 三个依赖段：``dependencies`` / ``dev_dependencies`` / ``dependency_overrides``；
    - 段值是映射 ⇒ 逐条目递归找 ``path``（含引号键；PyYAML 已归一）；
    - 段值是 ``None``（空段）⇒ 合法，不是问题；
    - 段值是其它类型 ⇒ 不可判定 ⇒ 记入 problems（⇒ 判红）。
    """
    deps: list[tuple[str, str]] = []
    problems: list[str] = []
    for section in _DEP_SECTIONS:
        if section not in data:
            continue
        value = data[section]
        if value is None:
            continue  # 空依赖段：合法
        if not isinstance(value, dict):
            problems.append(
                f"{section}: 依赖段不是映射（不支持/无法判定）: {type(value).__name__}")
            continue
        for dep_name, dep_value in value.items():
            _collect_path_deps(dep_value, str(dep_name), deps, problems, section)
    return deps, problems


def check_packages(results: list, ok: list):
    """三包版本 + 真实 path 依赖边界（App/core/PWA 逐项 resolve；越界/外指/别名拒绝）。

    REWORK3：改用 PyYAML 真解析。**解析期任何无法可靠判定 ⇒ 显式报红**
    （缺 PyYAML / YAML 语法错 / 顶层非映射 / 依赖段非映射 / path 值非标量），
    **不得当 0 依赖绿**。
    """
    versions: dict[str, str | None] = {}
    violations: list[str] = []
    unsupported: list[str] = []
    checked = 0
    workspace_root = WORKSPACE.resolve()
    for short, pkg_rel in _PUBSPECS:
        pub = WORKSPACE / pkg_rel / "pubspec.yaml"
        if not pub.is_file():
            violations.append(f"{pkg_rel}: pubspec 缺失")
            versions[short] = None
            continue
        try:
            text = pub.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            violations.append(f"{pkg_rel}: pubspec 读取失败 {e}")
            versions[short] = None
            continue
        data, err = _load_pubspec(text)
        if err is not None:
            unsupported.append(f"{pkg_rel}: {err}")
            versions[short] = None
            continue
        version = data.get("version")
        versions[short] = str(version) if isinstance(version, (str, int, float)) else None
        deps, problems = _extract_path_deps(data)
        for p in problems:
            unsupported.append(f"{pkg_rel}: {p}")
        for name, raw in deps:
            checked += 1
            # 统一 Windows/POSIX 形态再解析（防大小写/盘符别名与反斜杠形态）
            resolved = (pub.parent / Path(raw)).resolve()
            inside = os.path.normcase(str(resolved)).startswith(
                os.path.normcase(str(workspace_root) + os.sep))
            if not inside:
                violations.append(
                    f"{pkg_rel}: {name} path 解析到候选工作区外: {raw} → {resolved}")
            elif not resolved.is_dir():
                violations.append(f"{pkg_rel}: {name} path 不存在: {raw}")
    missing = [k for k, v in versions.items() if not v]
    results.append(("package_versions", not missing, str(versions)))
    ok.append(not missing)
    all_bad = violations + unsupported
    results.append(("path_dependency_scope", not all_bad,
                    f"本地 path 依赖 {checked} 项全部解析在候选工作区内"
                    if not all_bad else "; ".join(all_bad)))
    ok.append(not all_bad)


def check_verification_entries(results: list, ok: list):
    entries = ["scripts/verify.py", "backend/tests/conftest.py", "backend/requirements.txt"]
    missing = [e for e in entries if not (WORKSPACE / e).is_file()]
    results.append(("verification_entries", not missing,
                    "验证入口齐全" if not missing else f"缺失: {missing}"))
    ok.append(not missing)


def run_long_checks(results: list, ok: list):
    """显式长验证：py_compile 全 backend/app + 两个新测试定向 pytest（非全量）。"""
    r = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(BACKEND / "app")],
        capture_output=True, text=True, timeout=600, check=False,
        env={**os.environ, "INSTANCE_LOCK_PORT": "0"}, cwd=str(BACKEND),
    )
    results.append(("compileall_app", r.returncode == 0, f"exit={r.returncode}"))
    ok.append(r.returncode == 0)
    tests = ["tests/test_candidate_preflight.py", "tests/test_candidate_migration_rehearsal.py"]
    r2 = subprocess.run(
        [sys.executable, "-m", "pytest", *tests, "-q", "--tb=short",
         "-p", "no:cacheprovider", "--basetemp=.pytest_tmp_preflight"],
        capture_output=True, text=True, timeout=600, encoding="utf-8", errors="replace", check=False,
        env={**os.environ, "INSTANCE_LOCK_PORT": "0"}, cwd=str(BACKEND),
    )
    tail = (r2.stdout or "").strip().splitlines()[-1] if r2.stdout else ""
    results.append(("targeted_new_tests", r2.returncode == 0, f"exit={r2.returncode} {tail}"))
    ok.append(r2.returncode == 0)


# ─────────────── 受控根与写出口安全（REWORK1 基础 + REWORK2 修真实入口） ───────────────


def _real_identity(p: Path) -> str:
    """路径真实身份：resolve + normcase（Windows 大小写/盘符归一）。"""
    return os.path.normcase(str(Path(p).resolve()))


def _lexical_chain(p: Path) -> list[Path]:
    """词法绝对路径自身 + 祖先链。

    **绝不先 resolve**：resolve 会把 symlink/junction 解析掉，别名信息随之消失
    （先 resolve 再遍历＝别名已被抹掉）。这里只做词法归一（``os.path.abspath``），
    保留每一层路径分量本身，交给 `_check_existing_tree_aliases` 逐层探测。
    """
    base = Path(os.path.abspath(str(p)))
    return [base, *base.parents]


def _check_existing_tree_aliases(p: Path) -> str | None:
    """已存在祖先链别名探测：任一**已存在**祖先（含自身）是 symlink/junction/reparse ⇒ 返回拒绝理由。

    - 按**词法绝对路径**逐层检查（不 resolve），别名不会被抹掉；
    - 探测失败（PermissionError/OSError）＝**无法确认边界** ⇒ 一律拒绝（不在 except 放行）；
    - 只有平台确实没有 reparse 属性时（非 Windows）才走明确放行分支。
    """
    try:
        chain = _lexical_chain(p)
    except (OSError, ValueError) as e:
        return f"路径词法归一失败（无法确认边界）: {e}"
    for anc in chain:
        try:
            exists = anc.exists()
            is_link = anc.is_symlink()
        except OSError as e:
            return f"祖先存在性探测失败（无法确认边界）: {anc}: {e}"
        if not exists and not is_link:
            continue
        try:
            st = os.stat(anc, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except PermissionError as e:
            return f"祖先探测被拒绝（无法确认边界）: {anc}: {e}"
        except OSError as e:
            return f"祖先探测失败（无法确认边界）: {anc}: {e}"
        try:
            if is_link:
                return f"祖先为 symlink/junction: {anc}"
            if os.name == "nt":
                attrs = getattr(st, "st_file_attributes", None)
                if attrs is None:
                    return f"无法确认 reparse 属性（平台 {os.name}，无法确认边界）: {anc}"
                if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                    return f"祖先含 reparse point（junction/挂载）: {anc}"
            else:
                # 非 Windows：平台确实没有 reparse 概念，symlink 检查已覆盖（明确分支，不吞异常）
                pass
        except OSError as e:
            return f"祖先属性读取失败（无法确认边界）: {anc}: {e}"
    return None


def _forbidden_marker_hit(target: Path) -> str | None:
    """受保护路径判定（真实身份口径，非字符串黑名单）。

    FORBIDDEN_DIR_MARKERS 的每一项 resolve+normcase 后与 target 及其**全部已存在祖先**
    的真实身份比较——symlink 解析后落到受保护目录、Windows 大小写别名、盘符别名都在
    真实身份层面命中。解析失败（无法确认边界）＝命中（拒绝）。
    """
    markers = {_real_identity(Path(m)) for m in FORBIDDEN_DIR_MARKERS if m}
    try:
        chain = [target, *target.parents]
        for p in chain:
            real = _real_identity(p)
            for m in markers:
                if real == m or real.startswith(m + os.sep):
                    return f"目标/祖先真实身份落在受保护路径: {p} → {real}"
    except (OSError, RuntimeError) as e:
        return f"路径解析失败（无法确认边界）: {e}"
    return None


def rehearsal_gate(target: Path, controlled_root: Path | None = None) -> tuple[bool, str]:
    """演练目录安全门（兼容单参探针契约）。

    无 controlled_root：受保护路径判定（真实身份）+ 只判定不创建——已存在（含空目录）/
    symlink/父链别名一律拒绝。
    有 controlled_root：走完整授权根模型（gate_under_controlled_root，受控新建）。
    """
    if controlled_root is not None:
        return gate_under_controlled_root(target, controlled_root)
    hit = _forbidden_marker_hit(target)
    if hit:
        return False, hit
    if target.exists():
        kind = "目录" if target.is_dir() else "文件"
        return False, f"目标已存在（{kind}）——只允许本轮新建，拒绝复用: {target}"
    if target.is_symlink():
        return False, f"目标为符号链接: {target}"
    alias = _check_existing_tree_aliases(target.parent)
    if alias:
        return False, f"目标父链别名: {alias}"
    return True, f"安全门通过（新建+别名无）：{target}（创建动作由受控根入口执行）"


def gate_under_controlled_root(target: Path, controlled_root: Path,
                               ownership: _ControlledRoot | None = None) -> tuple[bool, str]:
    """授权根模型：target 必须位于本轮受控根之内、且是受控根下的**新建**路径。

    - **无所有权对象时**：受控根本身必须不存在（本轮创建并独占）——已存在根无法证明归属；
    - **有所有权对象时**（本轮由工具创建）：跳过「根必须不存在」这一道门（根已由本轮创建，
      二次新建门会恒拒），其余判定一律照旧；
    - target 相对受控根，规范化后不得逃逸（.. / 别名 / 大小写 / 盘符）；
    - target 若已存在（含空目录）＝非本轮新建 ⇒ 拒绝；
    - 受控根与 target 的**全部已存在祖先**做 symlink/junction/reparse 别名探测（词法口径）；
      探测失败＝拒绝；
    - 受保护路径判定与单参门**统一**（同一条 `_forbidden_marker_hit`）。
    """
    if ownership is not None:
        if Path(ownership.root) != Path(controlled_root):
            return False, (f"受控根所有权对象与目标根不一致: "
                           f"owned={ownership.root} target_root={controlled_root}")
        if not ownership.created:
            return False, f"受控根所有权对象未标记为本轮创建: {controlled_root}"
    else:
        if controlled_root.exists() or controlled_root.is_symlink():
            return False, f"受控根已存在（非本轮新建独占）: {controlled_root}"
    alias = _check_existing_tree_aliases(controlled_root.parent)
    if alias:
        return False, f"受控根父链别名: {alias}"
    hit = _forbidden_marker_hit(controlled_root)
    if hit:
        return False, f"受控根落在受保护路径: {hit}"
    hit = _forbidden_marker_hit(target)
    if hit:
        return False, f"目标落在受保护路径: {hit}"
    real_root = _real_identity(controlled_root)
    real_target = _real_identity(target)
    if not (real_target == real_root or real_target.startswith(real_root + os.sep)):
        return False, (f"目标不在受控根内（规范化后逃逸/别名）: "
                       f"root={real_root} target={real_target}")
    if target.exists() or target.is_symlink():
        kind = "目录" if target.is_dir() else "文件"
        return False, f"目标已存在（{kind}）——只允许本轮新建，拒绝复用: {target}"
    alias = _check_existing_tree_aliases(target.parent)
    if alias:
        return False, f"目标父链别名: {alias}"
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return False, f"目标已存在（竞态独占失败）: {target}"
    except OSError as e:
        return False, f"目录创建失败: {e}"
    return True, f"受控新建就绪: {target}（受控根 {controlled_root}）"


def _write_report_in_root(path: Path, controlled_root: Path, data: str) -> tuple[bool, str]:
    """报告写入：目标必须在受控根内（真实身份校验），文件独占创建（O_EXCL）。

    与目录新建门分离——受控根本轮已创建，写文件只验「在根内 + 不覆盖」。
    受保护路径与别名判定同样统一到受控根口径。
    """
    real_out = _real_identity(path)
    real_root = _real_identity(controlled_root)
    if not (real_out == real_root or real_out.startswith(real_root + os.sep)):
        return False, f"报告目标不在受控根内: {path}"
    hit = _forbidden_marker_hit(path)
    if hit:
        return False, f"报告目标落在受保护路径: {hit}"
    alias = _check_existing_tree_aliases(path.parent)
    if alias:
        return False, f"报告目标父链别名: {alias}"
    return _write_new_file_exclusive(path, data)


def _write_new_file_exclusive(path: Path, data: str) -> tuple[bool, str]:
    """独占创建写文件：O_CREAT|O_EXCL|O_WRONLY——已存在（含 .db/.sqlite/源码/旧 JSON）
    一律拒绝且**原字节不变**（拒绝发生在任何打开/截断之前）。"""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o644)
    except FileExistsError:
        return False, f"报告目标已存在，拒绝覆盖（原文件字节未动）: {path}"
    except OSError as e:
        return False, f"报告创建失败: {e}"
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
    except OSError as e:
        return False, f"报告写入失败（已创建的占位文件需人工清理）: {e}"
    return True, f"报告已写入（独占创建）: {path}"


def main() -> int:
    ap = argparse.ArgumentParser(description="统一候选发布前预检（隔离工作区专用；REWORK2 安全模型）")
    ap.add_argument("--long", action="store_true", help="显式长验证（compile + 定向新测试；不跑全量）")
    ap.add_argument("--json", metavar="PATH", help="机器可读 JSON 报告（须位于 --root 内；独占创建，拒绝覆盖）")
    ap.add_argument("--root", metavar="PATH", help="本轮受控根（必须不存在；由本工具创建并独占；--json/--rehearsal-dir 均须在其内）")
    ap.add_argument("--rehearsal-dir", metavar="PATH", help="演练目录（须位于 --root 内；只创建受控空目录=安全门就绪；**本工具不执行迁移**，真正的迁移演练由 tests/test_candidate_migration_rehearsal.py 执行）")
    args = ap.parse_args()

    if args.json or args.rehearsal_dir:
        if not args.root:
            print("[REJECT] --json/--rehearsal-dir 必须显式提供 --root（本轮受控根；拒绝落在未授权位置）")
            return 3
        root_path = Path(args.root)
        # 外部调用**预先存在**的根（含空目录）一律拒绝——不声明「本轮已创建」。
        if root_path.exists() or root_path.is_symlink():
            print(f"[REJECT] 受控根已存在（非本轮新建独占，拒绝复用）: {root_path}")
            return 3
        alias = _check_existing_tree_aliases(root_path.parent)
        if alias:
            print("[REJECT] 受控根父链别名: " + alias)
            return 3
        hit = _forbidden_marker_hit(root_path)
        if hit:
            print("[REJECT] " + hit)
            return 3
        try:
            root_path.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            print(f"[REJECT] 受控根创建竞态（已存在）: {root_path}")
            return 3
        except OSError as e:
            print(f"[REJECT] 受控根创建失败: {e}")
            return 3
        # 进程内所有权对象：本轮独占创建的根 → 后续子目录/报告**不再重复调用**「创建根」门
        owned = _ControlledRoot(root_path, created=True)
        payload: dict = {"controlled_root": f"受控根（本轮由本工具新建独占）: {root_path}"}
        if args.rehearsal_dir:
            t = Path(args.rehearsal_dir)
            r_ok, r_msg = gate_under_controlled_root(t, root_path, ownership=owned)
            payload["rehearsal_gate"] = r_ok
            payload["rehearsal_detail"] = r_msg
            print(("[OK] " if r_ok else "[REJECT] ") + r_msg)
            if not r_ok:
                if args.json:
                    out = Path(args.json)
                    w_ok, w_msg = _write_report_in_root(out, root_path,
                                                        json.dumps(payload, ensure_ascii=False, indent=2))
                    print(("[OK] " if w_ok else "[REJECT] ") + w_msg)
                return 3
        # 受控根内快检（默认）+ 可选长验证
        results: list = []
        ok_flags: list = []
        check_git(results, ok_flags)
        check_migration_chain(results, ok_flags)
        check_required_files(results, ok_flags)
        check_packages(results, ok_flags)
        check_verification_entries(results, ok_flags)
        if args.long:
            run_long_checks(results, ok_flags)
        payload["mode"] = "long" if args.long else "quick"
        payload["workspace"] = str(WORKSPACE)
        payload["checks"] = [{"name": n, "ok": bool(o), "detail": d} for (n, o, d) in results]
        payload["passed"] = [n for (n, o, _d) in results if o]
        payload["failed"] = [n for (n, o, _d) in results if not o]
        for (name, okv, detail) in results:
            print(("[PASS] " if okv else "[FAIL] ") + f"{name}: {detail}")
        print(f"结论：{len(payload['passed'])} 通过 / {len(payload['failed'])} 不满足")
        exit_code = 0 if not payload["failed"] else 2
        if args.json:
            out = Path(args.json)
            w_ok, w_msg = _write_report_in_root(out, root_path,
                                                json.dumps(payload, ensure_ascii=False, indent=2))
            print(("[OK] " if w_ok else "[REJECT] ") + w_msg)
            if not w_ok:
                exit_code = 3
        return exit_code

    # 无受控根模式：纯快检，不写任何文件/目录
    results: list = []
    ok_flags: list = []
    check_git(results, ok_flags)
    check_migration_chain(results, ok_flags)
    check_required_files(results, ok_flags)
    check_packages(results, ok_flags)
    check_verification_entries(results, ok_flags)
    if args.long:
        run_long_checks(results, ok_flags)
    passed = [n for (n, o, _d) in results if o]
    failed = [n for (n, o, _d) in results if not o]
    for (name, okv, detail) in results:
        print(("[PASS] " if okv else "[FAIL] ") + f"{name}: {detail}")
    if args.json:
        print("[REJECT] --json 需要 --root（报告只写本轮受控根内；拒绝在未授权位置创建/覆盖文件）")
        return 3
    print(f"结论：{len(passed)} 通过 / {len(failed)} 不满足")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
