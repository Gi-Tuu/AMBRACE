# -*- coding: utf-8 -*-
"""`[GEN_IMAGE]` 生图 prompt 存量清洗脚本（小批次任务 2，2026-09-16）。

背景（已核实）：批次四（P1-4）保证**新写入**不再把生图 prompt 落进可见 `content`，
但历史消息里仍存着整段生图 prompt，前端会原样显示，例如：
  - `[GEN_IMAGE] 傍晚的厨房，灶上一只砂锅咕嘟炖着红烧肉……生活感插画风格`
  - `[IMG_TEXT]锅里给你留着。`
本脚本清理这些存量 `chat_messages.content`。

清洗规则：
  1. 扫描 `content` 含 `[GEN_IMAGE]` / `【GEN_IMAGE】`（全/半角、大小写、标记内空白漂移、
     漏写闭合标签）的行；另可用 `--include-img-text-only` 追加清理「只含 [IMG_TEXT] 标记」的行。
  2. 去掉生图 prompt 整段（含标记与残片）；若同一 content 含 `[IMG_TEXT]`，保留其后的
     用户可见文案（只剥标记、保留文案，顺序不变）。
  3. 清洗后为空 → 先看 `extra_meta` 里已有的**文案**（img_text/caption/…）回退；若没有
     （或该文案其实就是 prompt）→ 用中性短句「（发了一张图）」兜底。
     **prompt / 画面描述绝不作为回退内容上屏**（与 P1-4「prompt 只进 meta」同口径）。
  4. `extra_meta` 内 Prompt/原始描述**保持不动**（追溯用，只改 content）。
  5. 默认 dry-run（只读，打印命中条数与改动前后对比）；`--apply` 时先自动备份（JSON，落
     `backend/data/sqlite/backups/`）再写；幂等、可重复执行。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_gen_image_content.py                 # dry-run
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_gen_image_content.py --include-img-text-only
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_gen_image_content.py --apply
  backend\\.venv\\Scripts\\python.exe scripts\\cleanup_gen_image_content.py --db <path>
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# ── 与 app.agent.actions（P1-4，2026-09-16）同口径的标记正则 ──────────────────
# 全/半角方括号、标记内空白、大小写漂移；闭合标签可选（漏写时按边界截断，绝不吞后续正文）。
_GEN_IMAGE_RE = re.compile(
    r"[\[【]\s*GEN_IMAGE\s*[\]】](?:(.*?)[\[【]\s*/\s*GEN_IMAGE\s*[\]】]|(.*?)(?=[\[【]|\r?\n[ \t]*\r?\n|\Z))",
    re.S | re.IGNORECASE,
)
_IMG_TEXT_RE = re.compile(
    r"[\[【]\s*IMG_TEXT\s*[\]】](?:(.*?)[\[【]\s*/\s*IMG_TEXT\s*[\]】]|(.*?)(?=[\[【]|\r?\n|\Z))",
    re.S | re.IGNORECASE,
)
# 孤立闭合标签 / 裸开标签（任何情况下都不该出现在展示文本里）
_IMG_ORPHAN_CLOSE_RE = re.compile(r"[\[【]\s*/\s*(?:GEN_IMAGE|IMG_TEXT)\s*[\]】]", re.IGNORECASE)
_IMG_OPEN_ONLY_RE = re.compile(r"[\[【]\s*/?\s*(?:GEN_IMAGE|IMG_TEXT)\s*[\]】]", re.IGNORECASE)
# 残留 GEN_IMAGE 标记 + 其后同行内容（与 actions.strip_image_residue 同口径的兜底）
_GEN_MARKER_RESIDUE_RE = re.compile(r"[\[【]\s*/?\s*GEN_IMAGE\s*[\]】][^\n]*", re.IGNORECASE)
# IMG_TEXT 标记（只剥标记本身，保留其后的用户可见文案）
_IMG_TEXT_MARKER_RE = re.compile(r"[\[【]\s*/?\s*IMG_TEXT\s*[\]】][ \t]*", re.IGNORECASE)
# 存在性判定
_GEN_MARKER_ANY_RE = re.compile(r"[\[【]\s*/?\s*GEN_IMAGE\s*[\]】]", re.IGNORECASE)
_IMG_MARKER_ANY_RE = re.compile(r"[\[【]\s*/?\s*IMG_TEXT\s*[\]】]", re.IGNORECASE)

# 中性兜底短句（清洗后无任何可见文案时使用；不含 prompt、不含内部标记）
NEUTRAL_FALLBACK = "（发了一张图）"
# 配文长度上限（与 actions._CAPTION_MAX_LEN / 落库截断同口径：超过一律判为「不是配文」）
_CAPTION_MAX_LEN = 60
# extra_meta 里可能承载「用户可见文案」的键（**不含 prompt/描述**——prompt 绝不回退上屏）
_CAPTION_META_KEYS = ("img_text", "image_text", "caption", "image_caption", "content", "text")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "backend" / "data" / "sqlite" / "ai_companion.db"
DEFAULT_BACKUP_DIR = Path(__file__).resolve().parent.parent / "backend" / "data" / "sqlite" / "backups"


def _marker_body(m: "re.Match[str]") -> str:
    """取「闭合/无闭合」双分支标记的正文（groups 里第一个非 None 分支）。"""
    for g in m.groups():
        if g is not None:
            return g
    return ""


def _norm_for_cmp(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def has_gen_image_marker(content: str) -> bool:
    """content 是否含 GEN_IMAGE 标记（本任务主扫范围）。"""
    return bool(content) and bool(_GEN_MARKER_ANY_RE.search(content))


def has_img_text_marker(content: str) -> bool:
    """content 是否含 IMG_TEXT 标记（只剥标记、保留文案）。"""
    return bool(content) and bool(_IMG_MARKER_ANY_RE.search(content))


def extract_prompt(content: str) -> str | None:
    """取 content 里的生图画面描述（仅用于「裸 prompt」判定与统计，不回流 content）。"""
    m = _GEN_IMAGE_RE.search(content or "")
    if not m:
        return None
    body = _IMG_ORPHAN_CLOSE_RE.sub("", _marker_body(m))
    body = _IMG_OPEN_ONLY_RE.sub("", body).strip()
    return body or None


def extract_caption(content: str) -> str | None:
    """取 content 里 `[IMG_TEXT]` 携带的用户可见文案（无则 None）。"""
    m = _IMG_TEXT_RE.search(content or "")
    if not m:
        return None
    return _marker_body(m).strip() or None


def is_bare_image_prompt(visible: str, prompt: str | None) -> bool:
    """可见文本是否「就是生图 prompt 本身」（归一化全等 / ≥8 字互为子串 / ≥70% 字符来自 prompt）。

    与 app.agent.actions.is_bare_image_prompt 同口径；prompt 缺失 → False（不误伤）。
    """
    a = _norm_for_cmp(visible)
    b = _norm_for_cmp(prompt)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 8 and (a in b or b in a):
        return True
    try:
        from difflib import SequenceMatcher
        matched = sum(bl.size for bl in SequenceMatcher(None, a, b).get_matching_blocks())
        return matched / len(a) > 0.7
    except Exception:  # noqa: BLE001 - 判定失败不误伤，按非裸 prompt 处理
        return False


def _tidy(text: str) -> str:
    """收敛剥标记后的空白：行尾空格 / 3+ 连续空行 / 首尾空白。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.rstrip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_meta(extra_meta) -> dict:
    if isinstance(extra_meta, dict):
        return extra_meta
    if not extra_meta:
        return {}
    try:
        meta = json.loads(extra_meta)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _meta_caption(extra_meta, prompt: str | None) -> str | None:
    """从 extra_meta 里找可回退的**用户可见文案**；找不到或就是 prompt → None。"""
    meta = _parse_meta(extra_meta)
    for key in _CAPTION_META_KEYS:
        val = meta.get(key)
        if not isinstance(val, str):
            continue
        t = val.strip()
        if not t or len(t) > _CAPTION_MAX_LEN:
            continue
        if is_bare_image_prompt(t, prompt):
            continue
        return t
    return None


def clean_content(content: str, extra_meta=None) -> dict:
    """清洗单条 content，返回决策信息（纯函数，不写库）。

    返回 dict：
      - new_content: 清洗后的可见文案
      - changed:     是否需要写库（new_content 与原 content 不同）
      - source:      img_text | extra_meta | neutral | kept
      - had_prompt:  原 content 是否含 GEN_IMAGE prompt
      - from_img_text: 是否从 [IMG_TEXT] 保留了用户可见文案
    """
    content = content or ""
    prompt = extract_prompt(content)
    caption = extract_caption(content)

    # ① 去掉 GEN_IMAGE 整段（prompt + 标记 + 残片）
    out = _GEN_IMAGE_RE.sub("", content)
    out = _IMG_ORPHAN_CLOSE_RE.sub("", out)
    out = _GEN_MARKER_RESIDUE_RE.sub("", out)
    # ② 只剥 IMG_TEXT 标记，保留其后的用户可见文案
    out = _IMG_TEXT_MARKER_RE.sub("", out)
    out = _tidy(out)
    # ③ 兜底：剥完只剩 prompt 本身 → 视为无可见正文（P1-4 同口径）
    if out and is_bare_image_prompt(out, prompt):
        out = ""

    source = "kept"
    from_img_text = False
    if not out:
        fb = _meta_caption(extra_meta, prompt)
        if fb:
            out, source = fb, "extra_meta"
        else:
            out, source = NEUTRAL_FALLBACK, "neutral"
    elif caption and not is_bare_image_prompt(caption, prompt) \
            and _norm_for_cmp(caption) in _norm_for_cmp(out):
        source, from_img_text = "img_text", True

    return {
        "new_content": out,
        "changed": out != content,
        "source": source,
        "had_prompt": prompt is not None,
        "from_img_text": from_img_text,
    }


def plan_cleanup(rows: list[dict], *, include_img_text_only: bool = False) -> list[dict]:
    """纯函数：给定 chat_messages 行（含 id/content/extra_meta），返回拟改动决策列表。

    主扫 `[GEN_IMAGE]` 行；`include_img_text_only=True` 时追加「只含 [IMG_TEXT] 标记」的行。
    """
    decisions: list[dict] = []
    for r in rows:
        content = r.get("content") or ""
        gen = has_gen_image_marker(content)
        if not gen and not (include_img_text_only and has_img_text_marker(content)):
            continue
        result = clean_content(content, r.get("extra_meta"))
        if not result["changed"]:
            continue
        decisions.append({
            "id": r.get("id"),
            "session_id": r.get("session_id"),
            "old_content": content,
            "new_content": result["new_content"],
            "extra_meta": r.get("extra_meta"),
            "source": result["source"],
            "had_prompt": result["had_prompt"],
            "from_img_text": result["from_img_text"],
            "scope": "gen_image" if gen else "img_text_only",
        })
    return decisions


def load_rows(con: sqlite3.Connection) -> list[dict]:
    """读取候选行（GEN_IMAGE / IMG_TEXT 粗筛，判定留给 plan_cleanup）。"""
    cur = con.cursor()
    cur.execute(
        "SELECT id, session_id, sender_type, content, extra_meta, created_at "
        "FROM chat_messages "
        "WHERE content LIKE '%GEN_IMAGE%' OR content LIKE '%IMG_TEXT%' "
        "ORDER BY id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def apply_decisions(con: sqlite3.Connection, decisions: list[dict]) -> int:
    """只改 content（extra_meta 保持不动）；返回写库条数。"""
    cur = con.cursor()
    n = 0
    for d in decisions:
        cur.execute("UPDATE chat_messages SET content=? WHERE id=?", (d["new_content"], d["id"]))
        n += 1
    con.commit()
    return n


def backup_rows(con: sqlite3.Connection, decisions: list[dict], path: Path) -> None:
    """写前备份：把将改动的行（原 content + extra_meta）落 JSON，供回滚/审计。"""
    ids = [d["id"] for d in decisions]
    if not ids:
        path.write_text("[]", encoding="utf-8")
        print(f"[backup] 无改动行，写入空备份 → {path}")
        return
    cur = con.cursor()
    placeholders = ",".join("?" for _ in ids)
    cur.execute(
        f"SELECT id, session_id, sender_type, content, extra_meta, created_at "
        f"FROM chat_messages WHERE id IN ({placeholders}) ORDER BY id",
        ids,
    )
    cols = [d[0] for d in cur.description]
    data = [dict(zip(cols, row)) for row in cur.fetchall()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[backup] 已备份 {len(data)} 行 → {path}")


def _preview(text: str, limit: int = 90) -> str:
    one = (text or "").replace("\n", "\\n")
    return one if len(one) <= limit else one[:limit] + "…"


def _connect(db_path: Path, *, apply: bool) -> sqlite3.Connection:
    mode = "rwc" if apply else "ro"
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode={mode}", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    return con


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="[GEN_IMAGE] 生图 prompt 存量清洗（默认 dry-run，--apply 前自动备份）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"sqlite 路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run 只读；写前自动备份）")
    ap.add_argument("--include-img-text-only", action="store_true",
                    help="追加清理「只含 [IMG_TEXT] 标记、无 GEN_IMAGE」的行（默认不处理）")
    ap.add_argument("--backup-dir", default=str(DEFAULT_BACKUP_DIR),
                    help=f"备份目录（默认 {DEFAULT_BACKUP_DIR}）")
    ap.add_argument("--sample", type=int, default=20, help="dry-run 打印样本条数（默认 20）")
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / db_path
    if not db_path.exists():
        print(f"[error] 库不存在：{db_path}")
        return 2

    con = _connect(db_path, apply=args.apply)
    try:
        rows = load_rows(con)
        decisions = plan_cleanup(rows, include_img_text_only=args.include_img_text_only)
        gen_decisions = [d for d in decisions if d["scope"] == "gen_image"]
        img_only_decisions = [d for d in decisions if d["scope"] == "img_text_only"]

        by_source: dict[str, int] = {}
        for d in gen_decisions:
            by_source[d["source"]] = by_source.get(d["source"], 0) + 1

        print("=== [GEN_IMAGE] 存量清洗 ===")
        print(f"DB: {db_path}  mode={'APPLY' if args.apply else 'dry-run(只读)'}")
        print(f"候选行（LIKE GEN_IMAGE/IMG_TEXT）: {len(rows)}")
        print(f"[命中] content 含 GEN_IMAGE/【GEN_IMAGE】需清洗: {len(gen_decisions)} 条")
        print(f"  其中可从 [IMG_TEXT] 保留用户可见文案: "
              f"{sum(1 for d in gen_decisions if d['from_img_text'])} 条")
        print(f"  需要中性回退（{NEUTRAL_FALLBACK}）: "
              f"{sum(1 for d in gen_decisions if d['source'] == 'neutral')} 条")
        print(f"  其中从 extra_meta 文案回退: "
              f"{sum(1 for d in gen_decisions if d['source'] == 'extra_meta')} 条")
        print(f"  回退来源分布: {by_source}")
        if args.include_img_text_only:
            print(f"[附加] 仅含 IMG_TEXT 标记（无 GEN_IMAGE）需剥标记: {len(img_only_decisions)} 条")
        else:
            _img_only_total = sum(
                1 for r in rows
                if has_img_text_marker(r.get("content") or "")
                and not has_gen_image_marker(r.get("content") or "")
            )
            if _img_only_total:
                print(f"[提示] 另有 {_img_only_total} 条仅含 IMG_TEXT 标记（未处理；加 "
                      f"--include-img-text-only 可一并剥标记、保留文案）")
        print(f"合计拟改动: {len(decisions)} 条")

        def _dump(items: list[dict]) -> None:
            for d in items[: max(0, args.sample)]:
                print(f"  - #{d['id']} ({d['scope']}/{d['source']})")
                print(f"      before: {_preview(d['old_content'])}")
                print(f"      after : {_preview(d['new_content'])}")
            if len(items) > args.sample:
                print(f"  …（其余 {len(items) - args.sample} 条略）")

        if gen_decisions:
            print("\n[GEN_IMAGE 改动样本]")
            _dump(gen_decisions)
        if img_only_decisions:
            print("\n[IMG_TEXT-only 改动样本]")
            _dump(img_only_decisions)

        if not decisions:
            print("[ok] 无需要清洗的行（幂等：可重复执行）。")
            return 0
        if not args.apply:
            print("\n[dry-run] 未写库。确认后加 --apply（写前自动备份）。")
            return 0

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = Path(args.backup_dir) / f"gen_image_content_backup_{ts}.json"
        backup_rows(con, decisions, backup_path)
        n = apply_decisions(con, decisions)
        print(f"[apply] 已更新 content {n} 条（extra_meta 未动）。")
        print("[verify] 建议重跑一次 dry-run 确认命中为 0（幂等）。")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
