# -*- coding: utf-8 -*-
"""A4 批0-1 · 历史向量维度一致性核对（**默认纯只读**，一个字节都不改）。

雷达 16 → 批 0 隐患项 0-1：维度不符的症状是「写入抛错、检索静默为空」，
所以这里把三层维度摆在一起看，外加覆盖率与一次活体检索探针。

五段输出（最后给一行结论 PASS / WARN）::

    1 集合声明维度    chroma.sqlite3 collections.dimension（file:...?mode=ro）
    2 HNSW 头维度     段目录 header.bin + data_level0.bin 反推（打印候选偏移取值，不硬猜）
    3 模型实际维度    项目现成嵌入函数编一句探针文本，打印 len(vec)（权威）
    4 覆盖率          embeddings 行数 vs 记忆主表行数（表名从 sqlite_master 定位）
    5 活体检索探针    稠密路 + 记忆检索链入口，看是否「静默为空」

用法（一律用项目 venv 的 python）::

    # ① 只读核对（默认就是这一条）
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\check_vector_dims.py
    #   不加载模型/不开向量库：--no-model --no-live
    #   换路径：--vector-dir backend\\data\\vector_store --app-db backend\\data\\sqlite\\ai_companion.db

    # ② 重建：默认仍不写，只出 dry-run 摘要
    ... check_vector_dims.py --rebuild
    # ③ 真写（维护者手跑）：先备份 chroma.sqlite3 与整个 HNSW 段目录，备份失败即中止
    ... check_vector_dims.py --rebuild --apply [--limit 500]

退出码：0=PASS；1=WARN（三层对不上 / 有记忆缺向量 / 检索零命中）；
2=--apply 前置检查或备份失败（fail-closed）；3=向量库与段目录都不存在（无从核对）。

HNSW 维度是怎么定的（不是猜某个偏移）
------------------------------------
``data_level0.bin`` 每条记录 = ``[链路区 size_links_level0][向量 4*dim][标签 8]``，
所以 ``dim = (步长 - 链路区字节数 - 8) / 4``。步长由「文件大小 ÷ 元素数」得到
（元素数优先取 index_metadata.pickle 的 total_elements_added，并只用整除的约数），
链路区字节数则从 header.bin 的全部 int32/int64 取值里取候选，两条判据同时成立才认定：
①header 三元组自洽（``步长``/``label_offset``/``链路区`` 三个数都要在 header 里出现）；
②数据自洽——按该起点切出的向量逐条满足「分量量级正常(>1e-12) + L2 范数≈1 + |分量|≤1」。
（不能用「链路区未用槽位全 0」定向量起点：hnswlib 落盘时不清零，实测那里是一批脏链路 id。）
输出的 ``candidates``/``rejected`` 分别留着通过的组合与被拒组合及其原因，便于复核。
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sqlite3
import struct
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

COLLECTION_NAME = "character_memories"
DEFAULT_VECTOR_DIR = BACKEND_DIR / "data" / "vector_store"
DEFAULT_APP_DB = BACKEND_DIR / "data" / "sqlite" / "ai_companion.db"
DEFAULT_BACKUP_DIR = BACKEND_DIR / "backups"
DEFAULT_PROBE_TEXT = "用户提到自己养的那只猫今天不太爱吃东西"

_FLOAT_BYTES = 4
_LABEL_BYTES = 8
_MIN_STRIDE = 32
_MAX_STRIDE = 1 << 16
_MIN_DIM = 16
_MAX_DIM = 8192


# ── 通用：只读连接 ──
def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读打开 SQLite（mode=ro + PRAGMA query_only 双保险）。"""
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


# ── 段 1：集合声明维度 ──
def read_collection_dims(vector_db: Path, collection: str = COLLECTION_NAME) -> dict:
    """读 collections 表（id/name/dimension）；库或表缺失时只记 note，不抛。"""
    out = {"ok": False, "declared": None, "rows": [], "note": ""}
    if not vector_db.is_file():
        out["note"] = f"chroma.sqlite3 不存在: {vector_db}"
        return out
    try:
        conn = _connect_ro(vector_db)
    except Exception as e:
        out["note"] = f"只读打开失败: {e.__class__.__name__}: {e}"
        return out
    try:
        if "collections" not in _tables(conn):
            out["note"] = "chroma.sqlite3 里没有 collections 表"
            return out
        cols = _columns(conn, "collections")
        dim_col = "dimension" if "dimension" in cols else None
        rows = conn.execute(
            f'SELECT id, name, {dim_col or "NULL"} FROM collections ORDER BY name'
        ).fetchall()
        out["rows"] = [{"id": r[0], "name": r[1], "dimension": r[2]} for r in rows]
        out["ok"] = True
        for r in out["rows"]:
            if r["name"] == collection and r["dimension"]:
                out["declared"] = int(r["dimension"])
        if out["declared"] is None and len(out["rows"]) == 1:
            out["declared"] = out["rows"][0]["dimension"]
        if dim_col is None:
            out["note"] = "collections 表无 dimension 列（该版本不声明维度）"
        elif out["declared"] is None:
            out["note"] = f"未从 collections 解析出 {collection} 的维度"
    except Exception as e:
        out["note"] = f"读取 collections 失败: {e.__class__.__name__}: {e}"
    finally:
        conn.close()
    return out


# ── 段 2：HNSW 头维度 ──
def find_hnsw_segments(root: Path) -> list[Path]:
    """定位 HNSW 段目录（含 header.bin / data_level0.bin 的子目录）。"""
    if not root.is_dir():
        return []
    found = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "data_level0.bin").is_file():
            found.append(child)
    return found


def describe_header(header: bytes) -> dict:
    """把 header.bin 的全部 int32/int64 取值列出来（含偏移），供人工复核维度字段。"""
    i32, i64 = [], []
    for off in range(0, len(header) - 3):
        v = struct.unpack_from("<I", header, off)[0]
        if v != 0:
            i32.append({"offset": off, "value": v})
    for off in range(0, len(header) - 7):
        v = struct.unpack_from("<Q", header, off)[0]
        if v != 0:
            i64.append({"offset": off, "value": v})
    return {"size": len(header), "int32": i32, "int64": i64}


def header_link_candidates(header: bytes) -> list[int]:
    """链路区字节数的候选：header 里所有 4 对齐取值，加上 (M0+1)*4 形式的推导。"""
    vals: set[int] = set()
    for off in range(0, len(header) - 3):
        v = struct.unpack_from("<I", header, off)[0]
        if _MIN_STRIDE <= v <= _MAX_STRIDE and v % _FLOAT_BYTES == 0:
            vals.add(v)
        if v % 2 == 0:  # hnswlib: size_links_level0 = (max_links_level0 + 1) * sizeof(tableint)
            link = (v + 1) * _FLOAT_BYTES
            if _MIN_STRIDE <= link <= _MAX_STRIDE:
                vals.add(link)
    return sorted(vals)


def header_values(header: bytes) -> set[int]:
    """header.bin 里出现过的所有整数值（int32/int64 两种宽度，用于三元组自洽校验）。"""
    vals: set[int] = set()
    for off in range(0, len(header) - 3):
        vals.add(struct.unpack_from("<I", header, off)[0])
    for off in range(0, len(header) - 7):
        vals.add(struct.unpack_from("<Q", header, off)[0])
    return vals


def _read_pickle_count(seg: Path) -> int | None:
    """index_metadata.pickle 里的 total_elements_added（元素数，用来钳定步长）。"""
    path = seg / "index_metadata.pickle"
    if not path.is_file():
        return None
    try:
        import pickle

        data = pickle.load(open(path, "rb"))
        value = data.get("total_elements_added") if isinstance(data, dict) else None
        return int(value) if value else None
    except Exception:
        return None


def _validate_row(row: bytes, stride: int, link: int, dim: int, strict: bool) -> str:
    """返回 ""=通过，否则为不通过原因。

    链路区未用槽位在 hnswlib 里是**脏数据**（保存时原样落盘，不清零），所以不能拿
    「后面必须全 0」定向量起点；改用浮点量级：小整数按 float 读是 1e-43 量级的次正规数，
    而归一化 embedding 的分量是 1e-2 量级，据此可判起点是否落在链路区里。
    """
    link_slots = link // _FLOAT_BYTES
    link_count = struct.unpack_from("<I", row, 0)[0]
    if link_count >= link_slots:
        return f"链路计数 {link_count} ≥ 槽数 {link_slots}"
    vec_bytes = row[link:link + dim * _FLOAT_BYTES]
    if len(vec_bytes) != dim * _FLOAT_BYTES:
        return "向量段越界"
    if not strict:
        return ""
    import numpy as np

    vec = np.frombuffer(vec_bytes, dtype=np.float32)
    abs_vec = np.abs(vec)
    if float(abs_vec.min()) < 1e-12:
        return "含次正规/零分量（起点疑似落在链路区脏数据上）"
    if float(abs_vec.max()) > 1.0:
        return "分量绝对值 > 1"
    if abs(float(np.linalg.norm(vec)) - 1.0) > 1e-3:
        return "L2 范数偏离 1"
    return ""


def derive_segment_dim(seg: Path, *, max_rows: int = 3) -> dict:
    """反推 HNSW 段的向量维度（只读）。ok=False 时看 note/candidates 里为什么。

    判据两条，同时成立才认定：
    ① header 三元组自洽——``size_data_per_element(=步长)``/``label_offset``/
      ``size_links_level0`` 三个数都要在 header.bin 里出现过，且满足
      ``步长 = 链路区 + 4*dim + 8``；
    ② 数据自洽——按该起点切出的向量逐条满足「分量量级正常 + L2 范数≈1 + |分量|≤1」。
    """
    out: dict = {
        "ok": False, "dim": None, "stride": None, "count": None, "link_bytes": None,
        "segment": seg.name, "header": None, "candidates": [], "rejected": [], "note": "",
        "verified": False,
    }
    data_path = seg / "data_level0.bin"
    if not data_path.is_file():
        out["note"] = f"缺 data_level0.bin: {data_path}"
        return out
    size = data_path.stat().st_size
    if size < _MIN_STRIDE:
        out["note"] = f"data_level0.bin 体积异常({size} B)"
        return out
    out["data_bytes"] = size
    header_path = seg / "header.bin"
    header = header_path.read_bytes() if header_path.is_file() else b""
    out["header"] = describe_header(header)
    hv = header_values(header)
    pickle_count = _read_pickle_count(seg)
    out["pickle_elements"] = pickle_count
    links = [v for v in header_link_candidates(header) if v in hv] or [_MIN_STRIDE]

    if pickle_count and size % pickle_count == 0:
        counts = [pickle_count]
    else:
        counts = [c for c in range(1, size // _MIN_STRIDE + 1) if size % c == 0 and size // c <= _MAX_STRIDE]
    rows: dict[int, list[bytes]] = {}
    for count in counts:
        stride = size // count
        if stride < _MIN_STRIDE or stride > _MAX_STRIDE:
            continue
        out.setdefault("strides", []).append(stride)
        with open(data_path, "rb") as f:
            blob = f.read(stride * max_rows)
        rows[stride] = [blob[i:i + stride] for i in range(0, len(blob), stride) if len(blob[i:i + stride]) == stride]

    tried: list[dict] = []
    for strict in (True, False):
        passing = []
        for stride, row_list in rows.items():
            count = size // stride
            for link in links:
                rest = stride - link - _LABEL_BYTES
                if rest <= 0 or rest % _FLOAT_BYTES:
                    continue
                dim = rest // _FLOAT_BYTES
                if not _MIN_DIM <= dim <= _MAX_DIM:
                    continue
                if stride not in hv or (link + dim * _FLOAT_BYTES) not in hv:
                    continue  # header 里没有这组 size_data_per_element / label_offset
                reason = ""
                for row in row_list:
                    reason = _validate_row(row, stride, link, dim, strict)
                    if reason:
                        break
                item = {"stride": stride, "link_bytes": link, "dim": dim, "count": count,
                        "label_offset": link + dim * _FLOAT_BYTES, "strict": strict,
                        "reject": reason or None}
                tried.append(item)
                if not reason:
                    passing.append(item)
        if passing:
            dims = sorted({p["dim"] for p in passing})
            best = passing[0]
            out.update(count=best["count"], stride=best["stride"], link_bytes=best["link_bytes"],
                       dim=best["dim"] if len(dims) == 1 else None, verified=strict,
                       ok=len(dims) == 1)
            if len(dims) > 1:
                out["note"] = f"多组维度同时通过 {dims}"
            elif not strict:
                out["note"] = "仅结构判据通过（浮点量级/范数校验未过：向量可能非归一化文本嵌入）"
            out["candidates"] = passing[:20]
            out["rejected"] = [
                f"{c['link_bytes']}B→{c['dim']}d: {c['reject']}"
                for c in tried if c["strict"] == strict and c["reject"]
            ][:12]
            return out
    out["candidates"] = []
    out["rejected"] = [f"{c['link_bytes']}B→{c['dim']}d: {c['reject']}" for c in tried][:12]
    out["note"] = out["note"] or "没有任何候选通过校验（段目录可能非 hnswlib 布局或已损坏）"
    return out


# ── 段 3：模型实际维度 ──
def probe_model_dim(probe_text: str, embed=None) -> dict:
    """用项目现成嵌入函数编一句探针文本，打印 len(vec)（最权威的一层）。"""
    out = {"ok": False, "dim": None, "model": "bge-m3(ONNX)", "note": ""}
    try:
        from app.memory.embedding import check_model_available

        if not check_model_available():
            out["note"] = "模型文件缺失（backend/models/bge-m3），本层跳过"
            return out
    except Exception as e:
        out["note"] = f"模型可用性检查失败: {e.__class__.__name__}: {e}"
        return out
    try:
        if embed is None:
            from app.memory.embedding import text_embedding

            embed = text_embedding
        vec = asyncio.run(embed(probe_text))
        if not vec:
            out["note"] = "嵌入探针返回空"
            return out
        out["dim"] = len(vec)
        out["ok"] = True
    except Exception as e:
        out["note"] = f"嵌入探针失败: {e.__class__.__name__}: {e}"
    return out


# ── 段 4：覆盖率 ──
def locate_memory_table(conn: sqlite3.Connection) -> str | None:
    """从 sqlite_master 定位记忆主表（不猜）：优先 memories，否则取同时含 content+character_id 的最短表名。"""
    tables = {t for t in _tables(conn) if not t.startswith("sqlite_")}
    if "memories" in tables and {"content", "character_id"} <= set(_columns(conn, "memories")):
        return "memories"
    hits = []
    for t in sorted(tables, key=len):
        cols = set(_columns(conn, t))
        if {"content", "character_id"} <= cols and {"importance"} & cols:
            hits.append(t)
    return hits[0] if hits else None


def read_coverage(vector_db: Path, app_db: Path, memory_table: str | None = None) -> dict:
    """embeddings 行数 vs 记忆主表行数；给出「有记忆但无向量」的 id 清单。"""
    out: dict = {
        "ok": False, "vector_rows": None, "memory_rows": None, "memory_active_rows": None,
        "missing_ids": [], "orphan_vector_ids": [], "all_memory_ids": [],
        "table": memory_table, "note": "",
        "probe": None,
    }
    if not vector_db.is_file():
        out["note"] = f"chroma.sqlite3 不存在: {vector_db}"
        return out
    if not app_db.is_file():
        out["note"] = f"应用库不存在: {app_db}"
        return out
    try:
        vconn = _connect_ro(vector_db)
    except Exception as e:
        out["note"] = f"向量库只读打开失败: {e.__class__.__name__}: {e}"
        return out
    try:
        if "embeddings" not in _tables(vconn):
            out["note"] = "向量库无 embeddings 表"
            return out
        # 2026-10-02 修：新版 chromadb 的 embeddings 表列是
        #   (id INTEGER PRIMARY KEY, segment_id, embedding_id TEXT, seq_id, created_at)
        # —— **真正的向量 id 在 embedding_id**；旧口径读 id 拿到的是行号，
        # 会把覆盖率算成天量假漂移（实测：94 条真缺口被报成 2178 条；
        # --rebuild 的目标清单同步受污染）。
        _ecols = {_r[1] for _r in vconn.execute("PRAGMA table_info(embeddings)").fetchall()}
        _idcol = "embedding_id" if "embedding_id" in _ecols else "id"
        vector_ids = set()
        for (raw,) in vconn.execute(f"SELECT DISTINCT {_idcol} FROM embeddings").fetchall():
            try:
                vector_ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        out["vector_rows"] = len(vector_ids)
    finally:
        vconn.close()
    try:
        aconn = _connect_ro(app_db)
    except Exception as e:
        out["note"] = f"应用库只读打开失败: {e.__class__.__name__}: {e}"
        return out
    try:
        table = out["table"] or locate_memory_table(aconn)
        if not table:
            out["note"] = "sqlite_master 里定位不到记忆主表（需含 content/character_id/importance 列）"
            return out
        out["table"] = table
        cols = set(_columns(aconn, table))
        archived = " AND is_archived=0" if "is_archived" in cols else ""
        out["memory_rows"] = aconn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        out["memory_active_rows"] = aconn.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE 1=1{archived}').fetchone()[0]
        rows = aconn.execute(
            f'SELECT id FROM "{table}" WHERE 1=1{archived} ORDER BY id').fetchall()
        alive = {r[0] for r in rows}
        out["all_memory_ids"] = sorted(alive)
        out["missing_ids"] = sorted(alive - vector_ids)
        out["orphan_vector_ids"] = sorted(vector_ids - alive)
        probe_cols = [c for c in ("character_id", "content") if c in cols]
        if len(probe_cols) == 2:
            why = ", why_it_matters" if "why_it_matters" in cols else ", ''"
            row = aconn.execute(
                f'SELECT id, character_id, substr(content,1,80){why} FROM "{table}" '
                f'WHERE 1=1{archived} AND length(content)>12 ORDER BY id DESC LIMIT 1'
            ).fetchone()
            if row:
                out["probe"] = {
                    "memory_id": row[0], "character_id": row[1],
                    "text": (f"{row[3]} {row[2]}".strip() if (row[3] or "").strip() else row[2]),
                }
        out["ok"] = True
    except Exception as e:
        out["note"] = f"覆盖率统计失败: {e.__class__.__name__}: {e}"
    finally:
        aconn.close()
    return out


# ── 段 5：活体检索探针 ──
def run_live_probe(text: str, character_id: int, memory_id: int | None = None) -> dict:
    """稠密路（app/db/vector_store）+ 记忆检索链入口（app/memory/retrieve）各查一次。

    稠密路异常/空命中＝「检索静默为空」的直接信号；链路口还含 BM25/关键词兜底，
    两条一起看才能区分「向量坏了但关键词撑着」和「整链为空」。
    """
    out = {"ok": False, "dense_hits": None, "chain_hits": None, "self_hit": None,
           "dense_top1": None, "note": ""}

    async def _run():
        from app.db.vector_store import search_memories as dense_search
        from app.memory.embedding import text_embedding
        from app.memory.retrieve import search_memories as chain_search

        emb = await text_embedding(text)
        dense = await dense_search(character_id=character_id, query_embedding=emb, limit=3)
        chain = await chain_search(character_id=character_id, query=text, limit=3)
        return dense, chain

    try:
        dense, chain = asyncio.run(_run())
    except Exception as e:
        out["note"] = f"探针异常（多为向量库打不开或维度不符）: {e.__class__.__name__}: {e}"
        return out
    out["dense_hits"] = len(dense)
    out["chain_hits"] = len(chain)
    out["ok"] = True
    if dense:
        out["dense_top1"] = {"id": dense[0].get("id"), "distance": round(float(dense[0].get("distance") or 0), 4)}
        if memory_id is not None:
            out["self_hit"] = any(d.get("id") == memory_id for d in dense)
    return out


# ── 重建（默认 dry-run，绝不写）──
def collect_targets(coverage: dict, *, dim_mismatch: bool, limit: int | None) -> list[dict]:
    """缺向量的记忆一律重建；维度不符时全部未归档记忆都要重算（现有向量都是错的）。"""
    ids = list(coverage.get("missing_ids") or [])
    if dim_mismatch:
        ids = list(coverage.get("all_memory_ids") or ids)
    if limit and limit > 0:
        ids = ids[:limit]
    return [{"memory_id": i} for i in ids]


def backup_vector_store(vector_dir: Path, segments: list[Path], backup_dir: Path) -> list[Path]:
    """备份 chroma.sqlite3（在线备份 API）+ 整个 HNSW 段目录；任一步失败即抛（调用方 fail-closed）。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(backup_dir) / f"vector_store.pre-b01-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    src_db = vector_dir / "chroma.sqlite3"
    if src_db.is_file():
        target = dest / "chroma.sqlite3"
        src = sqlite3.connect(src_db)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        made.append(target)
    for seg in segments:
        copied = dest / seg.name
        shutil.copytree(seg, copied)
        made.append(copied)
    if not made:
        raise RuntimeError(f"无可备份内容: {vector_dir}")
    return made


def apply_rebuild(targets: list[dict], app_db: Path, table: str) -> dict:
    """重算 embed 并 upsert 回向量库（只在 --rebuild --apply 下调用）。"""
    stats = {"done": 0, "failed": 0, "skipped": 0}
    ids = [t["memory_id"] for t in targets]
    if not ids:
        return stats
    conn = sqlite3.connect(app_db)
    try:
        conn.execute("PRAGMA query_only=ON")
        cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
        want = ["id", "character_id", "content"] + [
            c for c in ("memory_type", "importance", "why_it_matters", "user_id", "status") if c in cols]
        chunks = [ids[i:i + 200] for i in range(0, len(ids), 200)]
        payload = []
        for chunk in chunks:
            marks = ",".join("?" * len(chunk))
            sql = f'SELECT {",".join(want)} FROM "{table}" WHERE id IN ({marks})'
            payload.extend(conn.execute(sql, chunk).fetchall())
    finally:
        conn.close()
    if not payload:
        stats["skipped"] = len(ids)
        return stats

    from app.db.vector_store import upsert_memory_vector
    from app.memory.embedding import text_embedding

    try:
        from app.memory.service import star_from_pct
    except Exception:
        def star_from_pct(pct: float) -> int:  # 兜底：百分比 → 星级
            return max(1, int(pct // 20))

    key = {c: i for i, c in enumerate(want)}

    def _get(row, name, default=None):
        return row[key[name]] if name in key else default

    async def _one(row):
        why = str(_get(row, "why_it_matters", "") or "").strip()
        content = str(_get(row, "content", "") or "").strip()
        doc = f"{why} {content}".strip() if why else content
        emb = await text_embedding(doc)
        await upsert_memory_vector(
            memory_id=int(_get(row, "id")),
            character_id=int(_get(row, "character_id")),
            memory_type=str(_get(row, "memory_type", "event") or "event"),
            content=content,
            embedding=emb,
            importance=star_from_pct(float(_get(row, "importance", 40.0) or 40.0)),
            document=doc,
            status=str(_get(row, "status", "active") or "active"),
            user_id=_get(row, "user_id"),
        )

    async def _loop():
        for row in payload:
            try:
                await _one(row)
                stats["done"] += 1
            except Exception as e:
                stats["failed"] += 1
                print(f"  [FAIL] mem={_get(row, 'id')} {e.__class__.__name__}: {e}")

    asyncio.run(_loop())
    return stats


# ── 汇总与结论 ──
def collect_dims(collections: dict, segments: list[dict], model: dict) -> dict:
    """把三层维度摆成一张表（None/不可用的层留 None，不参与比较）。"""
    hnsw_dims = {s["dim"] for s in segments if s.get("ok") and isinstance(s.get("dim"), int)}
    return {
        "集合声明": collections.get("declared"),
        "HNSW": next(iter(hnsw_dims)) if len(hnsw_dims) == 1 else None,
        "模型": model.get("dim"),
    }


def dims_disagree(dims: dict) -> bool:
    known = [v for v in dims.values() if isinstance(v, int)]
    return len(set(known)) > 1


def build_verdict(collections: dict, segments: list[dict], model: dict,
                  coverage: dict, live: dict | None) -> tuple[str, list[str]]:
    reasons: list[str] = []
    dims = collect_dims(collections, segments, model)
    known = {k: v for k, v in dims.items() if isinstance(v, int)}
    if dims_disagree(dims):
        reasons.append("维度对不上: " + " / ".join(f"{k}={v}" for k, v in dims.items()))
    elif not known:
        reasons.append("三层维度无一可比（全部跳过或解析失败）")
    multi = {s["dim"] for s in segments if s.get("ok") and isinstance(s.get("dim"), int)}
    if len(multi) > 1:
        reasons.append(f"多个 HNSW 段维度不一致 {sorted(multi)}")
    for seg in segments:
        if not seg.get("ok"):
            reasons.append(f"HNSW 段 {seg.get('segment')} 维度未定: {seg.get('note')}")
    if coverage.get("ok"):
        missing = len(coverage.get("missing_ids") or [])
        if missing:
            reasons.append(f"有记忆但无向量 {missing} 条")
        orphans = len(coverage.get("orphan_vector_ids") or [])
        if orphans:
            reasons.append(f"向量残留但记忆已删 {orphans} 条")
    else:
        reasons.append(f"覆盖率未取到: {coverage.get('note')}")
    if live is not None:
        if not live.get("ok"):
            reasons.append(f"活体探针失败: {live.get('note')}")
        elif live.get("dense_hits") == 0:
            reasons.append("稠密检索零命中（即「检索静默为空」症状）")
    return ("PASS" if not reasons else "WARN"), reasons


def print_report(collections: dict, segments: list[dict], model: dict, coverage: dict,
                 live: dict | None, vector_db: Path) -> tuple[str, list[str]]:
    print("\n[1] 集合声明维度")
    for row in collections.get("rows") or []:
        print(f"    collection {row['name']} id={row['id'][:8]}… dimension={row['dimension']}")
    print(f"    库: {vector_db}  →  声明维度={collections.get('declared')}"
          + (f"（{collections['note']}）" if collections.get("note") else ""))

    print("\n[2] HNSW 头维度")
    if not segments:
        print("    未找到 HNSW 段目录（vector_store 下无 data_level0.bin）")
    for seg in segments:
        header = seg.get("header") or {}
        ints = header.get("int32") or []
        print(f"    段 {seg['segment']}：header.bin {header.get('size')}B，"
              f"非零 int32 取值 {[(i['offset'], i['value']) for i in ints[:14]]}"
              + ("…" if len(ints) > 14 else ""))
        print(f"      元素数={seg.get('count')}（pickle total_elements_added={seg.get('pickle_elements')}）"
              f" 步长={seg.get('stride')} 链路区={seg.get('link_bytes')}B → 维度={seg.get('dim')}"
              + ("" if seg.get("ok") else f"（未定：{seg.get('note')}）")
              + ("（仅结构判据，浮点校验未过）" if seg.get("ok") and not seg.get("verified") else ""))
        cands = seg.get("candidates") or []
        if len(cands) > 1:
            print(f"      同时通过的候选={[(c['stride'], c['link_bytes'], c['dim']) for c in cands[:8]]}")
        rejected = seg.get("rejected") or []
        if rejected:
            print(f"      被拒候选={rejected}")

    print("\n[3] 模型实际维度（最权威）")
    print(f"    {model.get('model')} 探针向量长度={model.get('dim')}"
          + (f"（{model['note']}）" if model.get("note") else ""))

    print("\n[4] 覆盖率")
    print(f"    记忆主表={coverage.get('table')} 记忆行={coverage.get('memory_rows')}"
          f"（未归档 {coverage.get('memory_active_rows')}） / 向量行={coverage.get('vector_rows')}")
    print(f"    有记忆但无向量={len(coverage.get('missing_ids') or [])} 条"
          f" / 有向量但记忆已删={len(coverage.get('orphan_vector_ids') or [])} 条"
          + (f"（{coverage['note']}）" if coverage.get("note") else ""))

    print("\n[5] 活体检索探针")
    if live is None:
        print("    跳过（--no-live）")
    else:
        src = (coverage.get("probe") or {}).get("memory_id")
        print(f"    探针文本来源=memories.id={src}"
              + (f"（{live['note']}）" if live.get("note") else ""))
        print(f"    稠密路命中={live.get('dense_hits')} top1={live.get('dense_top1')}"
              f" 自身命中={live.get('self_hit')} / 检索链命中={live.get('chain_hits')}")

    verdict, reasons = build_verdict(collections, segments, model, coverage, live)
    print("\n结论: " + verdict + (" — " + "；".join(reasons) if reasons else
                              " — 三层维度一致、无覆盖率缺口、检索有命中"))
    return verdict, reasons


def run_check(args: argparse.Namespace) -> tuple[str, int]:
    vector_dir = Path(args.vector_dir)
    vector_db = vector_dir / "chroma.sqlite3"
    app_db = Path(args.app_db)
    print("=== check_vector_dims（默认只读：mode=ro + PRAGMA query_only）===")
    print(f"向量库: {vector_db}\n应用库: {app_db}")

    collections = read_collection_dims(vector_db)
    segments = [derive_segment_dim(seg) for seg in find_hnsw_segments(vector_dir)]
    model = (probe_model_dim(args.probe_text)
             if not args.no_model else {"ok": False, "dim": None, "note": "--no-model 跳过"})
    coverage = read_coverage(vector_db, app_db, args.memory_table)

    live = None
    if not args.no_live:
        probe = coverage.get("probe") or {}
        text = probe.get("text") or args.probe_text
        character_id = probe.get("character_id") or args.character_id
        if character_id:
            live = run_live_probe(text, int(character_id), probe.get("memory_id"))
        else:
            live = {"ok": False, "dense_hits": None, "chain_hits": None,
                    "note": "定位不到探针记忆（缺记忆表或未给 --character-id）"}

    verdict, _ = print_report(collections, segments, model, coverage, live, vector_db)

    if not vector_db.is_file() and not segments:
        print("[abort] 向量库文件与 HNSW 段目录都不存在，无从核对")
        return verdict, 3

    if args.rebuild:
        dim_mismatch = dims_disagree(collect_dims(collections, segments, model))
        targets = collect_targets(coverage, dim_mismatch=dim_mismatch, limit=args.limit)
        if not args.apply:
            print(f"\n[rebuild DRY-RUN] 将重建 {len(targets)} 条"
                  f"（维度不符={dim_mismatch}，limit={args.limit}）；"
                  f"采样 id={[t['memory_id'] for t in targets[:20]]}")
            print("[rebuild DRY-RUN] 未写任何文件；确认无误后加 --apply（会先备份再写）")
            return verdict, 0
        try:
            made = backup_vector_store(vector_dir, find_hnsw_segments(vector_dir),
                                       Path(args.backup_dir))
        except Exception as e:
            print(f"[abort] 备份失败，拒绝写入: {e.__class__.__name__}: {e}")
            return verdict, 2
        print("[rebuild APPLY] 备份完成：")
        for path in made:
            print(f"    {path}")
        stats = apply_rebuild(targets, app_db, coverage.get("table") or "memories")
        print(f"[rebuild APPLY] 完成={stats['done']} 失败={stats['failed']} 跳过={stats['skipped']}")
        if stats["failed"]:
            return verdict, 2
    return verdict, (0 if verdict == "PASS" else 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史向量维度一致性核对（默认只读；--rebuild 也只做 dry-run，--rebuild --apply 才写）")
    parser.add_argument("--vector-dir", type=Path, default=DEFAULT_VECTOR_DIR,
                        help=f"ChromaDB 持久化目录（默认 {DEFAULT_VECTOR_DIR}）")
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB,
                        help=f"应用 SQLite 库（默认 {DEFAULT_APP_DB}）")
    parser.add_argument("--memory-table", default=None,
                        help="记忆主表名（缺省自动从 sqlite_master 定位）")
    parser.add_argument("--probe-text", default=DEFAULT_PROBE_TEXT, help="模型/检索探针文本")
    parser.add_argument("--character-id", type=int, default=None,
                        help="探针检索的角色 id（缺省用自动定位到的那条记忆的角色）")
    parser.add_argument("--no-model", action="store_true", help="跳过本地嵌入模型探针（不加载 ONNX）")
    parser.add_argument("--no-live", action="store_true", help="跳过活体检索探针（不开向量库）")
    parser.add_argument("--rebuild", action="store_true",
                        help="重建摘要（默认 dry-run，不写任何文件）")
    parser.add_argument("--apply", action="store_true",
                        help="与 --rebuild 同用才真写：先备份 chroma.sqlite3 与 HNSW 段目录")
    parser.add_argument("--limit", type=int, default=None, help="本次最多重建多少条")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help=f"--apply 前置备份目录（默认 {DEFAULT_BACKUP_DIR}）")
    args = parser.parse_args(argv)
    try:
        _, code = run_check(args)
    except Exception as e:  # 任何意外都不抛栈：给一行结论 + 非零退出码
        print(f"结论: WARN — 核对过程异常 {e.__class__.__name__}: {e}")
        return 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
