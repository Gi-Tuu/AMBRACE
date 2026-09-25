# -*- coding: utf-8 -*-
"""curated 存量清账执行器（C14，2026-09-25）。

背景：C13/C13b 判据只作用于新写入，生产库 char13 的 active curated 存量仍有同义条目
未合并（dry-run 报告：存量 68 → 拟置 superseded 19 → 保留 49，含合并动作的簇 4 个）。
本脚本把该报告口径落成可重跑的执行器。

口径（与报告一致）：
- 范围：``world_facts`` 中 ``character_id=13`` 且 ``status='active'`` 且 ``predicate='curated'``
  的全部行（跨 kind）；
- 判据：复用生产判据本身 ``app.events.facts._same_curated_value``（不复制实现）；
- 聚类：按 id 从新到旧扫描，与该簇代表同义即归入，簇代表＝该簇里最新那行；
- 写入：成员行 ``status='superseded'`` / ``superseded_by=代表行 id`` /
  ``superseded_at``、``updated_at``＝now(naive UTC)（时间列写生产同格式的 naive 串）；
  代表行不动、任何行都不删；全部动作在同一事务内，失败整体回滚；
- 幂等：第二次 ``--apply`` 必为 0 变更（成员已非 active，不在扫描池）。

用法（默认 dry-run 只读；写库必须 ``--apply`` 与 ``--yes`` 同时给）：
    backend\\.venv\\Scripts\\python.exe scripts/curated_cleanup.py --db <path> --dry-run
    backend\\.venv\\Scripts\\python.exe scripts/curated_cleanup.py --db <path> --apply --yes
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DEFAULT_DB = str(_BACKEND / "data" / "sqlite" / "ai_companion.db")

# 范围三元组：选行、前置断言、UPDATE 守卫共用同一份常量
SCOPE_CHARACTER_ID = 13
SCOPE_STATUS = "active"
SCOPE_PREDICATE = "curated"

_DT_FMT = "%Y-%m-%d %H:%M:%S.%f"
# 全表指纹（越界写入自检用）：world_facts 千级行数，一次读全表开销可忽略
_FINGERPRINT_SQL = (
    "SELECT id, status, COALESCE(superseded_by, -1), CAST(superseded_at AS TEXT), "
    "CAST(updated_at AS TEXT), object_value, kind, character_id, predicate "
    "FROM world_facts ORDER BY id"
)


def _abs_db(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def _db_url(path: str) -> str:
    return "sqlite+aiosqlite:///" + _abs_db(path).replace(os.sep, "/")


def _open_session_factory(db_path: str):
    """返回 ``(session_factory, dispose)``——尽量复用项目既有会话工厂。

    项目既有方式＝在 import app.* 之前覆盖 DATABASE_URL（app.db.engine 的引擎是 import 时单例）。
    若同进程早已把模块引擎绑到别的库（典型：pytest 会话沙箱先于本脚本 import 了 app），模块级
    工厂不可信，改为按 --db 连接串现建同构引擎/工厂（参数与 app/db/{engine,session}.py 一致），
    确保本脚本只读写 --db 指定的那一个文件。
    """
    saved = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _db_url(db_path)
    try:
        from app.db.database import async_session_factory
        from app.db.engine import engine as module_engine
    finally:
        if saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved
    if str(module_engine.url) == _db_url(db_path):
        return async_session_factory, None
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    own = create_async_engine(
        _db_url(db_path),
        echo=False,
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory = async_sessionmaker(own, class_=AsyncSession, expire_on_commit=False)

    async def _dispose():
        await own.dispose()

    return factory, _dispose


def scope_ok(row: dict) -> bool:
    """行是否属于本范围（三条缺一不可）。"""
    return (
        row["character_id"] == SCOPE_CHARACTER_ID
        and row["status"] == SCOPE_STATUS
        and row["predicate"] == SCOPE_PREDICATE
    )


def scope_violations(rows) -> list[str]:
    """前置断言：范围内出现越界行即中止（防止选行条件被误改后波及其它角色/谓词/状态）。"""
    bad = [
        f"id={r['id']} 越出范围 (character_id={r['character_id']}, "
        f"status={r['status']}, predicate={r['predicate']})"
        for r in rows if not scope_ok(r)
    ]
    if any(not (r["object_value"] or "").strip() for r in rows):
        bad.append("存在 object_value 为空的行，判据无法比较")
    return bad


def plan_violations(clusters) -> list[str]:
    """计划自检：一行不得同时是代表与成员（否则取代链自指），代表必须是簇内最新行。"""
    reps = {c["representative"]["id"] for c in clusters}
    members = [m["id"] for c in clusters for m in c["members"]]
    bad = []
    if len(members) != len(set(members)):
        bad.append("同一行被归进多个簇")
    if reps & set(members):
        bad.append(f"行既是代表又是成员：{sorted(reps & set(members))}")
    for c in clusters:
        rep_id = c["representative"]["id"]
        if c["members"] and rep_id != max(m["id"] for m in c["members"] + [c["representative"]]):
            bad.append(f"簇代表 id={rep_id} 不是该簇最新行")
    return bad


def build_plan(rows, same) -> list[dict]:
    """按 id 从新到旧贪心聚类；``same`` 注入生产判据（保持纯函数便于测试）。

    返回簇列表（按代表从新到旧排列）：``{representative, members(旧→新)}``。
    比较口径＝「既有簇代表 vs 更旧的新行」，与 ``assert_curated`` 写入侧池序（id 倒序取首个同义）
    一致，故结果与 dry-run 报告逐字对齐。
    """
    clusters: list[dict] = []
    for row in sorted(rows, key=lambda r: r["id"], reverse=True):
        hit = next(
            (c for c in clusters
             if same(c["representative"]["object_value"], row["object_value"])),
            None,
        )
        if hit is None:
            clusters.append({"representative": row, "members": []})
        else:
            hit["members"].append(row)
    for c in clusters:
        c["members"].sort(key=lambda r: r["id"])
    return clusters


def plan_actions(clusters) -> list[tuple[int, int]]:
    """``[(成员 id, 代表 id), ...]``（按成员 id 升序，写入顺序确定）。"""
    return sorted((m["id"], c["representative"]["id"])
                  for c in clusters for m in c["members"])


def stats(clusters, total: int) -> dict:
    merge_clusters = [c for c in clusters if c["members"]]
    merged = sum(len(c["members"]) for c in merge_clusters)
    return {
        "total": total, "merged": merged, "kept": total - merged,
        "clusters": len(merge_clusters), "merge_clusters": merge_clusters,
    }


def format_report(st: dict, *, applied: bool = False) -> str:
    """每簇一段（代表 id + 原文；成员逐行 id/kind/原文）+ 末尾汇总行。"""
    verb = "已置 superseded" if applied else "拟置 superseded"
    lines = [
        (f"判据：app.events.facts._same_curated_value（C13/C13b）；范围："
         f"character_id={SCOPE_CHARACTER_ID} / status={SCOPE_STATUS} / predicate={SCOPE_PREDICATE}")
    ]
    for i, c in enumerate(st["merge_clusters"], 1):
        rep = c["representative"]
        lines.append("")
        lines.append(f"簇 {i} · 保留 id={rep['id']}（{rep['kind']}）")
        lines.append(f"  代表原文：{rep['object_value']}")
        for m in c["members"]:
            lines.append(f"  {verb} id={m['id']} kind={m['kind']} 原文：{m['object_value']}")
    lines.append("")
    lines.append(
        f"汇总：存量 {st['total']} 行｜{verb} {st['merged']} 行｜"
        f"合并后保留 {st['kept']} 行｜含合并动作的簇 {st['clusters']} 个"
    )
    return "\n".join(lines)


async def _load_rows(db) -> list[dict]:
    from sqlalchemy import select

    from app.models.memory import WorldFact

    rows = (await db.execute(
        select(WorldFact).where(
            WorldFact.character_id == SCOPE_CHARACTER_ID,
            WorldFact.status == SCOPE_STATUS,
            WorldFact.predicate == SCOPE_PREDICATE,
        ).order_by(WorldFact.id.desc())
    )).scalars().all()
    return [
        {
            "id": r.id, "character_id": r.character_id, "status": r.status,
            "predicate": r.predicate, "kind": r.kind, "object_value": r.object_value,
            "superseded_by": r.superseded_by,
        }
        for r in rows
    ]


async def _fingerprints(db) -> dict:
    from sqlalchemy import text

    return {row[0]: row[1:] for row in (await db.execute(text(_FINGERPRINT_SQL))).all()}


async def _apply(db, actions: list[tuple[int, int]], now_str: str) -> int:
    """在传入的会话（单事务）里执行写入；三重护栏，任一不满足即不提交。

    ①UPDATE 自带范围条件 ⇒ 越界行结构上不可能被改写；②逐条 rowcount 必须为 1；
    ③提交前比对全表指纹，实际变更集必须**恰好等于**计划变更集（代表行与其它行必须零变化）。
    """
    from sqlalchemy import text

    sql = text(
        "UPDATE world_facts SET status='superseded', superseded_by=:by, "
        "superseded_at=:now, updated_at=:now "
        "WHERE id=:id AND character_id=:char AND status='active' AND predicate='curated'"
    )
    before = await _fingerprints(db)
    for member_id, rep_id in actions:
        res = await db.execute(sql, {
            "by": rep_id, "now": now_str, "id": member_id, "char": SCOPE_CHARACTER_ID,
        })
        if res.rowcount != 1:
            raise RuntimeError(
                f"id={member_id} 命中 {res.rowcount} 行（预期 1）：状态已变或越出范围")
    after = await _fingerprints(db)
    changed = {i for i in set(before) | set(after) if before.get(i) != after.get(i)}
    expected = {i for i, _ in actions}
    if changed != expected:
        raise RuntimeError(
            f"变更集越界：多改 {sorted(changed - expected)}、漏改 {sorted(expected - changed)}")
    await db.commit()
    return len(changed)


async def _run(args, applied: bool) -> int:
    from app.events.facts import _same_curated_value
    from app.utils.logger import get_logger
    from app.utils.timeutil import now_naive_utc

    logger = get_logger("curated_cleanup")
    factory, dispose = _open_session_factory(args.db)
    try:
        async with factory() as db:
            rows = await _load_rows(db)
            problems = scope_violations(rows)
            clusters = build_plan(rows, _same_curated_value)
            problems += plan_violations(clusters)
            if problems:
                for p in problems:
                    print(f"中止：{p}", file=sys.stderr)
                return 2
            st = stats(clusters, len(rows))
            actions = plan_actions(clusters)
            print(format_report(st))
            if not applied:
                print("[dry-run] 未写库。确认无误后加 --apply --yes 执行。")
                return 0
            if not actions:
                print("[apply] 0 变更（幂等：范围内已无同义 active 成员）。")
                return 0
            try:
                n = await _apply(db, actions, now_naive_utc().strftime(_DT_FMT))
            except Exception as e:
                await db.rollback()
                logger.warning("curated_cleanup apply 失败已整体回滚：%s", e)
                print(f"中止：写入校验未通过，已整体回滚（0 行变更）：{e}", file=sys.stderr)
                return 2
            print(f"[apply] 本轮实际置 superseded {n} 行（代表行未改动、无删除）")
            return 0
    finally:
        if dispose is not None:
            await dispose()


def _preflight(args) -> bool:
    """双开关护栏 + 目标库存在性检查；不通过时已打印原因。"""
    if args.apply != args.yes:
        print(
            f"拒绝执行：--apply 与 --yes 必须同时提供（当前 apply={args.apply} "
            f"yes={args.yes}）；只读预演直接运行（默认 dry-run）即可。",
            file=sys.stderr,
        )
        return False
    if args.apply and args.dry_run:
        print("拒绝执行：--apply 与 --dry-run 互斥，请二选一。", file=sys.stderr)
        return False
    if not os.path.exists(_abs_db(args.db)):
        print(f"DB 不存在: {_abs_db(args.db)}", file=sys.stderr)
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="curated 存量清账（C14）：默认 dry-run 只读；写库必须 --apply 与 --yes 同时给")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"数据库路径（默认 {DEFAULT_DB}；生产实跑由维护者备份后执行）")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="只读打印计划（缺省即此行为）")
    ap.add_argument("--apply", action="store_true", help="实际写库（必须与 --yes 同时给）")
    ap.add_argument("--yes", action="store_true", help="写库确认开关（必须与 --apply 同时给）")
    args = ap.parse_args(argv)
    if not _preflight(args):
        return 2
    args.db = _abs_db(args.db)
    return asyncio.run(_run(args, applied=args.apply))


if __name__ == "__main__":
    sys.exit(main())
