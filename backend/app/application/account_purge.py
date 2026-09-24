# -*- coding: utf-8 -*-
"""控制台删号·第二期第一批：**物理清除器**（「真删」这一条路，2026-09-24 派单）。

第一期（``account_deletion`` + ``user_cascade``）管的是「怎么标记、会带走什么」；
本模块管「怎么把它干净地删掉」。范围只有清除本身——
**不做**调度器自动化（扫 ``purge_after`` 自动跑是下一批）、**不做**控制台 UI（第三期）。

清除顺序（方案 v2 §3.2，硬依赖，不可调换）
------------------------------------------
1. **前置备份（fail-closed）**：调既有 ``scripts/backup.py`` 的 ``do_backup()``；
   备份拿不到就**中止**，绝不「备份失败也照删」——删除不可逆，这一步比任何确认弹窗都实在。
2. **第 0 步固化三份集合**（必须在任何 DELETE 之前）：目标 ``user_id``、该账号全部
   ``ai_characters.id``、该账号全部 ``chat_session.id``（外加向量反查用的 ``memory.id``）。
   理由与 v2 一致：``chat_sessions`` / ``ai_characters`` 行一删，文件归属与角色族就**永久不可反查**；
   集合同时落进 ``account_purge_jobs.cursor_json``，进程重启后照样续跑。
3. **文件隔离**：``uploads`` 没有归属表，归属只从路径推导 → 判据**一律走** :mod:`app.uploads_gate`
   （:func:`resolve_upload_scope`），本模块不另写一份路径规则。命中该账号的目录
   **移到 ``data/trash/<uid>/``，不直接删**（最后一次反悔机会；本批**不清理 trash 内容**）。
4. **先向量后记忆行**：``vector_store.delete_memory_vectors_by_user``（老向量缺 ``user_id``
   metadata 的按 ``memory_id`` 反查补齐）→ 再 BM25 按角色失效（只调既有 ``invalidate``）。
5. **按 cascade 清单删表**：清单只来自 :func:`user_cascade.discover_purge_plan`
   （用户族 ∪ 角色族 + 例外），**没有手写表名单**；每表按归属列分批删（5000 行/批）、
   每批提交、批间 ``await asyncio.sleep(0)`` 让出事件循环（40 万行不能把正在服务的 SQLite 卡死）。
6. **根表最后**：先 family，后 ``ai_characters``，最后 ``users``；``users`` 仍报外键错时把
   **报错表名与语句原文**写进报告并**中止**（不死重试——留着半删状态比猜着删安全）。
7. **收尾核对**：``PRAGMA foreign_key_check``（应为空）写进报告，并把体量快照写进审计。

为什么「每批提交」而不是字面上的「每表提交」
--------------------------------------------
派单口径是 ``DELETE ... WHERE <归属列>=? LIMIT 5000``，但本机 SQLite 未编译
``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``（实测 ``DELETE ... LIMIT`` 直接语法错），
故分批走 ``rowid IN (SELECT rowid ... LIMIT n)`` 达到同一效果；每批就提交才真正满足
「按表提交而不是一个巨型事务」的原意（WAL 不涨、客户端不卡），且崩在半张表中间时
已删的那几批不会回滚重来（重扫得 0 行，属正常）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import bindparam, text

from app.application import account_deletion, user_cascade
from app.application.admin_audit_service import record as _audit_record
from app.config import settings
from app.uploads_gate import resolve_upload_scope
from app.utils.logger import get_logger

_logger = get_logger("application.account_purge")

JOB_TABLE = "account_purge_jobs"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

#: 每批删除行数（派单口径 5000：再大就撑长事务，再小就批次爆炸）
BATCH_ROWS = 5000
#: 两张根表：候选族删完才动（先角色根、后账号根）
CHARACTER_ROOT = user_cascade.CHARACTER_ROOT_TABLE   # "ai_characters"
ACCOUNT_ROOT = "users"

# 文案：与第一期共用 :func:`account_deletion._msg`（唯一真源 app/i18n.py）。
# 第二期就地表与「先查自己再回落」的两级回落已随第三期 i18n 回填一并删除——
# 同一条拒绝理由不许有两种说法，也不许有两张表。
_msg = account_deletion._msg


# ── 前置备份（fail-closed）─────────────────────────────────────────────────────

def _run_backup() -> str:
    """跑一次既有每日备份，返回 zip 路径；**拿不到有效 zip 一律抛错**（fail-closed）。

    复用 :func:`app.application.system._load_backup_module`（``scripts/backup.py`` 不在 sys.path，
    按文件路径加载；备份口径不允许有两份实现）。``do_backup()`` 返回的是**文字摘要**
    （内含 zip 路径）而非路径本身，故成功与否按落盘结果判定：当天的 zip 存在且非空
    （与 ``trigger_backup`` 端点同一判据，含「当天已有备份即复用」的口径）。
    """
    from app.application.system import _load_backup_module

    mod = _load_backup_module()
    summary = mod.do_backup()
    zip_path = os.path.join(mod.BACKUP_ROOT, datetime.now().strftime("%Y%m%d") + ".zip")
    if not os.path.isfile(zip_path):
        raise RuntimeError(f"备份未产出文件：{summary}")
    if os.path.getsize(zip_path) <= 0:
        raise RuntimeError(f"备份文件为空：{zip_path}")
    return zip_path


# ── 账本（account_purge_jobs）─────────────────────────────────────────────────

async def _read_job(conn, user_id: int) -> dict[str, Any] | None:
    row = (await conn.execute(text(
        f"SELECT id, user_id, status, started_at, finished_at, cursor_json, report_json, error "
        f"FROM {JOB_TABLE} WHERE user_id = :uid"), {"uid": int(user_id)})).mappings().first()
    return dict(row) if row else None


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def _open_job(conn, user_id: int) -> tuple[int, str, dict[str, Any]]:
    """取/建该账号的清除账本 → ``(job_id, 打开前的状态, cursor)``。

    一账号一行（``user_id`` UNIQUE，也是续跑定位键）。``running`` / ``failed`` 都按「续跑」打开：
    状态回到 running、进度沿用 ``cursor_json``。刻意不因「上一轮还是 running」而拒绝重入——
    进程被杀留下的正是 running，而本端点是管理员手工动作，重入由幂等 DELETE 兜住。

    ``done`` 被重新打开（账号恢复后又删了一遍）时**清空进度**：上一轮的固化集合指向的是
    已不存在的行，沿用会漏掉这一轮新产生的数据（宁可重扫，不可漏删）。
    """
    uid = int(user_id)
    job = await _read_job(conn, uid)
    if job is None:
        await conn.execute(text(
            f"INSERT INTO {JOB_TABLE} (user_id, status, started_at, cursor_json) "
            "VALUES (:uid, :s, :t, '{}')"),
            {"uid": uid, "s": STATUS_RUNNING, "t": account_deletion.now_utc()})
        await conn.commit()
        row = await _read_job(conn, uid)
        return int(row["id"]), "", {}
    prev = str(job["status"] or "")
    await conn.execute(text(f"UPDATE {JOB_TABLE} SET status = :s, error = NULL WHERE id = :i"),
                       {"s": STATUS_RUNNING, "i": int(job["id"])})
    await conn.commit()
    return int(job["id"]), prev, ({} if prev == STATUS_DONE else _loads(job.get("cursor_json")))


async def _save_cursor(conn, job_id: int, cursor: dict[str, Any]) -> None:
    """进度落盘（每完成一个阶段/一张表写一次——重启后能接着跑全靠它）。"""
    await conn.execute(text(f"UPDATE {JOB_TABLE} SET cursor_json = :c WHERE id = :i"),
                       {"c": json.dumps(cursor, ensure_ascii=False, default=str), "i": int(job_id)})
    await conn.commit()


async def _finish_job(conn, job_id: int, *, status: str, cursor: dict[str, Any],
                      report: dict[str, Any], error: str | None = None) -> None:
    await conn.execute(text(
        f"UPDATE {JOB_TABLE} SET status = :s, finished_at = :t, cursor_json = :c, "
        "report_json = :r, error = :e WHERE id = :i"),
        {"s": status, "t": account_deletion.now_utc(), "i": int(job_id),
         "c": json.dumps(cursor, ensure_ascii=False, default=str),
         "r": json.dumps(report, ensure_ascii=False, default=str),
         "e": (error or None)})
    await conn.commit()


async def _account_still_exists(conn, uid: int) -> bool:
    return (await conn.execute(text(
        f"SELECT 1 FROM {ACCOUNT_ROOT} WHERE id = :uid LIMIT 1"), {"uid": int(uid)})).first() is not None


# ── 第 0 步：固化集合 + 固化删除依据 ──────────────────────────────────────────

def _frag(column: str, kind: str, *, family: str, cids: list[int]) -> str:
    """一个归属列 → 删除依据 SQL 片段。

    **片段构造直接调用 user_cascade 的函数**：dry-run 报出的行数与实际删掉的行必须同源，
    两套口径必然漂移（v2 §二.4 的教训就是这个）。``speaker_id`` 双语义按已算好的
    「本人（且不与别人角色撞号）∪ 该账号角色集合」片段用。
    """
    if family == "character":
        return user_cascade._character_fragment(column)
    if kind == user_cascade.KIND_DUAL:
        dual = user_cascade._dual_fragments(column)
        return dual["user"] if not cids else f"({dual['user']} OR {dual['character']})"
    return user_cascade._ownership_fragment(column)


def _delete_specs(plan: dict[str, Any]) -> list[list[str]]:
    """计划 → ``[[表, WHERE 片段], ...]``：只取 ``deletable`` 列（``updated_by`` 一律不进 WHERE）。"""
    cids = [int(c) for c in plan["character_ids"]]
    frags: dict[str, list[str]] = {}
    for family, key in (("user", "user_family"), ("character", "character_family")):
        for entry in plan[key]:
            for col in entry["columns"]:
                if not col.get("deletable"):
                    continue
                frag = _frag(col["column"], col["kind"], family=family, cids=cids)
                bucket = frags.setdefault(entry["table"], [])
                if frag not in bucket:
                    bucket.append(frag)
    return [[table, " OR ".join(f"({f})" for f in items)]
            for table, items in sorted(frags.items())]


async def _collect_frozen(conn, *, uid: int, plan: dict[str, Any]) -> dict[str, Any]:
    """在任何 DELETE 之前把「归属反查用的集合」固化下来（v2 §3.2 第 0 步）。

    - ``character_ids``：该账号名下全部角色 id（角色族那些表与 BM25 缓存都按它删）；
    - ``session_ids``：该账号全部会话 id（``uploads/{sid}/``、``files/{sid}/``、``voice/{sid}/``
      的文件归属**只能**在 ``chat_sessions`` 行被删之前反查）；
    - ``memory_ids``：该账号相关记忆 id（老向量缺 ``user_id`` metadata 时的反查依据）；
    - ``tables``：cascade 计划转成的「表 + WHERE 片段」——固化后重跑不重扫，
      免得删了一半之后再按库结构发现出另一份清单。
    """
    cids = [int(c) for c in plan["character_ids"]]
    session_ids = [int(r[0]) for r in (await conn.execute(text(
        'SELECT "id" FROM "chat_sessions" WHERE "user_id" = :uid ORDER BY "id"'), {"uid": uid}))]
    mem_sql = 'SELECT "id" FROM "memories" WHERE ("user_id" = :uid'
    mem_params: dict[str, Any] = {"uid": uid}
    if cids:
        mem_sql += ' OR "character_id" IN :cids'
        mem_params["cids"] = cids
    mem_sql += ') ORDER BY "id"'
    mem_stmt = text(mem_sql)
    if cids:
        mem_stmt = mem_stmt.bindparams(bindparam("cids", expanding=True))
    memory_ids = [int(r[0]) for r in (await conn.execute(mem_stmt, mem_params))]
    return {
        "user_id": uid,
        "mode": plan["scope"],
        "character_ids": cids,
        "character_count": len(cids),
        "session_ids": session_ids,
        "session_count": len(session_ids),
        "memory_ids": memory_ids,
        "memory_count": len(memory_ids),
        "tables": _delete_specs(plan),
    }


# ── 删表 ──────────────────────────────────────────────────────────────────────

class PurgeFKError(RuntimeError):
    """删根表时被物理外键挡住：表名 + 语句原文一并进报告（v2 §六「报错表名不在清单里」的处置）。"""

    def __init__(self, *, table: str, statement: str, cause: str):
        super().__init__(f"{table} 删除被外键拒绝：{cause}")
        self.table = table
        self.statement = statement
        self.cause = cause


async def _delete_order(conn, tables: list[str]) -> tuple[list[str], list[str]]:
    """按**物理外键**把候选表排成「子表先删」（``PRAGMA foreign_key_list``）。

    字母序不保证外键安全（``chat_messages`` 引用 ``chat_sessions``，先删父表直接报错）。
    自引用（``users.parent_id`` / ``moment_comments.parent_id``）不构成表间顺序约束，跳过。
    返回 ``(顺序, 互相引用成环、只能按名字序硬试的表)``。
    """
    cand = set(tables)
    parents: dict[str, set[str]] = {t: set() for t in tables}   # t 引用的候选父表
    dependents: dict[str, int] = {t: 0 for t in tables}         # 引用 t 的候选子表数（0 = 可先删）
    for t in tables:
        for fk in (await conn.execute(text(f"PRAGMA foreign_key_list({user_cascade._q(t)})"))):
            ref = fk[2]
            if ref in cand and ref != t:
                parents[t].add(ref)
                dependents[ref] += 1
    order: list[str] = []
    pending = set(tables)
    ready = sorted(t for t in tables if dependents[t] == 0)
    while ready:
        t = ready.pop(0)
        if t not in pending:
            continue
        pending.discard(t)
        order.append(t)
        for p in sorted(parents[t]):
            dependents[p] -= 1
            if dependents[p] == 0 and p in pending:
                ready.append(p)
    leftovers = sorted(pending)
    order += leftovers
    # 根表殿后：ai_characters 被一批表外键指向，删早了必炸；users 在 purge_account 里单独收尾
    if CHARACTER_ROOT in order:
        order.remove(CHARACTER_ROOT)
        order.append(CHARACTER_ROOT)
    return order, leftovers


def _tracker(table: str, base: int, stats: dict[str, int], save) -> Any:
    """「每提交一批就把累计行数记进账本」的回调（崩在半张表中间时已删的行数不丢）。

    ``base`` 是上一轮在这张表上已记的行数；本轮累计 ``total`` 行 → 记 ``base + total``。
    于是「一批已提交、下一批没跑完」这种半张表状态在重跑后计数仍然准确
    （重跑只补剩下的行，两边相加 = 一次删完的行数）。
    """
    async def _progress(total: int) -> None:
        stats[table] = base + int(total)
        await save()

    return _progress


async def _purge_table(conn, table: str, where: str, *, uid: int, cids: list[int],
                       limit: int = BATCH_ROWS, on_progress=None) -> int:
    """按归属列分批删一张表（每批 ``LIMIT`` 行、每批提交、批间让出事件循环）。

    单批失败**向上抛**（由调用方记进账本并中止）：这里静默继续就是「留下半删状态还看不出来」。
    """
    q = user_cascade._q(table)
    vcols = sorted({m for m in re.findall(r":v_([A-Za-z0-9_]+)", where)})
    ccols = sorted({m for m in re.findall(r":c_([A-Za-z0-9_]+)", where)})
    params: dict[str, Any] = {f"v_{c}": uid for c in vcols}
    params.update({f"c_{c}": list(cids) for c in ccols})
    stmt = text(
        f"DELETE FROM {q} WHERE rowid IN "
        f"(SELECT rowid FROM {q} WHERE {where} LIMIT :_batch)"
    ).bindparams(*[bindparam(f"c_{c}", expanding=True) for c in ccols])
    total = 0
    while True:
        rp = await conn.execute(stmt, {**params, "_batch": int(limit)})
        n = int(rp.rowcount or 0)
        await conn.commit()
        total += n
        if n == 0:
            return total
        if on_progress is not None:
            await on_progress(total)
        await asyncio.sleep(0)


async def _delete_root(conn, table: str, uid: int) -> int:
    """账号根收口：``users`` 按主键删（它没有任何归属列，cascade 计划里本就不该出现它）。

    仍报外键错 → 抛 :class:`PurgeFKError`（表名 + 语句原文进报告）。刻意不死重试：
    报错说明清单或顺序有缺口，重试只会把同一句话再撞一遍。
    """
    q = user_cascade._q(table)
    statement = f"DELETE FROM {q} WHERE id = {int(uid)}"
    try:
        rp = await conn.execute(text(statement), {"uid": int(uid)})
        await conn.commit()
    except Exception as e:
        raise PurgeFKError(table=table, statement=statement, cause=repr(e)) from e
    return int(rp.rowcount or 0)


# ── 文件隔离（uploads 按 uploads_gate 口径；移进 trash，不删）──────────────────

def _data_dir() -> Path:
    return Path(settings.PROJECT_ROOT) / "data"


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.exists() else 0


def _move_tree(src: Path, dst: Path) -> int:
    """整目录搬到 ``dst``（同盘优先 ``os.replace``，失败回退复制+删）。返回搬走的文件条目数。

    目标已存在时**合并**（续跑友好：上一轮搬了一半，这轮接着搬剩下的）；
    单文件失败只跳过它（源目录留着，报告里体现为 ``partial_dirs``），不吞掉整批。
    """
    if not src.is_dir():
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        try:
            os.replace(src, dst)
            return _count_files(dst)
        except OSError:
            pass
    moved = 0
    for root, _dirs, files in os.walk(src):
        rp = Path(root)
        for fn in files:
            s = rp / fn
            d = dst / s.relative_to(src)
            d.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(s, d)
            except OSError:
                try:
                    shutil.copy2(s, d)
                    s.unlink()
                except OSError as e:
                    _logger.warning("purge file move failed %s: %s", s, e)
                    continue
            moved += 1
    for root, _dirs, _files in os.walk(src, topdown=False):   # 搬空后的目录壳
        try:
            os.rmdir(root)
        except OSError:
            pass
    return moved


def _owned_upload_dirs(uploads: Path, *, uid: int, session_ids: set[int],
                       task_ids: set[int]) -> list[str]:
    """列出归属判定为该账号的 uploads 相对目录（**判据不自己写**）。

    候选目录逐个丢给 :func:`app.uploads_gate.resolve_upload_scope`：
    ``avatars|moments|images|phone/{uid}``、``emojis/user/{uid}`` 是用户目录；
    裸数字 ``{session_id}/``、``files/{sid}/``、``voice/{sid}/`` 按第 0 步的会话集合匹配；
    ``douyin/{task_id}/`` 按预筛出的 ``DouyinPending.tenant_id`` 判定；
    ``pets_assets|pets|tts|preview|market`` 等共享资源，以及解析不出归属的未知布局，一律不动。

    只下探一层就按 ``head/child`` 的**实际解析结果**取舍，不能按 ``head`` 的名字先粗判：
    ``douyin``/``emojis`` 这两类「head 自身判为 shared、子层却有归属」的布局，
    一旦按 head 早退就会整族漏掉（``douyin/{task}`` 的草稿图、``emojis/user/{uid}`` 的自制表情）。
    """
    if not uploads.is_dir():
        return []
    candidates: list[str] = []
    for head in sorted(p for p in uploads.iterdir() if p.is_dir()):
        kind0 = resolve_upload_scope(head.name)[0]
        if kind0 in ("user", "session", "douyin_task"):
            candidates.append(head.name)        # 裸 {session_id}/ 与 {task_id}/ 顶层目录
            continue
        for child in sorted(p for p in head.iterdir() if p.is_dir()):
            rel = f"{head.name}/{child.name}"
            if head.name == "emojis" and child.name == "user":
                # 第三层才是归属键（emojis/market 在上一轮判为 shared，压根不进这一支）
                candidates += [f"emojis/user/{g.name}"
                               for g in sorted(p for p in child.iterdir() if p.is_dir())]
            elif resolve_upload_scope(rel)[0] in ("user", "session", "douyin_task"):
                candidates.append(rel)
    owned: list[str] = []
    for rel in candidates:
        kind, key = resolve_upload_scope(rel)
        if key is None:
            continue
        key = int(key)
        if (kind == "user" and key == int(uid)) \
                or (kind == "session" and key in session_ids) \
                or (kind == "douyin_task" and key in task_ids):
            owned.append(rel)
    return owned


def _isolate_files(*, uid: int, session_ids: list[int], task_ids: list[int]) -> dict[str, Any]:
    """把该账号的文件**移进** ``data/trash/<uid>/``（不直接删；延迟清理留给后续批次）。

    目录不存在＝0 个，不报错（续跑时上一轮已搬走是常态）。
    """
    data = _data_dir()
    trash = data / "trash" / str(int(uid))
    dirs: list[str] = []
    moved = 0
    for rel in _owned_upload_dirs(data / "uploads", uid=uid,
                                  session_ids=set(session_ids), task_ids=set(task_ids)):
        src = data / "uploads" / rel
        n = _move_tree(src, trash / "uploads" / rel)
        if not src.exists() or n:
            dirs.append(rel)
        moved += n
    mcp_src = data / "mcp" / f"user_{int(uid)}"
    mcp_moved = _move_tree(mcp_src, trash / "mcp" / f"user_{int(uid)}")
    leftover = [rel for rel in dirs if (data / "uploads" / rel).exists()]
    if mcp_src.exists():
        leftover.append(f"mcp/user_{int(uid)}")
    return {
        "trash_dir": str(trash),
        "upload_dirs": dirs,
        "upload_dir_count": len(dirs),
        "files_moved": moved + mcp_moved,
        "mcp_files_moved": mcp_moved,
        "partial_dirs": leftover,       # 有文件被占用没搬走的：源目录仍在，报告里看得见
        "note": "trash 内容本批不清理（延迟清理由后续批次做）",
    }


async def _douyin_task_ids(conn, uid: int) -> list[int]:
    """``douyin/{task_id}/`` 的归属预筛：``douyin_pending.tenant_id``（该列本身就是家庭根）。

    插件表**可能不存在**（插件未装载时 registry 不建表，v2 §二.4）→ 没有候选目录，不是错误。
    """
    has = (await conn.execute(text(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='douyin_pending'"))).first()
    if not has:
        return []
    return [int(r[0]) for r in (await conn.execute(
        text("SELECT id FROM douyin_pending WHERE tenant_id = :uid"), {"uid": int(uid)}))]


# ── 向量 + BM25 ───────────────────────────────────────────────────────────────

async def _purge_vectors(user_id: int, memory_ids: list[int]) -> dict[str, Any]:
    """删向量（不抛穿，计数为主）。必须在删 ``memories`` 行之前跑——反查依据就是那批 id。"""
    from app.db import vector_store
    return await vector_store.delete_memory_vectors_by_user(user_id, memory_ids=memory_ids)


async def _purge_bm25(character_ids: list[int]) -> dict[str, Any]:
    """受影响角色的 BM25 落盘缓存一律失效（下次检索懒重建）。

    只调既有 :func:`app.memory.bm25_index.invalidate`——缓存键与文件路径规则都在那个模块里，
    自己拼路径删文件必然与它漂移（代价是那个角色索引重建一次，这是刻意的）。
    """
    from app.memory import bm25_index
    for cid in character_ids:
        bm25_index.invalidate(int(cid))
    return {"invalidated": len(character_ids), "character_ids": list(character_ids)}


# ── 入口 ──────────────────────────────────────────────────────────────────────

async def purge_account(db, *, actor_user_id: int, target_user_id: int,
                        body: dict | None = None, lang: str = "zh",
                        system_actor: bool = False) -> dict[str, Any]:
    """物理清除一个**已在回收站**的账号（控制台「立即清除」与第二期调度器的共用入口）。

    护栏（全部 4xx、零写入）
    -----------------------
    1. 仅 ``server_admin``（端点 ``require_server_admin``，本函数不重复判）；
    2. 第一期四条护栏原样复用 :func:`account_deletion.check_guards`（删自己 / 最后一个
       server_admin / 家庭最后一个主账号而家庭仍有人 / 家庭根名下有成员）；
    3. ``confirm_username`` 与目标用户名**逐字符相等**；
    4. **只允许清回收站里的账号**（``deleted_at`` 非空）——``force=true`` 也**不**给未标记账号
       开后门，它只表示「宽限期没到也要现在清」（即控制台「立即清除」的语义）。

    ``system_actor``（第二期调度器专用内部通道，**默认 False = 逐字旧行为**）
    -----------------------------------------------------------------------
    为 True 时只跳过「与 actor 身份相关」的两道护栏——① 不能删自己（系统身份非任何账号）
    ② ``confirm_username`` 逐字符核对（无人工输入串可核对）；**其余护栏一律保留**，
    尤其「不能删最后一个 server_admin」必须留着，否则调度器会自动清掉唯一的控制台管理员、
    把自己锁在门外。命中保留护栏时仍按原路径抛 4xx（不清、账号保持回收站态），由调度器
    捕获后记 WARNING 并计入 attempts。

    ⚠ **红线（务必不越）**：``system_actor`` 只能由函数关键字参数传入，且**只有内部调度器
    （app.application.account_purge_scheduler）允许传 True**。HTTP 端点
    ``POST /server/accounts/{id}/purge`` 只转发 ``body``，**绝不**读取 body 里的
    ``system_actor``/``confirm_username`` 喂给本参数——所以从 HTTP 塞 ``system_actor=true``
    无效、``confirm_username`` 仍必填（由 tests 钉死，勿改成从 body 取值）。

    重跑语义：job 已 ``done`` 且账号行确实没了 → 直接返回、不重复删、不报错；
    ``running`` / ``failed`` → 从 ``cursor_json`` 续跑（已删过的表再扫一遍得 0 行，属正常）。
    """
    body = body or {}
    uid = int(target_user_id)
    # 账本先于 ``_load_user``：清除成功后 ``users`` 行本就不存在，先查库必然 404，
    # 而「done 重跑直接返回」正是派单要求的幂等口径。
    job = await _read_job(db, uid)
    if job is not None and str(job["status"]) == STATUS_DONE and not await _account_still_exists(db, uid):
        report = _loads(job.get("report_json"))
        return {**report, "job_id": int(job["id"]), "status": STATUS_DONE, "resumed": False,
                "already_done": True, "idempotent": True,
                "note": "该账号已完成物理清除，本次调用未做任何删除"}

    target = await account_deletion._load_user(db, uid)
    reasons = await account_deletion.check_guards(db, actor_user_id=actor_user_id,
                                                  target=target, lang=lang)
    if system_actor:
        # 系统身份只跳过「与 actor 身份相关」的删自己；last_server_admin / 家庭护栏刻意保留。
        reasons = [r for r in reasons if r != _msg(lang, "cannot_delete_self")]
    for reason in reasons:
        raise HTTPException(status_code=400, detail=reason)

    if not system_actor:
        # confirm_username 只走函数内部通道；system_actor=True（仅调度器）时无人工串可核对，跳过。
        confirm = body.get("confirm_username")
        if not isinstance(confirm, str) or not confirm.strip():
            raise HTTPException(status_code=400, detail=_msg(lang, "confirm_username_required"))
        if confirm != target.username:
            raise HTTPException(status_code=400, detail=_msg(lang, "confirm_username_mismatch"))
    if target.deleted_at is None:
        raise HTTPException(status_code=400, detail=_msg(lang, "not_in_recycle_bin"))
    now = account_deletion.now_utc()
    force = bool(body.get("force"))
    if target.purge_after is not None and target.purge_after > now and not force:
        raise HTTPException(status_code=400, detail=_msg(
            lang, "grace_not_due", at=target.purge_after.isoformat()))

    job_id, prev_status, cursor = await _open_job(db, uid)
    started = time.monotonic()
    plan = await account_deletion.build_plan(db, target=target)
    report: dict[str, Any] = {
        "job_id": job_id,
        "user_id": uid,
        "username": target.username,
        "mode": plan["scope"],
        "status": STATUS_RUNNING,
        "resumed": prev_status in (STATUS_RUNNING, STATUS_FAILED),
        "prev_status": prev_status,
        "purge_after": target.purge_after.isoformat() if target.purge_after else None,
        "forced": force,
        "dry_run_row_estimate": plan["totals"]["row_count"],
    }
    try:
        # ① 前置备份（fail-closed：拿不到备份就中止，此时**一行都没删**）
        if not cursor.get("backup_zip"):
            cursor["backup_zip"] = await asyncio.to_thread(_run_backup)
            await _save_cursor(db, job_id, cursor)
        report["backup_zip"] = cursor["backup_zip"]

        # ② 第 0 步：固化三份集合 + 固化删除依据（续跑沿用同一份，不重扫）
        if not cursor.get("frozen"):
            cursor["frozen"] = await _collect_frozen(db, uid=uid, plan=plan)
            await _save_cursor(db, job_id, cursor)
        frozen = cursor["frozen"]
        cids = [int(c) for c in frozen["character_ids"]]
        session_ids = [int(s) for s in frozen["session_ids"]]
        report["frozen"] = {k: frozen.get(k) for k in
                            ("user_id", "character_ids", "character_count",
                             "session_ids", "session_count", "memory_count")}

        # ③ 文件隔离（第 0 步已固化会话集合，故即使 chat_sessions 行已删也能续跑）
        if not cursor.get("files"):
            task_ids = await _douyin_task_ids(db, uid)
            cursor["files"] = await asyncio.to_thread(
                _isolate_files, uid=uid, session_ids=session_ids, task_ids=task_ids)
            await _save_cursor(db, job_id, cursor)
        report["files"] = cursor["files"]

        # ④ 先向量，后记忆行
        if not cursor.get("vectors"):
            cursor["vectors"] = await _purge_vectors(uid, [int(m) for m in frozen["memory_ids"]])
            await _save_cursor(db, job_id, cursor)
        report["vectors"] = cursor["vectors"]

        # ⑤ BM25 按角色失效
        if not cursor.get("bm25"):
            cursor["bm25"] = await _purge_bm25(cids)
            await _save_cursor(db, job_id, cursor)
        report["bm25"] = cursor["bm25"]

        # ⑥ 按 cascade 清单分批删表（清单只来自 discover_purge_plan）
        tables_done: dict[str, int] = dict(cursor.get("tables_done") or {})
        cursor["tables_done"] = tables_done
        where_by_table = {t: w for t, w in frozen["tables"]}
        order, cyclic = await _delete_order(db, sorted(where_by_table))
        report["cyclic_tables"] = cyclic

        async def _save() -> None:
            await _save_cursor(db, job_id, cursor)

        async def _one(table: str) -> int:
            base = int(tables_done.get(table, 0))
            n = await _purge_table(db, table, where_by_table[table], uid=uid, cids=cids,
                                   on_progress=_tracker(table, base, tables_done, _save))
            tables_done[table] = base + int(n)
            await _save()
            return int(n)

        for table in order:
            if table in (CHARACTER_ROOT, ACCOUNT_ROOT):
                continue     # 两张根表在下面统一殿后
            await _one(table)

        # ⑦ 根表殿后：先角色根，后账号根（users 被 42 条外键指着，最先删必炸）
        if CHARACTER_ROOT in where_by_table:
            await _one(CHARACTER_ROOT)
        n_user = await _delete_root(db, ACCOUNT_ROOT, uid)
        tables_done[ACCOUNT_ROOT] = int(tables_done.get(ACCOUNT_ROOT, 0)) + n_user
        await _save()

        # ⑧ 收尾核对（应为空；非空就是「留下孤儿」的现场证据，必须进报告）
        fk_check = (await db.execute(text("PRAGMA foreign_key_check"))).fetchall()
        report["foreign_key_check"] = [{"table": r[0], "rowid": r[1], "parent": r[2],
                                        "fkid": r[3]} for r in fk_check[:50]]
        report["foreign_key_check_rows"] = len(fk_check)
        report["tables"] = [{"table": t, "rows": int(n)} for t, n in sorted(tables_done.items())]
        report["rows_deleted"] = sum(int(v) for v in tables_done.values())
        report["status"] = STATUS_DONE
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        await _finish_job(db, job_id, status=STATUS_DONE, cursor=cursor, report=report)
    except Exception as e:
        error = f"{e.table}: {e.statement} → {e.cause}" if isinstance(e, PurgeFKError) else repr(e)
        if isinstance(e, PurgeFKError):
            report["fk_blocker"] = {"table": e.table, "statement": e.statement, "cause": e.cause}
        report.update({"status": STATUS_FAILED, "error": error,
                       "elapsed_seconds": round(time.monotonic() - started, 3),
                       "tables": [{"table": t, "rows": int(n)} for t, n in
                                  sorted((cursor.get("tables_done") or {}).items())]})
        report["rows_deleted"] = sum(int(v) for v in (cursor.get("tables_done") or {}).values())
        await _finish_job(db, job_id, status=STATUS_FAILED, cursor=cursor, report=report,
                          error=error)
        _logger.error("account purge failed user=%s job=%s: %s", uid, job_id, error)
        await _audit(db, actor_user_id, target, report, STATUS_FAILED)
        raise HTTPException(status_code=500, detail={
            "code": "purge_failed", "job_id": job_id, "error": error,
            "message": _msg(lang, "purge_failed", err=error),
            "report": _compact(report)}) from e

    await _audit(db, actor_user_id, target, report, STATUS_DONE)
    from app.application.permission_service import _invalidate_account_state_cache
    _invalidate_account_state_cache(uid)   # 账号行已没了，门禁缓存必须立刻忘掉它
    _logger.info("account purged user=%s job=%s rows=%s files=%s vectors=%s",
                 uid, job_id, report["rows_deleted"], report["files"]["files_moved"],
                 report["vectors"])
    return {**report, "already_done": False, "idempotent": False}


def _compact(report: dict[str, Any]) -> dict[str, Any]:
    """500 响应体里的小摘要（完整账本在 ``account_purge_jobs.report_json``，不重复塞进 HTTP）。"""
    return {k: report.get(k) for k in
            ("job_id", "user_id", "username", "status", "rows_deleted", "tables",
             "files", "vectors", "bm25", "frozen", "fk_blocker")}


# ── 报告读端（控制台删号·第三期：只读，供控制台「清除结果」展示）─────────────────

#: ``cursor_json`` 的阶段顺序（与 :func:`purge_account` 的落盘顺序一致；用于「已处理到哪」）
CURSOR_STAGES = ("backup_zip", "frozen", "files", "vectors", "bm25", "tables_done")


def _iso(v: Any) -> str | None:
    """账本里的时间列 → ISO 串（已是字符串就原样返回，None 保持 None）。"""
    if v is None or isinstance(v, str):
        return v
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


async def get_job_report(db, user_id: int) -> dict[str, Any]:
    """读某账号的物理清除账本（**纯读，零写入**）：状态 + 解析后的报告 + 进度摘要。

    - 没有账本行 → ``{"job": None}``（回 200）：「从没被清过」是常态不是错误，控制台据此显示
      「尚无清除记录」，不该收到 404；
    - ``report_json`` 坏掉（截断/非 JSON）不抛穿：降级为 ``report=null`` + ``report_raw`` 摘要，
      运维仍看得见账本状态与错误原因；
    - 刻意不回原始 ``cursor_json``：里面既有大数组（frozen 的会话/角色 id）又有续跑细节，
      对展示无用的字段不进 HTTP（改发阶段摘要 + 已删表计数）；``report`` 则与 ``POST purge``
      的回包同源（同一份 ``report_json``，两条出口不许各说一套）。
    """
    job = await _read_job(db, int(user_id))
    if job is None:
        return {"user_id": int(user_id), "job": None}
    uid = int(job["user_id"])
    cursor = _loads(job.get("cursor_json"))
    raw_report = job.get("report_json")
    report = _loads(raw_report)
    done = [s for s in CURSOR_STAGES if cursor.get(s)]
    tables_done = {str(t): int(n or 0) for t, n in (cursor.get("tables_done") or {}).items()}
    out: dict[str, Any] = {
        "user_id": uid,
        "job": {
            "id": int(job["id"]),
            "user_id": uid,
            "status": str(job["status"] or ""),
            "started_at": _iso(job.get("started_at")),
            "finished_at": _iso(job.get("finished_at")),
            "error": job.get("error"),
            # 调度器把重试次数记在 cursor 里才有值（进程内 _RUNTIME 不进 HTTP）
            "attempts": cursor.get("attempts"),
        },
        "report": report or None,
        "cursor": {
            "stages_done": done,
            "next_stage": next((s for s in CURSOR_STAGES if not cursor.get(s)), None),
            "tables_done": [{"table": t, "rows": n} for t, n in sorted(tables_done.items())],
            "tables_done_count": len(tables_done),
            "rows_deleted_so_far": sum(tables_done.values()),
            "blocked_reason": cursor.get("blocked_reason") or job.get("error"),
        },
    }
    if raw_report and not report:
        out["report_raw"] = str(raw_report)[:500]
    return out


async def _audit(db, actor_user_id: int, target, report: dict[str, Any], status: str) -> None:
    """删号快照进审计（v2 §3.2 第 7 步）：``actor_user_id`` 允许悬空，但当时删了什么必须读得懂。

    审计本身 fail-open（``admin_audit_service.record`` 内部吞异常），不影响清除结果。
    """
    await _audit_record(db, actor_user_id, "account.purge", "user:%d" % int(target.id),
                        {"username": target.username,
                         "deleted_at": target.deleted_at.isoformat() if target.deleted_at else None,
                         "purge_after": target.purge_after.isoformat() if target.purge_after else None},
                        {"username": target.username, "status": status,
                         "mode": report.get("mode"),
                         "rows_deleted": report.get("rows_deleted"),
                         # 逐表明细留在 account_purge_jobs.report_json（本快照会撑破审计 4000 字截断线，
                         # 截断后 _loads 解不出 JSON → after 退化成字符串，控制台读不懂）；这里只留摘要
                         "table_count": len(report.get("tables") or []),
                         "files": {k: (report.get("files") or {}).get(k)
                                   for k in ("trash_dir", "upload_dir_count", "files_moved")},
                         "vectors": report.get("vectors"),
                         "bm25": (report.get("bm25") or {}).get("invalidated"),
                         "error": report.get("error"),
                         "job_id": report.get("job_id")})
    await db.commit()   # record() 只 flush（口径同第一期 mark_deleted），提交与否由调用方决定