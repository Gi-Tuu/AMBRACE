# -*- coding: utf-8 -*-
"""curated 存量清账执行器（C14 / C14b，2026-09-25）。

背景：C13/C13b 判据只作用于新写入，生产库 char13 的 active curated 存量仍有同义条目
未合并（dry-run 报告：存量 68 → 拟置 superseded 19 → 保留 49，含合并动作的簇 4 个）。
本脚本把该报告口径落成可重跑的执行器；C14b 补齐 dry-run 报告 §4 的四项执行口径。

口径（与报告一致）：
- 范围：``world_facts`` 中 ``character_id=13`` 且 ``status='active'`` 且 ``predicate='curated'``
  的全部行（跨 kind）；
- 判据：复用生产判据本身 ``app.events.facts._same_curated_value``（不复制实现）；
- 聚类：按 id 从新到旧扫描，与该簇代表同义即归入，簇代表＝该簇里最新那行；
- 写入成员行：``status='superseded'`` / ``superseded_by=代表行 id`` / ``superseded_at``、
  ``updated_at``＝now(naive UTC)（时间列写生产同格式的 naive 串）；任何行都不删；
- 写入代表行（§4.2 证据归并）：成员的证据并入簇代表——``sources_json``/``links_json`` 取并集、
  ``confidence`` 取 max、``verify_state`` 只升不降、``stale_after`` 取更晚（None＝永不过期视为
  不劣化，绝不用有限值覆盖 None）。**归并逻辑复用生产 helper ``app.events.facts.merge_curated_evidence``
  （即 ``assert_curated`` 的 same 分支，不复制实现）**；代表行正文/kind/身份列一律不动，仅在证据确有
  变化时才写回（无证据可并 ⇒ 代表行逐字节不变）。
- 备份（§4.4，fail-closed）：``--apply`` 在写库前用 sqlite3 在线备份 API 把库整份复制到
  ``<db 同目录>/backup_curated_cleanup_<YYYYmmdd_HHMMSS>.sqlite``；备份不存在/大小为 0/写入失败
  ⇒ 直接拒绝执行并返回非 0，绝不「先跑再说」。
- 审计（§4.4）：每一行变更都有一条可追溯记录（``id``/``kind``/旧 status→新 status/``superseded_by``
  /时间戳），写 stderr（可选 ``--audit-log`` 落文件），不止打合计。
- 分批（§4.4）：``--limit N`` 一次最多处理 N 个**含合并动作的簇**（按代表 id 从新到旧），未处理
  的簇下次可续（幂等）。
- 幂等：第二次 ``--apply`` 必为 0 变更（成员已非 active，不在扫描池）。
- 全部动作在同一事务内，任一护栏不满足即整体回滚。

用法（默认 dry-run 只读；写库必须 ``--apply`` 与 ``--yes`` 同时给）：
    backend\\.venv\\Scripts\\python.exe scripts/curated_cleanup.py --db <path> --dry-run
    backend\\.venv\\Scripts\\python.exe scripts/curated_cleanup.py --db <path> --apply --yes [--limit N]
"""
import argparse
import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

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
# 全表指纹（越界写入自检用）：world_facts 千级行数，一次读全表开销可忽略。
# C14b 起把 5 个证据列也纳入指纹，使「代表行仅证据列变化」被同一套自检钉死。
_FINGERPRINT_SQL = (
    "SELECT id, status, COALESCE(superseded_by, -1), CAST(superseded_at AS TEXT), "
    "CAST(updated_at AS TEXT), object_value, kind, character_id, predicate, "
    "sources_json, links_json, confidence, verify_state, CAST(stale_after AS TEXT) "
    "FROM world_facts ORDER BY id"
)
# 证据列读取（代表行与成员行共用；时间列统一 CAST 成文本，便于逐字节复制不漂移格式）
_EVIDENCE_COLS = ("sources_json", "links_json", "confidence", "verify_state", "stale_after")
_EVIDENCE_SQL = (
    "SELECT sources_json, links_json, confidence, verify_state, CAST(stale_after AS TEXT) "
    "FROM world_facts WHERE id=:id"
)
# 代表行证据写回守卫：三条范围条件缺一不可（越界代表结构上不可能被改写）
_UPDATE_REP_EVIDENCE_SQL = (
    "UPDATE world_facts SET sources_json=:sources, links_json=:links, confidence=:conf, "
    "verify_state=:verify, stale_after=:stale, updated_at=:now "
    "WHERE id=:id AND character_id=:char AND status='active' AND predicate='curated'"
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


def _iso_parse(text):
    """把 CAST(... AS TEXT) 的时间串解析回 datetime，仅供 stale_after 的「取更晚」比较。"""
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).strip())
    except ValueError:
        return None


def _stale_to_merge(rep_stale, member_stale):
    """§4.2 stale_after「取更晚」：返回应并入代表的来路值，否则 None。

    None＝永不过期（不劣化），故成员有限值不得覆盖代表的 None；仅当成员更晚才并入。
    比较为「串解析后的 datetime 序」，写回时复制成员原始文本（逐字节），不改格式。
    """
    if member_stale is None:
        return None
    if rep_stale is None:
        return None
    m_dt = _iso_parse(member_stale)
    r_dt = _iso_parse(rep_stale)
    if m_dt is None:
        return None
    return member_stale if (r_dt is None or m_dt > r_dt) else None


def write_audit(lines, log_path: str | None = None) -> None:
    """逐行审计日志（§4.4）：一行变更 = 一条记录，写 stderr（可选再落文件），不止打合计。"""
    buf = "\n".join(lines) + ("\n" if lines else "")
    sys.stderr.write(buf)
    sys.stderr.flush()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(buf)


def create_backup(db_path: str) -> tuple[str, int]:
    """apply 前强制备份（fail-closed，§4.4）：sqlite3 在线备份 API 整份复制库文件。

    备份成功且大小 > 0 才返回 ``(备份路径, 大小)``；任何异常或空文件一律抛错，交调用方拒绝执行。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(os.path.dirname(_abs_db(db_path)),
                       f"backup_curated_cleanup_{ts}.sqlite")
    src = sqlite3.connect(f"file:{_abs_db(db_path)}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dst)
        try:
            with out:
                src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    if not os.path.exists(dst) or os.path.getsize(dst) <= 0:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        raise RuntimeError(f"备份文件为空或未生成：{dst}")
    return dst, os.path.getsize(dst)


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


async def _read_evidence(db, row_id: int) -> SimpleNamespace:
    """读取一行的 5 个证据列（时间列取 CAST 文本），装进可原地改的轻量对象。

    sources_json/links_json 保持「库中原样的 JSON 文本」（与 assert_curated 读 same.sources_json
    的语义一致），供 merge helper 直接读；成员来路的「已解析列表」在调用点用 _safe_json 现取。
    """
    from sqlalchemy import text

    res = (await db.execute(text(_EVIDENCE_SQL), {"id": row_id})).first()
    if res is None:
        raise RuntimeError(f"证据读取失败：id={row_id} 不在库中")
    sources_json, links_json, confidence, verify_state, stale_after = res
    return SimpleNamespace(
        sources_json=sources_json if sources_json is not None else "[]",
        links_json=links_json if links_json is not None else "[]",
        confidence=confidence, verify_state=verify_state, stale_after=stale_after,
    )


def _evidence_tuple(ns: SimpleNamespace) -> tuple:
    """与 _FINGERPRINT_SQL 尾部证据列同口径的可比较元组（sources/links 取 JSON 文本）。"""
    return (ns.sources_json, ns.links_json, ns.confidence, ns.verify_state, ns.stale_after)


async def _write_evidence(db, rep_id: int, ns: SimpleNamespace, now_str: str):
    from sqlalchemy import text

    res = await db.execute(text(_UPDATE_REP_EVIDENCE_SQL), {
        "sources": ns.sources_json, "links": ns.links_json, "conf": ns.confidence,
        "verify": ns.verify_state, "stale": ns.stale_after, "now": now_str,
        "id": rep_id, "char": SCOPE_CHARACTER_ID,
    })
    if res.rowcount != 1:
        raise RuntimeError(f"代表行 id={rep_id} 证据写回命中 {res.rowcount} 行（预期 1）")


async def _apply(db, merge_clusters: list[dict], now_str: str, audit_log=None):
    """在同一会话（单事务）内落库：成员置 superseded + 代表行证据归并。四重护栏，任一不满足即不提交。

    ①成员/代表 UPDATE 均自带范围守卫 ⇒ 越界行结构上不可能被改写；②逐条 rowcount 必须为 1；
    ③代表行仅当归并后证据确有变化才写回（无证据可并 ⇒ 逐字节不动）；④提交前比对全表指纹，
    实际变更行集合与其**逐列新值**必须恰好等于计划（范围外行、成员行证据都必须零变化）。
    """
    from sqlalchemy import text

    from app.events.facts import _safe_json, merge_curated_evidence

    member_sql = text(
        "UPDATE world_facts SET status='superseded', superseded_by=:by, "
        "superseded_at=:now, updated_at=:now "
        "WHERE id=:id AND character_id=:char AND status='active' AND predicate='curated'"
    )
    before = await _fingerprints(db)

    supersedes = []      # (成员 id, 代表 id)
    rep_writes = []      # (代表 id, 归并后的 SimpleNamespace)
    for c in merge_clusters:
        rep_id = c["representative"]["id"]
        cur = await _read_evidence(db, rep_id)
        merged = _evidence_tuple(cur)
        for m in sorted(c["members"], key=lambda r: r["id"]):
            mv = await _read_evidence(db, m["id"])
            merge_curated_evidence(
                cur,
                sources=_safe_json(mv.sources_json), links=_safe_json(mv.links_json),
                verify_state=mv.verify_state,
                stale_after=_stale_to_merge(cur.stale_after, mv.stale_after),
                confidence=mv.confidence,
            )
        if _evidence_tuple(cur) != merged:
            rep_writes.append((rep_id, cur))

    for rep_id, ns in rep_writes:
        await _write_evidence(db, rep_id, ns, now_str)
    for rep_id, ns in rep_writes:
        cur = await _read_evidence(db, rep_id)
        if _evidence_tuple(cur) != _evidence_tuple(ns):
            raise RuntimeError(f"代表行 id={rep_id} 证据写回后与计划不一致")
    for rep in merge_clusters:
        for m in rep["members"]:
            supersedes.append((m["id"], rep["representative"]["id"]))
    for member_id, rep_id in supersedes:
        res = await db.execute(member_sql, {
            "by": rep_id, "now": now_str, "id": member_id, "char": SCOPE_CHARACTER_ID,
        })
        if res.rowcount != 1:
            raise RuntimeError(
                f"id={member_id} 命中 {res.rowcount} 行（预期 1）：状态已变或越出范围")

    after = await _fingerprints(db)
    changed = {i for i in set(before) | set(after) if before.get(i) != after.get(i)}
    expected_ids = {i for i, _ in supersedes} | {i for i, _ in rep_writes}
    if changed != expected_ids:
        raise RuntimeError(
            f"变更集越界：多改 {sorted(changed - expected_ids)}、"
            f"漏改 {sorted(expected_ids - changed)}")

    audit_lines = []
    for mid, by in sorted(supersedes):
        audit_lines.append(
            f"AUDIT id={mid} kind={before[mid][5]} status: active -> superseded "
            f"superseded_by={by} at={now_str}")
    for rid, _ns in sorted(rep_writes):
        audit_lines.append(
            f"AUDIT id={rid} kind={before[rid][5]} status: active -> active "
            f"superseded_by=- evidence_merged at={now_str}")
    write_audit(audit_lines, audit_log)
    await db.commit()
    return len(supersedes), len(rep_writes)


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
            merge_clusters = [c for c in st["merge_clusters"]]
            if args.limit and args.limit < len(merge_clusters):
                merge_clusters = merge_clusters[:args.limit]
                print(f"[分批] 本轮处理前 {len(merge_clusters)}/{st['clusters']} 个含合并簇"
                      f"（--limit {args.limit} 按代表 id 从新到旧），其余下次续跑。")
            try:
                backup_path, size = create_backup(args.db)
            except Exception as e:
                print(f"拒绝执行：备份未成功，已中止写入（fail-closed）：{e}", file=sys.stderr)
                return 2
            print(f"[备份] {backup_path}（{size} 字节）")
            try:
                n, m = await _apply(db, merge_clusters, now_naive_utc().strftime(_DT_FMT),
                                    args.audit_log)
            except Exception as e:
                await db.rollback()
                logger.warning("curated_cleanup apply 失败已整体回滚：%s", e)
                print(f"中止：写入校验未通过，已整体回滚（0 行变更）：{e}", file=sys.stderr)
                return 2
            print(f"[apply] 本轮实际置 superseded {n} 行、证据归并代表行 {m} 行"
                  f"（代表正文/kind/身份未改、成员证据未改、无删除）")
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
    if args.rollback:
        print("拒绝执行：--rollback 本期不提供（见报告 §4.3 手工回滚口径），本批未实现该开关。",
              file=sys.stderr)
        return False
    if not os.path.exists(_abs_db(args.db)):
        print(f"DB 不存在: {_abs_db(args.db)}", file=sys.stderr)
        return False
    if args.limit is not None and args.limit <= 0:
        print(f"拒绝执行：--limit 需为正整数（当前 {args.limit}）。", file=sys.stderr)
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="curated 存量清账（C14/C14b）：默认 dry-run 只读；写库必须 --apply 与 --yes 同时给")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"数据库路径（默认 {DEFAULT_DB}；生产实跑由维护者备份后执行）")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="只读打印计划（缺省即此行为）")
    ap.add_argument("--apply", action="store_true",
                    help="实际写库（必须与 --yes 同时给；写前强制备份 fail-closed）")
    ap.add_argument("--yes", action="store_true", help="写库确认开关（必须与 --apply 同时给）")
    ap.add_argument("--limit", type=int, default=None,
                    help="分批：一次最多处理 N 个「含合并动作的簇」（按代表 id 从新到旧），"
                         "其余下次续跑（幂等）；缺省不限")
    ap.add_argument("--audit-log", dest="audit_log", default=None,
                    help="逐行审计日志追加落盘路径（审计记录同时写 stderr）；缺省仅 stderr")
    ap.add_argument("--rollback", action="store_true",
                    help="本期不提供（无标记列/审计表可区分本工具置的行与自然 supersede 行，"
                         "见报告 §4.3 手工回滚口径）；传入即拒绝")
    args = ap.parse_args(argv)
    if not _preflight(args):
        return 2
    args.db = _abs_db(args.db)
    return asyncio.run(_run(args, applied=args.apply))


if __name__ == "__main__":
    sys.exit(main())
