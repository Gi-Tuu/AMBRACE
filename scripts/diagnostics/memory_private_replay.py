# -*- coding: utf-8 -*-
"""A30 私有留底离线复算闭环（零 LLM、零计费、零网络；**不连生产库**）。

补的是哪一个洞（交接单 §1）：10-14 已经把「完整私有留底」补齐（`make_private_evidence`：
完整动作原文 `actions_full` / 回复全文 `reply_text_full` / 整段输入 `ctx_messages` / 四维指纹），
但**还没有**「读留底 → 按原判据重新判分 → 与存值及普通 rows 对账」的可运行工具。
`memory_action_eval.py --mode recount` 只复算**存下的结构/签名**，不从原文重走判据。
本工具就是那条缺的腿：**从完整原文重新走判据**并核对原记录。

两个子命令：
  audit   —— 先固定输入清单（manifest：匿名文件标签/SHA256/角色/可审计字段）＋ 同源性**逐维逐跑**盘点。
  replay  —— 读完整留底，按原 `parse_actions`/`extract_memory_channel`/`j1_verdict` 重放判分，
             给逐跑「原判分 → 现算判分 → 一致/差异/缺失原因」的匿名结果，并与普通 rows 逐跑对账。

**四桶分母 + 双栏**（2026-10-15 主审返工修复①）：每条复算结果进且只进一桶
`effective`（唯一键·可比，进判分一致分母）/ `conflict`（重复键，只点名不作证据）/
`missing`（缺判据/缺原文/缺存值，不可复算）/ `incomparable`（export/未知stage/截断/类型非法/
sent_fp 错配，不可复算）；判分字段一致与**证据完整合格**（`evidence_ok`，全维自证）**分栏**，
绝不把 unknown/冲突包装成完整重放成功。

四条不可越过的证据边界（交接单 §4）：
1. **分维不互顶**：题集来源（`case_source_fp`）/ 输入 stable（`prompt_fp_stable`）/ 导出尺子
   （`export_evaluator_source_fp`）/ 当前尺子（`evaluator_source_fp`）/ 跑次 / 完整性 是不同维度，
   缺哪维写 `unknown`＋原因码，绝不拿另一维填补。
2. **复用原判据**：动作解析、锚点日期语义（`resolve_expect_date`/`parse_actions(base_date=anchor)`）、
   `j1_verdict` 及作废语境豁免，一律 import 原实现，本文件**不另造判据**。
   唯一新增的是「读哪里的原文来喂判据」与「怎么对账」，判据本体一个字不改。
3. **原文不外泄**：默认 stdout / 常规报告只含匿名题号、聚合、指纹维度状态与原因码；
   stage/run/err **枚举化**（`stage_other`/`run_other`/`err_other`），why 正文只进 detail-out，
   完整原文 / cid↔匿名映射 / 文件路径映射只在**显式** `--detail-out`/`--map-out`（仓库外）时落盘。
4. **零在线模型**：全程不 import 模型入口、不发起任何网络；DeepSeek 当日不可用亦无关——本工具不取样。

隔离（10-14 事故必须变成执行约束）：`parse_actions`/`extract_memory_channel` 会经
`app.memory.__init__ → service → app.db.database → app.db.engine` 在**导入那一刻**建 engine 并按当时的
`settings.database_url` 绑死。本工具在**任何 app 导入之前**（`_sandbox_db_env()`）就把
`DATABASE_URL`/`CHROMA_PERSIST_DIR`/`TEMP` 钉进一次性临时目录，绝不指向生产库；
沙箱目录带 atexit 生命周期（退出先 dispose 已绑 engine、删目录、还原环境变量）；
进程里若已有**非沙箱**预绑定 engine ⇒ 当场拒绝执行（隔离不能只改 settings）。本工具全程只读留底、
从不查询数据库。哨兵反证见 `backend/tests/test_memory_private_replay.py`。
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BACKEND = REPO_ROOT / "backend"

UNKNOWN = "unknown"
EXIT_OK = 0
EXIT_BAD = 2          # 输入/参数非法
EXIT_CONFLICT = 3     # 重复键/rows 内重复冲突：证据自相矛盾，不给干净退出码（修复①）
EXIT_REPLAY = 7       # 复算/写盘失败（对齐 memory_action_eval 的 PRIVATE_EVIDENCE_EXIT）

# ── 原因码（匿名结果里只出现这些，不带任何正文）────────────────────────────────────
R_OK = "ok"
R_MATCH = "match"                      # 现算判分 == 存下判分（逐字段）
R_DIFF_PASS = "diff_pass"              # 过/不过 与存值不同
R_DIFF_ERR = "diff_err"                # 错因不同（同过不过）
R_DIFF_FIELD = "diff_field"            # why/exemptions/dates_seen/actions_seen 有逐字差异
R_MISSING_FIELD = "missing_field"      # 缺复算必需字段（含 pass 不是原生 bool 的缺读数腿）
R_MISSING_REPLY = "missing_reply"      # generate/judge 缺回复原文且无动作留底，无法重走判据
R_MISSING_JUDGE = "missing_criteria"   # 缺 expect/time_anchor 判据（任一缺失都算缺）
R_STALE_TRUNC = "truncated_legacy"     # 旧截断样本，只能当下界/不可无损重放
R_UNKNOWN_STAGE = "unknown_stage"
R_DUP = "duplicate_key"                # 重复 (stage,cid,run)：所有行进 conflict 桶
R_EXPORT_ONLY = "export_input_only"    # stage=export：只核输入/来源，无回复无动作
R_VERDICT_TYPE_INVALID = "verdict_type_invalid"  # pass 非原生 bool（"False"/1…），绝不强转
R_INPUT_FP_DIFF = "input_fp_diff"      # 现算 stable ≠ 留存 stable
R_INPUT_FP_MATCH = "input_fp_match"
R_INPUT_FP_UNKNOWN = "input_fp_unknown"
R_SENT_FP_MISMATCH = "sent_fp_mismatch"
R_SENT_FP_NA = "sent_fp_not_applicable"          # 留底没带 sent_fp，不拦
R_SELFINC = "self_inconsistent"        # 回复原文重放出的动作 与 留底 actions_full 不自洽
R_RULER_RECOMPUTED = "recomputed_with_current_ruler"  # 换当前尺子复算（历史尺子≠现算），不得称原尺子复现
R_PARTIAL_FIELDS = "partial_fields"    # 被比字段含 unknown/not_compared ⇒ 证据不完整（第二轮：落 incomparable，不进有效分母）
R_CRITERIA_MISMATCH = "criteria_mismatch"  # 第二轮①：实际执行判据（expect/time_anchor/anchor）与已验证题面不同源
R_ROWS_CONFLICT = "rows_conflict"      # rows 文件内重复 (cid,run)，绝不静默覆盖
R_ROWS_AMBIGUOUS = "rows_ambiguous"    # 第二轮④：folded rows 无唯一匹配证据，不猜配
R_NO_DETAIL = "detail_not_requested"   # 详细输出未显式启用（不落原文）

# ── 分桶词表 ────────────────────────────────────────────────────────────────────
B_EFFECTIVE = "effective"
B_CONFLICT = "conflict"
B_MISSING = "missing"
B_INCOMPARABLE = "incomparable"

# ── 公共输出枚举词表（修复③：原始值绝不回显，其余一律安全码）──────────────────────
_STAGE_VOCAB = {"generate", "judge", "export"}
_STAGE_OTHER = "stage_other"
_ERR_VOCAB = {"", "E3", "E4", "E_unannotated", "E_anchor_stale", "E_gen_failed"}
_ERR_OTHER = "err_other"

# 字段对账覆盖的判分字段（修复②）：pass/err 决定可比性，其余逐字比
_CMP_FIELDS = ("why", "exemptions", "dates_seen", "actions_seen", "action_raw")

# 证据完整合格（evidence_ok）要求全部满足：
#   bucket==effective ∧ verdict_match is True ∧ fields 全部 "match"
#   ∧ fp.input_stable==input_fp_match ∧ fp.case_source=="verified"
#   ∧ fp.evaluator_vs_now=="same" ∧ self_consistent 不为 False
# 任一维 unknown/错配 ⇒ evidence_ok=False（报表不得把 unknown 包装成完整重放成功）。


# ════════════════════════ 隔离：任何 app 导入之前先钉沙箱 ════════════════════════
_SANDBOX = None
_SAVED_ENV = {}


def _prebound_engine_db_paths() -> list:
    """取**已导入**的 `app.db.engine`/`app.db.database` 里 engine 绑定的库路径（可能多个）。"""
    paths = []
    for name in ("app.db.engine", "app.db.database"):
        mod = sys.modules.get(name)
        eng = getattr(mod, "engine", None) if mod is not None else None
        if eng is None:
            continue
        try:
            url = str(eng.url)                      # AsyncEngine.url 透传
        except Exception:  # noqa: BLE001
            continue
        if not url.startswith("sqlite"):
            paths.append(url.split("://", 1)[0] + "://<非sqlite>")   # 非 sqlite 一律不可信
            continue
        fp = url.split("///", 1)[-1].split("?", 1)[0]
        if fp and fp != ":memory:":
            paths.append(fp)
    return paths


def _db_path_is_sandboxed(db_path: str) -> bool:
    """库路径是否在系统临时目录或本沙箱之下（pytest 沙箱在临时目录下 ⇒ 接受）。"""
    p = os.path.normcase(os.path.realpath(os.path.abspath(db_path)))
    tmp = os.path.normcase(os.path.realpath(os.path.abspath(tempfile.gettempdir())))
    if p.startswith(tmp + os.sep):
        return True
    for var in ("TEMP", "TMP", "TMPDIR"):
        v = os.environ.get(var)
        if v:
            vp = os.path.normcase(os.path.realpath(os.path.abspath(v)))
            if p.startswith(vp + os.sep):
                return True
    if _SANDBOX is not None:
        sb = os.path.normcase(os.path.realpath(str(_SANDBOX)))
        if p == sb or p.startswith(sb + os.sep):
            return True
    return False


def _cleanup_sandbox():
    """atexit 收尾（修复⑥）：dispose 已绑到本沙箱的 engine → 删沙箱目录 → 还原环境变量。"""
    global _SANDBOX
    if _SANDBOX is None:
        return
    root = str(_SANDBOX)
    try:
        import app.db.engine as eng_mod  # noqa: PLC0415
        try:
            eng_mod.engine.sync_engine.dispose()   # 释放连接池句柄（Windows 删目录前置）
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    import shutil  # noqa: PLC0415 —— 收尾阶段局部导入，避免模块级依赖
    shutil.rmtree(root, ignore_errors=True)
    for k, v in _SAVED_ENV.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _SAVED_ENV.clear()
    _SANDBOX = None


def _sandbox_db_env() -> str:
    """把 DATABASE_URL/CHROMA/TEMP 钉进一次性临时目录，**在 app.* 之前**调用。

    为什么必须在这儿（而不是靠调用方）：`parse_actions`/`extract_memory_channel` 一 import 就会经
    `app.memory.__init__` 拽起 `app.db.engine`，engine 于**导入那一刻**按当时 `settings.database_url`
    绑死并按进程缓存。后补 settings 已经晚了（10-14 事故）。本工具从不查询数据库，但即便有朝一日
    有人误加一句查询，也只会打到这份一次性临时库，绝不会碰到生产。

    **预绑定防线（修复⑥）**：创建沙箱前若进程里已有非沙箱 engine ⇒ 当场拒执行。
    隔离不能只改 settings 宣称——生产库路径不在临时目录下，必被拒。
    """
    global _SANDBOX
    if _SANDBOX is not None:
        return _SANDBOX
    for fp in _prebound_engine_db_paths():
        if not _db_path_is_sandboxed(fp):
            raise RuntimeError("拒绝执行：进程里已有非沙箱预绑定 DB engine，隔离不能只改 settings")
    root = Path(tempfile.mkdtemp(prefix="ambrace_replay_sandbox_"))
    url = "sqlite+aiosqlite:///" + str(root / "replay_never_query.db").replace(os.sep, "/")
    for k, v in (("DATABASE_URL", url),
                 ("CHROMA_PERSIST_DIR", str(root / "chroma")),
                 ("TEMP", str(root)), ("TMP", str(root)), ("TMPDIR", str(root))):
        _SAVED_ENV[k] = os.environ.get(k)
        os.environ[k] = v
    # 双保险：若 app.config 已被别的途径导入，也把 settings 指向沙箱。
    try:
        import app.config as cfg  # noqa: PLC0415
        cfg.settings.database_url = url
        cfg.settings.chroma_persist_dir = str(root / "chroma")
    except Exception:  # noqa: BLE001 —— 沙箱尽力而为，绝不因它崩溃或回显原文
        pass
    atexit.register(_cleanup_sandbox)
    _SANDBOX = str(root)
    return _SANDBOX


def _load_eval():
    """按路径加载既有评测器，复用其**原判据**（`j1_verdict`/`extract_memory_channel`/指纹函数）。

    用 `importlib` 走文件路径而不是 `import memory_action_eval`：后者顶部会把 `BACKEND` 塞进
    `sys.path` 并在函数内 import `app.*`；我们先钉沙箱再加载，确保任何 engine 都指向沙箱。
    本文件**绝不修改**判据本体，只是把原文喂进同一套判据。
    """
    _sandbox_db_env()
    import importlib.util  # noqa: PLC0415
    spec = importlib.util.spec_from_file_location(
        "_memact_eval_reused_by_replay", str(HERE / "memory_action_eval.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EV = None


def _ev():
    global EV
    if EV is None:
        EV = _load_eval()
    return EV


# ════════════════════════ 隐私：匿名化 + 仓库外闸 ════════════════════════
def repo_root_dir() -> str:
    return os.path.normcase(os.path.realpath(str(REPO_ROOT)))


_ANON_LABEL = "（匿名）"


def _fname(p) -> str:
    """**回显一律匿名**（第二轮⑤）：basename 仍可能含人名/题号/载荷，绝不回报。

    保留函数只为不打断既有调用点；返回值恒为匿名标签，正文/文件名/异常原文一概不出现。
    """
    return _ANON_LABEL


def guard_outside_repo(path: str, what: str) -> str:
    """详细原文 / 反查映射必须落**仓库外**（abspath＋realpath 双口径，拦软链接回流）。

    报错信息只给**匿名标签**（第二轮⑤：不回显 basename，也不回显全路径）。
    """
    p = str(path or "").strip()
    if not p:
        raise ValueError("%s 给了空路径 ⇒ 无处可落，拒写" % what)
    abs_p = os.path.abspath(os.path.expanduser(p))
    real_p = os.path.realpath(abs_p)
    root = repo_root_dir()
    for label, cand in (("规范化路径", os.path.normcase(abs_p)),
                        ("真实路径（跟随软链接）", os.path.normcase(real_p))):
        if cand == root or cand.startswith(root + os.sep):
            raise ValueError("%s 必须在仓库外（软链接落回仓库内部同样拒）：%s 落在仓库之内（路径已匿名）"
                             % (what, label))
    parent = os.path.dirname(real_p)
    if not os.path.isdir(parent):
        raise ValueError("%s 的目录不存在，拒写（不创建也不谎称已保存，路径已匿名）" % what)
    return real_p


def _write_text_guarded(path: str, text: str, what: str) -> str:
    """任何报表落盘都走仓库外闸（修复③：audit --out 与 replay --out 同一闸，拒绝即不落盘）。"""
    real = guard_outside_repo(path, what)
    with open(real, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    return real


class Anon:
    """cid → 匿名题号（A001…）；文件 → 匿名标签（F001…）。
    映射是私有派生物，只在显式 `--map-out` 时落盘（`{"cids": {...}, "files": {"F001": path}}`）。"""

    def __init__(self):
        self._m = {}
        self._f = {}
        self._fk = {}

    def key(self, cid) -> str:
        c = str(cid)
        if c not in self._m:
            self._m[c] = "A%03d" % (len(self._m) + 1)
        return self._m[c]

    def file_key(self, path) -> str:
        p = str(path)
        if p not in self._fk:
            self._fk[p] = "F%03d" % (len(self._fk) + 1)
            self._f[self._fk[p]] = p
        return self._fk[p]

    def dump(self):
        return {"cids": dict(self._m), "files": dict(self._f)}


def _write_jsonl_strict(path: str, rows) -> int:
    """严格 JSON 落盘：拒绝 NaN/Infinity，拒绝有损 `default=str`；写前不回显内容。"""
    real = guard_outside_repo(path, "详细输出")
    lines = []
    for rec in rows:
        try:
            blob = json.dumps(rec, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if json.loads(blob) != rec:
                raise ValueError("round-trip 不相等")
        except (TypeError, ValueError):
            # 只报类型与键坐标，绝不 repr(e)（可能带原文）
            raise RuntimeError("详细输出某行无法无损序列化 ⇒ 整批拒写（键=%s，异常类型见上文隐去）"
                               % sorted(rec.keys())) from None
        lines.append(blob)
    if not lines:
        return 0
    try:
        with open(real, "w", encoding="utf-8", newline="\n") as f:
            for ln in lines:
                f.write(ln + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        raise RuntimeError("详细输出写盘失败 ⇒ **可能已写入部分行（失败点未知）**，先核对现有行数再复跑："
                           % ()) from None
    return len(lines)


# ════════════════════════ 输入清单 / 同源性分维 ════════════════════════
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class _BadJsonConst(ValueError):
    """携常量名的轻量异常（常量名只可能是 JSON 语法词 NaN/Infinity/-Infinity，非行内容）。"""


def _reject_json_const(name):
    """NaN/Infinity 不是合法 JSON（修复⑥「写出严格不能替代读入严格」）：解析期就拒。"""
    raise _BadJsonConst(str(name))


def load_jsonl(path: str) -> list:
    """严格读入：NaN/Infinity 拒；每行必须是 dict；语法错只报行号与常量名，**不回显行内容**。"""
    if not os.path.isfile(path):
        raise FileNotFoundError("输入文件不存在（文件名已匿名，不回显）")
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for i, ln in enumerate(f, 1):
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln, parse_constant=_reject_json_const)
            except _BadJsonConst as e:
                raise ValueError("第 %d 行含非法 JSON 常量 %s" % (i, e)) from None
            except ValueError:
                raise ValueError("第 %d 行 JSON 语法错误" % i) from None
            if not isinstance(obj, dict):
                raise ValueError("第 %d 行不是 JSON 对象" % i)
            rows.append(obj)
    return rows


# 字段角色（用于 manifest 的「能审计的字段」列与维度盘点）
_DIM_FIELDS = {
    "input_stable": ("prompt_fp_stable",),
    "input_raw": ("prompt_fp",),
    "case_source": ("case_source_fp",),
    "export_evaluator": ("export_evaluator_source_fp",),
    "evaluator_current": ("evaluator_source_fp",),
    "ctx_full": ("ctx_messages",),
    "actions_full": ("actions_full",),
    "reply_full": ("reply_text_full",),
    "criteria": ("expect", "time_anchor"),
    "stored_verdict": ("pass", "err"),
}


def _has(rec: dict, key: str) -> bool:
    v = rec.get(key)
    return key in rec and v is not None and v != UNKNOWN


def classify_artifact(path: str, rows: list) -> dict:
    """按首个对象的键，判定一份产物的**角色**与能审计的维度（不看正文）。"""
    keys = set()
    for r in rows[:50]:
        if isinstance(r, dict):
            keys |= set(r.keys())
    role = "unknown"
    if {"stage", "actions_full"} <= keys or {"reply_text_full", "ctx_messages"} <= keys:
        role = "private_evidence"
    elif {"ctx_messages", "expect"} <= keys and "pass" not in keys:
        role = "prompts"
    elif {"pass", "sig"} <= keys or {"pass", "actions_seen"} <= keys:
        role = "rows"
    elif {"cid", "expect"} <= keys and "pass" not in keys:
        role = "dataset"
    elif {"cid", "solvability"} <= keys:
        role = "solvability"
    dims = {d: sum(1 for r in rows if isinstance(r, dict) and any(_has(r, k) for k in ks))
            for d, ks in _DIM_FIELDS.items()}
    return {"role": role, "n_rows": len(rows), "n_keys": len(keys),
            "dims_present_rows": dims,
            "sample_keys": sorted(keys)[:40]}


def build_manifest(paths: list, anon: "Anon | None" = None) -> list:
    """manifest：匿名文件标签 / SHA256 / 角色 / 来源批次（匿名）/ 能审计字段。输入只读。

    真实文件名/路径不进 manifest（公共报表列只有 F001 标签＋角色＋SHA256 前 16）。
    """
    out = []
    for p in paths:
        ap = os.path.abspath(os.path.expanduser(p))
        tag = anon.file_key(ap) if anon is not None else _fname(ap)
        batch = ("B" + str(abs(hash(Path(ap).parent.name)) % 1000)) if anon is not None \
            else Path(ap).parent.name
        if not os.path.isfile(ap):
            out.append({"file": tag, "path": ap, "exists": False, "sha256": None, "role": "missing"})
            continue
        try:
            rows = load_jsonl(ap)
        except Exception as e:  # noqa: BLE001 —— 只报类型，不回显可能含原文的 repr
            out.append({"file": tag, "path": ap, "exists": True, "sha256": sha256_file(ap),
                        "role": "unreadable(%s)" % type(e).__name__})
            continue
        meta = classify_artifact(ap, rows)
        out.append({"file": tag, "path": ap, "exists": True, "sha256": sha256_file(ap),
                    "batch": batch, "role": meta["role"], "n_rows": meta["n_rows"],
                    "audit_dims": {d: n for d, n in meta["dims_present_rows"].items() if n}})
    return out


# ════════════════════════ 公共输出枚举（修复③）════════════════════════
def _enum_stage(v) -> str:
    s = str(v or "")
    return s if s in _STAGE_VOCAB else _STAGE_OTHER


def _enum_run(v):
    """run 是 int（或纯数字串）显示数字，否则 `run_other`。"""
    if isinstance(v, bool):
        return "run_other"
    if isinstance(v, int):
        return v
    s = str(v or "")
    return int(s) if (s.isdigit() and len(s) < 12) else "run_other"


def _enum_err(v) -> str:
    s = str(v or "")
    return s if s in _ERR_VOCAB else _ERR_OTHER


def _agg_int(v):
    """聚合字段类型白名单（第二轮⑤）：只接受**原生 int**（非 bool），否则 None（渲染为 unknown）。

    rows 文件是外来输入，字段值可以是任意文本；公共报表不得把任意文本直接渲染。
    """
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _agg_bool(v):
    """聚合布尔白名单：只接受**原生 bool**，否则 None（渲染为 unknown）。"""
    return v if isinstance(v, bool) else None


# ════════════════════════ 复算核心：喂原判据 + 对账 ════════════════════════
class _Rebuilt:
    """从 `actions_full` 原结构重建的最小动作对象（供 `j1_verdict` 消费）。

    保留 `action_type`/`payload`/`raw` 三件，**payload 原样**（date/type/tag/未知合法键/JSON 嵌套
    一位不丢）。`j1_verdict` 只读 `.action_type` 与 `.payload`，故只需这两件可读。
    """

    __slots__ = ("action_type", "payload", "raw")

    def __init__(self, action_type, payload, raw=""):
        self.action_type = action_type
        self.payload = payload
        self.raw = raw


def _rebuild_actions(actions_full):
    """`actions_full` → 动作对象列表；`unknown`→None（这一腿没产出可留）；`[]`→真零动作。"""
    if actions_full is None or actions_full == UNKNOWN:
        return None
    if isinstance(actions_full, list):
        out = []
        for a in actions_full:
            if isinstance(a, dict):
                out.append(_Rebuilt(a.get("action", UNKNOWN), a.get("payload"),
                                    a.get("raw", "")))
            else:
                return None     # 形态不对 ⇒ 当不可用，不猜
        return out
    return None


def _replay_actions(reply_text, anchor):
    """用**原** `parse_actions(base_date=anchor)` 从回复全文重放动作（复用锚点日期语义）。"""
    from app.agent.actions import parse_actions  # noqa: PLC0415 —— 见 _sandbox_db_env
    return parse_actions(reply_text or "", base_date=anchor)


def _replay_channel(reply_text):
    """用**原** `extract_memory_channel`（生产 normalize 同源）从回复全文重放【记忆：】通道。"""
    return _ev().extract_memory_channel(reply_text or "")


def _actions_sig(actions):
    """动作结构签名（仅用于「回复重放 vs 留底动作」自洽核对，不进判据）。"""
    sig = []
    for a in actions or []:
        payload = getattr(a, "payload", None)
        try:
            pblob = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError):
            pblob = "<unjson>"
        sig.append((getattr(a, "action_type", UNKNOWN), pblob))
    return sig


def _raw_sig(actions):
    """动作 raw 串序列（`action_raw` 字段对账用；与 actions_full[].raw 同源）。"""
    return [str(getattr(a, "raw", "") or "") for a in actions or []]


def _stored_readings(rec: dict) -> tuple:
    """存下判分的「真实读数」三件：pass/err/why。返回 (pass, err, why, 全部有读数?)。
    任何一件缺失（键不在 / None / ==unknown）⇒ 该件为 None，has_all=False。
    **绝不 bool() 强转**（修复②："False"/1 一律不当真值读数）。"""
    def read(k):
        if k not in rec:
            return None
        v = rec.get(k)
        if v is None or v == UNKNOWN:
            return None
        return v
    p, e, w = read("pass"), read("err"), read("why")
    has_all = p is not None and e is not None and w is not None
    return p, e, w, has_all


def _replay_one_core(rec: dict, *, case_map: dict | None = None) -> dict:
    """对**一行** generate/judge 留底按原判据重放判分，并与存值逐字段对账。返回匿名就绪的结构。

    不打印正文；差异只给维度与原因码。`case_map`（cid→case）可选，用于独立校验 `case_source_fp`。
    分桶与证据合格由外层 `replay_one` 收口（冲突桶需要全局重复键视野）。
    """
    ev = _ev()
    stage = str(rec.get("stage") or "")
    out = {
        "stage": stage,
        "cid": rec.get("cid"),
        "run": rec.get("run"),
        "reasons": [],
        "source_used": None,
        "stored": None, "recomputed": None, "verdict_match": None,
        "self_consistent": None,
        "fields": {},
        "evidence_ok": False,
        "bucket": None,
        "fp": {},
        "criteria_dims": {},
        # 私有坐标（rows 对账用；渲染/落盘前剥掉 `_` 前缀键，不进公共输出）
        "_coords": {"prompt_fp_stable": rec.get("prompt_fp_stable"),
                    "prompt_fp": rec.get("prompt_fp"),
                    "sent_fp": rec.get("sent_fp")},
    }
    # ── 输入/来源分维（与判分正交，export 也做）──────────────────────────────
    _fp_dims(rec, out, case_map)
    _fp_criteria(rec, out, case_map)     # 第二轮①：实际执行判据 vs 已验证题面
    _fp_sent(rec, out)

    if stage == "export":
        out["reasons"].append(R_EXPORT_ONLY)
        out["verdict_match"] = None        # 无回复无动作，不算判分
        return out
    if stage not in ("generate", "judge"):
        out["reasons"].append(R_UNKNOWN_STAGE)
        out["verdict_match"] = None
        return out

    # ── 截断旧样本：900 字回复窗 / 70 字载荷窗之后的正文不在留底里 ⇒ 只能当下界，
    #    不能拿残缺原文假装无损重放（缺证据保留 unknown/下界，交接单 §4.3）。
    if rec.get("truncated") is True:
        out["reasons"].append(R_STALE_TRUNC)
        out["verdict_match"] = None
        return out

    # ── 判据坐标（修复②①）：expect **或** time_anchor 任一缺失 ⇒ 缺判据，verdict_match=None。
    #    非 dict（含字符串 "unknown"）一律走缺判据，绝不强转空字典、绝不对非 dict 调 .get。
    exp_raw, ta_raw = rec.get("expect"), rec.get("time_anchor")
    if (exp_raw is None or exp_raw == UNKNOWN or not isinstance(exp_raw, dict)
            or ta_raw is None or ta_raw == UNKNOWN or not isinstance(ta_raw, dict)):
        out["reasons"].append(R_MISSING_JUDGE)
        out["verdict_match"] = None
        return out
    expect, time_anchor = exp_raw, ta_raw

    reply = rec.get("reply_text_full")
    if reply is None or reply == UNKNOWN:
        # 无回复原文：若留底动作完整可退化重判（仍走原判据），但明确标注来源
        actions = _rebuild_actions(rec.get("actions_full"))
        chan = rec.get("mem_channel_full")
        chan = None if chan == UNKNOWN else chan
        if actions is None:
            out["reasons"].append(R_MISSING_REPLY)
            out["verdict_match"] = None
            return out
        out["source_used"] = "actions_full_no_reply"
    else:
        anchor = str(time_anchor.get("as_of") or "").strip() or None
        if not anchor:
            anchor = str(rec.get("anchor") or "").strip() or None
        actions = _replay_actions(reply, anchor)
        chan = _replay_channel(reply)
        out["source_used"] = "reply_text_full"
        # 自洽核对：回复重放出的动作 vs 留底 actions_full（不无条件信存下 payload）
        stored_actions = _rebuild_actions(rec.get("actions_full"))
        if stored_actions is not None:
            out["self_consistent"] = (_actions_sig(actions) == _actions_sig(stored_actions))
            if not out["self_consistent"]:
                out["reasons"].append(R_SELFINC)

    expect_date = ev.resolve_expect_date(time_anchor)
    v = ev.j1_verdict(expect, actions, expect_date=expect_date, memory_texts=chan)
    recomputed = {"pass": bool(v["pass"]), "err": v.get("err", ""), "why": v.get("why", ""),
                  "exemptions": list(v.get("exempted") or [])}
    out["recomputed"] = recomputed

    # ── 逐字段对账（修复②）：pass/err/why + exemptions/dates_seen/actions_seen/action_raw
    raw_now = _raw_sig(actions) if out["source_used"] == "reply_text_full" else None
    fields = _recon_fields(rec, out["source_used"], raw_now)
    out["fields"] = fields

    sp, se, sw, has_all = _stored_readings(rec)
    stored_err_s = "" if se is None else str(se)
    if has_all and not isinstance(sp, bool):
        # pass 非原生 bool（"False"/"True"/1/0…）⇒ 不可比，绝不 bool() 强转（修复②）
        out["reasons"].append(R_VERDICT_TYPE_INVALID)
        out["verdict_match"] = None
        return out

    st = {"pass": sp, "err": se, "why": sw,
          "exemptions": rec.get("exemptions"), "dates_seen": rec.get("dates_seen"),
          "actions_seen": rec.get("actions_seen"), "action_raw": rec.get("action_raw")}
    out["stored"] = st

    if not has_all:
        out["reasons"].append(R_MISSING_FIELD)
        out["verdict_match"] = None
        return out

    if sp != recomputed["pass"]:
        out["verdict_match"] = False
        out["reasons"].append(R_DIFF_PASS)
    elif stored_err_s != recomputed["err"]:
        out["verdict_match"] = False
        out["reasons"].append(R_DIFF_ERR)
    elif any(fields[k] == "diff" for k in _CMP_FIELDS):
        out["verdict_match"] = False
        out["reasons"].append(R_DIFF_FIELD)
    elif all(fields[k] == "match" for k in _CMP_FIELDS):
        out["verdict_match"] = True
        out["reasons"].append(R_MATCH)
    else:
        # pass/err 对得上，但被比字段含 unknown/not_compared ⇒ **不称逐字一致**、不进一致分母
        # （pass/err 的局部一致仍见于表格「原判分/现算判分」两格，但不得记成完整对上）
        out["verdict_match"] = None
        out["reasons"].append(R_PARTIAL_FIELDS)
    return out


def _recon_fields(rec: dict, source_used, raw_now) -> dict:
    """逐字段对账表（修复②）：值 ∈ match/diff/unknown/not_compared。

    现算值与 `replay_one` 同源同参（`j1_verdict` 纯函数、确定性，再放一遍即可）：
      why/exemptions ← 同一发 j1_verdict；dates_seen/actions_seen ← 现算动作派生（口径与生成侧逐字一致）；
      action_raw ← 现算动作 raw 列表 vs 留底 actions_full[].raw。
    存值缺失（None/==unknown）⇒ "unknown"；现算不可得 ⇒ "not_compared"；否则逐字比。
    """
    return _recon_fields_impl(rec, source_used, raw_now, _ev())


def _recon_fields_impl(rec: dict, source_used, raw_now, ev) -> dict:
    """真正的逐字段对账（现算值与 replay_one 同源同参，纯函数可重放）。"""
    out = {}
    # 现算四项的来源：
    #   why/exemptions ← 与 replay_one 同一发 j1_verdict（这里再算一遍：j1_verdict 纯函数、确定性）
    #   dates_seen/actions_seen ← 现算动作（回复重放或留底动作）派生，口径与生成侧逐字一致
    #   action_raw ← 现算动作 raw 列表 vs 留底 actions_full[].raw
    exp_raw, ta_raw = rec.get("expect"), rec.get("time_anchor")
    if (isinstance(exp_raw, dict) and isinstance(ta_raw, dict)):
        reply = rec.get("reply_text_full")
        if reply is not None and reply != UNKNOWN:
            anchor = str(ta_raw.get("as_of") or "").strip() or None
            if not anchor:
                anchor = str(rec.get("anchor") or "").strip() or None
            acts = _replay_actions(reply, anchor)
            chan = _replay_channel(reply)
        else:
            acts = _rebuild_actions(rec.get("actions_full"))
            chan = rec.get("mem_channel_full")
            chan = None if chan == UNKNOWN else chan
        try:
            v = ev.j1_verdict(exp_raw, acts, expect_date=ev.resolve_expect_date(ta_raw),
                              memory_texts=chan)
            now_why = str(v.get("why") or "")
            now_exempt = list(v.get("exempted") or [])
        except Exception:  # noqa: BLE001 —— 不回显异常文本
            now_why, now_exempt = None, None
        if acts is None:
            now_dates = now_actions = None
        else:
            now_dates = sorted({str((a.payload or {}).get("date") or "")[:10]
                                for a in acts if isinstance(a.payload, dict)
                                and (a.payload or {}).get("date")})
            now_actions = [str(getattr(a, "action_type", UNKNOWN)) for a in acts]
    else:
        now_why = now_exempt = now_dates = now_actions = None

    def cmp_val(key, stored_v, now_v, ordered=True):
        """存值缺失 ⇒ unknown；现算不可得 ⇒ not_compared；否则逐字比 ⇒ match/diff。"""
        if stored_v is None or stored_v == UNKNOWN:
            out[key] = "unknown"
            return
        if now_v is None:
            out[key] = "not_compared"
            return
        if key in ("exemptions", "dates_seen", "actions_seen", "action_raw"):
            sv = list(stored_v) if isinstance(stored_v, (list, tuple)) else [str(stored_v)]
            sv = [str(x) for x in sv]
            nv = [str(x) for x in now_v]
            if key == "exemptions":
                sv, nv = sorted(sv), sorted(nv)      # 豁免按排序列表比
            out[key] = "match" if sv == nv else "diff"  # actions_seen 按列表顺序比
        else:
            out[key] = "match" if str(stored_v) == str(now_v) else "diff"

    cmp_val("why", rec.get("why"), now_why)
    cmp_val("exemptions", rec.get("exemptions"), now_exempt)
    cmp_val("dates_seen", rec.get("dates_seen"), now_dates)
    cmp_val("actions_seen", rec.get("actions_seen"), now_actions)
    if source_used == "actions_full_no_reply":
        out["action_raw"] = "not_compared"
    else:
        # 留底动作 raw：`actions_full[].raw`。**空表是真实读数**（零动作腿）——
        # 第二轮③：`[]` 与现算 raw=[] 比较应 match，不得因「空」被当缺失；
        # None/unknown/结构非法（非 dict 元素）才保持 unknown。
        af = rec.get("actions_full")
        if isinstance(af, list) and all(isinstance(a, dict) for a in af):
            stored_raw = [str(a.get("raw", "") or "") for a in af]
            out["action_raw"] = "match" if stored_raw == [str(x) for x in (raw_now or [])] else "diff"
        else:
            out["action_raw"] = "unknown"
    # pass/err 的读数列（未参与本表判定时也给状态，报表透明）
    sp, se, _sw, has_all = _stored_readings(rec)
    out["pass"] = ("match" if (has_all and isinstance(sp, bool) and sp == (now_actions is not None
                   and bool(_pass_now(rec, ev)))) else
                   "unknown" if not has_all else "diff")
    del se
    return out


def _pass_now(rec: dict, ev) -> bool:
    """现算 pass（与 replay_one 同源同参；纯函数可重放）。"""
    exp_raw, ta_raw = rec.get("expect"), rec.get("time_anchor")
    reply = rec.get("reply_text_full")
    if reply is not None and reply != UNKNOWN:
        anchor = str(ta_raw.get("as_of") or "").strip() or None
        if not anchor:
            anchor = str(rec.get("anchor") or "").strip() or None
        acts = _replay_actions(reply, anchor)
        chan = _replay_channel(reply)
    else:
        acts = _rebuild_actions(rec.get("actions_full"))
        chan = rec.get("mem_channel_full")
        chan = None if chan == UNKNOWN else chan
    v = ev.j1_verdict(exp_raw, acts, expect_date=ev.resolve_expect_date(ta_raw),
                      memory_texts=chan)
    return bool(v["pass"])


def _fp_dims(rec: dict, out: dict, case_map: dict | None):
    """输入/来源逐维判读（缺哪维写 unknown，不互顶）。修复⑤：补 `evaluator_vs_now` 尺子维。"""
    ev = _ev()
    fp = out["fp"]
    ctx = rec.get("ctx_messages")
    ctx_ok = isinstance(ctx, list)
    stored_stable = rec.get("prompt_fp_stable")
    stored_stable = None if stored_stable in (None, UNKNOWN) else str(stored_stable)

    # ① 输入 stable：有 ctx_messages 才能现算原 stable_fp
    if ctx_ok and stored_stable:
        now = ev.stable_fp(ctx)
        fp["input_stable"] = R_INPUT_FP_MATCH if now == stored_stable else R_INPUT_FP_DIFF
        fp["input_stable_now"] = now
    elif stored_stable and not ctx_ok:
        fp["input_stable"] = R_INPUT_FP_UNKNOWN     # 只有带时钟的 prompt_fp 不能冒充 stable
    else:
        fp["input_stable"] = R_INPUT_FP_UNKNOWN

    # ② 题面来源：有独立题面原件（case_map）才可校验真实性，否则只报留存标识是否相等
    cs = rec.get("case_source_fp")
    cs = None if cs in (None, UNKNOWN) else str(cs)
    if case_map and rec.get("cid") in case_map and cs:
        now_cs = ev.case_source_fp(case_map[rec.get("cid")])
        fp["case_source"] = "verified" if now_cs == cs else "mismatch"
        fp["case_source_now"] = now_cs
    elif cs:
        fp["case_source"] = "retained_only"         # 只能说留存标识在，不能称真实性已校验
    else:
        fp["case_source"] = R_INPUT_FP_UNKNOWN

    # ③ 导出尺子（两腿同源？）—— 只比留存两维是否相等，缺即 unknown
    ec = rec.get("export_evaluator_source_fp")
    ec = None if ec in (None, UNKNOWN) else str(ec)
    cur = rec.get("evaluator_source_fp")
    cur = None if cur in (None, UNKNOWN) else str(cur)
    if ec and cur:
        fp["export_vs_current"] = "same" if ec == cur else "diff"
    else:
        fp["export_vs_current"] = R_INPUT_FP_UNKNOWN

    # ④ 当前尺子 vs 留底执行尺子（修复⑤）：same/diff/unknown。diff ⇒ 这是**换当前尺子复算**，
    #    不得声称原尺子复现（reasons 带 recomputed_with_current_ruler，evidence_ok=False）。
    try:
        now_ev_fp = ev.evaluator_source_fp()
    except Exception:  # noqa: BLE001
        now_ev_fp = None
    fp["evaluator_now"] = now_ev_fp if now_ev_fp else R_INPUT_FP_UNKNOWN  # 保留在 fp，公共报表不裸打
    if cur and now_ev_fp:
        fp["evaluator_vs_now"] = "same" if cur == now_ev_fp else "diff"
    else:
        fp["evaluator_vs_now"] = R_INPUT_FP_UNKNOWN
    if fp["evaluator_vs_now"] == "diff":
        out["reasons"].append(R_RULER_RECOMPUTED)


def _fp_sent(rec: dict, out: dict):
    """sent_fp 分维（修复④）：留底带 sent_fp（非 None/unknown）时用原 `sent_fp_of(ctx_messages)` 校验。

    错配 ⇒ `sent_fp_mismatch`（=发出去的不是这份输入，不可比，后由分桶落 incomparable）；
    没带 ⇒ `sent_fp_not_applicable` 不拦。有 sent_fp 但缺 ctx_messages ⇒ `input_fp_unknown`（无法自证）。
    """
    ev = _ev()
    fp = out["fp"]
    sf = rec.get("sent_fp")
    if sf is None or sf == UNKNOWN:
        fp["sent_fp"] = R_SENT_FP_NA
        return
    ctx = rec.get("ctx_messages")
    try:
        want = ev.sent_fp_of(ctx) if isinstance(ctx, list) else None
    except Exception:  # noqa: BLE001 —— 不回显异常
        want = None
    if not want:
        fp["sent_fp"] = R_INPUT_FP_UNKNOWN
        return
    if str(sf) == want:
        fp["sent_fp"] = R_INPUT_FP_MATCH
    else:
        fp["sent_fp"] = R_SENT_FP_MISMATCH
        out["reasons"].append(R_SENT_FP_MISMATCH)


def _crit_blob(obj):
    """判据对象规范化：`sort_keys`＋`allow_nan=False`；序列化失败 ⇒ None（不可比）。"""
    if obj is None or obj == UNKNOWN:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _fp_criteria(rec: dict, out: dict, case_map: dict | None):
    """**实际执行判据 vs 已验证题面**逐维判读（第二轮①）。

    为什么必须单独一维：`case_source` 只证明**留存题面指纹 == 独立题面原件指纹**（这道题是那一道），
    但判分用的是**记录里那份 expect/time_anchor**，它可以在不改题面指纹的前提下被独立改掉。
    只比题面指纹就会把一个「实际执行判据已经不是那道题」的记录报成证据完整合格。

    逐维：`expect` / `time_anchor` / **真正参与解析的锚点**（time_anchor.as_of，回退 rec.anchor）。
    值 ∈ match/diff/unknown；任一 diff ⇒ 汇总 diff＋原因码，且 evidence_ok 记否（缺证据亦不得合格）。
    """
    fp = out["fp"]
    cid = rec.get("cid")
    case = case_map.get(cid) if (case_map and cid in case_map) else None
    if not isinstance(case, dict):
        fp["criteria_binding"] = R_INPUT_FP_UNKNOWN
        out["criteria_dims"] = {"expect": R_INPUT_FP_UNKNOWN,
                                "time_anchor": R_INPUT_FP_UNKNOWN,
                                "anchor": R_INPUT_FP_UNKNOWN}
        return

    def _dim(key):
        a, b = _crit_blob(rec.get(key)), _crit_blob(case.get(key))
        if a is None or b is None:
            return R_INPUT_FP_UNKNOWN
        return "match" if a == b else "diff"

    def _anchor(src):
        ta = src.get("time_anchor")
        v = str(ta.get("as_of") or "").strip() if isinstance(ta, dict) else ""
        if not v:
            v = str(src.get("anchor") or "").strip()
        return v or None

    a_rec, a_case = _anchor(rec), _anchor(case)
    if a_rec is None or a_case is None:
        anchor_dim = R_INPUT_FP_UNKNOWN
    else:
        anchor_dim = "match" if a_rec == a_case else "diff"

    dims = {"expect": _dim("expect"), "time_anchor": _dim("time_anchor"), "anchor": anchor_dim}
    out["criteria_dims"] = dims
    vals = set(dims.values())
    if "diff" in vals:
        fp["criteria_binding"] = "diff"
        out["reasons"].append(R_CRITERIA_MISMATCH)
    elif vals == {"match"}:
        fp["criteria_binding"] = "match"
    else:
        fp["criteria_binding"] = R_INPUT_FP_UNKNOWN


def _compute_bucket(rec: dict, out: dict, dup_keys: set) -> str:
    """四桶归属（第二轮②）：conflict > incomparable > missing > effective。

    partial（被比字段含 unknown/not_compared）＝**严格不可比** ⇒ 落 incomparable，不进有效分母。
    """
    k = (str(rec.get("stage")), str(rec.get("cid")), str(rec.get("run")))
    if k in dup_keys:
        return B_CONFLICT
    stage = str(rec.get("stage") or "")
    if stage == "export" or R_EXPORT_ONLY in out["reasons"]:
        return B_INCOMPARABLE
    if stage not in ("generate", "judge") or R_UNKNOWN_STAGE in out["reasons"]:
        return B_INCOMPARABLE
    if R_STALE_TRUNC in out["reasons"]:
        return B_INCOMPARABLE
    if R_VERDICT_TYPE_INVALID in out["reasons"]:
        return B_INCOMPARABLE
    if R_SENT_FP_MISMATCH in out["reasons"]:
        return B_INCOMPARABLE
    if R_PARTIAL_FIELDS in out["reasons"]:
        return B_INCOMPARABLE
    if (R_MISSING_JUDGE in out["reasons"] or R_MISSING_REPLY in out["reasons"]
            or R_MISSING_FIELD in out["reasons"]):
        return B_MISSING
    return B_EFFECTIVE


def _compute_evidence_ok(out: dict) -> bool:
    """证据完整合格（全维自证）：任一维 unknown/错配 ⇒ False（双栏与判分一致**分开**）。

    第二轮①新增门槛：`criteria_binding` 必须 match——留存题面指纹相等**不等于**实际执行的
    expect/time_anchor 就是那道题的；判据不同源或缺证据 ⇒ 证据不完整，不得合格。
    """
    fp = out.get("fp") or {}
    fields = out.get("fields") or {}
    return (out.get("bucket") == B_EFFECTIVE
            and out.get("verdict_match") is True
            and bool(fields) and all(v == "match" for v in fields.values())
            and fp.get("input_stable") == R_INPUT_FP_MATCH
            and fp.get("case_source") == "verified"
            and fp.get("criteria_binding") == "match"
            and fp.get("evaluator_vs_now") == "same"
            and out.get("self_consistent") is not False)


def _dup_keys_of(records: list) -> set:
    """全部 (stage,cid,run) 键中出现 ≥2 次的＝冲突键（修复①：重复行全进 conflict 桶，不进有效分母）。"""
    seen, dup = set(), set()
    for r in records:
        k = (str(r.get("stage")), str(r.get("cid")), str(r.get("run")))
        if k in seen:
            dup.add(k)
        else:
            seen.add(k)
    return dup


def replay_one(rec: dict, *, case_map: dict | None = None,
               dup_keys: set | None = None) -> dict:
    """公开入口：重放一行并收口分桶/证据合格。单条调用时重复键视野为空（不可知）。"""
    out = _replay_one_core(rec, case_map=case_map)
    dups = dup_keys or set()
    k = (str(rec.get("stage")), str(rec.get("cid")), str(rec.get("run")))
    if k in dups and R_DUP not in out["reasons"]:
        out["reasons"].append(R_DUP)          # 重复键显式点名（原因码），行进 conflict 桶
    out["bucket"] = _compute_bucket(rec, out, dups)
    out["evidence_ok"] = _compute_evidence_ok(out)
    return out


def replay_all(records: list, *, case_map: dict | None = None) -> list:
    """整批重放：先算全局重复键，再逐行重放收口（冲突桶＝重复键的**所有**行）。"""
    dup = _dup_keys_of(records)
    return [replay_one(r, case_map=case_map, dup_keys=dup) for r in records]


# ════════════════════════ rows 逐跑对账（真实 fold_runs 折叠形态，修复④）═════════════
def _rows_raw_entries(rows: list) -> list:
    """把 rows 文件摊成逐跑候选：顶层行＝首跑（键 (cid,run)）；`other_runs[i]` **没有 run 字段**。

    只给「指纹坐标」（prompt_fp_stable/prompt_fp），跑号**不可凭空补**——匹配上才可认定。
    """
    entries = []
    for i, rr in enumerate(rows):
        if not isinstance(rr, dict):
            continue
        entries.append({"cid": rr.get("cid"), "run": rr.get("run"), "top_idx": i,
                        "prompt_fp_stable": rr.get("prompt_fp_stable"),
                        "prompt_fp": rr.get("prompt_fp"), "_rr": rr})
        for j, o in enumerate(rr.get("other_runs") or []):
            if isinstance(o, dict):
                entries.append({"cid": rr.get("cid"), "run": None, "top_idx": i, "other_idx": j,
                                "prompt_fp_stable": o.get("prompt_fp_stable"),
                                "prompt_fp": o.get("prompt_fp"), "_rr": o})
    return entries


def _rv(v):
    """指纹读数：None/unknown/空串 ⇒ None（不可作为匹配证据），否则字符串。"""
    if v is None or v == UNKNOWN or not str(v):
        return None
    return str(v)


def reconcile_rows(results: list, rows: list) -> list:
    """留底逐跑 vs rows（folded 形态）逐跑对账。返回逐条结果（匿名就绪）。

    规则（第二轮④）：
      ① 首跑按 (cid,run) 直配（matched_by:"key"），但**同样要核输入维度**：raw/stable 与留底矛盾的
         一律 rows_diff，绝不因 pass/err 相同就称 rows_match；
      ② other_runs（无 run 字段）按指纹匹配：**唯一精确 raw 优先**，raw 无解才退 stable，且
         若该 stable 候选的 raw 与留底矛盾 ⇒ 不配（共享 stable 不得吞掉 raw 冲突）；多候选无从区分
         ⇒ `rows_ambiguous`，绝不猜配；无候选 ⇒ rows_missing；跑号显示 `run_unproven`（不凭空补）；
      ③ rows 文件内重复 (cid,run) ⇒ rows_conflict，绝不静默覆盖；
      ④ sent_fp 对账在 `_fp_sent`（逐留底记录）完成，本函数只取 fp.sent_fp 进明细。
    """
    entries = _rows_raw_entries(rows)
    dup_keys, seen_keys = set(), set()
    for e in entries:
        if e["run"] is not None:
            k = (str(e["cid"]), str(e["run"]))
            if k in seen_keys:
                dup_keys.add(k)
            seen_keys.add(k)

    out = []
    used = set()
    for r in results:
        if r.get("stage") not in ("generate", "judge"):
            continue
        if r.get("bucket") == B_CONFLICT:
            continue            # 冲突桶只点名，不进对账分母
        cid, run = r.get("cid"), r.get("run")
        coords = r.get("_coords") or {}
        rec_raw = _rv(coords.get("prompt_fp"))
        rec_stable = _rv(coords.get("prompt_fp_stable"))
        item = {"cid": cid, "run": run, "matched_by": None, "run_display": _enum_run(run),
                "recon": None, "rows_run": None, "input": R_INPUT_FP_UNKNOWN,
                "sent_fp": (r.get("fp") or {}).get("sent_fp", R_SENT_FP_NA)}

        # ① 首跑直配（带 run 字段的 entry）
        cand = [i for i, e in enumerate(entries)
                if i not in used and e["run"] is not None
                and str(e["cid"]) == str(cid) and str(e["run"]) == str(run)]
        if cand:
            i = cand[0]
            e = entries[i]
            used.add(i)
            if (str(cid), str(run)) in dup_keys:
                item.update(matched_by="key", recon=R_ROWS_CONFLICT, rows_run=_enum_run(e["run"]))
            else:
                recon, inp = _rows_compare(r, e, rec_raw, rec_stable)
                item.update(matched_by="key", recon=recon,
                            rows_run=_enum_run(e["run"]), input=inp)
        else:
            # ② other_runs 无 run 字段 ⇒ 指纹匹配（唯一精确 raw 优先）
            how, i, e = _pick_by_fp(entries, used, cid, rec_raw, rec_stable)
            if how == "ambiguous":
                item.update(matched_by="ambiguous", recon=R_ROWS_AMBIGUOUS, rows_run="ambiguous")
            elif e is None:
                item.update(matched_by=None, recon="rows_missing", rows_run=None)
            else:
                used.add(i)
                recon, inp = _rows_compare(r, e, rec_raw, rec_stable)
                item.update(matched_by=how, run_display="run_unproven",
                            recon=recon, rows_run="run_unproven", input=inp)
        out.append(item)

    # ④ sent_fp 对账已在 `_fp_sent`（逐留底记录，与判分正交）完成，item["sent_fp"] 取自 fp；
    #    错配的行已把原因加进 reasons，由分桶落 incomparable，不进有效分母。
    return out


def _pick_by_fp(entries: list, used: set, cid, rec_raw, rec_stable):
    """指纹匹配：返回 `(how, idx, entry)`。`how ∈ {fp_raw, fp_stable, ambiguous, missing}`。

    唯一精确 raw 优先；raw 无解才退 stable，且该 stable 候选的 raw 与留底**矛盾时不得配**
    （共享 stable 不得吞掉 raw 冲突）；多候选 ⇒ ambiguous（不猜）；无候选 ⇒ missing。
    """
    def free(i, e):
        # 指纹只能补足未知跑号，不能覆盖已知且不同的跑号；保留首跑供后续按键匹配。
        return (i not in used and e["run"] is None
                and e["cid"] is not None and str(e["cid"]) == str(cid))

    def hits(getter, want):
        return [(i, e) for i, e in enumerate(entries) if free(i, e) and getter(e) == want]

    if rec_raw is not None:
        raw_hits = hits(lambda e: _rv(e.get("prompt_fp")), rec_raw)
        if len(raw_hits) == 1:
            return "fp_raw", raw_hits[0][0], raw_hits[0][1]
        if len(raw_hits) > 1:
            return "ambiguous", None, None
        # 无精确 raw：只在 stable 唯一且 raw 不矛盾时才认（缺 raw 视为无法比较，不算矛盾）
        if rec_stable is not None:
            st_hits = hits(lambda e: _rv(e.get("prompt_fp_stable")), rec_stable)
            if len(st_hits) > 1:
                return "ambiguous", None, None
            if len(st_hits) == 1:
                e_raw = _rv(st_hits[0][1].get("prompt_fp"))
                if e_raw is None or e_raw == rec_raw:
                    return "fp_stable", st_hits[0][0], st_hits[0][1]
                return "ambiguous", None, None     # raw 矛盾 ⇒ 共享 stable 不得吞
        return "missing", None, None
    # 无 raw 证据：只能按 stable（唯一才认）
    if rec_stable is not None:
        st_hits = hits(lambda e: _rv(e.get("prompt_fp_stable")), rec_stable)
        if len(st_hits) == 1:
            return "fp_stable", st_hits[0][0], st_hits[0][1]
        if len(st_hits) > 1:
            return "ambiguous", None, None
    return "missing", None, None


def _rows_input_state(entry: dict, rec_raw, rec_stable) -> str:
    """输入维度对账：match/diff/unknown。任一维两侧都在且不等 ⇒ diff（不得称 rows_match）。"""
    e_raw = _rv(entry.get("prompt_fp"))
    e_stable = _rv(entry.get("prompt_fp_stable"))
    diffs = (rec_raw is not None and e_raw is not None and rec_raw != e_raw) or \
            (rec_stable is not None and e_stable is not None and rec_stable != e_stable)
    if diffs:
        return "diff"
    matches = (rec_raw is not None and rec_raw == e_raw) or \
              (rec_stable is not None and rec_stable == e_stable)
    return "match" if matches else R_INPUT_FP_UNKNOWN


def _rows_compare(result: dict, entry: dict, rec_raw, rec_stable):
    """留底逐跑 vs rows 对应跑的**判分＋输入维度**对账。返回 `(recon, input_state)`。

    rows_match 要求 pass/err 对得上**且输入维度不矛盾**（input_state 非 diff）。
    """
    rr = entry.get("_rr")
    if not isinstance(rr, dict):
        return "rows_missing", R_INPUT_FP_UNKNOWN
    st = result.get("stored") or {}
    verdict = _rows_compare_impl(st, rr)
    inp = _rows_input_state(entry, rec_raw, rec_stable)
    if verdict == "rows_match" and inp == "diff":
        return "rows_diff", inp
    return verdict, inp


def _rows_compare_impl(stored: dict, rr: dict) -> str:
    """对账本体：pass 必须两侧都是原生 bool 且相等，err 逐字比；其余一律 rows_diff。"""
    sp, se = rr.get("pass"), rr.get("err")
    rp_, re_ = stored.get("pass"), stored.get("err")
    if not isinstance(rp_, bool) or not isinstance(sp, bool):
        return "rows_diff"
    return "rows_match" if (sp == rp_ and str(se or "") == str(re_ or "")) else "rows_diff"


# ════════════════════════ 报表渲染（匿名：只出枚举值/聚合/原因码，绝无正文）═══════════
def _verdict_cell(st) -> str:
    """判分格：`pass/err`，err 过枚举词表；pass 非原生 bool 一律 `-`，绝不回显原值。"""
    if not isinstance(st, dict) or not st:
        return "-"
    p = st.get("pass")
    e = _enum_err(st.get("err")) or "-"
    return "%s/%s" % (p, e) if isinstance(p, bool) else "-/%s" % e


def _fields_cell(fields: dict) -> str:
    """字段对账摘要：只出计数与字段名（均为安全码），不出任何字段值。"""
    from collections import Counter  # noqa: PLC0415
    c = Counter(v for k, v in (fields or {}).items() if k in _CMP_FIELDS)
    if not c:
        return "-"
    return ",".join("%s×%d" % (k, n) for k, n in sorted(c.items()))


def _render_replay(results: list, anon: Anon, meta: list, row_recon=None) -> str:
    """公共复算报表（修复①③⑤）：分桶各自分母、判分局部一致与证据完整合格**分栏**、
    尺子来源分维闭合；逐跑明细只出匿名题号/枚举值/原因码，stage/run/err 全走词表。"""
    from collections import Counter  # noqa: PLC0415
    lines = ["# A30 私有留底离线复算 · 逐跑匿名对账", "",
             "> 只含匿名题号、聚合、指纹维度状态与原因码；不含任何正文/cid/载荷/凭据/真实文件名。",
             "> 这是**证据能力**读数，不是 A30 质量认证。", ""]
    buckets = Counter(r.get("bucket") or B_INCOMPARABLE for r in results)
    n_all = len(results)
    eff = [r for r in results if r.get("bucket") == B_EFFECTIVE]
    n_eff = len(eff)
    match = sum(1 for r in eff if r.get("verdict_match") is True)
    diff = sum(1 for r in eff if r.get("verdict_match") is False)
    eok = sum(1 for r in results if r.get("evidence_ok") is True)

    n_bucket_sum = sum(buckets.get(b, 0) for b in (B_EFFECTIVE, B_CONFLICT, B_MISSING, B_INCOMPARABLE))
    lines += ["## 分桶（有效 / 冲突 / 缺失 / 不可比 各自分母，互不并入）", "",
              "| 桶 | 行数 | 含义 |", "|---|---|---|",
              "| effective | %d | 唯一键＋判据全＋**被比字段全部可比**（唯一可进判分一致分母的行） |" % buckets.get(B_EFFECTIVE, 0),
              "| conflict | %d | 重复 (stage,cid,run)：**所有行只点名**，不作有效一致证据 |" % buckets.get(B_CONFLICT, 0),
              "| missing | %d | 缺判据 / 缺回复与动作原文 / 缺存下读数 |" % buckets.get(B_MISSING, 0),
              "| incomparable | %d | 未知 stage / 截断 / 仅 export / pass 类型不正 / sent_fp 错配 / **被比字段含 unknown·not_compared（partial）** |" % buckets.get(B_INCOMPARABLE, 0),
              "",
              "> 四桶合计 %d ＝ 总行数 %d（%s）——分桶互斥且完备，`partial_fields` 已落 incomparable 桶、不进有效分母。"
              % (n_bucket_sum, n_all, "一致" if n_bucket_sum == n_all else "**不一致，读数不可信**"),
              ""]
    lines += ["## 判分局部一致 vs 证据完整合格（**分栏**，互不冒充）", "",
              "| 项 | 分子 | 分母 | 说明 |", "|---|---|---|---|",
              "| 判分局部一致（pass/err＋被比字段逐字全 match） | %d | %d | **仅 effective 桶**；只说明判分可复现 |" % (match, n_eff),
              "| 判分重放后有差异 | %d | %d | 含过不过/错因/字段逐字差异 |" % (diff, n_eff),
              "| 证据完整合格（evidence_ok 全维自证） | %d | %d | 另需 输入stable＋题面 verified＋尺子同源＋sent_fp 全成立 |" % (eok, n_all),
              "",
              "> 「判分局部一致」≠「证据完整合格」：判分对得上但输入指纹错配/题面只留存标识/尺子不同源时，",
              "> 只能记前者，后者必须记否——unknown/冲突/差异 一律不包装成完整重放通过。", ""]

    reason_c = Counter(x for r in results for x in r.get("reasons", []))
    lines.append("## 原因分布")
    lines.append("")
    for k, v in sorted(reason_c.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append("- `%s`：%d" % (k, v))
    lines.append("")

    def _dim(name, key, note=""):
        c = Counter((r.get("fp") or {}).get(key, "?") for r in results)
        lines.append("## %s" % name)
        lines.append("")
        for k, v in sorted(c.items()):
            lines.append("- `%s`：%d" % (k, v))
        if note:
            lines.append("")
            lines.append(note)
        lines.append("")

    _dim("输入 stable 分维（有 ctx_messages 才现算）", "input_stable")
    _dim("题面来源分维", "case_source",
         "> `verified`=有独立题面原件；`retained_only`=仅留存标识，不得称真实性已校验；`unknown`=缺。")
    _dim("判据绑定分维（实际执行 expect/time_anchor/锚点 vs 已验证题面）", "criteria_binding",
         "> `match`=实际执行判据就是那道题的；`diff`=**改了判据**（留存题面指纹仍相等也不顶事）⇒ 不得证据合格；"
         "`unknown`=无独立题面或缺判据，维持 unknown 不补造。")
    _dim("尺子来源分维（留底执行尺子 vs 现算尺子）", "evaluator_vs_now",
         "> `same`=历史指纹==现算，可称原尺子复现；`diff`=**换当前尺子复算**，不得声称原尺子复现；"
         "`unknown`=缺历史指纹，维持 unknown 不补造。")
    _dim("sent_fp 分维（留底带 sent_fp 才校验）", "sent_fp",
         "> `sent_fp_mismatch`=发出去的不是这份输入 ⇒ 不可比；`sent_fp_not_applicable`=没带，不拦。")
    _dim("导出尺子 vs 留底执行尺子", "export_vs_current")

    lines += ["## 逐字段比较范围（未经比较的字段绝不称逐字一致）", "",
              "- `pass/err`：过不过与错因，两侧都必须是真实读数（pass 必须原生 bool）才比；",
              "- `why/exemptions/dates_seen/actions_seen/action_raw`：存值缺失 ⇒ `unknown`、现算不可得 ⇒ "
              "`not_compared`，二者都不算一致；",
              "- `exemptions` 按排序列表比、`actions_seen` 按顺序比、`action_raw` 比原始标记串列表；",
              "- 任一被比字段为 `unknown/not_compared` 时即便判分对上也带 `partial_fields`，不称逐字一致。", ""]

    lines += ["## 逐跑明细（匿名）", "",
              "| 匿名 | run | stage | 桶 | 原判分 | 现算判分 | 判分对账 | 证据合格 | 输入stable | 题面来源 | 判据绑定 | 尺子vs现 | sent_fp | 字段对账 | 原因 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        a = anon.key(r.get("cid"))
        fp = r.get("fp") or {}
        if r.get("bucket") == B_CONFLICT:
            ov = "冲突(不计)"          # 冲突行即便判分字段对得上也不得显示"一致"（不进任何分母）
        else:
            ov = ("一致" if r.get("verdict_match") is True else
                  "差异" if r.get("verdict_match") is False else "不可复算")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            a, _enum_run(r.get("run")), _enum_stage(r.get("stage")),
            r.get("bucket") or B_INCOMPARABLE,
            _verdict_cell(r.get("stored")), _verdict_cell(r.get("recomputed")),
            ov, "是" if r.get("evidence_ok") else "否",
            fp.get("input_stable", "?"), fp.get("case_source", "?"),
            fp.get("criteria_binding", "?"),
            fp.get("evaluator_vs_now", "?"), fp.get("sent_fp", "?"),
            _fields_cell(r.get("fields")), ",".join(r.get("reasons", []))))

    if row_recon:
        lines += ["", "## rows 逐跑对账（folded 形态：首跑按 run 直配，other_runs 按指纹匹配不补跑号）", "",
                  "| 匿名 | run | 匹配方式 | rows跑 | 输入维度 | 对账 | sent_fp |",
                  "|---|---|---|---|---|---|---|"]
        for x in row_recon:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                anon.key(x.get("cid")), x.get("run_display", _enum_run(x.get("run"))),
                x.get("matched_by") or "-", x.get("rows_run") or "-",
                x.get("input", "?"), x.get("recon"), x.get("sent_fp", "-")))
        rc = Counter(x.get("recon") for x in row_recon)
        lines += ["", "rows 对账汇总：match=%d diff=%d rows_missing=%d rows_conflict=%d rows_ambiguous=%d" % (
            rc.get("rows_match", 0), rc.get("rows_diff", 0),
            rc.get("rows_missing", 0), rc.get(R_ROWS_CONFLICT, 0), rc.get(R_ROWS_AMBIGUOUS, 0))]

    if meta:
        lines += ["", "## 输入清单（匿名文件标签 + 哈希与角色，不含正文/真实文件名）", "",
                  "| 文件 | SHA256前16 | 角色 | 行数 |", "|---|---|---|---|"]
        for m in meta:
            lines.append("| %s | %s | %s | %s |" % (
                m.get("file", "?"), (m.get("sha256") or "")[:16],
                m.get("role"), m.get("n_rows", "-")))
    return "\n".join(lines) + "\n"


def _render_audit(manifest: list, same_input: list) -> str:
    """公共审计报表（修复⑤）：按 (输入文件, 匿名题) 分组保留证据，同名 cid 不再被后文件覆盖。"""
    lines = ["# A30 同源性复核 · 输入清单与逐维盘点", "",
             "> 只含匿名文件标签/哈希/角色/维度状态与聚合；不含正文/真实文件名。", "",
             "## 输入 manifest", "",
             "| 文件 | SHA256前16 | 角色 | 行数 | 来源批次 | 能审计维度 |",
             "|---|---|---|---|---|---|"]
    for m in manifest:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            m.get("file", "?"), (m.get("sha256") or "")[:16], m.get("role"),
            m.get("n_rows", "-"), m.get("batch", "-"),
            ",".join(sorted((m.get("audit_dims") or {}).keys())) or "—"))
    lines += ["", "## 同题集 ≠ 同输入（按「输入文件 × 匿名题」分组，逐文件保留不覆盖）", "",
              "| 文件 | 匿名 | 跑数 | stable去重 | raw去重 | 仅钟点漂移 |", "|---|---|---|---|---|---|"]

    def _acell(v):
        """聚合单元：仅渲染白名单原生类型；None（=非法/缺失）一律显示 `unknown`，绝不回显原文本。"""
        return "unknown" if v is None else v
    for x in same_input:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            x.get("file", "?"), x.get("anon", "?"),
            _acell(x.get("n_runs")), _acell(x.get("n_stable")),
            _acell(x.get("n_raw")), _acell(x.get("clock_only_drift"))))
    return "\n".join(lines) + "\n"


# ════════════════════════ CLI ════════════════════════
def _cmd_audit(args) -> int:
    paths = list(args.inputs or [])
    if not paths:
        print("[拒绝] audit 至少要一个输入文件", file=sys.stderr)
        return EXIT_BAD
    anon = Anon()
    manifest = build_manifest(paths, anon)
    # 同题多跑的输入结构差异（匿名）：按 (输入文件, 匿名题) 分组，**不按 cid 全局覆盖**
    same_input = []
    for m in manifest:
        if m.get("role") != "rows" or not m.get("exists"):
            continue
        try:
            rows = load_jsonl(m["path"])
        except Exception:  # noqa: BLE001
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            spread = r.get("fp_spread")
            spread = spread if isinstance(spread, list) else []
            others = r.get("other_runs")
            others = others if isinstance(others, list) else []
            raws = [r.get("prompt_fp")] + [x.get("prompt_fp") for x in others if isinstance(x, dict)]
            same_input.append({
                "file": m.get("file"), "anon": anon.key(r.get("cid")),
                "n_runs": _agg_int(r.get("n_runs")),          # 类型白名单：非原生 int ⇒ unknown
                "n_stable": len({x for x in spread if isinstance(x, str) and x}),
                "n_raw": len({x for x in raws if isinstance(x, str) and x}),
                "clock_only_drift": _agg_bool(r.get("clock_only_drift")),   # 非 bool ⇒ unknown
            })
    report = _render_audit(manifest, same_input)
    outp = args.out or "-"
    if outp == "-":
        print(report)
    else:
        real = _write_text_guarded(outp, report, "审计报表")
        print("审计报表 → %s" % real)
    if args.map_out:
        real = guard_outside_repo(args.map_out, "匿名反查映射")
        _write_jsonl_strict(real, [anon.dump()])
        print("匿名映射 → %s（私有，勿外传）" % real)
    return EXIT_OK


def _cmd_replay(args) -> int:
    evpath = args.evidence
    if not evpath or not os.path.isfile(evpath):
        print("[拒绝] replay 需要 --evidence 指向完整私有留底 jsonl", file=sys.stderr)
        return EXIT_BAD
    try:
        records = load_jsonl(evpath)
    except Exception as e:  # noqa: BLE001
        print("[拒绝] 留底读取失败（类型=%s，不回显内容）" % type(e).__name__, file=sys.stderr)
        return EXIT_BAD

    case_map = None
    if args.dataset:
        case_map = {}
        for c in load_jsonl(args.dataset):
            if c.get("cid"):
                case_map[str(c["cid"])] = c

    results = replay_all(records, case_map=case_map)

    # 与普通 rows 对账（真实 folded 形态，可选）
    row_recon = None
    if args.rows:
        row_recon = reconcile_rows(results, load_jsonl(args.rows))

    anon = Anon()
    paths = [evpath] + ([args.rows] if args.rows else []) + ([args.dataset] if args.dataset else [])
    meta = build_manifest(paths, anon)
    report = _render_replay(results, anon, meta, row_recon)
    outp = args.out or "-"
    if outp == "-":
        print(report)
    else:
        real = _write_text_guarded(args.out, report, "复算报表")
        print("复算报表 → %s" % real)
    if args.detail_out:
        rows_out = [dict(_strip_priv(r), kind="replay") for r in results]
        rows_out += [dict(_strip_priv(x), kind="row_recon") for x in (row_recon or [])]
        real = _write_jsonl_strict(args.detail_out, rows_out)
        print("逐跑详细 → %s（含指纹维度与原因，不含正文）" % real)
    if args.map_out:
        real = guard_outside_repo(args.map_out, "匿名反查映射")
        _write_jsonl_strict(real, [anon.dump()])
        print("匿名映射 → %s（私有，勿外传）" % real)
    # 冲突（重复键 / rows 内重复）＝证据自相矛盾，不给干净退出码（修复①）
    has_conflict = (any(r.get("bucket") == B_CONFLICT for r in results)
                    or any(x.get("recon") == R_ROWS_CONFLICT for x in (row_recon or [])))
    return EXIT_CONFLICT if has_conflict else EXIT_OK


def _strip_priv(d: dict) -> dict:
    """剥掉 `_` 前缀私有键（坐标等），只留公开结构。"""
    return {k: v for k, v in d.items() if not str(k).startswith("_")}


def main(argv=None) -> int:
    _sandbox_db_env()               # 任何 app 导入之前
    ap = argparse.ArgumentParser(
        prog="memory_private_replay",
        description="A30 私有留底离线复算闭环（零 LLM / 零计费 / 不连生产库）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("audit", help="固定输入清单 + 同源性逐维盘点（只读旧产物）")
    a.add_argument("--inputs", nargs="*", default=[], help="旧产物路径（rows/prompts/evidence/dataset）")
    a.add_argument("--out", default="-", help="报表路径（默认 stdout；仓库外）")
    a.add_argument("--map-out", default=None, help="匿名反查映射（私有，须仓库外）")
    a.set_defaults(func=_cmd_audit)

    r = sub.add_parser("replay", help="从完整留底按原判据重放判分并与存值/rows 对账")
    r.add_argument("--evidence", required=True, help="完整私有留底 jsonl")
    r.add_argument("--dataset", default=None, help="题面原件（可选，用于独立校验 case_source_fp）")
    r.add_argument("--rows", default=None, help="普通 rows（可选，folded 形态逐跑对账）")
    r.add_argument("--out", default="-", help="复算报表路径（默认 stdout；仓库外）")
    r.add_argument("--detail-out", default=None, help="逐跑详细结果（须仓库外，显式才写）")
    r.add_argument("--map-out", default=None, help="匿名反查映射（私有，须仓库外）")
    r.set_defaults(func=_cmd_replay)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as e:
        print("[拒绝] %s" % e, file=sys.stderr)
        return EXIT_BAD
    except RuntimeError as e:
        print("[失败] %s" % e, file=sys.stderr)
        return EXIT_REPLAY


if __name__ == "__main__":
    sys.exit(main())
