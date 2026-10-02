# -*- coding: utf-8 -*-
"""A4 批0-1：scripts/check_vector_dims.py 单测（临时目录造小库，绝不碰生产向量库）。

纪律：全部用 tmp_path；``--apply`` 一条都不跑（真写只由维护者手跑）。
覆盖派单要求的五项：①维度一致 PASS ②header 与集合声明不一致 WARN ③缺向量的覆盖率
④--rebuild 默认 dry-run 不落任何写入 ⑤向量库缺失时优雅报错（不抛栈）。
"""
import importlib.util
import sqlite3
import struct
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
_DIM = 1024
_LINK_BYTES = 132  # (M0+1)*4，M0=32


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_vector_dims_mod", _REPO / "backend" / "scripts" / "check_vector_dims.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cvd():
    return _load()


# ────────────────────── 造库工具 ──────────────────────
def _make_segment(seg: Path, dim: int, count: int, *, with_pickle: bool = True,
                  link_bytes: int = _LINK_BYTES) -> None:
    """合成一个 hnswlib 形态的段目录：每条 = [链路区][归一化向量][u64 标签]。"""
    seg.mkdir(parents=True, exist_ok=True)
    stride = link_bytes + dim * 4 + 8
    rng = np.random.default_rng(20260927)
    with open(seg / "data_level0.bin", "wb") as f:
        for i in range(count):
            vec = rng.normal(size=dim).astype(np.float32)
            vec /= np.linalg.norm(vec)
            link = struct.pack("<I", 2) + struct.pack("<2I", 0, min(1, i))
            link += b"\x00" * (link_bytes - len(link))
            f.write(link + vec.tobytes() + struct.pack("<Q", 1000 + i))
    header = bytearray(100)
    for off, value in ((20, count), (28, stride), (36, stride - 8), (44, link_bytes),
                       (60, 16), (68, 32)):
        struct.pack_into("<Q", header, off, value)
    (seg / "header.bin").write_bytes(bytes(header))
    if with_pickle:
        import pickle

        with open(seg / "index_metadata.pickle", "wb") as f:
            pickle.dump({"total_elements_added": count, "dimensionality": None}, f)


def _make_chroma(db: Path, *, dimension: int | None, doc_ids: list[int]) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT, dimension INTEGER,"
        " database_id TEXT, config_json_str TEXT, schema_str TEXT)")
    con.execute("INSERT INTO collections VALUES (?,?,?,?,?,?)",
                ("0140f2ed-62eb-463a-9b10-cdefa4c11a6b", "character_memories", dimension,
                 "00000000-0000-0000-0000-000000000000", "{}", "{}"))
    # 2026-10-02 修：夹具改成**真库形态** —— 新版 chromadb 的 embeddings 是
    #   (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT, seq_id, created_at)，
    #   其中 id 是行号、**真正的向量 id（＝ memory_id）在 embedding_id**。
    #   旧夹具把记忆 id 塞进 id 列，掩盖了「读错列 ⇒ 覆盖率报天量假漂移」这个真 bug（94→2178）。
    #   这里刻意让行号 +1000 与记忆 id 错开：谁再读 id 列，本用例立刻红。
    con.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT,"
                " embedding_id TEXT, seq_id INTEGER, created_at TEXT)")
    con.executemany("INSERT INTO embeddings (id, segment_id, embedding_id, seq_id, created_at)"
                    " VALUES (?,?,?,?,?)",
                    [(1000 + n, "81ca0406", str(i), n, "2026-09-01 00:00:00")
                     for n, i in enumerate(doc_ids)])
    con.commit()
    con.close()


def _make_app_db(db: Path, memory_ids: list[int], *, character_id: int = 7) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE memories (id INTEGER PRIMARY KEY, character_id INTEGER, user_id INTEGER,"
        " memory_type TEXT, content TEXT, importance REAL, is_archived INTEGER DEFAULT 0,"
        " why_it_matters TEXT, status TEXT DEFAULT 'active', created_at TEXT)")
    con.executemany(
        "INSERT INTO memories VALUES (?,?,?,?,?,?,0,?, 'active', '2026-09-20 10:00:00')",
        [(i, character_id, 3, "event", f"记忆内容 {i}：用户提到的一些具体事情", 60.0, None)
         for i in memory_ids])
    con.commit()
    con.close()


def _snapshot(root: Path) -> dict:
    return {
        str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def env(tmp_path):
    """一套一致的临时现场：声明 1024 / 段 1024 / 记忆与向量一一对应。"""
    vector_dir = tmp_path / "vector_store"
    _make_chroma(vector_dir / "chroma.sqlite3", dimension=_DIM, doc_ids=[1, 2, 3])
    _make_segment(vector_dir / "seg-aaaa", dim=_DIM, count=3)
    app_db = tmp_path / "data" / "sqlite" / "app.db"
    _make_app_db(app_db, [1, 2, 3])
    return {"vector_dir": vector_dir, "app_db": app_db, "tmp": tmp_path}


BASE_ARGS = ["--no-model", "--no-live"]


def _run(cvd, env, extra=()):
    args = ["--vector-dir", str(env["vector_dir"]), "--app-db", str(env["app_db"])] + BASE_ARGS + list(extra)
    return cvd.main(args)


# ────────────────────── ① 维度一致 ⇒ PASS ──────────────────────
def test_consistent_dims_pass(cvd, env, capsys):
    code = _run(cvd, env)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "结论: PASS" in out
    assert "维度=1024" in out


def test_model_probe_reads_vector_length(cvd, monkeypatch):
    """段3：用注入的假嵌入函数验证「探针长度＝模型维度」（不加载 ONNX）。"""
    import app.memory.embedding as emb_mod

    monkeypatch.setattr(emb_mod, "check_model_available", lambda: True)

    async def fake_embed(text: str) -> list[float]:
        return [0.0] * 768

    res = cvd.probe_model_dim("探针文本", embed=fake_embed)
    assert res["ok"] and res["dim"] == 768

    monkeypatch.setattr(emb_mod, "check_model_available", lambda: False)
    missing = cvd.probe_model_dim("探针文本", embed=fake_embed)
    assert not missing["ok"] and "模型文件缺失" in missing["note"]


# ────────────────────── ② header 与声明不一致 ⇒ WARN ──────────────────────
def test_hnsw_dim_mismatch_warns(cvd, env, capsys):
    seg = env["vector_dir"] / "seg-aaaa"
    _make_segment(seg, dim=768, count=3, with_pickle=False)  # 顺便走一遍约数反推路径
    code = _run(cvd, env)
    out = capsys.readouterr().out
    assert code == 1, out
    assert "维度=768" in out
    assert "结论: WARN" in out and "维度对不上" in out


def test_derive_segment_dim_is_unique_and_rejects_others(cvd, tmp_path):
    """维度反推必须唯一命中，且把错误起点按原因拒掉（不是硬猜某个偏移）。"""
    seg = tmp_path / "s"
    _make_segment(seg, dim=512, count=4)
    res = cvd.derive_segment_dim(seg)
    assert res["ok"] and res["dim"] == 512 and res["verified"]
    assert res["stride"] == _LINK_BYTES + 512 * 4 + 8
    assert [c["dim"] for c in res["candidates"]] == [512]
    assert res["rejected"], "被拒候选与原因应当留痕"


# ────────────────────── ③ 覆盖率 ──────────────────────
def test_coverage_reports_missing_vectors(cvd, env, capsys):
    _make_app_db(env["tmp"] / "data" / "sqlite" / "app2.db", [1, 2, 3, 4, 5])
    cov = cvd.read_coverage(env["vector_dir"] / "chroma.sqlite3",
                            env["tmp"] / "data" / "sqlite" / "app2.db")
    assert cov["ok"] and cov["table"] == "memories"
    assert cov["memory_active_rows"] == 5 and cov["vector_rows"] == 3
    assert cov["missing_ids"] == [4, 5]

    code = _run(cvd, env, extra=["--app-db", str(env["tmp"] / "data" / "sqlite" / "app2.db")])
    out = capsys.readouterr().out
    assert code == 1 and "有记忆但无向量=2 条" in out and "结论: WARN" in out


def test_locate_memory_table_from_sqlite_master(cvd, tmp_path):
    db = tmp_path / "odd.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE odd_memory_main (id INTEGER PRIMARY KEY, character_id INTEGER,"
                " content TEXT, importance REAL, is_archived INTEGER DEFAULT 0)")
    con.execute("INSERT INTO odd_memory_main VALUES (1, 7, '一条具体记忆', 40, 0)")
    con.execute("CREATE TABLE snacks (id INTEGER PRIMARY KEY, taste TEXT)")
    con.commit()
    con.close()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        assert cvd.locate_memory_table(conn) == "odd_memory_main"
    finally:
        conn.close()


# ────────────────────── ④ --rebuild 默认 dry-run 不写 ──────────────────────
def test_rebuild_default_is_dry_run(cvd, env, capsys):
    before = _snapshot(env["tmp"])
    code = _run(cvd, env, extra=["--rebuild"])
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "未写任何文件" in out
    assert _snapshot(env["tmp"]) == before, "dry-run 不得改动任何文件（含 mtime）"
    assert code in (0, 1)

    # 维度不符时 dry-run 摘要要覆盖全部记忆（此时现有向量都算废）
    # 注：造 768 段本身就改了现场，快照要在造完之后重取，否则比的是自己的写入
    seg = env["vector_dir"] / "seg-aaaa"
    _make_segment(seg, dim=768, count=3)
    after_seg = _snapshot(env["tmp"])
    capsys.readouterr()
    _run(cvd, env, extra=["--rebuild"])
    out2 = capsys.readouterr().out
    assert "将重建 3 条" in out2
    assert _snapshot(env["tmp"]) == after_seg, "dry-run 不得改动任何文件（含 mtime）"


# ────────────────────── ⑤ 向量库缺失 ⇒ 优雅报错 ──────────────────────
def test_missing_store_is_graceful(cvd, tmp_path, capsys):
    missing = tmp_path / "nope"
    app_db = tmp_path / "app.db"
    _make_app_db(app_db, [1])
    code = cvd.main(["--vector-dir", str(missing), "--app-db", str(app_db)] + BASE_ARGS)
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "结论:" in out and "无从核对" in out
    assert code == 3


def test_apply_without_backup_dir_parent_is_graceful(cvd, tmp_path, capsys):
    """备份目标不可用时 fail-closed（返回 2），且现场一个字节都没动。

    把 --backup-dir 指到一个**已存在的文件**：mkdir 必抛 → 备份失败即中止，
    绝不能走到 apply_rebuild（那会加载 ONNX 并按 settings 里的向量库路径真写）。
    """
    vector_dir = tmp_path / "vs"
    _make_chroma(vector_dir / "chroma.sqlite3", dimension=_DIM, doc_ids=[])
    _make_segment(vector_dir / "seg", dim=_DIM, count=1)
    app_db = tmp_path / "app.db"
    _make_app_db(app_db, [9])
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")
    before = _snapshot(tmp_path)
    code = cvd.main(["--vector-dir", str(vector_dir), "--app-db", str(app_db)] + BASE_ARGS
                    + ["--rebuild", "--apply", "--backup-dir", str(blocker)])
    out = capsys.readouterr().out
    assert code == 2, out
    assert "备份失败" in out and "Traceback" not in out
    assert _snapshot(tmp_path) == before, "fail-closed 前不得触碰向量库"
