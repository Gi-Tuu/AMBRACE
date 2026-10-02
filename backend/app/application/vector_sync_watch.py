# -*- coding: utf-8 -*-
"""向量对账定期化 + 写入失败留痕（2026-10-02 派单，本轮只做「让它可见」＋「定期量出来」）。

背景
----
2026-10-02 只读实测：**未归档但无向量 94 条 / 真孤儿 288 条**（重建后 52 / 57）。
⚠️ 同日发现并修复一个**对账口径 bug**：旧脚本读 @@embeddings.id@@（新版 chromadb 里那是**行号**，真正的向量 id 在 @@embedding_id@@ 列），会把真缺口放大约 20 倍（94 → 2178）。本模块已按正确列口径取数（@@_id_column@@）。
根因两条：①写路径 ``app/memory/write.py`` 里 ``add_memory()`` 失败只打一条 warning，
不重试不记账 ⇒ 这条链**静默失败过**；②**没人定期对账**，漂了不知道。

本轮边界（刻意不做）
--------------------
不做 outbox、不加重试、不改写入语义（真 outbox 留待拍板）。本模块**全程只读**：
两个库都用 ``sqlite3.connect("file:...?mode=ro", uri=True)`` + ``PRAGMA query_only`` 打开，
不写应用库一个字节，也不加载 embedding 模型（纯计数，秒级）。

写法参照 app/application/account_purge_scheduler.py
------------------------------------------------
端口注入（:func:`_app_db_path` / :func:`_vector_db_path` 两处测试注入缝）+ 纯函数
（:func:`coverage`）+ 单入口 tick（:func:`tick`，调度循环每拍调一次）。

异常一律 fail-open：任何取数异常只打 WARNING、返回 ``{"ok": False, ...}``，
绝不向调度主循环抛出。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.utils.logger import get_logger

_logger = get_logger("application.vector_sync_watch")

#: 缺向量告警阈值。**本期取 0**：这条链静默失败过（写失败只留一条 warning、无人对账），
#: 先要的是「可见」，不是「降噪」——有任何漂移就告警，等量出真实基线再谈阈值。
MISSING_WARN_THRESHOLD = 0
#: 孤儿向量告警阈值。取 0 的理由同上。
ORPHAN_WARN_THRESHOLD = 0

#: 记忆主表 / 向量 id 表（ChromaDB 的 embeddings 表，id 即 memory_id）
MEMORY_TABLE = "memories"
VECTOR_TABLE = "embeddings"


def _app_db_path() -> Path:
    """应用库路径：从 ``settings.database_url``（config 已解析为绝对路径）剥掉驱动前缀。测试注入缝。"""
    from app.config import settings

    url = str(getattr(settings, "database_url", "") or "")
    for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            return Path(url[len(prefix):])
    return Path(url)


def _vector_db_path() -> Path:
    """向量库路径：``settings.chroma_persist_dir/chroma.sqlite3``。测试注入缝。"""
    from app.config import settings

    return Path(str(getattr(settings, "chroma_persist_dir", "") or "")) / "chroma.sqlite3"


def _connect_ro(db_path: Path):
    """只读打开 SQLite（``mode=ro`` + ``PRAGMA query_only`` 双保险，与 scripts/check_vector_dims.py 同口径）。"""
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _id_column(conn, table: str) -> str:
    """向量表的 id 列口径（2026-10-02 修）。

    新版 chromadb 的 embeddings 表是 @@(id INTEGER PRIMARY KEY, segment_id, embedding_id TEXT,
    seq_id, created_at)@@：@@id@@ 是**行号**，**真正的向量 id（＝ memory_id）在 @@embedding_id@@**。
    旧口径读 @@id@@ 会把覆盖率算成天量假漂移（实测：94 条真缺口被报成 2178 条）。
    旧版 chromadb / 测试假库无 @@embedding_id@@ 时回退 @@id@@。
    """
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    return "embedding_id" if "embedding_id" in cols else "id"


def _ids(conn, table: str, where: str = "", column: str = "id") -> set[int]:
    """取某表的 id 集合（非整数 id 跳过）。@@column@@ 默认 @@id@@；
    向量表传 @@_id_column()@@ 的结果。"""
    ids: set[int] = set()
    for (raw,) in conn.execute(f'SELECT DISTINCT {column} FROM "{table}"{where}').fetchall():
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return ids


def coverage(app_db_path, vector_db_path) -> dict[str, Any]:
    """**纯只读**对账，给出四个数（不写任何库、不加载模型）。

    返回 ``{ok, alive_memories, vector_ids, missing, orphans, note}``：``alive_memories``
    未归档记忆数、``vector_ids`` 向量 id 去重数、``missing`` 未归档但无向量、
    ``orphans`` 向量 id 在 memories 里已不存在。

    ``orphans`` 是与**全部**记忆行（不区分归档）比对的：归档是软删、行还在，把它的向量算成孤儿
    会把「归档向量怎么清理」这件没拍板的事混进「写向量失败」的信号里。
    """
    out: dict[str, Any] = {
        "ok": False, "alive_memories": 0, "vector_ids": 0,
        "missing": 0, "orphans": 0, "note": "",
    }
    app_db, vector_db = Path(app_db_path), Path(vector_db_path)
    if not app_db.is_file():
        out["note"] = f"应用库不存在: {app_db}"
        return out
    if not vector_db.is_file():
        out["note"] = f"chroma.sqlite3 不存在: {vector_db}"
        return out
    try:
        vconn = _connect_ro(vector_db)
    except Exception as e:  # noqa: BLE001 —— fail-open：只留 note，不抛
        out["note"] = f"向量库只读打开失败: {e.__class__.__name__}: {e}"
        return out
    try:
        vector = _ids(vconn, VECTOR_TABLE, column=_id_column(vconn, VECTOR_TABLE))
    except Exception as e:  # noqa: BLE001
        out["note"] = f"读取 {VECTOR_TABLE} 失败: {e.__class__.__name__}: {e}"
        return out
    finally:
        vconn.close()
    try:
        aconn = _connect_ro(app_db)
    except Exception as e:  # noqa: BLE001
        out["note"] = f"应用库只读打开失败: {e.__class__.__name__}: {e}"
        return out
    try:
        cols = {r[1] for r in aconn.execute(f'PRAGMA table_info("{MEMORY_TABLE}")').fetchall()}
        if not cols:
            out["note"] = f"应用库里没有 {MEMORY_TABLE} 表"
            return out
        archived = " WHERE is_archived=0" if "is_archived" in cols else ""
        alive = _ids(aconn, MEMORY_TABLE, archived)
        every = _ids(aconn, MEMORY_TABLE)
    except Exception as e:  # noqa: BLE001
        out["note"] = f"读取 {MEMORY_TABLE} 失败: {e.__class__.__name__}: {e}"
        return out
    finally:
        aconn.close()
    out.update(
        ok=True,
        alive_memories=len(alive),
        vector_ids=len(vector),
        missing=len(alive - vector),
        orphans=len(vector - every),
    )
    return out


async def tick() -> dict[str, Any]:
    """调度循环每一拍调一次：只读取数 → 一行 INFO 摘要 → 超阈值再补一行 WARNING。

    挂台账 key ``vector_drift``（周期 24h，见 app/scheduling/scheduler.py）；**不新增 flag**。
    任何异常在本函数内消化（返回 ``ok=False``），绝不向主循环冒泡。
    """
    try:
        # 取数丢给工作线程：纯 sqlite 读虽然毫秒级，但不该在调度循环的事件循环上跑
        result = await asyncio.to_thread(coverage, _app_db_path(), _vector_db_path())
    except Exception as e:  # noqa: BLE001 —— coverage 已自兜底，这是第二层
        _logger.warning("vector drift check failed: %s: %s", e.__class__.__name__, e)
        return {"ok": False, "alive_memories": 0, "vector_ids": 0,
                "missing": 0, "orphans": 0, "note": f"{e.__class__.__name__}: {e}"}
    _logger.info(
        "vector drift check: alive_memories=%s vector_ids=%s missing=%s orphans=%s%s",
        result["alive_memories"], result["vector_ids"], result["missing"],
        result["orphans"], (" note=" + str(result["note"])) if result["note"] else "")
    try:
        if result["missing"] > MISSING_WARN_THRESHOLD or result["orphans"] > ORPHAN_WARN_THRESHOLD:
            _logger.warning(
                "vector drift exceeded: missing=%s(阈值>%s) orphans=%s(阈值>%s)"
                " alive_memories=%s vector_ids=%s",
                result["missing"], MISSING_WARN_THRESHOLD, result["orphans"],
                ORPHAN_WARN_THRESHOLD, result["alive_memories"], result["vector_ids"])
    except Exception as e:  # noqa: BLE001
        _logger.warning("vector drift threshold report failed: %s: %s", e.__class__.__name__, e)
    return result
