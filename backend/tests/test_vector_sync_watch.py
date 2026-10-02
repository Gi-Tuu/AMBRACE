# -*- coding: utf-8 -*-
"""向量对账模块（app/application/vector_sync_watch.py）回归 —— 2026-10-02 派单。

只验三件事（与派单规格逐条对齐）：
① ``coverage()`` 四个数在四种组合下逐个正确（纯只读、不依赖真 Chroma 与模型）；
② ``tick()`` 的日志口径：有漂移 ⇒ INFO 摘要 + WARNING 告警，零漂移 ⇒ 只 INFO；
③ 失败路径 fail-open（库路径不存在 ⇒ 不抛、``ok=False``），且**既不加载模型也不写库**。

tmp_path 造两个裸 sqlite：应用库 ``memories(id, is_archived)`` + 向量库 ``embeddings(id)``。
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
from datetime import timedelta

from app.application import vector_sync_watch as vsw


def _make_app_db(path, rows: list[tuple[int, int]]) -> None:
    """造应用库：只有 ``memories(id, is_archived)`` 一张表（写完即关，不留句柄影响 mtime 断言）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, is_archived INTEGER NOT NULL DEFAULT 0)")
        conn.executemany("INSERT INTO memories VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _make_vector_db(path, ids: list[int]) -> None:
    """造向量库：**按真 Chroma 表形态** —— `id` 是行号、`embedding_id` 才是真正的向量 id（＝ memory_id）。

    2026-10-02 修：旧夹具把 memory_id 塞进 id 列，恰好夹住了「读错列」这个真 bug（94 条真缺口被报成 2178）——现在行号 +1000 与记忆 id 刻意错开，谁再读 `id` 列本用例立刻红。
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE embeddings (id INTEGER PRIMARY KEY, embedding_id TEXT)")
        conn.executemany("INSERT INTO embeddings (id, embedding_id) VALUES (?, ?)",
                         [(1000 + n, str(i)) for n, i in enumerate(ids)])
        conn.commit()
    finally:
        conn.close()


def _use(monkeypatch, app_db, vector_db) -> None:
    """把 tick 的两处路径注入缝指向造假库（不碰 settings、不碰生产库）。"""
    monkeypatch.setattr(vsw, "_app_db_path", lambda: app_db)
    monkeypatch.setattr(vsw, "_vector_db_path", lambda: vector_db)


def _records(caplog, level: int) -> list[str]:
    """只看本模块 logger 的某个级别（避免同轮其它 logger 的 INFO 混进来）。"""
    return [r.getMessage() for r in caplog.records
            if r.levelno == level and r.name == vsw._logger.name]


def test_coverage_四种组合逐个数字(tmp_path):
    """正常 / 有缺 / 有孤儿 / 两者都有：四个数逐个钉死（含「已归档行不算缺」）。"""
    cases = [
        # (场景, memories 行, embeddings id, 期望 alive/vector/missing/orphans)
        ("正常", [(1, 0), (2, 0)], [1, 2], (2, 2, 0, 0)),
        ("有缺（未归档但无向量）", [(1, 0), (2, 0)], [1], (2, 1, 1, 0)),
        ("有孤儿（向量行对应的记忆已删）", [(1, 0)], [1, 7], (1, 2, 0, 1)),
        # 4/5 已归档：4 有向量不算孤儿（行还在），5 无向量不算缺（不注入、不必有向量）
        ("两者都有", [(1, 0), (2, 0), (3, 0), (4, 1), (5, 1)], [2, 4, 9], (3, 3, 2, 1)),
    ]
    for idx, (name, mem_rows, vec_ids, want) in enumerate(cases):
        # 文件名只用 ASCII：mode=ro 走 URI 打开，非 ASCII 路径在 Windows 上会引入无关的编码噪声
        app_db = tmp_path / f"app-{idx}.db"
        vector_db = tmp_path / f"vec-{idx}.sqlite3"
        _make_app_db(app_db, mem_rows)
        _make_vector_db(vector_db, vec_ids)

        got = vsw.coverage(app_db, vector_db)
        assert got["ok"] is True, name
        assert got["note"] == "", name
        assert (got["alive_memories"], got["vector_ids"], got["missing"], got["orphans"]) == want, name


def test_tick_有漂移时既打info摘要也打warning告警(tmp_path, caplog, monkeypatch):
    app_db, vector_db = tmp_path / "app.db", tmp_path / "chroma.sqlite3"
    _make_app_db(app_db, [(1, 0), (2, 0), (3, 0)])
    _make_vector_db(vector_db, [1])          # 缺 2、3
    _use(monkeypatch, app_db, vector_db)

    with caplog.at_level(logging.INFO, logger=vsw._logger.name):
        result = asyncio.run(vsw.tick())

    assert result["missing"] == 2 and result["orphans"] == 0
    infos = _records(caplog, logging.INFO)
    warns = _records(caplog, logging.WARNING)
    assert len(infos) == 1 and infos[0].startswith("vector drift check:"), infos
    assert "alive_memories=3" in infos[0] and "vector_ids=1" in infos[0] and "missing=2" in infos[0]
    assert len(warns) == 1 and warns[0].startswith("vector drift exceeded:"), warns
    # 告警必须带上具体数字（派单：「带上具体数字」）
    assert "missing=2" in warns[0] and "orphans=0" in warns[0]


def test_tick_零漂移时只打info不打warning(tmp_path, caplog, monkeypatch):
    app_db, vector_db = tmp_path / "app.db", tmp_path / "chroma.sqlite3"
    _make_app_db(app_db, [(1, 0), (2, 0)])
    _make_vector_db(vector_db, [1, 2])
    _use(monkeypatch, app_db, vector_db)

    with caplog.at_level(logging.INFO, logger=vsw._logger.name):
        result = asyncio.run(vsw.tick())

    assert result["missing"] == 0 and result["orphans"] == 0
    assert len(_records(caplog, logging.INFO)) == 1
    assert _records(caplog, logging.WARNING) == []


def test_tick_库路径不存在时fail_open不抛(tmp_path, caplog, monkeypatch):
    """取数失败（库不存在 / 打不开 / 表缺失）一律不掀翻调度循环：返回 ok=False、只留日志。"""
    missing_app = tmp_path / "no-such-app.db"
    missing_vec = tmp_path / "no-such-chroma.sqlite3"
    ok_app, ok_vec = tmp_path / "app.db", tmp_path / "chroma.sqlite3"
    assert not missing_app.exists() and not missing_vec.exists()
    _make_app_db(ok_app, [(1, 0)])
    _make_vector_db(ok_vec, [1])
    _use(monkeypatch, missing_app, missing_vec)
    with caplog.at_level(logging.INFO, logger=vsw._logger.name):
        result = asyncio.run(vsw.tick())          # 不抛 = 本用例主断言
    assert result["ok"] is False
    assert (result["missing"], result["orphans"]) == (0, 0)
    assert "note=" in _records(caplog, logging.INFO)[0]

    # 只有一半可读（向量库存在但表结构不对）同样 fail-open
    broken = tmp_path / "broken.sqlite3"
    sqlite3.connect(str(broken)).close()
    _use(monkeypatch, ok_app, broken)
    result2 = asyncio.run(vsw.tick())
    assert result2["ok"] is False


def test_coverage与tick既不写库也不加载模型(tmp_path, monkeypatch):
    """硬断言「只读 + 不碰模型」：库文件 mtime/size 完全不变，且模型/Chroma 依赖一被 import 就炸。"""
    app_db, vector_db = tmp_path / "app.db", tmp_path / "chroma.sqlite3"
    _make_app_db(app_db, [(1, 0), (2, 0), (3, 1)])
    _make_vector_db(vector_db, [1, 9])      # 缺 2、孤儿 9
    before = [(p, p.stat().st_mtime_ns, p.stat().st_size) for p in (app_db, vector_db)]
    # sys.modules[name] = None ⇒ 任何 `import name` 直接 ImportError（Py3 语义），本轮真跑了就红
    for banned in ("app.memory.embedding", "chromadb", "torch", "onnxruntime", "transformers"):
        monkeypatch.setitem(sys.modules, banned, None)

    assert vsw.coverage(app_db, vector_db)["missing"] == 1
    _use(monkeypatch, app_db, vector_db)
    result = asyncio.run(vsw.tick())

    assert result["ok"] is True and (result["missing"], result["orphans"]) == (1, 1)
    for path, mtime, size in before:
        st = path.stat()
        assert (st.st_mtime_ns, st.st_size) == (mtime, size), f"{path.name} 被写过了"
    # 只读连接不留 WAL/journal 之类的伴生文件（写了就会留）
    assert sorted(p.name for p in tmp_path.iterdir()) == ["app.db", "chroma.sqlite3"]


def test_阈值取零且已挂进周期台账():
    """派单：本期阈值都取 0（有任何漂移就告警）；挂载走既有台账、周期 24h、不新增 flag。"""
    import inspect

    from app.scheduling import scheduler

    assert vsw.MISSING_WARN_THRESHOLD == 0
    assert vsw.ORPHAN_WARN_THRESHOLD == 0
    flat = " ".join(inspect.getsource(scheduler.periodic_loop).split())
    assert 'run_if_due("vector_drift", VECTOR_DRIFT_INTERVAL, _vector_drift_tick, reason="tick")' in flat
    assert scheduler.VECTOR_DRIFT_INTERVAL == timedelta(days=1)
    src = inspect.getsource(scheduler._vector_drift_tick)
    assert "AGENT_FLAGS" not in src and "flag" not in src.lower(), "本任务不新增 flag"
