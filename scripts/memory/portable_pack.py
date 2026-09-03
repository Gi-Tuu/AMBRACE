# -*- coding: utf-8 -*-
"""Ariadne 模块 H：可移植记忆包（.mempak）离线工具。

只借 memoryfields 的"文件化格式"做 导出 / 冷归档 / 迁移，**不替换主存储、不造第二套事实源、
不进运行时热路径、不被主链路 import**。只做搬运与格式转换：

- 分层判定 / supersede / 冷归档（是否迁出、迁去哪层）归 #70；
- 冲突一律走 ``memory_id + version`` 合并规则，**禁止简单覆盖（last-write-wins）**；
- 导入遵循 Knowledge Scope（user/character 不串线），且**不把 superseded/stale 复活成 active**。

用法：
  # 导出活记忆（可检索集 = active/stale）
  python -m scripts.memory.portable_pack export --user 1 --char 3 --out sam.mempak

  # 导出 #70 冷归档（superseded 且已迁入 memory_archive 的行）
  python -m scripts.memory.portable_pack export --user 1 --char 3 --scope archived --out sam_arc.mempak

  # 导入（默认导入 manifest 的 user/char 作用域；--user/--char 可显式覆盖目标作用域）
  python -m scripts.memory.portable_pack import --file sam.mempak
  python -m scripts.memory.portable_pack import --file sam.mempak --user 1 --char 3 --reembed

  # 校验包结构 / manifest / 分页预算 / frontmatter 可还原性
  python -m scripts.memory.portable_pack validate --file sam.mempak

零运行时 LLM、零在线依赖；重嵌入（--reembed）为可选，依赖本地 bge（默认为关，见模块说明）。

Feature Flag / 回滚（§11）：模块 H **无运行时 flag**——纯离线工具、无运行时入口，不运行=零影响；
仅当未来要挂接运行时入口（如导出/导入按钮、自动冷归档任务）时才需加 flag 门控。
"""

# --reembed 默认为关 + 本实现不携带向量快照（index.sqlite 未写入）：对方案"导入默认重嵌"
# 为保守偏离——导入脚本默认零重嵌依赖（不加载本地 bge/Chroma），由目标端正常 ingest 或
# 显式 --reembed 重嵌；--with-vectors 仅作意图标记。见 docs/plans.md 台账与待拍板事项。

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

import yaml

# ── 允许从 backend 导入生产模型/会话（保持离线脚本可独立运行）──
_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── 格式常量（§10.2）──
PACK_FORMAT = "ambrace-mempak"
PACK_VERSION = 1
PACK_SCHEMA = "ambrace-mempak/v1"
PAGE_BUDGET = 8 * 1024  # 单页不超过 ~8KB（编码后字节）
# 可检索集 = {active, stale}（与 #70 `_retrievable_status_clause` 口径一致，但导出不受运行时
# supersede flag 影响——确定性、可离线复现；superseded 走 cold archive，不在此列）
RETRIEVABLE_STATUSES = ("active", "stale")
# 当前本地嵌入模型（app/memory/embedding.py 的 bge-m3，int8, 1024d）
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024

# frontmatter 必填字段（§10.2 一一对应，保证可还原）
FRONTMATTER_REQUIRED = (
    "memory_id",
    "memory_type",
    "created_at",
    "importance",
    "strength",
    "epistemic",
    "reliability",
    "chain_id",
    "parent_id",
    "speaker",
    "source",
    "status",
)
# frontmatter 扩展字段（提升导出一导入往返 fidelity，均为可空标量）
FRONTMATTER_EXTRA = (
    "sub_type",
    "title",
    "speaker_id",
    "version",
    "is_core",
    "is_pinned",
    "why_it_matters",
    "valid_from",
    "valid_to",
)
FRONTMATTER_FIELDS = FRONTMATTER_REQUIRED + FRONTMATTER_EXTRA

# ── 脱敏红线（§10.4 #4）：导出前对可见文本做密钥/内部敏感模式替换 ──
_SECRET_PATTERNS = (
    re.compile(r"(?i)(sk-[A-Za-z0-9]{16,})"),                                  # 常见 API key 前缀
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*\S{8,})"),                          # api_key=...
    re.compile(r"(?i)(bearer\s+[A-Za-z0-9._-]{8,})"),                          # Bearer token
    re.compile(r"(?i)(password\s*[=:]\s*\S{4,})"),                             # password=
    re.compile(r"(?i)((?:access|refresh|id|auth)[_-]?token\s*[=:]\s*\S{8,})"),  # access_token=...
    re.compile(r"(?i)(^|[^\w])(token|secret|auth)\s*[=:]\s*\S{6,}"),           # 裸 token=/secret=
    re.compile(r"(?i)(secret\s*[=:]\s*\S{8,})"),                               # secret=
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),      # JWT
)
# 若某个字段名命中敏感词，导出时直接剔除（防内字段泄漏）
_SENSITIVE_KEY_RE = re.compile(r"(?i)(secret|token|api[_-]?key|password|private[_-]?key|credential)")


class PackError(Exception):
    """.mempak 包结构/内容非法。"""


# ═══════════════════════════════════════════════════════════
# 纯函数（零 DB / 零 app 依赖，便于单测）
# ═══════════════════════════════════════════════════════════

def _normalize_dt(value) -> datetime | None:
    """统一为 naive-UTC（项目口径：库内 naive UTC，展示层再转 +8）。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        return _parse_dt(value)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(value) -> str | None:
    dt = _normalize_dt(value)
    return dt.isoformat(timespec="seconds") if dt is not None else None


def _parse_dt(value) -> datetime | None:
    """解析 ISO/常见时间字符串为 naive-UTC；失败返回 None（不猜）。"""
    if value is None:
        return None
    t = str(value).strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        return _normalize_dt(datetime.fromisoformat(t))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return _normalize_dt(datetime.strptime(t, fmt))
        except ValueError:
            continue
    return None


def _f(value) -> float | None:
    """安全转 float（None/空 返回 None）。"""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _redact(text: str | None) -> tuple[str, int]:
    """对导出前的可读文本做密钥/内部敏感模式替换；返回 (替换后文本, 替换次数)。"""
    if not text:
        return (text or "", 0)
    count = 0
    out = text
    for pat in _SECRET_PATTERNS:
        out, n = pat.subn("[REDACTED]", out)
        count += n
    return out, count


def _redact_record(record: dict) -> tuple[dict, int]:
    """对单条记录做脱敏：剔除敏感字段名 + 对 content/title/why_it_matters 做模式替换。"""
    cleaned: dict = {}
    n = 0
    for k, v in record.items():
        if _SENSITIVE_KEY_RE.search(k):
            n += 1
            continue  # 剔除敏感键（如导出数据里若混入 secret/token 字段）
        if isinstance(v, str) and k in ("content", "title", "why_it_matters"):
            v, k_n = _redact(v)
            n += k_n
        elif isinstance(v, str) and _SENSITIVE_KEY_RE.search(k):
            v, k_n = _redact(v)
            n += k_n
        cleaned[k] = v
    return cleaned, n


def _record_to_meta(record: dict) -> dict:
    """从导出记录（宽字段）抽出 frontmatter 字段（不含 content——content 是正文）。"""
    meta: dict = {}
    # 先用必填顺序，再补扩展，保证 yaml 输出稳定、可读
    for k in FRONTMATTER_REQUIRED + FRONTMATTER_EXTRA:
        if k in record:
            # 时间字段统一 ISO 字符串；数值转 float
            if k in ("created_at", "valid_from", "valid_to"):
                meta[k] = record[k]
            else:
                meta[k] = record[k]
    return meta


def build_frontmatter(record: dict) -> str:
    """构造单页 YAML frontmatter（与线上字段一一对应）。"""
    meta = _record_to_meta(record)
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n"


def build_block(record: dict) -> str:
    """单条记忆的完整页块 = frontmatter + 正文。"""
    return build_frontmatter(record) + (record.get("content") or "") + "\n"


def chunk_records(records: list[dict], page_budget: int = PAGE_BUDGET) -> tuple[list[str], int]:
    """把记录按 8KB 预算分页；返回 (页面列表, 超预算单页数)。

    单条记录（frontmatter+正文）超过预算时仍单独成页（同名记忆不可拆分），
    由调用方记入 manifest/报告作为警告，不影响其他页。
    """
    pages: list[str] = []
    buf: list[str] = []
    size = 0
    oversized = 0
    for rec in records:
        block = build_block(rec)
        bsz = len(block.encode("utf-8"))
        if size + bsz > page_budget and buf:
            pages.append("".join(buf))
            buf, size = [], 0
        buf.append(block)
        size += bsz
        if bsz > page_budget:
            oversized += 1
    if buf:
        pages.append("".join(buf))
    return pages, oversized


def parse_page(text: str, *, filename: str = "") -> tuple[dict, str]:
    """解析**单个** frontmatter 块：返回 (meta dict, content str)；格式非法抛 ValueError。

    宽松消费：frontmatter 缺字段不硬报错（缺 memory_id 由导入方计数跳过），
    仅是 frontmatter 分隔或非 mapping 时视为非法。
    """
    t = text.lstrip("\ufeff")
    if not t.startswith("---"):
        raise ValueError(f"{filename or '<page>'}: 缺 frontmatter 分隔符")
    parts = t.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"{filename or '<page>'}: frontmatter 未闭合")
    fm, body = parts[1], parts[2]
    meta = yaml.safe_load(fm) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{filename or '<page>'}: frontmatter 不是 mapping")
    return meta, body.strip("\n")


def iter_page_blocks(text: str, *, filename: str = "") -> list[tuple[dict, str]]:
    """把一个 page（可聚合多条记忆的多个 frontmatter 块）拆成若干 (meta, content)。

    对应方案参考代码的 `iter_pages`：同一页内可聚合多条记忆；逐块解析后由调用方
    （read_pack/import/validate）逐条消费。缺字段不硬报错，仅当页面内 frontmatter
    分隔符不配对或块非 mapping 时抛 ValueError（分隔符 = 恰好为 ``---`` 的行）。
    """
    lines = text.lstrip("\ufeff").split("\n")
    seps = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if not seps or len(seps) % 2 != 0:
        raise ValueError(f"{filename or '<page>'}: frontmatter 分隔符不配对")
    blocks: list[tuple[dict, str]] = []
    for k in range(0, len(seps) - 1, 2):
        open_i, close_i = seps[k], seps[k + 1]
        fm_text = "\n".join(lines[open_i + 1:close_i])
        body_start = close_i + 1
        body_end = seps[k + 2] if k + 2 < len(seps) else len(lines)
        body_text = "\n".join(lines[body_start:body_end])
        meta = yaml.safe_load(fm_text) or {}
        if not isinstance(meta, dict):
            raise ValueError(f"{filename or '<page>'}: frontmatter 不是 mapping")
        blocks.append((meta, body_text.strip("\n")))
    return blocks


def build_manifest(
    *,
    user_id: int,
    character_id: int,
    scope: str,
    count: int,
    page_count: int,
    redactions: int = 0,
    oversized: int = 0,
    vectors_included: bool = False,
    embed_model: str = EMBED_MODEL,
    embed_dim: int = EMBED_DIM,
) -> dict:
    """构造 manifest.json（§10.2：版本/导出时间/作用域/计数/嵌入模型名与维度/bge 版本）。"""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "schema": PACK_SCHEMA,
        "user_id": user_id,
        "character_id": character_id,
        "scope": scope,
        "count": count,
        "page_count": page_count,
        "exported_at": now,
        "embed_model": embed_model,
        "embed_dim": embed_dim,
        "bge_version": f"{embed_model}-int8-{embed_dim}d",
        "vectors_included": vectors_included,
        "redactions": redactions,
        "oversized_pages": oversized,
        "generator": "scripts.memory.portable_pack",
    }


def _readme_text(manifest: dict) -> str:
    return (
        f"AMBRACE 可移植记忆包（{PACK_FORMAT} v{PACK_VERSION}）\n"
        f"导出时间：{manifest.get('exported_at')}\n"
        f"作用域：user={manifest.get('user_id')} / character={manifest.get('character_id')} / scope={manifest.get('scope')}\n"
        f"记忆条数：{manifest.get('count')} | 页面：{manifest.get('page_count')}\n"
        f"嵌入模型：{manifest.get('embed_model')}（dim={manifest.get('embed_dim')}）| 含向量：{manifest.get('vectors_included')}\n"
        f"\n"
        f"⚠️ 本包包含用户与角色的敏感对话记忆，请妥善保管（复用本地存储加密约定，勿外发）。\n"
        f"此文件仅用于 导出/冷归档/迁移，不用于运行时热路径；导入遵循 Knowledge Scope，\n"
        f"不会把 superseded/stale 记忆复活成 active，冲突按 memory_id+version 合并而非覆盖。\n"
    )


def read_pack(path: str) -> tuple[dict, list[dict]]:
    """读取 .mempak zip，返回 (manifest, 页面列表)。

    页面列表元素：{"file": "pages/0001.md", "meta": {...}, "content": "..."}
    结构非法（坏 zip / 缺 manifest / frontmatter 非 mapping）抛 PackError。
    """
    if not os.path.isfile(path):
        raise PackError(f"文件不存在：{path}")
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as e:
        raise PackError(f"不是合法 zip：{e}") from e
    with zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            raise PackError("包内缺少 manifest.json")
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except json.JSONDecodeError as e:
            raise PackError(f"manifest.json 无法解析：{e}") from e
        pages: list[dict] = []
        page_names = sorted(n for n in names if n.startswith("pages/") and n.endswith(".md"))
        for nm in page_names:
            raw = zf.read(nm).decode("utf-8")
            try:
                blocks = iter_page_blocks(raw, filename=nm)
            except ValueError as e:
                raise PackError(str(e)) from e
            for meta, content in blocks:
                pages.append({
                    "file": nm,
                    "meta": meta,
                    "content": content,
                    "size": len(raw.encode("utf-8")),
                })
    return manifest, pages


def validate_pack(
    path: str,
    *,
    require_manifest_fields: bool = True,
) -> tuple[bool, list[str], dict | None]:
    """校验 .mempak 结构与内容；返回 (ok, issues, manifest)。"""
    issues: list[str] = []
    try:
        manifest, pages = read_pack(path)
    except PackError as e:
        return False, [f"包结构错误：{e}"], None
    if manifest.get("format") != PACK_FORMAT:
        issues.append(f"manifest.format 应为 {PACK_FORMAT!r}，实为 {manifest.get('format')!r}")
    try:
        v = int(manifest.get("version") or 0)
    except (TypeError, ValueError):
        v = -1
    if v != PACK_VERSION:
        issues.append(f"manifest.version 应为 {PACK_VERSION}，实为 {manifest.get('version')!r}")
    if require_manifest_fields:
        for k in ("format", "version", "user_id", "character_id", "scope", "count",
                  "embed_model", "embed_dim", "vectors_included"):
            if k not in manifest:
                issues.append(f"manifest 缺少字段：{k}")
    # 计数一致性
    try:
        count = int(manifest.get("count") or 0)
    except (TypeError, ValueError):
        count = -1
    if count != len(pages):
        issues.append(f"manifest.count={count} 与 pages 页数 {len(pages)} 不一致")
    # 每页前 8KB 预算 + frontmatter 必填 memory_id
    for pg in pages:
        meta = pg["meta"]
        if meta.get("memory_id") is None:
            issues.append(f"{pg['file']}: 缺 memory_id（导入将跳过）")
        for req in FRONTMATTER_REQUIRED:
            if req not in meta:
                issues.append(f"{pg['file']}: frontmatter 缺字段 {req}")
    # 页面字节预算（超预算单条=合法但记警告；结构性错误才判失败）
    warnings: list[str] = []
    seen_page: set[str] = set()
    for pg in pages:
        if pg["file"] in seen_page:
            continue
        seen_page.add(pg["file"])
        if pg.get("size", 0) > PAGE_BUDGET:
            warnings.append(f"WARN {pg['file']}: 页面 {pg['size']}B 超预算 {PAGE_BUDGET}B（超预算单条合法，记录于 manifest.oversized_pages）")
    if issues:
        return False, issues, manifest
    return True, warnings, manifest


def decide_import_action(existing: dict | None, incoming: dict) -> tuple[str, str]:
    """导入合并决策（纯函数，单测）：返回 (action, reason)。

    action ∈ {insert, update, conflict}；reason 说明原因。
    - 无现有行 → insert（新记忆）
    - id 已被其它作用域占用 → conflict（Knowledge Scope 不串线）
    - existing 为 superseded/stale 且 incoming 为 active → conflict（不复活）
    - incoming.version < existing.version → conflict（旧版本，不 last-write-wins）
    - 其余 → update（forward version merge，保留原 id）
    """
    if existing is None:
        return ("insert", "new")
    if (existing.get("user_id") != incoming.get("user_id")
            or existing.get("character_id") != incoming.get("character_id")):
        return ("conflict", "id_taken_other_scope")
    if existing.get("status") in ("superseded", "stale") and incoming.get("status") == "active":
        return ("conflict", "no_resurrect")
    if (incoming.get("version") or 0) < (existing.get("version") or 0):
        return ("conflict", "stale_version")
    return ("update", "version_merge")


# ═══════════════════════════════════════════════════════════
# DB 侧（延迟 import app.*，保持纯函数/CLI 可快速加载）
# ═══════════════════════════════════════════════════════════

def _memory_to_record(m) -> dict:
    """ORM Memory → 导出记录（宽字段）。"""
    return {
        "memory_id": m.id,
        "memory_type": m.memory_type,
        "sub_type": m.sub_type,
        "title": m.title,
        "created_at": _iso(_normalize_dt(m.created_at)),
        "importance": _f(m.importance),
        "strength": _f(m.strength_days),
        "epistemic": m.epistemic_status,
        "reliability": _f(m.reliability_score),
        "chain_id": m.chain_id,
        "parent_id": m.parent_id,
        "speaker": m.speaker_type,
        "speaker_id": m.speaker_id,
        "source": m.source,
        "status": m.status,
        "version": int(m.version or 0),
        "is_core": bool(m.is_core),
        "is_pinned": bool(m.is_pinned),
        "why_it_matters": m.why_it_matters,
        "valid_from": _iso(_normalize_dt(m.valid_from)),
        "valid_to": _iso(_normalize_dt(m.valid_to)),
        "content": m.content,
    }


def _archive_to_record(row) -> dict:
    """MemoryArchive 行 → 导出记录（payload 已存原行 JSON 快照）。"""
    try:
        payload = json.loads(row.payload or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "memory_id": row.memory_id or payload.get("id"),
        "memory_type": payload.get("memory_type") or "event",
        "sub_type": payload.get("sub_type"),
        "title": payload.get("title"),
        "created_at": _iso(payload.get("created_at")),
        "importance": _f(payload.get("importance")),
        "strength": _f(payload.get("strength_days")),
        "epistemic": payload.get("epistemic_status"),
        "reliability": _f(payload.get("reliability_score")),
        "chain_id": payload.get("chain_id"),
        "parent_id": payload.get("parent_id"),
        "speaker": payload.get("speaker_type"),
        "speaker_id": payload.get("speaker_id"),
        "source": payload.get("source"),
        "status": payload.get("status") or "superseded",
        "version": int(payload.get("version") or 0),
        "is_core": bool(payload.get("is_core")),
        "is_pinned": bool(payload.get("is_pinned")),
        "why_it_matters": payload.get("why_it_matters"),
        "valid_from": _iso(payload.get("valid_from")),
        "valid_to": _iso(payload.get("valid_to")),
        "content": payload.get("content") or "",
    }


async def query_records_for_export(user_id: int, character_id: int, scope: str = "all") -> list[dict]:
    """按作用域取导出记录：scope=all 系可检索活记忆；scope=archived 系冷归档行。

    - 走确定性口径（status ∈ {active, stale} 且 is_archived=False，排除 working_state），
      不依赖运行时 supersede flag，保证可离线复现；
    - 冷归档（memory_archive）只进不出，导出其 payload 快照，不触碰热表。
    """
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.memory import Memory, MemoryArchive

    records: list[dict] = []
    async with async_session_factory() as db:
        if scope == "archived":
            rows = (await db.execute(
                select(MemoryArchive).where(
                    MemoryArchive.user_id == user_id,
                    MemoryArchive.character_id == character_id,
                ).order_by(MemoryArchive.memory_id)
            )).scalars().all()
            records = [_archive_to_record(r) for r in rows]
        else:
            rows = (await db.execute(
                select(Memory).where(
                    Memory.user_id == user_id,
                    Memory.character_id == character_id,
                    Memory.is_archived == False,  # noqa: E712
                    Memory.memory_type != "working_state",
                    Memory.status.in_(RETRIEVABLE_STATUSES),
                ).order_by(Memory.id)
            )).scalars().all()
            records = [_memory_to_record(m) for m in rows]
    return records


async def export_pack(
    user_id: int,
    character_id: int,
    out_path: str,
    *,
    scope: str = "all",
    page_budget: int = PAGE_BUDGET,
    include_vectors: bool = False,
) -> dict:
    """导出 .mempak；返回报告 dict。默认不含向量（跨嵌入模型可移植）。"""
    if scope not in ("all", "archived"):
        raise ValueError(f"scope 仅支持 all/archived，实为 {scope!r}")
    records = await query_records_for_export(user_id, character_id, scope)
    # 脱敏
    cleaned: list[dict] = []
    redactions = 0
    for rec in records:
        r, n = _redact_record(rec)
        redactions += n
        cleaned.append(r)
    pages, oversized = chunk_records(cleaned, page_budget)
    # 本实现不携带向量快照（--with-vectors 仅作意图标记；跨嵌入模型/维度可移植），
    # 目标端可用 --reembed 由本地 bge 重嵌，故 manifest.vectors_included 恒为 False。
    manifest = build_manifest(
        user_id=user_id,
        character_id=character_id,
        scope=scope,
        count=len(cleaned),
        page_count=len(pages),
        redactions=redactions,
        oversized=oversized,
        vectors_included=False,
    )
    out_abs = os.path.abspath(os.path.expanduser(out_path))
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    with zipfile.ZipFile(out_abs, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for i, page in enumerate(pages):
            z.writestr(f"pages/{i:04d}.md", page)
        z.writestr("README.txt", _readme_text(manifest))
    return {
        "status": "ok",
        "out": out_abs,
        "scope": scope,
        "user_id": user_id,
        "character_id": character_id,
        "count": len(cleaned),
        "page_count": len(pages),
        "redactions": redactions,
        "oversized_pages": oversized,
        "vectors_included": False,
        "vector_snapshot_carried": False,
        "embed_model": manifest["embed_model"],
        "embed_dim": manifest["embed_dim"],
    }


def _meta_to_incoming(meta: dict, content: str, user_id: int, character_id: int, memory_id: int):
    """frontmatter meta + 正文 → Memory ORM（用于导入）。"""
    from app.models.memory import Memory

    return Memory(
        id=int(memory_id),
        user_id=user_id,
        character_id=character_id,
        memory_type=meta.get("memory_type") or "event",
        sub_type=meta.get("sub_type"),
        title=meta.get("title"),
        content=content,
        importance=meta.get("importance"),
        strength_days=_f(meta.get("strength")),
        epistemic_status=meta.get("epistemic"),
        reliability_score=_f(meta.get("reliability")),
        chain_id=meta.get("chain_id"),
        parent_id=_int_or_none(meta.get("parent_id")),
        speaker_type=meta.get("speaker"),
        speaker_id=_int_or_none(meta.get("speaker_id")),
        source=meta.get("source"),
        status=meta.get("status") or "active",
        version=int(meta.get("version") or 0),
        is_core=bool(meta.get("is_core")),
        is_pinned=bool(meta.get("is_pinned")),
        why_it_matters=meta.get("why_it_matters"),
        valid_from=_parse_dt(meta.get("valid_from")),
        valid_to=_parse_dt(meta.get("valid_to")),
        created_at=_parse_dt(meta.get("created_at")),
    )


def _int_or_none(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _existing_summary(m) -> dict:
    return {
        "id": m.id,
        "status": m.status,
        "version": int(m.version or 0),
        "user_id": m.user_id,
        "character_id": m.character_id,
    }


def _incoming_summary(incoming) -> dict:
    return {
        "id": incoming.id,
        "status": incoming.status,
        "version": int(incoming.version or 0),
        "user_id": incoming.user_id,
        "character_id": incoming.character_id,
    }


def _apply_incoming(existing, incoming) -> None:
    """forward merge：字段回填（保留原 id；作用域不串，仍在同 user/char）。"""
    existing.memory_type = incoming.memory_type
    existing.sub_type = incoming.sub_type
    existing.title = incoming.title
    existing.content = incoming.content
    existing.importance = incoming.importance
    existing.strength_days = incoming.strength_days
    existing.epistemic_status = incoming.epistemic_status
    existing.reliability_score = incoming.reliability_score
    existing.chain_id = incoming.chain_id
    existing.parent_id = incoming.parent_id
    existing.speaker_type = incoming.speaker_type
    existing.speaker_id = incoming.speaker_id
    existing.source = incoming.source
    existing.status = incoming.status
    existing.version = incoming.version
    existing.is_core = incoming.is_core
    existing.is_pinned = incoming.is_pinned
    existing.why_it_matters = incoming.why_it_matters
    existing.valid_from = incoming.valid_from
    existing.valid_to = incoming.valid_to


def _incoming_to_record(incoming) -> dict:
    """Memory ORM → 宽记录（用于冷归档 payload 还原）。"""
    return {
        "memory_id": incoming.id,
        "memory_type": incoming.memory_type,
        "sub_type": incoming.sub_type,
        "title": incoming.title,
        "created_at": _iso(incoming.created_at),
        "importance": _f(incoming.importance),
        "strength": _f(incoming.strength_days),
        "epistemic": incoming.epistemic_status,
        "reliability": _f(incoming.reliability_score),
        "chain_id": incoming.chain_id,
        "parent_id": incoming.parent_id,
        "speaker": incoming.speaker_type,
        "speaker_id": incoming.speaker_id,
        "source": incoming.source,
        "status": incoming.status,
        "version": int(incoming.version or 0),
        "is_core": bool(incoming.is_core),
        "is_pinned": bool(incoming.is_pinned),
        "why_it_matters": incoming.why_it_matters,
        "valid_from": _iso(incoming.valid_from),
        "valid_to": _iso(incoming.valid_to),
        "content": incoming.content,
    }


async def _import_archive_row(db, incoming) -> str:
    """冷归档导入：写入 memory_archive（只进不出的历史快照），不触碰热表 → 不复活 active。

    同 memory_id 已存在归档行 → conflict（避免重复）；否则 insert 并计入 archived。
    """
    from sqlalchemy import select

    from app.models.memory import MemoryArchive

    existing = (await db.execute(
        select(MemoryArchive).where(MemoryArchive.memory_id == incoming.id)
    )).scalars().first()
    if existing is not None:
        return "conflict"
    payload = json.dumps(_incoming_to_record(incoming), ensure_ascii=False, default=str)
    db.add(MemoryArchive(
        memory_id=incoming.id,
        user_id=incoming.user_id,
        character_id=incoming.character_id,
        payload=payload,
        archived_reason="mempak_import",
    ))
    return "archived"


async def _reembed_imported(memory_ids: list[int], character_id: int) -> int:
    """目标端本地 bge 重嵌导入的记忆（可选，默认为关）。

    - 模型缺失 / 向量库不可用 / 单条失败 → 静默跳过（warn），不阻塞导入；
    - 只在显式 --reembed / --with-vectors 时调用。
    """
    from app.utils.logger import get_logger

    logger = get_logger("memory.portable_pack")
    from app.db.database import async_session_factory
    from app.memory.embedding import text_embedding
    from app.db.vector_store import upsert_memory_vector
    from app.models.memory import Memory

    n = 0
    for mid in memory_ids:
        try:
            async with async_session_factory() as db:
                m = await db.get(Memory, mid)
                if m is None:
                    continue
                content, mt, imp = m.content, m.memory_type, float(m.importance or 0)
            emb = await text_embedding(content)
            await upsert_memory_vector(mid, character_id, mt, content, emb, importance=imp)
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("reembed memory=%s skipped: %s", mid, e)
    return n


async def import_pack(
    pack_path: str,
    *,
    target_user: int | None = None,
    target_char: int | None = None,
    with_vectors: bool = False,
    reembed: bool = False,
) -> dict:
    """导入 .mempak；返回报告 dict。默认不重嵌（见模块说明），--reembed 显式触发。"""
    manifest, pages = read_pack(pack_path)
    scope = manifest.get("scope") if manifest.get("scope") in ("all", "archived") else "all"
    if target_user is None:
        target_user = manifest.get("user_id")
    if target_char is None:
        target_char = manifest.get("character_id")
    if target_user is None or target_char is None:
        raise PackError("导入需确定目标作用域（manifest 缺失或 --user/--char 未给）")
    target_user, target_char = int(target_user), int(target_char)

    report: dict = {
        "status": "ok",
        "scope": scope,
        "manifest_scope": manifest.get("scope"),
        "target": {"user_id": target_user, "character_id": target_char},
        "total": len(pages),
        "insert": 0,
        "update": 0,
        "conflict": 0,
        "skipped": 0,
        "archived": 0,
        "reembedded": 0,
    }

    from app.db.database import async_session_factory
    from app.models.memory import Memory

    reembed_ids: list[int] = []
    async with async_session_factory() as db:
        for pg in pages:
            meta = pg["meta"]
            mid = meta.get("memory_id")
            if mid is None:
                report["skipped"] += 1
                continue
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                report["skipped"] += 1
                continue
            incoming = _meta_to_incoming(meta, pg["content"], target_user, target_char, mid)
            if scope == "archived":
                act = await _import_archive_row(db, incoming)
                report[act] = report.get(act, 0) + 1
                continue
            existing = await db.get(Memory, mid)
            ex = _existing_summary(existing) if existing is not None else None
            action, _reason = decide_import_action(ex, _incoming_summary(incoming))
            if action == "conflict":
                report["conflict"] += 1
                continue
            if action == "insert":
                db.add(incoming)
                report["insert"] += 1
                reembed_ids.append(mid)
            else:  # update
                _apply_incoming(existing, incoming)
                report["update"] += 1
                reembed_ids.append(mid)
        await db.commit()
    report["upsert"] = report["insert"] + report["update"]
    if (with_vectors or reembed) and scope != "archived":
        report["reembedded"] = await _reembed_imported(reembed_ids, target_char)
    return report


# ═══════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════

def _cmd_export(args) -> int:
    report = asyncio_run(export_pack(
        args.user, args.char, args.out,
        scope=args.scope,
        include_vectors=args.with_vectors,
    ))
    print(f"导出完成 → {report['out']}")
    print(f"  作用域={report['scope']} user={report['user_id']} char={report['character_id']}")
    print(f"  记忆 {report['count']} 条 | 页面 {report['page_count']} | 脱敏 {report['redactions']} | "
          f"超预算页 {report['oversized_pages']}")
    print(f"  嵌入模型={report['embed_model']} (dim={report['embed_dim']}) | 含向量={report['vectors_included']}")
    if args.with_vectors:
        print("  ⚠️ 本实现不携带向量快照（index.sqlite 未写入），跨模型可移植；目标端请用 --reembed 由本地 bge 重嵌。")
    if report["redactions"]:
        print("  ⚠️ 已对命中密钥/敏感模式的可见文本做 [REDACTED] 替换，请核对内容完整性。")
    return 0


def _cmd_import(args) -> int:
    report = asyncio_run(import_pack(
        args.file,
        target_user=args.user,
        target_char=args.char,
        with_vectors=args.with_vectors,
        reembed=args.reembed,
    ))
    print(f"导入完成：{args.file}")
    print(f"  包作用域={report['scope']}（manifest={report['manifest_scope']}）目标 user={report['target']['user_id']} "
          f"char={report['target']['character_id']}")
    print(f"  总计 {report['total']} | 新增 {report['insert']} | 更新 {report['update']} | "
          f"冲突 {report['conflict']} | 跳过 {report['skipped']} | 冷归档 {report['archived']}")
    print(f"  重嵌入 {report['reembedded']} 条（--reembed 才执行；默认为关）")
    return 0


def _cmd_validate(args) -> int:
    ok, issues, manifest = validate_pack(args.file)
    if manifest is not None:
        print(f"包信息：{manifest.get('format')} v{manifest.get('version')} scope={manifest.get('scope')} "
              f"user={manifest.get('user_id')} char={manifest.get('character_id')} count={manifest.get('count')} "
              f"embed={manifest.get('embed_model')}@{manifest.get('embed_dim')}")
    if ok:
        print("校验通过：包结构 / manifest / 分页 / frontmatter 均合法。")
        return 0
    print("校验未通过：")
    for iss in issues:
        print(f"  - {iss}")
    return 1


def _make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="portable_pack", description="AMBRACE .mempak 可移植记忆包（离线）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="导出 .mempak")
    p_exp.add_argument("--user", required=True, type=int, help="用户 id")
    p_exp.add_argument("--char", required=True, type=int, help="角色 id")
    p_exp.add_argument("--out", required=True, help="输出 .mempak 路径")
    p_exp.add_argument("--scope", choices=["all", "archived"], default="all",
                       help="all=活记忆（可检索集）；archived=#70 冷归档")
    p_exp.add_argument("--with-vectors", action="store_true",
                       help="意图标记：默认不带向量快照（本实现不写 index.sqlite，跨模型可移植）；导入请用 --reembed")
    p_exp.set_defaults(func=_cmd_export)

    p_imp = sub.add_parser("import", help="导入 .mempak")
    p_imp.add_argument("--file", required=True, help="待导入 .mempak 路径")
    p_imp.add_argument("--user", type=int, default=None, help="目标 user（缺省用 manifest 值）")
    p_imp.add_argument("--char", type=int, default=None, help="目标 char（缺省用 manifest 值）")
    p_imp.add_argument("--with-vectors", action="store_true", help="携带向量快照（可选）")
    p_imp.add_argument("--reembed", action="store_true", help="导入后用本地 bge 重嵌（默认关）")
    p_imp.set_defaults(func=_cmd_import)

    p_val = sub.add_parser("validate", help="校验 .mempak")
    p_val.add_argument("--file", required=True, help="待校验 .mempak 路径")
    p_val.set_defaults(func=_cmd_validate)
    return ap


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def main(argv: list[str] | None = None) -> int:
    # 仅 CLI 入口重构 stdout 编码（import 本模块不改动 sys.stdout，避免干扰 pytest 捕获）
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = _make_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
