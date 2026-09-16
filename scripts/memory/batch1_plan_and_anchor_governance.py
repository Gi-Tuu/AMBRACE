# -*- coding: utf-8 -*-
"""批次一（时效与现状止血）存量治理脚本（2026-09-16）—— 任务3 通例 + 任务5 残留/锚点。

交接：批次一（时效与现状止血）；交接文档见项目 output/ 目录（不写本机绝对路径）。
本脚本只做**状态标记与锚点补种**，不物理删除任何行（与「回忆不等于删除」口径一致）：

  [plans]     任务3 通例：把「已过期、仍 active」的计划类记忆置 stale + 补 valid_to
              （扫描窗/判定与在线日终维护 ``app.memory.maintain_plan_expiry`` **完全同口径**：
               先只读 ``list_expired_plans()`` 看清单，--apply 时再循环 ``expire_stale_plans()``，
               单次限额 200，跑到空；向量侧沿用既有 ``supersede._mark_vectors`` 通道）。
  [residuals] 任务5 定向：8 月长沙出差残留（默认 7017/7522/7573/7663/7721）按「已过期计划」处理
              → status='stale' + 补 valid_to + next_review_at=NULL（不删）。
  [anchor]    任务5 建立「当前现状锚点」（高权重、长有效期，走既有注入通道）：
              a) world_facts：curated ``kind='fact'``（is_authoritative=1、无 TTL、无 stale_after）
                 → section_curated【关于用户与世界的稳定事实】与 world_facts 槽；links_json 触发键
                 让「现状/在哪/位置/学校/宿舍」这类问法把它顶到该类最前（复用 get_curated_facts
                 既有 ``_trigger_hit``，不新增机制）；
              b) user_facts：``slot='location'`` 单值槽（旧值进 previous_value）
                 → [USER NOW]（section_user_now）与 current_state_anchor（section_current_state）即刻生效，
                 且该层是**跨角色共享**的用户级事实，三个角色同时受益。

安全设计（与 scripts/cleanup_prospective_intents.py、scripts/dedupe_world_facts.py 同约定）：
  - 默认 dry-run：只读，只打印变更计划与 before/after 计数，不写任何东西；
  - ``--apply`` 才写库，且写前自动整库备份到 ``backend/data/sqlite/backups/``
    （``ai_companion.db.pre_batch1_<UTC时间戳>``）；备份失败即中止，不做任何写入；
  - 幂等可重复执行：已 stale 的不再入窗；锚点同值跳过（assert_curated 同 object_value 更新不新增）。
  - 建议先停服务器再 --apply（避免与在线进程并发写 SQLite / Chroma）。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\batch1_plan_and_anchor_governance.py
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\batch1_plan_and_anchor_governance.py --apply
  backend\\.venv\\Scripts\\python.exe scripts\\memory\\batch1_plan_and_anchor_governance.py --only plans
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"
sys.path.insert(0, str(_BACKEND))

# ── 任务5 常量（占位默认值：请用 --fact/--user-fact 传入你自己的现状口径）──
ANCHOR_FACT = "用户当前常驻：<城市>·<学校/校区>·<住所>；身份：<身份>"
ANCHOR_USER_FACT = "常驻<城市>·<学校/校区>·<住所>（<身份>）"
ANCHOR_LINKS = ("现状", "在哪", "位置", "学校", "宿舍", "校区", "常驻", "住哪")
ANCHOR_CHAR_FALLBACK = 13          # 取不到活跃角色时兜底（首个活跃角色 id）
DEFAULT_USER_ID = 3                # 体检报告数据所属用户
DEFAULT_RESIDUAL_IDS = (7017, 7522, 7573, 7663, 7721)
PLAN_BATCH = 200                   # 单次限额（与 EXPIRE_BATCH_LIMIT 对齐）


# ────────────────────────── 可单测的纯函数 ──────────────────────────

def residual_valid_to(created_at: datetime | None, fallback_days: int = 7) -> datetime:
    """残留行程记忆的 valid_to：创建时间 + 默认水平窗（解析不到明确行程日期时的保守兜底）。"""
    base = created_at or datetime.now(timezone.utc).replace(tzinfo=None)
    return base + timedelta(days=fallback_days)


def anchor_payload() -> dict:
    """当前现状锚点的写入载荷（纯函数，供 world_facts / user_facts 两通道共用；便于单测）。"""
    return {
        "kind": "fact",
        "predicate": "curated",
        "object_value": ANCHOR_FACT,
        "user_fact_slot": "location",
        "user_fact_value": ANCHOR_USER_FACT,
        "links": list(ANCHOR_LINKS),
        "is_authoritative": True,
    }


def backup_path(backups_dir: str | os.PathLike, now: datetime | None = None) -> str:
    """备份文件名（UTC 时间戳；默认目录 backend/data/sqlite/backups/）。"""
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return os.path.join(str(backups_dir), f"ai_companion.db.pre_batch1_{ts}")


def db_file_path(database_url: str) -> str:
    """sqlite+aiosqlite:///<abs> → 文件路径（备份用）。非 sqlite 返回空串。"""
    prefix = "sqlite+aiosqlite:///"
    if not (database_url or "").startswith(prefix):
        return ""
    return database_url[len(prefix):]


# ────────────────────────── 三段治理 ──────────────────────────

async def _run_plans(*, apply: bool, limit: int = PLAN_BATCH) -> dict:
    """任务3：过期计划记忆存量治理（只读清单 / 循环置 stale）。"""
    from app.memory.maintain_plan_expiry import expire_stale_plans, list_expired_plans

    before = await list_expired_plans(limit=100000)
    print("[plans] 判定口径：memory_type='event'|sub_type='plan'|user_info/extracted 且 PLAN_MARKERS LIKE"
          " → classify_tense=plan 且 is_plan_expired")
    print(f"[plans] before：仍 active 的已过期计划类记忆 = {len(before)} 条")
    for r in before[:10]:
        print(f"    id={r['id']:<6} char={r['character_id']:<3} {r['memory_type']}/{r['sub_type']} "
              f"valid_to={str(r['valid_to'])[:19]}  {r['content'][:40]}")
    if len(before) > 10:
        print(f"    …… 其余 {len(before) - 10} 条（完整清单见 --apply 输出）")

    moved = 0
    if apply:
        while True:
            n = await expire_stale_plans(limit=limit)
            moved += n
            if n < limit:
                break
    after = await list_expired_plans(limit=100000)
    print(f"[plans] {'after' if apply else '（dry-run 预估 after）'}：仍 active 的已过期计划类记忆 = "
          f"{len(after)} 条；本次置 stale = {moved if apply else '（dry-run 未写）'}")
    return {"before": len(before), "after": len(after), "moved": moved}


async def _load_residuals(ids: tuple[int, ...]) -> list[dict]:
    """读残留行程记忆（active 才入列）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.models.memory import Memory

    async with async_session_factory() as db:
        rows = (await db.execute(
            select(Memory).where(Memory.id.in_(list(ids)))
        )).scalars().all()
        return [
            {"id": m.id, "character_id": m.character_id, "status": m.status,
             "created_at": m.created_at, "valid_to": m.valid_to,
             "content": (m.content or "")[:50]}
            for m in rows
        ]


async def _run_residuals(*, apply: bool, ids: tuple[int, ...], valid_days: int = 7) -> dict:
    """任务5：长沙出差残留按「已过期计划」处置（stale + valid_to，不删）。"""
    from app.db.database import async_session_factory
    from app.models.memory import Memory

    rows = await _load_residuals(ids)
    active = [r for r in rows if r["status"] == "active"]
    print(f"[residuals] 目标 id={list(ids)}；命中 {len(rows)} 条，其中 active {len(active)} 条 → stale")
    for r in rows:
        print(f"    id={r['id']:<6} char={r['character_id']:<3} status={r['status']:<9}  {r['content'][:40]}")
    moved_ids: list[int] = []
    char_ids: set[int] = set()
    if apply and active:
        async with async_session_factory() as db:
            for rid in [r["id"] for r in active]:
                m = await db.get(Memory, rid)
                if m is None or m.status != "active":
                    continue
                m.status = "stale"
                m.valid_to = m.valid_to or residual_valid_to(m.created_at, valid_days)
                m.next_review_at = None
                db.add(m)
                moved_ids.append(m.id)
                char_ids.add(m.character_id)
            await db.commit()
        # 向量侧同步（沿用既有通道；失败静默由该函数内部处理）
        if moved_ids:
            try:
                from app.memory.supersede import _bm25_invalidate_safe, _mark_vectors
                await _mark_vectors(moved_ids, {i: "stale" for i in moved_ids})
                for cid in char_ids:
                    await _bm25_invalidate_safe(cid)
            except Exception as e:  # pragma: no cover - 静默降级
                print(f"[residuals] 向量同步失败（不影响 SQLite 状态）：{e}")
    after = await _load_residuals(ids)
    print(f"[residuals] {'after' if apply else '（dry-run 预估 after）'}：active = "
          f"{sum(1 for r in after if r['status'] == 'active')} 条；本次置 stale = "
          f"{len(moved_ids) if apply else '（dry-run 未写）'}")
    return {"before": len(active),
            "after": sum(1 for r in after if r["status"] == "active"),
            "moved": len(moved_ids)}


async def _run_anchor(*, apply: bool, user_id: int, character_id: int | None) -> dict:
    """任务5：建立当前现状锚点（world_facts curated fact + user_facts location 槽）。"""
    from sqlalchemy import select

    from app.db.database import async_session_factory
    from app.events.facts import KIND_FACT
    from app.models.character import AICharacter
    from app.models.memory import WorldFact
    from app.models.user import GlobalUserFact

    payload = anchor_payload()
    async with async_session_factory() as db:
        if character_id is not None:
            char_ids = [character_id]
        else:
            char_ids = list((await db.execute(
                select(AICharacter.id).where(
                    AICharacter.user_id == user_id,
                    AICharacter.is_active == True,  # noqa: E712
                ).order_by(AICharacter.id)
            )).scalars().all())
            if not char_ids:
                char_ids = [ANCHOR_CHAR_FALLBACK]
        existing = (await db.execute(
            select(WorldFact.id, WorldFact.character_id).where(
                WorldFact.status == "active",
                WorldFact.kind == KIND_FACT,
                WorldFact.object_value == ANCHOR_FACT,
                WorldFact.character_id.in_(char_ids),
            )
        )).all()
        old_slot = (await db.execute(
            select(GlobalUserFact.value).where(
                GlobalUserFact.user_id == user_id,
                GlobalUserFact.slot == payload["user_fact_slot"],
            )
        )).scalar_one_or_none()

    have = {cid for _id, cid in existing}
    missing = [c for c in char_ids if c not in have]
    print(f"[anchor] 目标用户 user_id={user_id} characters={char_ids}")
    print(f"[anchor] world_facts：已有 active 锚点 {sorted(have) or '无'}，待补种 {missing or '无'}")
    print(f"[anchor] user_facts.slot=location：现值「{old_slot}」→ 「{payload['user_fact_value']}」")

    seeded: list[int] = []
    if apply:
        from app.events.facts import assert_curated
        async with async_session_factory() as db:
            for cid in missing:
                row = await assert_curated(
                    db, character_id=cid, user_id=user_id, kind=payload["kind"],
                    object_value=payload["object_value"], predicate=payload["predicate"],
                    audience=["public"], source="batch1_anchor", confidence=1.0,
                    links=payload["links"],
                )
                await db.commit()
                seeded.append(row.id)
        from app.memory.user_facts import upsert_user_fact
        change = await upsert_user_fact(user_id, payload["user_fact_slot"],
                                        payload["user_fact_value"], source="batch1_anchor")
        slot_note = "未变（幂等跳过）" if change is None else f"{change[0]} → {change[1]}"
    else:
        slot_note = "（dry-run 未写）"

    async with async_session_factory() as db:
        new_slot = (await db.execute(
            select(GlobalUserFact.value).where(
                GlobalUserFact.user_id == user_id,
                GlobalUserFact.slot == payload["user_fact_slot"],
            )
        )).scalar_one_or_none()
    print(f"[anchor] {'已补种 world_facts 锚点 id=' + str(seeded) if apply else '（dry-run 未写）'}；"
          f"location 槽写入：{slot_note}；当前槽值：{new_slot}")
    return {"world_facts_seeded": seeded, "location_before": old_slot, "location_after": new_slot}


# ────────────────────────── 入口 ──────────────────────────

def _do_backup(db_path: str, backups_dir: str) -> str | None:
    """写库前整库备份到 backups/；返回备份路径（失败返回 None）。"""
    if not db_path or not os.path.exists(db_path):
        print(f"[backup] 跳过：找不到库文件 {db_path!r}")
        return None
    os.makedirs(backups_dir, exist_ok=True)
    dst = backup_path(backups_dir)
    shutil.copy2(db_path, dst)
    print(f"[backup] 已备份 {db_path} → {dst}（{os.path.getsize(dst)} bytes）")
    return dst


async def _amain(args: argparse.Namespace) -> int:
    from app.config import settings

    only = {s.strip() for s in (args.only or "").split(",") if s.strip()}
    sections = [s for s in ("plans", "residuals", "anchor") if not only or s in only]
    if args.apply:
        # 与在线机制同口径：先加载当前运行时开关；本脚本属于显式治理动作，
        # 若 review_plan_expire_stale 关着则在本进程内临时启用（不改数据库、不改配置）。
        try:
            from app.application.flag_service import load_runtime_flags
            await load_runtime_flags()
        except Exception as e:  # pragma: no cover
            print(f"[warn] 运行时开关加载失败（沿用代码默认）：{e}")
        from app.agent.loop import AGENT_FLAGS
        if not AGENT_FLAGS.get("review_plan_expire_stale", False):
            AGENT_FLAGS["review_plan_expire_stale"] = True
            print("[warn] review_plan_expire_stale 未开 → 本进程内临时启用（仅影响本次脚本执行）")

    db_path = db_file_path(settings.database_url)
    print(f"=== 批次一存量治理（{'APPLY' if args.apply else 'dry-run'}）===")
    print(f"DB: {db_path or settings.database_url}")
    print(f"段: {','.join(sections)}")

    if args.apply:
        if not _do_backup(db_path, args.backups_dir):
            print("[abort] 备份失败，未做任何写入。")
            return 2

    out: dict = {}
    if "plans" in sections:
        out["plans"] = await _run_plans(apply=args.apply)
    if "residuals" in sections:
        out["residuals"] = await _run_residuals(apply=args.apply, ids=tuple(args.residual_ids))
    if "anchor" in sections:
        out["anchor"] = await _run_anchor(apply=args.apply, user_id=args.user_id,
                                          character_id=args.character_id)

    print("\n=== 汇总 ===")
    for k, v in out.items():
        print(f"  [{k}] {v}")
    if not args.apply:
        print("[dry-run] 未写库。确认影响面后加 --apply 执行（建议先停服务器；脚本会先自动备份）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="批次一存量治理：过期计划 + 长沙残留 + 现状锚点"
                                             "（默认 dry-run，--apply 才写；写前自动整库备份）")
    ap.add_argument("--apply", action="store_true", help="实际写库（缺省=只读 dry-run）")
    ap.add_argument("--only", default="plans,residuals,anchor",
                    help="只跑其中几段（逗号分隔，默认全部）")
    ap.add_argument("--user-id", type=int, default=DEFAULT_USER_ID, help=f"用户 id（默认 {DEFAULT_USER_ID}）")
    ap.add_argument("--character-id", type=int, default=None,
                    help="只给指定角色补种 world_facts 锚点（默认=该用户全部活跃角色）")
    ap.add_argument("--residual-ids", type=int, nargs="*", default=list(DEFAULT_RESIDUAL_IDS),
                    help=f"长沙残留记忆 id（默认 {' '.join(map(str, DEFAULT_RESIDUAL_IDS))}）")
    ap.add_argument("--backups-dir",
                    default=str(_BACKEND / "data" / "sqlite" / "backups"),
                    help="整库备份目录（默认 backend/data/sqlite/backups）")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
