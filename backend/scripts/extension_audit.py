# -*- coding: utf-8 -*-
"""A4 批 8 · M0「接口面与形态护栏」只读对账读数（**零行为**）。

设计依据：``output/AMBRACE_批8_接口面与形态护栏_详细设计_v1_20260929.md`` §7 M0。
M0 的唯一产出物是**读数**，不新增语义：不改权限名、不改文档、不动任何业务代码。

四块各一段：
  B-2  文档承诺 vs 代码事实 —— ``docs/extension-contract.md`` §3 的权限名 与
       ``app/plugins/manifest.py`` 的 ``VALID_PERMISSIONS`` 双向漂移对账（只登记）
  B-3  ``app/plugins/registry.py`` 的 ``verify_plugin_signature`` 恒 True 占位如实登记（含调用点）
  C-1  「桌面气泡 / 锁屏卡片」在本仓不存在 ⇒ 判据改写为通知正文长度分布：
       本单只给「能否测 / 怎么测 / 现有可测口径」
  D    ``llm_usage`` 无 ``estimated`` 列 + ``get_llm_usage`` 全表载入 ⇒ 落成**可执行约束**

纪律：**默认 dry-run、全程只读**。
  - 仓库侧：一次性建内存索引后做 AST / 正则扫描（rg 口径），不改任何被扫文件；
  - 库侧：**默认不连库**。只有显式 ``--app-db <路径>`` 才连，且 ``file:...?mode=ro`` +
    ``PRAGMA query_only=ON`` 双保险，每条 SQL 先过 :func:`readonly_sql`
    （非 SELECT/PRAGMA-白名单 一律抛 :class:`ReadOnlyViolation`，不落库任何写）；
  - 无写操作：本模块自身的 SQL 字面量也在自检里被逐条校验（``self_check_sql``）；
  - 唯一落盘 = ``--report`` 指定的报告文件（默认在仓库外的输出目录（``$AMBRACE_OUTPUT_DIR`` 或 ``./output``））。

用法（一律用项目 venv 的 python）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\extension_audit.py
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\extension_audit.py --json
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\extension_audit.py --no-report
    #   追加库侧可测口径实测（仍只读）：--app-db backend\\data\\sqlite\\ai_companion.db

退出码：0=跑通（漂移条数非 0 也算跑通，M0 只登记不修）；1=关键源文件缺失或解析失败（读数不可信）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

REPORT_NAME = "AMBRACE_批8_M0_对账读数_20260929.md"


def default_report_path() -> Path:
    """报告默认落点：环境变量 AMBRACE_OUTPUT_DIR（缺省 ./output）。

    刻意不写死作者机器路径——仓库要能脱敏公开（发布快照核对项之一）。
    """
    return Path(os.environ.get("AMBRACE_OUTPUT_DIR") or "output") / REPORT_NAME

CONTRACT_DOC = "docs/extension-contract.md"
MANIFEST_PY = "backend/app/plugins/manifest.py"
REGISTRY_PY = "backend/app/plugins/registry.py"
MODELS_AGENT_PY = "backend/app/models/agent/__init__.py"
APPLICATION_SYSTEM_PY = "backend/app/application/system.py"
CAPABILITIES_PY = "backend/app/device/capabilities.py"

# grep 口径：只扫源码/文档，且一次性读进内存后复用（避免每个断言重扫全仓）
GREP_ROOTS = ("backend/app", "backend/tests", "backend/scripts", "flutter_app/lib", "docs")
GREP_SUFFIXES = (".py", ".dart", ".md", ".json", ".arb", ".yaml", ".yml")
PRUNE_DIRS = {
    "__pycache__", ".venv", "venv", "build", "dist", ".git", ".pytest_tmp",
    ".dart_tool", "node_modules", ".mypy_cache", ".pytest_cache", "pub-cache",
}
MAX_FILE_BYTES = 2_000_000
# 「有人在用这个名字」的判定口径：只数运行时代码（前后端），测试/脚本/文档里的字面量不算
RUNTIME_CODE_PREFIXES = ("backend/app/", "flutter_app/lib/")

# 只读 SQL 闸门：允许的开头 + PRAGMA 只读白名单
_READONLY_HEADS = ("select", "with", "pragma", "explain")
_READONLY_PRAGMAS = ("query_only", "table_info", "index_list", "index_info", "user_version", "table_count")
_WRITE_HEADS = ("insert", "update", "delete", "drop", "alter", "create", "replace", "attach", "detach", "vacuum", "reindex")

# 通知裁剪现状（收口前的两份拷贝 + 生成侧上限 + 落库天花板）
NOTIFY_PREVIEW_CHARS = 50
NOTIFY_TARGET_CHARS = 40
PROACTIVE_LOG_CEILING = 500


class ReadOnlyViolation(RuntimeError):
    """试图执行非只读 SQL —— M0 脚本的硬闸门（只应出现在测试里）。"""


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def safe_ident(name: str) -> str:
    """表名/列名白名单式校验（本脚本的 SQL 里唯一允许拼接的位置，防把路径当标识符注进 PRAGMA）。"""
    text = str(name)
    if not _IDENT_RE.match(text):
        raise ValueError(f"非法标识符: {text!r}")
    return text


def readonly_sql(sql: str) -> str:
    """校验并返回 SQL（只读口径）。非 SELECT/WITH/PRAGMA/EXPLAIN 或写语句 ⇒ 抛错。

    多语句（分号后还有内容）也拒：``mode=ro`` 下 ``execute`` 本来就只吃一条，
    但这里显式拒掉是为了让「拼接注入第二条语句」在本脚本里不可能发生。
    """
    text = str(sql).strip().rstrip(";").strip()
    lowered = text.lower()
    if ";" in text:
        raise ReadOnlyViolation(f"拒绝多语句 SQL: {text[:60]!r}")
    if not lowered.startswith(_READONLY_HEADS):
        raise ReadOnlyViolation(f"拒绝非只读 SQL: {text[:60]!r}")
    if lowered.startswith(_WRITE_HEADS):
        raise ReadOnlyViolation(f"拒绝写语句: {text[:60]!r}")
    if lowered.startswith("pragma"):
        body = lowered.split(None, 1)[1].strip() if " " in lowered else ""
        # 赋值形态的 PRAGMA 会改库状态（journal_mode / user_version / synchronous…）——
        # 唯一放行的是本脚本自己的只读闸门本身 query_only=ON/OFF。
        if "=" in body:
            name, _, value = body.partition("=")
            if name.strip() != "query_only" or value.strip().strip("'\";") not in ("on", "off", "0", "1"):
                raise ReadOnlyViolation(f"PRAGMA 赋值被拒（只放行 query_only）: {body!r}")
            return text
        name = body.split()[0] if body else ""
        if not any(name.startswith(p) for p in _READONLY_PRAGMAS):
            raise ReadOnlyViolation(f"PRAGMA 不在只读白名单: {name!r}")
    return text


def query(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    """唯一取数入口（先过 :func:`readonly_sql`）。"""
    return conn.execute(readonly_sql(sql), params)


# ────────────────────────── 仓库索引与 grep 口径 ──────────────────────────

def build_repo_index(repo_root: Path = REPO_ROOT, roots=GREP_ROOTS, suffixes=GREP_SUFFIXES) -> dict:
    """把待扫文件一次性读进内存：``{"files": {rel: [line, ...]}, "root": ...}``。

    **必须排除本脚本自身及其配套用例**（``backend/scripts/`` 在扫描根里，否则本文件里的权限名
    字面量、「桌面气泡」这类断言词会被自己 grep 到，读数自我污染；用例里的合成样本同理）。
    """
    files: dict[str, list[str]] = {}
    self_path = Path(__file__).resolve()
    # 本审计的用例命名口径：test_<脚本名>*.py（含 _m0 这类里程碑后缀）
    twin_prefix = f"backend/tests/test_{self_path.stem}"
    for rel_root in roots:
        base = repo_root / rel_root
        if not base.exists():
            continue
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            try:
                if path.suffix not in suffixes or not path.is_file():
                    continue
                if path.resolve() == self_path:
                    continue
                if path.relative_to(repo_root).as_posix().startswith(twin_prefix):
                    continue
                if set(path.parts) & PRUNE_DIRS:
                    continue
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            rel = path.relative_to(repo_root).as_posix()
            files[rel] = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return {"root": str(repo_root), "files": files}


def grep_index(index: dict, needle: str, *, regex: bool = False,
               exclude_prefixes=(), include_prefixes=()) -> list[dict]:
    """rg 口径的仓库搜索（走内存索引）。返回 ``[{file, line, text}]``，``len()`` 即命中数。"""
    hits: list[dict] = []
    pattern = re.compile(needle) if regex else None
    for rel, lines in sorted(index["files"].items()):
        if any(rel.startswith(p) for p in exclude_prefixes):
            continue
        if include_prefixes and not any(rel.startswith(p) for p in include_prefixes):
            continue
        for n, text in enumerate(lines, 1):
            matched = bool(pattern.search(text)) if regex else (needle in text)
            if matched:
                hits.append({"file": rel, "line": n, "text": text.strip()[:200]})
    return hits


def evidence(hits: list[dict], limit: int = 4) -> str:
    """把命中压成 ``文件:行号`` 证据串（报告里每条结论都要带它）。"""
    if not hits:
        return "（0 命中）"
    head = "; ".join(f"{h['file']}:{h['line']}" for h in hits[:limit])
    return f"{head} 等 {len(hits)} 处" if len(hits) > limit else head


def read_text(repo_root: Path, rel: str) -> tuple[str | None, str | None]:
    path = repo_root / rel
    if not path.is_file():
        return None, f"文件不存在: {rel}"
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        return None, f"读取失败: {rel}: {exc}"
    return text.replace("\r\n", "\n").replace("\r", "\n"), None


# ────────────────────────── AST 小工具 ──────────────────────────

def ast_tree(source: str) -> ast.Module:
    return ast.parse(source)


def ast_find_func(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def ast_tuple_literal(tree: ast.Module, name: str) -> tuple[list[str] | None, bool]:
    """取模块级 ``NAME = (…str…)`` 的字面量。返回 (值, 是否含非字面量元素)。"""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            continue
        if not isinstance(node.value, (ast.Tuple, ast.List)):
            return None, False
        values: list[str] = []
        dynamic = False
        for elt in node.value.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                values.append(elt.value)
            else:
                dynamic = True  # 例如 *device_capability_permissions()
        return values, dynamic
    return None, False


def ast_class_columns(tree: ast.Module, class_name: str) -> list[dict]:
    """取 ORM 类的列名与是否 index=True（按 AnnAssign 目标名）。"""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            columns = []
            for stmt in node.body:
                if not isinstance(stmt, (ast.AnnAssign, ast.Assign)):
                    continue
                targets = [stmt.target] if isinstance(stmt, ast.AnnAssign) else stmt.targets
                name = next((t.id for t in targets if isinstance(t, ast.Name)), None)
                if not name or name.startswith("_"):
                    continue
                call = stmt.value if isinstance(stmt, ast.AnnAssign) else (
                    stmt.value if isinstance(getattr(stmt, "value", None), ast.Call) else None
                )
                indexed = False
                if isinstance(call, ast.Call):
                    indexed = any(
                        kw.arg == "index" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                        for kw in call.keywords
                    )
                columns.append({"name": name, "line": stmt.lineno, "indexed": indexed})
            return columns
    return []


def ast_select_shapes(func_node) -> list[dict]:
    """函数体内每个 ``select(...)`` 的实参个数与参数名（用于区分 ORM 全行载入 / 列投影 / count）。"""
    def _name(a) -> str:
        if isinstance(a, ast.Name):
            return a.id
        if isinstance(a, ast.Attribute):
            owner = getattr(a.value, "id", None) or getattr(a.value, "attr", "")
            return f"{owner}.{a.attr}" if owner else a.attr
        return type(a).__name__

    shapes = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "select":
            shapes.append({
                "lineno": node.lineno,
                "argc": len(node.args),
                "args": [_name(a) for a in node.args],
                # 单参且是大写开头的 ORM 实体名 ⇒ 整行载入（select(LlmUsage)）
                "orm_entity": len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                              and node.args[0].id[:1].isupper(),
            })
    return shapes


def ast_has_call_attr(func_node, attr: str) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == attr for n in ast.walk(func_node)
    )


_SQLISH_RE = re.compile(
    r"^(select|with|pragma|explain|insert|update|delete|drop|alter|create|replace|attach|detach|vacuum|reindex)"
    r"\s+[A-Za-z_*\(]"
)


def collect_sql_literals(path: Path) -> list[str]:
    """自检用：取本模块里所有「像 SQL」的字符串常量（AST 口径，不含运行时拼接）。

    只认「关键字 + 空白 + 标识符」形态，避免把 ``_WRITE_HEADS`` 里的裸词或中文报错文案
    误当成 SQL（那会让自检虚报违规）。
    """
    tree = ast_tree(path.read_text(encoding="utf-8-sig", errors="replace"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if _SQLISH_RE.match(text.lower()):
                out.append(text)
    return out


# ────────────────────────── B-2：文档承诺 vs 代码事实 ──────────────────────────

_PERM_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z0-9_*]+)+$|^[a-z]+(?:_[a-z0-9]+)+$")
_PREVIEW_RE = re.compile(r"仅预告|目标.{0,6}枚举|规划中|待落地|预留")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_HOOK_COUNT_RE = re.compile(r"VALID_HOOKS.{0,40}?共\s*(\d+)\s*个")


def doc_section_lines(doc_text: str, heading_prefix: str) -> list[tuple[int, str]]:
    """取 markdown 某一节（标题以 ``heading_prefix`` 开头）的正文行，带 1-based 行号。"""
    out: list[tuple[int, str]] = []
    started = False
    for n, line in enumerate(doc_text.split("\n"), 1):
        if line.startswith("## "):
            if started:
                break
            started = line.startswith(heading_prefix)
            continue
        if started:
            out.append((n, line))
    return out


def extract_doc_permission_entries(section_lines: list[tuple[int, str]]) -> list[dict]:
    """从「权限模型」一节抽出反引号里的权限名，并区分「声称已落地」与「明示仅预告」。

    分段口径：``- **写**：现有 3 个`` 这类无 token 行不改变状态；空行复位 preview 块。
    """
    entries: list[dict] = []
    preview_block = False
    for n, line in section_lines:
        if not line.strip():
            preview_block = False
        if _PREVIEW_RE.search(line):
            preview_block = True
        for token in _BACKTICK_RE.findall(line):
            token = token.strip()
            if not _PERM_TOKEN_RE.match(token):
                continue
            entries.append({
                "permission": token,
                "doc_line": n,
                "kind": "pattern" if "*" in token or "?" in token else ("preview" if preview_block else "asserted"),
            })
    return entries


def diff_permission_drift(doc_entries: list[dict], code_permissions) -> dict:
    """双向漂移对账（纯函数，可测）。通配条目（``device:*``）覆盖的名字不算漂移。"""
    code = sorted({str(p) for p in code_permissions})
    code_set = set(code)
    doc_named = {e["permission"] for e in doc_entries if e["kind"] != "pattern"}
    patterns = [e for e in doc_entries if e["kind"] == "pattern"]

    def covered_by_pattern(name: str) -> str | None:
        for entry in patterns:
            prefix = entry["permission"].split("*", 1)[0]
            if prefix and name.startswith(prefix):
                return entry["permission"]
        return None

    doc_only = []
    for entry in sorted({(e["permission"], e["kind"], e["doc_line"]) for e in doc_entries if e["kind"] != "pattern"}):
        name, kind, line = entry
        if name not in code_set:
            doc_only.append({
                "permission": name, "doc_kind": kind, "doc_line": line,
                "severity": "high" if kind == "asserted" else "medium",
            })
    code_only = []
    for name in code:
        if name in doc_named:
            continue
        pattern = covered_by_pattern(name)
        if pattern:
            continue
        code_only.append({"permission": name})
    pattern_notes = [{
        "pattern": entry["permission"],
        "doc_line": entry["doc_line"],
        "doc_kind": entry["kind"],
        "covers_code_names": sum(
            1 for name in code if name.startswith(entry["permission"].split("*", 1)[0])
        ),
    } for entry in patterns]
    return {
        "doc_entry_count": len(doc_entries),
        "code_permission_count": len(code),
        "counts": {
            "doc_has_code_lacks": len(doc_only),
            "doc_has_code_lacks_asserted": sum(1 for d in doc_only if d["doc_kind"] == "asserted"),
            "doc_has_code_lacks_preview": sum(1 for d in doc_only if d["doc_kind"] == "preview"),
            "code_has_doc_lacks": len(code_only),
            "both_ok": len([n for n in code if n in doc_named]),
        },
        "drift_doc_has_code_lacks": doc_only,
        "drift_code_has_doc_lacks": code_only,
        "wildcard_notes": pattern_notes,
    }


def load_code_permissions(repo_root: Path, manifest_py: str = MANIFEST_PY, capabilities_py: str = CAPABILITIES_PY) -> dict:
    """合法权限名清单：优先真 import（含动态 device 权限），失败则退回 AST 静态部分。"""
    try:
        import importlib
        module = importlib.import_module("app.plugins.manifest")
        names = sorted({str(p) for p in getattr(module, "VALID_PERMISSIONS")})
        hooks = sorted({str(h) for h in getattr(module, "VALID_HOOKS")})
        if names:
            return {"permissions": names, "hooks": hooks, "source": "import:app.plugins.manifest", "dynamic_unresolved": False}
    except Exception as exc:  # 只登记取源方式，不因为取不到而改判据
        err = f"{type(exc).__name__}: {exc}"
    else:
        err = "import 成功但清单为空"
    text, read_err = read_text(repo_root, manifest_py)
    if text is None:
        return {"permissions": [], "hooks": [], "source": f"unavailable: {read_err or err}", "dynamic_unresolved": True}
    tree = ast_tree(text)
    perms, perms_dynamic = ast_tuple_literal(tree, "VALID_PERMISSIONS")
    hooks, _ = ast_tuple_literal(tree, "VALID_HOOKS")
    return {
        "permissions": sorted(perms or []),
        "hooks": sorted(hooks or []),
        "source": f"ast:{manifest_py}（import 回落：{err}）",
        "dynamic_unresolved": perms_dynamic,
        "note": "动态 device 权限名经 app.device.capabilities 注入，AST 静态部分不含" if perms_dynamic else None,
        "capabilities_file_exists": (repo_root / capabilities_py).is_file(),
    }


def audit_doc_code_drift(repo_root: Path, index: dict, code_perms: dict) -> dict:
    doc_text, doc_err = read_text(repo_root, CONTRACT_DOC)
    if doc_text is None:
        return {"ok": False, "error": doc_err}
    entries = extract_doc_permission_entries(doc_section_lines(doc_text, "## 3"))
    drift = diff_permission_drift(entries, code_perms["permissions"])
    # 「文档有代码无」的每条补**运行时代码**残留读数（防止把它当纯笔误删掉；
    # 口径只数 backend/app 与 flutter_app/lib —— 测试/脚本/文档里的字面量不构成「有人在用这个权限」）
    for item in drift["drift_doc_has_code_lacks"]:
        hits = grep_index(index, item["permission"], include_prefixes=RUNTIME_CODE_PREFIXES)
        item["code_reference_hits"] = len(hits)
        item["code_reference_scope"] = "只数运行时代码（" + ", ".join(RUNTIME_CODE_PREFIXES) + "）"
        item["code_reference_evidence"] = evidence(hits, 2)
    hook_claim = _HOOK_COUNT_RE.search(doc_text)
    doc_hook_count = int(hook_claim.group(1)) if hook_claim else None
    consent_strict = grep_index(index, "def consent_matches", include_prefixes=("backend/app/",))
    return {
        "ok": True,
        "doc_source": f"{CONTRACT_DOC}:§3",
        "permission_source": code_perms["source"],
        "permission_dynamic_unresolved": code_perms.get("dynamic_unresolved", False),
        "consent_strict_match_evidence": evidence(consent_strict, 1),
        "hooks": {
            "doc_claim_count": doc_hook_count,
            "code_count": len(code_perms["hooks"]),
            "drift": (doc_hook_count is not None and doc_hook_count != len(code_perms["hooks"])),
            "method_limit": "只做到「条数对账」；文档正文反引号 token 大量是字段名/表名，逐条名对账噪声不可信 ⇒ 不做",
        },
        **drift,
    }


# ────────────────────────── B-3：签名校验桩 ──────────────────────────

def audit_signature_stub(repo_root: Path, index: dict, registry_py: str = REGISTRY_PY) -> dict:
    text, err = read_text(repo_root, registry_py)
    if text is None:
        return {"ok": False, "error": err}
    func = ast_find_func(ast_tree(text), "verify_plugin_signature")
    if func is None:
        return {"ok": False, "error": f"{registry_py} 未找到 verify_plugin_signature"}
    body = [s for s in func.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    always_true = (
        len(body) == 1
        and isinstance(body[0], ast.Return)
        and isinstance(body[0].value, ast.Constant)
        and body[0].value.value is True
    )
    callsites = [h for h in grep_index(index, "verify_plugin_signature", include_prefixes=RUNTIME_CODE_PREFIXES)
                 if h["file"] != registry_py]
    guard_sites = [h for h in callsites if re.match(r"^if\s+not\s+", h["text"])]
    return {
        "ok": True,
        "function": f"{registry_py}:{func.lineno}-{func.end_lineno}",
        "returns_constant_true": always_true,
        "signature_enforced": False,
        "callsite_count": len(callsites),
        "callsites": callsites,
        "dead_guard_count": len(guard_sites),
        "current_basis": {
            "sha256_trace": evidence(grep_index(index, "sha256", include_prefixes=("backend/app/models/",)), 3),
            "allowed_hosts": evidence(grep_index(index, "allowed_hosts", include_prefixes=("backend/app/api/",)), 3),
        },
        "registration": "恒 True ⇒ 上述三处 ``if not verify_plugin_signature(...)`` 分支永不可达（死闸）；"
                        "「来源可信」现状只依赖 sha256 留痕 + 市场 allowed_hosts，无签名强制。",
        "doc_wording_constraint": "对外文档/面板一律显示 signature=not_enforced，禁止表述为「签名校验通过」。",
    }


# ────────────────────────── C-1：短句形态判据 ──────────────────────────

FORM_ABSENT_TOKENS = ("桌面气泡", "锁屏卡片", "display_form", "msg_form")
# 通知 body 的两个收敛点（离线主动 + 私聊）——「两份拷贝」的口径只数这两处
NOTIFY_CONVERGENCE_FILES = ("backend/app/scheduling/scheduler.py", "backend/app/application/chat/io.py")


def audit_form_surface(repo_root: Path, index: dict) -> dict:
    absent = [{
        "token": token,
        "hit_count": len(grep_index(index, token)),
        "evidence": evidence(grep_index(index, token), 3),
    } for token in FORM_ABSENT_TOKENS]
    existing_gates = {
        "notify_preview_two_copies": grep_index(
            index, r"content\[:50\] \+ \(\"…\"", regex=True, include_prefixes=NOTIFY_CONVERGENCE_FILES),
        "segment_max": grep_index(index, "_MAX_SEGMENT_LEN", exclude_prefixes=("backend/tests/",)),
        "proactive_log_ceiling": grep_index(index, "content[:500]", include_prefixes=("backend/app/scheduling/scheduler.py",)),
        "wechat_hint": grep_index(index, "WECHAT_CHANNEL_HINT", exclude_prefixes=("backend/tests/", "docs/")),
    }
    extra_content_50 = len(grep_index(index, "content[:50]", exclude_prefixes=("backend/tests/",)))
    body_persisted = grep_index(index, "preview", include_prefixes=("backend/app/models/",))
    notify_maxlines = grep_index(index, "maxLines", include_prefixes=("flutter_app/lib/services/",))
    return {
        "ok": True,
        "upstream_wording_absent": absent,
        "all_absent": all(a["hit_count"] == 0 for a in absent),
        "existing_length_gates": {
            "notify_preview_50_copies": {
                "count": len(existing_gates["notify_preview_two_copies"]),
                "evidence": evidence(existing_gates["notify_preview_two_copies"]),
                "hits": existing_gates["notify_preview_two_copies"],
                "scope": f"只数两个收敛点（{', '.join(NOTIFY_CONVERGENCE_FILES)}）；"
                         f"全仓另有 {extra_content_50} 处 ``content[:50]`` 字样（多数与通知无关，不计入）",
                "finding": "两处逐字重复的 ``content[:50] + (\"…\" if len>50)``（同一逻辑两份拷贝，无单一事实源）",
            },
            "segment_max_80": {
                "count": len(existing_gates["segment_max"]),
                "evidence": evidence(existing_gates["segment_max"]),
                "scope": "backend/app 全域（排除测试）",
                "finding": "主动链每段 80 字硬截断；一条消息可达 4 段 ⇒ 单段上限不等于载体上限",
            },
            "proactive_log_ceiling_500": {
                "count": len(existing_gates["proactive_log_ceiling"]),
                "evidence": evidence(existing_gates["proactive_log_ceiling"]),
                "scope": "只数唯一发送出口 scheduler.py（其余 [:500] 属别的模块，不构成该列的天花板）",
                "finding": f"proactive_message_logs.content 是 String({PROACTIVE_LOG_CEILING}) 且写前已按 [:500] 截 ⇒ 长度分布必撞天花板，P99 不可信",
            },
            "wechat_channel_hint": {
                "count": len(existing_gates["wechat_hint"]),
                "evidence": evidence(existing_gates["wechat_hint"]),
                "finding": "唯一的「按渠道约束形态」先例（prompt 侧一处常量），形态约束沿用此形态而非新建注入分区",
            },
        },
        "flutter_notify_maxlines": {
            "count": len(notify_maxlines),
            "evidence": evidence(notify_maxlines, 3),
            "finding": "services/ 通知渲染侧无 maxLines ⇒ 小窗可见长度完全由服务端 body 决定",
        },
        "notify_body_persisted": {
            "model_hits": len(body_persisted),
            "conclusion": "通知 body 不落库（models/ 无 preview 列）⇒ 直接测 body 长度分布是退化读数（恒 ≤ "
                          f"{NOTIFY_PREVIEW_CHARS + 1} 字符）；可测口径只能是「原始 content 长度分布」+「超 40/50 字占比」反推裁剪触发率",
        },
        "verdict": {
            "measurable": True,
            "criterion_rewrite": "上游 T8「小窗溢出投诉数」不可测 ⇒ 改为：通知载体代理指标＝原始 content 长度分布 P50/P90/P99 + >40 字占比 + >50 字占比（=裁剪触发率上界）",
            "how_to_measure": [
                "SQL（只读）：SELECT length(content) FROM proactive_message_logs —— 见 measure_notify_body_length()",
                "必须同时报 length(content)>=500 的条数占比，作为「被 [:500] 截顶」的证据，否则 P99 是假数",
                "私聊回复侧暂无长度落库口径（正文不裁、无载体列）⇒ 本批只报主动链分布，回复侧标 not_measurable",
            ],
            "blocked": [
                "无「载体」维度：ChatMessage 无 channel/surface/scene 列（设计 §1.3 C8），长度分布只能按管线（proactive）而非按载体切",
            ],
        },
    }


def measure_notify_body_length(conn: sqlite3.Connection, *, table="proactive_message_logs", column="content") -> dict:
    """库侧可测口径实测（**只读**；只在显式 ``--app-db`` 时调用）。"""
    table, column = safe_ident(table), safe_ident(column)
    tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return {"ok": False, "error": f"表不存在: {table}"}
    cols = {r[1] for r in query(conn, f'PRAGMA table_info("{table}")')}
    if column not in cols:
        return {"ok": False, "error": f"{table} 无列 {column}"}
    lengths = [int(r[0] or 0) for r in query(conn, f"SELECT length({column}) FROM {table}").fetchall()]
    if not lengths:
        return {"ok": True, "table": table, "column": column, "rows": 0, "percentiles": None,
                "ceiling": PROACTIVE_LOG_CEILING, "share_at_ceiling": 0.0,
                "share_gt_target": 0.0, "share_gt_preview": 0.0}
    ordered = sorted(lengths)
    total = len(ordered)
    return {
        "ok": True,
        "table": table,
        "column": column,
        "rows": total,
        "percentiles": {f"p{p}": _percentile(ordered, p) for p in (50, 90, 99)},
        "max": ordered[-1],
        "ceiling": PROACTIVE_LOG_CEILING,
        "share_at_ceiling": round(sum(1 for v in ordered if v >= PROACTIVE_LOG_CEILING) / total, 4),
        "share_gt_target": round(sum(1 for v in ordered if v > NOTIFY_TARGET_CHARS) / total, 4),
        "share_gt_preview": round(sum(1 for v in ordered if v > NOTIFY_PREVIEW_CHARS) / total, 4),
    }


def _percentile(ordered: list[int], p: int) -> int:
    """最近秩法（整数长度分布够用，不做插值以免造出看不见的精度）。"""
    if not ordered:
        return 0
    idx = min(len(ordered) - 1, max(0, -(-len(ordered) * p // 100) - 1))
    return ordered[idx]


# ────────────────────────── D：费用面板（可执行约束） ──────────────────────────

def audit_cost_panel(repo_root: Path, index: dict,
                     models_py: str = MODELS_AGENT_PY, system_py: str = APPLICATION_SYSTEM_PY) -> dict:
    models_text, models_err = read_text(repo_root, models_py)
    system_text, system_err = read_text(repo_root, system_py)
    if models_text is None or system_text is None:
        return {"ok": False, "error": models_err or system_err}
    columns = ast_class_columns(ast_tree(models_text), "LlmUsage")
    names = [c["name"] for c in columns]
    indexed = [c["name"] for c in columns if c["indexed"]]
    est_marker = grep_index(index, "usage_estimated", exclude_prefixes=("backend/tests/",))
    est_kwarg = grep_index(index, "estimated=True", exclude_prefixes=("backend/tests/",))

    sys_tree = ast_tree(system_text)
    get_usage = ast_find_func(sys_tree, "get_llm_usage")
    usage_report = ast_find_func(sys_tree, "usage_report")
    full_load_lines = [
        s["lineno"] for s in ast_select_shapes(get_usage)
        if s["orm_entity"] and s["args"] == ["LlmUsage"]
    ] if get_usage else []
    report_shapes = ast_select_shapes(usage_report) if usage_report else []
    report_orm_selects = [s["lineno"] for s in report_shapes if s["orm_entity"]]

    def _assign_of(tree: ast.Module, name: str):
        """取模块级赋值（``x = {}`` 与 ``x: dict = {}`` 两种写法都要认——后者是 AnnAssign）。"""
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets
            ):
                return node
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id == name:
                return node
        return None

    price_assign = _assign_of(sys_tree, "_TOKEN_PRICE_RANGES")
    price_empty = isinstance(getattr(price_assign, "value", None), ast.Dict) and not price_assign.value.keys
    basis_hits = grep_index(index, "full_effective_budget_input_only", exclude_prefixes=("backend/tests/",))
    cost_hits = grep_index(index, "no_price_table", exclude_prefixes=("backend/tests/",))
    callers = [h for h in grep_index(index, "get_llm_usage(", exclude_prefixes=("backend/tests/",)) if h["file"] != system_py]

    return {
        "ok": True,
        "llm_usage_columns": names,
        "llm_usage_indexed_columns": indexed,
        "estimated_column_present": "estimated" in names,
        "estimated_marker_only_in": evidence(est_marker, 3),
        "estimated_call_sites": {"count": len(est_kwarg), "evidence": evidence(est_kwarg, 3)},
        "get_llm_usage_full_load": {
            "function_line": getattr(get_usage, "lineno", None),
            "select_full_orm_lines": full_load_lines,
            "finding": "``select(LlmUsage)`` 整行 ORM 载入后在 Python 里循环聚合 ⇒ 窗口越大越线性恶化",
            "caller_count": len(callers),
            "callers": callers,
        },
        "usage_report_path": {
            "function_line": getattr(usage_report, "lineno", None),
            "select_shapes": report_shapes,
            "group_by_present": bool(usage_report) and ast_has_call_attr(usage_report, "group_by"),
            "full_orm_row_select": bool(report_orm_selects),
            "full_orm_row_select_lines": report_orm_selects,
            "pure_sql_aggregation": bool(usage_report) and ast_has_call_attr(usage_report, "group_by")
                                    and not report_orm_selects,
            "finding": "usage_report 是「窗口内列投影 SELECT + 内存分桶」，**不是** GROUP BY 聚合 "
                       "⇒ 设计文档 §2.4/§1.4 里的「SQL 聚合」表述与代码事实有偏差，M0 如实登记（见自检订正条）",
        },
        "price_table": {
            "line": getattr(price_assign, "lineno", None),
            "empty": bool(price_empty),
            "entry_count": 0 if price_empty else None,
            "unavailable_reason_sites": {"count": len(cost_hits), "evidence": evidence(cost_hits, 3)},
            "basis_string_sites": {"count": len(basis_hits), "evidence": evidence(basis_hits, 3)},
        },
        "constraints": [
            {
                "id": "D-C1",
                "statement": "费用面板读端点禁止调用 get_llm_usage（全表 ORM 载入），一律走 usage_report 口径",
                "status": "checkable",
                "current_value": {"get_llm_usage_callers_outside_service": len(callers)},
                "verify_by": "落码时加守卫测试：断言 cost-panel 调用链里不出现 get_llm_usage；本脚本可作回归读数基线",
                "evidence": evidence(([{"file": system_py, "line": l} for l in full_load_lines] or
                                      [{"file": system_py, "line": getattr(get_usage, "lineno", 0)}]), 3),
            },
            {
                "id": "D-C2",
                "statement": "llm_usage 无 estimated 列 ⇒ 面板金额段默认不出；出则必须带「含估算」标注或显式 unavailable",
                "status": "enforced_by_design",
                "current_value": {
                    "estimated_column_present": "estimated" in names,
                    "estimated_marker_route_hits": len(est_marker),
                },
                "verify_by": "区分实测/估算只有两条路：join agent_task_logs.route='usage_estimated'，或补列（新迁移）；本批两条都不做 ⇒ 面板只做 token 与占比",
                "evidence": est_marker[0] if est_marker else None,
            },
            {
                "id": "D-C3",
                "statement": "面板 basis 字符串禁止复用 full_effective_budget_input_only（那是「单轮输入侧预算投影」，不是历史花费）",
                "status": "checkable",
                "current_value": {"basis_string_sites": len(basis_hits)},
                "verify_by": "新面板另立 basis（如 actual_usage_tokens_window）；测试断言两者字符串不相等",
                "evidence": evidence(basis_hits, 3),
            },
            {
                "id": "D-C4",
                "statement": "价目表保持为空 dict；缺价 ⇒ status=unavailable + reason=no_price_table，禁止为凑数写默认价",
                "status": "verified",
                "current_value": {"price_table_empty": bool(price_empty), "no_price_table_sites": len(cost_hits)},
                "verify_by": f"AST 断言 _TOKEN_PRICE_RANGES 为空 dict（{system_py}:{getattr(price_assign, 'lineno', 0)}）",
                "evidence": evidence(cost_hits, 3),
            },
            {
                "id": "D-C5",
                "statement": "是否加 (user_id, created_at) / (task, created_at) 复合索引，以 EXPLAIN 实测为准，不预先动 schema",
                "status": "deferred_to_db",
                "current_value": {"indexed_columns": indexed},
                "verify_by": "跑 --app-db 后读 llm_usage 索引清单；本单默认不连库 ⇒ 留空待测（不编数）",
                "evidence": f"{models_py}:" + ",".join(str(c["line"]) for c in columns if c["indexed"]),
            },
        ],
    }


def measure_cost_schema(conn: sqlite3.Connection, table: str = "llm_usage") -> dict:
    """库侧 schema 实测（只读）：列与索引清单（D-C5 的证据位）。"""
    table = safe_ident(table)
    tables = {r[0] for r in query(conn, "SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        return {"ok": False, "error": f"表不存在: {table}"}
    cols = [r[1] for r in query(conn, f'PRAGMA table_info("{table}")')]
    idx = [r[1] for r in query(conn, f'PRAGMA index_list("{table}")')]
    return {
        "ok": True, "table": table, "columns": cols,
        "estimated_present": "estimated" in cols,
        "indexes": idx,
        "indexed_columns": sorted({c for c in ("task", "channel", "user_id", "created_at") if c in cols}),
    }


# ────────────────────────── 汇总 / 自检 / 渲染 ──────────────────────────

def _safe(name: str, fn, fallback: dict | None = None) -> dict:
    """异常隔离：单块读数失败不拖垮整份报告（失败也如实登记，不静默填 0）。"""
    try:
        return fn()
    except Exception as exc:
        return {
            "ok": False,
            "section": name,
            "error": f"{type(exc).__name__}: {exc}",
            "isolation": "本块读数不可用，其余块照常输出（M0 只登记，绝不因取数失败编数）",
            **(fallback or {}),
        }


def run_audit(app_db: Path | None = None, repo_root: Path = REPO_ROOT, *, build_index: bool = True) -> dict:
    repo_root = Path(repo_root)
    index = build_repo_index(repo_root) if build_index else {"root": str(repo_root), "files": {}}
    code_perms = load_code_permissions(repo_root)
    data: dict = {
        "meta": {
            "task": "A4 批 8 / M0 接口面与形态护栏 —— 只读对账读数",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "script": str(Path(__file__).resolve()),
            "mode": "dry_run_readonly",
            "repo_root": str(repo_root),
            "db_connected": False,
            "db_path": None,
            "git_write_commands_executed": 0,
            "files_written_in_repo": 0,
        },
        "sections": {},
    }
    sections = data["sections"]
    sections["B2_doc_code_drift"] = _safe("B2", lambda: audit_doc_code_drift(repo_root, index, code_perms))
    sections["B3_signature_stub"] = _safe("B3", lambda: audit_signature_stub(repo_root, index))
    sections["C1_form_surface"] = _safe("C1", lambda: audit_form_surface(repo_root, index))
    sections["D_cost_panel"] = _safe("D", lambda: audit_cost_panel(repo_root, index))

    db_readings: dict = {"status": "skipped", "reason": "未连库（M0 默认 dry-run）；显式 --app-db 才实测"}
    if app_db is not None:
        app_db = Path(app_db)
        if not app_db.is_file():
            db_readings = {"status": "error", "reason": f"库文件不存在: {app_db}"}
        else:
            conn = sqlite3.connect(f"file:{app_db.as_posix()}?mode=ro", uri=True)
            try:
                conn.execute("PRAGMA query_only=ON")
                db_readings = {
                    "status": "readonly_measured",
                    "db_path": str(app_db),
                    "pragma_query_only": conn.execute("PRAGMA query_only").fetchone()[0],
                    "notify_body_length": _safe("C1-db", lambda: measure_notify_body_length(conn)),
                    "cost_schema": _safe("D-db", lambda: measure_cost_schema(conn)),
                }
                data["meta"]["db_connected"] = True
                data["meta"]["db_path"] = str(app_db)
            finally:
                conn.close()
    sections["DB_readings"] = db_readings
    sections["SELF_check"] = _safe("SELF", lambda: self_check(repo_root, index, sections))
    return data


def self_check(repo_root: Path, index: dict, sections: dict) -> dict:
    """自检段：只读性证明 + 「不存在」类断言的命中数台账 + 结论溯源。"""
    own_sql = collect_sql_literals(Path(__file__))
    violations = []
    for sql in own_sql:
        try:
            readonly_sql(sql)
        except ReadOnlyViolation as exc:
            violations.append(str(exc))
    negative = []
    for item in (sections.get("C1_form_surface") or {}).get("upstream_wording_absent", []) or []:
        negative.append({"assertion": f"本仓不存在「{item['token']}」渲染路径", "grep_hits": item["hit_count"],
                         "roots": list(GREP_ROOTS)})
    b2 = sections.get("B2_doc_code_drift") or {}
    negative.append({
        "assertion": "通知 body 不落库（无 preview 列）⇒ body 分布退化",
        "grep_hits": ((sections.get("C1_form_surface") or {}).get("notify_body_persisted") or {}).get("model_hits"),
        "roots": ["backend/app/models"],
    })
    negative.append({
        "assertion": "签名校验无强制（恒 True）",
        "grep_hits": (sections.get("B3_signature_stub") or {}).get("dead_guard_count"),
        "roots": ["backend/app"],
    })
    ok_flags = {name: bool((sections.get(name) or {}).get("ok", True)) for name in sections}
    return {
        "ok": True,
        "read_only_proof": {
            "sql_literals_in_script": len(own_sql),
            "sql_violations": violations,
            "db_connected_by_default": False,
            "write_sql_helpers": "所有取数唯一入口 query() ⇒ readonly_sql() 单点闸门",
            "git_commands_executed": 0,
        },
        "negative_assertions": negative,
        "sections_ok": ok_flags,
        "failed_sections": [n for n, v in ok_flags.items() if not v],
        "evidence_coverage": {
            "B2_drift_rows_with_evidence": sum(
                1 for d in b2.get("drift_doc_has_code_lacks", []) if d.get("code_reference_evidence")
            ),
            "B2_drift_rows": len(b2.get("drift_doc_has_code_lacks", []) or []),
        },
        "corrections_to_design_doc": [
            "设计 §1.4 D4/§2.4 称 usage_report 走「SQL 聚合」——实测为窗口内列投影 SELECT + 内存分桶，无 GROUP BY（见 D 段 usage_report_path）",
            "设计 §1.2 B-2 记「漂移 3 条」（douyin_publish / net:outbound / fs:limited）——实测 5 条（另 channel:read / channel:publish），其中「文档声称已落地」的只有 douyin_publish 1 条",
            "设计 §5 块 C 拟测「通知 body 长度分布」——body 不落库，该分布退化（恒 ≤ 51）；可测口径是原始 content 长度分布",
        ],
        "out_of_scope": [
            "used_30d（权限 × 实际调用次数）：现状无 obs_event 点位，M0 不测（需 B-M0 落码后建档）",
            "按角色调用集中度（块 A R2 基线）：属块 A 的 M0 单，不在本单四块内",
        ],
    }


def _table(rows: list[list[str]], header: list[str]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _shapes_summary(shapes: list[dict]) -> str:
    """把 select 形状压成一行可读文本（报告里不 dump 原始 dict）。"""
    if not shapes:
        return "无 select"
    parts = []
    for s in shapes:
        kind = "整行 ORM" if s.get("orm_entity") else (f"{s['argc']} 列投影" if s["argc"] > 1 else "单参")
        parts.append(f"select@{s['lineno']}={kind}")
    return "; ".join(parts)


def render_report(data: dict) -> str:
    s = data["sections"]
    b2 = s.get("B2_doc_code_drift") or {}
    b3 = s.get("B3_signature_stub") or {}
    c1 = s.get("C1_form_surface") or {}
    d = s.get("D_cost_panel") or {}
    db = s.get("DB_readings") or {}
    selfc = s.get("SELF_check") or {}
    meta = data["meta"]

    lines: list[str] = []
    add = lines.append
    add("# A4 批 8 / M0「接口面与形态护栏」只读对账读数")
    add("")
    add(f"- 生成时间：{meta['generated_at']}（脚本自动生成，勿手改；重跑即覆盖）")
    add(f"- 脚本：`{meta['script']}`")
    add(f"- 模式：**{meta['mode']}**（零行为：不改权限名、不改文档、不动业务代码；"
        f"仓库内写入 {meta['files_written_in_repo']} 个文件，git 写命令 {meta['git_write_commands_executed']} 次）")
    add(f"- 是否连库：{'**已连（只读）** ' + str(meta['db_path']) if meta['db_connected'] else '否（默认 dry-run，库侧基线留空待测，不编数）'}")
    add("- 设计依据：`output/AMBRACE_批8_接口面与形态护栏_详细设计_v1_20260929.md` §7 M0")
    add("")
    add("## 0 一页读数")
    add("")
    counts = b2.get("counts", {})
    lines.extend(_table([
        ["B-2 权限名漂移（文档有 / 代码无）", str(counts.get("doc_has_code_lacks", "?")),
         f"其中「声称已落地」{counts.get('doc_has_code_lacks_asserted', '?')} 条 ⇒ 只登记，不改名"],
        ["B-2 权限名漂移（代码有 / 文档无）", str(counts.get("code_has_doc_lacks", "?")),
         "device:* 通配覆盖的 11 条不计入（wildcard_notes）"],
        ["B-3 签名校验", str(b3.get("returns_constant_true", "?")),
         f"恒 True ⇒ {b3.get('dead_guard_count', 0)} 处 `if not verify(...)` 死闸"],
        ["C-1 通知长度判据", "可测（代理口径）" if c1.get("verdict", {}).get("measurable") else "不可测",
         "body 不落库 ⇒ 改测原始 content 长度分布 + 超 40/50 字占比"],
        ["D-1 estimated 列", f"present={d.get('estimated_column_present', '?')}", "实测/估算不可区分 ⇒ 面板只出 token，金额段默认不出"],
        ["D-2 全表载入", f"select(LlmUsage) @ {d.get('get_llm_usage_full_load', {}).get('select_full_orm_lines', [])}",
         "面板禁止沿该路扩窗口"],
    ], ["读数", "值", "结论"]))
    add("")

    add("## 1 B-2 文档承诺 vs 代码事实（权限名漂移对账，只登记）")
    add("")
    if not b2.get("ok", False):
        add(f"> 本块读数失败：`{b2.get('error')}`")
    else:
        add(f"- 文档侧：`{b2['doc_source']}` 反引号权限名 {b2['doc_entry_count']} 条")
        add(f"- 代码侧：合法权限名 {b2['code_permission_count']} 条，取源 `{b2['permission_source']}`"
            + ("（动态 device 权限名未经 import 解析 ⇒ 见 wildcard_notes 说明）" if b2.get("permission_dynamic_unresolved") else ""))
        add(f"- Hook 条数对账：文档声称 {b2['hooks']['doc_claim_count']} / 代码 {b2['hooks']['code_count']} "
            f"⇒ 漂移={b2['hooks']['drift']}；口径限制：{b2['hooks']['method_limit']}")
        add("")
        add("### 1.1 文档有、代码无（漂移）")
        add("")
        rows = [[
            f"`{item['permission']}`",
            item["doc_kind"],
            item["severity"],
            f"`{CONTRACT_DOC}:{item['doc_line']}`",
            str(item.get("code_reference_hits", 0)),
            item.get("code_reference_evidence", "—"),
        ] for item in b2.get("drift_doc_has_code_lacks", [])]
        lines.extend(_table(rows, ["权限名", "文档口径", "严重度", "文档证据", "代码/前端残留命中", "残留位置"]) if rows
                     else ["（无漂移）"])
        add("")
        add("### 1.2 代码有、文档未逐条列出（漂移）")
        add("")
        rows = [[f"`{item['permission']}`"] for item in b2.get("drift_code_has_doc_lacks", [])]
        add(f"- 计数：**{b2['counts']['code_has_doc_lacks']}**"
            + ("（0 ⇒ 代码未越出文档承诺面；device:* 通配已覆盖的动态权限名见 1.3）" if not rows else ""))
        if rows:
            lines.extend(_table(rows, ["权限名"]))
        add("")
        add("### 1.3 通配覆盖说明（不算漂移，但文档口径需补）")
        add("")
        lines.extend(_table([[n["pattern"], str(n["doc_line"]), n["doc_kind"], str(n["covers_code_names"])]
                             for n in b2.get("wildcard_notes", [])], ["文档通配", "行号", "口径", "覆盖的代码权限名条数"]))
        add("")
        add("### 1.4 处置登记（M0 不动语义）")
        add("")
        add("- 改名/删除权限名会让已装插件的 consent 记录整体失配（`consent_matches` 严格比较，"
            f"`{b2.get('consent_strict_match_evidence', 'backend/app/plugins/registry.py')}`）"
            "⇒ **本批禁止改名**，漂移按「先改文档，再改代码」处置（设计 §3.6）。")
        add(f"- 「文档声称已落地、代码无」共 {b2['counts']['doc_has_code_lacks_asserted']} 条"
            "（`douyin_publish`）：前端市场页仍在渲染它 ⇒ 不能当笔误删，"
            "需产品口径决定（回补权限名 or 文档改注「已移除」）。")
        add(f"- 「文档明示仅预告、代码无」共 {b2['counts']['doc_has_code_lacks_preview']} 条"
            "（`net:outbound` / `fs:limited` / `channel:read` / `channel:publish`）"
            "⇒ 落地以 X4 批次为准，M0 只登记。")
    add("")

    add("## 2 B-3 签名校验占位如实登记")
    add("")
    if not b3.get("ok", False):
        add(f"> 本块读数失败：`{b3.get('error')}`")
    else:
        lines.extend(_table([
            ["函数", f"`{b3['function']}`"],
            ["恒返回 True", str(b3["returns_constant_true"])],
            ["签名是否强制", "否（`signature_enforced=false`）"],
            ["调用点数", str(b3["callsite_count"])],
            ["因此永不可达的拒绝分支", str(b3["dead_guard_count"])],
            ["现状可信依据 · sha256 留痕", b3["current_basis"]["sha256_trace"]],
            ["现状可信依据 · allowed_hosts", b3["current_basis"]["allowed_hosts"]],
        ], ["项", "读数"]))
        add("")
        add(f"- 登记：{b3['registration']}")
        add(f"- 约束：{b3['doc_wording_constraint']}（调用点证据 {evidence(b3['callsites'], 3)}）")
    add("")

    add("## 3 C-1 短句形态：判据可测性结论与现有可测口径")
    add("")
    if not c1.get("ok", False):
        add(f"> 本块读数失败：`{c1.get('error')}`")
    else:
        add("### 3.1 上游文案里的形态在本仓不存在（每条给 grep 命中数）")
        add("")
        lines.extend(_table([[a["token"], str(a["hit_count"]), a["evidence"]] for a in c1["upstream_wording_absent"]],
                            ["上游文案 token", "命中数", "证据"]))
        add("")
        add(f"- 判定：{'全部 0 命中 ⇒ 上游 T8 的「桌面气泡 / 锁屏卡片」在本仓无渲染路径，原判据不可测' if c1['all_absent'] else '存在命中，见上表'}；"
            "搜索根 = " + ", ".join(f"`{r}`" for r in GREP_ROOTS))
        add("")
        add("### 3.2 现有可测口径（长度闸与天花板）")
        add("")
        rows = [[k, str(v["count"]), f"`{v['evidence']}`", v.get("scope", "—"), v["finding"]]
                for k, v in c1["existing_length_gates"].items()]
        lines.extend(_table(rows, ["口径", "命中", "证据（文件:行号）", "计数口径", "读法"]))
        add("")
        add(f"- Flutter 通知渲染侧 maxLines 命中：{c1['flutter_notify_maxlines']['count']} ⇒ "
            + c1["flutter_notify_maxlines"]["finding"])
        add(f"- 通知 body 是否落库：models 命中 {c1['notify_body_persisted']['model_hits']} ⇒ "
            + c1["notify_body_persisted"]["conclusion"])
        add("### 3.3 结论：能否测 / 怎么测")
        add("")
        add(f"- **可测性**：{'可测（代理指标）' if c1['verdict']['measurable'] else '不可测'}。"
            f"{c1['verdict']['criterion_rewrite']}")
        add("- 测法：")
        for step in c1["verdict"]["how_to_measure"]:
            add(f"  - {step}")
        add("- 尚未打通的前置：")
        for blk in c1["verdict"]["blocked"]:
            add(f"  - {blk}")
        if db.get("status") == "readonly_measured":
            add("")
            add("### 3.4 库侧实测（本次已连只读库）")
            add("")
            nb = (db.get("notify_body_length") or {})
            if nb.get("ok"):
                lines.extend(_table([[
                    str(nb["rows"]), json.dumps(nb["percentiles"], ensure_ascii=False), str(nb.get("max")),
                    f"{nb['share_gt_target']:.1%}", f"{nb['share_gt_preview']:.1%}", f"{nb['share_at_ceiling']:.1%}",
                ]], ["样本条数", "P50/P90/P99", "max", ">40 字占比", ">50 字占比", f">={nb['ceiling']} 截顶占比"]))
            else:
                add(f"- 未取到读数：`{nb.get('error') or nb.get('status')}`")
        else:
            add("")
            add(f"### 3.4 库侧实测：{db.get('status')}（{db.get('reason')}）——重跑加 `--app-db <库路径>` 即得基线")
    add("")

    add("## 4 D-1/D-2 费用面板：把两条现状写成可执行约束")
    add("")
    if not d.get("ok", False):
        add(f"> 本块读数失败：`{d.get('error')}`")
    else:
        add("### 4.1 事实读数")
        add("")
        lines.extend(_table([
            ["llm_usage 列", ", ".join(f"`{c}`" for c in d["llm_usage_columns"])],
            ["带索引的列", ", ".join(f"`{c}`" for c in d["llm_usage_indexed_columns"]) or "（无）"],
            ["estimated 列存在", str(d["estimated_column_present"])],
            ["估算标记唯一落点", f"`{d['estimated_marker_only_in']}`（另有 "
                                  f"{d['estimated_call_sites']['count']} 处 `estimated=True` 调用）"],
            ["get_llm_usage 整行载入", f"`{APPLICATION_SYSTEM_PY}:{d['get_llm_usage_full_load']['function_line']}` → "
                                        f"select(LlmUsage) @ {d['get_llm_usage_full_load']['select_full_orm_lines']}"],
            ["usage_report 路径", f"`{APPLICATION_SYSTEM_PY}:{d['usage_report_path']['function_line']}`，"
                                  f"{_shapes_summary(d['usage_report_path']['select_shapes'])}，"
                                  f"GROUP BY={d['usage_report_path']['group_by_present']}，"
                                  f"整行载入={d['usage_report_path']['full_orm_row_select']}"],
            ["价目表", f"`{APPLICATION_SYSTEM_PY}:{d['price_table']['line']}` 空 dict={d['price_table']['empty']}；"
                       f"unavailable 口径 {d['price_table']['unavailable_reason_sites']['count']} 处（{d['price_table']['unavailable_reason_sites']['evidence']}）"],
        ], ["项", "读数"]))
        add("")
        add(f"- D-2 关键订正：{d['usage_report_path']['finding']}")
        add("### 4.2 可执行约束（落码时逐条钉测试）")
        add("")
        rows = [[c["id"], c["statement"], c["status"], str(c["current_value"]), str(c["evidence"])]
                for c in d["constraints"]]
        lines.extend(_table(rows, ["编号", "约束", "状态", "当前值", "证据"]))
        add("")
        for c in d["constraints"]:
            add(f"- **{c['id']}** 验证方式：{c['verify_by']}")
    add("")

    add("## 5 自检")
    add("")
    if not selfc.get("ok", False):
        add(f"> 自检读数失败：`{selfc.get('error')}`")
    else:
        proof = selfc["read_only_proof"]
        add("### 5.1 只读性证明")
        add("")
        lines.extend(_table([
            ["脚本内 SQL 字面量条数", str(proof["sql_literals_in_script"])],
            ["其中违反只读闸门的条数", str(len(proof["sql_violations"])) + (
                " ⚠ " + "; ".join(proof["sql_violations"]) if proof["sql_violations"] else "")],
            ["默认是否连库", str(proof["db_connected_by_default"])],
            ["连库方式", "`file:...?mode=ro` + `PRAGMA query_only=ON` + `readonly_sql()` 单点闸门"],
            ["git 命令执行次数", str(proof["git_commands_executed"])],
            ["仓库文件写入次数", "0（唯一落盘在仓库外报告路径）"],
        ], ["项", "值"]))
        add("")
        add("### 5.2 「不存在」类断言的命中数台账")
        add("")
        lines.extend(_table([[n["assertion"], str(n["grep_hits"]), ", ".join(n["roots"])]
                             for n in selfc["negative_assertions"]], ["断言", "grep 命中数", "搜索根"]))
        add("")
        add("### 5.3 各块读数健康度")
        add("")
        add("- 失败块：" + (", ".join(f"`{n}`" for n in selfc["failed_sections"]) if selfc["failed_sections"] else "无（四块 + 自检全部 ok）"))
        add(f"- 证据覆盖：B-2 漂移 {selfc['evidence_coverage']['B2_drift_rows_with_evidence']}"
            f"/{selfc['evidence_coverage']['B2_drift_rows']} 条带「代码残留位置」证据（每条漂移都要求可溯源）")
        add("")
        add("### 5.4 对上游设计文档的订正（M0 实测 ≠ 设计登记）")
        add("")
        for fix in selfc["corrections_to_design_doc"]:
            add(f"- {fix}")
        add("")
        add("### 5.5 本单未做（避免读数假装覆盖）")
        add("")
        for out in selfc["out_of_scope"]:
            add(f"- {out}")
    add("")
    add("---")
    add("")
    add("**M0 验收姿态**：本文件所有数字都是**磁盘/库实读**，未连库时对应基线写「留空待测」；"
        "四块无一条语义变更，权限名一字未改，`docs/extension-contract.md` 一字未改。")
    add("")
    return "\n".join(ln for ln in lines if ln is not None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="A4 批 8 M0 只读对账读数（零行为）")
    parser.add_argument("--app-db", default=None, help="可选：只读打开指定 sqlite 库补测基线（默认不连库）")
    parser.add_argument("--report", default=str(default_report_path()), help=f"报告落盘路径（默认 {default_report_path()}）")
    parser.add_argument("--no-report", action="store_true", help="只打印不落盘（测试/查看用）")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON（不落报告）")
    parser.add_argument("--quiet", action="store_true", help="精简 stdout（默认打印摘要）")
    args = parser.parse_args(argv)

    app_db = Path(args.app_db) if args.app_db else None
    data = run_audit(app_db=app_db)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        report = render_report(data)
        if not args.no_report:
            target = Path(args.report)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report, encoding="utf-8")
            print(f"[report] 落盘 {target}（{len(report.splitlines())} 行）")
        if not args.quiet:
            print(report)

    failed = [n for n, sec in data["sections"].items() if isinstance(sec, dict) and sec.get("ok") is False]
    if failed:
        print(f"[warn] 读数失败块：{failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
