# -*- coding: utf-8 -*-
"""A3 阶段 1 · 序 2 —— 记忆时态「离线回放器」+ 样本集导出（只读生产库，零生产代码改动）。

本脚本自包含：prompt 形状、输出解析、统计口径全在这里；判定规则**直载**生产代码
`backend/app/memory/tense.py`（不另写第二套规则），且不走 `import app.memory.tense`
——`app/memory/__init__.py` 会连带 import service/decay/extractor，触达 DB 与向量目录。

三条例（怎么用）
----------------
① 导出样本集（纯只读，不碰 LLM）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\decision_tense_replay.py --export
    # 可选：--db <路径> --out-dir <目录> --state-max 300

    产出 <out-dir>/tense_samples_<YYYYMMDD>.jsonl       （逐条样本）
         <out-dir>/tense_samples_<YYYYMMDD>_stats.json   （总数/类别分布/长度分位）
    <!-- out-dir -->缺省＝仓库上一级的 ``tense_output/``，可用 ``--out-dir`` 或环境变量 ``AMBRACE_TENSE_OUT`` 覆盖。

② 回放（**管线自检**，零 LLM，随时可跑）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\decision_tense_replay.py --replay --backend rule
    # 候选 = 现网生效规则在样本自带原文上重算一遍；一致率应为 100%
    # 不满 100% 就说明「导出 → 读回 → 统计」某一环串了假数据，报告第 7 节列出逐条差异

③ 回放（路径 C 单 token 薄约定，**真调模型**）::

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\decision_tense_replay.py --replay --backend llm --allow-llm

    **必须显式加 --allow-llm**，否则一进门就报错退出（防误跑烧额度）；
    --limit 默认 50、硬上限 200（写死 LLM_LIMIT_HARD_MAX），且在任何文件/连接之前拦截。

只读纪律
--------
- 取数一律 `sqlite3.connect("file:...?mode=ro", uri=True)` + `PRAGMA query_only=ON`，
  全脚本对生产库只发 `SELECT`；严禁写库、严禁 alembic、不动 `backend/data/` 任何文件。
- 已知副作用（仅 ③ 走真通道时）：`app.agent.llm_client` 自身会往 `llm_usage` 记一行用量。
  回放器不写任何库表，但跑 ③ 前要知情。

口径（已拍定，写死在本脚本）
--------------------------
- 真值 = `_tcls` 现网生效值 = `tense_hint=None` 时
  `"episodic" if is_happened_source(m) else classify_tense(m)`（`app/memory/format.py:83`）；
  副口径纯 `classify_tense` 另存一列（两口径在 5536 条里差 32.7%）。
- 本轮只算**一致率**（候选 vs 规则）。严格「误判率 ≤ 现规则」需人工标注子集，后置为序 4。
- `plan` 类评测时窗：`now := created_at`（还原当时语境）；`now := 今天` 作为敏感性切片同表给出。
- `transient` 降级为**附带观测**（现网生效口径仅个位数），不进主指标。
- 样本面：主集 = 可注入面（`is_archived=0 AND status='active'`）；`plan` 子集（全库 `_tcls=='plan'`，
  含归档/失效行）单独成池，靠样本的 `pools` 字段区分。
"""
from __future__ import annotations

import argparse
import builtins
import importlib.util
import json
import math
import os
import sqlite3
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

# ────────────────────────────── 常量 ──────────────────────────────

REPO_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_DIR / "backend"
TENSE_PY = BACKEND_DIR / "app" / "memory" / "tense.py"
META_GUARD_PY = BACKEND_DIR / "app" / "memory" / "meta_guard.py"
DEFAULT_DB = BACKEND_DIR / "data" / "sqlite" / "ai_companion.db"
# 输出目录：先环境变量、再仓库外相对默认值（**不得写死本机路径**：脚本会随公开仓一起发布）
DEFAULT_OUT_DIR = Path(os.environ.get("AMBRACE_TENSE_OUT") or (REPO_DIR.parent / "tense_output"))

TENSE_OPTIONS = ("enduring", "episodic", "plan", "transient")   # 输出域（domain/decision/layer.py:263）
CHOICE_TO_LABEL = {"1": "enduring", "2": "episodic", "3": "plan", "4": "transient"}

STATE_MAX_CHARS = 300          # 候选输入截断上限（勘察 §2.4；留痕用的 _STATE_MAX=120 不够喂模型）
CALLER_CAPS = (80, 120, 150, 240)   # 四个真实调用点的截断口径（勘察 §3.4，导出时顺带量化翻转量）
LLM_LIMIT_DEFAULT = 50
LLM_LIMIT_HARD_MAX = 200
POOLS = ("injectable", "plan_all")

# 显示态前缀（复刻 app/memory/format.py:88-98 的只读镜像；本脚本不改生产代码）
_STALE_FAMILY = ("stale", "superseded", "expired")
_TENSE_TAG = {"episodic": "［往事］", "transient": "［当时状态］"}

SELECT_SQL = (
    "SELECT id, character_id, memory_type, sub_type, title, content, why_it_matters, "
    "       is_core, core_category, created_at, valid_to, status, is_archived "
    "  FROM memories"
)


# ──────────────────────── 生产规则直载（只读） ────────────────────────

_RULES = None


def _load_module_from_file(name: str, path: Path, alias_map: dict | None = None) -> types.ModuleType:
    """按文件路径装载模块；alias_map 把「绝对导入名 → 已装载模块」短路掉。

    alias_map 用临时替换 `builtins.__import__` 实现（只在 exec 期间生效、exec 完立即还原）：
    比往 sys.modules 里塞 `app` / `app.memory` 空壳包安全——那会遮蔽真实包，污染同进程的其他使用方。
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法从文件加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        if alias_map:
            real_import = builtins.__import__

            def _shim(import_name, globals=None, locals=None, fromlist=(), level=0):
                if import_name in alias_map and level == 0:
                    return alias_map[import_name]
                return real_import(import_name, globals, locals, fromlist, level)

            builtins.__import__ = _shim
        try:
            spec.loader.exec_module(mod)
        finally:
            if alias_map:
                builtins.__import__ = real_import
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def load_rules():
    """直载 `backend/app/memory/tense.py`（生产规则本体），绕开 app.memory.__init__ 副作用。

    tense.py 顶部是 `from app.memory.meta_guard import ...`；meta_guard 自己只 import re，
    所以先按文件路径装一份私有副本，再用 alias_map 把那条绝对导入接到副本上——
    全程不 import `app` / `app.memory`，也就不会触达 service/decay/extractor 的 DB 与向量目录。
    结果缓存（规则是同步纯函数，重复装载没有意义）。
    """
    global _RULES
    if _RULES is not None:
        return _RULES
    if not TENSE_PY.exists():
        raise FileNotFoundError(f"找不到生产规则文件: {TENSE_PY}")
    if not META_GUARD_PY.exists():
        raise FileNotFoundError(f"找不到规则依赖文件: {META_GUARD_PY}")
    mg = _load_module_from_file("_ambrace_replay_meta_guard", META_GUARD_PY)
    _RULES = _load_module_from_file("_ambrace_replay_tense", TENSE_PY,
                                    alias_map={"app.memory.meta_guard": mg})
    return _RULES


# ────────────────────────────── 取值 / 时间 ──────────────────────────────

def parse_dt(value, default=None):
    """把 sqlite 的 DATETIME 文本归一成 naive datetime（勘察 §3.6 约束 1）。

    sqlite3 取回字符串、生产 ORM 取回 datetime，不归一会 in `plan_valid_until` 的日期加法处
    TypeError。带 tzinfo 的一律去掉（项目约定库内存 UTC naive）。
    """
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip().replace("T", " ")
    if not text:
        return default
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return default


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_date(value) -> str:
    """取 YYYY-MM-DD（datetime 与文本都吃，取不到给空串）。"""
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else str(value or "")[:10]


def row_to_mem(row: dict) -> dict:
    """样本行 → `classify_tense` / `is_plan_expired` 可吃的 dict（缺字段由 tense._g 静默降级）。

    只喂规则真读的字段；title/content/why_it_matters 必须是**未截断原文**，否则重算出的真值会漂
    （勘察 §3.4：content[:80] 就有 19 条生效标签翻转）。
    """
    return {
        "memory_type": row.get("memory_type") or "",
        "sub_type": row.get("sub_type") or "",
        "title": row.get("title") or "",
        "content": row.get("content") or "",
        "why_it_matters": row.get("why_it_matters") or "",
        "is_core": bool(row.get("is_core") or 0),
        "core_category": row.get("core_category"),
        "created_at": parse_dt(row.get("created_at")),
        "valid_to": parse_dt(row.get("valid_to")),
        "status": row.get("status") or "",
    }


def truth_effective(rules, mem: dict) -> str:
    """真值 = `_tcls` 现网生效值（tense_hint=None 口径，format.py:83）。"""
    return "episodic" if rules.is_happened_source(mem) else rules.classify_tense(mem)


def truth_raw(rules, mem: dict) -> str:
    """副口径 = 纯 `classify_tense`（不含「天然已发生来源」短路）。"""
    return rules.classify_tense(mem)


# ────────────────────────────── state 文本拼装 ──────────────────────────────

def build_state_text(text: str, record_date: str, now_date: str, max_len: int = STATE_MAX_CHARS) -> str:
    """按勘察 §2.4 拼候选输入：正文截断上限 + `created_at` / `now` 两行时间锚。

    - 只截正文、保留头部；两行时间锚不参与截断（plan/episodic 的分界有一半在时间上）；
    - 不给 memory_type / sub_type / is_core：规则吃「元数据 + 文本」，候选只吃文本才量得出
      语义能力，且现网 sub_type 有 742 条 NULL，喂元数据会在缺失面上直接失真；
    - 正文全空返回空串，导出侧据此丢弃样本。
    """
    body = " ".join(str(text or "").split())
    if not body:
        return ""
    if max_len and max_len > 0:
        body = body[:max_len]
    return f"记录日期：{record_date}\n现在：{now_date}\n{body}"


def display_tag(label: str, expired: bool, status) -> str:
    """生效标签（plan 过期二分 + status 改写），与 format.py:88-98 一致；enduring 不加。"""
    if str(status or "").strip().lower() in _STALE_FAMILY:
        return "［往事/已过时］"
    if label == "plan":
        return "［旧安排·已过期］" if expired else "［计划］"
    return _TENSE_TAG.get(label, "")


# ────────────────────────────── prompt 与解析 ──────────────────────────────

SYSTEM_PROMPT = "你只做一件事：判断一条记忆属于哪一类。只输出一个数字，不要任何解释、标点或换行。"

# 路径 C 薄约定：候选集 = 4 个单 token 编号（封闭答案空间），形状按勘察 §2.3
USER_PROMPT_TMPL = (
    "【任务】判断下面这条记忆相对于「现在」属于哪一类。\n"
    "【类别】\n"
    "1 = 长期成立的事实/偏好/关系/身份\n"
    "2 = 已经发生的往事\n"
    "3 = 尚未发生的安排/计划/行程\n"
    "4 = 当时的瞬时状态（情绪/位置/在途，很快过去）\n"
    "【判定纪律】\n"
    "- 只依据文本本身判断，不要脑补文本之外的事。\n"
    "- 文本出现「明天/计划/打算」不等于「安排」：转述别人说过的、反问、已取消或已完成的，都不算。\n"
    "- 写了「回来了/到家/结束了/去过了」这类完成信号的，算 2。\n"
    "- 拿不准时选 2（宁可当往事，不要把旧事当现状）。\n"
    "【记忆】{state}\n"
    "【输出】只输出 1 / 2 / 3 / 4 中的一个数字。"
)


def render_messages(state_text: str) -> list:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT_TMPL.replace("{state}", state_text)},
    ]


def parse_choice(raw) -> tuple:
    """响应 → (类别 或 None, 失败原因 或 None)。失败模式只有「首个非空字符 ∉ 1..4」。"""
    if raw is None:
        return None, "no_token"
    if isinstance(raw, tuple):       # chat_completion(include_reasoning=True) 会回 (content, reasoning)
        raw = raw[0]
    text = str(raw).strip()
    if not text:
        return None, "no_token"
    label = CHOICE_TO_LABEL.get(text[0])
    if label is None:
        return None, "out_of_set"
    return label, None


# ────────────────────────────── 导出 ──────────────────────────────

def connect_ro(db_path) -> sqlite3.Connection:
    """只读连接（mode=ro 直接拒写）+ 双保险 PRAGMA query_only=ON。"""
    path = str(db_path)
    if not os.path.exists(path):
        raise SystemExit(f"[abort] 数据库不存在: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def export_samples(conn: sqlite3.Connection, *, db_path: str = "", now_today: datetime | None = None,
                   state_max: int = STATE_MAX_CHARS) -> tuple:
    """从生产库（只读）导样本集，返回 (samples, stats)。

    池：`injectable` = is_archived=0 AND status='active'（线上真正走到 `_tcls` 的集合）；
        `plan_all`   = 全库 `_tcls=='plan'`（含归档/失效行——plan 稀缺，按历史时刻回放才够样本）。
    """
    rules = load_rules()
    now_today = now_today or utc_now_naive()
    rows = [dict(r) for r in conn.execute(SELECT_SQL).fetchall()]

    samples = []
    flip_counts = {cap: 0 for cap in CALLER_CAPS}
    skipped_empty = 0
    for row in rows:
        mem = row_to_mem(row)
        tcls = truth_effective(rules, mem)
        injectable = int(row.get("is_archived") or 0) == 0 and str(row.get("status") or "").strip() == "active"
        if not injectable and tcls != "plan":
            continue
        created = parse_dt(row.get("created_at"))
        anchor = created or now_today           # 拍定口径 now := created_at
        record_date = as_date(created)
        state_text = build_state_text(rules._text(mem), record_date, as_date(anchor), state_max)
        if not state_text:
            skipped_empty += 1
            continue
        expired_primary = bool(rules.is_plan_expired(mem, anchor))
        expired_today = bool(rules.is_plan_expired(mem, now_today))
        pools = [p for p in POOLS
                 if (p == "injectable" and injectable) or (p == "plan_all" and tcls == "plan")]
        samples.append({
            "sample_id": row["id"],
            "memory_id": row["id"],
            "character_id": row.get("character_id"),
            "pools": pools,
            "state_text": state_text,
            "record_date": record_date,
            "created_at": str(row.get("created_at") or "") or None,
            "now_date": as_date(anchor),
            "now_anchor": "created_at" if created else "no_anchor",
            "truth_rule": tcls,                       # 真值来源：_tcls 现网生效值（tense_hint=None）
            "truth_source": "effective_tcls",
            "truth_rule_raw": truth_raw(rules, mem),  # 敏感性副口径：纯 classify_tense
            "truth_display_tag": display_tag(tcls, expired_primary, row.get("status")),
            "is_plan_expired_rule": expired_primary,
            "is_plan_expired_at_today": expired_today,
            "truth_display_tag_at_today": display_tag(tcls, expired_today, row.get("status")),
            "valid_to": str(row.get("valid_to") or "") or None,
            "memory_type": row.get("memory_type"),
            "sub_type": row.get("sub_type"),
            "is_core": bool(row.get("is_core") or 0),
            "core_category": row.get("core_category"),
            "status": row.get("status"),
            "tense_hint_used": False,                 # 未开闸：回放器恒 tense_hint=None（勘察 §3.6）
            # 规则重放所需的未截断原文（rule 后端靠这三段重算，自检才真的过了一遍管线）
            "title": row.get("title") or "",
            "content": row.get("content") or "",
            "why_it_matters": row.get("why_it_matters") or "",
        })
        if injectable:
            for cap in CALLER_CAPS:
                capped = dict(mem, content=(mem["content"] or "")[:cap])
                if truth_effective(rules, capped) != tcls:
                    flip_counts[cap] += 1

    return samples, build_export_stats(samples, rows, skipped_empty, flip_counts, str(db_path))


def percentile(values, q: float):
    """最近秩法（nearest-rank）；空集合返回 None。"""
    vals = sorted(v for v in (values or []) if v is not None)
    if not vals:
        return None
    idx = max(0, min(len(vals) - 1, math.ceil(q / 100.0 * len(vals)) - 1))
    return vals[idx]


def _plan_slice(items, *, truth_key="truth", expired_now="expired_primary", expired_today="expired_today"):
    """plan 类在两个时窗锚点下的未过期/已过期拆分（敏感性对比用）。"""
    plans = [it for it in items if it.get(truth_key) == "plan"]
    n = len(plans)
    e1 = sum(1 for p in plans if p.get(expired_now))
    e2 = sum(1 for p in plans if p.get(expired_today))
    return {"plan_total": n,
            "anchor_created_at": {"not_expired": n - e1, "expired": e1},
            "anchor_today": {"not_expired": n - e2, "expired": e2}}


def build_export_stats(samples, rows, skipped_empty: int, flip_counts, db_path: str) -> dict:
    """导出侧统计：总数、按类别分布、文本长度分位（+ 截断敏感性、plan 时窗切片）。"""
    def dist(key):
        return {lab: sum(1 for s in samples if s[key] == lab) for lab in TENSE_OPTIONS}

    injectable = [s for s in samples if "injectable" in s["pools"]]
    plan_all = [s for s in samples if "plan_all" in s["pools"]]
    body_lens = [len(s["state_text"].rsplit("\n", 1)[-1]) for s in samples]
    return {
        "generated_at_utc": utc_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": db_path,
        "db_rows_scanned": len(rows),
        "n_samples": len(samples),
        "skipped_empty_text": skipped_empty,
        "n_pools": {
            "injectable": len(injectable),
            "plan_all": len(plan_all),
            "plan_not_injectable": len([s for s in plan_all if "injectable" not in s["pools"]]),
        },
        "truth_rule_dist": dist("truth_rule"),
        "truth_rule_raw_dist": dist("truth_rule_raw"),
        "truth_rule_dist_injectable": _dist_of(injectable),
        "state_len": {
            "n": len(body_lens),
            "median": percentile(body_lens, 50),
            "p75": percentile(body_lens, 75),
            "p90": percentile(body_lens, 90),
            "p95": percentile(body_lens, 95),
            "p99": percentile(body_lens, 99),
            "max": max(body_lens) if body_lens else None,
        },
        "truncation_flip_counts": dict(flip_counts),
        "plan_expiry_slice": {"injectable": _plan_slice(injectable, truth_key="truth_rule",
                                                        expired_now="is_plan_expired_rule",
                                                        expired_today="is_plan_expired_at_today"),
                              "plan_all": _plan_slice(plan_all, truth_key="truth_rule",
                                                      expired_now="is_plan_expired_rule",
                                                      expired_today="is_plan_expired_at_today")},
    }


def _dist_of(samples) -> dict:
    return {lab: sum(1 for s in samples if s["truth_rule"] == lab) for lab in TENSE_OPTIONS}


def write_jsonl(samples, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(s, ensure_ascii=False) for s in samples]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def find_latest_samples(out_dir: Path):
    files = sorted(Path(out_dir).glob("tense_samples_*.jsonl"))
    return files[-1] if files else None


# ────────────────────────────── 回放 ──────────────────────────────

def _mk_result(sample, candidate, fail_reason, latency_ms, raw_output=""):
    return {
        "sample_id": sample["sample_id"],
        "pools": sample.get("pools") or [],
        "truth": sample["truth_rule"],
        "truth_raw": sample.get("truth_rule_raw"),
        "candidate": candidate,
        "fail_reason": fail_reason,
        "agree": candidate is not None and candidate == sample["truth_rule"],
        "latency_ms": latency_ms,
        "prompt_chars": len(sample.get("state_text") or ""),
        "completion_chars": len(raw_output or ""),
        "raw_output": (raw_output or "")[:40],
        "is_plan": sample["truth_rule"] == "plan",
        "expired_primary": bool(sample.get("is_plan_expired_rule")),
        "expired_today": bool(sample.get("is_plan_expired_at_today")),
    }


def replay_rule(samples) -> list:
    """候选 = 现网生效规则，在样本自带的未截断原文上**重算**（管线自检）。

    期望一致率 100%：候选与真值是同一条规则，不等就证明导出/读回/统计串了东西。
    """
    rules = load_rules()
    out = []
    for s in samples:
        t0 = time.perf_counter()
        candidate = truth_effective(rules, row_to_mem(s))
        out.append(_mk_result(s, candidate, None, round((time.perf_counter() - t0) * 1000, 3)))
    return out


def replay_llm(samples, limit: int, *, allow_llm: bool, task: str = "decision") -> list:
    """候选 = 路径 C 单 token 薄约定（现有通道 temperature=0 / max_tokens=1，逐条串行）。

    没 `--allow-llm` 直接 SystemExit，绝不触网；`limit` 超硬上限同样直接退出。
    通道异常按 no_token / out_of_set / timeout / exception 归类，不重试、不吞。
    通道只回文本不回 usage（结构性事实 P1），故 token 侧只给字符量，真实值看 llm_usage.task。
    """
    if not allow_llm:
        raise SystemExit("[abort] --backend llm 必须显式加 --allow-llm 才会真的调用模型（防误跑烧额度）")
    if limit > LLM_LIMIT_HARD_MAX:
        raise SystemExit(f"[abort] --limit 硬上限 {LLM_LIMIT_HARD_MAX}（本阶段不跑大批），当前 {limit}")
    picked = [s for s in samples if (s.get("state_text") or "").strip()][:max(1, limit)]
    if not picked:
        raise SystemExit("[abort] 样本集里没有可用 state")

    import asyncio

    if str(BACKEND_DIR) not in sys.path:
        sys.path.insert(0, str(BACKEND_DIR))
    from app.agent.llm_client import chat_completion      # 延迟 import：只有真跑 LLM 才碰生产包

    async def _one(sample):
        kwargs = {"messages": render_messages(sample["state_text"]), "temperature": 0.0,
                  "max_tokens": 1, "task": task}
        if sample.get("character_id"):
            kwargs["character_id"] = sample["character_id"]
        t0 = time.perf_counter()
        raw = ""
        fail = None
        try:
            raw = await chat_completion(**kwargs)
        except asyncio.TimeoutError:
            fail = "timeout"
        except Exception as e:  # noqa: BLE001 - 通道异常要如实归类进失败率，不能打断整批
            fail = f"exception:{type(e).__name__}"
        latency = round((time.perf_counter() - t0) * 1000, 3)
        text = str(raw[0]) if isinstance(raw, tuple) else str(raw or "")
        candidate, parse_fail = (None, fail) if fail else parse_choice(raw)
        return _mk_result(sample, candidate, fail or parse_fail, latency, text)

    async def _run_serial():
        out = []
        for s in picked:
            r = await _one(s)
            out.append(r)
            print(f"  [{len(out)}/{len(picked)}] sample={r['sample_id']} truth={r['truth']} "
                  f"cand={r['candidate']} fail={r['fail_reason']} {r['latency_ms']}ms")
        return out

    print(f"[llm] 单 token 通道串行跑 {len(picked)} 条（task={task}, temperature=0, max_tokens=1）")
    return asyncio.run(_run_serial())


def summarize(results) -> dict:
    """统计口径：一致率（总体 + 分类别召回）、格式失败率、延迟 P50/P95、token 观测。

    - 主指标 `agreement_main` 排除 truth=transient 的样本（拍板：降级为附带观测）；
    - `agreement_overall` 分母 = 成功产出类别的样本；`agreement_counting_failures` 把格式失败
      计为不一致（两个口径都给，避免「失败即免检」）。
    """
    n_total = len(results)
    ok = [r for r in results if r.get("candidate")]
    bad = [r for r in results if not r.get("candidate")]
    by_truth = {}
    for label in TENSE_OPTIONS:
        cls = [r for r in results if r["truth"] == label]
        cls_ok = [r for r in cls if r.get("candidate")]
        agreed = sum(1 for r in cls_ok if r["agree"])
        by_truth[label] = {"n": len(cls), "n_evaluated": len(cls_ok), "n_agreed": agreed,
                           "n_failed": len(cls) - len(cls_ok),
                           "recall": round(agreed / len(cls_ok), 4) if cls_ok else None}
    main = [r for r in ok if r["truth"] != "transient"]
    main_agreed = sum(1 for r in main if r["agree"])
    lat = [r["latency_ms"] for r in results if r.get("latency_ms") is not None]
    fails = {}
    for r in bad:
        key = str(r.get("fail_reason") or "unknown").split(":")[0]
        fails[key] = fails.get(key, 0) + 1
    transient = [r for r in results if r["truth"] == "transient"]
    agreed_all = sum(1 for r in ok if r["agree"])
    return {
        "n_total": n_total,
        "n_evaluated": len(ok),
        "n_failed": len(bad),
        "n_main": len(main),
        "agreement_overall": round(agreed_all / len(ok), 4) if ok else None,
        "agreement_counting_failures": round(agreed_all / n_total, 4) if n_total else None,
        "agreement_main": round(main_agreed / len(main), 4) if main else None,
        "format_failure_rate": round(len(bad) / n_total, 4) if n_total else None,
        "by_truth_class": by_truth,
        "transient_observed": {"n": len(transient), "n_agreed": sum(1 for r in transient if r["agree"])},
        "fail_reasons": fails,
        "latency_p50_ms": percentile(lat, 50),
        "latency_p95_ms": percentile(lat, 95),
        "prompt_chars_total": sum(r.get("prompt_chars") or 0 for r in results),
        "completion_chars_total": sum(r.get("completion_chars") or 0 for r in results),
        "plan_expiry_slice": _plan_slice(results),
    }


# ────────────────────────────── 报告 ──────────────────────────────

def _pct(v):
    return "n/a" if v is None else f"{v * 100:.2f}%"


def render_report(backend: str, results, stats_by_pool: dict, *, samples_path, db_path: str,
                  extra_notes=()) -> str:
    """两种 backend 共用同一份结构（rule 自检 / llm 真跑输出同一套指标）。"""
    st = stats_by_pool["all"]
    lines = [
        f"# 记忆时态离线回放报告 · backend={backend}",
        "",
        f"- 生成时间（UTC naive）：{utc_now_naive().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本来源：`{samples_path}`",
        f"- 数据库（只读 mode=ro + query_only）：`{db_path}`",
        "- 真值口径：`_tcls` 现网生效值（tense_hint=None）；副口径 `classify_tense` 已随样本另存",
        f"- 候选口径：{'现网规则重算（管线自检）' if backend == 'rule' else '路径 C 单 token 薄约定（1/2/3/4）'}",
        "- 时窗锚：`now := created_at`（plan 类还原当时语境）；`now := 今天` 见第 5 节敏感性切片",
        "",
        "## 1. 一致率（候选 vs 规则真值）",
        "",
        "| 指标 | 数值 | 分母 |",
        "|---|---|---|",
        f"| 主指标（排除 transient） | {_pct(st['agreement_main'])} | {st['n_main']} |",
        f"| 总体（只算成功产出类别） | {_pct(st['agreement_overall'])} | {st['n_evaluated']} |",
        f"| 总体（格式失败计为不一致） | {_pct(st['agreement_counting_failures'])} | {st['n_total']} |",
        "",
        "### 分类别（按真值类的召回 = 该类里候选判对的比例）",
        "",
        "| 类别 | 样本数 | 有效判定 | 一致 | 召回 | 格式失败 |",
        "|---|---|---|---|---|---|",
    ]
    for label in TENSE_OPTIONS:
        c = st["by_truth_class"][label]
        recall = "n/a" if c["recall"] is None else _pct(c["recall"])
        lines.append(f"| {label} | {c['n']} | {c['n_evaluated']} | {c['n_agreed']} | {recall} | {c['n_failed']} |")
    t_obs = st["transient_observed"]
    lines += [
        "",
        f"> `transient` 附带观测：{t_obs['n']} 条里一致 {t_obs['n_agreed']} 条（现网生效口径稀缺，不计入主指标）。",
        "",
        "### 分池",
        "",
        "| 池 | 样本数 | 主指标一致率 | 格式失败率 |",
        "|---|---|---|---|",
    ]
    for name in POOLS:
        p = stats_by_pool.get(name)
        if p:
            lines.append(f"| {name} | {p['n_total']} | {_pct(p['agreement_main'])} | "
                         f"{_pct(p['format_failure_rate'])} |")
    lines += [
        "",
        "## 2. 格式失败率",
        "",
        f"- 失败条数：{st['n_failed']} / {st['n_total']} = {_pct(st['format_failure_rate'])}",
        f"- 失败原因分布：`{json.dumps(st['fail_reasons'], ensure_ascii=False)}`"
        "（no_token=空输出 / out_of_set=首字符不是 1-4 / timeout / exception）",
        "",
        "## 3. 延迟",
        "",
        f"- P50 = {st['latency_p50_ms']} ms；P95 = {st['latency_p95_ms']} ms"
        + ("（rule 后端是本地纯函数计时，只作管线参考）" if backend == "rule" else "（含网络往返）"),
        "",
        "## 4. Token 消耗",
        "",
        f"- prompt 字符合计：{st['prompt_chars_total']}；completion 字符合计：{st['completion_chars_total']}",
    ]
    if backend == "rule":
        lines.append("- 本后端零 LLM 调用，token 消耗为 0。")
    else:
        lines.append("- `chat_completion` 只回文本、不回 usage（结构性事实 P1），故此处只有字符量；"
                     "真实 token 按 `llm_usage` 里 `task='decision'` 聚合。")
    lines += [
        "",
        "## 5. plan 时窗敏感性（now := created_at vs now := 今天）",
        "",
        "| 池 | plan 条数 | created_at 锚：未过期/已过期 | 今天锚：未过期/已过期 |",
        "|---|---|---|---|",
    ]
    for name, p in stats_by_pool.items():
        sl = p["plan_expiry_slice"]
        a, b = sl["anchor_created_at"], sl["anchor_today"]
        lines.append(f"| {name} | {sl['plan_total']} | {a['not_expired']} / {a['expired']} | "
                     f"{b['not_expired']} / {b['expired']} |")
    lines += [
        "",
        "## 6. 口径与注意",
        "",
        "- 本轮只算**一致率**（拿规则当真值）；严格「误判率 ≤ 现规则」需人工标注子集（M1），后置为序 4。",
        "- `transient` 已降级为附带观测，不进主指标。",
        "- 规则后端是**自检**：候选与真值同源，一致率不是 100% 即为管线失真，须逐条查差异。",
    ]
    lines += [f"- {n}" for n in extra_notes]
    if backend == "rule":
        diffs = [r for r in results if not r["agree"]]
        lines += ["", "## 7. 自检差异（不一致样本，前 20 条）", ""]
        if not diffs:
            lines.append("- 无（一致率 100%）。")
        else:
            lines.append("```json")
            lines += [json.dumps(r, ensure_ascii=False) for r in diffs[:20]]
            lines.append("```")
    lines.append("")
    return "\n".join(lines)


# ────────────────────────────── CLI ──────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="decision_tense_replay.py",
        description="记忆时态离线回放器（只读生产库；--export 导样本集 / --replay 回放并出统计）",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", action="store_true", help="从生产库（只读）导出样本集 JSONL + stats JSON")
    g.add_argument("--replay", action="store_true", help="读样本集回放并输出统计报告（须配 --backend）")
    p.add_argument("--backend", choices=("rule", "llm"), help="回放候选实现：rule=现网规则自检，llm=路径 C 单 token")
    p.add_argument("--allow-llm", action="store_true", help="--backend llm 的真调用开关，不加则直接报错退出")
    p.add_argument("--limit", type=int, default=LLM_LIMIT_DEFAULT,
                   help=f"llm 后端最多跑多少条（默认 {LLM_LIMIT_DEFAULT}，硬上限 {LLM_LIMIT_HARD_MAX}）")
    p.add_argument("--db", default=str(DEFAULT_DB), help=f"生产库路径（默认 {DEFAULT_DB}）")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="样本/报告输出目录")
    p.add_argument("--samples", default=None, help="回放用样本 JSONL（默认取 out-dir 里最新的 tense_samples_*.jsonl）")
    p.add_argument("--state-max", type=int, default=STATE_MAX_CHARS, help="state 正文截断上限（默认 300）")
    p.add_argument("--pool", choices=("all",) + POOLS, default="all", help="回放样本池过滤")
    return p


def _split_pools(results) -> dict:
    out = {"all": results}
    for name in POOLS:
        subset = [r for r in results if name in (r.get("pools") or [])]
        if subset:
            out[name] = subset
    return out


def do_export(args) -> int:
    out_dir = Path(args.out_dir)
    conn = connect_ro(args.db)
    try:
        samples, stats = export_samples(conn, db_path=args.db, state_max=args.state_max)
    finally:
        conn.close()
    stamp = utc_now_naive().strftime("%Y%m%d")
    jsonl_path = out_dir / f"tense_samples_{stamp}.jsonl"
    stats_path = out_dir / f"tense_samples_{stamp}_stats.json"
    write_jsonl(samples, jsonl_path)
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                          encoding="utf-8", newline="\n")
    print(f"[export] 样本 {len(samples)} 条 → {jsonl_path}")
    print(f"[export] 统计 → {stats_path}")
    print(f"[export] 池分布 {json.dumps(stats['n_pools'], ensure_ascii=False)}")
    print(f"[export] 真值(_tcls) 分布 {json.dumps(stats['truth_rule_dist'], ensure_ascii=False)}")
    print(f"[export] 副口径(classify_tense) 分布 {json.dumps(stats['truth_rule_raw_dist'], ensure_ascii=False)}")
    print(f"[export] 可注入面真值分布 {json.dumps(stats['truth_rule_dist_injectable'], ensure_ascii=False)}")
    print(f"[export] state 正文长度 {json.dumps(stats['state_len'], ensure_ascii=False)}")
    print(f"[export] 截断致标签翻转 {json.dumps(stats['truncation_flip_counts'], ensure_ascii=False)}")
    print(f"[export] plan 时窗切片 {json.dumps(stats['plan_expiry_slice'], ensure_ascii=False)}")
    return 0


def do_replay(args) -> int:
    if not args.backend:
        raise SystemExit("[abort] --replay 必须带 --backend rule|llm")
    if args.backend == "llm" and not args.allow_llm:
        raise SystemExit("[abort] --backend llm 必须显式加 --allow-llm 才会真的调用模型（防误跑烧额度）")
    out_dir = Path(args.out_dir)
    samples_path = Path(args.samples) if args.samples else find_latest_samples(out_dir)
    if not samples_path or not Path(samples_path).exists():
        raise SystemExit(f"[abort] 找不到样本集：{samples_path}（先跑 --export，或用 --samples 指定）")
    samples = read_jsonl(Path(samples_path))
    if args.pool != "all":
        samples = [s for s in samples if args.pool in (s.get("pools") or [])]
    if not samples:
        raise SystemExit(f"[abort] 样本集为空（pool={args.pool}）")

    if args.backend == "rule":
        results = replay_rule(samples)
    else:
        results = replay_llm(samples, args.limit, allow_llm=args.allow_llm)

    stats_by_pool = {name: summarize(rs) for name, rs in _split_pools(results).items()}
    notes = [f"本次回放池过滤 `pool={args.pool}`，样本 {len(samples)} 条。"]
    notes.append("本次未调用任何 LLM（离线回放，零模型调用）。" if args.backend == "rule"
                 else f"本次真调模型上限 {args.limit} 条。")
    report = render_report(args.backend, results, stats_by_pool,
                           samples_path=samples_path, db_path=args.db, extra_notes=notes)
    stamp = utc_now_naive().strftime("%Y%m%d_%H%M")
    report_path = out_dir / f"tense_replay_{args.backend}_{stamp}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8", newline="\n")

    all_st = stats_by_pool["all"]
    print(f"[replay:{args.backend}] 样本 {all_st['n_total']} 条")
    print(f"[replay:{args.backend}] 主指标一致率 {_pct(all_st['agreement_main'])}（分母 {all_st['n_main']}）；"
          f"总体 {_pct(all_st['agreement_overall'])}（分母 {all_st['n_evaluated']}）")
    print(f"[replay:{args.backend}] 分类别召回 "
          f"{json.dumps({k: v['recall'] for k, v in all_st['by_truth_class'].items()}, ensure_ascii=False)}")
    print(f"[replay:{args.backend}] 格式失败率 {_pct(all_st['format_failure_rate'])} "
          f"原因 {json.dumps(all_st['fail_reasons'], ensure_ascii=False)}")
    print(f"[replay:{args.backend}] 延迟 P50={all_st['latency_p50_ms']}ms P95={all_st['latency_p95_ms']}ms")
    print(f"[replay:{args.backend}] token 观测（字符量）prompt={all_st['prompt_chars_total']} "
          f"completion={all_st['completion_chars_total']}")
    print(f"[replay:{args.backend}] 报告 → {report_path}")
    if args.backend == "rule" and all_st["agreement_overall"] != 1.0:
        print("[warn] 自检未过：候选与真值同源，出现不一致说明管线失真，见报告第 7 节差异清单")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.export:
        return do_export(args)
    return do_replay(args)


if __name__ == "__main__":
    sys.exit(main())
